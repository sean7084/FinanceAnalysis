# Technical Guide

Last refreshed: `2026-04-26` (from live DB snapshots after v0.1.9 backfill and cleanup migration).

## Data Metrics Sheet
Date range format: `YYYY-MM-DD`.

| Metric Name | Source of Metric | Storage | Live Coverage | Current Usage and Missing Data Impact |
| --- | --- | --- | --- | --- |
| Assets | TuShare CSI 300 universe sync | `markets_asset` | `300` active listed assets; list dates `1991-01-29` to `2025-07-16`; no null `list_date` | Universe for all model/backtest/API surfaces. Missing `list_date` would weaken listing-age filters, but current coverage is complete. |
| OHLCV | TuShare market sync + backfill | `markets_ohlcv` | `1,145,611` rows, `300` assets, `2001-07-24` to `2026-04-24` | Core price/volume source for indicators, factors, model features, labels, and backtests. Missing rows directly reduce runtime feature quality and tradable dates. |
| Macro Snapshots | TuShare primary, AkShare fallback | `macro_macrosnapshot` | `316` monthly rows, `2000-01-01` to `2026-04-01`; field-level coverage varies | Feeds MarketContext, LightGBM macro features, and macro-aware backtest ranking. Missing macro fields fall back to neutral values and reduce regime sensitivity. |
| Market Context History | Derived from monthly macro snapshots | `macro_marketcontext` | `263` rows, `2005-01-01` to `2026-04-01` | Date-aware macro phase timeline for heuristic adjustments, LightGBM features, and backtest reports. Missing rows fall back to recovery/neutral behavior. |
| Technical Indicators (all) | Stored analytics snapshots from OHLCV | `analytics_technicalindicator` | `1,141,393` rows, `300` assets, `2001-08-21` to `2026-04-25` | API/display store and source for `RS_SCORE`. Runtime RSI/momentum/volatility are recomputed from OHLCV when needed. Missing stored non-RS indicators mainly affects inspection APIs. |
| Signal Events | Calculated from technical indicators and OHLCV patterns | `analytics_signalevent` | `2,795` rows, `294` assets, `2026-04-14` to `2026-04-25` | Used by the factor reversal scorer for oversold confirmation and by signal APIs. Missing rows reduce confirmation strength but OHLCV fallback logic still runs. |
| News Articles | Multi-provider sentiment ingestion | `sentiment_newsarticle` | `40,066` rows, `2026-02-04T18:26:00` to `2026-04-26T00:31:40` | Raw text source for sentiment/concept features. Missing articles make sentiment less informative and can push outputs toward neutral backfill. |
| Sentiment Scores | News-derived article, asset 7-day, and market 7-day scores plus neutral history | `sentiment_sentimentscore` | `1,835,998` rows, `300` assets, `2001-07-24` to `2026-04-25` | Used by heuristic probabilities, LightGBM/LSTM features, dashboards, and factor score storage. Missing asset sentiment falls back to neutral and removes news-tone signal. |
| Concept Heat | Tagged news/sentiment aggregation | `sentiment_conceptheat` | `80` rows, `2026-04-15` to `2026-04-25` | Theme/sector monitoring surface. Missing rows affect concept ranking only, not core prediction/backtest execution. |
| Fundamental Factor Snapshots | TuShare `daily_basic` and `fina_indicator` backfill | `factors_fundamentalfactorsnapshot` | `1,145,013` rows, `300` assets, `2001-07-24` to `2026-04-22`; non-null PE `1,099,979`, PB `1,144,671`, ROE `1,116,509`, ROE QoQ `1,096,762` | Feeds valuation/profitability component scores and LightGBM/LSTM factor features. Null PE/ROE values fall back to neutral feature scores for affected asset-dates. |
| Capital Flow Snapshots | TuShare moneyflow and margin-detail backfill | `factors_capitalflowsnapshot` | `1,145,611` rows, `300` assets, `2001-07-24` to `2026-04-24`; main-force 5d non-null `990,025`; margin 5d non-null `757,510` | Feeds main-force and margin flow component scores. Per-stock northbound columns were removed in v0.1.9 because the stored values were all null/not asset-specific. |
| Asset Money Flow Raw Rows | TuShare stock-level moneyflow | `factors_assetmoneyflowsnapshot` | `990,029` rows, `300` assets, `2007-01-04` to `2026-04-24`; net moneyflow non-null `967,479` | Raw input for main-force flow features. Earlier dates fall back to neutral flow scores. |
| Asset Margin Detail Raw Rows | TuShare margin detail | `factors_assetmargindetailsnapshot` | `768,393` rows, `300` assets, `2010-03-31` to `2026-04-24`; balance non-null `768,041` | Raw input for margin-flow features. Earlier dates fall back to neutral margin score. |
| Factor Scores | Stored composite and component factor surface | `factors_factorscore` | `1,801,500` rows, `300` assets, `2001-07-24` to `2026-04-24`; latest date has `300` rows | Used by factor APIs, heuristic features, and LightGBM/LSTM factor features. Latest nulls: PE percentile `16`, margin flow `110`, other key retained components `0`. |
| Heuristic Prediction Rows | Rule-based multi-horizon probabilities | `prediction_predictionresult` | `9,901` rows, `300` assets, `2024-12-31` to `2026-04-25` | API/dashboard prediction surface and heuristic backtest payload reference. Runtime backtests can also generate candidates on demand. |
| LightGBM Prediction Rows | Stored LightGBM inference output | `prediction_lightgbmprediction` | `444,980` rows, `300` assets, `2016-06-30` to `2026-04-25` | API/dashboard prediction surface. Backtests no longer require precomputed rows and can generate model candidates at runtime. |
| LightGBM Model Artifacts | Per-horizon model artifact registry | `prediction_lightgbmmodelartifact` | `8` total, `3` active; active artifacts trained on `2016-06-01` to `2024-12-31` | Deployment source for LightGBM inference. Missing active artifact for a horizon blocks that horizon's LightGBM runtime path. |
| Model Versions | Model registry metadata | `prediction_modelversion` | `17` total; active LightGBM, LSTM, and ensemble registry rows | Tracks high-level active model families and metrics. For per-horizon LightGBM deployment, use `LightGBMModelArtifact`. |
| Feature Importance Snapshots | LightGBM diagnostics | `prediction_featureimportancesnapshot` | `304` rows, `2026-04-15` to `2026-04-25` | Monitoring and feature-pruning input. Missing snapshots disables historical pruning and falls back to the full engineered feature set. |
| Ensemble Weight Snapshots | Model blending diagnostics | `prediction_ensembleweightsnapshot` | `3` rows, `2024-12-31` to `2026-04-15` | Tracks model-weight history. The latest date reflects a 60-day basis snapshot; the retrain registry stores the 2024-12-31 model-window ensemble metrics. |
| Backtest Export Sheet | Management command export from `BacktestRun` and `BacktestTrade` | `reports/backtests_89_112_v0_1_9/*.csv` | `6` CSVs; run summary/config include IDs `89..112` with IDs `95..100` marked `MISSING`; trades CSV has `11,255` data rows | Release audit artifact for v0.1.9. Missing run IDs are explicitly represented so comparisons do not imply nonexistent rows. |

