<!--
  GENERATED FILE - DO NOT EDIT BY HAND.
  Regenerate: python manage.py export_documentation_facts
  Source of truth: row counts and date ranges queried from the configured database.
-->

# Data Coverage Metrics

_Generated: 2026-10-20T11:38:12Z_

Table-level coverage for every concrete model in `apps/`. `Assets` is the distinct count of the `asset` foreign key where the table has one. Prose about what each metric *means* and what a gap *implies* lives in `TECHNICAL_GUIDE.md`; this sheet only states the measured facts.

## Table coverage

| Table | Model | Rows | Assets | Date column | Earliest | Latest |
| --- | --- | --- | --- | --- | --- | --- |
| `analytics_alertevent` | `analytics.AlertEvent` | 0 | &mdash; | created_at | &mdash; | &mdash; |
| `analytics_alertrule` | `analytics.AlertRule` | 0 | &mdash; | created_at | &mdash; | &mdash; |
| `analytics_screenertemplate` | `analytics.ScreenerTemplate` | 0 | &mdash; | created_at | &mdash; | &mdash; |
| `analytics_signalevent` | `analytics.SignalEvent` | 3,615,717 | 1,087 | timestamp | 2010-01-04 | 2026-10-19 |
| `analytics_technicalindicator` | `analytics.TechnicalIndicator` | 166,323,331 | 1,884 | timestamp | 2010-01-04 | 2026-10-19 |
| `backtest_backtestrun` | `backtest.BacktestRun` | 443 | &mdash; | created_at | 2026-04-16 | 2026-06-29 |
| `backtest_backtesttrade` | `backtest.BacktestTrade` | 240,874 | 595 | trade_date | 2023-01-03 | 2026-05-18 |
| `factors_assetmargindetailsnapshot` | `factors.AssetMarginDetailSnapshot` | 3,725,629 | 1,681 | date | 2010-03-31 | 2026-10-16 |
| `factors_assetmoneyflowsnapshot` | `factors.AssetMoneyFlowSnapshot` | 5,927,385 | 1,884 | date | 2010-01-04 | 2026-10-16 |
| `factors_capitalflowsnapshot` | `factors.CapitalFlowSnapshot` | 5,930,427 | 1,884 | date | 2010-01-04 | 2026-10-16 |
| `factors_factorscore` | `factors.FactorScore` | 1,305,219 | 1,134 | date | 2010-01-04 | 2026-10-16 |
| `factors_fundamentalfactorsnapshot` | `factors.FundamentalFactorSnapshot` | 5,925,014 | 1,884 | date | 2010-01-04 | 2026-09-23 |
| `macro_eventimpactstat` | `macro.EventImpactStat` | 0 | &mdash; | created_at | &mdash; | &mdash; |
| `macro_macrosnapshot` | `macro.MacroSnapshot` | 200 | &mdash; | date | 2010-01-01 | 2026-10-01 |
| `macro_marketcontext` | `macro.MarketContext` | 207 | &mdash; | created_at | 2026-04-15 | 2026-10-03 |
| `markets_asset` | `markets.Asset` | 1,910 | &mdash; | &mdash; | &mdash; | &mdash; |
| `markets_assetsuspension` | `markets.AssetSuspension` | 143,847 | 894 | trade_date | 2001-01-02 | 2026-10-13 |
| `markets_benchmarkindexdaily` | `markets.BenchmarkIndexDaily` | 4,074 | &mdash; | trade_date | 2010-01-04 | 2026-10-16 |
| `markets_exchangetradingcalendar` | `markets.ExchangeTradingCalendar` | 18,748 | &mdash; | trade_date | 2001-01-01 | 2026-10-18 |
| `markets_indexmembership` | `markets.IndexMembership` | 101,000 | 1,676 | trade_date | 2009-12-31 | 2026-09-30 |
| `markets_market` | `markets.Market` | 5 | &mdash; | &mdash; | &mdash; | &mdash; |
| `markets_ohlcv` | `markets.OHLCV` | 6,295,651 | 1,884 | date | 2008-07-02 | 2026-10-16 |
| `markets_pointintimebenchmarkdaily` | `markets.PointInTimeBenchmarkDaily` | 4,074 | &mdash; | trade_date | 2010-01-04 | 2026-10-16 |
| `prediction_ensembleweightsnapshot` | `prediction.EnsembleWeightSnapshot` | 6 | &mdash; | date | 2020-01-01 | 2026-09-21 |
| `prediction_featureimportancesnapshot` | `prediction.FeatureImportanceSnapshot` | 954 | &mdash; | created_at | 2026-04-15 | 2026-09-25 |
| `prediction_lightgbmmodelartifact` | `prediction.LightGBMModelArtifact` | 29 | &mdash; | created_at | 2026-04-15 | 2026-09-25 |
| `prediction_lightgbmprediction` | `prediction.LightGBMPrediction` | 40,863 | 611 | date | 2025-01-14 | 2026-10-20 |
| `prediction_modelversion` | `prediction.ModelVersion` | 47 | &mdash; | created_at | 2026-04-15 | 2026-09-25 |
| `prediction_predictionresult` | `prediction.PredictionResult` | 51,912 | 612 | date | 2025-01-14 | 2026-10-20 |
| `sentiment_conceptheat` | `sentiment.ConceptHeat` | 560 | &mdash; | date | 2026-04-15 | 2026-10-18 |
| `sentiment_newsarticle` | `sentiment.NewsArticle` | 174,895 | &mdash; | published_at | 2025-06-09 | 2026-10-20 |
| `sentiment_sentimentscore` | `sentiment.SentimentScore` | 6,517,000 | 1,910 | date | 2010-01-04 | 2026-10-20 |

