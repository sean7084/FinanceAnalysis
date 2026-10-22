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
- **p95 at two or three cold iterations is the maximum.** Linear interpolation has
  nowhere to put a 95th percentile with that few samples. Read the cold column as
  "no cold run exceeded this", not as a distribution.
- **A page's wall-clock floor is its slowest call, not the sum.** Cold runs are
  expensive enough that the harness reports both: `sum(p50)` is total server work,
  `max(p95)` is the critical path the browser waits on.
- **`+browser` is `max(p95)` plus 0.583s**, the largest browser-only term measured
  across the eight routes in Chrome against the production build -- not an
  allowance. `page-load-latency-browser-2026-10-22.md` in this directory is that
  pass, and it checks the same budget from the other side.

## After

`ITERATIONS=3 COLD=1 FRONTEND_OVERHEAD_SECONDS=0.583 LABEL=cold-head2`,
`reports/api_latency_20261022_184111_cold-head2/`. 66 calls, 66 HTTP 200.
`+browser` is the column the budget is actually stated in: the API critical path plus
the browser term measured in Chrome against the production build.

| Page | Calls | sum(p50) | max(p95) | +browser | Budget | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| Dashboard | 2 | 0.445s | 0.500s | 1.083s | <=3.0s p95 | PASS |
| Indicator Board | 1 | 1.180s | 1.184s | 1.767s | <=3.0s p95 | PASS |
| Stock Detail | 8 | 1.070s | 0.393s | 0.976s | <=3.0s p95 | PASS |
| Macro Context | 1 | 0.076s | 0.169s | 0.752s | <=3.0s p95 | PASS |
| Models | 4 | 0.321s | 0.216s | 0.799s | <=3.0s p95 | PASS |
| Backtest | 3 | 0.789s | 0.397s | 0.980s | <=3.0s p95 | PASS |
| Alert Center | 1 | 0.062s | 0.115s | 0.698s | <=3.0s p95 | PASS |
| Settings | 2 | 0.055s | 0.048s | 0.631s | <=3.0s p95 | PASS |

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
| Indicator Board `stocks (no candidate filter)` | 1.180s | 1.184s | 0.192s |
| Dashboard `stocks (candidate filters)` | 0.368s | 0.500s | 0.315s |
| Backtest `trades` | 0.385s | 0.397s | 0.254s |
| Stock Detail `sentiment` | 0.282s | 0.393s | 0.401s |
| Backtest `runs` | 0.315s | 0.352s | 0.324s |

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
   `/screener/bottom-candidates/`, including its macro-context branch, which had
   been pulling the universe into Python to re-weight three `DecimalField`s by three
   scalars -- the weights are now bound into one annotation, ordered and sliced in
   SQL. 2.615s to 1.184s cold, `cold-all` against `cold-head2`; the same query read
   1.189s in `cold-budget-final` and 1.400s in `cold-head`, so the spread between cold
   runs of one restarted stack is a couple of hundred milliseconds, not the fix.
5. **Stock Detail queues instead of inferring.** The three prediction endpoints
   return 200 with stored rows, or empty plus
   `generation: {'status': 'queued' | 'unavailable'}`, under a per-asset-per-date
   Redis lock. A broker outage cannot fail a page load.

## What these numbers do not say

- **Browser overhead is now charged, and it was measured, not assumed.**
  `FRONTEND_OVERHEAD_SECONDS` is **0.583s** (the larger browser-only term of the two
  HEAD passes, `LABEL=cold-head` 0.552s and `LABEL=warm-head` 0.583s), read off the
  production build in a real
  browser by `scripts/measure_browser_time_to_data.sh` over all eight routes (two
  cold iterations per route, sixteen probes, all usable), and
  `page-load-latency-browser-2026-10-22.md` in this directory is that pass. The
  number is the largest browser-only term across the routes that stamp --
  `data-tti-open` plus the part of `data-tti` that is not the request (reading `tti`
  alone bills the round trip twice, because the view is stamped when its last fetch
  lands). This project still has no Playwright, and does not need one: the probe
  drives the Chrome already installed on the Windows host. The two pages that
  compose their own requests -- Stock Detail and Backtest -- had no time-to-data at
  all until they registered with the instrument, so this bullet used to read
  "nothing has read them yet" for reasons that were partly untrue.
- **Two or three cold iterations is a floor, not a percentile.** Cold means a full
  stack restart per iteration; more samples were not paid for.
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

# the browser term those two are charged with
COLD=1 ITERATIONS=2 LABEL=cold bash scripts/measure_browser_time_to_data.sh
FRONTEND_OVERHEAD_SECONDS=0.583 COLD=1 ITERATIONS=3 bash scripts/measure_page_api_latency.sh
```

Guards that keep it true: `apps.analytics.tests.DashboardStocksCacheTests`,
`apps.prediction.tests.PredictionEndpointInferenceGuardTests`,
`apps.core.tests_frontend.PageLoadLatencyHarnessTests` -- the last one derives the
harness's required coverage from `frontend/src`, so a new page-load call that goes
unmeasured, a route missing from either harness, or a page that stops registering
with the time-to-data instrument fails CI instead of quietly going unmeasured, which
is how the harness stayed wrong for a whole release before.