### precomputed data

Historical model inputs and stored prediction tables are still populated for API/reporting surfaces and retrospective inspection.
That precomputed layer currently covers the backfilled model inputs used by LightGBM and the stored `LightGBMPrediction` history for horizons `3/7/30`.

Backtests no longer depend on stored LightGBM prediction coverage.
In the current implementation, heuristic, LightGBM, and LSTM backtest candidate selection is generated at runtime in `apps/backtest/tasks.py`, and long windows resume through chunked execution state persisted in `BacktestRun.report`.

### stored analytics vs runtime-computed features

Stored analytics rows are primarily for API, dashboard, and inspection surfaces.

- `analytics_technicalindicator` serves the stored indicator history exposed by the technical-indicator API.
- Those stored rows are not the sole source of truth for model/runtime feature extraction.
- Current heuristic and LightGBM runtime paths recompute several features directly from OHLCV when needed, including RSI, momentum, returns, volume ratios, realized volatility, and Bollinger-band-derived checks.
- `RS_SCORE` remains a stored analytics dependency and is still read from `analytics_technicalindicator`.
- `FactorScore` remains a stored dependency for heuristic and LightGBM factor features.

Implication:
- Sparse stored RSI/MACD/BBANDS rows do not by themselves break runtime backtests or runtime LightGBM inference.
- Stale or missing `RS_SCORE` / `FactorScore` rows still affect model behavior.

