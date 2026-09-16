# How to set up the local stack

Gets a Windows or WSL2 host from a clean checkout to a running backend, worker
fleet, and frontend. Assumes no Docker.

Time: ~20 minutes on a warm machine, longer if TA-Lib needs building.

---

## 1. Prerequisites

| Component | Version | Notes |
| --- | --- | --- |
| Python | 3.14 | `venv` module required |
| PostgreSQL | 15 | Runs **outside** the repo — host service or another machine |
| Redis | 7 | Runs **outside** the repo — same |
| Node.js + npm | current LTS | Frontend only |
| TA-Lib | C library | See §4 — the most common first-run failure |
| Git | any recent | |
| PowerShell 7 | `pwsh` | Windows launcher scripts |

The Compose stack in `docker-compose.yml` no longer starts PostgreSQL or Redis.
Those services must already be reachable at whatever `.env` points to.

---

## 2. Provision PostgreSQL

```sql
CREATE ROLE finance_analysis WITH LOGIN PASSWORD 'finance_analysis';
CREATE DATABASE finance_analysis OWNER finance_analysis;
```

Grant the role `CREATE` on the database — Django's test runner creates and
destroys a `test_finance_analysis` database, which needs that privilege.

If the server is on another host, ensure `postgresql.conf` has a matching
`listen_addresses` and `pg_hba.conf` permits the client network with
`scram-sha-256`.

---

## 3. Provision Redis

`.env.example` uses credentialed URLs, so Redis needs authentication configured —
see "Getting the URL shape right" below before copying those URLs verbatim:

```conf
# redis.conf
requirepass <your-password>
maxmemory 2gb
maxmemory-policy noeviction
```

`noeviction` matters: Redis holds the Celery broker (db 0), the Django cache
(db 1), and the Channels layer. Silent key eviction under `allkeys-lru` causes
tasks to vanish and cache-backed throttle counters to reset.

### Getting the URL shape right

`.env.example` ships `redis://finance_analysis:finance_analysis@host:6379/1`. The
part before the colon is a Redis **ACL username**. Which form is correct depends
entirely on how the server was configured, and the two are not interchangeable:

| Server configuration | Correct URL |
| --- | --- |
| ACL user created (`ACL SETUSER finance_analysis …`) | `redis://finance_analysis:<pw>@host:6379/1` |
| `requirepass <pw>` only, no ACL user | `redis://:<pw>@host:6379/1` — **empty username** |

**This deployment uses a real ACL user.** Verified by probing all four forms
against the configured server:

| Form tried | Result |
| --- | --- |
| `finance_analysis:<pw>@` | **PING OK** |
| `:<pw>@` (empty username) | AuthenticationError |
| `default:<pw>@` | AuthenticationError |
| no password | AuthenticationError |

So the empty-username form that works for a plain `requirepass` server **fails
here**. Do not "simplify" a working `REDIS_URL` by dropping the username.

Both failure modes produce the same message, which is why probing beats guessing:

```
redis.exceptions.AuthenticationError: invalid username-password pair or user is disabled.
```

An unauthenticated connection is *also* rejected, so seeing this error with no
credentials at all tells you the server requires auth rather than that your
username is wrong.

Always verify rather than reason about it:

```bash
./scripts/verify_local_stack.sh     # or .ps1 -- both probe through psycopg2 / redis-py
```

Both scripts now connect through the Python drivers and redact credentials from all
output, including failure messages. They previously shelled out to `pg_isready` and
`redis-cli`, which do not exist on Windows, so the PowerShell verifier skipped both
probes and still exited 0 — which is how a wrong Redis password survived
undetected while the documented verification step reported success.

A wrong credential is not a test-only concern. The same one backs DRF throttling,
so a mismatch makes every authenticated API request fail, disables `cache_page`,
and leaves the WebSocket alert stream without a channel layer. It accounted for 93
of the 109 failing backend tests before it was corrected — see
[`testing.md`](testing.md) §6.1.

Two related traps:

- `REDIS_URL` and `CELERY_BROKER_URL` are **separate variables** pointing at
  different logical databases. Updating one and not the other leaves the broker
  authenticating with a stale password.