## Breakdowns


### `analytics_signalevent` by `signal_type`

| signal_type | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `BB_BREAKOUT_DOWN` | 162,042 | 4.5% | 2010-01-04 | 2026-10-16 |
| `BB_BREAKOUT_UP` | 202,393 | 5.6% | 2010-01-04 | 2026-10-16 |
| `BB_RSI_OVERBOUGHT` | 114,314 | 3.2% | 2010-01-04 | 2026-10-16 |
| `BB_RSI_OVERSOLD` | 88,908 | 2.5% | 2010-01-04 | 2026-10-16 |
| `BB_SQUEEZE` | 149,949 | 4.1% | 2010-01-21 | 2026-10-16 |
| `DEATH_CROSS` | 91,890 | 2.5% | 2010-01-04 | 2026-04-30 |
| `GOLDEN_CROSS` | 92,042 | 2.5% | 2010-01-04 | 2026-04-30 |
| `HIGH_RS_SCORE` | 240,466 | 6.7% | 2010-01-04 | 2026-10-19 |
| `MA_BEAR_ALIGN` | 643,179 | 17.8% | 2010-01-04 | 2026-04-30 |
| `MA_BULL_ALIGN` | 593,550 | 16.4% | 2010-01-04 | 2026-04-30 |
| `MOMENTUM_DOWN_5D` | 437,495 | 12.1% | 2010-01-04 | 2026-10-16 |
| `MOMENTUM_UP_5D` | 484,161 | 13.4% | 2010-01-04 | 2026-10-16 |
| `OVERSOLD_COMBINATION` | 25,161 | 0.7% | 2010-01-04 | 2026-10-16 |
| `VOLUME_PRICE_DIVERGENCE` | 98,323 | 2.7% | 2010-01-04 | 2026-10-16 |
| `VOLUME_SPIKE` | 191,844 | 5.3% | 2010-01-04 | 2026-10-16 |

### `analytics_technicalindicator` by `indicator_type`