### macro snapshot coverage and semantics

`MacroSnapshot` stores monthly rows normalized to the first day of each month. Coverage is not uniform across fields because TuShare exposes different historical floors per macro series.

| Field | Upstream source | Stored semantics | Verified non-null range | Current usage |
| --- | --- | --- | --- | --- |
| `dxy` | TuShare `fx_daily` using `USDOLLAR.FXCM` then `USDOLLAR` | Monthly first-of-month normalization of DXY close | `2011-01-01` to `2026-04-01` (`184` rows) | Stored for API/admin; not currently consumed by model or backtest logic |
| `cny_usd` | TuShare `fx_daily` using `USDCNH.FXCM` | Stored as inverted `CNY/USD` from offshore `USD/CNH` quote | `2012-02-01` to `2026-04-01` (`171` rows) | Stored for API/admin; not currently consumed by model or backtest logic |
| `cn10y_yield` | TuShare `yc_cb` with `curve_term=10`, deterministic `curve_type` preference | Monthly first-of-month normalization of China 10Y yield | `2016-06-01` to `2026-04-01` (`119` rows) | Used directly in yield-curve features and MarketContext phase inference |
| `cn2y_yield` | TuShare `yc_cb` with `curve_term=2`, deterministic `curve_type` preference | Monthly first-of-month normalization of China 2Y yield | `2016-06-01` to `2026-04-01` (`119` rows) | Used directly in yield-curve features and MarketContext phase inference |
| `pmi_manufacturing` | TuShare `cn_pmi` field `PMI010000` | Manufacturing PMI | `2005-01-01` to `2026-04-01` (`256` rows) | Used directly in MarketContext phase inference and LightGBM macro features |
| `pmi_non_manufacturing` | TuShare `cn_pmi` field `PMI020100` | Non-manufacturing business activity PMI | `2007-01-01` to `2026-03-01` (`231` rows) | Used directly in MarketContext phase inference and LightGBM macro features |
| `cpi_yoy` | TuShare `cn_cpi` | CPI year-over-year growth | `2000-01-01` to `2026-03-01` (`315` rows) | Used in MarketContext phase inference |
| `ppi_yoy` | TuShare `cn_ppi` | PPI year-over-year growth | `2000-01-01` to `2026-03-01` (`315` rows) | Stored for API/admin; not currently consumed by model or backtest logic |

Important notes:
- `pmi_non_manufacturing` now maps to `PMI020100`. It previously and incorrectly followed the composite PMI field `PMI030000`.
- TuShare `cn_pmi` currently returns `MONTH` uppercase by default. The ingestion path now reads `MONTH`/`month` first and only falls back to `CREATE_TIME` if needed.
- Historical `MarketContext` is now backfilled from `MacroSnapshot` and remains queryable by date. Active historical rows currently span `2005-01-01` to `2026-04-01` (`263` rows).
- Training windows that depend on yield-curve or MarketContext features should start no earlier than `2016-06-01` if you want macro-driven features to be based on real yield data rather than fallback defaults.

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

Current implementation note:
- The daily scorer writes one `FactorScore(mode=COMPOSITE)` row per asset/date.
- In that scorer, `technical_score` is exactly the same value as `technical_reversal_score`.
- The technical component is intentionally a bottom-fishing reversal signal, not a general trend-strength score.

