# Technical Guide

Last refreshed: `2026-04-18` (from live DB snapshots).

## Data Metrics Sheet

Date range format: `YYYY-MM-DD`.

| Metric Name | Source of Metric | Storage | Current Date Range |
| --- | --- | --- | --- |
| OHLCV | TuShare (markets sync tasks + backfill command) | `markets_ohlcv` | `2001-07-24` to `2026-04-17` |
| Macro Snapshots | TuShare primary, AkShare fallback | `macro_macrosnapshot` | `2000-01-01` to `2026-04-01` |
| Technical Indicators (all) | Calculated from OHLCV (TA-Lib + analytics tasks) | `analytics_technicalindicator` | `2001-08-21` to `2026-04-18` |
| Technical Indicator: RSI | Calculated from OHLCV | `analytics_technicalindicator` (`indicator_type=RSI`) | `2026-04-14` to `2026-04-14` |
| Technical Indicator: MACD | Calculated from OHLCV | `analytics_technicalindicator` (`indicator_type=MACD`) | `2026-04-14` to `2026-04-14` |
| Technical Indicator: BBANDS | Calculated from OHLCV | `analytics_technicalindicator` (`indicator_type=BBANDS`) | `2026-04-14` to `2026-04-14` |
| Technical Indicator: SMA | Calculated from OHLCV | `analytics_technicalindicator` (`indicator_type=SMA`) | `2026-04-14` to `2026-04-14` |
| Technical Indicator: EMA | Calculated from OHLCV | `analytics_technicalindicator` (`indicator_type=EMA`) | `2026-04-14` to `2026-04-14` |
| Technical Indicator: STOCH | Calculated from OHLCV | `analytics_technicalindicator` (`indicator_type=STOCH`) | `2026-04-14` to `2026-04-14` |
| Technical Indicator: ADX | Calculated from OHLCV | `analytics_technicalindicator` (`indicator_type=ADX`) | `2026-04-14` to `2026-04-14` |
| Technical Indicator: OBV | Calculated from OHLCV | `analytics_technicalindicator` (`indicator_type=OBV`) | `2026-04-14` to `2026-04-14` |
| Technical Indicator: FIB_RET | Calculated from OHLCV | `analytics_technicalindicator` (`indicator_type=FIB_RET`) | `2026-04-14` to `2026-04-14` |
| Technical Indicator: MOM_5D | Calculated from OHLCV | `analytics_technicalindicator` (`indicator_type=MOM_5D`) | `2026-04-14` to `2026-04-17` |
| Technical Indicator: MOM_10D | Calculated from OHLCV | `analytics_technicalindicator` (`indicator_type=MOM_10D`) | `2026-04-14` to `2026-04-17` |
| Technical Indicator: MOM_20D | Calculated from OHLCV | `analytics_technicalindicator` (`indicator_type=MOM_20D`) | `2026-04-14` to `2026-04-17` |
| Technical Indicator: RS_SCORE | Calculated from OHLCV cross-section | `analytics_technicalindicator` (`indicator_type=RS_SCORE`) | `2001-08-21` to `2026-04-18` |
| Signal Events (all types) | Calculated from technical indicators and OHLCV patterns | `analytics_signalevent` | `2026-04-14` to `2026-04-18` |
| News Articles | Multi-provider sentiment ingestion pipeline | `sentiment_newsarticle` | `2026-03-27` to `2026-04-18` |
| Sentiment Scores (ARTICLE/ASSET_7D/MARKET_7D) | Calculated from ingested news + neutral backfill | `sentiment_sentimentscore` | `2001-07-24` to `2026-04-18` |
| Concept Heat | Calculated from tagged news/sentiment | `sentiment_conceptheat` | `2026-04-15` to `2026-04-16` |
| Fundamental Factor Snapshots | TuShare/AkShare ingestion (currently unpopulated) | `factors_fundamentalfactorsnapshot` | `N/A` |
| Capital Flow Snapshots | TuShare/AkShare ingestion (currently unpopulated) | `factors_capitalflowsnapshot` | `N/A` |
| Factor Scores (composite and components) | Calculated from fundamentals/flows/technical/sentiment | `factors_factorscore` | `2001-07-24` to `2026-04-16` |
| Heuristic Prediction Rows | Model-generated (rule-based probabilities) | `prediction_predictionresult` | `2026-04-15` to `2026-04-18` |
| LightGBM Prediction Rows | Model-generated (LightGBM inference) | `prediction_lightgbmprediction` | `2016-06-30` to `2026-04-18` |
| LightGBM Model Artifacts | Model training output metadata | `prediction_lightgbmmodelartifact` | `2026-04-15` to `2026-04-16` |
| Model Versions | Training registry metadata | `prediction_modelversion` | `2026-04-15` to `2026-04-18` |
| Feature Importance Snapshots | Model-generated diagnostics | `prediction_featureimportancesnapshot` | `2026-04-15` to `2026-04-16` |
| Ensemble Weight Snapshots | Model-generated blending weights | `prediction_ensembleweightsnapshot` | `2026-04-11` to `2026-04-15` |
| Trade Decision Coverage (target/stop/R:R/trade_score/suggested) | Prediction + odds logic | `prediction_predictionresult`, `prediction_lightgbmprediction` | Heuristic: `2026-04-15` to `2026-04-18`; LightGBM: `2016-06-30` to `2026-04-18` |

