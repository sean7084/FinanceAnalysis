# Changelog

### version 0.1.8: LSTM Pipeline + Multi-Model Backtest ✓
**Objective**: 让 LSTM 成为可训练、可推理、可回测的一等模型，并完善回测与个股页面操作体验

**Implemented Features**:
- Delivered real LSTM training (not registry-only):
  - added PyTorch LSTM retrain task with temporal sequence samples
  - added memory-safe chunked feature extraction and sample caps for long windows
  - added end-to-end command `rebuild_lstm_pipeline` with date-window/horizon controls
  - activated LSTM model version on `2000-01-01..2024-12-31`
- Delivered LSTM inference path parallel to LightGBM:
  - added runtime artifact loading + sequence feature build + probability inference
  - persisted LSTM predictions into `PredictionResult` with trade-decision fields
  - added API route family `/api/v1/lstm-predictions/` (stock, batch, train, recalculate)
- Expanded backtest source system:
  - added `lstm` as valid `prediction_source`
  - added frontend source option `all-models` that fans out one submission into heuristic/lightgbm/lstm runs
  - wired LSTM candidate selection into backtest runtime and serializer validation
- Backtest page UX upgrades:
  - run history: 10-row pagination
  - run history columns: added max drawdown + win rate
  - trade history: 10-row pagination
  - selected run metrics: expanded to full summary set (initial/final capital, returns, drawdown, sharpe, win-rate, trades, benchmark)
- Stock detail page UX upgrade:
  - added searchable stock selector (symbol/name filter)
- Documentation updates:
  - refreshed technical guide for LSTM training/inference and backtest source options

**Current Notes**:
- LSTM now supports both training and inference in production code paths.
- Backtest runner default source is now `all-models`.

**Key Files**:
- `apps/prediction/tasks_lstm.py`
- `apps/prediction/management/commands/rebuild_lstm_pipeline.py`
- `apps/prediction/views_lstm.py`
- `config/urls.py`
- `apps/backtest/tasks.py`
- `apps/backtest/serializers.py`
- `frontend/src/pages/BacktestWorkbenchPage.tsx`
- `frontend/src/pages/StockDetailPage.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/i18n.tsx`
- `TechnicalGuide.md`

### version 0.1.7: LightGBM Monitoring & Model Comparison ✓
**Objective**: 完成 LightGBM 的可观测性闭环，并把模型对比结果带到日常使用页面

**Implemented Features**:
- Deepened the LightGBM monitoring stack:
  - added historical `FeatureImportanceSnapshot` storage and admin visibility
  - exposed feature-importance trend API for recent model artifacts
  - registered LightGBM runs into `ModelVersion`
  - refreshed `EnsembleWeightSnapshot` from live LightGBM training output
- Expanded frontend model visibility:
  - added the dedicated model-monitoring page for model versions, LightGBM artifacts, prediction snapshots, feature trends, and ensemble weights
  - added heuristic-vs-LightGBM comparison sections on the dashboard and stock detail page
- Added missing LightGBM API parity:
  - stock-symbol LightGBM prediction endpoint aligned with the heuristic stock endpoint shape
  - feature-importance trends endpoint for historical inspection instead of only artifact JSON summaries
- Hardened live LightGBM training/inference behavior:
  - fixed nullable live feature extraction fallbacks in LightGBM training
  - added safe calibration fallback for raw LightGBM booster models
  - prepared historical factor-score and sentiment rows so live 3-day and 7-day LightGBM models could train successfully
- Live data population completed:
  - trained active 3-day and 7-day LightGBM models
  - generated 600 LightGBM prediction rows for the current date
  - populated feature-importance snapshots and ensemble-weight history in the database

**Current Notes**:
- 30-day LightGBM training still requires a deeper historical factor-score and sentiment backfill window.
- Dashboard probability chart section was removed after the comparison rollout to keep the page focused on candidate tables and model comparison data.

**Key Files**:
- `apps/prediction/tasks_lightgbm.py`
- `apps/prediction/views_lightgbm.py`
- `apps/prediction/serializers_lightgbm.py`
- `apps/prediction/models_lightgbm.py`
- `apps/prediction/tests_lightgbm.py`
- `frontend/src/pages/ModelMonitoringPage.tsx`
- `frontend/src/pages/DashboardPage.tsx`
- `frontend/src/pages/StockDetailPage.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/i18n.tsx`

---

### version 0.0.1: Foundation & Docker Setup ✓
**Objective**: Production-ready project structure with containerization

**Achievements**:
- Split settings architecture (`base.py`, `local.py`, `production.py`)
- Django project initialized with custom apps structure
- Docker Compose configuration:
  - Django application service
  - PostgreSQL 15 database
  - Redis for caching and Celery broker
  - Celery worker and beat services
- Environment variable management with `django-environ`
- Static files handling

**Key Files**:
- `config/settings/base.py` - Core Django settings
- `docker-compose.yml` - Service orchestration
- `compose/local/django/Dockerfile` - Django container build
- `compose/local/django/start*.sh` - Service startup scripts

---

### version 0.0.2: Bilingual Data Modeling ✓
**Objective**: Create core financial data models with translation support

**Achievements**:
- **Market Model**: Stock exchanges (SSE, SZSE, BSE)
- **Asset Model**: Individual stocks with bilingual names
- **OHLCV Model**: Daily price data (Open, High, Low, Close, Volume)
- Integrated `django-modeltranslation` for field-level translations
- Django Admin interface with translation support
- Database migrations applied successfully

