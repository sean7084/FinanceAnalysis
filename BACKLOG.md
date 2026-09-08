# Backlog

Working notes, open defects, and deferred ideas. This file absorbs content that
used to live in `README.md` as unfiled scratch, plus findings from documentation
and code audits.

It is deliberately **not** release notes — see `CHANGELOG.md` for what shipped.
Items here have no commitment attached. When something is done, move it to
`CHANGELOG.md` and delete it from here.

---

## Resolved

- **App-label test discovery failure — and a silent no-op test run.**
  `python manage.py test apps.sentiment` failed with `ImportError: attempted
  relative import with no known parent package` while the fully qualified
  `apps.sentiment.tests` worked. Root cause: `apps/__init__.py` did not exist,
  making `apps` a PEP 420 namespace package. Fixed by adding the marker file.

  The worse half, found while verifying the fix: bare `python manage.py test` —
  the invocation the old README documented — discovered **zero** tests and exited
  0. Confirmed against a pristine worktree at the pre-fix commit: `Ran 0 tests in
  0.000s`, success. The suite had been reporting green while running nothing, so
  every failure listed below was invisible until now. With the marker in place the
  same command discovers all 339. Documented in `docs/how-to/testing.md` §5.
- **README was globally gitignored.** `.gitignore` line 1 was a bare `README.md`,
  which matches at any depth. `frontend/README.md` was untracked as a result and
  no subdirectory README could be committed. Removed the rule.
- **Windows backtest parallelism.** The old note asked for multiple worker
  processes plus native-thread caps. Delivered by the four-queue Celery split
  (`ops` / `backtest` / `train-lightgbm` / `train-lstm`), per-queue worker node
  naming, and the `OMP_NUM_THREADS` / `MKL_NUM_THREADS` / `OPENBLAS_NUM_THREADS` /
  `NUMEXPR_NUM_THREADS` guidance in `docs/how-to/local-setup.md` §11.
- **Hand-transcribed reference data.** All row counts, coverage ranges, registry
  snapshots, command inventories, Celery routes, and env vars are now generated
  by `export_documentation_facts` into `docs/reference/`.
- **Dead root-level scripts.** `audit_script.py` (broken import — pulled `Asset`
  from `apps.prediction.models` where it does not exist — plus a hardcoded
  retired path), `check_indicators.py` (superseded by the generated metrics
  sheet), `query_check_v2.py` (ad-hoc query profiler with no `django.setup()`),
  and `test_register.json` (sample payload, now an inline example in
  `docs/reference/api.md`) were removed. Recoverable from git history. The empty
  `apps/analysis/` package was removed with them.
- **Scratch notes in README.** Roughly 155 lines of unfiled TODOs, pasted Celery
  logs, first-person fragments, and a stray `>>` were moved here. `README.md` is
  now orientation-only.
- **Redis credential rejected — was blocking 93 tests and all caching.** `.env`
  carried a stale password. Corrected in `REDIS_URL`, `CELERY_BROKER_URL`, and
  `CELERY_RESULT_BACKEND`; all three now authenticate, the Django cache
  round-trips, and the Channels layer completes a group add/send/discard/flush
  cycle. Recovered **92 tests** (109 failing → 17). Probing also settled the URL
  shape: this server uses a real ACL user, so `redis://:<pw>@` and
  `redis://default:<pw>@` both fail while `redis://finance_analysis:<pw>@`
  succeeds. Recorded in `docs/how-to/local-setup.md` §3 so nobody "simplifies" a
  working URL into a broken one.
- **`verify_local_stack.ps1` never actually verified anything.** It shelled out to
  `pg_isready` and `redis-cli`, neither of which ships with Windows, then downgraded
  their absence to a warning and still `exit 0`. On the primary documented platform
  the verifier introspected Django settings and printed URLs back without ever
  opening a connection — which is exactly how the stale Redis password survived.
  Both launchers now probe through `psycopg2` and `redis-py`, fail the exit code on
  any unreachable service, and redact credentials from every message including
  failures (the `.sh` previously printed the raw URL, password included, on error).
