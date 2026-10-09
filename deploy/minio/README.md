# MinIO deployment — FinanceAnalysis model artifacts

Turnkey deployment for the S3 artifact backend described in
[docs/how-to/artifact-store.md](../../docs/how-to/artifact-store.md). It runs on
HomeNAS (`192.168.31.8`), which already hosts this project's PostgreSQL and Redis.

This is infrastructure for a *different* host, so it is a spec plus scripts, not
something the Django app runs. Deploy it manually, once.

## Steps

1. **Copy this directory to the NAS** (e.g. `/opt/finance-minio/`).

2. **Bootstrap admin credentials** — create a `.env` beside `docker-compose.yml`
   (never committed):

   ```
   MINIO_ROOT_USER=<admin-user>
   MINIO_ROOT_PASSWORD=<strong-password>
   ```

3. **ZFS dataset + start**:

   ```bash
   sudo zfs create DefaultMirror/minio    # if it does not already exist
   docker compose up -d
   docker compose ps                       # wait for healthy
   ```

4. **Bucket + scoped app key**:

   ```bash
   set -a; . ./.env; set +a
   ./init-minio.sh                          # prints the app AccessKey/SecretKey ONCE
   ```

5. **Wire FinanceAnalysis** — put the printed key + endpoint in the app `.env`
   (`ARTIFACT_S3_*`, `ARTIFACT_STORE_BACKEND=s3`), then on the app host:

   ```bash
   python manage.py migrate_artifacts_to_store --execute --normalize-registry
   ```

   Validate a daily prediction and a queued backtest load from the store, then
   untrack artifacts (Phase 6b of the migration plan):
   `git rm -r --cached models/` + add `models/` to `.gitignore`.

6. **Record it in the HomeServer repo** (only after it is live): add rows to
   `docs/reference/inventory.md` and `live-state.md`, the key *location* to
   `docs/reference/secrets-inventory.md`, and a `docs/runbooks/minio.md`.

## Firewall

The compose binds `9000`/`9001` to `192.168.31.8` (LAN-only), like PostgreSQL and
Redis, so it is reachable from the LAN and WireGuard with no WAN forward. If the NAS
`DOCKER-USER` chain default-drops published ports (as it does for Immich), add an
ACCEPT for the LAN + WireGuard subnets — see the HomeServer `runbooks/firewall.md`
and the Immich precedent in `runbooks/immich.md`.

## Least-privilege option

`init-minio.sh` mints a service account under the root user, which is fine for a
LAN-only single-bucket store. For least privilege, instead create a dedicated user
with a bucket-scoped policy allowing only `s3:GetObject`, `s3:PutObject`, and
`s3:ListBucket` on `finance-analysis-artifacts`, and mint the service account under
that user.

## Backups

Artifacts land on the `DefaultMirror/minio` ZFS dataset, so daily sanoid snapshots
cover them. Fold the bucket into the offsite plan
(`HomeServer/docs/backlog/oss-backup-plan.md`) when that is implemented.
