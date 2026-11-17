# Runbook: sync and task failure

For when scheduled ingestion did not happen, a Celery task died, or a run is
stuck. Task inventory and time limits: [`../reference/celery.md`](../reference/celery.md).

---

## 1. First: is anything actually broken?

The daily pipeline is a chain with deliberate spacing:

| Time (CST, UTC+8) | Task | Depends on |
| --- | --- | --- |
| 00:10 | `sync_daily_a_shares` | provider availability |
| 02:00 | `generate_predictions_for_date` | indicators, factors, market context |
| 02:30 | `generate_lightgbm_predictions_for_date` | same, plus an active artifact per horizon |
| every 5 min | `check_alert_rules` | indicators |

The A-share session closes at 15:00 CST. The 00:10 CST sync runs about nine hours
after that close and still belongs to the same trading day — it is the first link
in the chain that processes the day just finished, which is why it sits after
midnight rather than at 15:05. If you are checking before 00:10 CST, there is
nothing new to see yet.

Quick health check:

```bash
python manage.py export_documentation_facts --only metrics
git diff docs/reference/metrics.md
```

The `Latest` column of the coverage table shows the most recent row per table.
If OHLCV, indicators, factors, and predictions all advanced to the expected
trading day, the pipeline is healthy.

---

## 2. Diagnose: which stage stopped

Work downstream from the data, not from the logs. The first table whose `Latest`
did not advance is the failing stage; everything after it is a consequence.

| Symptom | Failing stage | Go to |
| --- | --- | --- |
| OHLCV did not advance | market sync | §3 |
| OHLCV advanced, indicators did not | indicator task or stale guard | [`runbook-stale-data.md`](runbook-stale-data.md) |
| Indicators advanced, factor scores did not | factor scoring | §3 |
| Scores advanced, predictions did not | prediction task, or no active artifact | §5 |
| ETF signal refuses to write, or the book is 100% defensive | benchmark index feed a session behind | §5.1 |
| Nothing advanced | broker, worker, or beat down | §2.1 |

### 2.1 Is the worker fleet actually up?

```bash
celery -A config.celery inspect ping
celery -A config.celery inspect active_queues
celery -A config.celery status
```

`inspect active_queues` must show a consumer for **every** queue you expect to
run. The single most common cause of "backtests never start" is that only an
`ops` worker is running. See `local-setup.md` §11.

If `inspect ping` returns nothing:

1. Is the broker reachable? `scripts/verify_local_stack.sh` probes it.
2. Is the worker process alive? On Windows the `solo` pool dies silently if the
   console window was closed.
3. Is beat running? Without beat nothing is scheduled, though manual
   `.delay()` still works.

### 2.2 Read the worker log

Worker output goes to the console that launched it — there is no log file or
aggregator configured. If you need history, redirect at launch:

```bash
CELERY_WORKER_QUEUES=ops ./scripts/run_celery_worker.sh 2>&1 | tee -a reports/ops_logs/worker-ops.log
```

`reports/` is gitignored, so this is safe.

### 2.3 "Did beat actually fire it?"

Beat's own output is the only evidence that a scheduled task was *published*, as
opposed to published and then lost. `scripts/run_celery_beat.sh` runs with
`PYTHONUNBUFFERED=1` and prints a startup banner naming its pid and the pidfile,
because a beat log that stops updating has two very different readings — the
scheduler is dead, or that file is not the one the live scheduler writes to —
and the banner tells you which.

The one thing that must not be done is start a second scheduler. `restart_cold_stack.sh`
appends to the service logs rather than truncating them (`>>`), since a restart is
the last thing that should erase the evidence about what went wrong before it.

```bash
pgrep -af '[-]A config\.celery beat'          # exactly one, with its start time
grep -c 'Scheduler: Sending due task' tmp/cold_stack/beat.log
```

`restart_cold_stack.sh` writes each service to `tmp/cold_stack/<name>.log`
(`beat.log`, `worker-ops.log`, `runserver.log`), which is where to look for a
tick you believe was skipped.

---

## 3. Recover a failed sync or derived stage

All sync and backfill commands are **idempotent** — re-running a date range
upserts rather than duplicating. Re-run the failed stage for the affected range:

```bash
# Market data for a specific window
python manage.py backfill_ohlcv_history --start-date <date> --end-date <date>

# Then the derived stages that depend on it, in order
python manage.py backfill_technical_indicators --start-date <date> --end-date <date>
python manage.py backfill_model_data --start-date <date> --end-date <date>
```

