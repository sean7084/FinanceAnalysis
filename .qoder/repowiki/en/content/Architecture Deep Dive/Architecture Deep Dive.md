# Architecture Deep Dive

<cite>
**Referenced Files in This Document**
- [README.md](file://README.md)
- [TECHNICAL_GUIDE.md](file://TECHNICAL_GUIDE.md)
- [docker-compose.yml](file://docker-compose.yml)
- [compose/local/django/Dockerfile](file://compose/local/django/Dockerfile)
- [config/settings/base.py](file://config/settings/base.py)
- [config/urls.py](file://config/urls.py)
- [config/celery.py](file://config/celery.py)
- [config/routing.py](file://config/routing.py)
- [apps/markets/models.py](file://apps/markets/models.py)
- [apps/analytics/models.py](file://apps/analytics/models.py)
- [apps/factors/models.py](file://apps/factors/models.py)
- [apps/macro/models.py](file://apps/macro/models.py)
- [apps/sentiment/models.py](file://apps/sentiment/models.py)
- [apps/prediction/models.py](file://apps/prediction/models.py)
- [apps/backtest/models.py](file://apps/backtest/models.py)
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
FinanceAnalysis is a Django-based platform for analyzing Chinese A-share markets over the CSI 300 and CSI A500 universes. It ingests market, fundamental, macro, and news data; derives technical and factor features; runs heuristic, LightGBM, and LSTM prediction models; and backtests trading strategies against point-in-time benchmarks. The system exposes a REST API (Django REST Framework), WebSocket alerts (Channels over Redis), and a React dashboard that proxies to the backend. Celery with Beat drives scheduled ingestion, feature derivation, predictions, and backtests across dedicated queues.

Key design principles:
- Strict upstream → derived ordering across ten Django applications.
- Point-in-time integrity enforced by canonical universe rules and historical membership snapshots.
- Separation between raw data (markets, fundamentals, macro, sentiment articles) and derived features (technical indicators, factor scores, predictions).
- Resilient async processing via Celery queues and chunked execution for long-running jobs.

**Section sources**
- [README.md:48-114](file://README.md#L48-L114)
- [TECHNICAL_GUIDE.md:25-72](file://TECHNICAL_GUIDE.md#L25-L72)

## Project Structure
The repository organizes functionality into ten Django apps with a strict dependency order. At runtime:
- PostgreSQL stores all persistent data.
- Redis provides caching, Channels layer, and Celery broker/backend.
- Docker Compose orchestrates Django, Celery worker, and Celery Beat containers.
- URLs route REST endpoints under /api/v1/ and mount admin and OpenAPI schema.

```mermaid
graph TB
subgraph "Runtime"
PG["PostgreSQL"]
RDS["Redis"]
DJ["Django + DRF"]
CH["Channels (ASGI)"]
CW["Celery Worker"]
CB["Celery Beat"]
end
subgraph "Apps"
M["markets"]
A["analytics"]
F["factors"]
C["macro"]
S["sentiment"]
P["prediction"]
B["backtest"]
U["users"]
D["developer"]
CORE["core"]
end
PG --> DJ
RDS --> DJ
RDS --> CH
RDS --> CW
CB --> CW
DJ --> PG
CH --> RDS
M --> A
M --> F
M --> C
M --> S
A --> P
F --> P
C --> P
S --> P
P --> B
U --> DJ
D --> DJ
CORE --> DJ
```

**Diagram sources**
- [docker-compose.yml:1-39](file://docker-compose.yml#L1-L39)
- [config/settings/base.py:64-88](file://config/settings/base.py#L64-L88)
- [config/settings/base.py:174-255](file://config/settings/base.py#L174-L255)
- [config/urls.py:74-128](file://config/urls.py#L74-L128)

**Section sources**
- [docker-compose.yml:1-39](file://docker-compose.yml#L1-L39)
- [compose/local/django/Dockerfile:1-53](file://compose/local/django/Dockerfile#L1-L53)
- [config/settings/base.py:64-88](file://config/settings/base.py#L64-L88)
- [config/settings/base.py:122-126](file://config/settings/base.py#L122-L126)
- [config/settings/base.py:174-255](file://config/settings/base.py#L174-L255)
- [config/settings/base.py:264-339](file://config/settings/base.py#L264-L339)
- [config/urls.py:74-128](file://config/urls.py#L74-L128)

## Core Components
- core: Pagination, throttling tiers, historical floor enforcement, validation helpers, documentation generation.
- markets: Universe foundation—assets, OHLCV, trading calendar, suspensions, index membership, benchmark indices, point-in-time union benchmark.
- analytics: Stored technical indicators, signal events, alerts, screeners, dashboard DTOs.
- factors: Fundamentals, money flow, margin detail, capital flow, composite factor scores.
- macro: Monthly macro surface and inferred market regime context.
- sentiment: News ingestion, article scoring, rolling asset/market sentiment aggregation, concept heat.
- prediction: Heuristic, LightGBM, LSTM models; trade decisions; model registry and ensemble weights.
- backtest: Run engine, trades ledger, comparison curves, benchmark export, chunked resume.
- users: Authentication, subscriptions, usage metering.
- developer: API key portal and public changelog.

These components form a pipeline where markets feeds analytics, factors, macro, and sentiment; those layers feed prediction; prediction feeds backtest; and all surfaces are exposed through REST and WebSocket APIs consumed by the React dashboard.

**Section sources**
- [README.md:50-63](file://README.md#L50-L63)
- [config/settings/base.py:64-88](file://config/settings/base.py#L64-L88)

## Architecture Overview
The system enforces a strict upstream → derived ordering:
- Raw data: markets, factors (fundamentals/money flow/margin), macro, sentiment (articles).
- Derived features: analytics (technical indicators/signals), factors (composite scores), macro (market context).
- Prediction: heuristic, LightGBM, LSTM using shared stored features and context.
- Backtest: runtime candidate generation and execution against point-in-time benchmarks.

```mermaid
sequenceDiagram
participant Ext as "External Providers<br/>TuShare/AkShare"
participant Mk as "markets"
participant An as "analytics"
participant Fa as "factors"
participant Mc as "macro"
participant Se as "sentiment"
participant Pr as "prediction"
participant Bt as "backtest"
participant API as "REST/WebSocket API"
participant FE as "React Dashboard"
Ext->>Mk : Ingest OHLCV, calendar, suspensions, index membership
Mk-->>An : OHLCV, calendar, suspensions
Mk-->>Fa : Universe filters, index membership
Mk-->>Mc : Calendar alignment
Mk-->>Se : Asset list for news association
An-->>Pr : Technical indicators, signals
Fa-->>Pr : Factor scores, bottom probability
Mc-->>Pr : Macro phase/context
Se-->>Pr : Rolling sentiment scores
Pr-->>Bt : Predictions/trade decisions (runtime for backtests)
Bt-->>API : Runs, trades, equity curves
API-->>FE : Dashboards, alerts, reports
```

**Diagram sources**
- [README.md:65-90](file://README.md#L65-L90)
- [config/settings/base.py:218-255](file://config/settings/base.py#L218-L255)
- [config/urls.py:74-128](file://config/urls.py#L74-L128)

**Section sources**
- [README.md:65-90](file://README.md#L65-L90)
- [config/settings/base.py:218-255](file://config/settings/base.py#L218-L255)

## Detailed Component Analysis

### Data Integrity and Point-in-Time Universe
- Canonical rule: before a cutoff date, use CSI 300 only; after, use CSI 300 ∪ CSI A500.
- Historical membership snapshots ensure cross-sections reflect constituents at each date.
- Point-in-time union benchmark supports fair backtesting and avoids survivorship bias.

```mermaid
flowchart TD
Start(["Start Cross-Section"]) --> CheckDate["Resolve Date"]
CheckDate --> Before{"Before Cutoff?"}
Before --> |Yes| UseCSI300["Use CSI 300 Constituents"]
Before --> |No| UseUnion["Use CSI 300 ∪ CSI A500 Constituents"]
UseCSI300 --> ValidateCoverage{"Coverage Complete?"}
UseUnion --> ValidateCoverage
ValidateCoverage --> |Yes| Proceed["Proceed with Calculation"]
ValidateCoverage --> |No| FailClosed["Fail Closed (no silent fallback)"]
```

**Diagram sources**
- [README.md:92-105](file://README.md#L92-L105)
- [TECHNICAL_GUIDE.md:25-72](file://TECHNICAL_GUIDE.md#L25-L72)
- [apps/markets/models.py:117-144](file://apps/markets/models.py#L117-L144)
- [apps/markets/models.py:173-199](file://apps/markets/models.py#L173-L199)

**Section sources**
- [README.md:92-105](file://README.md#L92-L105)
- [TECHNICAL_GUIDE.md:25-72](file://TECHNICAL_GUIDE.md#L25-L72)
- [apps/markets/models.py:117-144](file://apps/markets/models.py#L117-L144)
- [apps/markets/models.py:173-199](file://apps/markets/models.py#L173-L199)

### Markets Layer
Stores foundational data: assets, OHLCV, trading calendar, suspensions, index membership, benchmark indices, and point-in-time union benchmark. These are the authoritative inputs for downstream feature computation and backtesting.

```mermaid
classDiagram
class Market {
+code
+name
}
class Asset {
+market
+symbol
+ts_code
+name
+listing_status
+list_date
+delist_date
+membership_tags
}
class ExchangeTradingCalendar {
+exchange_code
+trade_date
+is_open
+source
}
class AssetSuspension {
+asset
+trade_date
+suspend_type
+is_full_day
+source
}
class IndexMembership {
+asset
+index_code
+index_name
+trade_date
+weight
+source
}
class BenchmarkIndexDaily {
+index_code
+index_name
+trade_date
+open
+high
+low
+close
+source
}
class PointInTimeBenchmarkDaily {
+benchmark_code
+benchmark_name
+trade_date
+daily_return
+nav
+constituent_count
+overlap_count
+weighting_method
+metadata
}
class OHLCV {
+asset
+date
+open
+high
+low
+close
+adj_close
+volume
+amount
}
Asset --> Market : "belongs to"
AssetSuspension --> Asset : "per asset"
IndexMembership --> Asset : "historical membership"
BenchmarkIndexDaily --> Market : "index series"
PointInTimeBenchmarkDaily --> Market : "union benchmark"
OHLCV --> Asset : "price history"
```

**Diagram sources**
- [apps/markets/models.py:4-63](file://apps/markets/models.py#L4-L63)
- [apps/markets/models.py:65-85](file://apps/markets/models.py#L65-L85)
- [apps/markets/models.py:87-115](file://apps/markets/models.py#L87-L115)
- [apps/markets/models.py:117-144](file://apps/markets/models.py#L117-L144)
- [apps/markets/models.py:147-199](file://apps/markets/models.py#L147-L199)
- [apps/markets/models.py:201-226](file://apps/markets/models.py#L201-L226)

**Section sources**
- [apps/markets/models.py:4-63](file://apps/markets/models.py#L4-L63)
- [apps/markets/models.py:65-85](file://apps/markets/models.py#L65-L85)
- [apps/markets/models.py:87-115](file://apps/markets/models.py#L87-L115)
- [apps/markets/models.py:117-144](file://apps/markets/models.py#L117-L144)
- [apps/markets/models.py:147-199](file://apps/markets/models.py#L147-L199)
- [apps/markets/models.py:201-226](file://apps/markets/models.py#L201-L226)

### Analytics Layer
Stores technical indicators and signal events used by dashboards, alerts, and downstream prediction/backtest logic. Indicators include RSI, MACD, Bollinger Bands, moving averages, momentum, and volume metrics. Signal events capture technical patterns like golden/death crosses, breakouts, volume spikes, and oversold combinations.

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
class AlertRule {
+owner
+asset
+name
+condition_type
+indicator_type
+threshold
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
+dispatched_channels
+notified_at
}
AlertEvent --> AlertRule : "triggered by"
SignalEvent --> TechnicalIndicator : "consumes"
AlertRule --> TechnicalIndicator : "evaluates"
```

**Diagram sources**
- [apps/analytics/models.py:8-46](file://apps/analytics/models.py#L8-L46)
- [apps/analytics/models.py:87-146](file://apps/analytics/models.py#L87-L146)
- [apps/analytics/models.py:148-196](file://apps/analytics/models.py#L148-L196)
- [apps/analytics/models.py:198-255](file://apps/analytics/models.py#L198-L255)

**Section sources**
- [apps/analytics/models.py:8-46](file://apps/analytics/models.py#L8-L46)
- [apps/analytics/models.py:87-146](file://apps/analytics/models.py#L87-L146)
- [apps/analytics/models.py:148-196](file://apps/analytics/models.py#L148-L196)
- [apps/analytics/models.py:198-255](file://apps/analytics/models.py#L198-L255)

### Factors Layer
Aggregates fundamentals, money flow, margin detail, and capital flow into daily factor scores. Composite scoring combines financial, capital flow, technical reversal, and sentiment components with explicit weights.

```mermaid
classDiagram
class FundamentalFactorSnapshot {
+asset
+date
+pe
+pb
+roe
+total_mv
+circ_mv
+metadata
}
class AssetMoneyFlowSnapshot {
+asset
+date
+net_mf_amount
+buy_sm_amount
+sell_sm_amount
+metadata
}
class AssetMarginDetailSnapshot {
+asset
+date
+rzrqye
+rqyl
+rqmcl
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
+fundamental_score
+capital_flow_score
+technical_score
+composite_score
+bottom_probability_score
+mode
+metadata
}
FactorScore --> FundamentalFactorSnapshot : "uses"
FactorScore --> AssetMoneyFlowSnapshot : "uses"
FactorScore --> AssetMarginDetailSnapshot : "uses"
FactorScore --> CapitalFlowSnapshot : "uses"
```

**Diagram sources**
- [apps/factors/models.py:7-37](file://apps/factors/models.py#L7-L37)
- [apps/factors/models.py:39-68](file://apps/factors/models.py#L39-L68)
- [apps/factors/models.py:70-98](file://apps/factors/models.py#L70-L98)
- [apps/factors/models.py:100-122](file://apps/factors/models.py#L100-L122)
- [apps/factors/models.py:124-176](file://apps/factors/models.py#L124-L176)

**Section sources**
- [apps/factors/models.py:7-37](file://apps/factors/models.py#L7-L37)
- [apps/factors/models.py:39-68](file://apps/factors/models.py#L39-L68)
- [apps/factors/models.py:70-98](file://apps/factors/models.py#L70-L98)
- [apps/factors/models.py:100-122](file://apps/factors/models.py#L100-L122)
- [apps/factors/models.py:124-176](file://apps/factors/models.py#L124-L176)

### Macro Layer
Stores monthly macro snapshots (yield curve, PMI, CPI/PPI) and inferred market context phases. Context is resolved by date ranges for as-of correctness in prediction and backtesting.

```mermaid
classDiagram
class MacroSnapshot {
+date
+dxy
+cny_usd
+cn6m_yield..cn30y_yield
+pmi_manufacturing
+pmi_non_manufacturing
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
MarketContext --> MacroSnapshot : "derived from"
EventImpactStat --> MacroSnapshot : "historical impact"
```

**Diagram sources**
- [apps/macro/models.py:5-29](file://apps/macro/models.py#L5-L29)
- [apps/macro/models.py:31-57](file://apps/macro/models.py#L31-L57)
- [apps/macro/models.py:59-77](file://apps/macro/models.py#L59-L77)

**Section sources**
- [apps/macro/models.py:5-29](file://apps/macro/models.py#L5-L29)
- [apps/macro/models.py:31-57](file://apps/macro/models.py#L31-L57)
- [apps/macro/models.py:59-77](file://apps/macro/models.py#L59-L77)

### Sentiment Layer
Captures raw news articles and aggregates sentiment at article, per-asset rolling, and market levels. Concept heat tracks theme activity.

```mermaid
classDiagram
class NewsArticle {
+source
+title
+url
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
SentimentScore --> NewsArticle : "per-article score"
SentimentScore --> Asset : "per-asset aggregation"
ConceptHeat --> NewsArticle : "theme aggregation"
```

**Diagram sources**
- [apps/sentiment/models.py:35-62](file://apps/sentiment/models.py#L35-L62)
- [apps/sentiment/models.py:64-108](file://apps/sentiment/models.py#L64-L108)
- [apps/sentiment/models.py:110-125](file://apps/sentiment/models.py#L110-L125)

**Section sources**
- [apps/sentiment/models.py:35-62](file://apps/sentiment/models.py#L35-L62)
- [apps/sentiment/models.py:64-108](file://apps/sentiment/models.py#L64-L108)
- [apps/sentiment/models.py:110-125](file://apps/sentiment/models.py#L110-L125)

### Prediction Layer
Stores model versions, predictions, and artifacts. Includes heuristic, LightGBM, and LSTM models with ensemble weights and feature importance snapshots. Trade decision fields enrich predictions with target/stop-loss and suggested flags.

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
PredictionResult --> ModelVersion : "produced by"
```

**Diagram sources**
- [apps/prediction/models.py:7-41](file://apps/prediction/models.py#L7-L41)
- [apps/prediction/models.py:43-98](file://apps/prediction/models.py#L43-L98)

**Section sources**
- [apps/prediction/models.py:7-41](file://apps/prediction/models.py#L7-L41)
- [apps/prediction/models.py:43-98](file://apps/prediction/models.py#L43-L98)

### Backtest Layer
Tracks run lifecycle, parameters, report, and trades. Supports chunked execution with resume state and detailed trade legs with signal payloads for auditability.

```mermaid
classDiagram
class BacktestRun {
+user
+name
+strategy_type
+status
+start_date
+end_date
+initial_capital
+cash
+final_value
+total_return
+annualized_return
+max_drawdown
+sharpe_ratio
+win_rate
+total_trades
+winning_trades
+parameters
+report
+error_message
+current_task_id
+pending_control_action
+started_at
+completed_at
}
class BacktestTrade {
+backtest_run
+asset
+trade_date
+side
+quantity
+price
+fee
+slippage
+amount
+pnl
+signal_payload
+metadata
}
BacktestTrade --> BacktestRun : "belongs to"
```

**Diagram sources**
- [apps/backtest/models.py:24-118](file://apps/backtest/models.py#L24-L118)
- [apps/backtest/models.py:120-168](file://apps/backtest/models.py#L120-L168)

**Section sources**
- [apps/backtest/models.py:24-118](file://apps/backtest/models.py#L24-L118)
- [apps/backtest/models.py:120-168](file://apps/backtest/models.py#L120-L168)

## Dependency Analysis
Application dependency order reflects data flow:
- core → users → markets → analytics → factors → macro → sentiment → prediction → backtest → developer.
- Celery routes tasks to dedicated queues: ops, backtest, train-lightgbm, train-lstm.
- Channels uses Redis for WebSocket routing; DRF uses JWT and API key authentication.

```mermaid
graph LR
Core["core"] --> Users["users"]
Users --> Markets["markets"]
Markets --> Analytics["analytics"]
Markets --> Factors["factors"]
Markets --> Macro["macro"]
Markets --> Sentiment["sentiment"]
Analytics --> Prediction["prediction"]
Factors --> Prediction
Macro --> Prediction
Sentiment --> Prediction
Prediction --> Backtest["backtest"]
Developer["developer"] --> API["REST API"]
Users --> API
```

**Diagram sources**
- [config/settings/base.py:64-88](file://config/settings/base.py#L64-L88)
- [config/settings/base.py:174-203](file://config/settings/base.py#L174-L203)
- [config/urls.py:74-128](file://config/urls.py#L74-L128)

**Section sources**
- [config/settings/base.py:64-88](file://config/settings/base.py#L64-L88)
- [config/settings/base.py:174-203](file://config/settings/base.py#L174-L203)
- [config/urls.py:74-128](file://config/urls.py#L74-L128)

## Performance Considerations
- Point-in-time guards prevent stale or incomplete windows from corrupting features and predictions.
- Gap-tolerant vs exact-window metrics balance robustness with precision; missing rows fall back to documented neutral values rather than silently reusing stale data.
- Chunked backtest execution enables long runs to survive worker restarts and soft time limits.
- Feature pruning reduces model size while retaining important features based on latest importance snapshots.
- Separate queues isolate heavy workloads (backtests, training) from operational tasks.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and mitigations:
- Stale derived data: Refuse stale rows; rebuild RS_SCORE slices and signal events when windows become invalid.
- Provider blackouts: Use primary/fallback providers for macro and news; schedule retries and sleep intervals.
- Stuck tasks: Monitor current_task_id and pending_control_action on BacktestRun; detect orphaned runs via task health checks.
- Missing OHLCV or non-positive close: Predictions return null targets/stops and suggested=false; backtest holds positions until valid closes appear.
- Universe coverage gaps: Fail closed when point-in-time membership coverage is missing; do not widen silently.

**Section sources**
- [TECHNICAL_GUIDE.md:117-224](file://TECHNICAL_GUIDE.md#L117-L224)
- [apps/backtest/models.py:24-118](file://apps/backtest/models.py#L24-L118)
- [config/settings/base.py:205-217](file://config/settings/base.py#L205-L217)

## Conclusion
FinanceAnalysis implements a disciplined, upstream → derived architecture that prioritizes point-in-time integrity, separation of raw and derived data, and resilient async processing. The stack combines Django REST Framework for APIs, Channels for live alerts, Celery for scheduling and background jobs, PostgreSQL for persistence, and Redis for caching and messaging. The React dashboard integrates via proxied REST and WebSocket endpoints. With clear contracts for freshness, universe membership, and model governance, the platform supports reliable daily predictions and reproducible backtests.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Technology Stack Summary
- Backend: Django + Django REST Framework + Channels + Celery + Celery Beat
- Database: PostgreSQL
- Cache/Message Broker: Redis
- Containerization: Docker Compose with separate services for Django, Celery Worker, and Celery Beat
- Frontend: React dashboard proxying /api and /ws to backend

**Section sources**
- [README.md:107-114](file://README.md#L107-L114)
- [docker-compose.yml:1-39](file://docker-compose.yml#L1-L39)
- [compose/local/django/Dockerfile:1-53](file://compose/local/django/Dockerfile#L1-L53)
- [config/settings/base.py:174-255](file://config/settings/base.py#L174-L255)
- [config/settings/base.py:264-339](file://config/settings/base.py#L264-L339)
- [config/urls.py:74-128](file://config/urls.py#L74-L128)