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
  `redis://default:<pw>@` both fail while `redis://armillary:<pw>@`
  succeeds. Recorded in `docs/how-to/local-setup.md` §3 so nobody "simplifies" a
  working URL into a broken one.
- **`verify_local_stack.ps1` never actually verified anything.** It shelled out to
  `pg_isready` and `redis-cli`, neither of which ships with Windows, then downgraded
  their absence to a warning and still `exit 0`. On the primary documented platform
  the verifier introspected Django settings and printed URLs back without ever
  opening a connection — which is exactly how the stale Redis password survived.
  Both launchers now probe through `psycopg2` and `redis-py` and fail the exit code on
  any unreachable service.

  *Corrected afterwards.* That commit also claimed both launchers redact credentials
  "from every message including failures". They did not, and the claim was wrong in two
  different ways. `verify_local_stack.sh` redacted its probe messages but its settings
  summary at the end of the file printed `CELERY_BROKER_URL` and the cache `LOCATION`
  raw — `redact()` was defined inside a heredoc and was not in scope twenty lines later.
  `verify_local_stack.ps1` had a second, weaker `redact_url()` in its settings probe,
  with no `try/except` and no malformed-path guard, so it leaked whenever a mistyped URL
  pushed credentials into the path component.

  Three copies of the redaction logic existed and two were defective differently. The
  duplication was the root cause, not an incidental detail — it is how one copy got
  missed while the commit message asserted otherwise. Fixed in `04faf85` by consolidating
  into a single `scripts/_stack_probe.py` that both verifiers call, which also removed
  135 lines of Python embedded in shell strings.

  Found by running the `.sh` on Linux for the first time — it had never been executed on
  any platform, because Windows users run the `.ps1`. The weaker `.ps1` `redact_url()`
  likewise looked correct on Windows, because a well-formed URL puts credentials in
  `urlparse`'s `username`/`password` fields, which it drops; the malformed-path branch
  that it failed to guard only triggers on a mistyped URL. Both defects were invisible
  until something exercised the untested path. **That is the reason this file records it:
  a fix applied to two platform variants must be verified on both, and a claim in a commit
  message is not evidence.**
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
- **RS_SCORE fixtures had the same defect in a second location.** The three
  `test_calculate_rs_scores_for_all_assets_*` tests built OHLCV inline over 21
  **consecutive calendar days** from 2024-02-01 with no calendar rows. That range
  contains only 15 weekdays, so it could never satisfy the exact 20-trading-day
  anchor `RS_SCORE` requires — the window was unsatisfiable regardless of seeding.
  Routed all three through `_make_ohlcv_sequence`, which generates 21 business days
  ending at the patched `as_of` (2024-02-21, so the window starts 2024-01-24) and
  seeds the calendar. `Phase10SignalTests` is now 17/17.
- **Backtest PIT LightGBM candidate test had two stacked defects.** First
  `ValueError: No active LightGBM artifact available for horizon 7` — `_pick_candidates`
  resolves the runtime for provenance even when the prediction call is mocked, so it
  needs both an active artifact row and a stubbed `_load_model_artifacts`. Fixing that
  exposed the second: `mock_predict.call_count` was 0 because the default `auto`
  backend resolves to `cpu_batched`, which routes through
  `_predict_lightgbm_for_assets_batched` and bypasses the mocked per-asset function
  entirely. Pinned `lightgbm_inference_backend: cpu_serial` so the test exercises the
  seam it mocks.

  *Commit provenance.* The fix landed in two pieces: the artifact fixture and stubbed
  `_load_model_artifacts` in `6ec691d`, the backend pin in `ffe2347`. The first half
  was written while PostgreSQL was unreachable and left uncommitted pending
  verification, then committed separately. `ffe2347`'s message nonetheless describes
  **both** halves at length while its diff contains only the pin, so it overclaims and
  reads as though it duplicated `6ec691d`. It did not — the tree is correct and nothing
  is duplicated. Left uncorrected because both commits are published on `origin/main`;
  rewording would rewrite three SHAs and force-push `main`, which is not worth it for a
  prose defect, and would leave the WSL2 clone diverged. The durable fix is the
  "describe the staged diff, not the session" rule in `CONTRIBUTING.md`.
- **`apps.analytics.tests` is fully green (39/39, was 13 failing).** Beyond the two
  above: extracted a reusable `_seed_trading_calendar(asset, dates)` helper for
  fixtures that build OHLCV inline; seeded it in `Phase8IndicatorTests.setUp`, which
  is why `test_calculate_fibonacci_retracement_creates_indicator` got `None` back;
  corrected two RSI date-set assertions that still expected five consecutive
  *calendar* days after the window became five *trading* days spanning a weekend; and
  added `model_version=None` to the LSTM prediction stub, whose signature predated
  the model-version-selection work.
- **Migration drift cleared — `makemigrations --check` exits 0**, so the
  `CONTRIBUTING.md` definition of done is satisfiable at last. Two migrations were
  pending, and neither touches data. `backtest.0003_alter_backtestrun_status` is a
  proven no-op: `sqlmigrate` emits literally `-- (no-op)`, because `default` is a
  Python-level attribute and `0002` had simply omitted it. `markets.0012` is five
  `RenameIndex` operations, which PostgreSQL executes as `ALTER INDEX ... RENAME TO
  ...` — a catalog-only rename with no table rewrite and no meaningful lock. Before
  applying, verified that all five *old* index names existed in the dev database and
  none of the new ones did; that is the precondition for the rename to succeed, and a
  database built fresh under Django 6 would already carry the new names and fail. The
  drift came from auto-generated `models.Index` names (no explicit `name=`) being
  rehashed by a Django upgrade.
- **Compose worker now consumes all four declared queues.** `start-celeryworker` had no
  `-Q`, so Celery fell back to `CELERY_TASK_DEFAULT_QUEUE` (`ops`) and the single
  Compose worker would never run a backtest or a retrain — published to queues nothing
  reads, sitting there silently rather than failing. Now defaults to all four and is
  overridable via `CELERY_WORKER_QUEUES`, matching the native launcher's variable. The
  native script keeps its `ops` default because it is started once per queue group with
  a distinct node name; that model does not apply to a single container. Also switched
  to `exec` so celery becomes PID 1 and receives SIGTERM for a warm shutdown.
  `start-celerybeat` was reviewed and needs no `-Q` — beat publishes, it does not
  consume.
- **`FRONTEND_URL` default corrected to `http://localhost:5173`.** It was `3000`, a
  port nothing in this project listens on, while Vite pins 5173 with `strictPort: true`
  and so can never fall back to it. The setting builds the verification and
  password-reset links in `apps/users/views.py`, so both were dead out of the box.
  (Both the setting and `apps/users` were deleted in v0.1.17; recorded here as the
  history of the fix.)
- **`.env.example` is now complete: 33 of 33 settings-read variables.** Previously 9
  declared keys against ~30 reads, so `HISTORICAL_DATA_FLOOR` — the governance control
  both main docs treat as canonical — was undiscoverable from the example file, along
  with every `MACRO_*` and `NEWS_BACKFILL_*` tuning key and the whole `EMAIL_*` group.
  Each is listed at the default the code actually uses, with the non-obvious ones
  annotated. `WSL2_UBUNTU_ACCOUNT` / `WSL2_UBUNTU_PASSWORD` were removed: no consumer
  anywhere, and a `*_PASSWORD` key in a committed template invites a plaintext secret.
  `docs/reference/env.md` now reports zero missing variables and zero inert keys.
- **`export_documentation_facts` scans Compose entrypoints too.** Its consumer scan only
  looked in `scripts/`, so `CELERY_WORKER_QUEUES` — read by a container on every boot —
  would have been reported as "no consumer found — inert". The generated sheet is only
  trustworthy if its detection covers every place a key can be consumed.