Ordering and ownership are in [`backfill.md`](backfill.md) §4. Re-running a
downstream stage without repairing the upstream one wastes hours and reproduces
the same gaps.

If the failure was an outage of the scheduler itself — the box was off, so beat
never fired — do not reconstruct the date list by hand. `manage.py catch_up`
detects and repairs exactly these holes in dependency order; see §9.

Then re-trigger the daily tasks for the missed date rather than waiting for
tomorrow's schedule:

```bash
python manage.py shell -c "
from apps.prediction.tasks import generate_predictions_for_date
from apps.prediction.tasks_lightgbm import generate_lightgbm_predictions_for_date
generate_predictions_for_date.delay('<date>')
generate_lightgbm_predictions_for_date.delay('<date>')
"
```

---

## 4. Task timeouts

### Distinguish a timeout from a hang

Global limits are **soft 60s / hard 300s**. `run_backtest` overrides these to
**soft 1800s / hard 2100s**. A log line like:

```
Task apps.backtest.tasks.run_backtest[...] succeeded in 2061.9s:
  'Backtest failed for run_id=578: SoftTimeLimitExceeded()'
```

is **not** a successful task. Celery reports `succeeded` because the task
function caught the exception and returned a string. The backtest failed. Read
the returned value, not the Celery state.

| Symptom | Cause | Action |
| --- | --- | --- |
| `SoftTimeLimitExceeded` on an `ops` task | A sync exceeded 60s | Narrow the date range, or run it as a management command instead of a task |
| `SoftTimeLimitExceeded` on `run_backtest` | Run exceeded 1800s | Reduce the window, or use `--chunk-trading-days` so the run resumes across chunks |
| `TimeLimitExceeded` (hard) | Worker killed the task | The run row may be left `RUNNING` — see §6 |
| Task never appears in the log | Wrong queue, or no consumer | §2.1 |

Management commands run in the foreground with **no** Celery time limit. For any
long or repeatedly-timing-out job, prefer the command over the task.

---

## 5. Predictions stopped

Two causes, in order of likelihood:

**No active artifact for a horizon.** LightGBM inference loads per-horizon
artifacts from `LightGBMModelArtifact`. If a horizon has no `is_active=True`
row, that horizon's LightGBM path fails while heuristic and LSTM continue —
producing partial output that looks like a data problem.

```bash
python manage.py export_documentation_facts --only models
```

Confirm three active rows, one per horizon. If any are missing, promote or
retrain — see [`retrain.md`](retrain.md) §8.

**Artifact path does not resolve.** Stored `artifact_path` values are absolute
and host-specific. The generated models sheet reports whether each resolves. A
model that loads by version but cannot find its file errors at inference time.

Also check that the upstream feature tables actually advanced (§2). Predictions
computed against stale indicators succeed silently and produce neutral-heavy
output rather than erroring.

### 5.1 The ETF rotation signal came back all-defensive

RULE scoring needs three non-null columns per candidate: the momentum column,
`vol_20d`, and the relative-strength column measured against the total-return
benchmark. `_rule_scores` drops a row that is missing any of them and never
imputes, so a feed gap does not error -- it silently shrinks the candidate pool,
and when it shrinks to nothing the plan is "100% defensive". That is a *market
call*, and the page would have shown it as one.

On 2026-10-22 `BenchmarkIndexDaily` for `H00905.CSI` was one session behind while
`000905.SH` had the day, so `rs_vs_benchmark_*` was null for all 488 eligible rows.

`generate_rotation_signal` now refuses instead of writing that book: if the pool
is non-empty, nothing scored, and a required column is null for every row, it
raises `SignalGenerationError` naming the column. Recovery:

```bash
# 1. Close the gap (the daily 01:05 CST tick is sync_etf_benchmark_index).
DJANGO_READ_DOT_ENV_FILE=True python manage.py shell -c "
from apps.markets.tasks import sync_benchmark_index_history
print(sync_benchmark_index_history(index_codes=['000905.SH','H00905.CSI'],
      start_date='2026-10-15', end_date='2026-10-22'))"

# 2. Recompute the features that read it, then regenerate the signal.
DJANGO_READ_DOT_ENV_FILE=True python manage.py compute_etf_features \
    --start 2026-10-22 --end 2026-10-22
DJANGO_READ_DOT_ENV_FILE=True python manage.py generate_etf_rotation_signal --date 2026-10-22
```

