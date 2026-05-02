# Technical Guide

Last refreshed: `2026-04-27` (from live DB snapshots after expanding the Data Metrics Sheet coverage audit).

## Data Metrics Sheet
Date range format: `YYYY-MM-DD`.

Universe operations now use `sync_index_constituents` for CSI 300 + CSI A500 membership history/tag sync, `run_reference_benchmark_suite` for exported rolling benchmark bundles, and `onboard_csi_a500_universe` for the end-to-end A500 onboarding/retrain workflow.

| Metric Name | Source of Metric | Storage | Live Coverage | Current Usage | Missing Data Impact |
| --- | --- | --- | --- | --- | --- |
| Assets | TuShare CSI 300 + CSI A500 universe sync | `markets_asset`, `markets_indexmembership` | Active benchmark union after sync; current memberships live in `membership_tags`, historical snapshots live in `markets_indexmembership`, and overlapping constituents remain deduplicated at the asset row level. | Universe for `Heuristic`, `LightGBM`, `LSTM`, backtests, and API/dashboard surfaces. | Missing `list_date` weakens listing-age filters; missing membership tags/history breaks overlap-aware universe management and targeted onboarding. |
| OHLCV | TuShare market sync + backfill | `markets_ohlcv` | `1,145,611` rows, `300` assets, `2001-07-24` to `2026-04-24` | Core source for `Heuristic`, `LightGBM`, `LSTM`, runtime TP/SL logic, backtest fills/exits, and chart APIs. | Missing rows reduce tradable dates, degrade runtime features, and can suppress backtest entry/exit pricing. |
| PMI Manufacturing | TuShare `cn_pmi` field `PMI010000` | `macro_macrosnapshot.pmi_manufacturing` | `256` non-null rows, `2005-01-01` to `2026-04-01` | Used by `LightGBM`/`LSTM` macro features and `MarketContext` phase inference. | Missing values fall back to neutral PMI assumptions and weaken macro regime sensitivity. |
| PMI Non-Manufacturing | TuShare `cn_pmi` field `PMI020100` | `macro_macrosnapshot.pmi_non_manufacturing` | `231` non-null rows, `2007-01-01` to `2026-03-01` | Used by `LightGBM`/`LSTM` macro features and `MarketContext` phase inference. | Missing values fall back to neutral PMI assumptions and weaken services-side macro context. |
| China 10Y Yield | TuShare `yc_cb` (`curve_term=10`) | `macro_macrosnapshot.cn10y_yield` | `119` non-null rows, `2016-06-01` to `2026-04-01` | Used with 2Y yield for `LightGBM`/`LSTM` yield-curve features and `MarketContext` phase inference. | Missing pair coverage collapses yield-curve features toward neutral defaults. |
| China 2Y Yield | TuShare `yc_cb` (`curve_term=2`) | `macro_macrosnapshot.cn2y_yield` | `119` non-null rows, `2016-06-01` to `2026-04-01` | Used with 10Y yield for `LightGBM`/`LSTM` yield-curve features and `MarketContext` phase inference. | Missing pair coverage collapses yield-curve features toward neutral defaults. |
| CPI YoY | TuShare `cn_cpi` | `macro_macrosnapshot.cpi_yoy` | `315` non-null rows, `2000-01-01` to `2026-03-01` | Used by `MarketContext` phase inference and macro APIs/admin. | Missing values can distort macro phase inference and push context toward fallback behavior. |
| PPI YoY | TuShare `cn_ppi` | `macro_macrosnapshot.ppi_yoy` | `315` non-null rows, `2000-01-01` to `2026-03-01` | API/admin only; not consumed by `Heuristic`, `LightGBM`, `LSTM`, or backtest runtime today. | No direct model/backtest effect today; reduces macro inspection completeness only. |
| DXY | TuShare `fx_daily` | `macro_macrosnapshot.dxy` | `184` non-null rows, `2011-01-01` to `2026-04-01` | API/admin only; not consumed by `Heuristic`, `LightGBM`, `LSTM`, or backtest runtime today. | No direct model/backtest effect today; reduces FX inspection completeness only. |
| CNY/USD | TuShare `fx_daily` | `macro_macrosnapshot.cny_usd` | `171` non-null rows, `2012-02-01` to `2026-04-01` | API/admin only; not consumed by `Heuristic`, `LightGBM`, `LSTM`, or backtest runtime today. | No direct model/backtest effect today; reduces FX inspection completeness only. |
| Market Context History | Derived from monthly macro snapshots | `macro_marketcontext` | `263` rows, `2005-01-01` to `2026-04-01` | Used by `Heuristic` macro adjustments, `LightGBM`/`LSTM` `macro_phase`, and macro-aware backtest ranking/reporting. | Missing rows fall back to recovery/neutral behavior and weaken macro-aware ranking. |
| Technical Indicator: RS_SCORE | Stored cross-sectional relative strength | `analytics_technicalindicator` | `1,128,982` rows, `300` assets, `2001-08-21` to `2026-04-26` | Direct input for `Heuristic`, `LightGBM`, `LSTM`, and runtime backtest candidate generation. | Missing rows fall back to neutral `0.5` and weaken relative-strength signal quality. |
| Technical Indicator: RSI | Stored analytics snapshot from OHLCV | `analytics_technicalindicator` | `300` rows, `300` assets, `2026-04-14` only | API/dashboard inspection only; runtime `Heuristic` and `LightGBM` recompute RSI from OHLCV when needed. | No direct runtime model/backtest effect today; only stored indicator history becomes sparse. |
| Technical Indicator: MACD | Stored analytics snapshot from OHLCV | `analytics_technicalindicator` | `300` rows, `300` assets, `2026-04-14` only | API/dashboard inspection only. | No direct runtime model/backtest effect today; only stored indicator history becomes sparse. |
| Technical Indicator: BBANDS | Stored analytics snapshot from OHLCV | `analytics_technicalindicator` | `300` rows, `300` assets, `2026-04-14` only | API/dashboard inspection only; TP/SL logic uses runtime Bollinger helpers from OHLCV, not this store. | No direct runtime TP/SL/backtest effect today; only stored indicator history becomes sparse. |
| Technical Indicator: SMA | Stored analytics snapshot from OHLCV | `analytics_technicalindicator` | `1,500` rows, `300` assets, `2026-04-14` only | API/dashboard inspection only; TP/SL support uses runtime SMA helpers from OHLCV, not this store. | No direct runtime TP/SL/backtest effect today; only stored indicator history becomes sparse. |
| Technical Indicator: EMA | Stored analytics snapshot from OHLCV | `analytics_technicalindicator` | `1,500` rows, `300` assets, `2026-04-14` only | API/dashboard inspection only. | No direct model/backtest effect today; only stored indicator history becomes sparse. |
| Technical Indicator: Momentum Store | Stored analytics snapshot from OHLCV | `analytics_technicalindicator` | `MOM_5D` `2,538`, `MOM_10D` `2,538`, `MOM_20D` `2,538` rows; `300` assets; `2026-04-14` to `2026-04-24` | API/dashboard inspection only; runtime momentum features are recomputed from OHLCV for `Heuristic`, `LightGBM`, and backtests. | No direct runtime model/backtest effect today; only stored indicator history becomes sparse. |
| Technical Indicator: Other Inspection Store | Stored analytics snapshot from OHLCV | `analytics_technicalindicator` | `ADX` `300`, `OBV` `297`, `STOCH` `300`, `FIB_RET` `300` rows; `2026-04-14` only | API/dashboard inspection only; not consumed by `Heuristic`, `LightGBM`, `LSTM`, or backtest runtime today. | No direct model/backtest effect today; only stored indicator history becomes sparse. |
| Signal Events | Calculated from technical indicators and OHLCV patterns | `analytics_signalevent` | `2,855` rows, `294` assets, `2026-04-14` to `2026-04-26` | Used by factor reversal confirmation and signal APIs/dashboard. | Missing rows reduce reversal confirmation strength, but OHLCV-based fallback logic still runs. |
| News Articles | Multi-provider sentiment ingestion | `sentiment_newsarticle` | `44,468` rows, `2026-01-29T19:48:37` to `2026-04-27T00:34:24` | Raw text source for article scoring, `ASSET_7D`, `MARKET_7D`, concept heat, and sentiment dashboards. | Missing articles weaken downstream sentiment/concept signals and can push outputs toward neutral backfill. |
| Sentiment Score: Article | News-derived article scoring | `sentiment_sentimentscore` | `26,482` rows, `255` assets, `2026-02-11` to `2026-04-26` | Upstream input for aggregate sentiment APIs and sentiment QA surfaces; not a direct model feature. | Missing article-level scores weaken downstream asset/market aggregates and sentiment inspection. |
| Sentiment Score: Asset 7D | Rolling asset sentiment aggregation | `sentiment_sentimentscore` | `1,803,900` rows, `300` assets, `2001-07-24` to `2026-04-26` | Direct input for `Heuristic`, `LightGBM`, `LSTM`, runtime backtest candidate generation, and dashboards. | Missing rows fall back to neutral `0.0` and remove a live news-tone signal from affected asset-dates. |
| Sentiment Score: Market 7D | Rolling market sentiment aggregation | `sentiment_sentimentscore` | `6,013` rows, no asset id, `2001-07-24` to `2026-04-26` | Dashboard/market sentiment surface only; not a direct model or backtest input today. | No direct model/backtest effect today; reduces market-sentiment observability only. |
| Concept Heat | Tagged news/sentiment aggregation | `sentiment_conceptheat` | `88` rows, `2026-04-15` to `2026-04-26` | Theme/sector monitoring surface only. | Missing rows affect concept ranking and monitoring only, not core prediction/backtest execution. |
| Fundamental Snapshot: PE | TuShare `daily_basic` | `factors_fundamentalfactorsnapshot.pe` | `1,099,979` non-null rows, `2001-07-24` to `2026-04-22` | Upstream input for `FactorScore.pe_percentile_score`, then indirect `Heuristic` use and direct `LightGBM`/`LSTM` factor features through `FactorScore`. | Missing rows push valuation-derived features toward neutral scores on affected asset-dates. |
| Fundamental Snapshot: PB | TuShare `daily_basic` | `factors_fundamentalfactorsnapshot.pb` | `1,144,671` non-null rows, `2001-07-24` to `2026-04-22` | Upstream input for `FactorScore.pb_percentile_score`, then indirect `Heuristic` use and direct `LightGBM`/`LSTM` factor features through `FactorScore`. | Missing rows push valuation-derived features toward neutral scores on affected asset-dates. |
| Fundamental Snapshot: ROE | TuShare `fina_indicator` | `factors_fundamentalfactorsnapshot.roe` | `1,116,509` non-null rows, `2001-10-12` to `2026-04-22` | Upstream quality/profitability input for factor scoring and API/dashboard surfaces. | Missing rows reduce profitability context and can weaken factor-derived bottom scoring. |
| Fundamental Snapshot: ROE QoQ | TuShare `fina_indicator` | `factors_fundamentalfactorsnapshot.roe_qoq` | `1,096,762` non-null rows, `2002-01-25` to `2026-04-22` | Upstream input for `FactorScore.roe_trend_score`, then indirect `Heuristic` use and direct `LightGBM`/`LSTM` factor features through `FactorScore`. | Missing rows push profitability-trend features toward neutral scores on affected asset-dates. |
| Capital Flow Snapshot Coverage | TuShare moneyflow and margin-detail backfill | `factors_capitalflowsnapshot` | `1,145,611` rows, `300` assets, `2001-07-24` to `2026-04-24` | Precomputed source table for flow-factor construction and capital-flow APIs; models consume the downstream `FactorScore` components, not this table directly. | Coverage before raw-source history begins is structurally sparse and later resolves through neutral flow-score fallbacks. |
| Main Force Net 5D | Rolling sum of large/extra-large moneyflow | `factors_capitalflowsnapshot.main_force_net_5d` | `990,025` non-null rows, `2007-01-04` to `2026-04-24` | Feeds `FactorScore.main_force_flow_score`, then direct `LightGBM`/`LSTM` flow features and indirect `Heuristic` flow context through `FactorScore`. | Missing rows push main-force flow features toward neutral values and reduce capital-flow signal quality. |
| Margin Balance Change 5D | `diff_5d(rzrqye)` | `factors_capitalflowsnapshot.margin_balance_change_5d` | `757,205` non-null rows, `2010-04-08` to `2026-04-24` | Feeds `FactorScore.margin_flow_score`, then direct `LightGBM`/`LSTM` flow features and indirect `Heuristic` flow context through `FactorScore`. | Missing rows push margin-flow features toward neutral values and are the main flow-side coverage gap on recent dates. |
| Asset Money Flow Raw Rows | TuShare stock-level moneyflow | `factors_assetmoneyflowsnapshot` | `990,029` rows, `300` assets, `2007-01-04` to `2026-04-24`; formula inputs non-null: `buy_lg_amount` `990,029`, `buy_elg_amount` `990,029`, `sell_lg_amount` `989,052`, `sell_elg_amount` `969,000`, `net_mf_amount` `967,479` | Upstream raw input for `main_force_net_5d` and flow-factor construction only; not read directly by runtime models/backtests. | Missing raw rows or key formula fields propagate into neutral main-force flow scores downstream. |
| Asset Margin Detail Raw Rows | TuShare margin detail | `factors_assetmargindetailsnapshot` | `768,393` rows, `300` assets, `2010-03-31` to `2026-04-24`; `rzrqye` non-null `768,041` | Upstream raw input for `margin_balance_change_5d` and flow-factor construction only; not read directly by runtime models/backtests. | Missing raw rows or `rzrqye` values propagate into neutral margin-flow scores downstream. |
| Factor Score: PE Percentile | Percentile rank from PE history | `factors_factorscore.pe_percentile_score` | `1,125,072` non-null rows, `2001-07-24` to `2026-04-24`; latest date non-null `284/300` | Direct feature for `LightGBM`/`LSTM`; indirect `Heuristic` input through composite/bottom-probability scores; API/dashboard component surface. | Gaps weaken valuation context and push affected model features toward neutral `0.5`. |
| Factor Score: PB Percentile | Percentile rank from PB history | `factors_factorscore.pb_percentile_score` | `1,171,854` non-null rows, `2001-07-24` to `2026-04-24`; latest date non-null `300/300` | Direct feature for `LightGBM`/`LSTM`; indirect `Heuristic` input through composite/bottom-probability scores; API/dashboard component surface. | Gaps weaken valuation context and push affected model features toward neutral `0.5`. |
| Factor Score: ROE Trend | Normalized ROE QoQ trend | `factors_factorscore.roe_trend_score` | `1,123,069` non-null rows, `2002-01-25` to `2026-04-24`; latest date non-null `300/300` | Direct feature for `LightGBM`/`LSTM`; indirect `Heuristic` input through composite/bottom-probability scores; API/dashboard component surface. | Gaps weaken profitability-trend context and push affected model features toward neutral `0.5`. |
| Factor Score: Main Force Flow | Percentile rank of `main_force_net_5d` | `factors_factorscore.main_force_flow_score` | `1,008,846` non-null rows, `2007-01-04` to `2026-04-24`; latest date non-null `300/300` | Direct feature for `LightGBM`/`LSTM`; indirect `Heuristic` input through composite/bottom-probability scores; API/dashboard component surface. | Gaps weaken moneyflow context and push affected model features toward neutral `0.5`. |
| Factor Score: Margin Flow | Percentile rank of `margin_balance_change_5d` | `factors_factorscore.margin_flow_score` | `766,667` non-null rows, `2010-04-08` to `2026-04-24`; latest date non-null `190/300` | Direct feature for `LightGBM`/`LSTM`; indirect `Heuristic` input through composite/bottom-probability scores; API/dashboard component surface. | This is the main retained-factor coverage gap on recent dates and pushes affected model features toward neutral `0.5`. |
| Factor Score: Technical Reversal | Oversold/reversal scorer | `factors_factorscore.technical_reversal_score` | `1,801,500` non-null rows, `2001-07-24` to `2026-04-24`; latest date non-null `300/300` | Indirect `Heuristic` input through composite/bottom-probability scores and API/dashboard component surface. | Missing rows would weaken bottom-fishing reversal context, but current coverage is complete. |
| Factor Score: Sentiment | Stored factor-layer sentiment score | `factors_factorscore.sentiment_score` | `1,801,500` non-null rows, `2001-07-24` to `2026-04-24`; latest date non-null `300/300` | API/dashboard component surface and composite-score storage; not a direct `LightGBM`/`LSTM` feature today. | Missing rows would only matter if factor-layer sentiment weighting is enabled; current coverage is complete. |
| Factor Score: Composite | Weighted factor composite | `factors_factorscore.composite_score` | `1,801,500` non-null rows, `2001-07-24` to `2026-04-24`; latest date non-null `300/300` | Direct feature for `Heuristic`, `LightGBM`, and `LSTM`; also exposed by factor APIs/dashboard. | Missing rows would remove a shared cross-model factor regime signal; current coverage is complete. |
| Factor Score: Bottom Probability | Clamped bottom-candidate score | `factors_factorscore.bottom_probability_score` | `1,801,500` non-null rows, `2001-07-24` to `2026-04-24`; latest date non-null `300/300` | Direct `Heuristic` feature and factor API/dashboard surface. | Missing rows would weaken the heuristic bottom-candidate signal; current coverage is complete. |
| Prediction Result Rows | Generic prediction storage | `prediction_predictionresult` | `10,804` rows, `300` assets, `2024-12-31` to `2026-04-27` | Generic prediction APIs/views plus stored heuristic-style and `LSTM` outputs; backtests can regenerate heuristic and `LSTM` candidates on demand. | Missing rows reduce historical API coverage but do not block runtime backtest candidate generation. |
| LightGBM Prediction Rows | Stored LightGBM inference output | `prediction_lightgbmprediction` | `446,482` rows, `300` assets, `2016-06-30` to `2026-04-27` | `LightGBM` API/dashboard history; backtests can regenerate `LightGBM` candidates on demand from active artifacts. | Missing stored rows reduce historical API coverage but do not block runtime inference while active artifacts exist. |
| LightGBM Model Artifacts | Per-horizon model artifact registry | `prediction_lightgbmmodelartifact` | `14` total, `3` active; active artifacts trained on `2016-06-01` to `2024-12-31` | Deployment source for `LightGBM` inference and `LightGBM` backtest candidate generation. | Missing active artifact for a horizon blocks that horizon's `LightGBM` runtime path. |
| Model Versions | High-level model registry metadata | `prediction_modelversion` | `23` total; active `LIGHTGBM`, `LSTM`, and `ENSEMBLE` rows | Generic prediction APIs/admin registry and `LSTM`/ensemble metadata surfaces. | Missing active rows break registry labelling and generic prediction metadata, but per-horizon `LightGBM` deployment still depends on `LightGBMModelArtifact`. |
| Feature Importance Snapshots | LightGBM diagnostics | `prediction_featureimportancesnapshot` | `475` rows, `2026-04-15` to `2026-04-26` | Monitoring and pruning diagnostics for `LightGBM` retraining. | Missing snapshots disable historical pruning and fall back to the full engineered feature set. |
| Ensemble Weight Snapshots | Model blending diagnostics | `prediction_ensembleweightsnapshot` | `3` rows, `2024-12-31` to `2026-04-15` | Monitoring/reporting only; not used directly by backtest ranking today. | No direct runtime blocking; reduces retrospective blend tracking only. |
| Backtest Export Sheet | Management command export from `BacktestRun` and optional `BacktestTrade` detail rows | `reports/backtests_89_112_v0_1_9/*.csv` | Current folder state has `3` light-export CSVs: `run_summary.csv`, `run_config_results.csv`, and `model_references.csv`; run summary/config cover existing IDs in `89..112` | Export/reporting only. | Stale or missing files reduce auditability only. |

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

