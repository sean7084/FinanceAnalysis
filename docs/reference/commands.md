<!--
  GENERATED FILE - DO NOT EDIT BY HAND.
  Regenerate: python manage.py export_documentation_facts
  Source of truth: django.core.management.get_commands() and each command's argument parser.
-->

# Management Command Reference

_Generated: 2026-10-26T15:19:25Z_

Workflow ordering and the reasoning behind each stage live in `docs/how-to/backfill.md` and `docs/how-to/retrain.md`. This sheet is the option surface only.

Every command also accepts the standard Django options (`--version`, `-v/--verbosity`, `--settings`, `--pythonpath`, `--traceback`, `--no-color`, `--force-color`, `--skip-checks`); they are omitted from the per-command tables below.

`<dynamic-date>` marks a default computed from the current day rather than a fixed constant. The exact offset is not published because commands differ in whether they derive it from the local date (`date.today()`) or from UTC (`timezone.now().date()`), and those disagree for part of every day outside UTC. Read the `Help` column for the intended semantics, or the command source for the precise expression.

**49** project commands across **9** apps.


## `apps.analytics`


### `backfill_signal_events`

Backfill historical non-RS SignalEvent rows from OHLCV history.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  |  |
| `--end-date` | value | `<dynamic-date>` |  |  |
| `--symbols` | value | `""` |  |  |
| `--limit-assets` | value | `0` |  |  |
| `--signal-types` | value | `GOLDEN_CROSS,DEATH_CROSS,MA_BULL_ALIGN,MA_BEAR_ALIGN,BB_SQUEEZE,BB_BREAKOUT_UP,BB_BREAKOUT_DOWN,BB_RSI_OVERBOUGHT,BB_RSI_OVERSOLD,VOLUME_SPIKE,VOLUME_PRICE_DIVERGENCE,MOMENTUM_UP_5D,MOMENTUM_DOWN_5D,OVERSOLD_COMBINATION` |  |  |
| `--chunk-size-days` | value | `365` |  | Maximum inclusive date span per delete/insert transaction. Use 0 to process the full range in one chunk. |
| `--checkpoint-file` | value | `""` |  |  |
| `--resume-from-checkpoint` | flag | `False` |  |  |


### `backfill_technical_indicators`

Backfill historical TechnicalIndicator rows from OHLCV history for non-RS indicators.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  |  |
| `--end-date` | value | `<dynamic-date>` |  |  |
| `--symbols` | value | `""` |  |  |
| `--limit-assets` | value | `0` |  |  |
| `--technical-indicators` | value | `ADX,BBANDS,EMA,FIB_RET,MACD,REALIZED_VOLATILITY_5D,RELATIVE_VOLUME_20D,RELATIVE_VOLUME_5D,MOM_10D,MOM_20D,MOM_5D,OBV,RETURN_10D,RETURN_3D,RETURN_5D,RSI,SMA,STOCH` |  |  |
| `--chunk-size-days` | value | `365` |  | Maximum inclusive date span per delete/insert transaction. Use 0 to process the full range in one chunk. |
| `--checkpoint-file` | value | `""` |  |  |
| `--resume-from-checkpoint` | flag | `False` |  |  |


## `apps.backtest`


### `export_backtest_runs`

Export BacktestRun configuration and results to CSV files. Defaults to light export; use --detail-export for full CSV output.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-id` | value | `89` |  |  |
| `--end-id` | value | `112` |  |  |
| `--output-dir` | value | `reports/backtests_89_112_v0_1_9` |  |  |
| `--compare-start-id` | value | `101` |  |  |
| `--compare-end-id` | value | `106` |  |  |
| `--compare-offset` | value | `6` |  |  |
| `--light-export` | flag | `False` |  | Export only run_summary.csv, run_config_results.csv, and model_references.csv (default). |
| `--detail-export` | flag | `False` |  | Also export trades.csv, macro_context_monthly.csv, and comparison CSVs. |
| `--include-active-lightgbm-artifacts` | flag | `False` |  | Also export lightgbm_model_artifacts.csv for the current active LightGBM artifacts. |


### `rerun_backtests_for_comparison`

Clone existing backtests into new comparison runs, setting compare_backtest_run_id to the original run id and optionally executing the reruns inline.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--run-ids` | value | &mdash; | yes | Comma-separated ids and/or inclusive ranges, e.g. 525-532 or 525,526,530-532. |
| `--name-suffix` | value | `comparison-rerun` |  | Suffix appended to the cloned BacktestRun names. |
| `--queue` | flag | `False` |  | Queue reruns asynchronously instead of executing them inline. |


### `run_core_backtest_matrix`

Create and optionally queue the heuristic/lightgbm core-profile backtest matrix across selected variants.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | &mdash; | yes | Backtest start date (YYYY-MM-DD). |
| `--end-date` | value | &mdash; | yes | Backtest end date (YYYY-MM-DD). |
| `--variants` | value | `top-n,trade-score-limit` |  | Comma-separated matrix variants: top-n,trade-score-limit. |
| `--sources` | value | `heuristic,lightgbm` |  | Comma-separated prediction sources: heuristic,lightgbm. |
| `--name-prefix` | value | `core18` |  | Prefix used for BacktestRun names. |
| `--queue` | flag | `False` |  | Queue created runs asynchronously instead of executing the first chunk inline. |
| `--execute-inline` | flag | `False` |  | Execute all matrix runs to completion in this process, round-robin by queued chunk while preserving the in-memory matrix signal cache. |
| `--chunk-trading-days` | value | `60` |  | Per-run chunk size stamped into matrix BacktestRun parameters. Defaults to 60. |
| `--lightgbm-inference-backend` | value | `auto` |  | LightGBM-only inference backend stamped into created runs. Supported: auto,cpu_serial,cpu_batched,windows_gpu. |
| `--lightgbm-batch-size` | value | `256` |  | LightGBM-only inference batch size stamped into created runs. Defaults to 256. |
| `--dry-run` | flag | `False` |  | Print the planned matrix without creating BacktestRun rows. |
| `--output-dir` | value | `""` |  | Optional compact export directory. Defaults to reports/<name-prefix>-<timestamp>. |
| `--include-active-lightgbm-artifacts` | flag | `False` |  | Also export active LightGBM artifact metadata in the compact bundle. |