- **The `weighting` default was decided by a pre-registered holdout.** `equal` won the
  2016–2023 tuning window (+99.69% / Sharpe 0.5357 vs `score`'s +71.73% / 0.4437) but
  lost the 2024-01-01..2026-10-20 holdout on every criterion: −7.15% / 28.80% maxDD /
  Sharpe 0.0913, versus `score` +1.70% / 26.02% / 0.1987 and `invvol` −7.78% / 26.30% /
  0.0782. The default stays `score` in all three places that carry it; the
  contradiction is the evidence that the tuning-window ranking was overfit, recorded in
  `docs/plans/etf-rotation-selection-sizing-2026-10-23.md` §7.7. That window is spent:
  no later phase may query it again.
- **The live ledger states a yuan PnL.** `ETF_SIM_CAPITAL_BASE` (default 1,000,000)
  turns each new signal leg's target weight into a whole-lot share count, so
  `mark_etf_rotation_positions_daily` computes `pnl=(exit−entry)×shares−both
  commissions` instead of leaving it NULL; setting the base to 0 restores the old
  `shares=NULL` behaviour. Carried holdings keep the shares they were opened with,
  so a position opened while the base was 0 stays NULL rather than being restated.
  The dev database's OPEN rows were cleared on 2026-10-24 and the next signal run is
  what writes shares, so the ledger card keeps showing `--` until that run happens;
  the duplicate rows from pre-carry-forward regeneration (159887.SZ and 515300.SH)
  were deleted with that cleanup and the carry-forward path prevents their return.

---

## Open — feature expansion

- **Index valuation is blocked by data coverage, not by code.** `index_dailybasic` covers six
  broad indices (12.1% of the pool), so the feature family is switched off by design
  (report section 14). Re-open it only with a source that covers thematic indices (CSIndex
  downloads or Wind), and re-check the 70% coverage floor first.
- **P30 (constituent breadth) is terminated on coverage, like P28.** The data layer is built
  (`IndexWeightSnapshot`, `sync_index_weight`, `attach_index_breadth`, migration 0020's four
  columns) but TuShare's `index_weight` has no 2016-2017 monthly snapshots for most thematic
  indices: only **53.2% of eligible rows** carry breadth, against a 70% floor. On the covered
  rows `idx_share_above_ma60` is 0.83-correlated with `ma60_ratio`, and the one orthogonal
  feature (`idx_mom_consistency`) has ICIR 0.13. Reopen only with a source that has monthly
  weights back to 2016, or accept a post-2020 measurement window (which invalidates every
  reading since P18). The sync itself resumes with `--only-missing`.
- **P31 (share flows) is done and measured**: 95.8% eligible coverage, features enabled, and
  no increment in either arm (report section 19).
- **P32 (macro regime) is done**: the gate uses a 35-day publication lag instead of a new
  release-date column; yields remain NULL because `yc_cb` is not permitted on this token.

## Open — measurement infrastructure

- **The instrument is noisier than the effect (report section 16.1).** RULE has no learned
  parameters, yet its median block Sharpe reads 0.0791 / 0.4460 / -0.5284 across three panel
  starts -- roughly one Sharpe of swing from block placement alone. Six blocks of ~20
  rebalances cannot resolve a 0.1-0.5 effect. Until the block layout changes (more blocks, or
  coarser blocks with 60+ rebalances, and a fixed 4-5 start set declared in the
  pre-registration), no arm's single-point win is adjudicable and P35 stays no-go.
- **P25 (label horizon) is measured, and the answer is "20 days stays".** The table shape
  still holds one label horizon per row, so the sweep recomputes the label from `ETFDaily`
  instead of re-backfilling: ICIR falls 0.59 -> 0.40 from 20 to 60 sessions (report section
  21). A training-label change is not justified, and the sweep is reusable via
  `--horizons 20,40,60`.
- **P22 is built; P38 stays deferred.** `ETFRotationSignal.arm` (migration 0018) plus the
  per-arm ledger/report/task plumbing means two candidate books can accumulate in parallel
  without displacing each other. Nothing is observing forward because no arm passed the
  tightened gate (report section 17.2): the machinery exists for the next qualifying arm.
- **P39 (single-shot activation runbook) has nothing to activate yet.** Writing step numbers
  for a model that failed replication would be ceremony; the runbook belongs with the first
  arm that clears both legs.

- **Purge and block layout are now coupled correctly, but the §11 readings are superseded.**
  Report §12 replaces them. Any future rematch must carry `--cpcv-retrain` plus the default
  ML purge; a run at `--cpcv-purge` below the label horizon is refused by the command.
- **The sensitivity harness and the ledger are built.** `measure_etf_rotation_sensitivity`
  refuses any cell not declared in the pre-registration file and records its hash; the ledger
  counts how often a context has been read. The open item is no longer tooling but block
  layout (first bullet).
- **Rolling vs expanding is measured** (report sections 15-16): the same RULE arm swings
  ~1 Sharpe between plans, so the fold shape is a measurement variable, not a strategy knob.
  An LSTM arm on rolling folds is still unmeasured and would need the per-fold retrain path
  extended to that model.

## Open — ML rematch

- **The "ML as a filter" arm has had its sensitivity test, and it does not replicate.** This
  entry used to quote 0.2906 (LightGBM veto) against 0.2227 (RULE) from report §11.5-§11.6 and
  ask for a sensitivity round; both are now done and the numbers are superseded. Those readings
  came from `purge=1`, i.e. a fold model trained on bars inside its own held-out block (§12).
  At the label-window purge (20) the same six blocks read 0.5400 for the LightGBM veto against
  RULE's 0.1881, and the pre-registered three-start grid
  (`docs/reports/sensitivity/sensitivity-veto-2026-10-25-604e01283f7c.md`, ledger ids
  `22c886be1a53`/`6a3be427e12c`/`c850d4c52216` and the 2017/2018 rows listed in §17.1) gives
  `lgb-veto` **2/3** and `lstm-veto` **1/3**, the LightGBM arm failing at the 2018 start
  (−0.4900 against RULE 0.2306). Under the gate tightened in plan §9 — replication across ≥2
  veto models or ≥2 panel starts — the arm does not qualify for forward observation, so P38
  stays closed. What is still open is not the veto hypothesis but the instrument: see "the
  instrument is noisier than the effect" above.

## Closed — research programme P18-P37 (2026-10-27)

The measurement/verification gaps left after PR #73 are closed with evidence in
`docs/reports/etf-rotation-cpcv-model-comparison-2026-10-25.md` §27–§37:

- **#72** — every measurement path writes a ledger row (walk-forward, pinned CPCV, retrain),
  and the report counts reads per panel start.
- **#71** — four labels including `downside_rank`, `LABEL_SCHEMA_VERSION` on artifacts, and
  label/ablation pivots from ledger rows.
- **#69** — variance table (seed spread) and the P35 verdict re-derived from the declared
  per-fold IC / ICIR criteria; all ML arms fail mean IC.
- **#13** — ML stack pinned in `requirements/base.txt`; `library_versions` recorded on
  artifacts and in the ledger context.
- **#68** — per-series release lags replace the 35-day constant; entitlement probe filed;
  macro regime attribution and the five gate-only inputs implemented. (b) won't-fix:
  date-level series cannot separate two funds and would invalidate the existing ledgers.
- **#51** — macro freshness reported with age; yields remain NULL because `yc_cb` is denied,
  recorded as unavailable rather than fabricated.
- **#67** — split/unexplained classification, an opt-in adjustment (default off), and a filed
  backfill-quality report. (b) not run: the flow flag's forward-return spread is −0.10% on 51%
  of dates, so regime-slicing a null effect would not change a decision.
- **#65** — coverage against the ever-eligible, ever-in-book and current-rankable denominators,
  plus a filed coverage-bias measurement (no systematic bias).
- **#66** — four valuation rules and a filed spot check; the family is terminated because only
  6 of 299 mapped indices have rows (2.01%), not because a value looks wrong.
- **#5** — activation refuses accuracy > 0.75 / IC > 0.30; two 2026-04 artifacts archived;
  pinned loaders refuse archived versions.
- **#70** — rolling/expanding fold shape adjudicated on per-fold ICIR; no ML arm passes the
  pre-registered bar, and the plan gap is smaller than the required effect.
- **#6** — `sweep_stale_backtest_runs` reports/clears orphaned RUNNING rows for both run types.
- **#7 / #11** — verified already satisfied (ETF artifact paths are relative; the stale-task
  threshold is wired); closed with evidence.

Still open from this programme: nothing. Both trainers now accept `--feature-set`
(`all`/`no_flow`), the ledger context names the subset instead of hardcoding `all`, and the
artifact records it in `ModelVersion.metadata`; the feature-set axis therefore has two
columns, though a `no_flow` reading still needs a pre-registered panel batch. The fourth
label's data landed on 2026-10-31 (`--recompute-labels-only`, single process); the sample
windows and the labelled-row set were verified unchanged.


## Open — correctness

### The 7-day stock LightGBM model has below-coin-flip directional accuracy

Retrained on the `2016-06-01` date-contract window, the three stock horizons scored
accuracy 0.5492 (3-day), **0.4874 (7-day)** and 0.5744 (30-day), on 1.25M / 1.25M /
1.24M samples respectively. The 7-day model is worse than a coin flip on its own
training window's evaluation split, and it is the horizon the daily beat consumes
alongside 3-day.

This is **not** caused by the narrower window, and the numbers say so directly: the
superseded `lgb-*-2026-11-03-adj-fix` rows trained from the `2010-01-01` floor on
~1.99M samples scored 0.5226 / **0.4835** / 0.5984. The 7-day horizon was already
sub-random before the contract, on 60% more data, and it is the only horizon that got
*no* better after -- 3-day improved 0.5226 -> 0.5492. Compare the paired rows in
`docs/reference/models.md` before assuming a retrain fixed it.

Two things make this worth opening rather than shrugging at:

* The stock training path reports **only** `accuracy`, with no time-based holdout and no
  calibration or rank metric. So these figures are in-sample and cannot be compared
  across windows the way the ETF `rank_ic` series can. We are flying the most-consumed
  horizon without an out-of-sample number at all.
* A below-0.5 accuracy is either noise around 0.5 or an inverted signal. Distinguishing
  those needs a held-out window, which this path does not produce.

Measure the 7-day model on a genuine time-based holdout before trusting or replacing it,
and add at least one out-of-sample metric to `train_lightgbm_models` so this is
detectable without a manual retrain. Found while re-baselining the stock models for
`apps/core/date_contract.py`; the re-baseline did not cause it, it is the first time the
number was looked at.

### Stock headline metrics were re-baselined onto the `2016-06-01` window

Done as part of the date-contract change: `rebuild_lightgbm_pipeline --skip-backfill`
was run for horizons 3, then 7 and 30. Numbers are in `CHANGELOG.md` under version
0.1.53. The remaining gap is the missing out-of-sample metric above, not a stale window.

**Not gating, by design:** `docs/reference/metrics.md` and `models.md` are in
`DB_GATED_SHEETS` (`apps/core/management/commands/export_documentation_facts.py:1132`).
CI regenerates them against a freshly created, table-less service database because
migrations have not run at that step, so a "fresh" CI render is a wall of
`UNAVAILABLE ... <exc>` rows that can never equal a developer's populated render in
either direction -- `--check` skips both for exactly that reason. They have been
refreshed locally for this change and committed. Budget ~9 minutes with warm caches and
40+ cold for `export_documentation_facts --only metrics,models`: it scans ~30 tables
including ~174M rows of `analytics_technicalindicator` (`pg_stat_activity`: `active` /
`IO` / `DataFileRead`, real sequential scans, not the wedge below). Run it detached.

Note for anyone re-running this: each invocation rebuilds the entire feature matrix
(~2.9M OHLCV rows, then one pass per indicator at 10-110 s, then per-asset masking over
1,296 assets) before it trains, so one horizon costs roughly the same as three. Pass all
horizons in a single call. One early attempt made no progress for 41 minutes at zero
client CPU while the test suite was also hammering the database; it was killed rather
than nursed, and the retry on a quieter system finished normally. Whether that was the
wedge below or pure I/O contention was never confirmed -- `pg_stat_activity` was not
sampled at the time, so do not record it as a reproducing case.

### The two ETF trainers disagree on max positions: LightGBM holds 5, the LSTM 3

`apps/etf_rotation/train_lightgbm.py:1059` reads `ETF_ROTATION_MAX_POSITIONS`, which no
settings module defines, so it keeps its fallback of 5. `apps/etf_rotation/train_lstm.py:1260`
reads `ETF_MAX_POSITIONS`, which `config/settings/base.py:336` sets to 3. The comment at
`train_lstm.py:1257-1259` already flags the asymmetry. Two serving models of the same
strategy therefore size positions differently, and any comparison between them is
comparing different portfolios.

Decide which is intended, then delete the other name. `/data-limits` now shows both
values side by side so the divergence cannot be missed again. Found while wiring the
date contract, which needed the thresholds resolved the way the code resolves them.

### The 250-session minimum train window is six duplicated literals

`WALK_FORWARD_MIN_TRAIN_SESSIONS = 250` (`run_etf_rotation_backtest.py:126`),
`min_train_days: int = 250` (`walk_forward.py:232`), `_setting_int('ETF_MIN_TRAIN_DAYS', 250)`
at `train_lightgbm.py:974` and `:1041`, `train_lstm.py:1250`, plus the `(250)` in
`train_etf_rotation_model.py:71` help text. `ETF_MIN_TRAIN_DAYS` is not in settings, so the
fallback is always the live value.