**Data Model**:
```
Market (3 exchanges)
  ├── Asset (334 CSI 300 stocks)
      └── OHLCV (Historical daily data)
```

**Key Files**:
- `apps/markets/models.py` - Core financial models
- `apps/markets/translation.py` - Translation configuration
- `apps/markets/admin.py` - Admin interface

---

### version 0.0.3: Data Ingestion Engine ✓
**Objective**: Automated data synchronization with AkShare

**Achievements**:
- Celery worker and beat services configured
- Distributed task architecture (dispatcher + workers)
- **Main Tasks**:
  - `sync_daily_a_shares()` - Dispatcher task for CSI 300 stocks
  - `sync_asset_history()` - Individual stock data processor
- Successfully imported 334 CSI 300 stocks with historical data
- Idempotent data handling (no duplicates on re-runs)
- Error handling and logging

**Data Coverage**:
- **Markets**: Shanghai (SSE), Shenzhen (SZSE), Beijing (BSE)
- **Stocks**: 334 CSI 300 constituents
- **Historical Data**: ~1 year of daily OHLCV data per stock

**Key Files**:
- `apps/markets/tasks.py` - Data ingestion tasks
- `config/celery.py` - Celery configuration
- `compose/local/django/start-celeryworker` - Worker startup script
- `compose/local/django/start-celerybeat` - Scheduler startup script

---

### version 0.0.4: Financial Analysis & Indicators ✓
**Objective**: Calculate technical indicators using TA-Lib

**Achievements**:
- TA-Lib C library compiled and installed in Docker container
- **TechnicalIndicator Model**: Stores calculated indicators with timestamps
- **Calculation Tasks**:
  - `calculate_rsi_for_asset()` - 14-period RSI calculation
  - `calculate_macd_for_asset()` - MACD calculation (12, 26, 9)
  - `calculate_indicators_for_all_assets()` - Batch processing dispatcher
- Pandas-based data pipeline for efficient computation
- Successfully calculated 668 indicators (334 RSI + 334 MACD)

**Indicators Calculated**:
- **RSI (Relative Strength Index)**: 14-period, identifies overbought/oversold conditions
- **MACD (Moving Average Convergence Divergence)**: 12/26/9 periods, trend indicator

**Key Files**:
- `apps/analytics/models.py` - TechnicalIndicator model
- `apps/analytics/tasks.py` - Indicator calculation tasks
- `compose/local/django/Dockerfile` - TA-Lib installation

---

### version 0.0.5: REST API with Caching ✓
**Objective**: Expose data through secure, performant API

**Achievements**:
- **API Endpoints**:
  - `/api/v1/markets/` - List markets
  - `/api/v1/assets/` - Search and filter stocks
  - `/api/v1/ohlcv/` - Historical price data
  - `/api/v1/indicators/` - Technical indicators
  - `/api/v1/indicators/top_rsi/` - Top 20 overbought stocks
  - `/api/v1/indicators/bottom_rsi/` - Top 20 oversold stocks
- **Features**:
  - Pagination (50 items per page)
  - Advanced filtering with `django-filter`
  - Search functionality
  - Redis caching (2 hours for data, 5 minutes for dynamic endpoints)
  - Optimized queries with `select_related()`
  - Separate list/detail serializers for performance
- **Browsable API** for easy testing and documentation

**API Capabilities**:
- Filter assets by market: `?market__code=SSE`
- Search by name/symbol: `?search=平安`
- Date range filtering for OHLCV and indicators
- Custom aggregations (top/bottom RSI)

**Key Files**:
- `apps/markets/serializers.py` - Market/Asset/OHLCV serializers
- `apps/markets/views.py` - Market API ViewSets
- `apps/analytics/serializers.py` - Indicator serializers
- `apps/analytics/views.py` - Analytics API ViewSets
- `config/urls.py` - API routing

---

### version 0.0.6: Production Readiness & SaaS Features ✓
**Objective**: Authentication, authorization, and rate limiting

**Achievements**:
- **JWT Authentication**:
  - SimpleJWT integration
  - Access tokens (60-minute lifetime)
  - Refresh tokens (7-day lifetime with rotation)
  - Token blacklist for security
- **Authentication Endpoints**:
  - `POST /api/v1/auth/token/` - Obtain tokens
  - `POST /api/v1/auth/token/refresh/` - Refresh access token
  - `POST /api/v1/auth/token/verify/` - Verify token validity
- **Rate Limiting (SaaS Tiers)**:
  - Anonymous users: 100 requests/day
  - Authenticated users: 1,000 requests/day
  - Premium users: 10,000 requests/day (extensible)
- **Security**:
  - Redis-backed caching
  - Stateless authentication
  - Token rotation and blacklisting
  - Custom throttle classes for tier management

**API Security Model**:
- Read-only access for unauthenticated users
- Full access for authenticated users
- Tier-based rate limiting
- JWT bearer token authentication

**Key Files**:
- `config/settings/base.py` - JWT and throttling configuration
- `apps/core/throttling.py` - Custom throttle classes
- `config/urls.py` - Authentication endpoints


---

### version 0.0.7: User Management & Subscriptions ✓
**Objective**: Multi-tenant user system with subscription tiers