### `run_reference_benchmark_suite`

Run a rolling benchmark backtest suite and export the resulting report bundle under reports/.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | &mdash; | yes | Validation start date (YYYY-MM-DD). |
| `--end-date` | value | &mdash; | yes | Validation end date (YYYY-MM-DD). |
| `--window-days` | value | `180` |  | Days in each rolling validation window. |
| `--step-days` | value | `30` |  | Step size between window starts. |
| `--sources` | value | `heuristic,lightgbm,lstm` |  | Comma-separated sources: heuristic,lightgbm,lstm. |
| `--top-n` | value | `3` |  | Number of picks per entry cohort. |
| `--horizon-days` | value | `7` |  | Prediction horizon in days (3, 7, or 30). |
| `--holding-period-days` | value | `7` |  | Holding period in calendar days. |
| `--capital-fraction-per-entry` | value | `0.5` |  | Capital fraction used for each entry cohort. |
| `--min-up-probability` | value | `0.0` |  | Minimum up probability threshold. |
| `--name-prefix` | value | `validation` |  | Prefix used for BacktestRun names. |
| `--queue` | flag | `False` |  | Queue runs asynchronously instead of running inline. |
| `--output-dir` | value | `""` |  | Optional report output directory. Defaults to reports/<suite-name>. |
| `--suite-name` | value | `""` |  | Optional suite name used for the output directory and manifest. |
| `--include-active-lightgbm-artifacts` | flag | `False` |  | Also export active LightGBM artifact metadata. |


### `run_validation_backtests`

Run systematic backtest validations over rolling windows for heuristic, LightGBM, and LSTM sources.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | &mdash; | yes | Validation start date (YYYY-MM-DD). |
| `--end-date` | value | &mdash; | yes | Validation end date (YYYY-MM-DD). |
| `--window-days` | value | `180` |  | Days in each rolling validation window. |
| `--step-days` | value | `30` |  | Step size between window starts. |
| `--sources` | value | `heuristic,lightgbm,lstm` |  | Comma-separated sources: heuristic,lightgbm,lstm. |
| `--top-n` | value | `3` |  | Number of picks per entry cohort. |
| `--horizon-days` | value | `7` |  | Prediction horizon in days (3, 7, or 30). |
| `--holding-period-days` | value | `7` |  | Holding period in calendar days. |
| `--capital-fraction-per-entry` | value | `0.5` |  | Capital fraction used for each entry cohort. |
| `--min-up-probability` | value | `0.0` |  | Minimum up probability threshold. |
| `--name-prefix` | value | `validation` |  | Prefix used for BacktestRun names. |
| `--queue` | flag | `False` |  | Queue runs asynchronously instead of running inline. |


## `apps.core`


### `export_documentation_facts`

Regenerate the docs/reference/ fact sheets (metrics, models, commands, celery, env) from live repository and database state.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--output-dir` | value | `docs/reference` |  | Directory to write the generated sheets into. Defaults to docs/reference. |
| `--only` | value | `""` |  | Comma-separated subset of: metrics,models,commands,celery,env. |
| `--check` | flag | `False` |  | Exit non-zero if any committed sheet differs from freshly generated output. |


### `purge_pre_floor_historical_data`

Dry-run or delete historical database rows dated before the configured floor date.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--before-date` | value | `2010-01-01` |  |  |
| `--execute` | flag | `False` |  | Actually delete rows. Without this flag, the command only reports candidate counts. |


### `validate_data_quality`

Validate historical data quality and write actionable reports under reports/ without mutating model data.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  |  |
| `--end-date` | value | `<dynamic-date>` |  |  |
| `--symbols` | value | `""` |  | Comma-separated symbols or TuShare ts_codes. Default validates active assets. |
| `--include-delisted` | flag | `False` |  |  |
| `--effective-universe-only` | flag | `False` |  | Restrict validation to assets/dates present in the PIT effective universe during the requested window. |
| `--output-dir` | value | `""` |  |  |
| `--technical-indicators` | value | `ADX,BBANDS,EMA,FIB_RET,MACD,REALIZED_VOLATILITY_5D,RELATIVE_VOLUME_20D,RELATIVE_VOLUME_5D,MOM_10D,MOM_20D,MOM_5D,OBV,RETURN_10D,RETURN_3D,RETURN_5D,RSI,RS_SCORE,SMA,STOCH` |  |  |
| `--cross-section-audit-dates` | value | `2024-09-20,2024-09-23,2025-01-02,2025-12-31` |  | Comma-separated trading dates to audit RS_SCORE/factor/composite cross-sectional participants against effective_universe(date). |
| `--macro-max-age-days` | value | `45` |  |  |
| `--max-detail-rows` | value | `0` |  | 0 means write all affected asset/date rows. |
| `--only-report` | value | `""` |  | Comma-separated report filenames to write, for example ohlcv_continuity_gaps.csv. |
| `--fundamental-reconciliation-sample-size` | value | `0` |  | 0 disables second-layer upstream reconciliation. Positive values sample stored FundamentalFactorSnapshot rows and recompute them from TuShare daily_basic/fina_indicator. |
| `--fundamental-reconciliation-seed` | value | `17` |  | Deterministic seed for sampled fundamental reconciliation rows when the sample size is positive. |
| `--technical-indicator-reconciliation-sample-size` | value | `0` |  | 0 disables sampled TechnicalIndicator replay. Positive values sample stored TechnicalIndicator rows and recompute them from OHLCV using the backfill formulas. |
| `--technical-indicator-reconciliation-seed` | value | `29` |  | Deterministic seed for sampled TechnicalIndicator replay rows when the sample size is positive. |
| `--alert` | flag | `False` |  | Email a summary when critical data-quality issues are found. |
| `--alert-recipients` | value | `""` |  | Comma-separated alert recipients. Falls back to settings. |
| `--fail-on-critical` | flag | `False` |  |  |