`ETF_BACKTEST_START` in `apps/core/date_contract.py` is derived from this number -- it is the
251st eligible session after the training start -- so moving one without the other silently
breaks the contract's coherence. `apps/core/tests_date_contract.py` pins the arithmetic
against a synthetic calendar, but that tests the rule, not the duplication. Consolidate on
one named constant next to the contract. Found while writing the contract tests.

### `sentiment_score` is 0.5 on all 2,043,000 COMPOSITE factor rows, so it is a zero-variance feature

`audit_model_data_quality --start-date 2010-01-01 --end-date 2026-11-03` (exit 0) reported, for the
whole 2010-2026 window:

```
field=sentiment_score total=2043000 non_null=2043000 null=0 zero=0 half=2043000
```

Not one row differs from the neutral default. `FactorScore.technical_score` by comparison is fully
populated and varied. The earlier full-universe `validate_data_quality` run independently logged
`factor_score.sentiment_score.neutral_default_value` as an info item on 2,041,651 rows, so the two
audits agree.

Consequences worth deciding on:
- `sentiment_score` is one of the 39 features in the LightGBM feature matrix and is used in
  `feature_source_asof` checks. A constant column cannot split, so it contributes nothing and the
  model pays for it in every training pass.
- `SENTIMENT_7D` and `sentiment_7d_avg_20d` in the feature matrix derive from `SentimentScore` rows
  that *do* exist (5,637,394 fetched for 2010..2026), so the raw sentiment data is present -- it is
  the **composite factor's** sentiment component that is defaulted. Look for the scoring path that
  should have written a non-neutral value and is not, rather than assuming sentiment is unavailable.
- Either fix the composite scoring, or drop the feature and shrink the schema. Leaving it is the
  worst option because it looks like a live signal in `docs/reference/models.md`.

### The newest trading session is missing for 1,680 of 1,886 assets

Measured 2026-11-06: per-date OHLCV counts run 1,759 / 1,759 / 1,760 / 1,760 / 1,760 / 1,758 for
2026-10-26..2026-11-02, then collapse to **206** on 2026-11-03. By newest-row-per-asset, 1,552
assets stop at 2026-11-02 and 1,680 predate 2026-11-03 entirely.

This is an operational catch-up gap, not a value defect: `docs/reference/metrics.md` recorded OHLCV
coverage ending at 2026-10-16 before the adjustment-basis work, and the beat and workers were then
stopped for ~27 hours to keep the machine clear for the retrains, so no scheduled sync ran. The
repair filled history through 2026-11-02 for the assets it walked.

Expect `catch_up --trigger startup`, fired by `scripts/run_local_stack.sh`, to close it. Verify after
restart that 2026-11-03 and 2026-11-06 each reach ~1,760 rows before trusting
`validate_data_quality --effective-universe-only --fail-on-critical`, whose remaining 34,416
`missing_ohlcv` criticals are partly this gap. Note the same run leaves a long tail of assets whose
newest row is far older (3 assets end 2024-06-21, 2 end 2026-09-14) -- those are the delisted and
suspended names and should be excused by `list_date`/`delist_date`, which is the NULL-list_date
problem above.

### `validate_data_quality` is slow because it fetches through a server-side cursor, the same trap the trainer just hit

**Measured outcome 2026-11-08 — the cursor fix landed, and it was not the main cost.** The
rewrite replaced every `.iterator(chunk_size=...)` with a windowed bulk fetch and narrowed the
largest scan from `indicator_type__in=<19>` to `indicator_type='RS_SCORE'` (105x fewer rows, a
bitmap index instead of a seq scan; EXPLAIN and a byte-identical six-month A/B are recorded in
`apps/core/db_scans.py`). Then the full range was run with that rewrite in place:
`--effective-universe-only` over the whole history was **killed at the 90-minute timeout without
finishing** (`VALIDATE_RC=124`). Sampling `pg_stat_activity` through the run showed the bulk of
the time in the **per-asset** OHLCV continuity phase, which the rewrite does not touch.

So the premise in this entry — that the coverage scans drive the 16-year runtime — is wrong. The
gate is dominated by validating 1,886 assets across ~4,000 sessions one asset at a time. Making
it usable needs that phase addressed (restrict to sessions where the asset is in the effective
universe, or parallelise across assets), which is separate work.

What did land is still worth having: no named cursor anywhere in the command, 105x fewer rows in
the largest scan, one window of peak memory instead of one history of rows, and the long read
transaction that pinned xmin is gone. **Do not quote a full-range speedup — there is not one.**

**Profiled 2026-11-08: it is round-trip bound, and the fix is batching, not scanning.** One year
of data under `cProfile` (`tmp/vdq.prof`): `cursor.execute` accounts for **722 s of 812 s — 89%
of the run — across only 7,327 calls**, i.e. **~99 ms per query**. That is latency on the
WSL2<->NAS link, not row volume or CPU. `_write_feature_continuity_gaps` alone is 484 s (60% of
the total) over 602 assets, and it issues **six queries per asset**: `FundamentalFactorSnapshot`,
`CapitalFlowSnapshot`, `AssetMoneyFlowSnapshot`, two `AssetMarginDetailSnapshot` reads, and
`TechnicalIndicator`. 602 x 6 = ~3,600 round trips for one year; 16 years is the 90-minute run.

So the next change should collapse those per-asset queries into a few batched reads grouped in
Python — the same shape `_write_effective_universe_daily_coverage` already uses. Expected effect
is roughly an order of magnitude, because the cost is per round trip rather than per row.

**Then the micro-benchmark corrected the cause, and it is worse than "round trips".** EXPLAIN
(ANALYZE, BUFFERS) on the per-asset technical-indicator read, same window:

| read | execution | shared buffers |
| --- | --- | --- |
| first (cold) | 700-900 ms | `read=170..290`, `hit≈4,100` |
| re-read (warm) | 12-27 ms | `read=0`, `hit≈4,600` |

Same plan, same rows (6,692), and a variant without the `timestamp__date` cast changed nothing.
The 800 ms is **cold block reads over the NAS link** — not planning, not the cast, not the filter.
~35x between cold and warm.

That advantage does not survive a whole run. Two consecutive one-year invocations measured **91 s
and 87 s**, so the second got nothing, because the working set exceeds shared_buffers and early
blocks are evicted before the run ends. (A profiled run of that same window took 812 s while the
cache was cold — a ~9x spread attributable to cache state, not to code.) So the answer is not
"run it twice": it is to read each block **once** in a batched pass and consume the rows while
they are hot.

Not attempted here on purpose. This is the release gate, its failure mode is silent under-reporting
rather than a crash, and the change needs an old-vs-new equivalence run on a fixed window (the
method used for the coverage scans) before it can be trusted. A subtly wrong gate is worse than a
slow one.


Observed 2026-11-06 while running the gate: after ~58 minutes the command was still executing,
with the backend in `wait_event_type=IO, wait_event=DataFileRead` on
`FETCH FORWARD 50000 FROM "_django_curs_12349"`. The quoted cursor name is the tell — the command
iterates with `.iterator(chunk_size=...)`.

That is exactly the pattern measured in the LightGBM trainer on 2026-11-05: a chunked iterator makes
Django open a named server-side cursor, Postgres will not use a parallel plan through one, and the
identical 5.7M-row query took **86.1 s bulk** versus **still unfinished at 432 s** through the cursor.
A parallel plan shows up in `pg_stat_activity` as several backends sharing one `query_start`; a cursor
shows up as a single backend doing `FETCH FORWARD` forever.

Remedy is the one already applied in `apps/prediction/tasks_lightgbm.py`: narrow the selection to the
columns actually needed and release the row list per call, rather than streaming the wide result. If a
row count is only being *compared*, aggregate it in SQL instead of transferring rows at all. This also
matters because the command is the release gate — a gate that takes an hour gets skipped, and the
`--fail-on-critical` exit code above shows what happens when nobody has run it recently.

### `list_date` is NULL on 1,591 of 1,912 assets, which makes `validate_data_quality` uninterpretable

Measured 2026-11-06 while closing out the OHLCV adjustment-basis repair. Of the 1,886 assets that
actually carry price rows, **1,590 have no `list_date`**.

The consequence is that `manage.py validate_data_quality --fail-on-critical` cannot bound each
asset's expected date range, so it demands rows from `HISTORICAL_DATA_FLOOR` (2010-01-01) for
assets that did not exist yet, and reports the difference as critical. A bare run returned
**exit 1 with 5,302,805 critical issues**, dominated by `missing_factor_score` 4,176,723 and
`missing_ohlcv` 1,126,060 — plus its own `missing_list_date` warning on 1,590 assets, which is the
cause sitting inside the same report. These are coverage gaps against an over-broad expectation, not
wrong values: the repaired series passes the local adjustment invariant at 0 violations over all
6,542,582 rows.

`--effective-universe-only` restricts to asset/dates present in the PIT effective universe and is the
correct way to invoke this gate; it was not used for the run above.

**Executed 2026-11-08, and the prediction held.** `backfill_asset_list_dates` processed 1,912
assets and updated **1,383**, moving `null_list_date` from **1,385 to 2** (of which exactly 1 also
has price rows). It costs three `stock_basic` calls plus an AkShare fallback, and writes only the
fields whose value differs, so it is additive and idempotent — the earlier note about "spending
quota" was wrong about this command.

Re-running the gate on a one-year window with `--fail-if-critical` (`--effective-universe-only`)
now **exits 1 with 1,530 criticals, and the split is the point**:

| issue | count | before the backfill |
| --- | --- | --- |
| `missing_factor_score` | 1,497 | — |
| `missing_ohlcv` | **12** | ~1,126,060 |
| `stale_macro_snapshot` | 11 | — |
| `usable_asset_count_cliff` | 9 | — |
| `etf_index_valuation_index_coverage` | 1 | — |

`missing_ohlcv` collapsed by five orders of magnitude, and the gate is now interpretable: 1,497 of
1,530 criticals are one named, explainable class rather than 5.3M artefacts of an assumed range.
`missing_ohlcv_unknown_list_date` — the warning added alongside this — does not even appear, because
only one asset is left in that state.

**Then `missing_factor_score` was attributed, and it was the same class of mistake.** The 1,497
rows were 499 assets × exactly the last three sessions, because `FactorScore` stopped at
2026-11-03 while OHLCV ran on: the factor pipeline simply had not reached those sessions yet. The
gate compared against the calendar and had no notion of how far the pipeline had got — the same
error as demanding OHLCV from 2010 for an asset listed in 2020.