#### Component scores
- Fundamental score: average of `pe_score`, `pb_score`, `roe_trend`
- Capital flow score: average of `main_force_flow_score` and `margin_flow_score`; asset-level northbound fields were physically removed in v0.1.9 because northbound flow is not a valid per-stock field in the current schema.
- Technical reversal score (`technical_score`):
  - Input lookups use the latest available rows on or before `as_of`, not strictly same-day rows.
  - Inputs:
    - `latest_rsi(asset_id, as_of, default=50)`
    - `latest_bbands(asset_id, as_of)` lower band
    - `latest_ohlcv(asset_id, as_of)` close and recent volume
    - `SignalEvent(signal_type=OVERSOLD_COMBINATION)` on or before `as_of`
  - Score blocks:
    - RSI oversold: if `RSI <= 35`, add `0.35`
    - Lower-band proximity: if `close <= lower_band * 1.03`, add `0.25`
    - Confirmed oversold reversal: add `0.40` if either:
      - an `OVERSOLD_COMBINATION` signal exists on or before `as_of`; or
      - the fallback confirmation block passes all of:
        - `RSI < 30`
        - `close <= lower_band * 1.02`
        - latest volume is below `80%` of the average volume over the prior 20 sessions
  - Final formula:
    - `technical_score = min(rsi_block + lower_band_block + reversal_block, 1.0)`
  - Missing-data behavior:
    - missing RSI falls back to `50`, which disables the RSI-driven blocks
    - missing BBANDS or latest OHLCV disables the price-vs-band checks
    - fewer than 21 OHLCV rows disables the fallback volume-confirmed reversal block
  - Interpretation:
    - near `0.00`: no oversold/reversal evidence
    - around `0.35`: RSI-only oversold setup
    - around `0.60`: oversold plus lower-band proximity
    - `1.00`: strongest confirmed reversal setup after capping
  - Not included today:
    - stored MACD, ADX, OBV, SMA/EMA, and RS score analytics do not currently feed `technical_score` in `FactorScore`
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

Primary code: `apps/prediction/tasks_lightgbm.py` (`_extract_features_for_asset`, `_create_feature_matrix`, `_create_labels_for_training`, `train_lightgbm_models`, `_predict_with_lightgbm`).

#### Prediction target / labels
For each horizon (`3`, `7`, `30` days):
- `UP` if forward return >= `+2%`
- `DOWN` if forward return <= `-2%`
- `FLAT` otherwise

The label builder uses the first available trading day on or after `target_date + horizon_days`, not a fixed calendar-row offset.

#### Feature inventory

Current active artifacts are on engineered feature set `v2` and prune historically weak features before training. The live active artifacts currently keep `37` features for `3d`, `37` features for `7d`, and `35` features for `30d`.