**Implemented Features**:
- User registration with email verification flow
- Password reset via token-based email link
- User profile management (`phone_number`, `company`, subscription info)
- Subscription tier model: `FREE`, `PRO`, `PREMIUM` with Stripe-ready fields (`stripe_subscription_id`, `stripe_customer_id`)
- Subscription lifecycle: `is_active`, `start_date`, `end_date`, `auto_renew`, `cancel()` method
- API usage tracking via `APIUsageMiddleware` — records endpoint, method, status, IP for every `/api/v1/` call
- Usage stats dashboard — daily/monthly counts, top endpoints, tier-based daily limit
- Admin panel with `UserAdmin` inlines, `SubscriptionAdmin` with activate/deactivate bulk actions

**Technical Implementation**:
- `UserProfile` (OneToOne), `Subscription` (FK), `APIUsage` (FK, nullable for anonymous) models
- `UserProfile` auto-created on user save via Django signal
- `subscription_tier`, `is_pro`, `is_premium` computed properties on `UserProfile`
- Tier-aware daily limits (`FREE: 100`, `PRO: 1000`, `PREMIUM: 10000`)
- Router endpoints:
  - `POST /api/v1/users/register/`
  - `POST /api/v1/users/verify-email/`
  - `POST /api/v1/users/password-reset/`
  - `POST /api/v1/users/password-reset-confirm/`
  - `GET/PATCH /api/v1/users/profile/me/`
  - `GET /api/v1/users/subscriptions/current/`
  - `GET /api/v1/users/usage/stats/`

**Key Files**:
- `apps/users/models.py` — UserProfile, Subscription, APIUsage, SubscriptionTier
- `apps/users/views.py` — Registration, email/password, profile, subscription, usage viewsets
- `apps/users/serializers.py` — All serializers with validation
- `apps/users/middleware.py` — APIUsageMiddleware
- `apps/users/signals.py` — Auto-create UserProfile
- `apps/users/admin.py` — UserAdmin with inlines, SubscriptionAdmin with bulk actions

---

### version 0.0.8: Advanced Technical Indicators ✓
**Objective**: Expand analytical capabilities

**Implemented Indicators**:
- **Bollinger Bands** (`BBANDS`) with upper/middle/lower levels
- **Moving Averages**: `SMA` and `EMA` (multi-period)
- **Stochastic Oscillator** (`STOCH`)
- **ADX** (trend strength with `plus_di` / `minus_di`)
- **OBV** (On-Balance Volume)
- **Fibonacci Retracement** (`FIB_RET`) levels

**Features**:
- Configurable indicator parameters via `/api/v1/indicators/recalculate/`
- Historical indicator values query support via date filters
- Indicator comparison API: `/api/v1/indicators/compare/`
- Specialized indicator endpoints for trend/overbought/oversold and Fibonacci levels

**Key Files**:
- `apps/analytics/tasks.py` - Indicator calculation tasks (including Fibonacci)
- `apps/analytics/views.py` - Indicator compare/recalculate/Fibonacci endpoints
- `apps/analytics/models.py` - Unified `TechnicalIndicator` storage model
---

### version 0.0.9: Stock Screeners & Alerts ✓
**Objective**: Automated screening and notification system

**Implemented Features**:
- Pre-built screener endpoints:
  - Overbought/Oversold stocks
  - Breakout candidates
  - High volume stocks
  - Trend reversal signals
- Saved screener templates API
- Alert rule management API (price + indicator conditions)
- Alert event history API
- Multi-channel notifications:
  - Email
  - SMS via webhook provider integration hook
  - WebSocket push notifications
- Celery periodic alert checks with configurable cooldown

**Technical Implementation**:
- `AlertRule`, `AlertEvent`, `ScreenerTemplate` models
- Celery tasks: `check_alert_rules`, `send_alert_notifications`
- WebSocket endpoint: `/ws/alerts/` (Channels + Redis channel layer)
- Router endpoints:
  - `/api/v1/screeners/`
  - `/api/v1/screener-templates/`
  - `/api/v1/alerts/`
  - `/api/v1/alert-events/`

**Key Files**:
- `apps/analytics/models.py` - version 0.0.9 data models
- `apps/analytics/tasks.py` - Alert evaluation and dispatch tasks
- `apps/analytics/views.py` - Screener and alert APIs
- `apps/analytics/consumers.py` - WebSocket alert consumer
- `config/asgi.py` - ASGI protocol routing for HTTP + WebSocket
- `config/settings/base.py` - Channels and periodic schedule config

---

### version 0.0.10: Advanced Technical Indicators Expansion ✓
**Objective**: Extend the analytics engine with signal detection across moving averages, Bollinger Bands, volume-price relationships, and momentum factors

**Implemented Signals** (`SignalEvent` model):

| Category | Signal Type | Trigger Condition |
|---|---|---|
| Moving Averages | `GOLDEN_CROSS` | MA5 crosses above MA20 |
| Moving Averages | `DEATH_CROSS` | MA5 crosses below MA20 |
| Moving Averages | `MA_BULL_ALIGN` | MA5 > MA10 > MA20 > MA60 |
| Moving Averages | `MA_BEAR_ALIGN` | MA5 < MA10 < MA20 < MA60 |
| Bollinger Bands | `BB_SQUEEZE` | Bandwidth < 5% (volatility compression) |
| Bollinger Bands | `BB_BREAKOUT_UP` | Close above upper band |
| Bollinger Bands | `BB_BREAKOUT_DOWN` | Close below lower band |
| Bollinger Bands | `BB_RSI_OVERBOUGHT` | Close ≥ upper band×0.98 AND RSI > 70 |
| Bollinger Bands | `BB_RSI_OVERSOLD` | Close ≤ lower band×1.02 AND RSI < 30 |
| Volume | `VOLUME_SPIKE` | Volume > 2× 20-day average |
| Volume | `VOLUME_PRICE_DIVERGENCE` | ≥3% price move with opposing OBV trend |
| Momentum | `MOMENTUM_UP_5D` | 5-day return > +5% |
| Momentum | `MOMENTUM_DOWN_5D` | 5-day return < -5% |
| Momentum | `HIGH_RS_SCORE` | Top 20% by 20-day return (cross-asset) |
| Reversal | `OVERSOLD_COMBINATION` | RSI < 30 + near lower BB + volume contraction |