## `apps.etf_rotation`


### `build_etf_index_mapping`

Resolve ETF -> tracked index codes from fund_basic + index_basic.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--dry-run` | flag | `False` |  | Resolve and report; write nothing. |
| `--report` | value | `docs/reports/etf-index-mapping-coverage.md` |  | Coverage report path (default docs/reports/...). |
| `--pause` | value | `0.3` |  | Seconds between TuShare calls (default 0.3). |


### `compute_etf_features`

Compute point-in-time ETF features and labels into ETFFeatureSnapshot.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start` | value | &mdash; |  | YYYY-MM-DD, inclusive. |
| `--end` | value | `""` |  | YYYY-MM-DD, inclusive. Defaults to today. |
| `--years` | value | `0` |  | Trailing N calendar years ending today; replaces --start/--end. |
| `--horizon` | value | `0` |  | Label horizon in trading sessions. |
| `--min-amount` | value | `0.0` |  | Pool entry: 20-session average turnover in yuan (ETFDaily.amount is yuan, not thousands). |
| `--min-age-days` | value | `0` |  | Pool entry: minimum days since listing. |
| `--batch-size` | value | `0` |  | Rows per upsert batch. |
| `--dry-run` | flag | `False` |  | Compute and report; write nothing. |
| `--recompute-labels-only` | flag | `False` |  | Update the label columns of existing rows only; never insert, never touch a feature. |


### `generate_etf_rotation_signal`

Rank the point-in-time ETF pool and write the rotation signal for one date.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--date` | value | &mdash; |  | Trade date (YYYY-MM-DD). Default: newest session with a non-empty eligible cross-section. |
| `--signal-type` | value | `MONTHLY` |  | Rebalance cadence the row is stored under (default MONTHLY). |
| `--arm` | value | `""` |  | Book discriminator. Empty is the production book; two arms may publish on the same date and cadence because the uniqueness key includes it. |
| `--model-type` | value | `RULE` |  | Scorer: the rule blend, a trained LIGHTGBM/LSTM, or their ensemble. An ML type with no active ModelVersion fails instead of quietly falling back to RULE. |
| `--max-positions` | value | &mdash; |  | Slots in the book. Default: settings.ETF_MAX_POSITIONS (3). |
| `--momentum-lookback` | value | &mdash; |  | Momentum horizon in sessions, e.g. 60 -> mom_60d. Default: 60; the nearest stored horizon is substituted and recorded in metadata when it is absent. |
| `--model-version` | value | &mdash; |  | Pin one READY ML artifact instead of resolving the active one. Needed to run a shadow arm without activating the model. |
| `--dry-run` | flag | `False` |  | Score, rank, verify and report; write nothing. |


### `mark_etf_rotation_positions_daily`

Mark OPEN ETF rotation positions to the newest close and close legs displaced by a newer signal of the same type.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--date` | value | &mdash; |  | Mark date (YYYY-MM-DD). Default: the newest ETFDaily session. |


### `measure_etf_rotation_sensitivity`

Run a pre-registered sensitivity grid over panel starts and arms.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--preregistration` | value | &mdash; | yes | Markdown file listing every context that may run, as "<arm>@<panel-start>". Its hash is recorded with the results. |
| `--panel-starts` | value | &mdash; | yes | Comma-separated panel starts, e.g. 2016-01-29,2017-01-03. |
| `--arms` | value | `rule,lgb-veto,lstm-veto` |  | Comma-separated arms from ['lgb-select', 'lgb-select-rolling', 'lgb-veto', 'lstm-select', 'lstm-veto', 'rule', 'rule-rolling']. |
| `--baseline-arm` | value | `rule` |  | Arm every other arm is compared against (default rule). |
| `--end` | value | `2023-12-31` |  | Panel end (ISO). |
| `--blocks` | value | `6` |  | Blocks per split (default 6). |
| `--rolling-train-days` | value | `750` |  | Training window for the rolling arms (default 750 sessions). |
| `--out-dir` | value | &mdash; |  | Where to write the markdown table. Default: docs/reports/sensitivity. |


### `report_etf_rotation_evidence`

Render the ETF rotation measurement ledger, flagging repeated contexts.

_No options._


### `report_etf_rotation_live_track`

Report the live ETF rotation book and its drift against a declared reference backtest.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--reference-backtest-id` | value | &mdash; |  | ETFRotationBacktest id the live book is judged against. Default: settings.ETF_LIVE_REFERENCE_BACKTEST_ID (0 = descriptive report only). |
| `--win-rate-tolerance` | value | &mdash; |  | Absolute win-rate deviation that counts as drift. Default: settings.ETF_LIVE_DRIFT_WIN_RATE_TOLERANCE (0.20). |
| `--mean-return-tolerance` | value | &mdash; |  | Absolute mean-trade-return deviation that counts as drift. Default: settings.ETF_LIVE_DRIFT_MEAN_RETURN_TOLERANCE (0.03). |
| `--min-closed-trades` | value | &mdash; |  | Closed trades required before a drift verdict is made. Default: settings.ETF_LIVE_DRIFT_MIN_CLOSED_TRADES (10). |
| `--alert` | flag | `False` |  | Write a LIVE_DRIFT alert event when a book breaches its tolerance. |