So `missing_factor_score` is now a **warning named `missing_factor_score_pipeline_lag`** for any
date after the newest date *any* asset carries a composite score for, with the watermark named in
the detail row. A hole *before* the watermark stays critical, because that one is real. One
aggregate computes the watermark per run, not per asset.

That took the gate from **1,530 criticals to 33**, and every one of the 33 is a concrete claim:

| issue | count |
| --- | --- |
| `missing_ohlcv` | 12 |
| `stale_macro_snapshot` | 11 |
| `usable_asset_count_cliff` | 9 |
| `etf_index_valuation_index_coverage` | 1 |

Still open, deliberately:
- **Those 33 are the real backlog of the data layer**, and each needs its own look. None is an
  artefact of an assumption any more.
- **The gate is still too slow to run at full history** (>90 minutes; see the
  `perf/validate-data-quality-scans` notes — it is cold-block-I/O bound on per-asset reads). Until
  that is addressed, a one-year window is the only practically runnable form.

**The 33 attributed, and 12 of them traced to a missing suspension series — resolved 2026-11-08.**

| issue | count | what it is |
| --- | --- | --- |
| `missing_ohlcv` | 12 | individual assets missing 1-5 consecutive sessions between 2026-07 and 2026-10 |
| `stale_macro_snapshot` | 11 | macro series older than the 45-day bound |
| `usable_asset_count_cliff` | 9 | coverage drops past the cliff ratio on 9 dates |
| `etf_index_valuation_index_coverage` | 1 | already documented: 6 of 299 mapped indices have rows |

**The first read of the 12 was wrong, and the correction is the finding.** They looked like holes in
the price series. They are not: `pro.daily` returns **no rows at all** for those assets on those
dates, because the stocks were halted. And `pro.suspend_d` *does* return the halts — 600363
`2026-09-29` type `S`, 002155 `2026-08-20/21/24` type `S`, 300567 `2026-07-09/10/13` type `S`,
000582 `2026-10-08/09` type `S`. What was missing was **our** `AssetSuspension` rows: the table spans
2001-2026 with 143,860 rows, so it is not a window limit, but those specific dates were absent.

Backfilling that window (`backfill_asset_suspensions --start-date 2026-07-01 --end-date 2026-10-31`)
wrote **196 rows, 171 of them full-day**, and the gate's criticals went **33 -> 21** with every
`missing_ohlcv` gone. So the price series was never wrong; the excuse data was.

Why the window was missing: the suspension series is **watermark-driven**, not row-presence driven —
`detectors.py` class B — because a day with no suspensions legitimately has no rows. Catch-up replays
it only when the *beat task itself* missed an occurrence, not when the provider window a past run
covered was incomplete. A range the pipeline never walked is therefore never revisited, and nothing
in the system reports that. That is the durable lesson: **class-B series need a periodic range
backfill, not only a watermark replay.**

**The remaining 21, attributed. None of them is a code defect, and none was downgraded.**

| issue | count | what it actually is |
| --- | --- | --- |
| `stale_macro_snapshot` | 11 | one outage. Every session 2026-07-17..07-31 saw its newest `MacroSnapshot` at `2026-06-01`, past the 45-day bound. Macro is current now (newest `2026-11-01`), so this is a historical hole, not a live one. |
| `usable_asset_count_cliff` | 9 | single-day, single-family holes that zero the usable mask: 2026-05-28 `sentiment_score` 0 of 500, 2026-06-05 `factor_score` 0 of 500, 2026-09-24 `fundamental_snapshot` 57 of 500. |
| `etf_index_valuation_index_coverage` | 1 | already documented: 6 of 299 mapped indices have rows. |

Deliberately **not** downgraded, unlike the `list_date` and pipeline-watermark cases above. Those two
were the gate being unable to justify its own expectation (unknown listing date; a session the pipeline
had not reached yet). Here the expectation is right and the data was genuinely absent on those dates.
Downgrading would have hidden a real gap.

That leaves an open policy question rather than a task: **should the release gate fail on a historical
gap that has since closed?** Today it does, so 21 criticals stand between the current tree and a clean
all-history run. The alternatives are (a) keep failing and backfill the three holes, (b) scope the gate
to a recent window and treat all-history as a separate report, or (c) let the gate fail only on gaps
newer than the last verified-clean date. Not decided here.

### The DB connection between this WSL2 host and the NAS-hosted Postgres can wedge permanently

Observed twice on 2026-11-05. Signature, from three independent sources that all have to agree
before "hung" is a safe conclusion: the server backend sits in `wait_event_type=Client,
wait_event=ClientWrite`; the client is in `poll_schedule_timeout` at **zero CPU**; and `ss -tnp`
shows the socket `ESTAB` with **both** `Recv-Q` and `Send-Q` at 0. Nothing times out, so it never
recovers on its own.

The trigger was a `COUNT(*)` over the 174M-row `analytics_technicalindicator` table running
concurrently with a large result-set fetch. A controlled test ruled out a structural fault: the same
link moved 500,000 rows in 1.5 s (**331k rows/s**) when nothing else was running. So the rule is
concurrency, not capacity.

Two consequences that are still open:
- A killed client does **not** free its backend. Orphans were seen sitting in `ClientWrite` **1,068 s**
  after the client died, because `tcp_keepalives_idle=600` does not fire on a backend actively blocked
  writing. Against `max_connections=100` this is a slow leak, and `pg_terminate_backend(pid)` plus a
  `pg_stat_activity` emptiness check before each launch is the only current remedy.
- Long training and audit jobs need a client-side read timeout, or at minimum a watchdog that
  compares CPU ticks against `pg_stat_activity` and fails loudly instead of waiting for a human to
  notice a flat log.

### The reference RULE parameter set is window-specific

Phase 4 ran a CLI-only walk-forward over 2016–2023 (four expanding folds,
selection by Sharpe with lower turnover as the tie-break, training window only).
The row-256 configuration — BIWEEKLY / p3 / mom120 / absolute momentum off /
equal — was selected in **0 of 4** folds. Every fold picked MONTHLY + mom120,
with positions 3/5/8, the absolute filter on twice and off twice, and weighting
equal three times / score once. Out-of-sample returns were +0.98% / +75.26% /
−2.32% / −27.75% and Sharpe 0.1090 / 1.4493 / 0.0746 / −1.4334. The table is in
`docs/plans/etf-rotation-selection-sizing-2026-10-23.md` §7.12. This is a
statement about stability, not a better configuration: folds 1 and 4 sit exactly
on the 20-rebalance floor, only four folds exist, and each fold restarts from
initial capital. No default was changed. The 2024–2026 window has since been
**released into the training pool** (2026-10-25, `etf-rotation-next-phases-2026-10-24.md`
§8): it is training data now, so ranking winners on it is allowed but produces a tuning
number, never out-of-sample evidence. The sealed forward window is the only honest one.

**The date contract made the window dependence concrete, and it is worse than "unstable".**
Re-running the reference RULE configuration with one engine and one config, moving only
`--start` while holding `--end 2023-12-31` fixed: from `2016-01-29` it returns **+7.04%**
(Sharpe **+0.147**, alpha **+1.13%**); from `2021-01-13` it returns **-37.93%** (Sharpe
**-0.654**, alpha **-12.49%**). Over the full contract window `2021-01-13 .. 2026-10-24`
it is **-58.07%**, Sharpe **-0.432**. So the sign of the headline depends entirely on
whether the thin-universe 2016–2020 years are inside the window -- every prior number in
this item, the CPCV block distribution included, was measured with those years included.
Turnover also rises 15.8x -> 19.3x per year while win rate falls 39.5% -> 28.4%, so it is
not a pure fee artifact. Full table in `TECHNICAL_GUIDE.md` §8. This does not invalidate
the contract: a configuration whose edge lives in a 24-fund universe was never measuring
repeatable alpha. It does mean the RULE defaults now have **no** evidence behind them on
the post-contract window, and re-selecting them there is the actual open work.

**Phase 8 added the measurement tool; it has now been run.**
`walk_forward.combinatorial_purged_folds` plus
`run_etf_rotation_backtest --cpcv-blocks N` produce a per-block Sharpe
*distribution* over 2016–2023 (purged and embargoed, window refuses 2024+). The
row-256 distribution is in `docs/plans/etf-rotation-next-phases-2026-10-24.md`
§7.9: six blocks, median Sharpe 0.1620 (min −0.0741, max 1.2789), 83.33% of
blocks positive. It corroborates the walk-forward finding — the full-window
0.5357 is not "valid throughout"; blocks 4 (2020–2021) carries almost all of it
and block 5 is negative. Read it as a stability reading, never as a ranking over
configurations.

### Phase 9's `max_positions=3` is an alignment, not a proven winner

The live default moved 5 -> 3 to match the production reference row 256 (`p3`)
and the §7.7 holdout book. The §7.12 walk-forward selected p3/p3/p5/p8 across
four folds, so there is no evidence that 3 is better than 5. **The p3-vs-p5
CPCV comparison has now been run** (`docs/plans/etf-rotation-next-phases-2026-10-24.md`
§7.10): p5 has the higher median Sharpe (0.2725 vs 0.1620) and the shallower
median drawdown (14.54% vs 17.96%) but a worse positive-block share (66.67% vs
83.33%), so the pre-registered "all three legs" rule kept the default at 3.
Do not cite the default as evidence for p3, and do not flip it to 5 on the
median Sharpe alone.

### The ML rematch's CV arm needs per-fold retraining, not another artifact

Phase 10 added the shadow book (`signal_type=SHADOW`, `ETF_ML_SHADOW_MODEL`, a
03:50 CST beat task — `19:50 UTC` before the 2026-11 timezone unification, same
instant) and the API/ledger isolation that keeps it out of production.
It is inert until an ML `ModelVersion` is active, which the gate forbids until a
candidate wins.

The CV arm's entry point now exists: `load_predictor_for_version` in both
trainers loads a READY artifact while ignoring `is_active`, and
`--cpcv-blocks --model LIGHTGBM --model-version <v>` scores with it. **LSTM was
adjudicated and failed the CV leg; LightGBM is not adjudicable.** The pinned
LSTM artifact is actually fold 5 (trained through 2022-10-21, per its
`metrics.json`), so blocks 1–5 are in-sample and block 6 is genuinely out of
sample. Even with that advantage it lost the Sharpe-median and
positive-block-share legs (0.1238 vs 0.1620; 66.67% vs 83.33%); only median
drawdown was lower (15.93% vs 17.96%), which does not satisfy the three-leg rule.
The pinned LightGBM artifact was trained through 2023-11-30, covering every
block, so its Sharpe 1.5–3.3 is in-sample fit.

