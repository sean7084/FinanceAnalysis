# How to run the services in WSL2

Everything — backend, Celery workers, beat, and the built SPA the backend serves — runs inside
the WSL2 ext4 clone. The only thing on the Windows side is the browser you view the dashboard
in. Vite is opt-in (`STACK_FRONTEND=1`); the day-to-day flow needs no second port.

## Quick start

```bash
cd ~/Armillary-wsl2
./scripts/run_local_stack.sh
```

That is the whole day-to-day flow. It starts the full stack in one terminal, each service with
prefixed logs both on screen and in `tmp/cold_stack/<service>.log`, and `Ctrl+C` stops them
together:

| Service | What it is |
| --- | --- |
| `backend` | Django on `0.0.0.0:8000` — the API **and** the SPA |
| `celery-worker-ops` | `ops` queue — syncs, indicators, sentiment, daily predictions |
| `celery-worker-backtest` | `backtest` queue — queued backtests |
| `celery-worker-train` | `train-lightgbm` + `train-lstm` queues — model retrains |
| `celery-worker-etf` | `etf-rotation` queue — ETF rotation signals and backtests |
| `celery-beat` | the periodic-task scheduler |

Then open **`http://localhost:8000/`** in the Windows browser. There is no second port in the
day-to-day flow: `config/settings/base.py` defaults `DJANGO_VITE_DEV_MODE=False`, so
`{% vite_asset %}` reads `frontend/dist/.vite/manifest.json` and Django serves the built bundle
from `/static/` itself. Queued backtests and retrains are consumed with no extra steps; to start
a lighter stack that covers only `ops`, set `STACK_ALL_QUEUES=0` (see
[Workers and queues](#workers-and-queues)).

Because the SPA is served from a build, **a frontend change needs `npm run build`** before a
refresh shows it — see [Traps](#traps). To get HMR back for a frontend session, start the Vite
dev server alongside everything else:

```bash
STACK_FRONTEND=1 ./scripts/run_local_stack.sh    # then open http://localhost:5173/
```

That requires npm; if you see `STACK_FRONTEND=1 but npm not found`, Node.js is missing or too old
— see [Prerequisites](#prerequisites-one-time). A first run or a stale clone also needs the
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

The clone at `~/Armillary-wsl2` talks to GitHub **directly**: its git `origin` is
`https://github.com/sean7084/Armillary.git`, so one `git fetch` sees every merged PR with no
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
cd ~/Armillary-wsl2
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
(`redis://armillary:<pw>@…`) and any special character in the password percent-encoded.

> **Commit and push real work** — `origin` is GitHub now, so a pushed branch is backed up
> off-machine (the old two-hop clone pushed nowhere and could silently hold changes that existed
> on no other host). Still sync with `--ff-only`; if it refuses, the clone has local commits, so
> branch them rather than `git reset --hard` (unrecoverable against uncommitted work).

---

## Must run from the ext4 clone, not `/mnt/c`

`cd ~/Armillary-wsl2` and confirm `pwd` does **not** begin with `/mnt/`. This is not a
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

By default there is nothing to run: Django serves the built SPA on `:8000` from
`frontend/dist`, so the page and the API are one process on one port. Build it once and after
every frontend change:

```bash
cd frontend && npm run build      # tsc -b then vite build; writes dist/.vite/manifest.json
```

`run_local_stack.sh` refuses to start without that manifest, because `{% vite_asset %}` raises
without it and every page answers 500 — a dead SPA that looks like a data failure. `collectstatic`
is **not** part of this loop: with `DEBUG=True`, `django.contrib.staticfiles` resolves the hashed
chunk straight out of `frontend/dist` through `STATICFILES_DIRS`. Run it only if you set
`DJANGO_DEBUG=False`, which moves `/static/` behind WhiteNoise at `STATIC_ROOT`.

For an HMR session, `STACK_FRONTEND=1` starts Vite alongside everything else and you browse
`http://localhost:5173/` instead. Vite binds `0.0.0.0:5173` (`strictPort`, so it errors rather
than drifting to another port if 5173 is taken) and proxies `/api` and `/ws` to
`127.0.0.1:8000`. Browse via `localhost`, never via the WSL IP: `vite.config.ts` sets
`server.origin` to `http://localhost:5173`, so asset and HMR URLs are emitted as absolute
`localhost` references.

That proxy is the reason the SPA is no longer the default entry point. While Django was still
starting it answered `/api/...` with **502 and an empty body**, and the dashboard read that as
"Failed to load dashboard data" — a message about the dashboard, for a problem that was a port in
front of a process that had not come up yet. Serving from `:8000` makes the failure honest: if the
backend is down, the page itself will not load.

To run the frontend dev server on its own, without the rest of the stack:

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

Django (`:8000`) listens inside WSL, and serves the SPA from it; `:5173` exists only for an
`STACK_FRONTEND=1` session. Whether Windows reaches them depends on the networking mode.
**`.wslconfig` is read only at VM start**, so an edit is inert until you restart with
`wsl --shutdown` (from PowerShell — it kills every process in every distro).

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
curl -sI http://localhost:8000/ | head -n 1                    # expect HTTP/1.1 200 -- the SPA
curl -s http://localhost:8000/ | grep -o 'assets/main-[A-Za-z0-9]*\.js' | head -1
```

The `:8000` call is anonymous and read-only, so it exercises routing, pagination and the cache
without a credential. It returns an empty result set until the database is populated — see
[`backfill.md`](backfill.md).

---

## Stopping and restarting

`Ctrl+C` in the `run_local_stack.sh` terminal stops every service together. Each one runs in its
own process group (`setsid`), and teardown signals the **group**, not the pid the script happened
to record — which matters because `run_backend.sh` execs into `runserver` (whose autoreload child
owns the listening socket) and `run_frontend.sh` execs into npm (whose grandchild is Vite).
Signalling only the recorded pid left both serving as orphans, and that is exactly what this
replaced.

Consequences worth knowing:

- Teardown is now ordered rather than instantaneous. `Ctrl+C` sends TERM, waits up to
  `STACK_STOP_TIMEOUT` (15s) for each group to empty, then escalates to `KILL`. A Celery worker
  finishing an in-flight task can legitimately exceed that; raise the timeout rather than let it
  be killed mid-write if that matters to you.
- A second `Ctrl+C` during teardown is deliberately ignored. An interrupted teardown is how
  orphans got created in the first place.
- `runserver` autoreloads on Python changes; **Celery workers and beat do not**, so restart the
  stack after editing any code they import. Frontend changes need `npm run build` (or HMR, above).
- Every service also appends to `tmp/cold_stack/<name>.log`, appended rather than truncated, so a
  restart never erases the evidence about what failed before it.

### Recovering an orphaned stack

If the supervisor is killed outright rather than interrupted, the services survive it. The
teardown runs from an `INT TERM EXIT` trap in `run_local_stack.sh`, so a closed terminal, a lost
SSH session or a `wsl --shutdown` kills the supervisor and never fires the trap: the workers and
beat keep their own process groups and keep serving, silently, for as long as the distro stays up.

The next start then refuses, and that refusal is correct — it is not a lock to be worked around.
Since `check_stack` now also pre-flights the ports, a stale listener is named with its pid and
age *before* anything starts, instead of aborting a second in and taking the new stack down with
it:

```
[stack] port 5173 is already held; starting here would collide with a stack this script cannot stop.
[stack]   pid 9068 (29:22 node) -- kill -TERM -- -8986
[stack] stop it first, or set STACK_RECLAIM_PORTS=1 to reclaim the port.
```

Confirm the ownership before deciding — `ppid` equal to init's (often `9`, sometimes `262`) is the
signature of an orphan, as is an `etime` far longer than the current session:

```bash
ps -eo pid,ppid,pgid,etime,cmd | grep -E 'celery -A config.celery|manage.py runserver|vite' | grep -v grep
```

Then stop the orphans **by process group**, which is precise, and start once:

```bash
for g in $(ps -eo pgid=,cmd= | grep -E 'celery -A config\.celery|manage\.py runserver|vite/bin/vite\.js' \
           | grep -v grep | awk '{print $1}' | sort -u); do kill -TERM -- "-$g"; done
pgrep -af 'celery -A config\.celery'           # must print nothing before retrying
./scripts/run_local_stack.sh
```

Prefer the group kill over `pkill -TERM -f <pattern>`. `-f` matches the **entire command line of
every process**, including the shell you are typing in: a `bash -c` whose text happens to contain
`manage.py runserver` is a legitimate target and gets taken out along with everything else in its
group. That has already misfired here once.

Celery deletes its own `celerybeat.pid` on a clean exit, so give beat a few seconds. Only remove
a leftover pidfile by hand if it survives and blocks the new start.

**`STACK_SKIP_BEAT=1` is the wrong answer when the reason you are restarting is to load new
code.** It starts workers with *no* scheduler, so nothing fires on the clock at all. It also
cannot pick up a new `CELERY_BEAT_SCHEDULE` entry: `django_celery_beat`'s `setup_schedule()`
upserts static entries into the `PeriodicTask` table only when a beat process starts, so a new
beat row appears on the next real restart, not on a beat-skipped one. Use `STACK_SKIP_BEAT=1`
only when another host or terminal already owns the scheduler and you just need workers.

Verify a restart actually registered what you expect rather than assuming it did:

```bash
DJANGO_READ_DOT_ENV_FILE=True .venv/bin/python manage.py shell -c "from django_celery_beat.models import PeriodicTask as P; \
print(list(P.objects.filter(name='catch-up-missed-series-hourly').values_list('name','enabled')))"
DJANGO_READ_DOT_ENV_FILE=True celery -A config.celery inspect registered | grep catchup
```

Both need `DJANGO_READ_DOT_ENV_FILE=True`: without it `config/settings/base.py` raises
`ImproperlyConfigured: Set the DATABASE_URL environment variable`, which looks like a
registration failure but is just an unread `.env`.

An empty `PeriodicTask` row means no new beat came up; a task missing from `inspect registered`
means the worker process still predates the code.

Read the run counters a minute or two after a scheduled minute, not immediately. Beat writes
`last_run_at` and `total_run_count` on its own sync interval, so within the first tens of seconds
after a tick the row can still show `None` / `0` even though the task already ran. Confirm against
the work the task actually did before concluding the scheduler is broken.

To keep the supervisor alive past your shell, start the stack inside `tmux` or `screen` — or as a
systemd user service. Reattaching lets `Ctrl+C` do its job later.

---

## Traps

**Node 18 from apt will not run the frontend.** Vite 8 needs Node `^20.19.0 || >=22.12.0`;
Ubuntu's archive default is 18. Install Node 22 from NodeSource (see
[Prerequisites](#prerequisites-one-time)). The symptom is Vite refusing to start or throwing on
an unsupported Node API.

**`node_modules` is per-clone.** The WSL clone does not share `node_modules` with the Windows
checkout, and native modules are not interchangeable across OSes. Run `npm ci` inside
`~/Armillary-wsl2/frontend` after cloning and after any `package-lock.json` change.

**A stale `frontend/dist` serves old JS with no error at all.** Now that Django serves the bundle
instead of compiling on demand, every frontend change needs `npm run build`, and the browser can
be running a chunk you edited an hour ago. The failure is silent because the manifest and the
hashed filenames are internally consistent — they are just both out of date. `run_local_stack.sh`
refuse to start when the manifest is *missing*; it cannot tell that it is *stale*.
`scripts/measure_browser_time_to_data.sh` can: it checks that the served HTML references the
manifest's current entry chunk, and says so when the two disagree.

**The migration guard is a startup failure, not a warning.** `run_backend.sh` refuses to serve a
schema the database does not have. Until 2026-11-06 that guard could never fire: it grepped
`'^\[ \]'` while `showmigrations --list` indents every row with a space, so it printed "all
applied" against exactly the four-migrations-behind database it was written for. It now asks
`manage.py migrate --check` directly, which parses nothing. If you edit that script, keep the
grep for *reporting* and the exit code for *deciding* — that split is the whole fix.

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