**New Indicator Values** (stored as `TechnicalIndicator`):
- `MOM_5D`, `MOM_10D`, `MOM_20D` — period return as a decimal fraction
- `RS_SCORE` — normalized relative strength rank (0–1) vs. all assets

**API Endpoints**:
- `GET /api/v1/signals/` — paginated list, filterable by `asset` and `signal_type`
- `GET /api/v1/signals/recent/?days=7` — signals from the last N days
- `POST /api/v1/signals/recalculate/` — queue full signal recalculation (HTTP 202)

**Technical Implementation**:
- `SignalEvent` model with 15 `SignalType` choices, `unique_together` on `(asset, timestamp, signal_type)`
- 6 new Celery tasks: `calculate_ma_signals_for_asset`, `calculate_bollinger_signals_for_asset`, `calculate_volume_signals_for_asset`, `calculate_momentum_signals_for_asset`, `calculate_reversal_signals_for_asset`, `calculate_rs_scores_for_all_assets`
- Batch dispatcher: `calculate_signals_for_all_assets`
- Celery Beat: daily at 16:00 UTC (after A-share market close at 15:00 CST)

**Key Files**:
- `apps/analytics/models.py` — `SignalEvent` model
- `apps/analytics/tasks.py` — all version 0.0.10 signal tasks
- `apps/analytics/views.py` — `SignalEventViewSet` with `recent` and `recalculate` actions
- `apps/analytics/serializers.py` — `SignalEventSerializer`
- `apps/analytics/migrations/0004_phase10_signal_events.py` — migration
- `config/settings/base.py` — `calculate-signals-daily` Celery Beat schedule

---

### version 0.0.11: Multi-Factor Alpha Model ✓
**Objective**: Build a configurable multi-factor stock ranking engine for bottom-candidate screening

**Implemented Features**:
- New `factors` app with dedicated data models for factor ingestion and scoring
- Fundamental snapshot model (`PE`, `PB`, `ROE`, `ROE QoQ`)
- Capital flow snapshot model (northbound net flow, main-force net flow, margin-balance change)
- Composite `FactorScore` model storing normalized component scores and bottom-probability output
- Bottom-candidate screener endpoint:
  - `GET /api/v1/screener/bottom-candidates/`
  - `POST /api/v1/screener/bottom-candidates/recalculate/`
- Parameterized weighted scoring (`financial_weight`, `flow_weight`, `technical_weight`)
- Admin support for all version 0.0.11 models

**Scoring Engine**:
- Financial factors:
  - PE percentile score (lower PE -> higher score)
  - PB percentile score (lower PB -> higher score)
  - ROE trend score from `roe_qoq`
- Capital-flow factors:
  - northbound net flow rank
  - main-force net flow rank
  - margin-balance change rank
- Technical factors:
  - RSI oversold signal
  - close near Bollinger lower band
  - version 0.0.10 oversold signal (`OVERSOLD_COMBINATION`)
- Weighted aggregation into `composite_score` and `bottom_probability_score`

**Data / API Models**:
- `FundamentalFactorSnapshot`
- `CapitalFlowSnapshot`
- `FactorScore`

**Key Files**:
- `apps/factors/models.py` — factor data and scoring models
- `apps/factors/tasks.py` — daily factor scoring task
- `apps/factors/views.py` — factor ingestion and bottom-candidate APIs
- `apps/factors/serializers.py` — factor serializers
- `apps/factors/tests.py` — version 0.0.11 test coverage
- `apps/factors/migrations/0001_initial.py` — initial migration

---

### version 0.0.12: Macro & Event-Driven Context Engine ✓
**Objective**: Introduce macro context and event-driven overlays as a global adjustment layer for model scoring

**Implemented Features**:
- New `macro` app with three core models:
  - `MacroSnapshot` for macro time-series snapshots
  - `MarketContext` for active environment labels
  - `EventImpactStat` for historical tagged-event return statistics
- Macro API endpoints:
  - `GET/POST /api/v1/macro/snapshots/`
  - `POST /api/v1/macro/snapshots/sync/`
  - `GET/POST /api/v1/macro/contexts/`
  - `GET /api/v1/macro/contexts/current/`
  - `POST /api/v1/macro/contexts/refresh/`
  - `GET/POST /api/v1/macro/event-impacts/`
- Macro phase inference task using PMI + yield-curve logic:
  - `RECOVERY`, `OVERHEAT`, `STAGFLATION`, `RECESSION`
- Context-aware weight service for downstream ranking models
- version 0.0.11 integration:
  - Bottom-candidates endpoint now accepts `macro_context` and `event_tag`
  - Recalculate endpoint applies context-adjusted weights before queuing scoring
  - List endpoint returns `adjusted_bottom_probability_score` and `context_applied`