Feature-source coverage and missing-data behavior for the shared `Heuristic`/`LightGBM`/`LSTM` inputs now lives in the Data Metrics Sheet above. This section focuses on label logic, training flow, and active deployment artifacts.

Current implementation note:
- Active artifacts use engineered feature set `v2`, the `core80-v1` pruning rule, and retain `20` features per horizon after pruning.
- `northbound_flow` remains as a neutral `0.5` compatibility placeholder inside the active `LightGBM` artifacts. It is no longer stored on `FactorScore`, exposed in the dashboard DTO, or stored in per-stock capital-flow snapshots.

#### Training process
1. Build feature matrix for training window.
2. Build labels per horizon.
3. Align labels to feature rows.
4. Prune to a compact core feature set from the latest active `FeatureImportanceSnapshot` per horizon.
5. Standardize features with `StandardScaler`.
6. Train LightGBM multiclass booster.
7. Calibrate probabilities (`sigmoid` for sklearn-style estimators when available; `identity` fallback for the native booster).
8. Save artifact files + DB metadata + feature importance snapshots.
9. Refresh ensemble weights based on latest metrics.

Current active artifact snapshot:
- Training window: `2016-06-01` to `2024-12-31`
- Training samples: `548605` for each active horizon artifact
- 3d artifact: `lgb-3d-2024-12-31-core80-v1`, accuracy `0.557191`, features `20`
- 7d artifact: `lgb-7d-2024-12-31-core80-v1`, accuracy `0.490223`, features `20`
- 30d artifact: `lgb-30d-2024-12-31-core80-v1`, accuracy `0.571041`, features `20`
- Current calibration metadata on all active artifacts: `identity`
- Current pruning rule on all active artifacts: `latest_snapshot_cumulative_80_core20_25`.
- The pruning source was the active `regstrong-v1` snapshot family; the 80% cumulative-importance prefix was `11` features for 3d, `11` for 7d, and `6` for 30d, then the dual-constraint floor retained the top `20` features for each horizon.