Beat ordering is the guard that keeps this from recurring: the index tick runs at
01:05 CST and the feature job at 01:20 CST, and both tasks are routed to `ops` rather
than `etf-rotation` so a two-second refresh never queues behind an hour-long
backtest. If the signal still refuses, `ETF_SECONDARY_BENCHMARK_CODES` lists what
is synced -- the total-return series is the one that is easy to forget.

---

## 6. Stuck backtest runs

A run left in `RUNNING` after its worker died will not self-heal. The system
detects this through `apps/core/task_health.py`:

| Condition | Verdict |
| --- | --- |
| `current_task_id` in a terminal state (`SUCCESS`, `FAILURE`, `REVOKED`) | Stale owner |
| `PENDING`, no `runtime_state`/`progress` in `report`, age ≥ threshold | Stale owner |
| `PENDING` **with** runtime progress | Legitimate — a chunked continuation is queued and waiting for a free worker |
| No `current_task_id`, age ≥ threshold | Stale owner |

The threshold is `BACKTEST_STALE_TASK_MAX_AGE_SECONDS` — declared at
`config/settings/base.py:533` through `env.int` with default **2400** (40 minutes), so it is set
from `.env` without a code edit; `docs/reference/env.md` lists it as environment-readable.

Recovery:

```bash
# Inspect the stuck runs
python manage.py shell -c "
from apps.backtest.models import BacktestRun
from apps.core.task_health import get_backtest_run_task_owner_state
for run in BacktestRun.objects.filter(status='RUNNING'):
    print(run.id, run.name, get_backtest_run_task_owner_state(run))
"
```

- If `has_stale_task_owner` is `True`, use the **restart** action on the run
  (API `POST /api/v1/backtest/{id}/restart/` or the admin). The serializer
  surfaces staleness, and the workbench exposes restart.
- Chunked runs resume from `BacktestRun.report.runtime_state`, so a restart
  continues rather than starting over.
- Only delete a run if its parameters are wrong; a restart preserves the row and
  its history.

---

## 7. Database connection failures

Long-running commands outlive idle connection timeouts and surface as:

```
django.db.utils.OperationalError: server closed the connection unexpectedly
django.db.utils.InterfaceError: connection already closed
```

The backtest and matrix paths handle this by calling `connections.close_all()`
and retrying once. The backfill commands do **not**.

| Situation | Action |
| --- | --- |
| Backtest failed with a connection error | Restart the run — it resumes from checkpoint state |
| Backfill failed with a connection error | Re-run with `--resume-from-checkpoint` |
| Connections fail immediately and persistently | The server is down or credentials changed — run `scripts/verify_local_stack.sh` |
| Only some workers fail | That worker holds a connection pool opened before a server restart; restart the worker |

Do not add blanket retry logic to a backfill command mid-run. Resume from the
checkpoint instead; it is what the checkpoint exists for.

---

## 8. After recovery

```bash
# Confirm coverage advanced
python manage.py export_documentation_facts --only metrics

# Confirm nothing is silently broken
python manage.py validate_data_quality \
  --start-date <first-affected-date> --end-date <today> \
  --effective-universe-only \
  --output-dir reports/ops_logs/incident_<date>

# Commit the regenerated facts with a note on what happened
```

If the incident revealed a gap in this runbook, add it here. Runbooks that are
not updated after an incident are how the next one takes longer.

---

## 9. Catching up after downtime

This box is shared, so beat and the workers get stopped for days at a time.
**Beat never replays a missed occurrence**: if the scheduler was down at the
scheduled minute, that run is simply gone. Nothing errors — each consumer falls
back to a documented neutral (`RSI`→50, `RS_SCORE`→0.5, `relative_volume`→1.0),
so the system keeps producing plausible-but-less-informed output while every
series quietly accumulates a hole the length of the outage. `apps/catchup`
repairs those holes.

### 9.1 The two triggers