- A malformed URL fails quietly in a confusing way. If the `:` separating username
  from password is mistyped as `/`, `urlparse` reads the *username* as the hostname
  and pushes the credential into the path — Django then tries to connect to a host
  literally named `finance_analysis`. Percent-encode any password character that is
  significant in a URL (`:` → `%3A`, `/` → `%2F`, `@` → `%40`, also `#`, `?`, `%`).

Three logical databases are in use:

| URL setting | DB | Purpose |
| --- | --- | --- |
| `CELERY_BROKER_URL` | 0 | Celery broker **and** result backend |
| `REDIS_URL` | 1 | Django cache, `cache_page` responses, throttle counters, Channels layer |

`CELERY_RESULT_BACKEND` is **not** read from the environment — `config/settings/base.py`
assigns it from `CELERY_BROKER_URL`. Setting it in `.env` has no effect. See
[`../reference/env.md`](../reference/env.md).

---

## 4. Install TA-Lib

`requirements/base.txt` declares an unpinned `TA-Lib`, which is the Python
**wrapper**. It needs the underlying C library present at build or import time.
`scripts/verify_local_stack.sh` fails if `import talib` raises.

### Windows

Try the wheel first:

```powershell
.\.venv\Scripts\Activate.ps1
pip install TA-Lib
```

If that fails with a compiler error, install a prebuilt binary wheel matching
your Python version and architecture from the `cgohlke/talib-build` GitHub
releases, then:

```powershell
pip install .\ta_lib-<version>-cp314-cp314-win_amd64.whl
```

Verify:

```powershell
.\.venv\Scripts\python.exe -c "import talib; print(talib.__version__)"
```

### Linux / WSL2

Build the C library from source:

```bash
wget https://github.com/ta-lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4-src.tar.gz
tar -xzf ta-lib-0.6.4-src.tar.gz
cd ta-lib-0.6.4
./configure --prefix=/usr
make
sudo make install
cd ..
pip install TA-Lib
```

Confirm the shared library is discoverable:

```bash
python -c "import talib; print(talib.__version__)"
```

If it imports under your login shell but not under the launcher scripts, the
`.venv` was created before the library was installed — recreate it.

---

## 5. Create the virtual environment

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements/local.txt
```

```bash
# Linux / WSL2
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements/local.txt
```

**Never reuse a `.venv` copied from another OS.** The interpreter path is baked
into `pyvenv.cfg` and the console-script shims; `scripts/_native_env.*` detects
this and refuses to start rather than failing obscurely later.

`requirements/local.txt` and `requirements/production.txt` are both a single
`-r base.txt` line. There is no separate dev or test dependency set, and no
ASGI/WSGI server is declared — the local stack runs `manage.py runserver`.

---

## 6. Configure `.env`

```powershell
Copy-Item .env.example .env      # Windows
cp .env.example .env             # bash
```

Minimum viable edits:

```dotenv
DJANGO_SECRET_KEY=<something-random>
DATABASE_URL=postgres://finance_analysis:finance_analysis@<db-host>:5432/finance_analysis
REDIS_URL=redis://:<redis-password>@<redis-host>:6379/1
CELERY_BROKER_URL=redis://:<redis-password>@<redis-host>:6379/0
TUSHARE_TOKEN=<your-tushare-token>
FRONTEND_URL=http://localhost:8000
```

`FRONTEND_URL` defaults to `http://localhost:8000` because Django now serves the
built SPA itself (see §12). Password-reset and email-verification links land on
the same origin that owns `/api` and `/ws`. Operators who still run the pure-Vite
HMR flow and want email links to land on `http://localhost:5173` should override
the variable explicitly.

`manage.py` sets `DJANGO_READ_DOT_ENV_FILE=True` automatically when `.env`
exists. OS environment variables take precedence over `.env` values.

`.env.example` declares a subset of what settings actually reads. The complete
inventory, with defaults and the file:line of each read, is generated in
[`../reference/env.md`](../reference/env.md). Notably absent from the example:
`HISTORICAL_DATA_FLOOR` (defaults to `2010-01-01`), all `MACRO_*` and
`NEWS_BACKFILL_*` tuning keys, and the `EMAIL_*` group.

