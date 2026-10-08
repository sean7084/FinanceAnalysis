# How to run the services in WSL2

Everything — backend, Celery workers, beat, **and the frontend** — runs inside the WSL2 ext4
clone. The only thing on the Windows side is the browser you view the dashboard in.

## Quick start

```bash
cd ~/FinanceAnalysis-wsl2
./scripts/run_local_stack.sh
```

That is the whole day-to-day flow. It starts the full stack in one terminal, each service with
prefixed logs, and `Ctrl+C` stops them together:

| Service | What it is |
| --- | --- |
| `backend` | Django on `0.0.0.0:8000` |
| `celery-worker-ops` | `ops` queue — syncs, indicators, sentiment, daily predictions |
| `celery-worker-backtest` | `backtest` queue — queued backtests |
| `celery-worker-train` | `train-lightgbm` + `train-lstm` queues — model retrains |
| `celery-beat` | the periodic-task scheduler |
| `frontend` | Vite dev server on `0.0.0.0:5173` (started when npm is present) |

Then open **`http://localhost:5173/`** in the Windows browser. Queued backtests and retrains
are consumed with no extra steps; to start a lighter stack that covers only `ops`, set
`STACK_ALL_QUEUES=0` (see [Workers and queues](#workers-and-queues)).

`run_local_stack.sh` starts the frontend only when it detects npm. If you instead see
`npm not found — frontend will be skipped`, Node.js is missing or too old — see
[Prerequisites](#prerequisites-one-time). A first run or a stale clone also needs the
[Preflight](#preflight-first-run-or-after-a-pull).

---

## Prerequisites (one time)

The clone needs Python, the native TA-Lib C library, and a **current** Node.js. Building the
clone end to end is [`local-setup.md`](local-setup.md) §13; the packages that matter for
running every service here are:

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev build-essential git curl
```

### Node.js: the plain apt version is too old

Vite 8 requires Node `^20.19.0 || >=22.12.0`. Ubuntu's archive ships **Node 18**, so a bare
`sudo apt install nodejs npm` installs a version that will **not** run the frontend. Use the
NodeSource repo — still apt-driven — to get Node 22 LTS, which bundles npm:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
node --version    # must report v20.19+ or v22.12+
```

Behind the corporate/local proxy, export it first so `curl` and `apt` can reach the network:

```bash
export https_proxy=http://localhost:10808 http_proxy=http://localhost:10808
```

`nvm` is a no-sudo alternative: `nvm install 22 && nvm use 22`.

---

## Preflight (first run, or after a pull)

The clone at `~/FinanceAnalysis-wsl2` talks to GitHub **directly**: its git `origin` is
`https://github.com/sean7084/FinanceAnalysis.git`, so one `git fetch` sees every merged PR with no
intermediate clone. (This replaces an older two-hop setup that relayed through a Windows clone at
`/mnt/c/…/FinanceAnalysis` as `origin`; that hop is retired. A Windows clone, if you keep one, is
now an independent peer that also points at GitHub.)

Two one-time prerequisites make the direct link work from WSL:

- **Reachability** — GitHub is reached through the PassWall proxy via a repo-local setting:
  `git config --local http.https://github.com.proxy http://localhost:10808`. Direct connectivity
  also works when the proxy is down; the router host's own proxy ports are filtered from WSL, so
  use `localhost:10808`.
- **Auth** — writes authenticate through the GitHub CLI: `gh auth login` once, then
  `gh auth setup-git` wires git's credential helper to it. Reads are anonymous (the repo is public).

Before the first start — and after any pull — sync and align dependencies:

```bash
cd ~/FinanceAnalysis-wsl2
git fetch origin
git status -sb                        # confirm clean, and how far behind
git merge --ff-only origin/main
.venv/bin/pip install -r requirements/local.txt
```

Run the `pip install` even when the pull looks trivial: `requirements/base.txt` carries exact
pins, and the symptom of drift is a `ModuleNotFoundError` raised inside `apps.populate()`
before any project code runs.

Install the frontend dependencies **inside the clone** — `node_modules` is not shared with the
Windows checkout, and native modules are not interchangeable across operating systems:

```bash
cd frontend && npm ci && cd ..
```

Then verify before starting anything:

```bash
bash scripts/verify_local_stack.sh
```

It probes PostgreSQL (`psycopg2`) and Redis (`redis-py`), imports the core Python deps, and runs
`manage.py check`. It exits non-zero if either service is unreachable. If Redis auth fails while
PostgreSQL connects, the `.env` credentials are stale — the server needs a real ACL user
(`redis://finance_analysis:<pw>@…`) and any special character in the password percent-encoded.

> **Commit and push real work** — `origin` is GitHub now, so a pushed branch is backed up
> off-machine (the old two-hop clone pushed nowhere and could silently hold changes that existed
> on no other host). Still sync with `--ff-only`; if it refuses, the clone has local commits, so
> branch them rather than `git reset --hard` (unrecoverable against uncommitted work).

---

## Must run from the ext4 clone, not `/mnt/c`

`cd ~/FinanceAnalysis-wsl2` and confirm `pwd` does **not** begin with `/mnt/`. This is not a
style preference — `scripts/_native_env.sh` (sourced by every launcher) hard-guards it and
exits 1:

```
WSL detected but project root is on a Windows mount: /mnt/c/Users/<you>/Documents/FinanceAnalysis
Clone the repository into the WSL ext4 filesystem before running backend workloads.
```

A Windows clone at `/mnt/c/...` (if you keep one) has no `.venv/bin/`, and file I/O over the 9P
mount is far slower than ext4 — so run backend work from the ext4 clone. `/mnt/c` is no longer the
git `origin` (that is GitHub now), so this guard is purely about venv layout and I/O performance,
not about git sync.

---

## Frontend in WSL

With npm present, `run_local_stack.sh` starts Vite alongside the backend, so the entire stack —
including the SPA — runs in WSL. Open `http://localhost:5173/` from the Windows browser.

Vite binds `0.0.0.0:5173` (`strictPort`, so it errors rather than drifting to another port if
5173 is taken) and proxies `/api` and `/ws` to `127.0.0.1:8000` — now the backend in the *same*
WSL host, so there is no cross-OS hop for API calls. Browse via `localhost`, never via the WSL
IP: `vite.config.ts` sets `server.origin` to `http://localhost:5173`, so asset and HMR URLs are
emitted as absolute `localhost` references.

To run the frontend on its own, without the rest of the stack:

```bash
./scripts/run_frontend.sh
```

---

## Workers and queues

A worker consumes only the queues it is told to. By default `run_local_stack.sh` starts one
worker per queue group, so every queue the project publishes to is covered:

| Worker | Queue(s) | Needed for |
| --- | --- | --- |
| `celery-worker-ops` | `ops` | syncs, indicators, sentiment, daily predictions |
| `celery-worker-backtest` | `backtest` | any queued backtest |
| `celery-worker-train` | `train-lightgbm`, `train-lstm` | queued LightGBM / LSTM retrains |

Each worker gets a unique node name derived from its queue list, so they coexist on one host.
A task published to a queue nothing reads **waits silently rather than failing** — nothing
surfaces the problem. Confirm coverage with:

```bash
.venv/bin/celery -A config.celery inspect active_queues
```

**Lighter stack.** The three workers each spawn Celery's default **prefork** pool at CPU count,
which is a lot of idle processes if you are not running backtests or retrains. Start only the
`ops` worker with:

```bash
STACK_ALL_QUEUES=0 ./scripts/run_local_stack.sh
```

Then add a backtest or train worker in another terminal only when you need it:

```bash
CELERY_WORKER_QUEUES=backtest ./scripts/run_celery_worker.sh
CELERY_WORKER_QUEUES=train-lightgbm,train-lstm ./scripts/run_celery_worker.sh
```

Cap any worker's process count with `CELERY_WORKER_CONCURRENCY=N` (e.g. prefix the commands
above). Run exactly **one** beat instance (the quick start already does): duplicate schedulers
publish every periodic task twice. The prefork default is the entire reason this work lives in
WSL2 — the Windows launcher pins `--pool solo --concurrency 1`.

---

## Reaching WSL services from the Windows browser

Both the Vite dev server (`:5173`) and Django (`:8000`) listen inside WSL; whether Windows
reaches them depends on the networking mode. **`.wslconfig` is read only at VM start**, so an
edit is inert until you restart with `wsl --shutdown` (from PowerShell — it kills every process
in every distro).

| Mode | Windows → WSL | Notes |
| --- | --- | --- |
| `networkingMode=mirrored` | `localhost` works natively | Preferred. No NAT IP to churn; LAN devices can reach it too. Ports are shared — do **not** also run Vite on Windows, or `:5173` collides and `strictPort` aborts. |
| NAT (default) | via the localhost relay | `localhostForwarding=true` is the default, but the relay can be inert even when correct, and commonly breaks after sleep/resume. |

In NAT mode the WSL address is DHCP-assigned and changes on every restart, so any
`netsh interface portproxy` rule pointing at it dies silently. Probe both paths rather than
trusting the config:

```powershell
wsl -- bash -lc "hostname -I"                                      # NAT: 172.x.x.x
Invoke-WebRequest http://localhost:5173 -NoProxy                   # Vite
Invoke-WebRequest http://localhost:8000/api/v1/markets/ -NoProxy   # Django
```

If `localhost` is refused but the WSL IP answers, the relay is broken: restart WSL, add a
portproxy rule, or switch to mirrored mode.

---

## Smoke check

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py showmigrations | grep -c '\[ \]'    # expect 0
curl -s http://localhost:8000/api/v1/markets/ | head -c 400
curl -sI http://localhost:5173/ | head -n 1                    # expect HTTP/1.1 200
```

The `:8000` call is anonymous and read-only, so it exercises routing, pagination and the cache
without a credential. It returns an empty result set until the database is populated — see
[`backfill.md`](backfill.md).

---

## Stopping and restarting

`Ctrl+C` in the `run_local_stack.sh` terminal stops every service together. `runserver`
autoreloads on Python changes; **Celery workers and beat do not**, so restart the stack after
editing any code they import. Frontend changes are picked up by Vite HMR without a restart.

---

## Traps

**Node 18 from apt will not run the frontend.** Vite 8 needs Node `^20.19.0 || >=22.12.0`;
Ubuntu's archive default is 18. Install Node 22 from NodeSource (see
[Prerequisites](#prerequisites-one-time)). The symptom is Vite refusing to start or throwing on
an unsupported Node API.

**`node_modules` is per-clone.** The WSL clone does not share `node_modules` with the Windows
checkout, and native modules are not interchangeable across OSes. Run `npm ci` inside
`~/FinanceAnalysis-wsl2/frontend` after cloning and after any `package-lock.json` change.

**The launcher scripts are executable in git.** Every `scripts/*.sh` is committed mode `100755`,
so `./scripts/run_local_stack.sh` works with no `chmod`. If a future reset drops the bits and
you see `Permission denied`, use `bash scripts/run_local_stack.sh`, and repair with
`git update-index --chmod=+x`.

**Migrations hit the shared database.** Both clones point at one PostgreSQL, so
`manage.py migrate` from WSL2 migrates the database the Windows side uses. There is no dry run
and no separate WSL dataset — for destructive migrations take a real backup first and confirm
it is non-zero bytes.

---

> ### Building the clone in the first place
>
> This document *runs* the WSL2 clone. To *build* it — apt packages, TA-Lib, `venv`, the
> `.env` copy — see [`local-setup.md`](local-setup.md) §13. That document's §10–§12 remain the
> authority on launcher variables, queue semantics and the frontend flows.