Registry note:
- Per-horizon deployment state should be read from `LightGBMModelArtifact`.
- `ModelVersion(model_type=LIGHTGBM)` is refreshed during training, but it does not retain one simultaneously active row per horizon.
- Retraining the same cutoff date safely now requires a version tag, which produces version strings such as `lgb-7d-2024-12-31-core80-v1` instead of overwriting earlier artifact families.

#### Hyperparameters (current code defaults)
- objective: `multiclass`
- num_class: `3`
- num_leaves: `15`
- learning_rate: `0.05`
- feature_fraction: `0.6`
- bagging_fraction: `0.8`
- bagging_freq: `5`
- lambda_l1: `1.0`
- lambda_l2: `1.0`
- min_data_in_leaf: `50`
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
- Inputs: the same shared feature extraction pipeline used by `LightGBM`, sourced from the Data Metrics Sheet coverage rows above and converted into rolling sequences (default length `20`).
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
| 3d | `lgb-3d-2024-12-31-core80-v1` | `2026-04-26 09:43:27` | `2016-06-01` to `2024-12-31` | 548605 | 20 | 0.557191 |
| 7d | `lgb-7d-2024-12-31-core80-v1` | `2026-04-26 09:43:40` | `2016-06-01` to `2024-12-31` | 548605 | 20 | 0.490223 |
| 30d | `lgb-30d-2024-12-31-core80-v1` | `2026-04-26 09:43:51` | `2016-06-01` to `2024-12-31` | 548605 | 20 | 0.571041 |