| Feature(s) | Source surface | Producer / lookup path | Usage in model pipeline | Fallback behavior | Current live range |
| --- | --- | --- | --- | --- | --- |
| `rsi` | `markets_ohlcv` | Runtime: `latest_rsi()` in `apps/prediction/historical_features.py`; training: `_compute_rsi_series()` in `apps/prediction/tasks_lightgbm.py` | Core technical level / oversold state | Defaults to `50.0` when unavailable | OHLCV source: `2001-07-24` to `2026-04-24` |
| `mom_5d` | `markets_ohlcv` | Runtime: `latest_momentum(..., n_days=5)`; training: 5-day close pct change | Short-horizon price momentum | Defaults to `0.0` when unavailable | OHLCV source: `2001-07-24` to `2026-04-24` |
| `rs_score` | `analytics_technicalindicator` (`RS_SCORE`) | Runtime/training: `latest_rs_score()` and `RS_SCORE` merge in `_create_feature_matrix()` | Cross-sectional relative-strength signal | Defaults to `0.5` when unavailable | `2001-08-21` to `2026-04-25` |
| `rsi_lag_3d`, `rsi_delta_3d` | `markets_ohlcv` | Runtime: lagged `latest_rsi()` lookups; training: shifted RSI series | Short-lag RSI regime and change | Lag falls back to current `rsi`; delta becomes `0` | OHLCV source with 3-day lookback: `2001-07-24` to `2026-04-24` |
| `rsi_lag_5d`, `rsi_delta_5d` | `markets_ohlcv` | Same as above with 5-day lag | Medium-lag RSI regime and change | Lag falls back to current `rsi`; delta becomes `0` | OHLCV source with 5-day lookback: `2001-07-24` to `2026-04-24` |
| `rsi_lag_10d`, `rsi_delta_10d` | `markets_ohlcv` | Same as above with 10-day lag | Longer RSI change signal | Lag falls back to current `rsi`; delta becomes `0` | OHLCV source with 10-day lookback: `2001-07-24` to `2026-04-24` |
| `mom_5d_delta_3d`, `mom_5d_delta_5d`, `mom_5d_delta_10d` | `markets_ohlcv` | Runtime: lagged `latest_momentum()`; training: shifted momentum series | Momentum acceleration / deceleration | Missing lag falls back to current `mom_5d`; delta becomes `0` | OHLCV source with lagged lookback: `2001-07-24` to `2026-04-24` |
| `rs_score_delta_3d`, `rs_score_delta_5d`, `rs_score_delta_10d` | `analytics_technicalindicator` (`RS_SCORE`) | Runtime: lagged `latest_rs_score()`; training: shifted RS score series | Relative-strength change signal | Missing lag falls back to current `rs_score`; delta becomes `0` | `RS_SCORE` source with lagged lookback: `2001-08-21` to `2026-04-25` |
| `return_3d`, `return_5d`, `return_10d` | `markets_ohlcv` | Runtime: `_compute_return()` over recent OHLCV rows; training: pct-change windows | Price trend / reversal context | Defaults to `0.0` when insufficient history | OHLCV source: `2001-07-24` to `2026-04-24` |
| `relative_volume_5d`, `relative_volume_20d` | `markets_ohlcv` | Runtime/training: current volume over rolling mean | Participation / abnormal-volume signal | Defaults to `1.0` when rolling average unavailable | OHLCV source: `2001-07-24` to `2026-04-24` |
| `realized_volatility_5d` | `markets_ohlcv` | Runtime: `_compute_realized_volatility()`; training: rolling std of close returns | Short-term realized risk / noise level | Defaults to `0.0` when insufficient history | OHLCV source: `2001-07-24` to `2026-04-24` |
| `pe_percentile` | `factors_factorscore` derived from `factors_fundamentalfactorsnapshot` | Runtime: latest `FactorScore.pe_percentile_score`; training: `merge_asof` from `FactorScore` | Valuation percentile for bottom-catching logic | Fills to `0.5` if no score or `NULL` | FactorScore `2001-07-24` to `2026-04-24`; fundamental source `1,145,013` rows through `2026-04-22` |
| `pb_percentile` | `factors_factorscore` derived from `factors_fundamentalfactorsnapshot` | Same path as above | Book-value valuation percentile | Fills to `0.5` if no score or `NULL` | FactorScore `2001-07-24` to `2026-04-24`; source PB non-null `1,144,671` rows |
| `roe_trend` | `factors_factorscore` derived from `factors_fundamentalfactorsnapshot` | Same path as above | Fundamental profitability trend signal | Fills to `0.5` if no score or `NULL` | FactorScore `2001-07-24` to `2026-04-24`; ROE non-null `1,116,509`, ROE QoQ non-null `1,096,762` |
| `northbound_flow` | Neutral compatibility placeholder | Runtime/training: fixed `0.5` in LightGBM feature extraction/matrix build | Preserves compatibility with active artifacts that still contain the feature name | Always `0.5` | Per-stock DB/API field removed in v0.1.9; not read from `FactorScore` |
| `main_force_flow` | `factors_factorscore` derived from moneyflow snapshots | Runtime/training: latest `main_force_flow_score` from `FactorScore` | Main-force flow pressure signal | Fills to `0.5` if no score or `NULL` | FactorScore `2001-07-24` to `2026-04-24`; raw moneyflow from `2007-01-04` |
| `margin_flow` | `factors_factorscore` derived from margin detail snapshots | Runtime/training: latest `margin_flow_score` from `FactorScore` | Margin-balance pressure signal | Fills to `0.5` if no score or `NULL` | FactorScore `2001-07-24` to `2026-04-24`; raw margin detail from `2010-03-31` |
| `factor_composite` | `factors_factorscore` | Runtime/training: latest `FactorScore.composite_score` | Aggregate factor regime / bottom-probability context | Fills to `0.5` if no score or `NULL` | `2001-07-24` to `2026-04-24` |
| `macro_phase` | `macro_marketcontext` | Runtime: latest active `MarketContext` on or before `as_of`; training: as-of merge from `MarketContext` timeline | Encodes current macro regime (`RECESSION/RECOVERY/OVERHEAT/STAGFLATION`) | Fills to `1.0` (`RECOVERY`) when unavailable | Active historical timeline: `2005-01-01` to `2026-04-01` |
| `pmi_manufacturing` | `macro_macrosnapshot` | Runtime/training: latest `MacroSnapshot` <= `as_of` | Manufacturing activity backdrop | Fills to `50.0` when unavailable | Non-null `2005-01-01` to `2026-04-01` (`256` rows) |
| `pmi_non_manufacturing` | `macro_macrosnapshot` | Runtime/training: latest `MacroSnapshot` <= `as_of` | Services activity backdrop | Fills to `50.0` when unavailable | Non-null `2007-01-01` to `2026-03-01` (`231` rows) |
| `yield_curve` | `macro_macrosnapshot` | Runtime/training: `cn10y_yield - cn2y_yield` from latest `MacroSnapshot` | Rates / cycle-state signal | Fills to `0.0` when unavailable | Yield fields non-null `2016-06-01` to `2026-04-01` (`119` rows) |
| `sentiment_7d` | `sentiment_sentimentscore` (`ASSET_7D`) | Runtime/training: latest asset 7-day sentiment <= `as_of` | Asset-level news tone signal | Fills to `0.0` when unavailable | `2001-07-24` to `2026-04-25` |
| `sentiment_7d_avg_20d` | `sentiment_sentimentscore` (`ASSET_7D`) | Runtime: 20-day aggregate query; training: 20-day rolling mean after as-of merge | Smoothed sentiment regime | Fills to `0.0` when unavailable | `2001-07-24` to `2026-04-25` |
| `rsi_x_relative_volume_5d` | Runtime-engineered from OHLCV features | `_build_interaction_features()` | Captures oversold/overbought state under abnormal volume | Inherits constituent fallbacks | Effective range inherits `rsi` and `relative_volume_5d`: `2001-07-24` to `2026-04-24` |
| `rsi_x_macro_phase` | Runtime-engineered from OHLCV + macro | `_build_interaction_features()` | Technical signal conditioned on macro regime | Inherits constituent fallbacks | Effective range inherits `rsi` plus macro fallback behavior |
| `factor_composite_x_sentiment` | Runtime-engineered from factor + sentiment | `_build_interaction_features()` | Joint bottom-score and news-tone interaction | Inherits constituent fallbacks | Effective range inherits `factor_composite` and `sentiment_7d`: `2001-07-24` to `2026-04-24/25` |
| `northbound_flow_x_mom_5d` | Runtime-engineered from neutral placeholder + OHLCV | `_build_interaction_features()` | Artifact-compatible interaction term | Inherits constant `northbound_flow=0.5` and momentum fallback | Kept only because active artifacts include the feature; DB/API northbound score was removed |
| `pe_percentile_x_macro_phase` | Runtime-engineered from factor + macro | `_build_interaction_features()` | Valuation conditioned on macro regime | Inherits constituent fallbacks | Effective range inherits populated factor and macro snapshots; PE nulls still fall back to `0.5` |

