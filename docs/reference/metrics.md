<!--
  GENERATED FILE - DO NOT EDIT BY HAND.
  Regenerate: python manage.py export_documentation_facts
  Source of truth: row counts and date ranges queried from the configured database.
-->

# Data Coverage Metrics

_Generated: 2026-11-09T11:36:46Z_

Table-level coverage for every concrete model in `apps/`. `Assets` is the distinct count of the `asset` foreign key where the table has one. Prose about what each metric *means* and what a gap *implies* lives in `TECHNICAL_GUIDE.md`; this sheet only states the measured facts.

## Table coverage

| Table | Model | Rows | Assets | Date column | Earliest | Latest |
| --- | --- | --- | --- | --- | --- | --- |
| `analytics_alertevent` | `analytics.AlertEvent` | 0 | &mdash; | created_at | &mdash; | &mdash; |
| `analytics_alertrule` | `analytics.AlertRule` | 1 | 1 | created_at | 2026-10-24 | 2026-10-24 |
| `analytics_screenertemplate` | `analytics.ScreenerTemplate` | 0 | &mdash; | created_at | &mdash; | &mdash; |
| `analytics_signalevent` | `analytics.SignalEvent` | 7,088,530 | 1,886 | timestamp | 2010-01-03 | 2026-11-02 |
| `analytics_technicalindicator` | `analytics.TechnicalIndicator` | 174,015,330 | 1,886 | timestamp | 2010-01-03 | 2026-11-02 |
| `backtest_backtestrun` | `backtest.BacktestRun` | 443 | &mdash; | created_at | 2026-04-16 | 2026-06-29 |
| `backtest_backtesttrade` | `backtest.BacktestTrade` | 240,874 | 595 | trade_date | 2023-01-03 | 2026-05-18 |
| `catchup_catchuprun` | `catchup.CatchUpRun` | 196 | &mdash; | &mdash; | &mdash; | &mdash; |
| `etf_rotation_etfdaily` | `etf_rotation.ETFDaily` | 2,656,074 | &mdash; | date | 2016-01-04 | 2026-11-06 |
| `etf_rotation_etffeaturesnapshot` | `etf_rotation.ETFFeatureSnapshot` | 2,656,074 | &mdash; | date | 2016-01-04 | 2026-11-06 |
| `etf_rotation_etfindexmapping` | `etf_rotation.ETFIndexMapping` | 1,966 | &mdash; | &mdash; | &mdash; | &mdash; |
| `etf_rotation_etfrotationbacktest` | `etf_rotation.ETFRotationBacktest` | 356 | &mdash; | created_at | 2026-10-20 | 2026-11-06 |
| `etf_rotation_etfrotationposition` | `etf_rotation.ETFRotationPosition` | 11 | &mdash; | created_at | 2026-10-25 | 2026-11-07 |
| `etf_rotation_etfrotationsignal` | `etf_rotation.ETFRotationSignal` | 8 | &mdash; | date | 2018-12-28 | 2026-11-06 |
| `etf_rotation_etfrotationsignalrun` | `etf_rotation.ETFRotationSignalRun` | 9 | &mdash; | date | 2026-10-23 | 2026-11-06 |
| `etf_rotation_etfsharedaily` | `etf_rotation.ETFShareDaily` | 854,170 | &mdash; | date | 2015-10-08 | 2026-10-23 |
| `etf_rotation_etfuniverse` | `etf_rotation.ETFUniverse` | 3,009 | &mdash; | created_at | 2026-10-20 | 2026-10-23 |
| `etf_rotation_indexvaluationdaily` | `etf_rotation.IndexValuationDaily` | 17,168 | &mdash; | date | 2015-01-05 | 2026-10-23 |
| `etf_rotation_indexweightsnapshot` | `etf_rotation.IndexWeightSnapshot` | 2,971,264 | &mdash; | trade_date | 2016-01-04 | 2026-09-30 |
| `factors_assetmargindetailsnapshot` | `factors.AssetMarginDetailSnapshot` | 3,739,031 | 1,685 | date | 2010-03-31 | 2026-11-05 |
| `factors_assetmoneyflowsnapshot` | `factors.AssetMoneyFlowSnapshot` | 5,942,930 | 1,885 | date | 2010-01-04 | 2026-11-06 |
| `factors_capitalflowsnapshot` | `factors.CapitalFlowSnapshot` | 5,945,970 | 1,885 | date | 2010-01-04 | 2026-11-06 |
| `factors_factorscore` | `factors.FactorScore` | 2,043,000 | 1,678 | date | 2010-01-04 | 2026-11-03 |
| `factors_fundamentalfactorsnapshot` | `factors.FundamentalFactorSnapshot` | 5,926,320 | 1,884 | date | 2010-01-04 | 2026-11-06 |
| `macro_eventimpactstat` | `macro.EventImpactStat` | 0 | &mdash; | created_at | &mdash; | &mdash; |
| `macro_macrosnapshot` | `macro.MacroSnapshot` | 202 | &mdash; | date | 2010-01-01 | 2026-11-01 |
| `macro_marketcontext` | `macro.MarketContext` | 208 | &mdash; | created_at | 2026-04-15 | 2026-11-02 |
| `markets_asset` | `markets.Asset` | 1,912 | &mdash; | &mdash; | &mdash; | &mdash; |
| `markets_assetsuspension` | `markets.AssetSuspension` | 144,052 | 909 | trade_date | 2001-01-02 | 2026-11-06 |
| `markets_benchmarkindexdaily` | `markets.BenchmarkIndexDaily` | 8,178 | &mdash; | trade_date | 2010-01-04 | 2026-11-06 |
| `markets_exchangetradingcalendar` | `markets.ExchangeTradingCalendar` | 18,872 | &mdash; | trade_date | 2001-01-01 | 2026-11-08 |
| `markets_indexmembership` | `markets.IndexMembership` | 101,500 | 1,678 | trade_date | 2009-12-31 | 2026-10-30 |
| `markets_market` | `markets.Market` | 5 | &mdash; | &mdash; | &mdash; | &mdash; |
| `markets_ohlcv` | `markets.OHLCV` | 6,544,372 | 1,886 | date | 2008-07-02 | 2026-11-06 |
| `markets_pointintimebenchmarkdaily` | `markets.PointInTimeBenchmarkDaily` | 4,089 | &mdash; | trade_date | 2010-01-04 | 2026-11-06 |
| `prediction_ensembleweightsnapshot` | `prediction.EnsembleWeightSnapshot` | 9 | &mdash; | date | 2020-01-01 | 2026-11-08 |
| `prediction_featureimportancesnapshot` | `prediction.FeatureImportanceSnapshot` | 1,305 | &mdash; | created_at | 2026-04-15 | 2026-11-09 |
| `prediction_lightgbmmodelartifact` | `prediction.LightGBMModelArtifact` | 38 | &mdash; | created_at | 2026-04-15 | 2026-11-09 |
| `prediction_lightgbmprediction` | `prediction.LightGBMPrediction` | 63,311 | 897 | date | 2025-01-14 | 2026-11-07 |
| `prediction_modelversion` | `prediction.ModelVersion` | 107 | &mdash; | created_at | 2026-04-15 | 2026-11-09 |
| `prediction_predictionresult` | `prediction.PredictionResult` | 76,199 | 898 | date | 2025-01-14 | 2026-11-07 |
| `sentiment_conceptheat` | `sentiment.ConceptHeat` | 654 | &mdash; | date | 2026-04-15 | 2026-11-07 |
| `sentiment_newsarticle` | `sentiment.NewsArticle` | 176,098 | &mdash; | published_at | 2025-06-09 | 2026-11-07 |
| `sentiment_sentimentscore` | `sentiment.SentimentScore` | 6,702,927 | 1,912 | date | 2010-01-04 | 2026-11-07 |