### `run_etf_rotation_backtest`

Run a single ETF rotation backtest, or the 144-combination RULE grid search.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start` | value | &mdash; |  | YYYY-MM-DD, inclusive. |
| `--end` | value | &mdash; |  | YYYY-MM-DD, inclusive. Required with --start. |
| `--model` | value | &mdash; |  | Ranker to use. Default: RULE. |
| `--frequency` | value | &mdash; |  | Rebalance frequency. Default: BacktestConfig (MONTHLY). |
| `--max-positions` | value | &mdash; |  | Holding count. Default: BacktestConfig (5). |
| `--momentum-lookback` | value | &mdash; |  | Ranking horizon in sessions. Default: BacktestConfig (60). |
| `--no-absolute-momentum-filter` | flag | &mdash; |  | Drop the risk-on/risk-off absolute momentum gate (default: on). |
| `--weighting` | value | &mdash; |  | Position sizing across the selected legs. Default: settings.ETF_WEIGHTING. |
| `--no-dedup-track-index` | flag | &mdash; |  | Let several funds tracking the same index into one book. Default: on (one fund per tracked index). |
| `--max-per-category` | value | &mdash; |  | Cap on names one category may contribute; 0 disables the cap. Default: settings.ETF_MAX_PER_CATEGORY (2). |
| `--kelly-fraction` | value | &mdash; |  | Fraction of the Kelly-optimal exposure to use as a portfolio-level cap on the risk budget. 0 (default) disables the governor entirely. |
| `--kelly-cap` | value | &mdash; |  | Upper bound on the Kelly exposure scale (default 1.0, i.e. never lever). |
| `--kelly-floor` | value | &mdash; |  | Lower bound on the Kelly exposure scale (default 0.0). |
| `--kelly-lookback-trades` | value | &mdash; |  | Closed trades the win/payoff estimate is taken over (default 60). |
| `--cash-yield-annual` | value | &mdash; |  | Annualised rate credited to cash the liquidity floor left idle (default 0.0, which reproduces every pre-existing run). |
| `--defensive-asset` | value | &mdash; |  | TS code of the risk-off sleeve. Default: settings.ETF_DEFENSIVE_ASSET. |
| `--initial-capital` | value | &mdash; |  | Starting equity in yuan. Default: settings.ETF_INITIAL_CAPITAL. A small value is how the min-commission floor gets exercised. |
| `--commission-rate` | value | &mdash; |  | Two-way broker commission rate. Default: settings.ETF_COMMISSION_RATE (0.0003). |
| `--min-commission` | value | &mdash; |  | Yuan floor on one fill. Default: settings.ETF_MIN_COMMISSION (5). |
| `--slippage` | value | &mdash; |  | Fraction applied to the execution price. Default: settings.ETF_SLIPPAGE (0.001). |
| `--liquidity-floor-multiple` | value | &mdash; |  | Required trailing daily turnover as a multiple of the leg notional; 0 disables the screen. Default: settings.ETF_LIQUIDITY_FLOOR_MULTIPLE (10). |
| `--min-trade-notional-fraction` | value | &mdash; |  | Skip rebalance deltas below this share of equity (default 0.005). |
| `--drift-band-fraction` | value | &mdash; |  | Leave a delta below this share of its target notional to drift (default 0 = off). |
| `--liquidity-mode` | value | &mdash; |  | Reject an oversized leg (default) or scale it to the ADV cap. |
| `--adv-participation-cap` | value | &mdash; |  | Scale-mode ceiling as a multiple of trailing daily turnover (default 0 = off). |
| `--name` | value | `""` |  | Name of the persisted row. |
| `--persist` | flag | `True` |  | Persist an ETFRotationBacktest row (the default). |
| `--no-persist` | flag | `True` |  | Run without writing anything; metrics go to stdout only. |
| `--backtest-id` | value | &mdash; |  | Re-run an existing ETFRotationBacktest, using its columns as the config. |
| `--queue` | flag | `False` |  | Enqueue tasks.run_etf_rotation_backtest on etf-rotation instead of running inline. |
| `--sample-fills` | value | `5` |  | Filled trades to reprint with recomputed fees for the hand check (default 5). |
| `--grid` | flag | `False` |  | Sweep the 144 RULE combinations (C25). Overrides --model. |
| `--grid-top` | value | `10` |  | Rows of the ranked grid table (default 10). |
| `--grid-limit` | value | `0` |  | Run only the first N combinations (0 = all). Cheap smoke of a long sweep. |
| `--grid-plan` | flag | `False` |  | Print the combinations the grid would run and execute nothing. |
| `--grid-prefix` | value | `grid` |  | Name stem for the persisted grid rows (default: grid). The row name is the resume key, so a re-run after a selection-rule change needs a fresh stem to keep both generations side by side. |
| `--grid-force` | flag | `False` |  | Re-run grid rows that already COMPLETED under the same name (default: skip them). |
| `--walk-forward-steps` | value | &mdash; |  | With --grid: split the window into N expanding folds, select the RULE configuration on each training window by Sharpe (lower turnover breaks a tie), and trade it on that fold's validation window. Ephemeral: no row is written. A fold with fewer than 20 rebalances is too thin; rerun with N=4, which gives longer validation blocks. |
| `--cpcv-blocks` | value | &mdash; |  | Combinatorial purged cross-validation: cut the window into N contiguous blocks and measure the named configuration on every held-out block. Ephemeral. A window reaching 2024-01-01 is allowed but labelled tuning-only: that holdout was released into the training pool. |
| `--cpcv-test-blocks` | value | `2` |  | Blocks held out per split (default 2); blocks the other blocks train on after a label-window purge and an embargo. |
| `--cpcv-embargo` | value | `5` |  | Sessions excluded after each held-out block (default 5). |
| `--macro-gate-pmi` | value | &mdash; |  | Scale risk-on exposure by --macro-gate-scale while the latest published manufacturing PMI is below LEVEL (0 disables). Macro is a portfolio gate here, never a ranking feature. |
| `--macro-gate-scale` | value | &mdash; |  | Exposure multiplier while the gate is open (default 0.5). |
| `--seeds` | value | `1` |  | Fit N boosters per fold with different seeds and average their scores (default 1). Variance reduction, not a bigger model. |
| `--label` | value | `rank_pct` |  | Label a re-trained ML arm fits (default rank_pct). dd_rank is the safety rank: lowest forward drawdown ranks highest, so a veto still drops the bottom share of the score. |
| `--horizons` | value | `""` |  | With --cpcv-retrain and an ML --model: also report rank IC against price-based forward returns at these sessions-ahead horizons (e.g. 20,40,60). The model is unchanged -- this reads the same scores against a different label, so no extra training happens. |
| `--fold-plan` | value | &mdash; |  | Split shape: combinatorial blocks (the default for --cpcv-blocks), a fixed-length rolling window, or an expanding window. Rolling and expanding share the same validation calendar, so the only difference between them is how much history the model sees. With --walk-forward-steps the default is expanding and cpcv is refused: that path has no combinatorial split. |
| `--rolling-train-days` | value | `750` |  | Sessions in a rolling training window (default 750, about three years). Only used with --fold-plan rolling. |
| `--preregistration` | value | &mdash; |  | Pre-registration file (markdown) declaring the arms and criteria of this measurement. Its sha256 is stored on the evidence-ledger row, which is what lets a verdict be tied back to criteria fixed before the run. Requires --cpcv-retrain: that is the only path that writes a ledger row. |
| `--cpcv-purge` | value | &mdash; |  | Sessions excluded before each held-out block. Defaults to 1 for RULE and to ETF_LABEL_HORIZON_DAYS for an ML arm; an ML arm may raise it but not lower it. |
| `--cpcv-hybrid` | flag | `False` |  | With --cpcv-retrain and an ML --model: RULE selects, the fold model only vetoes. Implements the 'ML as a filter' arm: the names the model likes least (bottom --ml-veto-quantile) are removed before selection. Fixed quantile, no tuning. |
| `--ml-veto-quantile` | value | `0.2` |  | Bottom share of the ML score distribution vetoed by --cpcv-hybrid (default 0.2). Fixed for the whole run. |
| `--cpcv-retrain` | flag | `False` |  | With --cpcv-blocks and an ML --model: train one model per held-out block on that block's complement (purged/embargoed) and score the block with it. In-memory only: no ModelVersion row, no artifact, no activation. Needs --cpcv-test-blocks 1. |
| `--model-version` | value | &mdash; |  | With --cpcv-blocks and an ML --model: pin the READY ModelVersion to score with, without activating it. ENSEMBLE takes "lgb=<version>,lstm=<version>". |


### `sweep_etf_rotation_lstm`

Bounded, resumable LSTM hyperparameter sweep over the ETF tuning window.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start` | value | &mdash; |  | Tuning window start (ISO). Default: earliest eligible snapshot date. |
| `--end` | value | &mdash; |  | Tuning window end (ISO). Default: 2023-12-31. |
| `--folds` | value | `6` |  | Walk-forward steps per trial (default 6). Part of the fingerprint. |
| `--horizon` | value | &mdash; |  | Label horizon in sessions. Default: settings.ETF_LABEL_HORIZON_DAYS. |
| `--min-train-days` | value | &mdash; |  | Minimum dates in the first training window. Default: settings.ETF_MIN_TRAIN_DAYS. |
| `--sequence-length` | value | `20` |  | Bars per sequence (default 20). Part of the fingerprint. |
| `--limit` | value | &mdash; |  | Run only the first N configurations, for a smoke test. |
| `--top` | value | `10` |  | Rows in the ranked table (default 10). |
| `--force` | flag | `False` |  | Re-run configurations whose row is already COMPLETED. |
| `--plan` | flag | `False` |  | Print every configuration, name and fingerprint; write nothing. |
| `--dry-run` | flag | `False` |  | Resolve the window, report the ledger state, train nothing, write nothing. |