#### Active ensemble model version
- id: `14`
- version: `ensemble-2024-12-31`
- status: `READY`
- trained_at: `2026-04-26 09:43:51+00:00`
- training window: `2016-06-01` to `2024-12-31`
- metrics: `lightgbm_accuracy=0.539485`, `heuristic_accuracy=0.5`, `lstm_accuracy=0.465278`
- implied registry weights from those metrics: `lightgbm=0.3585`, `lstm=0.3092`, `heuristic=0.3323`

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
- The function first resolves the latest OHLCV bar on or before `as_of`. Missing OHLCV or a non-positive close returns null `target_price`, `stop_loss_price`, `risk_reward_ratio`, and `trade_score`, with `suggested=false`.
- Recent price context scans up to the latest `60` OHLCV rows:
  - resistance inputs: 20-row high, 60-row high, upper Bollinger band, and rounded-up `close * 1.01`.
  - support inputs: 20-row low, 60-row low, lower Bollinger band, and SMA support (`SMA60`, with `SMA50` fallback).
- By default, the rounded-up resistance uses the internal price ladder: `0.5` step below `10`, `1` below `50`, `2` below `100`, `5` below `500`, and `10` at `500` or above.
- `target_price` is the nearest valid resistance above current close: `min(resistance_candidates)`.
- `stop_loss_price` is the nearest valid support below current close: `max(support_candidates)`.
- Target and stop fall back independently: if `resistance_candidates` is empty, target uses `close * (1 + upside_fallback)`; if `support_candidates` is empty, stop uses `close * (1 - downside_fallback)`.