**A look-ahead was found and fixed on the way.** `rotation_engine._call_predictor`
used to drop the `date` column, so every LSTM backtest scored each historical
session as-of the newest eligible snapshot. `date` now travels with the frame
(LightGBM ignores it), and the fix has a test. **Phase D's stored LSTM numbers
(row 209) were produced through that path and are no longer valid evidence**.
Re-measuring them now runs in the released window (training data since
2026-10-25, plan §8), so a fresh reading is allowed but is a tuning number, not
a restoration of the old claim.

Gap (a) is **resolved** in 0.1.28: `walk_forward.train_dates_for_any_fold` plus
`train_lightgbm.train_fold_predictors` /
`train_lstm.train_walk_forward(folds=..., capture_fold_predictors=True)` and
`run_etf_rotation_backtest --cpcv-retrain` now score each block with a model
trained on its complement. The rematch is in the report §11: LightGBM median
Sharpe −0.0136 and LSTM 0.0249 against RULE's 0.2227, so neither replaces RULE.
Gap (b) stands: the shadow's forward evidence still needs ≥20 rebalances, and the
shadow is not open — `ETF_ML_SHADOW_MODEL` can now pin a version
(`LSTM:<version>`) without activating it, which is what makes opening it
possible at all.

### The band and the capacity cap have tuning-window evidence only

Phase 3 added `drift_band_fraction`, `liquidity_mode` and
`adv_participation_cap`, all off by default. A 2% band suppresses 49 drift
fills on the reference configuration and moves the headline by less than a
rounding-sized margin; a 1% band never binds because its threshold sits below
the 0.5% minimum-trade floor on a three-leg equal-weight book. Both readings
come from the 2016–2023 tuning window and the same one-shot logic as the Kelly
floor: there is no holdout evidence for either band. `scale` mode has no
real-window return table at all — only the unit-level redistribution proof.
Defaults therefore stay `0.0` / `reject` / `0.0`; do not flip them from this
evidence.

**Phase 12 explicitly declined to add the scale-mode table.** Distinguishing "scale
is better" from "this window happened to reward it" would require evidence the
closed holdout cannot supply, and the band evidence is already error-sized.
The decision is recorded in `docs/plans/etf-rotation-next-phases-2026-10-24.md`
§2 (Phase 12); defaults remain unchanged.

### `position_fees_paid` changed meaning; pre-0.1.23 rows keep the old one

Phase 2 made a closed leg's `position_fees_paid` include the slippage its fills
embedded, not just commission. The reference row's 233 closed trades sum to
168,901.10 on a rerun (39,243.23 commission + 129,657.86 closed-leg slippage),
while the persisted row still sums to 39,243.23. Nothing is corrupted — `pnl`
already carried slippage inside the execution price and is bit-identical — but a
query that averages `position_fees_paid` across rows must either filter by
`created_at` or use the `total_fees` / `total_slippage_cost` metric columns. No
backfill is planned: the old field never stored the commission/slippage split, so
inventing one would be worse than the documented break.

The other Phase 2 report blocks (`turnover`, `sub_windows`, `rebalances`,
`model_versions`) are computed at finalization and are absent from rows completed
earlier. Readers must treat them as optional; the §7.10 evidence comes from an
ephemeral rerun of the reference configuration, not from the stored row.

### The Kelly governor is a kill switch at its own default floor

`kelly_fraction=0` (off) is bit-identical to the pre-change engine, which is the
regression guarantee the plan asked for. Enabling it is a different story: on the
reference book the realised edge is negative (51 closed trades, win rate 43.1%,
payoff 1.29 → `f* = -0.009`), so `raw_scale` is negative and the default
`kelly_floor=0` clamps to *zero exposure* on 164 of 213 sessions. The result is
Sharpe 0.967 / maxDD 6.84% / turnover 2.13x, which is flattering only because
beta is 0.03. One open item remains: (a) a floor sweep (0.5 → Sharpe 0.692,
0.75 → 0.588 with 8.9pp less drawdown than off) has tuning-window evidence and
no holdout evidence. Item (b) is resolved in 0.1.27: `kelly_fraction`, `kelly_cap`,
`kelly_floor`, `kelly_lookback_trades` and `cash_yield_annual` are real columns
(migration `etf_rotation/0009`), so `--queue` carries a sized book. Their defaults
`0 / 1.0 / 0 / 60 / 0` reproduce the pre-promotion engine bit for bit; the
fingerprint changes, so old PAUSED rows restart from the beginning.

### Rows 354 and 356-361 are a dedup-off measurement set

`--no-dedup-track-index` was silently applied to *every* command-line run while
`bool(None)` stood in for "not asked for" (fixed in 0.1.21). Rows written during
that window have `dedup_track_index=False` in their own columns, so they are
self-describing but misleadingly named -- `verify-invvol-*` (354) reads like the
Phase-3a comparison row and is not it. The corrected numbers are already in
`docs/plans/etf-rotation-selection-sizing-2026-10-23.md` §7.3/§7.4 as ephemeral
runs. Nothing here is a data-integrity problem: the fingerprint/digest was always
built from the columns that were actually written.

**Phase 12 added the filter, not a rename.** `/etf/backtests/` now accepts
`?dedup_track_index=true|false`, so a reader can exclude the measurement set
without a name-based rule that would also hide row 355, the *intended* dedup-off
control. The rows themselves are unchanged; rerunning them under the fixed path
remains optional housekeeping.

### Test suite green — 339/339

Resolved. The reduction ran 109 (Redis) → 17 → 10 → 7 → 6 → 3 → **0**. Every step was
verified against the full suite. No production behaviour changed except the one genuine
validator bug below, which was itself surfaced by a failing test that was right.

**Cluster 2 — data-quality continuity (3 tests, `core`). Resolved.** The three shared an
area but not a cause, which is why the earlier "probably the calendar again" note was
wrong and had to be replaced rather than acted on:

- `TechnicalIndicatorValidationRegressionTests.…flags_out_of_range_rsi` exposed a **real
  production bug** — see the next entry. Fixed in the validator; the test was not
  modified.
- `DataQualityValidationCommandTests.test_validate_data_quality_writes_actionable_reports`
  compared `metadata['technical_indicators']` against `REQUIRED_TECHNICAL_INDICATORS`, a
  hand-copied 13-entry fixture constant, while the command defaults to
  `DEFAULT_TECHNICAL_INDICATORS` — 19 entries since `RETURN_3D/5D/10D`,
  `RELATIVE_VOLUME_5D/20D` and `REALIZED_VOLATILITY_5D` were added. Now asserts against
  the imported production constant, so it cannot drift again. This is the
  `RETURN_3D/5D/10D` discrepancy flagged in the original documentation validation.
- `…test_technical_indicator_continuity_warnings_appear_in_summary_and_metadata` listed
  its asset on the window's first date, so RSI(14) had roughly 15 bars of warmup to find
  in a 4-day calendar and every date was legitimately excused. The validator was right;
  the fixture was asking for a finding the rules correctly refuse to produce. Relisted at
  `d1 - 1 day`, which is the branch where the guard *does* expect continuity, because
  warmup cannot be assessed from a calendar that starts after the listing.

Two of the three were fixtures asserting against hand-copied or unstated preconditions
rather than against production constants. That is the same failure mode the generated
reference sheets were built to eliminate, appearing in tests instead of docs.

### Validator skipped value-range checks on dates excused from continuity

**Production bug, fixed in `1d018d3`.** `_write_technical_indicator_continuity_gaps`
gated the bounded-value check behind the continuity expectation:

```python
if not variant_expected_dates:
    continue          # skipped the out-of-range loop further down as well
```

A date is legitimately excused from continuity when warmup is insufficient, the asset is
suspended, or it is newly listed — but an excused date can still carry a stored row, and
a stored `RSI` of 120 or an `RS_SCORE` of 4.0 is a defect wherever it sits. The old
ordering silently suppressed range validation on exactly the partial-window dates where
malformed rows are most likely: warmup edges, post-suspension resumes, backfill
boundaries.

The range check now runs before the continuity guard and does not depend on it.
`variant_rows` is already restricted to OHLCV-backed baseline dates by the caller, so
this validates what exists without asserting anything about what ought to exist.

**Expect new findings on the next production run.** `validate_data_quality` will now
report `value_out_of_range` rows it previously suppressed. They were always present and
invisible. Budget triage time for that run rather than reading the new warnings as a
regression.

Two related notes. The `issue_type` is spelled `technical_indicator_value_out_of_range`
in the counters and `value_out_of_range` in the CSV detail row — confusing but
intentional, and worth unifying if the report schema is ever versioned. And the
`critical_issues=4` seen while instrumenting that fixture were its genuinely absent
related rows (FactorScore, MacroSnapshot, MarketContext, IndexMembership), not a
symptom.

**Instrumentation found this; reading did not.** Dumping every row the validator emitted
for the fixture, and re-running without `--only-report`, showed the target CSV had 0 rows
while 15 were written elsewhere — and `factor_score_gaps` had already confirmed OHLCV
existed for the date. That eliminated the empty-baseline hypothesis in a single run and
pointed straight at the `continue`. Several hundred lines of static reading had narrowed
it to three candidates but not to the cause. The temporary debug block was reverted with
`git checkout` before committing.

Baseline evidence: the original 109 failures were confirmed **pre-existing** by
running the identical selection against a pristine worktree at the pre-change
commit -- same test counts and same failing test names for both `apps.backtest` +
`apps.core` (94 tests) and the remaining nine modules (245 tests). No regression was
introduced by the documentation restructure. Every reduction up to 3 (109 → 17 → 10 →
7 → 6 → 3) came from an environment credential fix or a test-fixture fix, with no
change to production code. The final step (3 → 0) is the exception: it included one
genuine production fix, the validator bug above, found because a failing test turned out
to be correct and the code wrong. That is the outcome the whole exercise was for — a
suite that actually runs is what makes such a bug visible at all.

### Production code inspects whether it is being mocked

`apps/backtest/tasks.py` around line 757 calls `inspect.signature` on
`_predict_lightgbm_for_asset.side_effect` to decide whether to pass `run=`. Production
code branching on the shape of a test double is a compatibility shim for older stub
signatures, and it hides drift instead of surfacing it.

