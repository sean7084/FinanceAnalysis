<!--
  GENERATED FILE - DO NOT EDIT BY HAND.
  Regenerate: python manage.py export_documentation_facts
  Source of truth: row counts and date ranges queried from the configured database.
-->

# Data Coverage Metrics

_Generated: 2026-09-13T13:43:16Z_

Table-level coverage for every concrete model in `apps/`. `Assets` is the distinct count of the `asset` foreign key where the table has one. Prose about what each metric *means* and what a gap *implies* lives in `TECHNICAL_GUIDE.md`; this sheet only states the measured facts.

## Table coverage

| Table | Model | Rows | Assets | Date column | Earliest | Latest |
| --- | --- | --- | --- | --- | --- | --- |
| `analytics_alertevent` | `analytics.AlertEvent` | 0 | &mdash; | created_at | &mdash; | &mdash; |
| `analytics_alertrule` | `analytics.AlertRule` | 0 | &mdash; | created_at | &mdash; | &mdash; |
| `analytics_screenertemplate` | `analytics.ScreenerTemplate` | 0 | &mdash; | created_at | &mdash; | &mdash; |
| `analytics_signalevent` | `analytics.SignalEvent` | 3,615,159 | 959 | timestamp | 2010-01-04 | 2026-06-03 |
| `analytics_technicalindicator` | `analytics.TechnicalIndicator` | 85,289,363 | 961 | timestamp | 2010-01-04 | 2026-06-03 |
| `backtest_backtestrun` | `backtest.BacktestRun` | 443 | &mdash; | created_at | 2026-04-16 | 2026-06-29 |
| `backtest_backtesttrade` | `backtest.BacktestTrade` | 240,874 | 595 | trade_date | 2023-01-03 | 2026-05-18 |
| `developer_developerapikey` | `developer.DeveloperAPIKey` | 124 | &mdash; | created_at | 2026-04-15 | 2026-06-24 |
| `factors_assetmargindetailsnapshot` | `factors.AssetMarginDetailSnapshot` | 2,298,992 | 951 | date | 2010-03-31 | 2026-06-29 |
| `factors_assetmoneyflowsnapshot` | `factors.AssetMoneyFlowSnapshot` | 3,077,547 | 961 | date | 2010-01-04 | 2026-06-30 |
| `factors_capitalflowsnapshot` | `factors.CapitalFlowSnapshot` | 3,084,013 | 961 | date | 2010-01-04 | 2026-06-30 |
| `factors_factorscore` | `factors.FactorScore` | 1,303,719 | 961 | date | 2010-01-04 | 2026-06-03 |
| `factors_fundamentalfactorsnapshot` | `factors.FundamentalFactorSnapshot` | 3,071,147 | 959 | date | 2010-01-04 | 2026-04-30 |
| `macro_eventimpactstat` | `macro.EventImpactStat` | 0 | &mdash; | created_at | &mdash; | &mdash; |
| `macro_macrosnapshot` | `macro.MacroSnapshot` | 198 | &mdash; | date | 2010-01-01 | 2026-06-01 |
| `macro_marketcontext` | `macro.MarketContext` | 205 | &mdash; | created_at | 2026-04-15 | 2026-06-02 |
| `markets_asset` | `markets.Asset` | 990 | &mdash; | &mdash; | &mdash; | &mdash; |
| `markets_assetsuspension` | `markets.AssetSuspension` | 143,843 | 893 | trade_date | 2001-01-02 | 2026-05-07 |
| `markets_benchmarkindexdaily` | `markets.BenchmarkIndexDaily` | 10,434 | &mdash; | trade_date | 2005-01-04 | 2026-06-30 |
| `markets_exchangetradingcalendar` | `markets.ExchangeTradingCalendar` | 18,602 | &mdash; | trade_date | 2001-01-01 | 2026-06-30 |
| `markets_indexmembership` | `markets.IndexMembership` | 229,400 | 989 | trade_date | 2010-01-04 | 2026-06-30 |
| `markets_market` | `markets.Market` | 5 | &mdash; | &mdash; | &mdash; | &mdash; |
| `markets_ohlcv` | `markets.OHLCV` | 3,330,683 | 983 | date | 2008-07-07 | 2026-06-30 |
| `markets_pointintimebenchmarkdaily` | `markets.PointInTimeBenchmarkDaily` | 4,385 | &mdash; | trade_date | 2008-07-07 | 2026-06-30 |
| `prediction_ensembleweightsnapshot` | `prediction.EnsembleWeightSnapshot` | 4 | &mdash; | date | 2024-12-31 | 2026-05-23 |
| `prediction_featureimportancesnapshot` | `prediction.FeatureImportanceSnapshot` | 720 | &mdash; | created_at | 2026-04-15 | 2026-05-23 |
| `prediction_lightgbmmodelartifact` | `prediction.LightGBMModelArtifact` | 23 | &mdash; | created_at | 2026-04-15 | 2026-05-23 |
| `prediction_lightgbmprediction` | `prediction.LightGBMPrediction` | 40,345 | 611 | date | 2025-01-14 | 2026-06-30 |
| `prediction_modelversion` | `prediction.ModelVersion` | 37 | &mdash; | created_at | 2026-04-15 | 2026-05-23 |
| `prediction_predictionresult` | `prediction.PredictionResult` | 51,864 | 612 | date | 2025-01-14 | 2026-06-30 |
| `sentiment_conceptheat` | `sentiment.ConceptHeat` | 502 | &mdash; | date | 2026-04-15 | 2026-06-30 |
| `sentiment_newsarticle` | `sentiment.NewsArticle` | 173,917 | &mdash; | published_at | 2025-06-09 | 2026-07-01 |
| `sentiment_sentimentscore` | `sentiment.SentimentScore` | 3,369,100 | 962 | date | 2010-01-04 | 2026-06-30 |
| `users_apiusage` | `users.APIUsage` | 32,992 | &mdash; | timestamp | 2026-04-15 | 2026-07-01 |
| `users_subscription` | `users.Subscription` | 1 | &mdash; | created_at | 2026-04-15 | 2026-04-15 |
| `users_userprofile` | `users.UserProfile` | 5 | &mdash; | created_at | 2026-04-15 | 2026-05-22 |