#### TP/SL candidate construction

For a valid positive `close`, recent OHLCV rows are loaded with `date <= as_of`, newest first, capped at `60` rows. Missing or invalid high/low, Bollinger, or SMA values are converted to `close`, so they are neutral and are filtered out unless they are strictly above or below the close.

`resistance_candidates` is calculated from values strictly above current close:
- `max(highs_20)`: highest `high` in the most recent `20` rows, defaulting to `close` when unavailable.
- `max(highs_60)`: highest `high` in the most recent `60` rows, defaulting to `close` when unavailable.
- `upper_band`: latest Bollinger upper band on or before `as_of`, defaulting to `close` when unavailable.
- `_round_price_ceiling(close * 1.01)`: `1%` above close rounded up to the internal price ladder. This is enabled by default and can be disabled in backtest experiments with `trade_decision_policy.include_near_round_target=false`.

`support_candidates` is calculated from values strictly below current close:
- `min(lows_20)`: lowest `low` in the most recent `20` rows, defaulting to `close` when unavailable.
- `min(lows_60)`: lowest `low` in the most recent `60` rows, defaulting to `close` when unavailable.
- `lower_band`: latest Bollinger lower band on or before `as_of`, defaulting to `close` when unavailable.
- `moving_average_support`: latest `SMA60`, falling back to latest `SMA50`, then to `close` when unavailable.

