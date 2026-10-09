# Model artifact store (MinIO / S3)

Trained model artifacts — LightGBM pickles, LSTM `.pt` weights, per-family metadata —
used to live in the repository under `models/` as plain git blobs. Because
[TECHNICAL_GUIDE.md](../../TECHNICAL_GUIDE.md) §6.2 retains a new family on every
retrain rather than overwriting, `.git` grew permanently with each run (151 blobs,
~58 MB of history at the time of writing). This page describes the object-store
backend that removes that coupling, and how to deploy and operate it.

Status: the storage abstraction, both backends, the migration/sync commands, and the
wiring into the LightGBM and LSTM save/load paths are implemented and tested. The
default backend is `local`, so nothing changes until you deploy MinIO and cut over.

## Design

`apps/prediction/artifact_store.py` puts a thin interface in front of artifact I/O:

| Backend | Canonical store | Local cache | Save | Load |
| --- | --- | --- | --- | --- |
| `local` (default) | filesystem under `BASE_DIR` | same dir | write file | read file |
| `s3` | S3/MinIO bucket | `BASE_DIR/models` | write cache, then upload | read cache; download on miss |

Two properties make this safe to adopt incrementally:

- **The `local` backend is a pure passthrough.** `upload_dir` / `ensure_dir_local`
  are no-ops, so the save/load paths behave byte-identically to before. CI and tests
  use it — no MinIO required.
- **The object key is the `BASE_DIR`-relative `artifact_path` already in the registry**
  (e.g. `models/lightgbm/3d_lgb-3d-2020-01-01/model.pkl`). So `artifact_path` stays
  portable and doubles as the key, which is what closes the "stored artifact paths are
  not portable" backlog item — a key resolves the same on any host.

Loads consult, in order: the in-memory LRU (`_LIGHTGBM_ARTIFACT_CACHE`), the local
cache directory, then the store. A warm cache never touches the network.

## Configuration

All keys are read from `.env` (see `.env.example`); settings live in
`config/settings/base.py`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ARTIFACT_STORE_BACKEND` | `local` | `local` or `s3` |
| `ARTIFACT_LOCAL_CACHE_ROOT` | repo root | cache/store root for the local backend |
| `ARTIFACT_S3_ENDPOINT_URL` | — | MinIO S3 endpoint, e.g. `http://192.168.31.8:9000` |
| `ARTIFACT_S3_BUCKET` | `finance-analysis-artifacts` | bucket name |
| `ARTIFACT_S3_ACCESS_KEY_ID` | — | scoped access key (secret) |
| `ARTIFACT_S3_SECRET_ACCESS_KEY` | — | scoped secret key (secret) |
| `ARTIFACT_S3_REGION` | `us-east-1` | region label (MinIO ignores it but boto3 requires one) |
| `ARTIFACT_S3_ADDRESSING_STYLE` | `path` | `path` for MinIO; `virtual` for AWS |

Secrets stay in `.env` (gitignored) and in the NAS secrets inventory — never in the repo.

## Deploying MinIO on HomeNAS

The NAS (`192.168.31.8`) already runs Docker, Samba, ZFS, PostgreSQL, and Redis. Add
MinIO as another LAN-only container, mirroring the Immich/Postgres firewall pattern
(bind to the LAN address, accept from LAN + WireGuard in `DOCKER-USER`, no WAN forward).

Reference compose (adjust the dataset path and credentials; keep the data on a ZFS
dataset so snapshots cover it):

```yaml
services:
  minio:
    image: minio/minio:latest
    container_name: finance_artifacts_minio
    command: server /data --console-address ":9001"
    ports:
      - "192.168.31.8:9000:9000"    # S3 API, LAN-only
      - "192.168.31.8:9001:9001"    # console, LAN-only
    environment:
      MINIO_ROOT_USER: <root-user>       # bootstrap admin, not used by the app
      MINIO_ROOT_PASSWORD: <root-password>
    volumes:
      - /DefaultMirror/minio:/data       # ZFS-backed
    restart: unless-stopped
```

Then, once:

1. Create the bucket `finance-analysis-artifacts`; enable **versioning** (cheap
   rollback, matches the §6.2 retain-every-family policy); optionally add a lifecycle
   rule to expire non-current versions after N days.
2. Create a **scoped** access/secret key (read/write that bucket only), bucket-private.
3. Record the endpoint and the scoped key location in the NAS
   `docs/reference/secrets-inventory.md`, and add a `minio.md` runbook + `inventory.md`
   / `live-state.md` rows in the HomeServer repo.

The ZFS pool was ~89% full (~1 TB free) at the time of writing; model artifacts are
GB-scale, so this is negligible, but keep it on the capacity watch.

## Operations

```bash
# Preview what would upload (dry run is the default; no writes)
python manage.py migrate_artifacts_to_store

# Upload every on-disk family to the store, and canonicalise registry paths
python manage.py migrate_artifacts_to_store --execute --normalize-registry

# Fresh clone (models/ untracked): populate the local cache from the store
python manage.py sync_artifacts_from_store
python manage.py sync_artifacts_from_store --prefix models/lightgbm
```

### Cutover sequence

1. Deploy MinIO (above); put the endpoint + scoped key in `.env`.
2. `migrate_artifacts_to_store --execute --normalize-registry` and confirm the active
   families report as uploaded.
3. Set `ARTIFACT_STORE_BACKEND=s3`; restart the stack.
4. Validate a daily prediction, a queued backtest, and an LSTM/LightGBM inference all
   load from the cache/store.
5. Only then untrack artifacts: `git rm -r --cached models/`, add `models/` to
   `.gitignore` (it is now a cache), and rely on `sync_artifacts_from_store` for fresh
   clones. This is forward-only — existing git history keeps its blobs; `.git` simply
   stops growing.

### Fallback

If the NAS is unreachable, set `ARTIFACT_STORE_BACKEND=local` to run against the cache
directory. A genuinely missing artifact already fails closed at inference
(TECHNICAL_GUIDE §6.5), so a cold cache with no store surfaces as a load failure rather
than a silent wrong prediction.