### `sync_etf_daily`

Sync TuShare ETF daily bars into ETFDaily over a date window or the trailing gaps.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start` | value | &mdash; |  | YYYY-MM-DD, inclusive. Required unless --recent is given. |
| `--end` | value | `""` |  | YYYY-MM-DD, inclusive. Defaults to today. |
| `--recent` | value | `0` |  | Fill gaps in the trailing N calendar days via sync_etf_daily_recent. Replaces --start/--end. |
| `--force` | flag | `False` |  | Re-sync every open trading date, including dates that already have rows. |
| `--queue` | flag | `False` |  | Dispatch one sync_etf_daily_by_date task per trading date instead of syncing inline. |
| `--dry-run` | flag | `False` |  | Print the planned dates and current counts; calls no provider and writes nothing. |


### `sync_etf_share`

Sync daily ETF shares outstanding for funds that have been eligible.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start` | value | `20160101` |  | Start date YYYYMMDD. |
| `--end` | value | `""` |  | End date YYYYMMDD (default today). |
| `--limit` | value | `0` |  | Only the first N funds. |
| `--pause` | value | `1.1` |  | Seconds between calls. |
| `--only-missing` | flag | `False` |  | Skip funds that already have share rows. |


### `sync_etf_universe`

Refresh ETFUniverse from TuShare fund_basic(market="E").

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--queue` | flag | `False` |  | Dispatch the sync to Celery instead of running inline. |
| `--refresh-first-trade-dates` | flag | `False` |  | Only re-derive first_trade_date and repair blank list_date from stored bars; skip fund_basic. |
| `--dry-run` | flag | `False` |  | Print the current universe counts; calls no provider and writes nothing. |


### `sync_index_valuation`

Sync index valuation history for every index mapped from an ETF.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start` | value | `20100101` |  | Start date YYYYMMDD. |
| `--end` | value | `""` |  | End date YYYYMMDD (default today). |
| `--limit` | value | `0` |  | Only the first N indices. |
| `--pause` | value | `1.2` |  | Seconds between calls (default 1.2; the free tier throttles above roughly one call per second and then returns empty frames instead of an error). |
| `--only-missing` | flag | `False` |  | Skip index codes that already have valuation rows. |