- Monthly macro sync scheduled via Celery Beat

**Key Files**:
- `apps/macro/models.py` — MacroSnapshot, MarketContext, EventImpactStat
- `apps/macro/services.py` — macro/event weight adjustment logic
- `apps/macro/tasks.py` — monthly sync and context refresh tasks
- `apps/macro/views.py` — macro APIs and custom actions
- `apps/macro/serializers.py` — macro serializers
- `apps/macro/tests.py` — version 0.0.12 test coverage
- `apps/macro/migrations/0001_initial.py` — initial migration

---

### version 0.0.13: NLP Sentiment & News Intelligence ✓
**Objective**: Add sentiment intelligence from news and concept heat signals, and feed sentiment into multi-factor ranking

**Implemented Features**:
- New `sentiment` app with core models:
  - `NewsArticle` for finance news ingestion
  - `SentimentScore` for article-level and aggregated sentiment
  - `ConceptHeat` for concept/theme popularity tracking
- Sentiment API endpoints:
  - `GET /api/v1/sentiment/`
  - `GET /api/v1/sentiment/latest/`
  - `POST /api/v1/sentiment/recalculate/`
  - `GET /api/v1/sentiment/news/`
  - `POST /api/v1/sentiment/news/ingest/`
  - `GET /api/v1/sentiment/concepts/`
  - `GET /api/v1/sentiment/concepts/top/`
- Daily sentiment pipeline tasks:
  - News ingest task (`ingest_latest_news`)
  - Daily article/asset/market sentiment scoring (`calculate_daily_sentiment`)
  - Concept heat computation (`calculate_concept_heat`)
  - Unified daily dispatcher (`run_daily_sentiment_pipeline`)
- Sentiment factor integration into version 0.0.11:
  - `FactorScore` now stores `sentiment_score` and `sentiment_weight`
  - Factor scoring task supports `sentiment_weight`
  - Asset 7-day sentiment aggregate participates in composite score
- Daily Celery Beat schedule for sentiment pipeline

**Key Files**:
- `apps/sentiment/models.py` — NewsArticle, SentimentScore, ConceptHeat
- `apps/sentiment/tasks.py` — sentiment scoring and concept heat tasks
- `apps/sentiment/views.py` — sentiment/news/concept API viewsets
- `apps/sentiment/serializers.py` — sentiment serializers
- `apps/sentiment/tests.py` — version 0.0.13 test coverage
- `apps/sentiment/migrations/0001_initial.py` — initial migration
- `apps/factors/tasks.py` — sentiment factor integration in composite scoring
- `apps/factors/models.py` — sentiment fields on FactorScore

---

### version 0.0.14: ML Prediction Engine ✓
**Objective**: Build the core prediction engine to estimate direction probabilities for each stock



**Implemented Features**:

**Tier 1: Heuristic Baseline** (original version 0.0.14):
  - New `prediction` app with core models:
    - `ModelVersion` for prediction model registry and version lifecycle
    - `PredictionResult` for daily stock-level probability snapshots by horizon
  - Heuristic Prediction API endpoints:
    - `GET /api/v1/prediction/{stock_code}/`
    - `POST /api/v1/prediction/batch/`
    - `POST /api/v1/prediction/recalculate/`
    - `GET /api/v1/prediction-model-versions/`
  - Multi-horizon outputs for 3/7/30-day direction probabilities:
    - `up`, `flat`, `down`, `confidence`, and `predicted_label`
  - Feature fusion from prior phases (10-13):
    - technical momentum/relative-strength signals
    - multi-factor bottom-probability signals
    - macro context tags
    - sentiment scores
  - Baseline ensemble training workflow:
    - weekly model version refresh task (Saturday 04:00 UTC)
    - daily prediction generation task (18:00 UTC)
    - macro context/event tag override support

**Tier 2: LightGBM Parallel ML Engine** (version 0.0.14 Extension):
  - Production-ready parallel prediction pipeline alongside heuristic baseline
  - Dual-model architecture for side-by-side accuracy comparison and gradual adoption
  - Core Models:
    - `LightGBMModelArtifact` — model persistence registry with version tracking, metrics, and feature importance
    - `LightGBMPrediction` — daily predictions with raw and calibrated probability scores
    - `EnsembleWeightSnapshot` — historical weight tracking for accuracy-weighted ensemble
  - LightGBM Training Pipeline:
    - Automatic feature extraction from Phases 10–13 infrastructure (technical, factors, macro, sentiment)
    - StandardScaler normalization + CalibratedClassifierCV (Platt scaling) for probability calibration
    - Weekly retraining task (Sunday 05:00 UTC, offset from heuristic)
    - Disk-based model persistence (pickle + JSON) under `/models/lightgbm/`
  - LightGBM Inference & API:
    - Per-asset async inference (`generate_lightgbm_prediction_for_asset`)
    - Batch daily predictions (`generate_lightgbm_predictions_for_date`)
    - Three new endpoints:
      - `POST /api/v1/lightgbm-predictions/train/` — Queue model retraining
      - `POST /api/v1/lightgbm-predictions/recalculate/` — Queue daily prediction generation
      - `POST /api/v1/lightgbm-predictions/batch/` — Batch predictions for multiple stocks
      - `GET /api/v1/lightgbm-predictions/{stock_code}/` — Single-stock LightGBM predictions
      - `GET /api/v1/lightgbm-models/` — Model artifact registry (read-only)
      - `GET /api/v1/ensemble-weights/` — Ensemble weight history
  - Independent from heuristic: both systems run in parallel with own DB tables and schedules
  - Enables production risk-mitigation: fallback to heuristic if LightGBM underperforms