| Trigger | When | Notes |
| --- | --- | --- |
| Cold-stack startup hook | `scripts/restart_cold_stack.sh`, inside the readiness-poll success branch | Runs detached **after** the readiness poll, because catch-up needs Redis (the cache lock) and the DB, and `/assets/` returning 200 is the evidence both are answering. Logs to `tmp/cold_stack/catchup.log`. `scripts/run_local_stack.sh` fires it the same detached way (deliberately *not* tracked in the `pids` array, so the `wait -n` EXIT trap does not tear the stack down when it finishes). |
| Hourly beat entry | `catch-up-missed-series-hourly`, `crontab(minute='25')` | Offset from the other hourly tick (`:45` prediction reconcile) and the `:00`/`:15` nightly chain. Runs `apps.catchup.tasks.catch_up_missed_runs` on the `ops` queue. |

Both are detached, so a kill mid-repair is harmless: the run is resumable and the
next pass re-detects whatever is still missing.

### 9.2 Survey the damage first — zero quota

```bash
python manage.py catch_up --dry-run
```

`--dry-run` runs **only the detectors**, prints a per-series gap table, makes
**zero** TuShare calls and writes **no** `CatchUpRun` rows. This is the "what did
the last outage cost me" question and is always safe. Columns are `series`,
`outcome`, `gap`, `calls`, `remaining`, `cause`, with the `detail` on the
following indented line. Real output from this machine on 2026-10-31:

```
series                outcome           gap                          calls  remaining  cause
trading_calendar      SKIPPED           2026-07-03..2026-08-13           0         42  provider_gap
                      dry run: 42 of 121 calendar days absent for SSE
suspensions           NO_GAP            none                             0          0  unknown
                      watermark fresh
ohlcv                 SKIPPED           2026-09-24..2026-10-30           0         21  provider_gap
                      dry run: 21 dates with a collapsed cross-section; one sync_daily_a_shares run repairs all of them
benchmark_index       NO_GAP            none                             0          0  unknown
                      complete
index_membership      NO_GAP            none                             0          0  unknown
                      watermark fresh
etf_daily             NO_GAP            none                             0          0  unknown
                      0 ETF trading dates absent
capital_flow          SKIPPED           2026-09-24..2026-10-30           0         21  provider_gap
fundamentals          SKIPPED           2026-09-24..2026-10-30           0         21  provider_gap
macro_core            SKIPPED           2026-07-01..2026-08-01           0          2  beat_outage
macro_yields          SKIPPED           2026-07-01..2026-08-01           0          2  beat_outage
technical_indicators  NO_GAP            none                             0          0  unknown
model_data_rs_score   NO_GAP            none                             0          0  unknown
factor_scores         NO_GAP            none                             0          0  unknown
etf_features          NO_GAP            none                             0          0  unknown
predictions           SKIPPED           2026-10-27..2026-10-30           0          4  provider_gap
                      dry run: 4 trading dates with missing stored predictions
```

The `SKIPPED` rows here are the survey *naming* the gaps it would repair on a
real run; under `--dry-run` nothing is written, so dependency gating is disabled
— otherwise every dependent series would report `BLOCKED_UPSTREAM` and hide the
very holes the survey was asked to show.

### 9.3 Bound a real run

A real run is bounded by a **call budget** and a **wall-clock budget** and works
oldest-hole-first, one unit per series per run, so it converges over repeated
hourly ticks rather than in one unbounded job. To cap a manual run:

```bash
python manage.py catch_up --max-calls 200 --max-seconds 900 --trigger manual
python manage.py catch_up --series ohlcv,capital_flow   # comma-separated subset
python manage.py catch_up --json                        # machine-readable
```

`--max-calls` / `--max-seconds` override `CATCHUP_MAX_CALLS_PER_RUN` (200) and
`CATCHUP_MAX_SECONDS_PER_RUN` (900); `--trigger` is recorded on the run rows;
`--as-of` overrides today for testing. Full option surface:
[`../reference/commands.md`](../reference/commands.md); the five `CATCHUP_*`
settings: [`../reference/env.md`](../reference/env.md). Being throttled — or
hitting `SoftTimeLimitExceeded` — stops the **whole** run, not just one series:
continuing to spend quota after a throttle is exactly how a backfill ends up
half-written and mistaken for a blackout.

### 9.4 Where the run history lives

Every real run writes `CatchUpRun` rows, surfaced read-only in the admin:
`series`, `triggered_by` (startup/beat/manual), `outcome`, `cause`,
`gap_start`/`gap_end`, `repaired_dates`, `units_done`/`units_remaining`,
`estimated_calls`, `detail`, `started_at`/`finished_at`.

