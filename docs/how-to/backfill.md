# How to backfill and repair data

The option surface for every command below is generated in
[`../reference/commands.md`](../reference/commands.md). This document covers
**ordering**, **why** the order matters, and **how to resume** long runs.

---

## 1. The two rules that govern everything

### Rule 1 — the effective universe

Every cross-sectional calculation, training-sample filter, backtest candidate
pool, benchmark build, and daily prediction must resolve the same universe:

```
date >= 2010-01-01   ->  CSI 500 (000905.SH)
```

Silent fallback to "all assets" is prohibited. When required point-in-time
membership coverage is missing, the workflow **fails** rather than widening.
The canonical implementation is `apps/markets/benchmarking.py`; the rationale is
in `TECHNICAL_GUIDE.md`.

Consequence for backfills: index membership history must exist **before**
anything that computes a cross-sectional feature.

### Rule 2 — the historical floor

`HISTORICAL_DATA_FLOOR` (default `2010-01-01`) bounds how far back routine
backfills reach. Two deliberate exceptions exist:

- Exchange calendars and suspension history are backfilled from `2001-01-01`,
  because they are reference data, not features.
- Feature **warm-up** windows intentionally reach about 18 months before the
  floor, so that indicators and `RS_SCORE` are valid on the floor's first
  trading day rather than null for their first 20 sessions.

`purge_pre_floor_historical_data` enforces the floor and is dry-run by default.

---

## 2. Stage 1 — Universe and market foundation

Run these in order. Each stage depends on the previous.

```bash
python manage.py sync_index_constituents \
  --start-date 2010-01-04 --end-date <today> --skip-sync-dispatch

python manage.py backfill_ohlcv_history \
  --start-date 2010-01-04 --end-date <today> --technical-indicator-warmup

python manage.py backfill_ohlcv_history \
  --start-date 2010-01-04 --end-date <today> --effective-universe-entry-warmup

python manage.py backfill_asset_list_dates
python manage.py backfill_asset_suspensions --start-date 2001-01-01 --end-date <today>
python manage.py backfill_trading_calendar --start-date 2001-01-01 --end-date <today>
python manage.py sync_benchmark_index_history --start-date 2001-01-01 --end-date <today>
python manage.py build_pit_union_benchmark --start-date 2010-01-04 --end-date <today>
```

Why the order matters:

| Step | Depends on | Breaks if skipped |
| --- | --- | --- |
| `sync_index_constituents` | — | No membership history → universe rule cannot resolve → every later stage fails closed |
| `backfill_ohlcv_history` | membership | Nothing to compute indicators from |
| `backfill_asset_list_dates` | OHLCV | Listing-age filters and pre-listing gap exclusion |
| `backfill_asset_suspensions` | list dates | Suspension days counted as missing data |
| `backfill_trading_calendar` | — | Stale-guard and continuity checks have no authoritative open-day source |
| `sync_benchmark_index_history` | — | Official CSI comparison curves unavailable |
| `build_pit_union_benchmark` | membership + calendar | Internal PIT benchmark curve missing from backtest reports |

`--skip-sync-dispatch` on `sync_index_constituents` prevents it from enqueueing a
per-asset market sync; run the OHLCV backfill explicitly instead so you control
chunking.

`backfill_ohlcv_history` is run twice with different warm-up flags because the
two warm-ups compute different pre-window requirements. Both are idempotent.

### CSI 500 onboarding

For the end-to-end CSI 500 universe onboarding (membership sync, benchmark index
history, OHLCV, derived features, PIT benchmark rebuild, model-data backfill, and
retrain):

```bash
python manage.py onboard_csi500_universe --start-date 2010-01-01 --end-date <today>
```

Check [`../reference/commands.md`](../reference/commands.md) for the current
option surface (including the `--skip-*` stages) before choosing.

---

## 3. Stage 2 — Raw factor, macro, and news sources

These are independent of each other and can run in any order or in parallel
terminals. They depend only on Stage 1.