Fallback target/stop assumptions:
- 3d: `+3% / -2%`
- 7d: `+6% / -4%`
- 30d: `+12% / -8%`
- Other horizons default to `+5% / -3%`.
- Prices are quantized to `0.0001`; ratios and scores are quantized to `0.000001`.
- Minimum reward/risk floor: `0.5%` of close.

Backtest-only TP/SL policy overrides can be supplied in `BacktestRun.parameters.trade_decision_policy` without changing the default prediction task behavior:
- `include_near_round_target`: boolean, default `true`; when `false`, omits the rounded `close * 1.01` resistance candidate.
- `min_target_return_pct`: optional decimal ratio such as `0.05`; enforces `target_price >= close * (1 + min_target_return_pct)` after structural/fallback target selection.
- `min_stop_distance_pct`: optional decimal ratio such as `0.03`; enforces a minimum stop distance by using `stop_loss_price <= close * (1 - min_stop_distance_pct)` when structural support is closer than the requested distance.

#### Trade decision formulas
- `reward = max(target_price - close, close * 0.005)`
- `risk = max(close - stop_loss_price, close * 0.005)`
- `risk_reward_ratio = reward / risk` when `risk > 0`
- `down_risk = max(0.05, 1 - up_probability)`
- `trade_score = (up_probability * reward) / (down_risk * risk)` when `risk > 0`
- Suggested setup requires:
  - predicted label is `UP`
  - `risk_reward_ratio >= 1.5`
  - `trade_score >= 1.0`