---

## 7. Verify before running anything else

```bash
./scripts/verify_local_stack.sh
```

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_local_stack.ps1
```

It probes PostgreSQL through `psycopg2` and both Redis databases through
`redis-py` — not through `pg_isready` / `redis-cli`, which a minimal WSL2 install
does not ship. Then it imports `celery, django, psycopg2, redis, talib`, runs
`manage.py check`, and prints the resolved `DATABASE_URL`, `CELERY_BROKER_URL`,
and cache location.

Any failure here is a configuration problem, not a code problem. Fix it before
continuing.

---

## 8. Migrate and create a superuser

```bash
python manage.py migrate
python manage.py createsuperuser
```

The database starts empty. Populating it is a separate, long-running workflow —
see [`backfill.md`](backfill.md).

---

## 9. Settings module

Two names are in play and both resolve to the same content:

| Entry point | `DJANGO_SETTINGS_MODULE` |
| --- | --- |
| `manage.py` | `config.settings` |
| `scripts/run_*.ps1` / `.sh` | `config.settings.local` |

`config/settings/__init__.py` is `from .local import *`, so they are equivalent.
`config/settings/production.py` exists but is not selected by any launcher and
has no deployment story behind it yet.

---

## 10. Start the stack

Preferred on both platforms — VS Code tasks (`Terminal → Run Task`):

| Task | Windows | Linux / WSL2 |
| --- | --- | --- |
| Run backend | `scripts/run_backend.ps1` | `scripts/run_backend.sh` |
| Run celery worker | `scripts/run_celery_worker.ps1` | `scripts/run_celery_worker.sh` |
| Run celery beat | `scripts/run_celery_beat.ps1` | `scripts/run_celery_beat.sh` |
| Run frontend | `scripts/run_frontend.ps1` | `scripts/run_frontend.sh` |
| **Run local stack** | all four in parallel | all four in parallel |

Each task carries a `linux` override in `.vscode/tasks.json`, so opening the repo
in VS Code Remote - WSL routes to the shell launchers automatically.

Or run them directly in four separate terminals:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_backend.ps1
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_celery_worker.ps1
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_celery_beat.ps1
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_frontend.ps1
```

Then:

| Surface | URL |
| --- | --- |
| Frontend (Django-served) | `http://localhost:8000/` |
| Frontend (Vite HMR) | `http://localhost:5173/` |
| API root | `http://localhost:8000/api/v1/` |
| Admin | `http://localhost:8000/admin/` |
| Swagger | `http://localhost:8000/api/v1/schema/swagger-ui/` |

Both frontend URLs work; see §12 for when to use which.

The backend binds `0.0.0.0:8000` by default; override with `DJANGO_BIND`.

---

## 11. Celery workers — one per queue

**This is the step most often missed.** Tasks are routed to four queues, and a
worker only consumes the queues it is told to. A single default worker will run
daily syncs and predictions but will **never** execute a backtest or a retrain.

| Queue | Consumes | Needed for |
| --- | --- | --- |
| `ops` (default) | syncs, indicators, sentiment, daily predictions | Normal operation |
| `backtest` | `run_backtest` | Any queued backtest |
| `train-lightgbm` | `train_lightgbm_models` | Queued LightGBM retrain |
| `train-lstm` | `train_lstm_models` | Queued LSTM retrain |

Start each in its own terminal. The launcher derives a node name from the queue
list, so several workers coexist on one host without collision:

```bash
# ops (default — CELERY_WORKER_QUEUES may be omitted)
./scripts/run_celery_worker.sh

# backtests
CELERY_WORKER_QUEUES=backtest ./scripts/run_celery_worker.sh

# retrains
CELERY_WORKER_QUEUES=train-lightgbm,train-lstm ./scripts/run_celery_worker.sh
```

```powershell
$env:CELERY_WORKER_QUEUES = 'backtest'
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_celery_worker.ps1
```