- **Analytics test fixtures never seeded the trading calendar.**
  `_make_ohlcv_sequence` created OHLCV on consecutive *calendar* days and
  `ExchangeTradingCalendar` appeared nowhere in `apps/analytics/tests.py`. Since the
  freshness guards resolve trading dates from the official calendar, an empty
  calendar made `latest_official_trade_date` return `None`, so no indicator or
  signal row was written and 8 tests failed. The helper now generates business days,
  seeds a matching open calendar row per date, derives the exchange code through the
  production `asset_exchange_code` helper so fixture and guard cannot diverge, and
  returns the dates so callers can bound windows by trading day instead of assuming
  one row per calendar day.

---

## Open — correctness

### Remaining test failures — 10, in four clusters

Down from 109 (Redis) to 17 to **10** after the calendar fixture fix. All 10 are
pre-existing; none was introduced by the documentation work or the fixture fix, and
the fixture fix was verified not to regress the chunking tests that share the helper.

**Cluster 1 — RS_SCORE fixtures (3 tests, `analytics.Phase10SignalTests`).**
`test_calculate_rs_scores_for_all_assets_{creates_high_rs_score_for_top_bucket,
filters_to_point_in_time_union_when_membership_exists,ignores_future_dated_ohlcv_rows}`.
Same root cause as the fixed cluster but in a different place: these build OHLCV
inline over 21 **consecutive calendar days** from 2024-02-01 and never seed
`ExchangeTradingCalendar`. `RS_SCORE` needs an exact 20-trading-day anchor from the
official calendar. Fix the same way — business days plus seeded calendar rows.

**Cluster 2 — data-quality continuity (3 tests, `core`).**
`DataQualityValidationCommandTests.test_validate_data_quality_writes_actionable_reports`,
`…test_technical_indicator_continuity_warnings_appear_in_summary_and_metadata`, and
`TechnicalIndicatorValidationRegressionTests.…flags_out_of_range_rsi`. Reported as
`AssertionError: 0 != 1` — the continuity report finds no issue where one is
expected. The validator was rewritten around official exchange calendars, so the
fixtures likely need the same treatment; the earlier note about
`RETURN_3D/5D/10D` disagreements in an expected indicator-type list is a second,
separate symptom in the same area and needs its own look.

**Cluster 3 — missing active artifact fixture (1 test, `backtest`).**
`Phase15BacktestTests.test_pick_candidates_filters_on_demand_lightgbm_candidates_to_point_in_time_union`
raises `ValueError: No active LightGBM artifact available for horizon 7` from
`_get_selected_lightgbm_artifact`. The test exercises on-demand LightGBM candidate
generation but does not create an active horizon-7 `LightGBMModelArtifact`. Distinct
from the calendar clusters.

**Cluster 4 — unclassified (3 tests, `analytics`).**
`Phase8IndicatorTests.test_calculate_fibonacci_retracement_creates_indicator`,
`TechnicalIndicatorBackfillCommandTests.test_backfill_technical_indicators_persists_default_non_rs_history`
(a set comparison reporting expected indicator types missing from the actual set),
and `Phase17DashboardStockApiTests.test_dashboard_stocks_overlays_runtime_lstm_candidate_payload`.
Each needs individual diagnosis; the first two may still be calendar or warmup
related, the third involves the LSTM runtime overlay.

Baseline evidence: the original 109 failures were confirmed **pre-existing** by
running the identical selection against a pristine worktree at the pre-change
commit — same test counts and same failing test names for both `apps.backtest` +
`apps.core` (94 tests) and the remaining nine modules (245 tests). No regression was
introduced by the documentation restructure. The two later reductions (109 → 17 → 10)
came from an environment credential fix and a test-fixture fix, not from any change
to production code.

### `apps/macro/tests.py` depends on a gitignored local fixture