### `sync_index_weight`

Sync monthly index constituent weights for the indices the eligible pool tracks.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start` | value | `20160101` |  | Start date YYYYMMDD. |
| `--end` | value | `""` |  | End date YYYYMMDD (default today). |
| `--limit` | value | `0` |  | Only the first N indices. |
| `--pause` | value | `0.35` |  | Seconds between calls. |
| `--window-months` | value | `6` |  | Months per request (default 6; a year can hit the page limit). |
| `--only-missing` | flag | `False` |  | Skip indices that already have weight rows. |


### `train_etf_rotation_model`

Walk-forward training and reporting for the ETF rotation models.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--type` | value | `lightgbm` |  | Which ETF model to act on (default lightgbm). |
| `--start` | value | &mdash; |  | First snapshot date to consider (ISO). Default: earliest eligible row. |
| `--end` | value | &mdash; |  | Last snapshot date to consider (ISO). Default: latest eligible row. |
| `--folds` | value | `6` |  | Number of expanding-window validation steps (default 6). |
| `--label` | value | `rank_pct` |  | Which stored label the model fits (default rank_pct). dd_rank trains on the safety rank (smallest forward drawdown ranks highest); LightGBM only, the LSTM has its own multi-horizon labels. |
| `--objective` | value | `lambdarank` |  | lambdarank on label_rank_pct grades (default), binary on label_top_bucket, or regression on label_rank_pct. |
| `--boost-rounds` | value | &mdash; |  | Fixed lgb.train round count; no early stopping. |
| `--horizon` | value | &mdash; |  | Label horizon in trading sessions. Default: settings.ETF_LABEL_HORIZON_DAYS (20). |
| `--min-train-days` | value | &mdash; |  | Minimum dates in the first training window. Default: settings.ETF_MIN_TRAIN_DAYS (250). |
| `--version-tag` | value | `""` |  | Suffix appended to the ModelVersion key. |
| `--no-activate` | flag | `False` |  | Register the trained row inactive and leave the serving model alone. Required for a post-release retrain: this command activates by default. |
| `--dry-run` | flag | `False` |  | Build the frames and fold plan, print sample/group counts, train nothing, write nothing. |
| `--report` | flag | `False` |  | Print registered ETF model versions with their holdout metrics and exit. |


## `apps.factors`


### `audit_model_data_quality`

Audit default/null model-data buckets for a historical date range without modifying data.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | &mdash; | yes | Inclusive start date in YYYY-MM-DD format. |
| `--end-date` | value | &mdash; | yes | Inclusive end date in YYYY-MM-DD format. |
| `--symbol` | value | `""` |  | Optional asset symbol or TuShare ts_code to diagnose. |
| `--sample-size` | value | `5` |  | Maximum rows to print for suspicious samples. |


### `backfill_capital_flow_snapshots`

Backfill CapitalFlowSnapshot from TuShare moneyflow and margin_detail onto trading dates.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  |  |
| `--end-date` | value | `<dynamic-date>` |  |  |
| `--symbols` | value | `""` |  |  |
| `--limit-assets` | value | `0` |  |  |


### `backfill_fundamental_snapshots`

Backfill FundamentalFactorSnapshot from TuShare daily_basic and fina_indicator onto trading dates.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  |  |
| `--end-date` | value | `<dynamic-date>` |  |  |
| `--symbols` | value | `""` |  |  |
| `--limit-assets` | value | `0` |  |  |
| `--repair-same-announcement-roe` | flag | `False` |  | Reprocess assets whose stored roe rows point at an older report_end_date for the same fina_indicator announcement date. |


## `apps.macro`


### `backfill_macro_snapshots`

Backfill MacroSnapshot monthly data from TuShare plus historical ChinaBond yield CSV, with AkShare fallback.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  |  |
| `--end-date` | value | `<dynamic-date>` |  |  |
| `--disable-fallback` | flag | `False` |  |  |
| `--resume-yields` | flag | `False` |  |  |


### `backfill_market_context`