The LSTM path has no such shim, which is why
`test_dashboard_stocks_overlays_runtime_lstm_candidate_payload` failed loudly with
`TypeError: … unexpected keyword argument 'model_version'` -- the better failure mode,
though it surfaces from inside the view and reads like a view bug.

Removing the LightGBM shim means updating every LightGBM stub in
`apps/backtest/tests.py` to accept `run=`. Worth doing; not urgent.

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

### `makemigrations --check` — resolved

Both drifts are gone; the check exits 0 and is enforced in CI. See the Resolved entry
for what the generated migrations actually contained. Two corrections to the earlier
diagnosis, recorded so the reasoning is not reused blindly:

- `backtest.0003` was correctly predicted to be harmless, and `sqlmigrate` confirms it
  emits literally `-- (no-op)`.
- `markets.0012` was described as touching "real indexes", which overstated it. Django
  generated `RenameIndex`, and PostgreSQL executes that as `ALTER INDEX ... RENAME TO
  ...` — a catalog-only rename with no rebuild, no table rewrite and no meaningful
  lock. The distinction mattered: it is what made applying this to the dev database
  routine rather than something to schedule.

The `CHANGELOG.md` note that the check was clean at v0.1.9 still stands, and the
likely cause of the index half is a Django upgrade rehashing auto-generated
`models.Index` names.

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
will not self-heal. `apps/core/task_health.py` can identify them; see
`docs/how-to/runbook-sync-failure.md` §6. Needs a sweep and a decision on whether
to restart or fail them.

### Artifact storage: DONE (portability + git-bloat, cut over 2026-10-10)

Portability is largely resolved. New artifacts store `BASE_DIR`-relative paths
(`_resolve_artifact_path` / `_resolve_lstm_artifact_path`), and the relative path
doubles as the object key for the artifact store
(`apps/prediction/artifact_store.py`, `docs/how-to/artifact-store.md`). Legacy rows
from other hosts (`/app/...`, a Linux home dir, `C:\...`) still do not resolve; they
are inactive, and `migrate_artifacts_to_store --normalize-registry` rewrites any that
map under the current `BASE_DIR`.

The git-bloat half is **done** (2026-10-10). MinIO is deployed on the NAS
(`finance_artifacts_minio`, bucket `armillary-artifacts`, versioning on); all
170 on-disk artifacts were migrated (`migrate_artifacts_to_store --execute`),
`ARTIFACT_STORE_BACKEND=s3` is live, `verify_artifact_store` passes for every ACTIVE
artifact, and `models/` is untracked + gitignored (forward-only — existing history keeps
its ~58 MB of blobs, but `.git` stops growing one family per retrain). Fresh clones
repopulate via `sync_artifacts_from_store`. Procedure + ops:
`docs/how-to/artifact-store.md`; NAS runbook: HomeServer `docs/runbooks/minio.md`.

### `TECHNICAL_GUIDE.md` disagrees with the staleness code

The guide states that `relative_volume_5d` and `relative_volume_20d` inherit the
5-day and 20-day moving-average gap rules (2 and 5 trading days).
`apps/analytics/technical_staleness.py` sets both to **0** in
`BASE_MAX_GAP_TRADING_DAYS`. The code is authoritative; the guide needs
correcting.

### Empty tables behind live features

Measured 0 rows: `analytics_alertrule`, `analytics_alertevent`,
`analytics_screenertemplate`, `macro_eventimpactstat`.

**Partly resolved since.** `check_alert_rules` now seeds one active `FEED_STALE` rule
(`'ETF data feed health'`) on first run via `_ensure_feed_stale_rule`, so
`analytics_alertrule` no longer stays at zero and the alert stream has something to fire
on. `analytics_alertevent` fills only when a rule triggers, which is correct.

**`ScreenerTemplate.is_public` is gone.** It gated nothing: there is no `owner` field
since the auth module was removed, `ScreenerTemplateViewSet.get_queryset` returns every
row, and the flag existed only in the serializer, the admin list and an index. A checkbox
in the admin that changes no behaviour is worse than no checkbox. Dropped in migration
`analytics/0010`, along with its index and the serializer/admin references.

**Still open, and it is a product decision rather than a cleanup:**
`macro_eventimpactstat` has no production writer. It is not quite dead code — it has an
API endpoint (`apps/macro/views.py`), a serializer, an admin registration, a purge
command reference, and tests that create rows directly — so removing it is removing an
API surface, not deleting an unused model. Two honest options: implement the writer that
populates it from news/policy events, or remove the model, endpoint, serializer, admin and
those tests together. Leaving it as an endpoint that can only ever return nothing is the
option least worth keeping.

No seed/fixture mechanism exists anywhere in the repo (`find apps -type d -name fixtures`
is empty, no seed command), so "seed reference data" would mean building one first.
Documenting the remaining surfaces as user-provisioned is the cheaper path, and the Alert
Center already has an empty-state string (`alerts.noLive`).

---

## Open — frontend

### Frontend static checks — resolved; the ratchet is gone

All three `react-hooks/exhaustive-deps` warnings are fixed and `npm run lint` reports zero.
With nothing left to tolerate, `frontend/scripts/check-static-baseline.mjs` and
`frontend/static-baseline.json` are deleted and CI calls `npm run build` and `npm run lint`
directly again.

The ratchet's real value was that a FOURTH warning could not arrive silently: eslint exits
0 with warnings present unless `--max-warnings` is passed, the same blind-spot class as
`manage.py test` once discovering zero tests and exiting 0. That guarantee is preserved more
cheaply — the `lint` script itself is now `eslint . --max-warnings 0`, so a new warning
fails locally and in CI with no baseline file to maintain.

How each was fixed, because two were not the shape they looked like:

| Where | Diagnostic | Fix |
| --- | --- | --- |
| `StockDetailPage` | missing dependency `t` | **Not** by adding `t`. The effect stores its failure message in state, so translating inside it would make `t` a dependency and a locale switch would re-run the whole load-and-poll cycle — which broke the test asserting "one load, one poll". The error is now stored as a `{key, status}` descriptor and translated at render, so the message follows the language with no refetch and the effect keeps `[symbol, reloadToken]` |
| `BacktestWorkbenchPage` | missing `loadRuns` | A `loadRunsRef` carries the latest closure into the mount-only interval effect. Listing `loadRuns` directly would re-create the interval on every render and reset its phase; `runsRef` in the same file was already the pattern for state |
| `BacktestWorkbenchPage` | conditional `extraComparisonOptions` | Wrapped in `useMemo` keyed on `[selectedRun, runs, selectedRunCompareTargetId]`, so the effect below keys on the candidate set rather than on a new array identity every render |

The six TypeScript errors this entry used to record were fixed by `5883eaf` (PR #17), and one
of them is worth keeping as a near-miss: `BacktestWorkbenchPage.tsx` appeared to require
`entry_weekdays` in the create payload, looked exactly like a dropped user selection, and was
in fact the type being wrong — `BacktestWorkbenchPage.test.tsx` asserts
`expect(payload?.parameters).not.toHaveProperty('entry_weekdays')`, so omitting it is
deliberate and tested. A required parameter silently missing from a payload is what a real
bug looks like, and the test asserting its absence was the only thing distinguishing the two.
Check for that before "fixing" a type error.

### CI exists, and asserts a discovery floor

`.github/workflows/ci.yml` runs on push to `main` and on pull requests, with two jobs:

- **`backend`** (blocking) — PostgreSQL 15 and Redis 7 service containers, then
  `manage.py check`, `makemigrations --check --dry-run`,
  `export_documentation_facts --check`, and the full suite.
- **`frontend`** (blocking, named "Frontend tests, type-check, and lint") — `npm ci`,
  `npm run build`, `npm run lint` (which now fails on any warning via
  `--max-warnings 0`), and the vitest suite.

There used to be a third, `frontend-static`, holding the two static checks under
`continue-on-error: true` while they failed. `5883eaf` fixed the errors and folded the
steps into `frontend`; both job names above are required status checks, so neither can be
renamed without re-applying branch protection.

Both test jobs assert a **minimum test count** (`MIN_TEST_COUNT`) as well as a green
result. This is the load-bearing part. A workflow that only checked the exit code would
have reported success through the entire period when `manage.py test` was discovering
zero tests, which is how 109 failures stayed hidden. The floor converts that class of
silent success into a hard failure.

Raise the floors when tests are added. Lowering one should require a stated reason, since
the count can only fall if discovery breaks or tests are deleted. The last lowering was
v0.1.17's removal of the authentication module: backend 421 → 361 and frontend 48 → 42,
with the reason recorded inline in the workflow; the frontend floor has since been raised
again by the alert-socket tests. The current numbers live only in
`.github/workflows/ci.yml`, deliberately: this paragraph said 339 and 31 long after both
floors had been raised to the measured 421 and 48, which is the argument for keeping the
workflow as the single source of truth instead of restating counts here.

Two notes on the service configuration. The Redis service runs unauthenticated: the
development server requires an ACL user, but that is a property of that server's
configuration, not of the code, so CI does not need to reproduce it. And Python is pinned
to 3.14 to match the verified local interpreter — TA-Lib 0.6.8 ships manylinux wheels
bundling the C library, so no apt build step is needed, but `torch` is unpinned and large
enough to dominate the job runtime.

## Open — configuration and infrastructure

### `BACKTEST_STALE_TASK_MAX_AGE_SECONDS` is not configurable

`apps/core/task_health.py` reads it via `getattr(settings, ..., 2400)`, but
the setting is never defined and never read from the environment. Changing the
stale-run threshold currently requires a code edit. Wire it through `env.int()`.

### OpenAPI schema metadata — version resolved

`pyproject.toml` now owns the project version; `config/settings/base.py` reads it at import
and `SPECTACULAR_SETTINGS['VERSION']` uses it, so the schema reports the version the
artifacts were released under instead of a number cross-referenced by nothing. This entry
used to record three schemes that disagreed — schema `0.2.0`, the CHANGELOG's newest
heading `0.1.53`, and `frontend/package.json` `0.0.0` — with no connection between them.
v0.1.17's lowering from `1.2.0` to `0.2.0` (removing the authentication module is a
breaking schema change) had narrowed the gap without closing it.

`frontend/package.json` is aligned to the same number, and `apps/core/tests_version.py`
fails if either it or the newest CHANGELOG heading drifts from `pyproject.toml`. That test
is the connection the three numbers never had.

The schema description reads "(CSI 500)", matching the migrated universe — that half was
already fixed.

### Unpinned ML dependencies

