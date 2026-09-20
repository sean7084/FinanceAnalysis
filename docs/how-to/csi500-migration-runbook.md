# CSI 500 migration runbook (one-time)

PR #23 merged the **code** switch from the CSI 300 + CSI A500 point-in-time union to a
single-index **CSI 500** (`000905.SH`) universe. Merging the code does **not** migrate the
data: the live database still holds the old universe until this runbook is executed.

> **Until step 3 completes, universe-gated workflows fail closed.** The contract now
> requires `000905.SH` point-in-time membership for every date `>= 2010-01-01`. With no
> such rows in the database, `ensure_pit_membership_coverage(...)` raises
> `PITMembershipCoverageError`, so daily prediction (heuristic/LightGBM/LSTM), backtest
> candidate selection, factor scoring, RS refresh, and `backfill_model_data` will error
> rather than silently widen to "all assets". This is by design.

Tracking issue: **#22** (`Execute CSI 500 universe data migration + retrain runbook`).

---

## Prerequisites

- The merged code (migration `markets.0013` present; `onboard_csi500_universe` available).
- `TUSHARE_TOKEN` configured and with enough quota for a full-history constituent +
  daily-basic pull across ~500 symbols from `2010-01-01`.
- A **database backup** (step 2 is destructive: it purges the legacy membership rows).
- Enough wall-clock time: the raw backfill plus LightGBM/LSTM retrain is long-running
  (hours). Run it where it will not be interrupted (tmux/screen or a supervised shell).
- Celery workers/beat can stay up, but restart them at step 6 so they pick up the new code.

---

## Running this in WSL2

Every command below is a bare `python manage.py …`, which assumes an activated
environment. In WSL2 that assumption fails quietly: Ubuntu 24.04 ships `/bin/python`
pointing at the system interpreter, so a missing venv does **not** give you
`command not found`. It gives you `manage.py`'s own hint:

```
ImportError: Couldn't import Django. Are you sure it's installed and available on your
PYTHONPATH environment variable? Did you forget to activate a virtual environment?
```

Activate the venv first, or prefix every command with `.venv/bin/python`.

WSL2 is the right host for this runbook specifically. Step 3 is hours of backfill plus
a full LightGBM and LSTM retrain; Celery's prefork pool works on Linux, whereas the
Windows launcher is pinned to `--pool solo` with concurrency 1.

### Prepare

```bash
wsl -d Ubuntu-24.04                    # from PowerShell
cd ~/FinanceAnalysis-wsl2              # NOT /mnt/c/... -- see the warning below
```

