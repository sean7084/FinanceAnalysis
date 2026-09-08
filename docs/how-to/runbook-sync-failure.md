# Runbook: sync and task failure

For when scheduled ingestion did not happen, a Celery task died, or a run is
stuck. Task inventory and time limits: [`../reference/celery.md`](../reference/celery.md).

---

## 1. First: is anything actually broken?

The daily pipeline is a chain with deliberate spacing:

| Time (UTC) | Task | Depends on |
| --- | --- | --- |
| 16:10 | `sync_daily_a_shares` | provider availability |
| 16:35 | `fetch_latest_market_news` | — |
| 17:00 | `run_daily_sentiment_pipeline` | news ingest |
| 18:00 | `generate_predictions_for_date` | indicators, factors, sentiment, market context |
| 18:30 | `generate_lightgbm_predictions_for_date` | same, plus an active artifact per horizon |
| every 5 min | `check_alert_rules` | indicators |

A-share markets close at 15:00 CST (07:00 UTC), so a 16:10 UTC sync is same-day.
If you are checking before 16:10 UTC, nothing is wrong yet.

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
| Predictions advanced, news/sentiment did not | news ingest | [`runbook-provider-blackout.md`](runbook-provider-blackout.md) |
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

---

## 6. Stuck backtest runs

A run left in `RUNNING` after its worker died will not self-heal. The system
detects this through `apps/backtest/task_health.py`:

| Condition | Verdict |
| --- | --- |
| `current_task_id` in a terminal state (`SUCCESS`, `FAILURE`, `REVOKED`) | Stale owner |
| `PENDING`, no `runtime_state`/`progress` in `report`, age ≥ threshold | Stale owner |
| `PENDING` **with** runtime progress | Legitimate — a chunked continuation is queued and waiting for a free worker |
| No `current_task_id`, age ≥ threshold | Stale owner |

The threshold is `BACKTEST_STALE_TASK_MAX_AGE_SECONDS`, default **2400** (40
minutes), read via `getattr(settings, ...)`. It is **not** exposed in
`config/settings/base.py` and not readable from the environment, so changing it
currently requires a code edit. Tracked in `BACKLOG.md`.

Recovery:

```bash
# Inspect the stuck runs
python manage.py shell -c "
from apps.backtest.models import BacktestRun
from apps.backtest.task_health import get_backtest_run_task_owner_state
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