```bash
python manage.py backfill_fundamental_snapshots \
  --start-date 2010-01-04 --end-date <today> --repair-same-announcement-roe

python manage.py backfill_capital_flow_snapshots \
  --start-date 2010-01-04 --end-date <today>

python manage.py backfill_macro_snapshots \
  --start-date 2010-01-04 --end-date <today>

python manage.py backfill_news \
  --start-at "2010-01-04 00:00:00" --end-at "<today> 23:59:59" --run-pipeline
```

Notes:

- `backfill_news` takes **datetime** handles (`--start-at`, `--end-at`), not
  date-only ones. This is the only command in the set that does.
- `--run-pipeline` also refreshes `SentimentScore` and `ConceptHeat` after
  ingest. Without it you get articles but no scores.
- `--repair-same-announcement-roe` corrects ROE rows that were written against
  the wrong announcement date. Safe to re-run.
- Capital-flow coverage has structural gaps from upstream moneyflow and
  margin-detail blackouts. That is expected, not a failure — see
  [`runbook-provider-blackout.md`](runbook-provider-blackout.md).

---

## 4. Stage 3 — OHLCV-derived analytics and model inputs

Depends on Stages 1 and 2. These are the long-running commands; use checkpointing.

```bash
python manage.py backfill_market_context \
  --start-date 2010-01-04 --end-date <today>

python manage.py backfill_signal_events \
  --start-date 2010-01-04 --end-date <today> \
  --chunk-size-days 120 \
  --checkpoint-file reports/ops_logs/signal_events.json

python manage.py backfill_technical_indicators \
  --start-date 2010-01-04 --end-date <today> \
  --chunk-size-days 120 \
  --checkpoint-file reports/ops_logs/technical_indicators.json

python manage.py backfill_model_data \
  --start-date 2010-01-04 --end-date <today> \
  --checkpoint-file reports/ops_logs/model_data.json
```

### Which command owns which series

This is the most common source of wasted hours:

| Series | Owner | Not owned by |
| --- | --- | --- |
| `RSI`, `MACD`, `BBANDS`, `SMA`, `EMA`, `STOCH`, `ADX`, `OBV`, `FIB_RET` | `backfill_technical_indicators` | — |
| `MOM_5D/10D/20D`, `RETURN_3D/5D/10D`, `RELATIVE_VOLUME_5D/20D`, `REALIZED_VOLATILITY_5D` | `backfill_technical_indicators` | — |
| **`RS_SCORE`, `HIGH_RS_SCORE`** | **`backfill_model_data`** | `backfill_technical_indicators` will not write these |
| `SentimentScore`, `FactorScore` | `backfill_model_data` | — |
| Non-RS `SignalEvent` families | `backfill_signal_events` | — |
| `MarketContext` | `backfill_market_context` | — |

`backfill_model_data` is the storage surface for the shared OHLCV-derived model
metrics that LightGBM, LSTM, and runtime backtests all read, so it must run
**after** `backfill_technical_indicators`.

---

## 5. Checkpointing and resume

The three Stage 3 commands accept `--checkpoint-file` and
`--resume-from-checkpoint`. `reports/ops_logs/` is the conventional location
(`reports/` is gitignored, so checkpoint state never enters version control).

First run — writes progress as it goes:

```bash
python manage.py backfill_technical_indicators \
  --start-date 2010-01-04 --end-date <today> \
  --chunk-size-days 120 \
  --checkpoint-file reports/ops_logs/technical_indicators.json
```

After an interruption — resumes from the last completed chunk:

```bash
python manage.py backfill_technical_indicators \
  --start-date 2010-01-04 --end-date <today> \
  --chunk-size-days 120 \
  --checkpoint-file reports/ops_logs/technical_indicators.json \
  --resume-from-checkpoint
```

Guidance:

- **`--chunk-size-days 120`** is the practical default for the derived-analytics
  commands. Each chunk is one delete/insert transaction, so smaller chunks mean
  more transactions but cheaper retries. `--chunk-size-days 0` processes the
  whole range in one transaction — do not use it on multi-year ranges.
- **Use a dated checkpoint filename** per campaign
  (`technical_indicators_20260907.json`). Reusing one file across unrelated runs
  makes it impossible to tell what a resume will skip.
- **A checkpoint is not a receipt.** It records what was *attempted and
  completed*, not whether the result is correct. Always run Stage 5 validation
  afterwards.