## Current Models and Formulas

### 1) Heuristic Probability Model

Primary code: `apps/prediction/tasks.py` (`_feature_snapshot`, `_probabilities_from_features`).

#### Inputs (feature snapshot)
- `factor_composite`
- `factor_bottom_prob`
- `sentiment_score` (ASSET_7D)
- `rsi`
- `mom_5d`
- `rs_score`

#### Core probability formula
- Base prior: `base = 0.33`
- Signals:
  - `sentiment_signal = sentiment_score * 0.25`
  - `momentum_signal = mom_5d * 1.2`
  - `rs_signal = (rs_score - 0.5) * 0.4`
  - `factor_signal = (factor_bottom_prob - 0.5)`
- Horizon scale:
  - 3d: `1.10`
  - 7d: `1.00`
  - 30d: `0.85`

Unclamped equations:
- `up = base + (momentum_signal + sentiment_signal + rs_signal - factor_signal) * horizon_scale`
- `down = base + (factor_signal - sentiment_signal - momentum_signal - rs_signal) * horizon_scale`
- `flat = 1 - up - down`

Macro adjustments:
- `RECESSION`: `down += 0.04`, `up -= 0.02`
- `RECOVERY`: `up += 0.03`, `down -= 0.02`

### 2) Factor Scoring Model (Bottom Candidate Engine)

Primary code: `apps/factors/tasks.py` (`calculate_factor_scores_for_date`, `_technical_reversal_score`).

#### Component scores
- Fundamental score: average of `pe_score`, `pb_score`, `roe_trend`
- Capital flow score: average of northbound/main-force/margin percentile scores
- Technical reversal score:
  - RSI <= 35: `+0.35`
  - Close near lower BBAND (`<= 1.03 * lower`): `+0.25`
  - Oversold reversal block: `+0.40`
  - Capped at `1.0`
- Sentiment score mapping: from `[-1, 1]` to `[0, 1]`

#### Weights in composite formula
Default weights before normalization:
- financial: `0.4`
- capital flow: `0.3`
- technical: `0.3`
- sentiment: `0.0`

Composite:
- `composite = financial_score * financial_weight + capital_flow_score * flow_weight + technical_score * technical_weight + sentiment_score * sentiment_weight`
- `bottom_probability_score = clamp(composite, 0, 1)`

### 3) LightGBM Multi-class Model

Primary code: `apps/prediction/tasks_lightgbm.py` (`_create_feature_matrix`, `_create_labels_for_training`, `train_lightgbm_models`).

#### Prediction target / labels
For each horizon (`3`, `7`, `30` days):
- `UP` if forward return >= `+2%`
- `DOWN` if forward return <= `-2%`
- `FLAT` otherwise

#### Feature families
- Technical: RSI, momentum, returns, volume ratios, volatility, RS score
- Lag/derivative features: lag windows `3/5/10` days (delta and lag terms)
- Fundamental/flow: PE/PB/ROE trend, northbound/main-force/margin, factor composite
- Macro: phase code + PMI + yield curve
- Sentiment: 7d sentiment and 20d moving average
- Interaction features (engineered):
  - `rsi_x_relative_volume_5d`
  - `rsi_x_macro_phase`
  - `factor_composite_x_sentiment`
  - `northbound_flow_x_mom_5d`
  - `pe_percentile_x_macro_phase`

#### Training process
1. Build feature matrix for training window.
2. Build labels per horizon.
3. Align labels to feature rows.
4. Prune historically weak features when available.
5. Standardize features with `StandardScaler`.
6. Train LightGBM multiclass booster.
7. Calibrate probabilities (`sigmoid` when possible; identity fallback).
8. Save artifact files + DB metadata + feature importance snapshots.
9. Refresh ensemble weights based on latest metrics.

