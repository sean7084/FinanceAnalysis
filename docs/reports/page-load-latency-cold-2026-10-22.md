# Page-load latency: the cold path, 2026-10-22

Evidence for the 3-second browser time-to-data budget. Raw CSVs live in the
gitignored `reports/` tree next to the harness's own convention; this file is the
narrative, because the previous round of performance work left its after-numbers
only inside commit messages.

Host: WSL2, single developer machine, PostgreSQL and Redis on `192.168.31.8`.
Data as of factor date **2026-10-20**, CSI 500 effective universe of **500**
assets, `page_size=120`.

## Definitions, stated rather than implied

- **Cold** = Redis emptied through Django's `cache.clear()` (never `redis-cli`, so
  the `REDIS_URL` password does not land on a command line), then Django, beat and
  all five worker pools restarted, per iteration. `COLD=1` in the harness.
- **p95 at two iterations is the maximum.** Linear interpolation has nowhere to put
  a 95th percentile with two samples. Read the cold column as "no cold run exceeded
  this", not as a distribution.
- **A page's wall-clock floor is its slowest call, not the sum.** Cold runs are
  expensive enough that the harness reports both: `sum(p50)` is total server work,
  `max(p95)` is the critical path the browser waits on.

## After

`ITERATIONS=2 COLD=1 LABEL=cold-final`, `reports/api_latency_20261022_105321_cold-final/`

| Page | Calls | sum(p50) | max(p95) | Budget | Verdict |
| --- | --- | --- | --- | --- | --- |
| Dashboard | 2 | 0.383s | 0.325s | <=3.0s p95 | PASS |
| Indicator Board | 1 | 1.209s | 1.246s | <=3.0s p95 | PASS |
| Stock Detail | 8 | 1.150s | 0.268s | <=3.0s p95 | PASS |
| Macro Context | 1 | 0.066s | 0.068s | <=3.0s p95 | PASS |
| Models | 4 | 0.449s | 0.298s | <=3.0s p95 | PASS |
| Backtest | 3 | 0.896s | 0.518s | <=3.0s p95 | PASS |
| Alert Center | 1 | 0.060s | 0.061s | <=3.0s p95 | PASS |
| Settings | 2 | 0.053s | 0.051s | <=3.0s p95 | PASS |

`ITERATIONS=12 LABEL=warm-all`, `reports/api_latency_20261022_103035_warm-all/`

| Page | Calls | sum(p50) | max(p95) | Verdict |
| --- | --- | --- | --- | --- |
| Dashboard | 2 | 0.095s | 0.315s | PASS |
| Indicator Board | 1 | 0.065s | 0.192s | PASS |
| Stock Detail | 8 | 0.585s | 0.401s | PASS |
| Macro Context | 1 | 0.042s | 0.125s | PASS |
| Models | 4 | 0.164s | 0.157s | PASS |
| Backtest | 3 | 0.372s | 0.324s | PASS |
| Alert Center | 1 | 0.033s | 0.073s | PASS |
| Settings | 2 | 0.034s | 0.086s | PASS |

Slowest five cold endpoints, so the ranking is visible:

| Endpoint | cold p50 | cold p95 | warm p95 |
| --- | --- | --- | --- |
| Indicator Board `stocks (no candidate filter)` | 1.209s | 1.246s | 0.192s |
| Backtest `trades` | 0.441s | 0.518s | 0.254s |
| Models `feature importance` | 0.214s | 0.298s | 0.157s |
| Dashboard `stocks (candidate filters)` | 0.312s | 0.325s | 0.315s |
| Stock Detail `sentiment` | 0.264s | 0.268s | 0.401s |

## Before

Partial, and stated as such. The working tree was 5 ahead / 36 behind
`origin/main` when this started, and `git reset --hard origin/main` (kept on
`backup/main-pre-reconcile`) had to land first, so the "before" numbers are the
ones reproducible on the reconciled tree:

| Path | Before | How it was measured |
| --- | --- | --- |
| Dashboard `stocks (candidate filters)` | 45.56s, 46.27s | `reports/api_latency_20261022_091337_cold-before/raw.csv` -- the run was abandoned after the Dashboard group, which is why the other pages say "not measured" rather than pretending to a baseline |
| Indicator Board | one query alone at 151.7s | `SET LOCAL statement_timeout` profiling of the unbounded `timestamp__date__lte` `DISTINCT ON` over the 500-asset universe; the page itself timed out rather than returning |
| Stock Detail | inferred inline whenever a horizon had no stored row | read from the three `views*.py` stock actions, not timed |

