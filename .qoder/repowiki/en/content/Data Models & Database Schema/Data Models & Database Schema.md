# Data Models & Database Schema

<cite>
**Referenced Files in This Document**
- [apps/markets/models.py](file://apps/markets/models.py)
- [apps/analytics/models.py](file://apps/analytics/models.py)
- [apps/factors/models.py](file://apps/factors/models.py)
- [apps/prediction/models.py](file://apps/prediction/models.py)
- [apps/prediction/models_lightgbm.py](file://apps/prediction/models_lightgbm.py)
- [apps/backtest/models.py](file://apps/backtest/models.py)
- [apps/macro/models.py](file://apps/macro/models.py)
- [apps/sentiment/models.py](file://apps/sentiment/models.py)
- [apps/developer/models.py](file://apps/developer/models.py)
- [apps/users/models.py](file://apps/users/models.py)
</cite>

## Table of Contents
1. Introduction
2. Project Structure
3. Core Components
4. Architecture Overview
5. Detailed Component Analysis
6. Dependency Analysis
7. Performance Considerations
8. Troubleshooting Guide
9. Conclusion
10. Appendices

## Introduction
This document provides a comprehensive data model and database schema reference for the FinanceAnalysis platform. It covers entity relationships across all applications, including Asset, OHLCV, TechnicalIndicator, FactorScore, PredictionResult, BacktestRun, and related models. It documents primary/foreign keys, indexes, constraints, validation rules, point-in-time integrity patterns (notably IndexMembership and asset lifecycle), data access patterns, caching strategies, performance considerations for large-scale financial datasets, data lifecycle policies (historical floors, staleness detection, archival), and security/access control mechanisms implemented at the model level.

## Project Structure
The platform is organized into Django apps that each own their domain models:
- markets: core market entities (Asset, OHLCV, calendars, index membership, benchmarks)
- analytics: technical indicators, alerts, signal events
- factors: fundamental snapshots, money flow, margin detail, capital flow, factor scores
- prediction: model versions, predictions, LightGBM artifacts and predictions, ensemble weights, feature importance
- backtest: run orchestration and trade ledger
- macro: macro snapshots, market context, event impact statistics
- sentiment: news articles, sentiment scores, concept heat
- developer: API key management and changelog
- users: user profiles, subscriptions, API usage tracking

```mermaid
graph TB
subgraph "Markets"
M_Asset["Asset"]
M_OHLCV["OHLCV"]
M_Calendar["ExchangeTradingCalendar"]
M_IndexMembership["IndexMembership"]
M_Benchmark["BenchmarkIndexDaily"]
M_PITBench["PointInTimeBenchmarkDaily"]
end
subgraph "Analytics"
A_TI["TechnicalIndicator"]
A_Signal["SignalEvent"]
end
subgraph "Factors"
F_Fund["FundamentalFactorSnapshot"]
F_Money["AssetMoneyFlowSnapshot"]
F_Margin["AssetMarginDetailSnapshot"]
F_Capital["CapitalFlowSnapshot"]
F_Score["FactorScore"]
end
subgraph "Prediction"
P_Model["ModelVersion / LightGBMModelArtifact"]
P_Res["PredictionResult / LightGBMPrediction"]
P_Ensemble["EnsembleWeightSnapshot"]
P_FeatImp["FeatureImportanceSnapshot"]
end
subgraph "Backtest"
B_Run["BacktestRun"]
B_Trade["BacktestTrade"]
end
subgraph "Macro"
M_Macro["MacroSnapshot"]
M_Context["MarketContext"]
M_Impact["EventImpactStat"]
end
subgraph "Sentiment"
S_Article["NewsArticle"]
S_Score["SentimentScore"]
S_Heat["ConceptHeat"]
end
subgraph "Developer"
D_Key["DeveloperAPIKey"]
end
subgraph "Users"
U_Profile["UserProfile"]
U_Sub["Subscription"]
U_Usage["APIUsage"]
end
M_Asset --> M_OHLCV
M_Asset --> A_TI
M_Asset --> F_Fund
M_Asset --> F_Money
M_Asset --> F_Margin
M_Asset --> F_Capital
M_Asset --> F_Score
M_Asset --> P_Res
M_Asset --> B_Trade
M_IndexMembership --> M_Asset
P_Model --> P_Res
P_Model --> P_Ensemble
P_Model --> P_FeatImp
B_Run --> B_Trade
S_Article --> S_Score
```

**Diagram sources**
- [apps/markets/models.py:19-226](file://apps/markets/models.py#L19-L226)
- [apps/analytics/models.py:8-255](file://apps/analytics/models.py#L8-L255)
- [apps/factors/models.py:7-176](file://apps/factors/models.py#L7-L176)
- [apps/prediction/models.py:7-107](file://apps/prediction/models.py#L7-L107)
- [apps/prediction/models_lightgbm.py:7-137](file://apps/prediction/models_lightgbm.py#L7-L137)
- [apps/backtest/models.py:24-168](file://apps/backtest/models.py#L24-L168)
- [apps/macro/models.py:5-77](file://apps/macro/models.py#L5-L77)
- [apps/sentiment/models.py:35-125](file://apps/sentiment/models.py#L35-L125)
- [apps/developer/models.py:10-156](file://apps/developer/models.py#L10-L156)
- [apps/users/models.py:13-186](file://apps/users/models.py#L13-L186)

**Section sources**
- [apps/markets/models.py:1-226](file://apps/markets/models.py#L1-L226)
- [apps/analytics/models.py:1-255](file://apps/analytics/models.py#L1-L255)
- [apps/factors/models.py:1-176](file://apps/factors/models.py#L1-L176)
- [apps/prediction/models.py:1-107](file://apps/prediction/models.py#L1-L107)
- [apps/prediction/models_lightgbm.py:1-137](file://apps/prediction/models_lightgbm.py#L1-L137)
- [apps/backtest/models.py:1-168](file://apps/backtest/models.py#L1-L168)
- [apps/macro/models.py:1-77](file://apps/macro/models.py#L1-L77)
- [apps/sentiment/models.py:1-125](file://apps/sentiment/models.py#L1-L125)
- [apps/developer/models.py:1-156](file://apps/developer/models.py#L1-L156)
- [apps/users/models.py:1-186](file://apps/users/models.py#L1-L186)

## Core Components
This section summarizes the most critical entities and their roles:
- Asset: canonical identity for tradable instruments with listing/delisting dates and current index memberships.
- OHLCV: daily price/volume series per asset.
- TechnicalIndicator: time-stamped indicator values per asset.
- FactorScore: composite and component scores used for screening and modeling.
- PredictionResult/LightGBMPrediction: forward-looking probabilities and trade signals by horizon.
- BacktestRun/BacktestTrade: strategy execution state and trade ledger.
- MacroSnapshot/MarketContext: macroeconomic context and phase tagging.
- SentimentScore/NewsArticle: text-derived sentiment features and raw inputs.
- DeveloperAPIKey: secure API authentication primitives.
- UserProfile/Subscription/APIUsage: user account and usage telemetry.

**Section sources**
- [apps/markets/models.py:19-226](file://apps/markets/models.py#L19-L226)
- [apps/analytics/models.py:8-255](file://apps/analytics/models.py#L8-L255)
- [apps/factors/models.py:7-176](file://apps/factors/models.py#L7-L176)
- [apps/prediction/models.py:7-107](file://apps/prediction/models.py#L7-L107)
- [apps/prediction/models_lightgbm.py:7-137](file://apps/prediction/models_lightgbm.py#L7-L137)
- [apps/backtest/models.py:24-168](file://apps/backtest/models.py#L24-L168)
- [apps/macro/models.py:5-77](file://apps/macro/models.py#L5-L77)
- [apps/sentiment/models.py:35-125](file://apps/sentiment/models.py#L35-L125)
- [apps/developer/models.py:10-156](file://apps/developer/models.py#L10-L156)
- [apps/users/models.py:13-186](file://apps/users/models.py#L13-L186)

## Architecture Overview
The data architecture separates raw market data, derived features, predictive outputs, and execution results while preserving point-in-time correctness through explicit membership and benchmark histories.

```mermaid
graph TB
A["Asset"]
O["OHLCV"]
T["TechnicalIndicator"]
F["FactorScore"]
P["PredictionResult / LightGBMPrediction"]
B["BacktestRun / BacktestTrade"]
I["IndexMembership"]
R["BenchmarkIndexDaily / PointInTimeBenchmarkDaily"]
M["MacroSnapshot / MarketContext"]
S["SentimentScore / NewsArticle"]
A --> O
A --> T
A --> F
A --> P
A --> B
A --> I
I --> R
M --> P
S --> P
P --> B
```

**Diagram sources**
- [apps/markets/models.py:117-226](file://apps/markets/models.py#L117-L226)
- [apps/analytics/models.py:8-255](file://apps/analytics/models.py#L8-L255)
- [apps/factors/models.py:7-176](file://apps/factors/models.py#L7-L176)
- [apps/prediction/models.py:7-107](file://apps/prediction/models.py#L7-L107)
- [apps/prediction/models_lightgbm.py:7-137](file://apps/prediction/models_lightgbm.py#L7-L137)
- [apps/backtest/models.py:24-168](file://apps/backtest/models.py#L24-L168)
- [apps/macro/models.py:5-77](file://apps/macro/models.py#L5-L77)
- [apps/sentiment/models.py:35-125](file://apps/sentiment/models.py#L35-L125)

## Detailed Component Analysis

### Markets Domain
- Asset: identifies instruments; unique on (market, symbol); includes listing/delisting dates and current index membership tags to support lifecycle-aware queries.
- ExchangeTradingCalendar: official trading days per exchange; indexed and unique on (exchange_code, trade_date).
- AssetSuspension: daily suspension flags per asset; indexed on (asset, trade_date) and (trade_date, is_full_day); unique on (asset, trade_date).
- IndexMembership: historical membership snapshots enabling point-in-time universe construction; indexed on (index_code, trade_date) and (asset, index_code); unique on (asset, index_code, trade_date).
- BenchmarkIndexDaily: official index daily series; indexed on (index_code, trade_date); unique on (index_code, trade_date).
- PointInTimeBenchmarkDaily: internal union benchmark for backtests; indexed on (benchmark_code, trade_date); unique on (benchmark_code, trade_date).
- OHLCV: daily price/volume per asset; indexed on (asset, date); unique on (asset, date).

```mermaid
erDiagram
MARKET {
string code PK
string name
}
ASSET {
int id PK
int market_id FK
string symbol
string ts_code UK
string name
char listing_status
date list_date
date delist_date
json membership_tags
}
EXCHANGE_TRADING_CALENDAR {
string exchange_code
date trade_date
boolean is_open
string source
}
ASSET_SUSPENSION {
int id PK
int asset_id FK
date trade_date
char suspend_type
char suspend_timing
boolean is_full_day
string source
}
INDEX_MEMBERSHIP {
int id PK
int asset_id FK
string index_code
string index_name
date trade_date
decimal weight
string source
}
BENCHMARK_INDEX_DAILY {
string index_code
string index_name
date trade_date
decimal open
decimal high
decimal low
decimal close
string source
}
POINT_IN_TIME_BENCHMARK_DAILY {
string benchmark_code
string benchmark_name
date trade_date
decimal daily_return
decimal nav
int constituent_count
int overlap_count
string weighting_method
json metadata
datetime created_at
datetime updated_at
}
OHLCV {
int id PK
int asset_id FK
date date
decimal open
decimal high
decimal low
decimal close
decimal adj_close
bigint volume
decimal amount
}
MARKET ||--o{ ASSET : "has many"
ASSET ||--o{ ASSET_SUSPENSION : "has many"
ASSET ||--o{ INDEX_MEMBERSHIP : "has many"
ASSET ||--o{ OHLCV : "has many"
```

**Diagram sources**
- [apps/markets/models.py:4-226](file://apps/markets/models.py#L4-L226)

**Section sources**
- [apps/markets/models.py:4-226](file://apps/markets/models.py#L4-L226)

### Analytics Domain
- TechnicalIndicator: per-asset indicator values with parameters; unique on (asset, timestamp, indicator_type, parameters); indexed on (asset, timestamp, indicator_type).
- SignalEvent: discrete technical signals; unique on (asset, timestamp, signal_type); indexed on (asset, timestamp, signal_type) and (signal_type, timestamp).
- ScreenerTemplate/AlertRule/AlertEvent: user-defined screens and alerting; AlertEvent tracks status transitions and channels.

```mermaid
classDiagram
class TechnicalIndicator {
+asset
+timestamp
+indicator_type
+value
+parameters
}
class SignalEvent {
+asset
+signal_type
+timestamp
+description
+metadata
}
class ScreenerTemplate {
+owner
+name
+screener_type
+config
+is_public
}
class AlertRule {
+owner
+asset
+condition_type
+indicator_type
+threshold
+custom_condition
+channels
+cooldown_minutes
+is_active
}
class AlertEvent {
+alert_rule
+asset
+status
+trigger_value
+message
+metadata
+dispatched_channels
+notified_at
}
TechnicalIndicator --> Asset : "FK"
SignalEvent --> Asset : "FK"
AlertRule --> User : "FK"
AlertRule --> Asset : "FK"
AlertEvent --> AlertRule : "FK"
AlertEvent --> Asset : "FK"
```

**Diagram sources**
- [apps/analytics/models.py:8-255](file://apps/analytics/models.py#L8-L255)

**Section sources**
- [apps/analytics/models.py:8-255](file://apps/analytics/models.py#L8-L255)

### Factors Domain
- FundamentalFactorSnapshot, AssetMoneyFlowSnapshot, AssetMarginDetailSnapshot, CapitalFlowSnapshot: daily snapshots keyed by (asset, date) with indexes for efficient slicing.
- FactorScore: composite and component scores; unique on (asset, date, mode); optimized indexes for ranking queries.

```mermaid
classDiagram
class FundamentalFactorSnapshot {
+asset
+date
+pe
+pe_ttm
+pb
+total_share
+float_share
+free_share
+total_mv
+circ_mv
+roe
+roe_qoq
+metadata
}
class AssetMoneyFlowSnapshot {
+asset
+date
+buy_sm_amount
+sell_sm_amount
+buy_md_amount
+sell_md_amount
+buy_lg_amount
+sell_lg_amount
+buy_elg_amount
+sell_elg_amount
+net_mf_amount
+metadata
}
class AssetMarginDetailSnapshot {
+asset
+date
+rzye
+rqye
+rzmre
+rzche
+rqyl
+rqchl
+rqmcl
+rzrqye
+metadata
}
class CapitalFlowSnapshot {
+asset
+date
+main_force_net_5d
+margin_balance_change_5d
+metadata
}
class FactorScore {
+asset
+date
+mode
+fundamental_score
+capital_flow_score
+technical_score
+composite_score
+bottom_probability_score
+weights...
+metadata
}
FundamentalFactorSnapshot --> Asset : "FK"
AssetMoneyFlowSnapshot --> Asset : "FK"
AssetMarginDetailSnapshot --> Asset : "FK"
CapitalFlowSnapshot --> Asset : "FK"
FactorScore --> Asset : "FK"
```

**Diagram sources**
- [apps/factors/models.py:7-176](file://apps/factors/models.py#L7-L176)

**Section sources**
- [apps/factors/models.py:7-176](file://apps/factors/models.py#L7-L176)

### Prediction Domain
- ModelVersion/LightGBMModelArtifact: registry of trained models with status, metrics, training windows, and active flag.
- PredictionResult/LightGBMPrediction: per-asset, per-date, per-horizon predictions with probabilities, labels, risk metrics, and optional trade decision fields; linked to model version/artifact.
- EnsembleWeightSnapshot: time-varying ensemble weights.
- FeatureImportanceSnapshot: per-model artifact feature importance history.

```mermaid
classDiagram
class ModelVersion {
+model_type
+version
+status
+artifact_path
+metrics
+feature_schema
+training_window_start
+training_window_end
+trained_at
+is_active
+metadata
}
class PredictionResult {
+asset
+date
+horizon_days
+up_probability
+flat_probability
+down_probability
+confidence
+predicted_label
+target_price
+stop_loss_price
+risk_reward_ratio
+trade_score
+suggested
+model_version
+macro_phase
+event_tag
+feature_payload
+metadata
}
class LightGBMModelArtifact {
+horizon_days
+version
+status
+artifact_path
+metrics_json
+feature_names
+training_window_start
+training_window_end
+trained_at
+is_active
+feature_importance
+metadata
}
class LightGBMPrediction {
+asset
+date
+horizon_days
+up_probability
+flat_probability
+down_probability
+predicted_label
+confidence
+target_price
+stop_loss_price
+risk_reward_ratio
+trade_score
+suggested
+model_artifact
+feature_snapshot
+raw_scores
+calibrated_scores
+metadata
}
class EnsembleWeightSnapshot {
+date
+lightgbm_weight
+lstm_weight
+heuristic_weight
+basis_lookback_days
+basis_metrics
}
class FeatureImportanceSnapshot {
+model_artifact
+horizon_days
+feature_name
+importance_score
+importance_rank
}
PredictionResult --> ModelVersion : "FK"
LightGBMPrediction --> LightGBMModelArtifact : "FK"
EnsembleWeightSnapshot --> ModelVersion : "contextual"
FeatureImportanceSnapshot --> LightGBMModelArtifact : "FK"
```

**Diagram sources**
- [apps/prediction/models.py:7-107](file://apps/prediction/models.py#L7-L107)
- [apps/prediction/models_lightgbm.py:7-137](file://apps/prediction/models_lightgbm.py#L7-L137)

**Section sources**
- [apps/prediction/models.py:7-107](file://apps/prediction/models.py#L7-L107)
- [apps/prediction/models_lightgbm.py:7-137](file://apps/prediction/models_lightgbm.py#L7-L137)

### Backtest Domain
- BacktestRun: stores strategy type, date range, capitalization, summary metrics, JSON parameters/report, and async control fields; indexed on strategy/status/date ranges.
- BacktestTrade: leg-level trades linked to runs and assets; indexed on (backtest_run, trade_date) and (asset, trade_date).

```mermaid
sequenceDiagram
participant API as "API"
participant Run as "BacktestRun"
participant Trade as "BacktestTrade"
participant Asset as "Asset"
participant Bench as "PointInTimeBenchmarkDaily"
API->>Run : Create run (strategy, dates, params)
Run->>Run : Status=PENDING -> RUNNING
loop For each date in range
Run->>Trade : Record BUY/SELL legs
Trade->>Asset : Resolve prices via OHLCV
Run->>Bench : Compute benchmark returns
end
Run->>Run : Update metrics, report, status=COMPLETED/FAILED
```

**Diagram sources**
- [apps/backtest/models.py:24-168](file://apps/backtest/models.py#L24-L168)
- [apps/markets/models.py:173-226](file://apps/markets/models.py#L173-L226)

**Section sources**
- [apps/backtest/models.py:24-168](file://apps/backtest/models.py#L24-L168)

### Macro and Sentiment Domains
- MacroSnapshot: monthly/daily macro variables; unique date key.
- MarketContext: macro phases and event tags with active windows.
- EventImpactStat: historical impact stats by event tag, sector, horizon.
- NewsArticle: raw ingested articles deduplicated by URL.
- SentimentScore: three scopes (ARTICLE, ASSET_7D, MARKET_7D) with unique constraints ensuring idempotent upserts.
- ConceptHeat: aggregated concept theme metrics.

```mermaid
classDiagram
class MacroSnapshot {
+date UK
+dxy
+cny_usd
+yields...
+pmi
+cpi_yoy
+ppi_yoy
+metadata
}
class MarketContext {
+context_key
+macro_phase
+event_tag
+is_active
+starts_at
+ends_at
+notes
+metadata
}
class EventImpactStat {
+event_tag
+sector
+horizon_days
+avg_return
+excess_return
+sample_size
+observations_start
+observations_end
+metadata
}
class NewsArticle {
+source
+title
+url UK
+published_at
+content
+summary
+language
+related_assets
+concept_tags
+metadata
}
class SentimentScore {
+article
+asset
+date
+score_type
+positive_score
+neutral_score
+negative_score
+sentiment_score
+sentiment_label
+metadata
}
class ConceptHeat {
+concept_name
+date
+heat_score
+article_count
+up_limit_count
+net_inflow
+metadata
}
SentimentScore --> NewsArticle : "FK (nullable)"
SentimentScore --> Asset : "FK (nullable)"
```

**Diagram sources**
- [apps/macro/models.py:5-77](file://apps/macro/models.py#L5-L77)
- [apps/sentiment/models.py:35-125](file://apps/sentiment/models.py#L35-L125)

**Section sources**
- [apps/macro/models.py:5-77](file://apps/macro/models.py#L5-L77)
- [apps/sentiment/models.py:35-125](file://apps/sentiment/models.py#L35-L125)

### Security and Access Control Models
- DeveloperAPIKey: secure API key storage using SHA-256 hash; only prefix stored in plaintext; supports sandbox mode and expiration.
- UserProfile/Subscription: subscription tiers and activation status.
- APIUsage: endpoint/method/timestamp logs for rate limiting and analytics.

```mermaid
classDiagram
class DeveloperAPIKey {
+user
+name
+key_prefix
+key_hash UK
+is_active
+is_sandbox
+expires_at
+last_used_at
}
class UserProfile {
+user
+phone_number
+company
+email_verified
+email_verification_token
}
class Subscription {
+user
+tier
+stripe_subscription_id UK
+stripe_customer_id
+is_active
+start_date
+end_date
+auto_renew
}
class APIUsage {
+user
+endpoint
+method
+timestamp
+response_status
+ip_address
}
DeveloperAPIKey --> User : "FK"
UserProfile --> User : "1 : 1"
Subscription --> User : "FK"
APIUsage --> User : "FK (nullable)"
```

**Diagram sources**
- [apps/developer/models.py:10-156](file://apps/developer/models.py#L10-L156)
- [apps/users/models.py:13-186](file://apps/users/models.py#L13-L186)

**Section sources**
- [apps/developer/models.py:10-156](file://apps/developer/models.py#L10-L156)
- [apps/users/models.py:13-186](file://apps/users/models.py#L13-L186)

## Dependency Analysis
Cross-app dependencies are intentionally minimal and centered around Asset as the universal identifier. Predictions depend on model versions/artifacts; backtests depend on assets and benchmarks; factors and analytics depend on assets; sentiment depends on articles and optionally assets.

```mermaid
graph LR
Asset["Asset"]
OHLCV["OHLCV"]
TI["TechnicalIndicator"]
FS["FactorScore"]
PR["PredictionResult"]
LGBM["LightGBMPrediction"]
BR["BacktestRun"]
BT["BacktestTrade"]
IM["IndexMembership"]
PB["PointInTimeBenchmarkDaily"]
MS["MacroSnapshot"]
SC["SentimentScore"]
Asset --> OHLCV
Asset --> TI
Asset --> FS
Asset --> PR
Asset --> LGBM
Asset --> BT
Asset --> IM
IM --> PB
MS --> PR
SC --> PR
BR --> BT
```

**Diagram sources**
- [apps/markets/models.py:19-226](file://apps/markets/models.py#L19-L226)
- [apps/analytics/models.py:8-255](file://apps/analytics/models.py#L8-L255)
- [apps/factors/models.py:7-176](file://apps/factors/models.py#L7-L176)
- [apps/prediction/models.py:7-107](file://apps/prediction/models.py#L7-L107)
- [apps/prediction/models_lightgbm.py:7-137](file://apps/prediction/models_lightgbm.py#L7-L137)
- [apps/backtest/models.py:24-168](file://apps/backtest/models.py#L24-L168)
- [apps/macro/models.py:5-77](file://apps/macro/models.py#L5-L77)
- [apps/sentiment/models.py:35-125](file://apps/sentiment/models.py#L35-L125)

**Section sources**
- [apps/markets/models.py:19-226](file://apps/markets/models.py#L19-L226)
- [apps/prediction/models.py:7-107](file://apps/prediction/models.py#L7-L107)
- [apps/backtest/models.py:24-168](file://apps/backtest/models.py#L24-L168)

## Performance Considerations
- Indexing strategy:
  - Time-series tables use composite indexes on (entity, date) or (entity, timestamp) to optimize range scans and joins.
  - High-cardinality filters (e.g., predicted_label, score_type, horizon_days) are indexed to speed up dashboards and exports.
- Unique constraints:
  - Enforce idempotency for daily snapshots and predictions (e.g., (asset, date), (asset, date, horizon_days, model_version)).
- Partitioning and archival:
  - Large tables (OHLCV, TechnicalIndicator, SentimentScore) benefit from partitioning by date ranges and archiving older periods to cold storage.
- Query optimization:
  - Use covering indexes for frequent read paths (e.g., asset+date+indicator_type).
  - Avoid full-table scans by filtering on indexed columns (e.g., index_code, trade_date).
- Caching:
  - Cache hot aggregates (factor scores, top signals) with short TTLs.
  - Cache model artifacts and feature schemas at application layer.
- Bulk operations:
  - Prefer bulk_create/update for backfills; batch by asset and date ranges to minimize transaction size.
- Staleness detection:
  - Monitor last_updated timestamps and compare against expected refresh cadence; trigger recomputation when gaps are detected.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and remedies grounded in the schema:
- Duplicate rows:
  - Violations of unique_together constraints indicate duplicate ingestion; ensure idempotent upserts and deduplicate by natural keys (e.g., asset+date).
- Missing point-in-time data:
  - If IndexMembership lacks entries for a date, universe construction may be incomplete; verify sync tasks and backfill pipelines.
- Stale indicators:
  - Check TechnicalIndicator timestamps and recompute if behind schedule; leverage staleness utilities to detect gaps.
- Backtest inconsistencies:
  - Validate BacktestRun.status and report.runtime_state; inspect BacktestTrade legs for missing prices or incorrect sides.
- API key misuse:
  - Verify DeveloperAPIKey.is_active and expiration; confirm key_hash matches provided key prefix.

**Section sources**
- [apps/analytics/models.py:8-255](file://apps/analytics/models.py#L8-L255)
- [apps/markets/models.py:117-145](file://apps/markets/models.py#L117-L145)
- [apps/backtest/models.py:24-168](file://apps/backtest/models.py#L24-L168)
- [apps/developer/models.py:10-156](file://apps/developer/models.py#L10-L156)

## Conclusion
The FinanceAnalysis platform’s data model emphasizes robust point-in-time integrity, clear separation of concerns across domains, and strong indexing and constraints to support large-scale financial analytics. Asset-centric design enables consistent joins across market data, derived features, predictions, and backtests. Lifecycle management via IndexMembership and asset dates ensures accurate historical analysis. Security and access controls are enforced at the model level through hashed API keys and user-scoped resources.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Point-in-Time Integrity Patterns
- IndexMembership records which indices an asset belonged to on each trade_date, enabling correct historical universe construction.
- PointInTimeBenchmarkDaily provides a union benchmark reflecting constituents available at each date, avoiding look-ahead bias in backtests.
- Asset.list_date and .delist_date gate inclusion/exclusion in analyses based on effective dates.

```mermaid
flowchart TD
Start(["Start Historical Query"]) --> GetDate["Select target trade_date"]
GetDate --> Universe["Build universe via IndexMembership where trade_date <= target"]
Universe --> FilterAssets["Filter by Asset.list_date <= target and (delist_date > target or null)"]
FilterAssets --> LoadData["Load OHLCV/Factors/Predictions for universe"]
LoadData --> Analyze["Compute metrics/benchmarks"]
Analyze --> End(["Return results"])
```

**Diagram sources**
- [apps/markets/models.py:19-226](file://apps/markets/models.py#L19-L226)

**Section sources**
- [apps/markets/models.py:117-226](file://apps/markets/models.py#L117-L226)

### Data Lifecycle Policies
- Historical floors: maintain immutable historical snapshots (e.g., OHLCV, FactorScore) with strict append-only semantics; corrections should be handled via new revisions rather than in-place updates.
- Staleness detection: monitor last_updated fields and expected refresh schedules; alert on gaps exceeding thresholds.
- Archival rules: archive data beyond retention windows to cold storage; keep lightweight indexes for recent hot data.

[No sources needed since this section provides general guidance]

### Security and Privacy Requirements
- Store only hashed API keys; expose safe prefixes for identification.
- Enforce sandbox vs production modes for API keys to limit exposure.
- Scope user-specific data via foreign keys to User/UserProfile; restrict access through application-layer authorization.
- Log API usage for auditability and rate limiting.

**Section sources**
- [apps/developer/models.py:10-156](file://apps/developer/models.py#L10-L156)
- [apps/users/models.py:13-186](file://apps/users/models.py#L13-L186)