Current implementation note:
- Fundamental, moneyflow, margin, factor-score, macro, sentiment, and LightGBM prediction history have been backfilled for the current v0.1.9 release window.
- `northbound_flow` remains as a neutral `0.5` feature only for LightGBM artifact compatibility. It is no longer stored on `FactorScore`, exposed in the dashboard DTO, or stored in per-stock capital-flow snapshots.

#### Training process
1. Build feature matrix for training window.
2. Build labels per horizon.
3. Align labels to feature rows.
4. Prune historically weak features using recent feature-importance history when available.
5. Standardize features with `StandardScaler`.
6. Train LightGBM multiclass booster.
7. Calibrate probabilities (`sigmoid` for sklearn-style estimators when available; `identity` fallback for the native booster).
8. Save artifact files + DB metadata + feature importance snapshots.
9. Refresh ensemble weights based on latest metrics.

Current active artifact snapshot:
- Training window: `2016-06-01` to `2024-12-31`
- Training samples: `548605` for each active horizon artifact
- 3d artifact: `lgb-3d-2024-12-31`, accuracy `0.569238`, features `37`
- 7d artifact: `lgb-7d-2024-12-31`, accuracy `0.513034`, features `37`
- 30d artifact: `lgb-30d-2024-12-31`, accuracy `0.593609`, features `35`
- Current calibration metadata on all active artifacts: `identity`