Runtime prediction payloads from heuristic, LightGBM, and LSTM candidates carry the same trade-decision fields when available. During backtest entry, `_backfill_prediction_trade_decision` fills missing `trade_score`, `target_price`, `stop_loss_price`, and `suggested` if the payload still has a prediction source, horizon, up probability, and predicted label.

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
5. Long runs are processed in chunks of `20` trading days (`BACKTEST_CHUNK_TRADING_DAYS`) and resume through `BacktestRun.report.runtime_state` until complete.
6. Final report writes equity curve, benchmark curve, and strategy metadata.

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
| Fee Rate | `fee_rate` | Transaction fee rate. Backend default is `0.001`. |
| Slippage (bps) | `slippage_bps` | Per-trade slippage in basis points. Backend default is `5`. |
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
  - Uses the raw daily close from the price map, not the slippage-adjusted fill price.
  - Checks `close <= stop_loss_price` first and exits with `exit_reason=STOP_LOSS`.
  - If stop-loss does not trigger, checks `close >= target_price` and exits with `exit_reason=TARGET_PRICE`.
- If neither early-exit condition is met and the scheduled exit date has not arrived, the position remains open.
- If sell close is missing or non-positive for the current date, the position remains open.
- If neither TP/SL condition triggers on the scheduled exit date, the position exits with `exit_reason=SCHEDULED`.

### Cost and position mechanics