| Outcome | Meaning |
| --- | --- |
| `OK` | The detected gap was fully repaired this run |
| `PARTIAL` | Some units repaired, budget ran out; `units_remaining` says how many — the next tick continues |
| `NO_GAP` | Nothing missing; detectors found the series complete |
| `BLOCKED_UPSTREAM` | A `depends_on` upstream still reports a gap, so this series was skipped this round — repairing downstream without upstream reproduces the same gap ([`runbook-stale-data.md`](runbook-stale-data.md) §4.1) |
| `SKIPPED` | Deliberately not attempted — e.g. `macro_yields`, whose `yc_cb` access is entitlement-denied on this token tier, so it is never retried |
| `FAILED` | The repair raised; read `detail` and `cause` |

`cause` is one of `beat_outage`, `task_failure`, `provider_gap`, `throttled`,
`entitlement`, `unknown`. `units_remaining` is **reporting, not state**: the
detectors are data-level, so the database itself is the checkpoint and the next
run re-detects whatever is still missing — there is no checkpoint file for the
raw layer (the derived-layer `backfill_*` commands' own `--checkpoint-file` /
`--resume-from-checkpoint` are used). `NO_GAP` and `SKIPPED` write at most one
row per (series, outcome, Beijing date) — the dedup lookup in
`apps/catchup/service.py` filters `started_at__date` on `timezone.localdate()` —
because an hourly tick across seventeen series would otherwise write hundreds of
identical rows a day; `OK`/`PARTIAL`/`BLOCKED_UPSTREAM`/`FAILED` always insert,
because they are evidence.

### 9.5 How detection works — the three detector classes

Not every series can be gap-detected the same way, and getting this wrong either
misses holes or spins forever:

| Class | Rule | Series |
| --- | --- | --- |
| **A — row presence** | An open trading day *should* have rows, so absence is a real gap | `trading_calendar`, `benchmark_index`, `etf_daily`, `macro_core` (month-starts), `fundamentals` |
| **B — watermark only** | Absence is **not** evidence of a gap; detected by reading `PeriodicTask.last_run_at` instead. If the watermark is older than `as_of - grace`, the outage window was never fetched, and the repair re-runs one idempotent command over `[last_run_at.date(), as_of]` | `suspensions` (a zero-suspension day legitimately has zero rows), `index_membership` (`index_weight` is published periodically, not daily), `macro_yields` (entitlement-denied on this token tier). Row-presence-detecting these would produce permanent false positives |
| **C — cross-sectional row count** | Per-asset series: rows are counted per date and compared against the median of the `±3` open dates on **each side** (`CATCHUP_CROSS_SECTION_MIN_RATIO`), and a date is only a gap when *both* sides are higher. This separates a genuine fetch failure (all assets missing on some dates) from the *structural* blackouts in [`runbook-provider-blackout.md`](runbook-provider-blackout.md) §4 (some assets missing on all dates — later history starts, null `pe_ttm`), which are permanent and must not be retried every hour. The reference must stay local: a universe-size step (delistings, ST filtering, a redefined effective universe) makes every date after it look collapsed against one window-wide median, and no repair can restore assets that no longer exist, so the gap would never clear and every dependent series would block upstream forever | `ohlcv`, `capital_flow`, `fundamentals` |

The registry runs the 17 series in dependency order (`trading_calendar` first,
`predictions` last), and a series whose `depends_on` upstream still reports a gap
is recorded `BLOCKED_UPSTREAM` and skipped this round.

Repair behaviours worth knowing:

- `index_membership` passes `skip_sync_dispatch=True`; without it the command
  fans out one `sync_asset_history` task per constituent — redundant work and an
  unbudgeted quota spike.
- `macro_yields` fails fast: `yc_cb` is denied on this token tier and the yield
  phase sleeps 31s between calls, so the first permission error is cached
  permanently and every later run reports `SKIPPED` / `cause=entitlement`
  without calling the provider. It is never retried.
- `predictions` only `.delay()`s `reconcile_prediction_coverage`; it never calls
  `generate_predictions_for_date` itself, which would double-run against the
  existing hourly reconcile entry that already owns that repair.
- The cache lock TTL is `max(CATCHUP_LOCK_SECONDS, max_seconds + 300)` — a lock
  that expired before the run ended would let the hourly beat entry stack a
  second pass on top of the startup hook, and both would spend the same
  per-minute quota.

### 9.6 Convergence is by design, not a defect