Registry note:
- Per-horizon deployment state should be read from `LightGBMModelArtifact`.
- `ModelVersion(model_type=LIGHTGBM)` is refreshed during training, but it does not retain one simultaneously active row per horizon.

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
- Each successful LSTM retrain now also refreshes the active ensemble weights against the current active LightGBM artifacts.

Model structure and training flow:
- Sequence model: 2-layer LSTM (`hidden_size=64`, `dropout=0.2`) + MLP classifier head.
- Inputs: same engineered feature set as LightGBM feature matrix, converted into rolling sequences (default length `20`).
- Labels: same direction labels as LightGBM (`UP/FLAT/DOWN` using ±2% thresholds by horizon).
- Optimization: `Adam(lr=1e-3)` + cross-entropy, temporal train/validation split (`80/20`), best-validation checkpoint selection.

Current active LSTM registry snapshot:
- id: `15`
- version: `lstm-2024-12-31`
- status: `READY`
- trained_at: `2026-04-25` (latest retrain run)
- training window: `2016-06-01` to `2024-12-31`
- artifact_path: `/app/models/lstm/lstm-2024-12-31`
- metrics:
  - aggregate accuracy: `0.465278`
  - 3d accuracy: `0.547333`
  - 7d accuracy: `0.428000`
  - 30d accuracy: `0.420500`

Formula note:
- Command example:
  - `docker exec finance_analysis_django python manage.py rebuild_lstm_pipeline --skip-backfill --start-date 2016-06-01 --end-date 2024-12-31 --horizons 3,7,30 --sequence-length 20 --asset-chunk-size 60 --max-samples-per-horizon 30000`

### 5) Active Model Registry Snapshot

#### Active LightGBM artifacts (`is_active=True`)

| Horizon | Version | Trained At (UTC) | Training Window | Samples | Features | Accuracy |
| --- | --- | --- | --- | ---: | ---: | ---: |
| 3d | `lgb-3d-2024-12-31` | `2026-04-25 15:45:25` | `2016-06-01` to `2024-12-31` | 548605 | 37 | 0.569238 |
| 7d | `lgb-7d-2024-12-31` | `2026-04-25 15:45:42` | `2016-06-01` to `2024-12-31` | 548605 | 37 | 0.513034 |
| 30d | `lgb-30d-2024-12-31` | `2026-04-25 15:45:59` | `2016-06-01` to `2024-12-31` | 548605 | 35 | 0.593609 |

#### Active ensemble model version
- id: `14`
- version: `ensemble-2024-12-31`
- status: `READY`
- trained_at: `2026-04-25 15:47:28+00:00`
- training window: `2016-06-01` to `2024-12-31`
- metrics: `lightgbm_accuracy=0.558627`, `heuristic_accuracy=0.5`, `lstm_accuracy=0.465278`
- implied registry weights from those metrics: `lightgbm=0.3666`, `lstm=0.3053`, `heuristic=0.3281`

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

Registry nuance:
- The active ensemble `ModelVersion` is dated to the retrain window end (`2024-12-31`) and carries the latest retrain metrics for LightGBM/LSTM/heuristic blending.
- The latest `EnsembleWeightSnapshot` row is a chronological monitoring snapshot dated `2026-04-15`; it reflects the 60-day basis available for that date, not the latest retrain artifact window.

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