## What changed

1. **Candidate ranking reads stored prediction rows.** The dashboard used to
   *compute* predictions for the universe when rows were missing, because the cache
   hiding that cost (`_MATRIX_SIGNAL_CACHE`) is a process-local `OrderedDict` no
   external invalidation reaches and no worker can warm. Ranking now applies
   `candidate_mode`, `trade_score_scope`, `up_threshold`, `top_n_metric`,
   `max_positions` and the macro multiplier over `LightGBMPrediction` /
   `PredictionResult` itself. `apps/backtest/tasks.py` is untouched: backtests keep
   exact behaviour, and the matrix cache stays process-local on purpose.
2. **Coverage is reported, not hidden.** A date with 120 of 500 universes stored
   returns the partial rows plus `prediction_coverage.missing` and queues one
   `reconcile_prediction_coverage` pass under a 15-minute lock, instead of spending
   45 seconds making the gap invisible. `PREDICTION_RECONCILE_MAX_ASSETS_PER_RUN`
   went 20 to 250 so the sweep can actually clear the universe in a trading day.
3. **The indicator lookup stayed inside its index.** A plain timestamp range
   (`DASHBOARD_INDICATOR_LOOKBACK_DAYS=60`) replaced `timestamp__date__lte`, whose
   function wrap made Postgres sort every historical indicator row per asset. The
   window came from measured staleness, not a guess: 14 days drops 5 of 2000
   `(asset, indicator_type)` pairs, 30 days drops 4, 60 days drops none.
4. **The page slice moved into SQL.** Every per-asset lookup is a `DISTINCT ON`
   keyed on `asset_id`, so its cost is the number of ids handed to it; the
   unfiltered board materialized 500 assets to return 120. Same fix at
   `/screener/bottom-candidates/`. 2.615s to 1.246s cold.
5. **Stock Detail queues instead of inferring.** The three prediction endpoints
   return 200 with stored rows, or empty plus
   `generation: {'status': 'queued' | 'unavailable'}`, under a per-asset-per-date
   Redis lock. A broker outage cannot fail a page load.

## What these numbers do not say

- **Browser overhead is not charged.** `FRONTEND_OVERHEAD_SECONDS` is 0.000s. The
  instrument is in place and guarded by
  `frontend/src/hooks/useApiResource.test.tsx`: a cold route load stamps
  `data-tti-open`, `data-tti` and `data-tti-path` on `<body>`, and the constant is
  `data-tti-open` plus the part of `data-tti` that is not the request (read
  `responseEnd - requestStart` off the matching
  `performance.getEntriesByType('resource')` entry, or the round trip is billed
  twice). Nothing has read them yet because there is no browser to read them
  with -- this project deliberately has no Playwright, and this WSL2 image has no
  Chrome, Chromium or Firefox binary either. So Indicator Board leaves 1.75s for
  chunk load and render, and every other page more. Filling in that constant, and
  a real-browser pass over the eight routes with it, is the open item.
- **Two cold iterations is a floor, not a percentile.** Cold means a full stack
  restart per iteration; more samples were not paid for.
- **The universe is still only partly stored.** 120 of 500 assets have LightGBM
  rows for 2026-10-20 and 0 LSTM rows. The pages are fast because they no longer
  wait for that to be true; the data is not complete yet, and `prediction_coverage`
  is where the incompleteness is visible.
- **The prewarm is no longer the thing protecting the first request.** It now
  costs ~4.4s to build both cached payloads where it used to be a ~27-minute
  inference sweep, so the cold numbers above are mostly the read path, not a
  cache-hit lottery.

## Reproduce

```bash
ITERATIONS=2 COLD=1 LABEL=cold bash scripts/measure_page_api_latency.sh
ITERATIONS=20 LABEL=warm bash scripts/measure_page_api_latency.sh
ONLY_PAGES="Indicator Board,Dashboard" COLD=1 ITERATIONS=2 bash scripts/measure_page_api_latency.sh
```

Guards that keep it true: `apps.analytics.tests.DashboardStocksCacheTests`,
`apps.prediction.tests.PredictionEndpointInferenceGuardTests`,
`apps.core.tests_frontend.PageLoadLatencyHarnessTests` -- the last one derives the
harness's required coverage from `frontend/src`, so a new page-load call that goes
unmeasured fails CI instead of quietly going unmeasured, which is how the harness
stayed wrong for a whole release before.