`test_backfill_macro_snapshots_uses_yahoo_cny_usd_before_tushare_start` overrides
`MACRO_CNYUSD_CSV_PATH` with a temp file, but `backfill_macro_snapshots` also loads
the ChinaBond yield CSV from `source_data/CGBYieldCurve_2010to2016.csv`. That
directory is **gitignored**, so on a fresh clone the test fails with:

```
CommandError: Historical yield CSV not found: .../source_data/CGBYieldCurve_2010to2016.csv
```

Confirmed: this is the one test whose result differed between the working tree and
a pristine HEAD worktree, and the difference was the missing untracked file, not
code. Fix by building the yield fixture in a temp directory the way the CNY/USD
case in the same module already does.

### `makemigrations --check` can never pass

Two sets of pending migrations exist at HEAD, both pre-existing:

1. **`backtest.0003_alter_backtestrun_status`** — `0002_backtestrun_lifecycle_controls`
   declares the `status` `AlterField` **without** `default='PENDING'`, while the
   model has it. Django therefore proposes a perpetual `AlterField` whose field
   definition is otherwise identical to what 0002 already wrote. Harmless at
   runtime (Django applies defaults in Python, not as DB constraints) but it makes
   the check permanently dirty.
2. **`markets.0012_rename_*_idx`** — five index renames on `assetsuspension`,
   `benchmarkindexdaily`, `exchangetradingcalendar`, and
   `pointintimebenchmarkdaily`. `apps/markets/models.py` is byte-identical to HEAD,
   so this drift predates any current work. Unlike (1), these touch real indexes.

Generate and commit both. Do not edit the applied `0002`. Until this is done, the
`makemigrations --check` line in the `CONTRIBUTING.md` definition of done cannot be
satisfied — either fix the drift or drop that line.

Note `CHANGELOG.md` records `makemigrations --check` as clean at v0.1.9, so both
drifts were introduced between then and now.

### Non-hermetic sentiment tests

Independent of the Redis credential problem above, two tests in
`apps.sentiment.tests.Phase13SentimentTests` make real network calls rather than
using test doubles:

- `test_sentiment_latest_endpoint`
- `test_recalculate_endpoint_queues_pipeline`

Observed provider traffic during a test run:
`Total fetched: 1 items from providers: eastmoney`.

They currently fail on Redis first, which masks this defect. Once the credential
is fixed they will still vary run to run with provider availability and quota.
They need a `locmem` cache override and mocked providers.

### Implausible model accuracies in the registry

`ModelVersion` rows for `lgb-3d-2026-04-11` and `lgb-7d-2026-04-11` record
accuracies of **0.949667** and **0.882889**. Directional accuracy for this
problem sits around 0.42–0.58 across every other family. These are almost
certainly look-ahead leakage, not good models.

Worse, `EnsembleWeightSnapshot` id 1 carries a basis `lightgbm_accuracy` of
**0.916278**, so the leaked artifacts propagated into ensemble weights. That
snapshot is inactive now, but the mechanism is not guarded.

- Investigate what the 2026-04-11 feature pipeline did differently.
- Consider retiring those rows to `ARCHIVED` so they cannot be selected.
- Add a sanity bound that refuses to activate an artifact whose accuracy exceeds
  a plausible ceiling, or at least warns loudly.

### Stuck `RUNNING` backtest runs

The coverage sheet shows runs in `RUNNING` whose `created_at` is weeks old. They
will not self-heal. `apps/backtest/task_health.py` can identify them; see
`docs/how-to/runbook-sync-failure.md` §6. Needs a sweep and a decision on whether
to restart or fail them.

### Stored artifact paths are not portable

`ModelVersion.artifact_path` and LSTM `summary.json` record **absolute** paths
from whatever host trained them. The registry currently holds paths from a Docker
container (`/app/...`), a Linux home directory, and a Windows OneDrive checkout —
none of which resolve on the current host. Only the most recent LightGBM family
resolves.

Options: store repo-relative paths and resolve against `BASE_DIR` at load time, or
add a re-registration step for moved clones. A model that loads by version but
cannot find its file fails at **inference** time, not at promotion time, which is
the worst place to discover it.