### Launcher variables

| Variable | Default | Effect |
| --- | --- | --- |
| `CELERY_WORKER_QUEUES` | `ops` | Comma-separated `-Q` list |
| `CELERY_WORKER_NODE_SUFFIX` | queue list with non-alphanumerics → `__` | Node name prefix |
| `CELERY_WORKER_HOSTNAME` | `<suffix>@%h` | Full `-n` value |
| `CELERY_WORKER_POOL` | `solo` on Windows, prefork on Linux | `--pool` |
| `CELERY_WORKER_CONCURRENCY` | `1` when pool is `solo`, else unset | `--concurrency` |
| `CELERY_LOG_LEVEL` | `info` | `-l` |
| `VENV_BIN` | `.venv/Scripts` or `.venv/bin` | Override venv location |
| `PYTHON_BIN` / `CELERY_BIN` | derived from `VENV_BIN` | Override interpreters |
| `DJANGO_BIND` | `0.0.0.0:8000` | Backend bind address |

### Windows pool caveat

Windows defaults to `--pool solo --concurrency 1`, which serialises tasks inside
a worker, because `billiard` handle inheritance is unreliable there. To get
parallelism on Windows, run **several** solo workers rather than raising
concurrency, and cap native threading in each so they do not oversubscribe the
CPU:

```powershell
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:NUMEXPR_NUM_THREADS = '1'
```

### Time limits

Global soft/hard limits are 60s / 300s. `run_backtest` overrides them to
1800s / 2100s. If a task dies with `SoftTimeLimitExceeded`, check whether it is
one of the overriding tasks before assuming a hang. The authoritative table is
generated in [`../reference/celery.md`](../reference/celery.md).

### Docker Compose workers

`compose/local/django/start-celeryworker` consumes **all four** declared queues by
default, because the Compose stack runs a single worker container. Set
`CELERY_WORKER_QUEUES` in `.env` to narrow it.

This matters because a Celery worker started without `-Q` falls back to
`CELERY_TASK_DEFAULT_QUEUE`, which is `ops`. `run_backtest` and both retrain tasks
would then be published to queues nothing reads, and would sit there silently rather
than failing — the worst outcome, since nothing surfaces the problem.

The native launcher `scripts/run_celery_worker.sh` defaults to `ops` instead, because
it is designed to be started once per queue group, each with a distinct node name
(`CELERY_WORKER_NODE_SUFFIX`). Both honour the same `CELERY_WORKER_QUEUES` variable.

`start-celerybeat` needs no `-Q`: beat publishes to queues, it does not consume them.

---

## 12. Frontend

```bash
cd frontend
npm install
npm run dev        # Vite HMR on http://localhost:5173
npm run build      # writes frontend/dist for Django to serve
```

Scripts invoke the binaries through Node directly
(`node ./node_modules/vite/bin/vite.js`) rather than through npm's shim, which
avoids a class of Windows path-resolution failures. Use `npm run <script>`, not
`npx vite`.

### Two ways to reach the dashboard

Django now ships the SPA itself (see `apps/core/views.py`,
`apps/templates/frontend/index.html`, and `DJANGO_VITE` in
`config/settings/base.py`). Both flows below stay supported:

| Flow | URL | Vite needed? | HMR? | When to use |
| --- | --- | --- | --- | --- |
| Pure Vite (unchanged) | `http://localhost:5173/` | Yes | Yes | Frontend-only work; Vite serves `frontend/index.html` and proxies `/api` and `/ws` to Django on `:8000`. |
| Django-served, dev mode | `http://localhost:8000/` | Yes | Yes | You want same-origin cookies/CSRF/WebSockets during development. Set `DJANGO_VITE_DEV_MODE=True` (default when `DJANGO_DEBUG=True`); Django renders the shell, `<script>` tags point at Vite on `:5173`. |
| Django-served, prod mode | `http://localhost:8000/` | No | No | Verifying the built bundle. Run `npm run build` once, set `DJANGO_VITE_DEV_MODE=False`; Django reads `frontend/dist/.vite/manifest.json` and emits hashed `/static/` URLs. |