## Breakdowns


### `analytics_signalevent` by `signal_type`

| signal_type | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `BB_BREAKOUT_DOWN` | 161,973 | 4.5% | 2010-01-04 | 2026-04-30 |
| `BB_BREAKOUT_UP` | 202,387 | 5.6% | 2010-01-04 | 2026-04-30 |
| `BB_RSI_OVERBOUGHT` | 114,313 | 3.2% | 2010-01-04 | 2026-04-30 |
| `BB_RSI_OVERSOLD` | 88,837 | 2.5% | 2010-01-04 | 2026-04-30 |
| `BB_SQUEEZE` | 149,920 | 4.1% | 2010-01-21 | 2026-04-30 |
| `DEATH_CROSS` | 91,890 | 2.5% | 2010-01-04 | 2026-04-30 |
| `GOLDEN_CROSS` | 92,042 | 2.5% | 2010-01-04 | 2026-04-30 |
| `HIGH_RS_SCORE` | 240,372 | 6.6% | 2010-01-04 | 2026-06-03 |
| `MA_BEAR_ALIGN` | 643,179 | 17.8% | 2010-01-04 | 2026-04-30 |
| `MA_BULL_ALIGN` | 593,550 | 16.4% | 2010-01-04 | 2026-04-30 |
| `MOMENTUM_DOWN_5D` | 437,277 | 12.1% | 2010-01-04 | 2026-04-30 |
| `MOMENTUM_UP_5D` | 484,147 | 13.4% | 2010-01-04 | 2026-04-30 |
| `OVERSOLD_COMBINATION` | 25,136 | 0.7% | 2010-01-04 | 2026-04-30 |
| `VOLUME_PRICE_DIVERGENCE` | 98,312 | 2.7% | 2010-01-04 | 2026-04-30 |
| `VOLUME_SPIKE` | 191,824 | 5.3% | 2010-01-04 | 2026-04-30 |

### `analytics_technicalindicator` by `indicator_type`