**Key Files**:
  - `apps/prediction/models.py` — `ModelVersion`, `PredictionResult` (heuristic baseline)
  - `apps/prediction/models_lightgbm.py` — `LightGBMModelArtifact`, `LightGBMPrediction`, `EnsembleWeightSnapshot`
  - `apps/prediction/tasks.py` — heuristic training and prediction generation
  - `apps/prediction/tasks_lightgbm.py` — LightGBM training, inference, and model persistence
  - `apps/prediction/views.py` — heuristic prediction endpoints
  - `apps/prediction/views_lightgbm.py` — LightGBM prediction endpoints
  - `apps/prediction/serializers.py` — heuristic serializers
  - `apps/prediction/serializers_lightgbm.py` — LightGBM serializers
  - `apps/prediction/tests.py` — version 0.0.14 heuristic tests (5 tests, all passing)
  - `apps/prediction/tests_lightgbm.py` — version 0.0.14 LightGBM tests (7 tests, all passing)
  - `apps/prediction/migrations/0001_initial.py` — initial heuristic schema
  - `apps/prediction/migrations/0002_ensembleweightsnapshot_lightgbmmodelartifact_and_more.py` — LightGBM schema (applied)

**Test Coverage**: 12/12 tests passing (100%)
  - Heuristic: 5/5 tests ✓
  - LightGBM: 7/7 tests ✓ (including routing fix for train endpoint)

### version 0.0.15: Backtesting & Strategy Validation ✓
**Objective**: Validate strategy effectiveness and prediction quality with historical simulation

**Implemented Features**:
- New `backtest` app with execution and trade log models:
  - `BacktestRun` for strategy configuration, async lifecycle, and performance metrics
  - `BacktestTrade` for per-trade records (BUY/SELL, fee, slippage, realized PnL)
- Async backtest engine task:
  - `run_backtest(backtest_run_id)` processes historical OHLCV data by date range
  - Supports strategy modes: `PREDICTION_THRESHOLD`, `BOTTOM_CANDIDATE`, `MACRO_ROTATION`
  - Simulates fee/slippage and computes `total_return`, `annualized_return`, `max_drawdown`, `sharpe_ratio`, `win_rate`
- Backtest API endpoints:
  - `GET /api/v1/backtest/`
  - `POST /api/v1/backtest/` (create run and queue async execution)
  - `GET /api/v1/backtest/{id}/`
  - `POST /api/v1/backtest/{id}/rerun/`
  - `GET /api/v1/backtest/{id}/trades/`
  - `GET /api/v1/backtest-trades/?backtest_run={id}`
- Admin support for run monitoring and trade inspection

**Key Files**:
- `apps/backtest/models.py` — `BacktestRun`, `BacktestTrade`
- `apps/backtest/tasks.py` — async simulation and performance calculation pipeline
- `apps/backtest/views.py` — backtest run/trade APIs with rerun and trade-list actions
- `apps/backtest/serializers.py` — run/trade serializers and validations
- `apps/backtest/admin.py` — admin registrations
- `apps/backtest/tests.py` — version 0.0.15 test coverage
- `apps/backtest/migrations/0001_initial.py` — initial migration

**Test Coverage**: 4/4 tests passing (100%)

---

### version 0.0.16: API Documentation & Developer Portal ✓
**Objective**: Complete API documentation ecosystem and developer portal

**Implemented Features**:
- **OpenAPI 3.0 schema auto-generation** via `drf-spectacular`:
  - Machine-readable schema: `GET /api/v1/schema/`
  - Interactive Swagger UI: `GET /api/v1/schema/swagger-ui/`
  - ReDoc interface: `GET /api/v1/schema/redoc/`
- **API Key management** — new `developer` app:
  - `DeveloperAPIKey` model with SHA-256-hashed keys (plain text never stored)
  - Key format: `fa-<40 hex chars>` with `key_prefix` stored for display
  - Sandbox mode flag for read-only / synthetic-data workflows
  - Configurable expiry (`expires_at`)
  - `POST /api/v1/developer/keys/` — mint new key (raw key returned once)
  - `GET /api/v1/developer/keys/` — list own keys
  - `DELETE /api/v1/developer/keys/{id}/` — soft-revoke (sets `is_active=False`)
  - `POST /api/v1/developer/keys/{id}/rotate/` — revoke + mint replacement
- **`X-API-Key` authentication** — `APIKeyAuthentication` class added to `DEFAULT_AUTHENTICATION_CLASSES`; works alongside JWT
- **API Changelog** — `ChangelogEntry` model:
  - `GET /api/v1/developer/changelog/` — public endpoint, no auth required
  - Filterable by `?version=`, `?change_type=`, `?is_breaking=true`
  - Change types: ADDED / CHANGED / DEPRECATED / REMOVED / FIXED / SECURITY
- **`SPECTACULAR_SETTINGS`** configured with title, description, version, Swagger UI persistence, and rate-limit documentation table