- Deleting the checkpoint file restarts the campaign from the beginning. The
  commands are idempotent, so a restart is safe — just slow.

---

## 6. Stage 4 — Validation and audit

```bash
python manage.py validate_data_quality \
  --start-date 2010-01-04 --end-date <today> \
  --effective-universe-only --include-delisted \
  --output-dir reports/data_quality_<date>

python manage.py audit_model_data_quality \
  --start-date 2010-01-04 --end-date <today>
```

`validate_data_quality` writes CSV/JSON audit reports and **never mutates
historical tables**. It uses `ExchangeTradingCalendar` as the authoritative
open-day source rather than inferring trading dates from OHLCV rows, and it
classifies gaps as excused (pre-listing, delisted, suspension) or suspicious.

Useful flags:

| Flag | Effect |
| --- | --- |
| `--output-dir` | **Requires a value.** Where reports are written |
| `--only-report` | Regenerate reports from cached findings without rescanning |
| `--fail-on-critical` | Non-zero exit when critical findings exist — for scripted gates |
| `--alert` / `--alert-recipients` | Notify on findings |
| `--cross-section-audit-dates` | Sample specific dates for participant-list audits |
| `--macro-max-age-days` | Tolerance before a macro snapshot counts as stale |

`audit_model_data_quality` is the faster, narrower tool: it inspects default and
null buckets across factor, fundamental, capital-flow, and `RS_SCORE` history.
Run it first when a model looks wrong; run the full validator before a release.

---

## 7. Stage 5 — Regenerate the documentation facts

Coverage numbers in the docs are generated, not typed:

```bash
python manage.py export_documentation_facts
git diff docs/reference/
```

Review the diff — it is the most direct summary of what the backfill actually
changed. Commit it with the backfill. See `CONTRIBUTING.md`.

---

## 8. Stage 6 — Retrain

Backfilling inputs does not update deployed models. See
[`retrain.md`](retrain.md).

---

## 9. Recovery patterns

### A stage failed partway

The Stage 3 commands are idempotent per chunk. Resume with
`--resume-from-checkpoint`. If the failure was a data problem rather than an
interrupt, fix the input first — resuming will otherwise reproduce the failure
at the same chunk.

### Database connection dropped mid-run

Long backfills outlive idle connection timeouts. The backtest and matrix paths
already retry on `OperationalError` / `InterfaceError` by calling
`connections.close_all()` and re-attempting; the backfill commands do not. On a
dropped connection, resume from the checkpoint rather than restarting.

### A derived series is stale after a repair

Repair **upstream first**, then re-run the downstream owner. Re-running
`backfill_technical_indicators` will not fix a bad `RS_SCORE`, and re-running
`backfill_model_data` will not fix a bad OHLCV row. The ownership table in §4 is
the dependency map.

### You need to remove pre-floor data

```bash
python manage.py purge_pre_floor_historical_data --before-date 2010-01-01          # dry run
python manage.py purge_pre_floor_historical_data --before-date 2010-01-01 --execute
```

Read the dry-run output before adding `--execute`. This is the only destructive
command in the workflow, and it has no undo.

---

## 10. Benchmark and validation bundles

After a backfill and retrain, produce a comparable evidence bundle:

```bash
python manage.py run_reference_benchmark_suite \
  --start-date 2024-01-01 --end-date <today> \
  --output-dir reports/reference_suite_latest
```

For a large local matrix, the fastest path is inline execution with
matrix-scoped signal caching:

```bash
python manage.py run_core_backtest_matrix \
  --start-date 2025-01-01 --end-date 2025-12-31 \
  --variants top-n --sources lightgbm \
  --execute-inline --chunk-trading-days 60 \
  --lightgbm-inference-backend cpu_serial \
  --output-dir reports/<label>
```

Inline execution groups runs by horizon and drains each group round-robin,
clearing the in-process trading-date, price-map, and signal caches between
groups so a long matrix does not grow unbounded. Choose
`--lightgbm-inference-backend` deliberately — it is the dominant performance
variable, and `windows_gpu` requires a CUDA-capable runtime that the default
Windows `torch`/LightGBM install does not provide.