| indicator_type | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `ADX` | 2,998,872 | 3.5% | 2010-01-04 | 2026-04-30 |
| `BBANDS` | 3,063,098 | 3.6% | 2010-01-04 | 2026-04-30 |
| `EMA` | 17,800,157 | 20.9% | 2010-01-04 | 2026-05-27 |
| `FIB_RET` | 2,968,148 | 3.5% | 2010-01-04 | 2026-04-30 |
| `MACD` | 2,967,740 | 3.5% | 2010-01-04 | 2026-04-30 |
| `MOM_10D` | 2,981,509 | 3.5% | 2010-01-04 | 2026-04-30 |
| `MOM_20D` | 2,897,748 | 3.4% | 2010-01-04 | 2026-06-03 |
| `MOM_5D` | 3,025,952 | 3.5% | 2010-01-04 | 2026-04-30 |
| `OBV` | 3,073,050 | 3.6% | 2010-01-04 | 2026-06-03 |
| `REALIZED_VOLATILITY_5D` | 3,076,415 | 3.6% | 2010-01-04 | 2026-05-22 |
| `RELATIVE_VOLUME_20D` | 3,070,468 | 3.6% | 2010-01-04 | 2026-05-22 |
| `RELATIVE_VOLUME_5D` | 3,076,836 | 3.6% | 2010-01-04 | 2026-05-22 |
| `RETURN_10D` | 3,074,295 | 3.6% | 2010-01-04 | 2026-05-22 |
| `RETURN_3D` | 3,077,257 | 3.6% | 2010-01-04 | 2026-05-22 |
| `RETURN_5D` | 3,076,415 | 3.6% | 2010-01-04 | 2026-05-22 |
| `RSI` | 3,026,420 | 3.5% | 2010-01-04 | 2026-04-30 |
| `RS_SCORE` | 1,209,502 | 1.4% | 2010-01-04 | 2026-06-03 |
| `SMA` | 17,800,157 | 20.9% | 2010-01-04 | 2026-05-27 |
| `STOCH` | 3,025,324 | 3.5% | 2010-01-04 | 2026-04-30 |

### `backtest_backtestrun` by `status`

| status | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `COMPLETED` | 431 | 97.3% | 2026-04-16 | 2026-06-29 |
| `FAILED` | 9 | 2.0% | 2026-04-16 | 2026-04-29 |
| `RUNNING` | 3 | 0.7% | 2026-05-24 | 2026-05-26 |

### `markets_asset` by `listing_status`

| listing_status | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `A` | 950 | 96.0% | &mdash; | &mdash; |
| `D` | 39 | 3.9% | &mdash; | &mdash; |
| `L` | 1 | 0.1% | &mdash; | &mdash; |

### `markets_indexmembership` by `index_code`

| index_code | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `000300.SH` | 218,400 | 95.2% | 2010-01-04 | 2026-06-30 |
| `000510.CSI` | 11,000 | 4.8% | 2024-09-23 | 2026-06-30 |

### `prediction_modelversion` by `model_type`

| model_type | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `ENSEMBLE` | 8 | 21.6% | 2026-04-15 | 2026-05-23 |
| `LIGHTGBM` | 24 | 64.9% | 2026-04-15 | 2026-05-23 |
| `LSTM` | 5 | 13.5% | 2026-04-18 | 2026-05-23 |

### `sentiment_sentimentscore` by `score_type`

| score_type | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `ARTICLE` | 105,396 | 3.1% | 2025-10-16 | 2026-06-30 |
| `ASSET_7D` | 3,259,693 | 96.8% | 2010-01-04 | 2026-06-30 |
| `MARKET_7D` | 4,011 | 0.1% | 2010-01-04 | 2026-06-25 |

## Field-level non-null coverage

Restricted to the tables whose individual columns feed model features. A low non-null count with a short span usually means an upstream provider floor or blackout rather than a bug; see `docs/how-to/runbook-provider-blackout.md`.