**Key Files**:
- `apps/developer/models.py` — `DeveloperAPIKey`, `ChangelogEntry`
- `apps/developer/authentication.py` — `APIKeyAuthentication`
- `apps/developer/views.py` — `DeveloperAPIKeyViewSet`, `ChangelogEntryViewSet`
- `apps/developer/serializers.py` — key create/read serializers
- `apps/developer/admin.py` — admin registrations
- `apps/developer/tests.py` — version 0.0.16 test coverage
- `apps/developer/migrations/0001_initial.py` — initial migration
- `config/settings/base.py` — `SPECTACULAR_SETTINGS`, `APIKeyAuthentication` in auth classes
- `config/urls.py` — schema + developer portal routes
- `requirements/base.txt` — `drf-spectacular==0.27.2`

**Test Coverage**: 16/16 tests passing (100%)


### version 0.1.0: Frontend Dashboard ✓
**Objective**: 面向用户的可视化操作界面

**Implemented Features**:
- New `frontend/` app bootstrapped with **Vite + React + TypeScript**
- Dashboard routing and application shell with 6 pages:
  - Dashboard
  - Stock Detail
  - Bottom Screener
  - Macro Context
  - Backtest Workbench
  - Alert Center
- Charting integration:
  - **TradingView Lightweight Charts** for candlestick rendering
  - **Recharts** for multi-horizon probability visualization
- Real-time alert stream hook using **WebSocket** (`/ws/alerts/` configurable by env)
- API utility layer with JWT + API Key header support (`Authorization`, `X-API-Key`)
**Key Files**:
- `frontend/src/App.tsx` — route definitions and page composition
- `frontend/src/components/layout/AppShell.tsx` — nav shell
- `frontend/src/components/charts/CandlestickChart.tsx` — K-line chart
- `frontend/src/components/charts/ProbabilityChart.tsx` — prediction probability chart
- `frontend/src/pages/*.tsx` — page implementations
- `frontend/src/hooks/useAlertsSocket.ts` — real-time alert stream
- `frontend/src/lib/api.ts` — API client with auth headers
- `frontend/src/index.css` — responsive dashboard styling
- `frontend/package.json` — dependencies and HGFS-safe scripts

**Run Frontend**:
```bash
cd frontend
npm install --no-bin-links
npm run dev
```

Open: `http://localhost:5173`

### version 0.1.1: Real Backend Data Wiring ✓
  - Dashboard metrics now fetched from live endpoints (`macro`, `concept heat`, `signals`, `alert-events`)
  - Stock Detail page now fetches live asset lookup, OHLCV candles, prediction probabilities, and sentiment scores
  - Bottom Screener now renders live `/screener/bottom-candidates/` results
  - Macro Context page now renders live `/macro/contexts/` entries
  - Backtest Workbench now renders live `/backtest/` runs
  - Alert Center now combines live WebSocket stream + `/alert-events/` API history

### version 0.1.2: Dynamic Series Completion ✓
  - Dashboard probability chart now uses live prediction series (top screener symbol fallback)
  - Stock Detail removed static fallback chart/probability data; now displays API-driven series only
  - Chart components now show explicit empty-state messages when API data is unavailable

### version 0.1.3: UX & Performance Enhancements ✓
  - Route-level code splitting via lazy-loaded page modules + `Suspense` fallback
  - Lightweight Auth Settings panel in sidebar:
    - JWT token input
    - API key input
    - Persistence mode selector (`local`, `session`, `none`)
    - Save/Clear controls wired to storage
  - Additional live dashboard aggregates:
    - completed backtest runs count
    - average bottom-candidate probability from screener endpoint
- Mobile-responsive layout and custom visual theme
- HGFS-compatible npm scripts (no `.bin` symlink dependency)

### version 0.1.4 Data Sync & Coverage Recovery ✓
**Objective**: 恢复核心行情数据覆盖并完成阶段性全链路数据校准

**Implemented Features**:
- Added and verified TuShare-based incremental/backfill workflow in local Docker runtime
- Re-synced representative core symbols (e.g. `600519`, `000001`, `300750`) and completed broader constituent recovery runs
- Introduced throttled batch resume strategy to handle provider rate limits and improve long-run sync stability
- Re-ran model data dependencies for current date:
  - technical/signal recalculation
  - macro snapshot/context refresh
  - sentiment/concept-heat recalculation
  - factor score regeneration
  - prediction result regeneration
- Performed post-sync consistency checks for OHLCV/assets coverage and key feature tables

**Key Files / Modules**:
- `apps/markets/tasks.py` — market/asset history synchronization pipeline
- `apps/analytics/tasks.py` — technical indicator and signal recalculation tasks
- `apps/macro/tasks.py` — macro snapshot and context refresh tasks
- `apps/sentiment/tasks.py` — sentiment and concept heat recalculation
- `apps/factors/tasks.py` — factor score regeneration
- `apps/prediction/tasks.py` — prediction regeneration

### version 0.1.5 Dashboard, Stock, Macro, Alerts UX Fixes ✓
**Objective**: 修复前端关键页面的可用性与数据可读性问题

**Implemented Features**:
- Dashboard UX updates:
  - moved `Top N Bottom Candidates` table above charts
  - replaced chart section title with formal title: `Top Candidate Probability Outlook`
- Stock Detail data fixes:
  - expanded OHLCV loading from single-page fetch to paginated aggregation for long-history K-line rendering
  - increased frontend OHLCV request limit for deeper historical chart coverage
- Macro Context display improvements:
  - upgraded list layout to table with explicit headers (`Macro Phase`, `Event Tag`, `Status`)
  - changed ambiguous `N/A` event display to explicit `No event tag configured`
- Alert Center connection-state UX:
  - introduced reconnecting state and exponential backoff reconnect behavior
  - status now distinguishes `Connected` / `Reconnecting...` / `Disconnected`