#### Hyperparameters (current)
- objective: `multiclass`
- num_class: `3`
- num_leaves: `31`
- learning_rate: `0.05`
- feature_fraction: `0.8`
- bagging_fraction: `0.8`
- bagging_freq: `5`
- lambda_l1: `0.1`
- lambda_l2: `0.1`
- min_data_in_leaf: `20`
- num_boost_round: `200`
- random_state: `42`

### 4) LSTM Model (Current Status)

Primary code references:
- training task: `apps/prediction/tasks_lstm.py` (`train_lstm_models`)
- management command: `apps/prediction/management/commands/rebuild_lstm_pipeline.py`
- registry: `apps/prediction/models.py` (`ModelVersion.ModelType.LSTM`)

Current implementation status:
- LSTM now has a real retraining pipeline implemented with PyTorch.
- Training supports horizons `3/7/30`, user-configurable sequence length, chunked feature extraction, and capped per-horizon sequence sampling to keep memory stable on long windows.
- Artifacts are persisted under `models/lstm/<version>/` (`3d_model.pt`, `7d_model.pt`, `30d_model.pt`, and `summary.json`) and the active `ModelVersion` row is updated via `update_or_create`.

Model structure and training flow:
- Sequence model: 2-layer LSTM (`hidden_size=64`, `dropout=0.2`) + MLP classifier head.
- Inputs: same engineered feature set as LightGBM feature matrix, converted into rolling sequences (default length `20`).
- Labels: same direction labels as LightGBM (`UP/FLAT/DOWN` using ±2% thresholds by horizon).
- Optimization: `Adam(lr=1e-3)` + cross-entropy, temporal train/validation split (`80/20`), best-validation checkpoint selection.

Current active LSTM registry snapshot:
- id: `15`
- version: `lstm-2024-12-31`
- status: `READY`
- trained_at: `2026-04-18` (latest retrain run)
- training window: `2000-01-01` to `2024-12-31`
- artifact_path: `/app/models/lstm/lstm-2024-12-31`
- metrics:
  - aggregate accuracy: `0.425694`
  - 3d accuracy: `0.471250`
  - 7d accuracy: `0.365417`
  - 30d accuracy: `0.440417`

Formula note:
- Command example:
  - `docker exec finance_analysis_django python manage.py rebuild_lstm_pipeline --skip-backfill --start-date 2000-01-01 --end-date 2024-12-31 --horizons 3,7,30 --sequence-length 20 --asset-chunk-size 40 --max-samples-per-horizon 12000`

### 5) Active Model Registry Snapshot

#### Active LightGBM artifacts (`is_active=True`)

| Horizon | Version | Trained At (UTC) | Training Window | Samples | Features | Accuracy |
| --- | --- | --- | --- | ---: | ---: | ---: |
| 3d | `lgb-3d-2026-04-15` | `2026-04-16 08:56:36` | `2016-04-16` to `2026-04-15` | 363854 | 39 | 0.585669 |
| 7d | `lgb-7d-2026-04-15` | `2026-04-16 08:57:02` | `2016-04-16` to `2026-04-15` | 598499 | 39 | 0.468084 |
| 30d | `lgb-30d-2026-04-15` | `2026-04-16 08:57:22` | `2016-04-16` to `2026-04-15` | 358127 | 39 | 0.481488 |

#### Active ensemble model version
- id: `9`
- version: `ensemble-2026-04-18`
- status: `READY`
- trained_at: `2026-04-18 04:00:00+00:00`
- training window: `2021-04-18` to `2026-04-18`
- metrics: `accuracy=0.5`, `f1_macro=0.5`

### 6) Ensemble Weight Model

Primary code: `apps/prediction/tasks_lightgbm.py` (`_refresh_ensemble_weights`).

- Basis window: last `60` days.
- Weights proportional to model accuracies (`LightGBM`, `LSTM`, `Heuristic`) with 4-decimal quantization.
- Fallback when no usable accuracy: approximately equal weights (`0.3333/0.3333/0.3334`).

Latest snapshot (`2026-04-15`):
- `lightgbm_weight = 0.5058`
- `heuristic_weight = 0.4942`
- `lstm_weight = 0.0000`
- basis metrics:
  - `lightgbm_accuracy = 0.511747...`
  - `heuristic_accuracy = 0.5`
  - `lstm_accuracy = 0.0`

### 7) Trade Decision Model

Primary code: `apps/prediction/odds.py` (`estimate_trade_decision`).

#### Outputs persisted on predictions
- `target_price`
- `stop_loss_price`
- `risk_reward_ratio`
- `trade_score`
- `suggested`

#### Key rules and thresholds
- Horizon fallback target/stop assumptions:
  - 3d: `+3% / -2%`
  - 7d: `+6% / -4%`
  - 30d: `+12% / -8%`