After e.g. a three-week outage (~15 trading days) the **raw layer** is
essentially repaired within the first startup run — `ohlcv` in particular is
repaired by a **single** `sync_daily_a_shares()` call whose `latest.date + 1 day`
resume logic fills an arbitrarily long hole in one pass, never per-date. The
**derived layer** converges at one trading day per series per run, so roughly 15
hourly ticks. This mirrors the `reconcile_prediction_coverage` philosophy: a date
missed entirely heals over several hours rather than in one job that would need a
time budget nobody would approve.

Startup-run log: `tmp/cold_stack/catchup.log`.

---

## 10. Deploying a timezone or schedule change

`TIME_ZONE`, `CELERY_TIMEZONE` and the hour numbers in `CELERY_BEAT_SCHEDULE` are
one change, not three. Beat reads those numbers against `CELERY_TIMEZONE` and does
**not** fall back to Django's `TIME_ZONE`; `apps/core/tests_beat_schedule.py`
asserts that the two agree and that every entry still fires at the instant it always
did. Ship two of the three and the whole nightly chain moves eight hours early or
late.

That is the one failure this runbook has no symptom table for, because there is no
symptom: every tick fires on time, every task succeeds, every row is written, and
the schedule merely looks healthy. The only evidence is that the data describes the
wrong day.

Order:

1. **Stop beat.** Nothing may fire while the schedule is half-applied.
2. **Stop the workers.** This must *precede* `migrate`, not merely accompany it: a
   worker still writing under the old interpretation after a correction has landed
   produces rows that satisfy the migration's predicate but that the migration will
   never revisit -- already run, and it does not run twice. Those rows stay wrong
   forever and nothing reports them.
3. **Deploy the code** — settings, `apps/core/timez.py`, the task call sites.
4. **`python manage.py migrate`, and capture what the data migration prints.** A
   correction migration should report its own row counts -- shifted, already-handled,
   untouched, total scanned, plus a per-source breakdown -- because that report is the
   only record of how much changed, and a re-run that claims everything was already
   handled is the evidence the pass is idempotent. Key the reverse off a marker the
   forward pass writes, so rows it never touched stay untouched after a rollback.
5. **Start beat.** django-celery-beat's `DatabaseScheduler` re-syncs the
   `CrontabSchedule` rows against the new table and stamps them
   `timezone='Asia/Shanghai'`. `PeriodicTask.last_run_at` is preserved, so the
   watermark-based detectors in §9.5 do not read the restart as an outage. The
   previous UTC `CrontabSchedule` rows are orphaned rather than deleted: harmless,
   since nothing references them, and recorded as a no-action decision in
   [`BACKLOG_ARCHIVE.md`](../../BACKLOG_ARCHIVE.md) §F.
6. **Start the workers.**

New rows need no attention: once the corrected call sites are deployed, writes are
stamped under the new interpretation, and the migration only has to deal with history
written before it.

### What the rehearsal proved, and where the record lives

`sentiment/0003` was rehearsed on an isolated copy of the live database and then applied
2026-11-02 19:26 CST. Both runs reported `shifted=175730`, and live's post-migration row checksum
came out **bit-identical to the copy's** -- live's pre-migration checksum had equalled it too, so
the rehearsal exercised the actual rows rather than a look-alike. That equality is the property
worth demanding of any rehearsal of this class, more than any individual count in the table it
produced.

The full record -- predicate, per-source breakdown, idempotency and reverse checks, the two
`pg_restore` foreign-key errors that were tolerated because the copy was rebuilt from a table
subset, and the backup's path, size and md5 -- is in
[`sentiment-data-corrections.md`](../reference/sentiment-data-corrections.md). The tables those
numbers describe (`sentiment_newsarticle`, `sentiment_sentimentscore`) were dropped by
`sentiment/0005` on 2026-11-11 (#52), so they are frozen evidence about a deploy, not a
description of the current database.

One outcome of that deploy is still in the schema and still unaddressed:
`django_celery_beat_crontabschedule` keeps 27 rows stamped `timezone='UTC'` alongside the
`Asia/Shanghai` rows `DatabaseScheduler.sync()` created on the first tick of step 5. They are
orphaned rather than referenced, which is the only reason they are harmless; the decision to leave
them is recorded in [`BACKLOG_ARCHIVE.md`](../../BACKLOG_ARCHIVE.md) §F.