**Key Files**:
- `frontend/src/pages/DashboardPage.tsx`
- `frontend/src/pages/StockDetailPage.tsx`
- `frontend/src/pages/MacroContextPage.tsx`
- `frontend/src/pages/AlertCenterPage.tsx`
- `frontend/src/hooks/useAlertsSocket.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/i18n.tsx`

### version 0.1.6 Realtime Auth & Sentiment Availability Hardening ✓
**Objective**: 提升实时告警鉴权稳定性并消除个股情绪分缺失

**Implemented Features**:
- WebSocket auth compatibility enhancement:
  - backend alerts consumer now supports JWT query-token authentication fallback for `/ws/alerts/`
  - frontend socket URL now appends JWT token when available
- Sentiment data availability enhancement:
  - `calculate_daily_sentiment` now creates neutral fallback `ASSET_7D` entries for active assets without article aggregation
  - prevents stock detail sentiment from showing persistent `N/A` when source article coverage is sparse
- End-to-end checks completed:
  - frontend production build passed
  - Django system checks passed
  - target APIs returned `200` for OHLCV / sentiment / macro-current smoke checks

**Key Files**:
- `apps/analytics/consumers.py`
- `apps/sentiment/tasks.py`
- `frontend/src/hooks/useAlertsSocket.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/pages/AlertCenterPage.tsx`

---

### version 0.1.7: Trade-Decision Engine, LightGBM Parity, and Dashboard Consolidation ✓
**Objective**: 把概率预测升级为可执行的交易决策，并把 Heuristic / LightGBM 的对比收敛到统一的日常操作界面

**Implemented Features**:
- Added a shared odds and trade-decision engine:
  - introduced `apps/prediction/odds.py` to estimate `target_price`, `stop_loss_price`, `risk_reward_ratio`, `trade_score`, and `suggested`
  - derived trade levels from OHLCV history, Bollinger Bands, SMA support, and simple resistance rounding rules
  - stabilized `trade_score` so near-1.0 `p_up` values do not create unusable rankings
- Extended heuristic prediction outputs end to end:
  - added persistent trade-decision fields to `PredictionResult`
  - added migration `apps/prediction/migrations/0004_predictionresult_trade_decision_fields.py`
  - integrated trade-decision generation into heuristic prediction tasks and stock/batch APIs
  - extended bottom-candidate screener responses to expose and sort by `trade_score` and `risk_reward_ratio`
- Extended LightGBM prediction outputs to the same contract:
  - added persistent trade-decision fields to `LightGBMPrediction`
  - added migration `apps/prediction/migrations/0005_lightgbmprediction_trade_decision_fields.py`
  - reused the same odds engine in `tasks_lightgbm.py` so heuristic and LightGBM setup quality remain directly comparable
  - updated LightGBM serializers and stock/batch endpoints with the same additive fields
- Consolidated the frontend stock-selection workflow around the dashboard:
  - stock detail now compares heuristic and LightGBM setup quality by horizon alongside probability comparison
  - dashboard now surfaces top-candidate comparison with model-family and suggested-only filtering
  - added a new all-stocks indicator board backed by a composite `dashboard/stocks` API
  - removed the dedicated screener page from routing and navigation
- Added the new dashboard aggregation API:
  - introduced `DashboardStockViewSet` and `DashboardStockRowSerializer`
  - new endpoint returns one row per asset with factor scores, key indicators, sentiment, and heuristic/LightGBM trade summaries

**Validation**:
- Applied both prediction migrations successfully.
- Backend tests passed:
  - `apps.prediction.tests` and `apps.factors.tests`
  - `apps.prediction.tests_lightgbm` with 12 tests passing
  - `apps.analytics.tests` with 22 tests passing
- Frontend production build passed after the dashboard consolidation.
- Regenerated live heuristic predictions for `2026-04-15` so the new trade fields are populated on current rows.
- Regenerated live LightGBM predictions for `2026-04-15` with 600 asset-horizon rows updated.
- Live smoke checks confirmed:
  - `/api/v1/prediction/600519/?date=2026-04-15` returns target/stop/R:R/trade-score/suggested fields
  - `/api/v1/lightgbm-predictions/600519/?date=2026-04-15` and the LightGBM batch endpoint return the same trade-decision fields
  - `/api/v1/dashboard/stocks/?prediction_horizon=7&ordering=-composite_score` returns 300 mixed-source dashboard rows with factor, indicator, sentiment, and dual-model trade fields

**Current Notes**:
- The dashboard is now the primary stock-selection surface; the dedicated screener page was removed from the frontend.
- 30-day LightGBM training still depends on a deeper historical factor-score and sentiment backfill window.
- Frontend build still warns that `StockDetailPage.tsx` exceeds the default chunk-size warning threshold.

**Key Files**:
- `apps/prediction/odds.py`
- `apps/prediction/models.py`
- `apps/prediction/models_lightgbm.py`
- `apps/prediction/tasks.py`
- `apps/prediction/tasks_lightgbm.py`
- `apps/prediction/views.py`
- `apps/prediction/views_lightgbm.py`
- `apps/factors/views.py`
- `apps/analytics/views.py`
- `apps/analytics/serializers.py`
- `frontend/src/pages/DashboardPage.tsx`
- `frontend/src/pages/StockDetailPage.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/App.tsx`
- `frontend/src/components/layout/AppShell.tsx`
- `frontend/src/i18n.tsx`