### `TECHNICAL_GUIDE.md` disagrees with the staleness code

The guide states that `relative_volume_5d` and `relative_volume_20d` inherit the
5-day and 20-day moving-average gap rules (2 and 5 trading days).
`apps/analytics/technical_staleness.py` sets both to **0** in
`BASE_MAX_GAP_TRADING_DAYS`. The code is authoritative; the guide needs
correcting.

### Empty tables behind live features

`analytics_alertrule`, `analytics_alertevent`,
`analytics_screenertemplate`, and `macro_eventimpactstat` all have **0 rows**.

- `check_alert_rules` runs every 5 minutes against no rules, so the WebSocket
  alert stream connects and stays permanently silent.
- The Alert Center page combines a live socket with `/alert-events/` history and
  has nothing to show.
- Screener templates are a documented API surface with no templates.

Either seed reference data, or document these as user-provisioned and empty by
design. Right now the features look broken rather than unconfigured.

---

## Open — configuration and infrastructure

### `FRONTEND_URL` default points at a dead port

Defaults to `http://localhost:3000`; Vite serves `5173` with `strictPort: true`.
Outbound email links (verification, password reset) therefore target nothing.
Change the default and add the key to `.env.example`.

### `.env.example` is a small subset of what settings reads

9 declared keys against ~30 `env(...)` reads. Notably absent:
`HISTORICAL_DATA_FLOOR` (the governance control both main docs treat as
canonical), all `MACRO_*` and `NEWS_BACKFILL_*` tuning keys, the `EMAIL_*` group,
`FRONTEND_URL`, `ALERTS_ENABLE_SMS`, `SMS_WEBHOOK_URL`.

Also in `.env.example` but **not read by settings**: `CELERY_RESULT_BACKEND`
(hardcoded from `CELERY_BROKER_URL` in `base.py`), and
`WSL2_UBUNTU_ACCOUNT` / `WSL2_UBUNTU_PASSWORD` (no consumer anywhere — dead, and
a `*_PASSWORD` key invites a plaintext secret). `SMOKE_USERNAME` /
`SMOKE_PASSWORD` are legitimate but consumed by `scripts/`, not settings.

`docs/reference/env.md` now reports all of this automatically. Act on it.

### `BACKTEST_STALE_TASK_MAX_AGE_SECONDS` is not configurable

`apps/backtest/task_health.py` reads it via `getattr(settings, ..., 2400)`, but
the setting is never defined and never read from the environment. Changing the
stale-run threshold currently requires a code edit. Wire it through `env.int()`.

### Compose worker does not honour the queue split

`compose/local/django/start-celeryworker` runs `celery -A config.celery worker -l info`
with no `-Q`, so a Compose worker consumes only `ops` and will never execute a
backtest or a retrain. Either add the queues or document Compose workers as
`ops`-only. `start-celerybeat` and `start.sh` should be reviewed for the same
class of drift.

### OpenAPI schema metadata is stale

- `SPECTACULAR_SETTINGS['VERSION'] = '1.2.0'` — a third version scheme alongside
  the project version and the README footer, cross-referenced by nothing.
- The schema description says "Chinese Markets **(CSI 300)**", omitting CSI A500.
- The published rate-limit table omits the `auth` and legacy `user` scopes.

### Unpinned ML dependencies

`lightgbm`, `torch`, `scikit-learn`, `mlflow`, `numpy`, `akshare`, `tushare`,
`TA-Lib`, and `django-celery-beat` are all unpinned in `requirements/base.txt`,
while the guide documents exact hyperparameters and accuracies. A rebuild on a
different day can produce a different model. Pin at least the ML stack, and
record the resolved set used for each artifact family.

Also: `torch` installs as a **CPU-only** build from the default index on Windows,
which conflicts with the `windows_gpu` LightGBM inference backend and the
WSL2-as-NVIDIA-owner framing. Document which runtimes actually have GPU.

### `requirements/production.txt` cannot support the documented deployment