## Breakdowns


### `analytics_signalevent` by `signal_type`

| signal_type | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `BB_BREAKOUT_DOWN` | 325,041 | 4.6% | 2010-01-04 | 2026-11-02 |
| `BB_BREAKOUT_UP` | 397,085 | 5.6% | 2010-01-03 | 2026-11-02 |
| `BB_RSI_OVERBOUGHT` | 216,826 | 3.1% | 2010-01-03 | 2026-11-02 |
| `BB_RSI_OVERSOLD` | 172,775 | 2.4% | 2010-01-12 | 2026-11-02 |
| `BB_SQUEEZE` | 250,999 | 3.5% | 2010-01-05 | 2026-11-01 |
| `DEATH_CROSS` | 180,502 | 2.5% | 2010-01-03 | 2026-11-02 |
| `GOLDEN_CROSS` | 180,035 | 2.5% | 2010-01-03 | 2026-11-02 |
| `HIGH_RS_SCORE` | 367,539 | 5.2% | 2010-01-03 | 2026-11-02 |
| `MA_BEAR_ALIGN` | 1,270,570 | 17.9% | 2010-01-03 | 2026-11-02 |
| `MA_BULL_ALIGN` | 1,148,076 | 16.2% | 2010-01-03 | 2026-11-02 |
| `MOMENTUM_DOWN_5D` | 923,034 | 13.0% | 2010-01-03 | 2026-11-02 |
| `MOMENTUM_UP_5D` | 984,008 | 13.9% | 2010-01-03 | 2026-11-02 |
| `OVERSOLD_COMBINATION` | 52,895 | 0.7% | 2010-01-20 | 2026-11-01 |
| `VOLUME_PRICE_DIVERGENCE` | 209,726 | 3.0% | 2010-01-03 | 2026-11-02 |
| `VOLUME_SPIKE` | 409,419 | 5.8% | 2010-01-03 | 2026-11-02 |