| indicator_type | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `ADX` | 5,903,440 | 3.5% | 2010-01-04 | 2026-10-16 |
| `BBANDS` | 5,910,030 | 3.6% | 2010-01-04 | 2026-10-16 |
| `EMA` | 35,232,976 | 21.2% | 2010-01-04 | 2026-10-16 |
| `FIB_RET` | 5,924,207 | 3.6% | 2010-01-04 | 2026-09-23 |
| `MACD` | 5,898,476 | 3.5% | 2010-01-04 | 2026-10-16 |
| `MOM_10D` | 5,917,372 | 3.6% | 2010-01-04 | 2026-10-16 |
| `MOM_20D` | 5,909,183 | 3.6% | 2010-01-04 | 2026-10-16 |
| `MOM_5D` | 5,921,445 | 3.6% | 2010-01-04 | 2026-10-16 |
| `OBV` | 5,924,706 | 3.6% | 2010-01-04 | 2026-10-16 |
| `REALIZED_VOLATILITY_5D` | 5,920,971 | 3.6% | 2010-01-04 | 2026-09-23 |
| `RELATIVE_VOLUME_20D` | 5,909,531 | 3.6% | 2010-01-04 | 2026-09-23 |
| `RELATIVE_VOLUME_5D` | 5,921,781 | 3.6% | 2010-01-04 | 2026-09-23 |
| `RETURN_10D` | 5,916,899 | 3.6% | 2010-01-04 | 2026-09-23 |
| `RETURN_3D` | 5,922,591 | 3.6% | 2010-01-04 | 2026-09-23 |
| `RETURN_5D` | 5,920,971 | 3.6% | 2010-01-04 | 2026-09-23 |
| `RSI` | 5,914,129 | 3.6% | 2010-01-04 | 2026-10-16 |
| `RS_SCORE` | 1,209,975 | 0.7% | 2010-01-04 | 2026-10-19 |
| `SMA` | 35,232,976 | 21.2% | 2010-01-04 | 2026-10-16 |
| `STOCH` | 5,911,672 | 3.6% | 2010-01-04 | 2026-10-16 |

### `backtest_backtestrun` by `status`

| status | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `COMPLETED` | 431 | 97.3% | 2026-04-16 | 2026-06-29 |
| `FAILED` | 9 | 2.0% | 2026-04-16 | 2026-04-29 |
| `RUNNING` | 3 | 0.7% | 2026-05-24 | 2026-05-26 |

### `markets_asset` by `listing_status`

| listing_status | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `A` | 1,784 | 93.4% | &mdash; | &mdash; |
| `D` | 125 | 6.5% | &mdash; | &mdash; |
| `L` | 1 | 0.1% | &mdash; | &mdash; |

### `markets_indexmembership` by `index_code`

| index_code | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `000905.SH` | 101,000 | 100.0% | 2009-12-31 | 2026-09-30 |

### `prediction_modelversion` by `model_type`

| model_type | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `ENSEMBLE` | 10 | 21.3% | 2026-04-15 | 2026-09-25 |
| `LIGHTGBM` | 30 | 63.8% | 2026-04-15 | 2026-09-25 |
| `LSTM` | 7 | 14.9% | 2026-04-18 | 2026-09-25 |

### `sentiment_sentimentscore` by `score_type`

| score_type | Rows | Share | Earliest | Latest |
| --- | --- | --- | --- | --- |
| `ARTICLE` | 166,780 | 2.6% | 2025-06-09 | 2026-10-18 |
| `ASSET_7D` | 6,346,208 | 97.4% | 2010-01-04 | 2026-10-20 |
| `MARKET_7D` | 4,012 | 0.1% | 2010-01-04 | 2026-06-25 |

## Field-level non-null coverage

Restricted to the tables whose individual columns feed model features. A low non-null count with a short span usually means an upstream provider floor or blackout rather than a bug; see `docs/how-to/runbook-provider-blackout.md`.