`lightgbm`, `torch`, `scikit-learn`, `mlflow`, `numpy`, `akshare`, `tushare`,
`TA-Lib`, and `django-celery-beat` are all unpinned in `requirements/base.txt`,
while the guide documents exact hyperparameters and accuracies. A rebuild on a
different day can produce a different model. Pin at least the ML stack, and
record the resolved set used for each artifact family.

Also: `torch` installs as a **CPU-only** build from the default index on Windows,
which conflicts with the `windows_gpu` LightGBM inference backend and the
WSL2-as-NVIDIA-owner framing. Document which runtimes actually have GPU.

### Transitive dependencies are now locked — resolved

`requirements/*.in` carry the direct intent; the matching `.txt` are `pip-compile` lockfiles
with the full transitive closure and a hash per wheel, and CI installs
`pip install --require-hashes -r requirements/local.txt`. A package released overnight can
no longer turn an unrelated PR red, which is what kombu 5.7.0 did on 2026-11-03: celery only
constrains it to `<6.0`, a fresh resolve floated it onto a version whose redis extra rejects
`redis==5.0.4`, and every branch went red without a code change.

`scripts/check_requirements_lockfile.py` runs first in the job and fails if an `.in` and its
`.txt` disagree, if a package line has no `--hash`, or if a `.txt` still carries a `-r`
include (a compiled file is flattened). It is a text comparison on purpose: a CI
`pip-compile --dry-run` would re-resolve torch, mlflow and akshare with hashes -- about
twenty minutes per file here -- to catch the one failure that actually happens, a forgotten
recompile.

Two things found while generating them, neither introduced by this change:

- **`local.txt` and `production.txt` disagree on the server stack.** mlflow pulls
  `gunicorn==25.3.0` and `uvicorn==0.54.0` transmissively, so the CI lockfile pins those,
  while `production.txt` pins the declared `gunicorn==23.0.0` / `uvicorn[standard]==0.32.0`.
  Each file is internally consistent; the two describe different environments. Worth
  reconciling before a real deployment, because CI tests a gunicorn that production would
  not run.
- **The lockfiles are resolved on Python 3.12 and CI runs 3.14** (`setup-python`), because
  the verified local interpreter is 3.12. Marker evaluation can differ between the two. A
  hashed install on a different interpreter may legitimately reject a marker-excluded
  package; if that happens, compile with `--python-version` matching CI.

Read the whole traceback, not the first one: `IndexError` at the top of a "During handling of
the above exception" chain is rarely the cause.

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

- **`CONTRIBUTING.md` §6 misdescribes where model artifacts live, and the error is
  dangerous.** It says "`models/` is **tracked in git** — 132 files including `.pkl`,
  `.pt`" and instructs "Commit a new artifact family together with the registry change that
  activates it." In fact `git ls-files models/` returns **0** and `.gitignore:102` has ignored
  the tree since 2026-10-10, because artifacts live in the object store
  (`ARTIFACT_STORE_BACKEND = s3`). Following the published instruction would commit multi-MB
  binaries. The correct step is `migrate_artifacts_to_store --execute` followed by
  `verify_artifact_store`. Found on 2026-11-05 while publishing the `adj-fix` families.
- **`CONTRIBUTING.md` §1 says there is no branch or PR workflow, which the history
  contradicts.** It states "Work happens on `main`. There is no protected-branch workflow, no
  PR template, and no CI." `.github/PULL_REQUEST_TEMPLATE.md` exists, and PRs #56-#78 each
  merged a `<type>/<slug>` branch. The branching convention is therefore inferable only from
  git history, which is exactly what §1 claims is unnecessary.
- **`export_documentation_facts --check` reports drift in an order that invites a false
  green.** The `CommandError` is written before the per-sheet `Building …` lines, so checking a
  log with `tail` can show a clean run for a sheet that failed. Verified the hard way:
  `--check` flagged `commands.md`, a regeneration invoked with a mistyped flag
  (`--sheets`, which the parser rejects) exited non-zero, and a `tail -4` of the follow-up
  `--check` showed no error at all. Use `--only <sheet>` and read the whole output.

- Correct the `relative_volume_*` gap values in `TECHNICAL_GUIDE.md` (see above).
- Add docstrings to `apps/backtest/` (19 non-test files, ~4,100 lines, zero
  docstrings — including the 2,061-line engine in `tasks.py` that encodes the fee
  model, TP/SL exit ordering, chunked resume, and candidate generation) and to
  `apps/sentiment/` (13 files, ~1 docstring). Module-level docstrings were added
  in this pass; the deep function-level coverage of `backtest/tasks.py` is still
  outstanding.
- Duplicate `import json` at
  `apps/backtest/management/commands/run_core_backtest_matrix.py` lines 28-29.
- `CHANGELOG.md` 0.1.13 (557 lines, `:2486-3042`) is an audit checklist rather than release
  notes. **Re-scoped, not converted:** the section already opens with its own note
  (`:2488-2491`) saying it is an audit workflow record and "will be converted to standard
  release-note format before the 0.1.13 tag is created". That tag never came — the project
  is at 0.1.53 — so the condition the note sets has passed rather than been missed.
  Rewriting it now would mean writing release notes for a release many versions ago,
  claiming what shipped without the artefacts to check it against; a labelled historical
  document is worth more than a reconstruction. Leave it, and do not treat it as a template
  for new entries.
- `CHANGELOG.md` 0.1.12 states env loading moved to `.envs/.local`; that was
- ~~Duplicate `import json` at
  `apps/backtest/management/commands/run_core_backtest_matrix.py` lines 28-29.~~
  Stale: `grep -n "import json"` returns exactly one line (32). Nothing to remove.
- `CHANGELOG.md` 0.1.13 is an audit scratch pad, not release notes — mixed
  Chinese/English checklist with unchecked items and no
  `Objective`/`Implemented Features`/`Key Files` structure. Convert before
  tagging.
- ~~`CHANGELOG.md` 0.1.12 states env loading moved to `.envs/.local`; that was
  reversed by the single-`.env` consolidation. Unlike the duplicate-`0.1.7`
  entry, which carries an explicit historical note, this one has no forward
  pointer.~~ Stale: it has one. `CHANGELOG.md:3081-3083` carries "**Note (0.1.13)**:
  This was later reversed by the single-`.env` consolidation", pointing at
  `docs/reference/env.md`.
- Decide `LICENSE`. `README.md` declares the project private and proprietary and
  a proprietary notice file now exists, but the choice should be conscious
  rather than incidental.

---

## Deferred ideas

Carried over from the old `README.md` scratch sections. Unprioritised and
unvalidated.

### Data semantics

- **OHLCV adjustment semantics — measured, no longer a tidy-up.** `close` is
  already qfq and `adj_close` holds the same value; the unadjusted original close
  and the `adj_factor` are not retained, so a stored row cannot be re-based or
  audited locally. Because TuShare re-bases qfq to the newest factor at query time
  and the writer only ever appends (`ignore_conflicts=True` plus
  `start_dt = latest.date + 1`), each ex-dividend event leaves the asset's history
  on two bases. A 2026-11-03 census (479 trading dates, 1,767 measurable assets)
  found **38% of the universe** carrying a seam — and **54% of the 500 assets in
  the synced universe**, which is the population the models actually read. The
  metric is the total spread of bases inside one asset's series, not a single day's
  step: median 1.93%, p90 5.33%, max 71.4%, and 657 of the 679 drifted assets have
  more than half their rows off-basis. Damage to return series is therefore not
  "one bad day" but a majority of rows on a basis the newest rows no longer use.
  See `docs/reference/price-adjustment.md` and `manage.py audit_adjustment_drift`;
  remediation is the approved `raw_close` + `adj_factor` schema change plus a
  per-asset re-fetch.
- **`OHLCV.amount` and `ETFDaily.amount` differ by 1000x.** The stock table stores
  TuShare's raw 千元 unchanged (`markets/tasks.py`, `_safe_decimal(row['amount'])`),
  the ETF table converts to 元 via `TUSHARE_AMOUNT_QIAN_YUAN_TO_YUAN`. Deliberately
  left alone for now: converting would break every reader of `amount` for no
  cleaning benefit. Record the unit in `OHLCV.amount`'s `help_text` when the schema
  is next touched, and do not join the two tables on `amount`.
- **No limit-up / limit-down rules.** Nothing judges 10% / 20% / ST limits against
  the previous close, distinguishes main board / ChiNext / STAR / BSE regimes, or
  flags return-based price anomalies. Limit-up days are not marked.
- **PIT union benchmark: two write-path defects, both pre-existing.**
  (a) `refresh_point_in_time_union_benchmark(start, end, initial_nav)` treats
  `start_date` as a seed row and writes it with `daily_return = 0`. Its only
  caller for the daily path,
  `refresh_latest_point_in_time_union_benchmark`, passes the *previous* row's date
  as `start` -- so every daily refresh zeroes the previous session's PIT return,
  and that date is never revisited. Observed directly: refreshing 2010-01-07 rewrote
  2010-01-06's `daily_return` from -0.00083720 to 0 with `nav` unchanged.
  (b) The first trading day of 2011-2016 all carry a flat NAV (`daily_return = 0`)
  while the index itself moved (2016-01-04: -8.33%). Data coverage on those dates is
  normal (461-492 computable members, same as neighbouring sessions), and it
  reproduces with a single whole-range build, so it is build logic, not a chunking
  artefact and not missing data. Six dates out of 4,084, and a flat day shifts the
  NAV *level* rather than corrupting later returns, so the impact is bounded -- but
  `PIT vs 000905.SH daily-return correlation` sits at 0.9529 and should be
  re-measured after a fix.
- **`TechnicalIndicator.timestamp` is stored as midnight in the active `TIME_ZONE`,
  so reading it back with `datetime.date()` is wrong.** `.date()` on the UTC value
  truncates in UTC, which for a Beijing-midnight row returns the *previous* day --
  every session, not occasionally. `apps/core/date_floor.session_midnight`'s
  docstring documents the SQL-side hazard (non-sargable `timestamp__date`) but not
  this one. Convert with `django.utils.timezone.localtime()` first.
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

- Four `localStorage`/`sessionStorage` keys outlive the code that read them:
  `finance_jwt`, `finance_jwt_refresh`, `finance_api_key` and
  `finance_auth_persistence` are still in the browser of anyone who used the
  pre-v0.1.17 UI, and nothing reads or clears them now. No `removeItem` sweep was
  added: the only browser that matters is the operator's own, and a migration for
  one user's storage is code with no consumer. Clear them by hand if it bothers
  you, or delete the whole key list if a second deployment ever appears.