In production (Docker), `DJANGO_VITE_DEV_MODE` is unset and `DEBUG=False`, so
`dev_mode` resolves to `False` automatically. The multi-stage Dockerfile builds
`frontend/dist` in a `node:22-alpine` stage and copies it into the runtime
image; `entrypoint.sh` runs `collectstatic` so WhiteNoise serves the hashed
assets from `STATIC_ROOT`.

See [`../../frontend/README.md`](../../frontend/README.md) for the frontend
architecture.

---

## 13. WSL2 for heavy workloads

For backtests, training, and Celery-parallel work, WSL2 Ubuntu outperforms the
Windows `solo` path substantially because Celery can use its normal prefork
worker model.

1. Clone the repo **inside** the WSL ext4 filesystem (`~/FinanceAnalysis`), not
   under `/mnt/c/...`. `scripts/_native_env.sh` refuses to run from a `/mnt/*`
   root.
2. Copy the root `.env` into the WSL clone.
3. Create a **fresh Linux** `.venv` there and install `requirements/local.txt`.
   The script also refuses to run if it resolves `.exe` interpreters or finds
   only a Windows-style `.venv/Scripts`.
4. Open the WSL clone in VS Code Remote - WSL. Task labels route to the `.sh`
   launchers automatically.
5. Smoke with `./scripts/verify_local_stack.sh` before starting the stack.

Windows keeps the browser, the VS Code UI, and the NVIDIA driver; WSL2 runs the
Python workloads. Both point at the same external PostgreSQL and Redis.

Note that `torch` installed from `requirements/base.txt` on Windows is a
CPU-only build. GPU-dependent paths need the WSL2 or a native Linux environment
with CUDA-enabled PyTorch installed explicitly.

---

## 14. First-run smoke check

```bash
python manage.py check
python manage.py showmigrations | grep -c '\[ \]'   # expect 0 unapplied
curl -s http://localhost:8000/api/v1/markets/ | head -c 400
```

The last call is anonymous and read-only, so it exercises routing, pagination,
and the cache without a credential. It returns an empty result set until the
database is populated — see [`backfill.md`](backfill.md).

---

## After changing Python code

Restart the processes that loaded it:

```bash
./scripts/run_backend.sh
./scripts/run_celery_worker.sh
./scripts/run_celery_beat.sh
```

`runserver` autoreloads, but Celery workers do not. Frontend changes are picked
up by Vite HMR without a restart.

---

## 15. Helper scripts

The `scripts/` directory contains several helper scripts that are not part of
the core startup flow but are useful for specific tasks:

### `run_local_stack.sh`

Runs all local services (backend, celery-worker, celery-beat, frontend) in
parallel with prefixed output. Useful for development when you want all services
running in a single terminal.

```bash
./scripts/run_local_stack.sh
```

Each service's output is prefixed with its name (e.g., `[backend]`, `[celery-worker]`)
so you can distinguish log lines. Press Ctrl+C to stop all services.

### `smoke_api_check.sh`

Runs a smoke test against the API endpoints using the credentials from `.env`
(`SMOKE_USERNAME` and `SMOKE_PASSWORD`). Verifies that authentication, key
endpoints, and pagination work correctly.

```bash
./scripts/smoke_api_check.sh
```

Requires the backend to be running. Set `API_BASE` to override the default
`http://localhost:8000/api/v1`.

### `run_staged_news_backfill.sh`

Helper for staged news backfill. Computes the backfill window from the earliest
existing `NewsArticle` row and runs the backfill in chunks. Useful for gradually
filling in historical news data without overwhelming provider rate limits.

```bash
./scripts/run_staged_news_backfill.sh
```

Environment variables:
- `PROVIDER`: News provider (default: `tushare_major`)
- `CHUNK_DAYS`: Days per chunk (default: `31`)
- `BACKFILL_FLOOR`: Earliest date to backfill (default: `2021-04-15 00:00:00`)
- `RUN_PIPELINE`: Run sentiment pipeline after ingest (default: `1`)

Requires at least one `NewsArticle` row to exist (seed current news first).