It is a single `-r base.txt` line. No `gunicorn`, `uvicorn`, or `daphne`
anywhere — and the WebSocket surface requires an ASGI server. The README's
production section describes Kubernetes, RDS, CDN, CI/CD, Prometheus, Sentry, and
ELK, none of which exists. Relabelled as a plan; still unbuilt.

### No CI

There is no CI configuration in the repository. `export_documentation_facts --check`
and the test suites are designed to be gate-able but nothing runs them
automatically. A minimal pipeline (lint, backend tests with `--keepdb`, frontend
tests, docs `--check`) would catch most of the drift class documented here.

### No coverage measurement

No `coverage` dependency, no `.coveragerc`. `.gitignore` anticipates `.coverage`
and `htmlcov/` but nothing generates them.

---

## Open — documentation

- Correct the `relative_volume_*` gap values in `TECHNICAL_GUIDE.md` (see above).
- Add docstrings to `apps/backtest/` (19 non-test files, ~4,100 lines, zero
  docstrings — including the 2,061-line engine in `tasks.py` that encodes the fee
  model, TP/SL exit ordering, chunked resume, and candidate generation) and to
  `apps/sentiment/` (13 files, ~1 docstring). Module-level docstrings were added
  in this pass; the deep function-level coverage of `backtest/tasks.py` is still
  outstanding.
- Duplicate `import json` at
  `apps/backtest/management/commands/run_core_backtest_matrix.py` lines 28-29.
- Clarify the relationship between `onboard_csi_a500_universe` and
  `rollout_csi_a500_universe`. Both exist; nothing says when to use which.
- `scripts/run_local_stack.sh`, `scripts/smoke_api_check.sh`, and
  `scripts/run_staged_news_backfill.sh` are referenced by no document.
- `CHANGELOG.md` 0.1.13 is an audit scratch pad, not release notes — mixed
  Chinese/English checklist with unchecked items and no
  `Objective`/`Implemented Features`/`Key Files` structure. Convert before
  tagging.
- `CHANGELOG.md` 0.1.12 states env loading moved to `.envs/.local`; that was
  reversed by the single-`.env` consolidation. Unlike the duplicate-`0.1.7`
  entry, which carries an explicit historical note, this one has no forward
  pointer.
- Decide `LICENSE`. `README.md` declares the project private and proprietary and
  a proprietary notice file now exists, but the choice should be conscious
  rather than incidental.

---

## Deferred ideas

Carried over from the old `README.md` scratch sections. Unprioritised and
unvalidated.

### Data semantics

- **OHLCV adjustment semantics.** `close` is already qfq and `adj_close` holds the
  same value; the unadjusted original close is not retained. Add a `raw_close`
  column so the two stop being "same value, different name".
- **No limit-up / limit-down rules.** Nothing judges 10% / 20% / ST limits against
  the previous close, distinguishes main board / ChiNext / STAR / BSE regimes, or
  flags return-based price anomalies. Limit-up days are not marked.
- **Macro snapshot alignment.** The yield curve uses each month's first-day data
  and CNY/USD uses the monthly open. Switch to the previous month's OHLCV, and
  align snapshots and market context by publication/availability date rather than
  hard-pasting by statistical period.
- **Rename the dollar index field.** `MacroSnapshot.dxy` actually syncs the Dow
  Jones FXCM Dollar Index Basket (`USDOLLAR`), not the ICE DXY. Rename to match
  what is stored.
- **Northbound/southbound flow.** Decide whether `moneyflow_hsgt` 北向资金 /
  南向资金 (百万元) belongs on `MacroSnapshot` — it is a market-level series, not
  a per-stock one, which is why the per-stock northbound fields were removed.

### Model and feature work

- **Provenance on artifacts and backtests.** Record
  `effective_universe_policy` version, `label_definition`, code version / git
  commit, `data_snapshot_version`, and `schema_version` on training artifacts, and
  capture the same during backtests.
- **Feature schema validation.** Persist the feature schema in the artifact and
  validate strictly at inference time, so a schema mismatch fails loudly instead
  of neutral-filling.