> **Check `pwd` before anything else.** The Windows clone is visible inside WSL at
> `/mnt/c/Users/<you>/Documents/FinanceAnalysis`, and it is the wrong place to run
> this. It has `.venv\Scripts\` rather than `.venv/bin/`, so `source .venv/bin/activate`
> fails with `No such file or directory` — and because a failed activation does not
> stop the next command, the `pip install` that follows runs against the system Python
> and dies with `error: externally-managed-environment` (PEP 668). That reads like a
> packaging problem and is really a wrong-directory problem.
> `scripts/_native_env.sh` also refuses to run from a `/mnt/*` root by design. See
> [`local-setup.md`](local-setup.md) §13.

```bash
# 1. Bring the clone up to date. This runbook needs the code from PR #23; a stale
#    clone does not have migration 0013 or the onboard command at all.
git fetch origin
git status -sb                         # confirm clean, and how far behind
git merge --ff-only origin/main

# 2. Dependencies move between pulls, and a stale venv fails during apps.populate()
#    before any project code runs.
source .venv/bin/activate
pip install -r requirements/local.txt

# 3. pg_dump is not installed by default and step 1 needs it.
sudo apt-get install -y postgresql-client

# 4. Prove the clone has what this runbook calls, rather than assuming the pull worked.
ls apps/markets/migrations/0013_* \
   apps/markets/management/commands/onboard_csi500_universe.py
python manage.py showmigrations markets | tail -2    # 0013 must read [ ], unchecked

# 5. Services, settings, and the suite.
bash scripts/verify_local_stack.sh     # every service must report "ready"
python manage.py test --keepdb         # cheap insurance before a destructive step
```

If step 1 reports local commits rather than a clean fast-forward, **stop and branch
them first** — see the sync rules in [`local-setup.md`](local-setup.md) §13.

### Two warnings specific to this runbook

**`python manage.py migrate` *is* step 2, and step 2 is destructive.** After a pull it
is tempting to run `migrate` as part of getting set up, because that is what
[`local-setup.md`](local-setup.md) §8 tells you to do on a fresh database. Here it
applies `markets.0013_purge_csi300_csia500_universe`, which deletes the legacy
membership, benchmark and PIT rows. Do not run it until step 1's backup exists **and
has been confirmed restorable**.

**Both clones share one PostgreSQL.** There is no separate WSL dataset, so migrating
from WSL migrates the database the Windows side uses. Bring the Windows clone up to
the PR #23 code as well before starting; otherwise it runs the old universe contract
against migrated data. Expect universe-gated workflows to fail on *both* hosts from
the moment step 2 lands until step 3 completes — that is the fail-closed behaviour
described at the top of this file, not a new problem.

### Run step 3 inside tmux

`tmux` is present on Ubuntu 24.04; `screen` is not.

```bash
tmux new -s csi500
# ... run step 3 ...
# detach:   Ctrl-b d
# reattach: tmux attach -t csi500
```

Step 3 is the long one, and a terminal or SSH disconnect without tmux loses the run
partway through a backfill. Steps 4–7 are quick enough to run directly and need
nothing WSL-specific.

### Step 6's worker restart means the Linux workers

```bash
CELERY_WORKER_QUEUES=backtest ./scripts/run_celery_worker.sh
```

The native launcher defaults to the `ops` queue only, so a single worker will never
pick up backtest or retrain tasks. Start one worker per queue group as described in
[`local-setup.md`](local-setup.md) §11.

---

## Step 0 - Record the starting state (read-only)

Capture a before-snapshot so you can prove the migration landed. From the repo root:

```bash
python manage.py shell -c "
from apps.markets.models import Asset, IndexMembership, BenchmarkIndexDaily, PointInTimeBenchmarkDaily
from django.db.models import Count, Min, Max
print('membership_by_code', dict(IndexMembership.objects.values_list('index_code').annotate(c=Count('id')).order_by()))
print('csi500_membership', IndexMembership.objects.filter(index_code='000905.SH').aggregate(n=Count('id'), mn=Min('trade_date'), mx=Max('trade_date')))
print('bench_by_code', dict(BenchmarkIndexDaily.objects.values_list('index_code').annotate(c=Count('id')).order_by()))
print('pit_by_code', list(PointInTimeBenchmarkDaily.objects.values_list('benchmark_code').annotate(c=Count('id')).order_by()))
"
```

Expected **before** the migration: `membership_by_code` shows only `000300.SH` and
`000510.CSI`; `csi500_membership` is `n=0`; `pit_by_code` shows only
`CSI300_CSIA500_PIT_UNION`.

---

## Step 1 - Back up the database

`DATABASE_URL` is **not** in your shell environment. Django loads `.env` itself
(`DJANGO_READ_DOT_ENV_FILE=True`), so every `manage.py` command connects happily while a
bare `pg_dump "$DATABASE_URL"` expands to an empty argument and falls back to libpq's
defaults: the local unix socket `/var/run/postgresql/.s.PGSQL.5432`, which does not exist
because PostgreSQL runs on another host. The error reads like a dead database and is
really an unset variable. Export it the way `scripts/_native_env.sh` does:

```bash
set -a; source .env; set +a
echo "${DATABASE_URL##*@}"     # host:port/db -- proves it resolved, prints no credentials

pg_dump "$DATABASE_URL" -Fc -f ~/finance_analysis_pre_csi500.dump
```

Do not `source scripts/_native_env.sh` instead; it applies `set -euo pipefail` and a `cd`
to your interactive shell.

Write the dump **outside the repo**. `.gitignore` has no `*.dump` rule, so a multi-GB
archive in the working tree is one `git add .` away from being committed, and keeping it
out of both clones avoids picking a side when the two share one PostgreSQL.

Step 2's purge is `RunPython.noop` on reverse, so this dump is the only way back. Prove it
is restorable rather than merely present:

```bash
ls -lh ~/finance_analysis_pre_csi500.dump
pg_restore --list ~/finance_analysis_pre_csi500.dump | grep -c 'TABLE DATA'
pg_restore --list ~/finance_analysis_pre_csi500.dump \
  | grep -E 'markets_indexmembership|markets_pointintimebenchmarkdaily'
```

Expect a few hundred `TABLE DATA` entries, and both tables step 2 purges must appear. If
`pg_dump` instead reports a server/client version mismatch, the server is PostgreSQL 15
while Ubuntu 24.04's `postgresql-client` metapackage installs 16; install
`postgresql-client-15` to match.

---

## Step 2 - Apply the schema/data migration (destructive purge)

```bash
python manage.py migrate
```

This applies `markets.0013_purge_csi300_csia500_universe`, which:

- deletes `IndexMembership` rows for `000300.SH`, `000510.CSI`, `399300.SZ`;
- deletes `BenchmarkIndexDaily` rows for those codes;
- deletes `PointInTimeBenchmarkDaily` rows with `benchmark_code='CSI300_CSIA500_PIT_UNION'`;
- strips the `CSI300` / `CSIA500` tags from every `Asset.membership_tags`;
- updates the `membership_tags` help text.

It does **not** touch OHLCV/fundamental/technical rows for ex-universe assets (those are
left as harmless orphans; prune later if desired).

**Verify:** re-run the step 0 snippet. `membership_by_code` should now be empty (or contain
no `000300.SH`/`000510.CSI`), and `pit_by_code` should no longer list
`CSI300_CSIA500_PIT_UNION`.

---

## Step 3 - Onboard the CSI 500 universe (sync + backfill + retrain)

One orchestrated command syncs memberships and benchmark history, backfills raw + derived
data for all current CSI 500 constituents, rebuilds the PIT benchmark, recomputes model
inputs, and retrains LightGBM/LSTM:

```bash
python manage.py onboard_csi500_universe --start-date 2010-01-01 --end-date <today>
```

Stages it runs, in order: `sync_index_constituents` -> `sync_benchmark_index_history`
-> `backfill_ohlcv_history` (targeted symbols, technical warm-up) -> `backfill_ohlcv_history`
(effective-universe entry warm-up) -> `backfill_fundamental_snapshots` ->
`backfill_capital_flow_snapshots` -> `backfill_technical_indicators` ->
`build_pit_union_benchmark` -> `backfill_model_data` -> `rebuild_lightgbm_pipeline` ->
`rebuild_lstm_pipeline` -> `run_reference_benchmark_suite`.

Useful flags:

- `--skip-retrain` - do the data only, retrain later in a controlled window.
- `--skip-benchmarks` - skip the post-onboarding reference benchmark suite.
- `--skip-sentiment` - skip news/sentiment backfill and retrain features.
- `--report-label <name>` / `--report-root-dir reports` - where the manifest and suites land.

It writes `reports/<label>/rollout_manifest.json` with the resolved window, index codes,
and universe symbols.

If you prefer to run stages by hand (e.g. to resume), the equivalent sequence is in
[`backfill.md`](backfill.md) followed by [`retrain.md`](retrain.md); at minimum:

```bash
python manage.py sync_index_constituents   --start-date 2010-01-01 --end-date <today>
python manage.py sync_benchmark_index_history --index-codes 000905.SH --start-date 2010-01-01 --end-date <today>
python manage.py backfill_ohlcv_history     --start-date 2010-01-01 --end-date <today> --technical-indicator-warmup
python manage.py backfill_ohlcv_history     --start-date 2010-01-01 --end-date <today> --effective-universe-entry-warmup
python manage.py backfill_fundamental_snapshots --start-date 2010-01-01 --end-date <today>
python manage.py backfill_capital_flow_snapshots --start-date 2010-01-01 --end-date <today>
python manage.py backfill_technical_indicators  --start-date 2010-01-01 --end-date <today>
python manage.py build_pit_union_benchmark  --start-date 2010-01-01 --end-date <today>
python manage.py backfill_model_data        --start-date 2010-01-01 --end-date <today>
python manage.py rebuild_lightgbm_pipeline  --start-date 2016-06-01 --end-date <train-end>
python manage.py rebuild_lstm_pipeline      --start-date 2016-06-01 --end-date <train-end>
```

**Verify:** re-run the step 0 snippet. Expect `csi500_membership` with `n > 0` and
`mn` near `2010-01`; `bench_by_code` to include `000905.SH`; `pit_by_code` to include
`CSI500_PIT`. Then confirm coverage and models:

```bash
python manage.py shell -c "
from apps.markets.models import Asset, OHLCV
from apps.prediction.models_lightgbm import LightGBMModelArtifact
from apps.prediction.models import ModelVersion
ids=list(Asset.objects.filter(membership_tags__contains=['CSI500']).values_list('id', flat=True))
print('csi500_assets', len(ids))
print('ohlcv_assets_with_data', OHLCV.objects.filter(asset_id__in=ids).distinct('asset').count() if ids else 0)
print('active_lgb', list(LightGBMModelArtifact.objects.filter(is_active=True).values_list('version','horizon_days','created_at')))
print('latest_models', list(ModelVersion.objects.order_by('-id').values_list('model_type','version','status','created_at')[:6]))
"
```

`csi500_assets` should be a few hundred; every one should have OHLCV; the active LightGBM
artifacts and latest `ModelVersion` rows should carry a fresh `created_at` (today) and a
post-switch version tag.

---

## Step 4 - Validate data quality

```bash
python manage.py validate_data_quality --start-date 2010-01-01 --end-date <today>
```

Confirm there are no `index_membership_history_gaps`, `benchmark_index_daily_gap`, or
`missing_pit_benchmark_daily` findings for `000905.SH`, and that coverage reaches back to
`2010-01`. Investigate any critical finding before promoting models.

---

## Step 5 - Regenerate the DB-derived reference sheets

`docs/reference/metrics.md` and `models.md` are generated from the database and still
describe the old universe until refreshed:

```bash
python manage.py export_documentation_facts
```

Commit the regenerated sheets (they are the only docs that change here; `commands.md`,
`celery.md`, `env.md` are unaffected by the data migration).

---

## Step 6 - Promote models and restart workers

- Confirm the intended LightGBM/LSTM artifacts are `is_active` / `READY` (see the
  promotion procedure in [`retrain.md`](retrain.md)). Roll back to the previous version if
  validation backtests look wrong.
- Restart Celery workers and beat so the daily `sync_daily_a_shares` and monthly
  `sync_monthly_index_memberships` run against `000905.SH`.

---

## Step 7 - Smoke test

- Trigger (or wait for) a daily prediction for a recent trading date and confirm rows are
  produced without `PITMembershipCoverageError`.
- Open a completed backtest in the dashboard and confirm the comparison chart shows a
  single **CSI 500** benchmark series.

---

## Rollback

- Data: restore the step 1 backup. The `0013` purge is reversible only from backup
  (`RunPython.noop` on reverse) - it does not reconstruct deleted rows.
- Code: revert PR #23 if you must return to the CSI 300 + CSI A500 union, then restore the
  backup so the membership rows match the reverted contract.

## Notes

- CSI 500 constituents are largely disjoint from the old CSI 300 union A500 pool, so this is
  a substantial raw backfill plus a full retrain, not a top-up.
- Orphaned OHLCV/fundamental/technical rows for assets that left the universe are harmless;
  prune them separately if you want to reclaim space.
- The `2010-01-01` start matches `HISTORICAL_DATA_FLOOR`; the first actual trading day is
  `2010-01-04`.