- Minimum risk buffer: `0.5%` of close.
- Suggested setup requires:
  - predicted label is `UP`
  - `risk_reward_ratio >= 1.5`
  - `trade_score >= 1.0`

## Backtest Workbench (Detailed)

Primary UI: `frontend/src/pages/BacktestWorkbenchPage.tsx`.

Primary backend/API:
- `POST /api/v1/backtest/` (create run and enqueue async job)
- `GET /api/v1/backtest/` (list runs)
- `GET /api/v1/backtest/{id}/trades/` (trade details)
- Celery task: `apps/backtest/tasks.py::run_backtest`

### Runtime behavior overview

1. Create run from UI form (`strategy_type=PREDICTION_THRESHOLD`).
2. Backend validates parameter set and queues asynchronous job.
3. Job iterates trading dates, closes positions first, then opens positions on eligible entry days.
4. Sell exits can be either scheduled-hold exit or TP/SL early exit.
5. Final report writes equity curve, benchmark curve, and strategy metadata.

### Runner options and semantics

| Runner Option | Parameter Key | Semantics |
| --- | --- | --- |
| Run Mode (Single / Rolling Batch) | UI-only mode | Single creates one run; batch creates multiple sliding windows. |
| Prediction Source | `prediction_source` | `heuristic`, `lightgbm`, or `lstm`. UI `all-models` option submits parallel runs for all three sources. |
| Start Date / End Date | `start_date`, `end_date` | Date bounds of backtest run. |
| Date Preset | UI helper | One-click date presets for last year / last 6 months. |
| Forecast Horizon (Days) | `horizon_days` | Prediction horizon in `{3, 7, 30}`. |
| Selections per Entry | `top_n` | Candidate count used by top-N mode. |
| Minimum Up Probability | `up_threshold` | Minimum `up_probability` filter before candidate enters ranking. |
| Candidate Selection Mode | `candidate_mode` | `top_n` or `trade_score`. |
| Maximum Open Positions | `max_positions` | Final cap after ranking/filtering. |
| Trade Score Source | `trade_score_scope` | `independent` (selected model only) or `combined` (heuristic+LightGBM averaging). |
| Minimum Trade Score | `trade_score_threshold` | Minimum `trade_score` when mode is `trade_score`. |
| Planned Holding Days | `holding_period_days` | Scheduled holding days before normal sell. |
| Capital Allocation per Entry | `capital_fraction_per_entry` | Fraction of initial capital deployable per entry cycle. |
| Starting Capital | `initial_capital` | Portfolio initial cash for run. |
| Enable Macro-Aware Ranking | `use_macro_context` | Applies macro-phase multiplier to candidate ranking and writes monthly macro report. |
| Enable TP/SL Early Exit | `enable_stop_target_exit` | Enables early sell on stop-loss / target-price before scheduled exit date. |
| Entry Weekdays | `entry_weekdays` | Allowed opening weekdays (`MON`..`FRI`). |

### Candidate modes in detail

- `top_n` mode:
  - Uses prediction source (`heuristic`, `lightgbm`, or `lstm`) and ranks by `up_probability`.
- `trade_score` mode:
  - `independent`: uses selected model predictions and filters by `trade_score_threshold`.
  - `combined`: merges heuristic + LightGBM for each asset and averages trade score/up probability.

### Exit logic in detail

- Scheduled exit:
  - Exit on first trading day after `holding_period_days` target date.
- Early exit (when enabled):
  - Exit immediately when close <= `stop_loss_price` (`exit_reason=STOP_LOSS`) or
  - close >= `target_price` (`exit_reason=TARGET_PRICE`).
- If neither condition is met, position exits with `exit_reason=SCHEDULED` on scheduled date.

### Report payload highlights

Backtest run report contains:
- `equity_curve`
- `benchmark.equity_curve`
- `prediction_source`
- `candidate_mode`
- `trade_score_scope`
- `entry_weekdays`
- `holding_period_days`
- `enable_stop_target_exit`
- `macro_context_monthly` (when macro-aware ranking is enabled)

### Default values currently used by UI

- End Date defaults to the most recent Friday based on real-time local date.
- Start Date defaults to one year before that end date (`end_date - 364 days`).
- Other defaults include:
  - `prediction_source=heuristic`
  - optional UI source `all-models` (creates separate runs for `heuristic`, `lightgbm`, and `lstm`)
  - `horizon_days=7`
  - `top_n=3`
  - `up_threshold=0.55`
  - `candidate_mode=top_n`
  - `trade_score_scope=independent`
  - `trade_score_threshold=1.0`
  - `max_positions=5`
  - `use_macro_context=true`
  - `enable_stop_target_exit=true`