### `analytics_technicalindicator` by `indicator_type`

| indicator_type | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `ADX` | 6,155,231 | 3.5% | 2010-01-03 | 2026-11-02 |
| `BBANDS` | 6,161,837 | 3.5% | 2010-01-03 | 2026-11-02 |
| `EMA` | 36,744,423 | 21.1% | 2010-01-03 | 2026-11-02 |
| `FIB_RET` | 6,176,549 | 3.5% | 2010-01-03 | 2026-11-02 |
| `MACD` | 6,150,257 | 3.5% | 2010-01-03 | 2026-11-02 |
| `MOM_10D` | 6,169,223 | 3.5% | 2010-01-03 | 2026-11-02 |
| `MOM_20D` | 6,161,014 | 3.5% | 2010-01-03 | 2026-11-02 |
| `MOM_5D` | 6,173,305 | 3.5% | 2010-01-03 | 2026-11-02 |
| `OBV` | 6,176,549 | 3.5% | 2010-01-03 | 2026-11-02 |
| `REALIZED_VOLATILITY_5D` | 6,173,305 | 3.5% | 2010-01-03 | 2026-11-02 |
| `RELATIVE_VOLUME_20D` | 6,161,837 | 3.5% | 2010-01-03 | 2026-11-02 |
| `RELATIVE_VOLUME_5D` | 6,174,117 | 3.5% | 2010-01-03 | 2026-11-02 |
| `RETURN_10D` | 6,169,223 | 3.5% | 2010-01-03 | 2026-11-02 |
| `RETURN_3D` | 6,174,929 | 3.5% | 2010-01-03 | 2026-11-02 |
| `RETURN_5D` | 6,173,305 | 3.5% | 2010-01-03 | 2026-11-02 |
| `RSI` | 6,165,947 | 3.5% | 2010-01-03 | 2026-11-02 |
| `RS_SCORE` | 1,846,373 | 1.1% | 2010-01-03 | 2026-11-02 |
| `SMA` | 36,744,423 | 21.1% | 2010-01-03 | 2026-11-02 |
| `STOCH` | 6,163,483 | 3.5% | 2010-01-03 | 2026-11-02 |

### `backtest_backtestrun` by `status`

| status | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `COMPLETED` | 431 | 97.3% | 2026-04-16 | 2026-06-29 |
| `FAILED` | 9 | 2.0% | 2026-04-16 | 2026-04-29 |
| `RUNNING` | 3 | 0.7% | 2026-05-24 | 2026-05-26 |

### `markets_asset` by `listing_status`

| listing_status | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `A` | 1,784 | 93.3% | &mdash; | &mdash; |
| `D` | 127 | 6.6% | &mdash; | &mdash; |
| `L` | 1 | 0.1% | &mdash; | &mdash; |

### `markets_indexmembership` by `index_code`

| index_code | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `000905.SH` | 101,500 | 100.0% | 2009-12-31 | 2026-10-30 |

### `prediction_modelversion` by `model_type`

| model_type | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `ENSEMBLE` | 13 | 12.1% | 2026-04-15 | 2026-11-09 |
| `ETF_ENSEMBLE` | 3 | 2.8% | 2026-10-20 | 2026-11-06 |
| `ETF_LIGHTGBM` | 5 | 4.7% | 2026-10-20 | 2026-11-06 |
| `ETF_LSTM` | 39 | 36.4% | 2026-10-20 | 2026-11-06 |
| `LIGHTGBM` | 39 | 36.4% | 2026-04-15 | 2026-11-09 |
| `LSTM` | 8 | 7.5% | 2026-04-18 | 2026-11-05 |

### `sentiment_sentimentscore` by `score_type`

| score_type | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `ARTICLE` | 168,964 | 2.5% | 2025-06-09 | 2026-11-07 |
| `ASSET_7D` | 6,529,853 | 97.4% | 2010-01-04 | 2026-11-07 |
| `MARKET_7D` | 4,110 | 0.1% | 2010-01-04 | 2026-11-06 |

## Field-level non-null coverage

Restricted to the tables whose individual columns feed model features. A low non-null count with a short span usually means an upstream provider floor or blackout rather than a bug; see `docs/how-to/runbook-provider-blackout.md`.