| Table | Field | Non-null rows | Min | Max | Non-null range |
| --- | --- | --- | --- | --- | --- |
| `factors_fundamentalfactorsnapshot` | `pe` | 5,202,935 | 0.3864 | 30364.0753 | 2010-01-04 &rarr; 2026-09-23 |
| `factors_fundamentalfactorsnapshot` | `pe_ttm` | 5,000,486 | 0.3389 | 661439.6060 | 2010-01-04 &rarr; 2026-09-23 |
| `factors_fundamentalfactorsnapshot` | `pb` | 5,759,751 | 0.0226 | 45723.5682 | 2010-01-04 &rarr; 2026-09-23 |
| `factors_fundamentalfactorsnapshot` | `total_share` | 5,788,096 | 4000.0000 | 35640625.7089 | 2010-01-04 &rarr; 2026-09-23 |
| `factors_fundamentalfactorsnapshot` | `float_share` | 5,788,096 | 605.5189 | 31924421.0777 | 2010-01-04 &rarr; 2026-09-23 |
| `factors_fundamentalfactorsnapshot` | `free_share` | 5,786,897 | 33.5694 | 5564157.9186 | 2010-01-04 &rarr; 2026-09-23 |
| `factors_fundamentalfactorsnapshot` | `total_mv` | 5,788,096 | 36211.1282 | 326737047.7800 | 2010-01-04 &rarr; 2026-09-23 |
| `factors_fundamentalfactorsnapshot` | `circ_mv` | 5,788,096 | 14860.0000 | 326737047.7800 | 2010-01-04 &rarr; 2026-09-23 |
| `factors_fundamentalfactorsnapshot` | `roe` | 5,810,439 | -0.999900 | 0.999900 | 2010-01-04 &rarr; 2026-09-23 |
| `factors_fundamentalfactorsnapshot` | `roe_qoq` | 5,803,220 | -1.855400 | 1.892900 | 2010-01-04 &rarr; 2026-09-23 |
| `factors_capitalflowsnapshot` | `main_force_net_5d` | 5,921,473 | -2160582.7500 | 1195457.1800 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_capitalflowsnapshot` | `margin_balance_change_5d` | 3,660,250 | -15711445144.0000 | 8996340592.0000 | 2010-04-08 &rarr; 2026-10-16 |
| `factors_factorscore` | `pe_percentile_score` | 1,259,310 | 0.000000 | 0.998155 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `pe_ttm_percentile_score` | 1,230,009 | 0.000000 | 0.998077 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `pb_percentile_score` | 1,304,986 | 0.000000 | 0.998239 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `roe_trend_score` | 1,305,050 | 0.000000 | 1.000000 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `main_force_flow_score` | 1,304,904 | 0.001761 | 1.000000 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `margin_flow_score` | 1,126,532 | 0.001761 | 1.000000 | 2010-04-08 &rarr; 2026-10-16 |
| `factors_factorscore` | `technical_reversal_score` | 1,305,219 | 0.000000 | 1.000000 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `sentiment_score` | 1,305,219 | 0.500000 | 0.500000 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `fundamental_score` | 1,305,219 | 0.000000 | 1.000000 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `capital_flow_score` | 1,305,219 | 0.001761 | 1.000000 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `technical_score` | 1,305,219 | 0.000000 | 1.000000 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `financial_weight` | 1,305,219 | 0.4000 | 0.4000 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `flow_weight` | 1,305,219 | 0.3000 | 0.3000 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `technical_weight` | 1,305,219 | 0.3000 | 0.3000 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `sentiment_weight` | 1,305,219 | 0.0000 | 0.0000 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `composite_score` | 1,305,219 | 0.016227 | 0.971831 | 2010-01-04 &rarr; 2026-10-16 |
| `factors_factorscore` | `bottom_probability_score` | 1,305,219 | 0.016227 | 0.971831 | 2010-01-04 &rarr; 2026-10-16 |
| `macro_macrosnapshot` | `dxy` | 188 | 93.5300 | 134.0950 | 2011-01-01 &rarr; 2026-10-01 |
| `macro_macrosnapshot` | `cny_usd` | 200 | 0.1363 | 0.1656 | 2010-01-01 &rarr; 2026-10-01 |
| `macro_macrosnapshot` | `cn6m_yield` | 198 | 1.0065 | 4.1864 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn1y_yield` | 198 | 1.0693 | 4.2251 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn3y_yield` | 197 | 1.1694 | 4.4538 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn5y_yield` | 197 | 1.3976 | 4.5068 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn7y_yield` | 197 | 1.5315 | 4.6111 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn10y_yield` | 197 | 1.6077 | 4.6018 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `cn30y_yield` | 197 | 1.8270 | 5.1435 | 2010-01-01 &rarr; 2026-06-01 |
| `macro_macrosnapshot` | `pmi_manufacturing` | 200 | 35.700 | 55.800 | 2010-01-01 &rarr; 2026-10-01 |
| `macro_macrosnapshot` | `pmi_non_manufacturing` | 199 | 29.600 | 59.200 | 2010-01-01 &rarr; 2026-10-01 |
| `macro_macrosnapshot` | `cpi_yoy` | 199 | -0.800 | 6.500 | 2010-01-01 &rarr; 2026-10-01 |
| `macro_macrosnapshot` | `ppi_yoy` | 199 | -5.950 | 13.500 | 2010-01-01 &rarr; 2026-10-01 |