- Buy fill price: `close + (close * slippage_bps / 10000)`.
- Sell fill price: `close - (close * slippage_bps / 10000)`.
- Buy fee: `quantity * buy_price * fee_rate`.
- Sell fee: `quantity * sell_price * fee_rate`.
- Realized PnL: `sell_amount - sell_fee - buy_amount - buy_fee`.
- Deployable capital per entry cycle: `min(cash, initial_capital * capital_fraction_per_entry)`.
- Per-candidate allocation: `(deployable_capital / selected_candidate_count) / (1 + fee_rate)`.
- `trade_score` mode enforces `max_positions` as the concurrent-position cap. In `top_n` mode, candidate count is controlled by `top_n`; the backend `_max_positions` fallback is effectively unlimited unless trade-score mode is active, while the UI still submits `max_positions` for consistency.

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

### Report and export files

The `export_backtest_runs` command defaults to a light export:
- `run_summary.csv`
- `run_config_results.csv`
- `model_references.csv`

Optional exports:
- `--detail-export` also writes `trades.csv`, `macro_context_monthly.csv`, and comparison CSVs.
- `--include-active-lightgbm-artifacts` also writes `lightgbm_model_artifacts.csv` with active artifact pruning metadata.

`trades.csv` includes signal payload fields such as `trade_score`, `target_price`, `stop_loss_price`, `suggested`, `model_version`, `model_version_id`, and `model_artifact_id` when detail export is enabled.

### Metric definitions

- `total_return = (final_value - initial_capital) / initial_capital`.
- `annualized_return = (1 + total_return) ** (365 / calendar_days) - 1` when total return is above `-100%`.
- `max_drawdown` is the maximum peak-to-trough decline over the strategy equity curve.
- `sharpe_ratio` is annualized from daily equity-curve returns using `252` trading days and population standard deviation.
- `total_trades` counts closed positions, not raw buy/sell rows.
- `winning_trades` counts closed positions with positive realized PnL.
- `win_rate = winning_trades / total_trades`, or `0` when there are no closed positions.

### Default values currently used by UI

- End Date defaults to the most recent Friday based on real-time local date.
- Start Date defaults to one year before that end date (`end_date - 364 days`).
- Other defaults include:
  - `prediction_source=all` in the UI, submitted as separate runs for `heuristic`, `lightgbm`, and `lstm`
  - `horizon_days=7`
  - `top_n=8`
  - `top_n_metric=up_prob_7d`
  - `up_threshold=0.45`
  - `candidate_mode=top_n`
  - `trade_score_scope=independent`
  - `trade_score_threshold=1.0`
  - `max_positions=5`
  - `use_macro_context=true`
  - `enable_stop_target_exit=true`
  - `entry_weekdays=[2, 4]` in UI ISO weekday form, submitted as `TUE,THU`
  - `holding_period_days=14`
  - `capital_fraction_per_entry=0.2`
  - `initial_capital=200000.00`

Backend fallbacks differ from UI defaults when parameters are omitted:
- `holding_period_days=1`
- no weekday filter unless `entry_weekdays` is supplied
- `capital_fraction_per_entry=1 / number_of_entry_weekdays` when weekdays are supplied, otherwise `1.0`
- `fee_rate=0.001`
- `slippage_bps=5`
- `use_macro_context=false`
- `enable_stop_target_exit=false`

### Documentation audit notes

Duplicated or overlapping areas:
- The Data Metrics Sheet is now the single live coverage index for model inputs and stored analytics. Later model sections should focus on formulas, training flow, and registry state instead of re-listing feature coverage.
- Macro fields still appear both in the Data Metrics Sheet and the later macro semantics table; this is intentional because the sheet owns live coverage while the later table owns upstream field semantics.
- LightGBM feature counts can be confused between full engineered/source inventories and active deployment artifacts. The active `core80-v1` artifacts deploy `20` retained features per horizon.
- README-level status summaries overlap this guide. Treat `TechnicalGuide.md` as the detailed source of truth and README as an overview.

Previously missing areas now covered in this refresh:
- TP/SL resistance/support selection and trade-decision formulas.
- TP/SL backtest trigger order and close-price-only threshold checks.
- Missing/non-positive price behavior during exits.
- Fee, slippage, position allocation, and realized-PnL formulas.
- Backtest chunking/resume behavior.
- Export modes and trade signal fields.
- Backtest metric definitions.

Outdated information corrected in this refresh:
- The v0.1.9 export folder description now reflects the current light-export CSV set instead of assuming detail CSVs are always present.
- UI defaults now match the current Backtest Workbench state.
- LightGBM feature-count wording now separates full inventories from pruned active artifact counts.