- **Overfitting and model quality.** The in-sample / out-of-sample alpha gap is
  the metric to attack.
- **Per-date candidate snapshots.** Persist the full cross-section so unselected
  names also carry rank, pass/fail, and `candidate_selected=False`. Today only
  executed BUY trades are explainable.
- **Prediction audit workflow.** A dedicated job joining stored predictions to
  realised returns by horizon, writing hit rate, layered returns, and calibration
  to a report. Currently only observable indirectly through backtests.
- **Promote macro series into features.** `dxy`, `cny_usd`, `cpi_yoy`, and
  `ppi_yoy` are stored but consumed by nothing.
- **Replace the heuristic in the ensemble.** It is the weakest member and drags
  the blend; a tuned multi-factor linear model would be a better complement to
  LightGBM.
- **Replace LSTM with a Transformer / TFT.** The consensus strongest architecture
  for this problem: handles multiple time scales, digests price + macro +
  sentiment together, and does not forget distant history. Expensive to tune.
- **Add a 14-day horizon.**
- **Stored analytics not feeding `technical_score`.** MACD, ADX, OBV, SMA/EMA, and
  RS score are stored but the `FactorScore` technical component is a
  bottom-fishing reversal scorer that ignores them. Decide whether that is
  intentional (it currently is) and say so where a reader will look.
- **Backfill as a validation-only step.** Consider removing backfill from the
  model rebuild path so it runs only during data validation, making rebuilds
  faster and more predictable.

### Trade execution

- **Entry price depends on the close.** Buys fill at `close + slippage`, which is
  not achievable in practice. Consider intraday minute data to model real entry
  cost.
- **Entry weekday policy.** Currently Tuesday/Thursday by default. Is that
  justified, or an artefact?
- **Position sizing.** Move from binary `suggested` to sized recommendations.
  Half-Kelly as the conservative baseline:
  `position_fraction = (win_rate × odds − loss_rate) / odds`, then halve.
  A tiered mapping on the existing `trade_score` is the cheap first step:
  10–12 → ¥10,000, 12–14 → ¥15,000, 14+ → ¥20,000 per entry.

### Product surface

- **Model version selection.** Let the operator pick a model version so the latest
  LightGBM/LSTM can be validated against previous ones: add selection to the
  backtest page, sync it to the dashboard, expose it on `BacktestRun` in admin
  defaulting to the latest, update exported reports, and wire it through the
  backend.
- **Trade-decision integration and dashboard consolidation.** Invest in the next
  layer rather than adding parallel pages.
- **Sector rotation signals.** Industry and theme rotation context to improve
  selection directionality.
- **Real NLP sentiment.** Replace the rule-based, neutral-heavy fallback with a
  finance-oriented Chinese BERT model.
- **Policy text analysis.** Parse CSRC / NDRC documents for sector-level
  directional impact.
- **Personal holdings tracking.** Let a user enter cost basis and position size
  for portfolio-specific suggestions.

### Quality targets

The bar the model work is aiming at, against the 3.2 baseline:

| Metric | Baseline | Target |
| --- | --- | --- |
| Out-of-sample return | +10.66% | > 20% (approach or beat benchmark) |
| Out-of-sample Sharpe | 0.81 | > 1.0 |
| Out-of-sample win rate | 50.56% | > 55% |
| In/out-of-sample alpha gap | ~44% | < 20% |

### Notes with no owner yet

- A backtest once ran 2061.9s and returned
  `'Backtest failed for run_id=578: SoftTimeLimitExceeded()'` while Celery logged
  it as `succeeded`. The soft limit is now 1800s for `run_backtest`, and
  `docs/how-to/runbook-sync-failure.md` §4 explains why Celery reports success on
  a failed run. Whether 1800s is the right ceiling for long windows is still open.
- Real-time strictness was scoped to backtests only. Prediction APIs, alerts, and
  the rest of the project were deliberately not switched.
- An unexplained arithmetic note survived from the old scratch section:
  `$23.7 / 0.2257 ≈ 105`. Kept here only because deleting it loses the hint that
  something was being sized.