- Eight i18n keys are orphaned by the deleted `ScreenerPage.tsx` — `screener.title`,
  `screener.desc`, `screener.loadError`, `screener.sortBy`, `screener.suggested`,
  `screener.rr`, `screener.tradeScore` and `nav.screener`, in both dictionaries.
  They are inert (`t()` returns the key string, and no call site asks for them), so
  this is tidiness, not a bug. Note that `dict` is typed
  `Record<Locale, Record<string, string>>`: an orphan passes `tsc`, and so does an
  asymmetry between the two locales — only a *missing* key is user-visible. The
  current 222 literals resolve against both dictionaries with zero asymmetry.
- `ScreenerTemplate.is_public` gates nothing. With `owner` gone there is no
  private-vs-shared split to make: `ScreenerTemplateViewSet.get_queryset` returns
  every row, and the flag survives only in the serializer and the admin list.
  Either drop the column or give it a meaning (e.g. hide templates the operator
  marks private from the list endpoint).
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
- Catch-up treats an in-progress Beijing trading day as a missing day.
  `apps/catchup/service.py:106` sets `as_of` from `session_today()`, and every
  detector then compares that against `trade_date` without ever asking whether the
  market has closed: `detectors.py:183-184` (a zero cross-sectional count *is* a
  gap), `detectors.py:224-231` (`benchmark_index` rows absent up to `as_of`),
  `detectors.py:249` (`missing_etf_daily_dates` up to `as_of`), and the repairs that
  act on them at `repairs.py:89-145`. So during every trading session the hourly
  `:25` tick really does call TuShare to repair a date that cannot have data yet —
  burning budget and writing `FAILED` / `PROVIDER_GAP` rows for a hole that the
  00:10 CST sync fills by itself. The fix is a "the session has closed" guard on the
  **repair** path, not a different `as_of`. Note explicitly that using
  `session_today()` rather than `timezone.localdate()` here was a **deliberate
  choice made during the timezone unification, not a fix for this**: it is what
  bounds the exposure to ~7h/day (08:00–15:00 Beijing, the hours in which `as_of`
  names a day the market has not finished) instead of widening it to ~15h/day
  (00:00–15:00). Switching it to the calendar date would make the bug worse while
  looking more "correct".
- `manage.py catch_up --as-of` never parses its argument. The help text promises
  "Override today as YYYY-MM-DD", but `apps/catchup/management/commands/catch_up.py:45`
  passes `options['as_of']` through as a **string**; `service.py:106`
  (`as_of = as_of or session_today()`) keeps it, because a non-empty string is
  truthy, and `detectors.py:109` then evaluates `as_of - timedelta(...)`. Any
  non-empty `--as-of` therefore raises `TypeError` before a single detector runs.
  The path is uncovered rather than working: every test calls `run_catch_up` with a
  real `date` (`AS_OF = date(2026, 10, 30)`) and none of them goes through the
  command. Fix by parsing in `handle()` and raising `CommandError` on a bad value.
- A readability pass over the existing session-date call sites, deliberately not
  taken now. The 39 sites in `apps/*/tasks*.py` still spell the session date as
  `timezone.now().date()`, which by the definition in `apps/core/timez.py` is
  *exactly* `session_today()` —
  the module says so, and it is why the unification could flip `TIME_ZONE` without
  touching any of them. Rewriting to the named helper is behaviour-identical and
  pure naming, so it was left undone to keep the diff reviewable and the regression
  surface at zero. The view and management-command sites need the same decision one
  at a time, because for those `timezone.localdate()` is sometimes the better name —
  that is a per-call-site judgement, not a sweep.
- Orphaned UTC `CrontabSchedule` rows. After the flip, django-celery-beat's
  `DatabaseScheduler` created a new row per beat entry keyed on the new
  `timezone='Asia/Shanghai'`; the pre-flip rows with `timezone='UTC'` are still in
  the database and referenced by no `PeriodicTask`. Harmless — inert rows, and
  deleting them only invites the next scheduler sync to recreate whatever it wants.
  Worth a one-off query only if that table is ever audited for drift.
- `export_documentation_facts` output depends on which sheets are built in the same
  process. `--check` walks `SHEET_NAMES` in order, and `build_commands()` imports every
  management module — which registers additional Celery tasks — so `celery.md`'s task
  tables and its "N registered project tasks" count differ from a partial run like
  `--only celery`. Regenerate with `--only commands,celery,env` or a full run, never
  `--only celery`, or CI's `--check` fails on a file that looked freshly written.
  Making each sheet independent of build order (or asserting the task count from the
  registry rather than the import side effects) is the real fix.
- Redis boolean flags written with `timeout=None` never expire —
  `apps/catchup/repairs.py:231` caches `catchup:macro_yields_entitlement_denied`
  permanently on the first `yc_cb` permission error. Correct today (a token tier does
  not gain entitlement mid-run, and every re-probe costs a 31s sleep per call), but
  the flag then outlives the condition: upgrading the token requires deleting the key
  by hand, and nothing in the code says so. Unrelated to timezones, and listed here
  only because it is the same shape of problem — a convention that changed, with a
  stored value still written under the old one.
- The hourly news backfill hands its provider a window label eight hours behind the
  zone that label names. `apps/sentiment/tasks.py:367-368` builds `start_at`/`end_at`
  by calling `.strftime('%Y-%m-%d %H:%M:%S')` on the window bounds read back from the
  database. Postgres returns those **UTC-aware**, so `strftime` renders the UTC reading
  of an instant TuShare then parses as Beijing wall time — the window sent is eight
  hours earlier than the one the task computed. This was expected to fix itself when
  `TIME_ZONE` flipped; it did not, because the offset lives in the driver's return
  value, not in the setting. Fix is `timezone.localtime(window_start).strftime(...)` at
  both sites. Deliberately left alone in the unification: changing what a live provider
  receives is a semantic change, not a mechanical one, and it wants its own rehearsal
  against real TuShare responses. Until then
  `apps/sentiment/tests.py::test_hourly_historical_backfill_fetches_next_window` pins
  the current UTC-reading strings, so the first person to fix this sees a failing
  assertion that explains the trade rather than a silent behaviour change.
- That same rendering costs the backfill one permanent eight-hour hole at the moment
  sentiment `0003` lands. Every stored `published_at` moves −8h, the walk's next
  `end_at` is still rendered as the UTC reading, and so it jumps eight hours behind
  where the walk had already got to: news published 01:30–09:30 Beijing on the
  boundary day is skipped once and never revisited, because the walk only goes
  backwards. Measured on the rehearsal copy across all 175,730 rows and pinned by
  `apps/sentiment/tests_tz_migration.py::test_the_backfill_walk_boundary_moves_eight_hours_at_the_correction`.
  Day-aligning `window_end` — the obvious-looking containment fix — widens the hole to
  eighteen hours, since `window_end = earliest - 1s` is already below `earliest` in the
  corrected frame and pulling it back to the previous Beijing midnight discards the
  rest of the boundary day. Rendering in the zone the label names closes this and the
  steady-state skew at once; both wait on the same TuShare rehearsal.
  **Deployed 2026-11-02 19:26 CST**, and the hole is still open rather than closed: the
  planned manual fill of Beijing `2025-06-09 02:06:00 → 10:05:59` returned
  `抱歉，您没有接口(major_news)访问权限`, so the window has to be fetched the moment the
  entitlement comes back — see the next entry.
- The historical news backfill has been **dead since 2026-06-13** and nothing surfaces it.
  `sentiment_newsarticle` rows carrying `metadata.provider = 'tushare_major'` (166,821 of them)
  stop being created at `2026-06-13 20:12:40Z`; ingest by month is Apr 56,014 / May 95,217 /
  Jun 15,590 / nothing since. The walk reached `2025-06-09` against a floor of `2021-04-15`, so
  it is four and a half years short. Two separate things make this invisible. First, the
  provider now answers `抱歉，您没有接口(major_news)访问权限` — a *permission* loss, not a rate
  limit — and `_is_provider_quota_error` (`apps/sentiment/tasks.py:58-66`) only matches
  `最多访问该接口` and `频率超限`, so the branch at `:379` never fires and `:384` re-raises: the
  task fails loudly instead of returning the "deferred" string that the provider-blackout
  runbook is written around. Second, `run-hourly-historical-news-backfill` is `enabled=true` with
  `total_run_count=1797` and `last_run_at` 2026-11-02 02:12Z, so beat keeps ticking it and every
  hour's failure looks like the previous hour's failure. Unrelated to timezones, listed here
  because it is the same shape — a convention that changed (the token's entitlement) with the
  error-handling code still written against the old one. Fix: recognise permission loss as its
  own deferred class, with an alert after N consecutive failures rather than after 1,700.
- `TechnicalIndicator.timestamp` changes meaning at the flip, so a recomputed date becomes a
  second row rather than an update. Every indicator task builds its key the same way —
  `make_aware(combine(trade_date, time.min))`, e.g. `apps/analytics/tasks.py:104` for RSI and
  `:155` for MACD — which under `TIME_ZONE='UTC'` stored a trade date at `00:00+00:00` and now
  stores it at `00:00+08:00`, i.e. eight hours *earlier* as an instant. Measured on
  `analytics_technicalindicator` before the deploy: for asset 660 since 2026-09-01, 476 rows sit
  at time-of-day `00:00:00`, 68 at one bulk `00:12:43.145224`, and **zero** at `16:00:00`. So
  `get_or_create(asset, timestamp, indicator_type, parameters)` on a date that old code already
  wrote inserts a parallel row instead of updating it, `unique_together` cannot catch it (the
  timestamps differ), and the dashboard's `order_by('-timestamp').distinct('asset_id',
  'indicator_type')` (`apps/analytics/views.py:663`) then prefers the *older-convention* row
  because its instant is later. No displayed number is wrong — both rows are recomputed from the
  same OHLCV — but the cost is a slowly doubling key space on a 166,338,926-row table and an
  ambiguous winner. Left alone here for the same reason as the label skew: it is a semantic fix
  in the deferred sweep, and the right shape is a zone-stable key (`session_midnight()` from
  `apps/core/date_floor.py:18-29`, or a real `DateField`) rather than `make_aware` on a wall
  midnight. Check after the first new-code indicator run: for the seam trade date, count
  `(asset_id, indicator_type, parameters)` groups holding two rows exactly eight hours apart.
- `sentiment.0003`'s `RunPython` is `elidable=True`, so a future `squashmigrations` over the
  sentiment apps drops the −8h correction silently. A freshly built database does not need it —
  that is why it is elidable — but the flag also means the correction survives only as long as
  nobody squashes. Note it next to the migration file, and keep the deploy log
  (`shifted=175730`, 2026-11-02 19:26 CST) as the record that it ran.