| Table | Field | Non-null rows | Min | Max | Non-null range |
| --- | --- | --- | --- | --- | --- |
| `factors_fundamentalfactorsnapshot` | `pe` | 2,852,283 | 0.5077 | 30364.0753 | 2010-01-04 &rarr; 2026-04-30 |
| `factors_fundamentalfactorsnapshot` | `pe_ttm` | 2,770,156 | 0.4818 | 661439.6060 | 2010-01-04 &rarr; 2026-04-30 |
| `factors_fundamentalfactorsnapshot` | `pb` | 3,062,933 | 0.1644 | 19746.0230 | 2010-01-04 &rarr; 2026-04-30 |
| `factors_fundamentalfactorsnapshot` | `total_share` | 3,071,147 | 4274.5652 | 35640625.7089 | 2010-01-04 &rarr; 2026-04-30 |
| `factors_fundamentalfactorsnapshot` | `float_share` | 3,071,147 | 1069.0000 | 31924421.0777 | 2010-01-04 &rarr; 2026-04-30 |
| `factors_fundamentalfactorsnapshot` | `free_share` | 3,070,633 | 567.1490 | 5564157.9186 | 2010-01-04 &rarr; 2026-04-30 |
| `factors_fundamentalfactorsnapshot` | `total_mv` | 3,071,147 | 46769.7871 | 326737047.7800 | 2010-01-04 &rarr; 2026-04-30 |
| `factors_fundamentalfactorsnapshot` | `circ_mv` | 3,071,147 | 14860.0000 | 326737047.7800 | 2010-01-04 &rarr; 2026-04-30 |
| `factors_fundamentalfactorsnapshot` | `roe` | 3,062,820 | -0.999900 | 0.999900 | 2010-01-04 &rarr; 2026-04-30 |
| `factors_fundamentalfactorsnapshot` | `roe_qoq` | 3,061,192 | -1.855400 | 1.864900 | 2010-01-04 &rarr; 2026-04-30 |
| `factors_capitalflowsnapshot` | `main_force_net_5d` | 3,077,525 | -2160582.7500 | 1195457.1800 | 2010-01-04 &rarr; 2026-06-30 |
| `factors_capitalflowsnapshot` | `margin_balance_change_5d` | 2,257,168 | -15711445144.0000 | 8996340592.0000 | 2010-04-08 &rarr; 2026-06-29 |
| `factors_factorscore` | `pe_percentile_score` | 1,257,993 | 0.000000 | 0.998155 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `pe_ttm_percentile_score` | 1,228,719 | 0.000000 | 0.998077 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `pb_percentile_score` | 1,303,558 | 0.000000 | 0.998239 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `roe_trend_score` | 1,303,574 | 0.000000 | 1.000000 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `main_force_flow_score` | 1,303,404 | 0.001761 | 1.000000 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `margin_flow_score` | 1,125,044 | 0.001761 | 1.000000 | 2010-04-08 &rarr; 2026-06-02 |
| `factors_factorscore` | `technical_reversal_score` | 1,303,719 | 0.000000 | 1.000000 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `sentiment_score` | 1,303,719 | 0.500000 | 0.500000 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `fundamental_score` | 1,303,719 | 0.000000 | 0.999117 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `capital_flow_score` | 1,303,719 | 0.001761 | 1.000000 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `technical_score` | 1,303,719 | 0.000000 | 1.000000 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `financial_weight` | 1,303,719 | 0.4000 | 0.4000 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `flow_weight` | 1,303,719 | 0.3000 | 0.3000 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `technical_weight` | 1,303,719 | 0.3000 | 0.3000 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `sentiment_weight` | 1,303,719 | 0.0000 | 0.0000 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `composite_score` | 1,303,719 | 0.033913 | 0.971831 | 2010-01-04 &rarr; 2026-06-03 |
| `factors_factorscore` | `bottom_probability_score` | 1,303,719 | 0.033913 | 0.971831 | 2010-01-04 &rarr; 2026-06-03 |
| `macro_macrosnapshot` | `dxy` | 186 | 93.5300 | 134.0950 | 2011-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cny_usd` | 198 | 0.1363 | 0.1656 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn6m_yield` | 198 | 1.0065 | 4.1864 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn1y_yield` | 198 | 1.0693 | 4.2251 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn3y_yield` | 197 | 1.1694 | 4.4538 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn5y_yield` | 197 | 1.3976 | 4.5068 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn7y_yield` | 197 | 1.5315 | 4.6111 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn10y_yield` | 197 | 1.6077 | 4.6018 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn30y_yield` | 197 | 1.8270 | 5.1435 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `pmi_manufacturing` | 198 | 35.700 | 55.800 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `pmi_non_manufacturing` | 197 | 29.600 | 59.200 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cpi_yoy` | 197 | -0.800 | 6.500 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `ppi_yoy` | 197 | -5.950 | 13.500 | 2010-01-01 &rarr; 2026-06-01 |