| Table | Field | Non-null rows | Min | Max | Non-null range |
| --- | --- | --- | --- | --- | --- |
| `factors_fundamentalfactorsnapshot` | `pe` | 5,204,093 | 0.3864 | 30364.0753 | 2010-01-04 &rarr; 2026-11-06 |
| `factors_fundamentalfactorsnapshot` | `pe_ttm` | 5,001,574 | 0.3389 | 661439.6060 | 2010-01-04 &rarr; 2026-11-06 |
| `factors_fundamentalfactorsnapshot` | `pb` | 5,761,057 | 0.0226 | 45723.5682 | 2010-01-04 &rarr; 2026-11-06 |
| `factors_fundamentalfactorsnapshot` | `total_share` | 5,789,402 | 4000.0000 | 35640625.7089 | 2010-01-04 &rarr; 2026-11-06 |
| `factors_fundamentalfactorsnapshot` | `float_share` | 5,789,402 | 605.5189 | 31924421.0777 | 2010-01-04 &rarr; 2026-11-06 |
| `factors_fundamentalfactorsnapshot` | `free_share` | 5,788,203 | 33.5694 | 5564157.9186 | 2010-01-04 &rarr; 2026-11-06 |
| `factors_fundamentalfactorsnapshot` | `total_mv` | 5,789,402 | 36211.1282 | 326737047.7800 | 2010-01-04 &rarr; 2026-11-06 |
| `factors_fundamentalfactorsnapshot` | `circ_mv` | 5,789,402 | 14860.0000 | 326737047.7800 | 2010-01-04 &rarr; 2026-11-06 |
| `factors_fundamentalfactorsnapshot` | `roe` | 5,811,744 | -0.999900 | 0.999900 | 2010-01-04 &rarr; 2026-11-06 |
| `factors_fundamentalfactorsnapshot` | `roe_qoq` | 5,804,524 | -1.855400 | 1.892900 | 2010-01-04 &rarr; 2026-11-06 |
| `factors_capitalflowsnapshot` | `main_force_net_5d` | 5,937,016 | -2160582.7500 | 1195457.1800 | 2010-01-04 &rarr; 2026-11-06 |
| `factors_capitalflowsnapshot` | `margin_balance_change_5d` | 3,673,629 | -15711445144.0000 | 8996340592.0000 | 2010-04-08 &rarr; 2026-11-05 |
| `factors_factorscore` | `pe_percentile_score` | 1,879,096 | 0.000000 | 0.997925 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `pe_ttm_percentile_score` | 1,811,864 | 0.000000 | 0.997895 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `pb_percentile_score` | 1,993,238 | 0.000000 | 0.997992 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `roe_trend_score` | 2,017,154 | 0.000000 | 1.000000 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `main_force_flow_score` | 2,041,597 | 0.002000 | 1.000000 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `margin_flow_score` | 1,302,787 | 0.002008 | 1.000000 | 2011-12-12 &rarr; 2026-11-03 |
| `factors_factorscore` | `technical_reversal_score` | 2,043,000 | 0.000000 | 1.000000 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `sentiment_score` | 2,043,000 | 0.500000 | 0.500000 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `fundamental_score` | 2,043,000 | 0.000000 | 1.000000 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `capital_flow_score` | 2,043,000 | 0.002000 | 1.000000 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `technical_score` | 2,043,000 | 0.000000 | 1.000000 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `financial_weight` | 2,043,000 | 0.4000 | 0.4000 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `flow_weight` | 2,043,000 | 0.3000 | 0.3000 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `technical_weight` | 2,043,000 | 0.3000 | 0.3000 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `sentiment_weight` | 2,043,000 | 0.0000 | 0.0000 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `composite_score` | 2,043,000 | 0.002405 | 0.985900 | 2010-01-04 &rarr; 2026-11-03 |
| `factors_factorscore` | `bottom_probability_score` | 2,043,000 | 0.002405 | 0.985900 | 2010-01-04 &rarr; 2026-11-03 |
| `macro_macrosnapshot` | `dxy` | 190 | 93.5300 | 134.0950 | 2011-01-01 &rarr; 2026-11-01 |
| `macro_macrosnapshot` | `cny_usd` | 202 | 0.1363 | 0.1656 | 2010-01-01 &rarr; 2026-11-01 |
| `macro_macrosnapshot` | `cn6m_yield` | 198 | 1.0065 | 4.1864 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn1y_yield` | 198 | 1.0693 | 4.2251 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn3y_yield` | 197 | 1.1694 | 4.4538 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn5y_yield` | 197 | 1.3976 | 4.5068 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn7y_yield` | 197 | 1.5315 | 4.6111 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn10y_yield` | 197 | 1.6077 | 4.6018 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn30y_yield` | 197 | 1.8270 | 5.1435 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `pmi_manufacturing` | 199 | 35.700 | 55.800 | 2010-01-01 &rarr; 2026-11-01 |
| `macro_macrosnapshot` | `pmi_non_manufacturing` | 197 | 29.600 | 59.200 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cpi_yoy` | 200 | -0.800 | 6.500 | 2010-01-01 &rarr; 2026-11-01 |
| `macro_macrosnapshot` | `ppi_yoy` | 200 | -5.950 | 13.500 | 2010-01-01 &rarr; 2026-11-01 |