Backfill MarketContext history from monthly MacroSnapshot data.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  |  |
| `--end-date` | value | `<dynamic-date>` |  |  |


### `check_earliest_data`

Check earliest available macro snapshot and OHLCV data from sources and local database.

_No options._


## `apps.markets`


### `backfill_asset_list_dates`

Backfill Asset.list_date, delist_date, and listing_status from TuShare stock_basic with AkShare current-listing fallback.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--symbols` | value | `""` |  |  |
| `--limit-assets` | value | `0` |  |  |


### `backfill_asset_suspensions`

Backfill daily asset suspension data from TuShare suspend_d.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  |  |
| `--end-date` | value | `<dynamic-date>` |  |  |
| `--symbols` | value | `""` |  |  |


### `backfill_ohlcv_history`

Backfill OHLCV history from the configured floor date using TuShare.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  |  |
| `--end-date` | value | `""` |  |  |
| `--csv-file` | value | `""` |  |  |
| `--symbols` | value | `""` |  |  |
| `--limit-assets` | value | `0` |  |  |
| `--queue` | flag | `False` |  |  |
| `--technical-indicator-warmup` | flag | `False` |  | Extend the OHLCV repair window earlier by the maximum technical-indicator warm-up lookback and allow that bounded repair to cross the historical floor. |
| `--effective-universe-entry-warmup` | flag | `False` |  | Backfill OHLCV warm-up windows ending on each asset's first effective-universe date in the requested range so technical indicators can initialize when the asset first enters PIT validation scope. |


### `backfill_trading_calendar`

Backfill official exchange trading calendar data from TuShare trade_cal.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  |  |
| `--end-date` | value | `<dynamic-date>` |  |  |
| `--exchange-codes` | value | `SSE,SZSE` |  |  |


### `build_pit_union_benchmark`

Build or refresh the internal point-in-time CSI 500 benchmark.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | &mdash; | yes | Inclusive start date (YYYY-MM-DD). |
| `--end-date` | value | &mdash; | yes | Inclusive end date (YYYY-MM-DD). |
| `--initial-nav` | value | `100000` |  | Initial NAV used for the first benchmark row. |


### `onboard_csi500_universe`

Onboard the CSI 500 effective universe: sync historical memberships, backfill raw data for all current constituents, rebuild the point-in-time benchmark, recompute model inputs, retrain LightGBM/LSTM, and optionally export a reference benchmark suite.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  |  |
| `--end-date` | value | `<dynamic-date>` |  |  |
| `--index-codes` | value | `000905.SH` |  |  |
| `--benchmark-start-date` | value | `""` |  |  |
| `--benchmark-end-date` | value | `""` |  |  |
| `--benchmark-window-days` | value | `180` |  |  |
| `--benchmark-step-days` | value | `30` |  |  |
| `--benchmark-sources` | value | `heuristic,lightgbm,lstm` |  |  |
| `--benchmark-top-n` | value | `3` |  |  |
| `--benchmark-horizon-days` | value | `7` |  |  |
| `--benchmark-holding-period-days` | value | `7` |  |  |
| `--benchmark-capital-fraction-per-entry` | value | `0.5` |  |  |
| `--benchmark-min-up-probability` | value | `0.0` |  |  |
| `--benchmark-name-prefix` | value | `csi500` |  |  |
| `--report-label` | value | `""` |  |  |
| `--report-root-dir` | value | `reports` |  |  |
| `--horizons` | value | `3,7,30` |  |  |
| `--skip-benchmarks` | flag | `False` |  |  |
| `--skip-raw-backfills` | flag | `False` |  |  |
| `--skip-model-backfill` | flag | `False` |  |  |
| `--skip-retrain` | flag | `False` |  |  |
| `--skip-sentiment` | flag | `False` |  |  |
| `--lightgbm-version-tag` | value | `""` |  |  |
| `--lightgbm-use-snapshot-pruning` | flag | `False` |  |  |
| `--lstm-sequence-length` | value | `20` |  |  |
| `--lstm-asset-chunk-size` | value | `60` |  |  |
| `--lstm-max-samples-per-horizon` | value | `30000` |  |  |


### `reconcile_suspension_ohlcv_overlaps`

Verify OHLCV/full-day suspension overlaps against AkShare Baidu suspension notices and optionally delete confirmed OHLCV rows.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--csv-file` | value | `""` |  |  |
| `--symbols` | value | `""` |  |  |
| `--output-file` | value | `""` |  |  |
| `--baidu-cookie` | value | `""` |  |  |
| `--execute` | flag | `False` |  |  |


### `sync_benchmark_index_history`

Sync official benchmark index history for the CSI 500 price and total-return series.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--index-codes` | value | &mdash; |  | Comma-separated benchmark index codes. Defaults to 000905.SH,H00905.CSI. |
| `--start-date` | value | &mdash; |  | Start date in YYYY-MM-DD format. |
| `--end-date` | value | &mdash; |  | End date in YYYY-MM-DD format. |


### `sync_index_constituents`

Sync CSI 500 index constituents, persist membership history, refresh current tags, and dispatch unique asset syncs.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--index-codes` | value | `000905.SH` |  |  |
| `--start-date` | value | `<dynamic-date>` |  | Inclusive start date for index_weight snapshots (YYYY-MM-DD). |
| `--end-date` | value | `<dynamic-date>` |  | Inclusive end date for index_weight snapshots (YYYY-MM-DD). |
| `--skip-sync-dispatch` | flag | `False` |  | Persist membership/tags only and skip dispatching sync_asset_history tasks. |
| `--force-floor-backfill` | flag | `False` |  | Dispatch sync_asset_history with force_floor_backfill=True. |
| `--dispatch-changed-assets-only` | flag | `False` |  | Dispatch only assets whose current CSI 500 membership changed. |


## `apps.prediction`


### `backfill_model_data`

Backfill model input data over a historical date range for heuristic and LightGBM pipelines.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | &mdash; |  | Inclusive start date in YYYY-MM-DD format. |
| `--end-date` | value | &mdash; |  | Inclusive end date in YYYY-MM-DD format. |
| `--sentiment-weight` | value | `0.0` |  |  |
| `--skip-sentiment` | flag | `False` |  |  |
| `--checkpoint-file` | value | `""` |  |  |
| `--resume-from-checkpoint` | flag | `False` |  |  |


### `check_prediction_coverage`

List which universe members have no stored prediction for each open trading date in the lookback window. Writes nothing.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--as-of` | value | &mdash; |  | Last date to check (default: today). |
| `--lookback` | value | &mdash; |  | Trading days to check (default: settings.PREDICTION_RECONCILE_LOOKBACK_TRADING_DAYS = 5). |
| `--source` | value | &mdash; |  | Restrict to one source. Repeatable. |
| `--json` | flag | `False` |  | Machine-readable output. |
| `--fail-on-gaps` | flag | `False` |  | Exit non-zero if any date is missing rows, for use as a gate. |


### `migrate_artifacts_to_store`

Mirror local model artifacts (models/) into the configured artifact store.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--execute` | flag | `False` |  | Actually upload; without it the command is a dry run. |
| `--root` | value | &mdash; |  | Artifact root to walk (default: <BASE_DIR>/models). |
| `--normalize-registry` | flag | `False` |  | Rewrite registry artifact_path values that resolve under BASE_DIR to canonical store keys (requires --execute). |


### `purge_prediction_model_stubs`

Purge inactive legacy phase14_training_stub LightGBM/LSTM ModelVersion rows.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--apply` | flag | `False` |  | Delete the matching rows. Defaults to dry-run mode. |


### `rebuild_lightgbm_pipeline`

Backfill features and retrain 3/7/30-day LightGBM models end-to-end.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  | Training start date (YYYY-MM-DD). Defaults to HISTORICAL_DATA_FLOOR. |
| `--end-date` | value | `<dynamic-date>` |  | Training end date (YYYY-MM-DD). Defaults to yesterday. |
| `--horizons` | value | `3,7,30` |  | Comma-separated horizons to train (subset of 3,7,30). |
| `--skip-backfill` | flag | `False` |  | Skip model data backfill and retrain directly. |
| `--skip-sentiment` | flag | `False` |  | Pass through to backfill_model_data to skip sentiment recomputation. |
| `--version-tag` | value | `""` |  | Optional suffix added to the LightGBM model version to preserve existing artifact families. |
| `--use-snapshot-pruning` | flag | `False` |  | Prune features from the latest active FeatureImportanceSnapshot before retraining. |


### `rebuild_lstm_pipeline`

Backfill features and retrain 3/7/30-day LSTM models end-to-end.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--start-date` | value | `2010-01-01` |  | Training start date (YYYY-MM-DD). Defaults to HISTORICAL_DATA_FLOOR. |
| `--end-date` | value | `<dynamic-date>` |  | Training end date (YYYY-MM-DD). Defaults to yesterday. |
| `--horizons` | value | `3,7,30` |  | Comma-separated horizons to train (subset of 3,7,30). |
| `--sequence-length` | value | `20` |  | LSTM lookback window length in trading rows. |
| `--asset-chunk-size` | value | `60` |  | Number of assets processed per feature-extraction chunk to control memory. |
| `--max-samples-per-horizon` | value | `30000` |  | Upper bound of sequence samples used per horizon to keep retrain memory-safe. |
| `--skip-backfill` | flag | `False` |  | Skip model data backfill and retrain directly. |
| `--skip-sentiment` | flag | `False` |  | Pass through to backfill_model_data to skip sentiment recomputation. |
| `--version-tag` | value | `""` |  | Optional suffix added to the LSTM model version to preserve existing artifact families. |


### `sync_artifacts_from_store`

Download model artifacts from the store into the local cache (for fresh clones).

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--prefix` | value | `models` |  | Store key prefix to sync (default: models). |


### `verify_artifact_store`

Verify registered model artifacts resolve in the configured artifact store.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--json` | flag | `False` |  | Emit a machine-readable JSON report instead of prose. |


## `apps.sentiment`


### `backfill_news`

Fetch and ingest recent market news from configured providers.

| Option | Takes | Default | Required | Help |
| --- | --- | --- | --- | --- |
| `--providers` | value | `eastmoney,sina,tonghuashun` |  | Comma-separated provider list. Supported: eastmoney,sina,tonghuashun |
| `--limit-per-provider` | value | `20` |  |  |
| `--start-at` | value | &mdash; |  | Inclusive lower datetime bound, e.g. 2026-04-15 00:00:00 |
| `--end-at` | value | &mdash; |  | Inclusive upper datetime bound, e.g. 2026-04-15 23:59:59 |
| `--chunk-days` | value | `30` |  | Date-range fetch chunk size in days. |
| `--sleep-seconds` | value | `0.2` |  | Throttle delay between chunks. |
| `--max-retries` | value | `4` |  | Max retries per chunk when provider rate limit is hit. |
| `--dry-run` | flag | `False` |  | Fetch and display rows without writing them. |
| `--queue` | flag | `False` |  | Queue the fetch task via Celery instead of running inline. |
| `--run-pipeline` | flag | `False` |  | Run daily sentiment and concept calculations after ingest. |
