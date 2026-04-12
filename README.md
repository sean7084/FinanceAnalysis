# FinanceAnalysis - Bilingual Financial Data SaaS Platform

A production-ready Django-based SaaS platform for analyzing Chinese financial markets with bilingual (English/Chinese) support, real-time data ingestion, technical analysis, and RESTful API.

## 🚀 Project Overview

This platform provides comprehensive financial data analysis for Chinese stock markets (CSI 300 Index), featuring:

- **Real-time Data Ingestion**: Automated daily synchronization of stock data using AkShare
- **Technical Analysis**: RSI, MACD, and extensible indicator calculations using TA-Lib
- **RESTful API**: Secure, rate-limited API with JWT authentication
- **Bilingual Support**: Full English/Chinese translation support
- **SaaS-Ready**: Multi-tier rate limiting and caching for scalability
- **Production Infrastructure**: Fully containerized with Docker Compose

## 🛠️ Technology Stack

### Backend
- **Django 6.0.1** - Web framework
- **Django REST Framework 3.15.1** - API framework
- **PostgreSQL 15** - Primary database
- **Redis 7** - Caching and message broker
- **Celery 5.4.0** - Asynchronous task processing
- **Celery Beat** - Periodic task scheduler

### Data & Analysis
- **AkShare** - Chinese financial data provider
- **Pandas 2.2.2** - Data manipulation
- **TA-Lib** - Technical analysis library
- **NumPy** - Numerical computing

### Infrastructure
- **Docker & Docker Compose** - Containerization
- **Nginx** - Reverse proxy (production-ready)

### Additional Tools
- **django-modeltranslation 0.18.12** - Model field translation
- **django-filter 24.2** - Advanced API filtering
- **djangorestframework-simplejwt 5.3.1** - JWT authentication

## ✅ Completed Phases

### Phase 1: Foundation & Docker Setup ✓
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

### Phase 2: Bilingual Data Modeling ✓
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

### Phase 3: Data Ingestion Engine ✓
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

### Phase 4: Financial Analysis & Indicators ✓
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

### Phase 5: REST API with Caching ✓
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

### Phase 6: Production Readiness & SaaS Features ✓
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

### Phase 7: User Management & Subscriptions ✓
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

### Phase 8: Advanced Technical Indicators ✓
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

### Phase 9: Stock Screeners & Alerts ✓
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
- `apps/analytics/models.py` - Phase 9 data models
- `apps/analytics/tasks.py` - Alert evaluation and dispatch tasks
- `apps/analytics/views.py` - Screener and alert APIs
- `apps/analytics/consumers.py` - WebSocket alert consumer
- `config/asgi.py` - ASGI protocol routing for HTTP + WebSocket
- `config/settings/base.py` - Channels and periodic schedule config

---

### Phase 10: Advanced Technical Indicators Expansion ✓
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
- `apps/analytics/tasks.py` — all Phase 10 signal tasks
- `apps/analytics/views.py` — `SignalEventViewSet` with `recent` and `recalculate` actions
- `apps/analytics/serializers.py` — `SignalEventSerializer`
- `apps/analytics/migrations/0004_phase10_signal_events.py` — migration
- `config/settings/base.py` — `calculate-signals-daily` Celery Beat schedule

---

### Phase 11: Multi-Factor Alpha Model ✓
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
- Admin support for all Phase 11 models

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
  - Phase 10 oversold signal (`OVERSOLD_COMBINATION`)
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
- `apps/factors/tests.py` — Phase 11 test coverage
- `apps/factors/migrations/0001_initial.py` — initial migration

---

### Phase 12: Macro & Event-Driven Context Engine ✓
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
- Phase 11 integration:
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
- `apps/macro/tests.py` — Phase 12 test coverage
- `apps/macro/migrations/0001_initial.py` — initial migration

---

### Phase 13: NLP Sentiment & News Intelligence ✓
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
- Sentiment factor integration into Phase 11:
  - `FactorScore` now stores `sentiment_score` and `sentiment_weight`
  - Factor scoring task supports `sentiment_weight`
  - Asset 7-day sentiment aggregate participates in composite score
- Daily Celery Beat schedule for sentiment pipeline

**Key Files**:
- `apps/sentiment/models.py` — NewsArticle, SentimentScore, ConceptHeat
- `apps/sentiment/tasks.py` — sentiment scoring and concept heat tasks
- `apps/sentiment/views.py` — sentiment/news/concept API viewsets
- `apps/sentiment/serializers.py` — sentiment serializers
- `apps/sentiment/tests.py` — Phase 13 test coverage
- `apps/sentiment/migrations/0001_initial.py` — initial migration
- `apps/factors/tasks.py` — sentiment factor integration in composite scoring
- `apps/factors/models.py` — sentiment fields on FactorScore

---

### Phase 14: ML Prediction Engine ✓
**Objective**: Build the core prediction engine to estimate direction probabilities for each stock



**Implemented Features**:

**Tier 1: Heuristic Baseline** (original Phase 14):
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

**Tier 2: LightGBM Parallel ML Engine** (Phase 14 Extension):
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
  - `apps/prediction/tests.py` — Phase 14 heuristic tests (5 tests, all passing)
  - `apps/prediction/tests_lightgbm.py` — Phase 14 LightGBM tests (7 tests, all passing)
  - `apps/prediction/migrations/0001_initial.py` — initial heuristic schema
  - `apps/prediction/migrations/0002_ensembleweightsnapshot_lightgbmmodelartifact_and_more.py` — LightGBM schema (applied)

**Test Coverage**: 12/12 tests passing (100%)
  - Heuristic: 5/5 tests ✓
  - LightGBM: 7/7 tests ✓ (including routing fix for train endpoint)

### Phase 15: Backtesting & Strategy Validation ✓
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
- `apps/backtest/tests.py` — Phase 15 test coverage
- `apps/backtest/migrations/0001_initial.py` — initial migration

**Test Coverage**: 4/4 tests passing (100%)
---

## 📊 Current System Status

### Data Metrics
- **Markets**: 3 (SSE, SZSE, BSE)
- **Assets**: 334 CSI 300 stocks
- **OHLCV Records**: ~100,000+ daily price points
- **Technical Indicators**: RSI, MACD, BBANDS, SMA, EMA, STOCH, ADX, OBV, FIB_RET, MOM_5D, MOM_10D, MOM_20D, RS_SCORE
- **Signal Events**: 15 signal types (MA, Bollinger, Volume, Momentum, Reversal)
- **Sentiment Analytics**: article-level and 7-day aggregated sentiment + concept heat
- **Prediction Snapshots**: 3/7/30-day directional probabilities with confidence and model versioning
- **Backtest Engine**: async strategy simulation with trade logs and performance metrics

### API Endpoints
- **Markets API**: 2 endpoints (list, detail)
- **Assets API**: 2 endpoints + search/filter
- **OHLCV API**: 2 endpoints + date range filtering
- **Indicators API**: list/detail + compare/recalculate/fibonacci + ranking endpoints
- **Screeners API**: 4 pre-built screeners + screener templates
- **Alerts API**: alert rules + alert events
- **Signals API**: list/filter/recent/recalculate signal events
- **Factors API**: fundamentals, capital-flows, and bottom-candidates screener
- **Macro API**: snapshots, current context, event-impact statistics
- **Sentiment API**: news ingestion, sentiment scores, latest sentiment, concept heat ranking
- **Prediction API (Heuristic Baseline)**: single-stock prediction, batch prediction, model-version registry
- **Prediction API (LightGBM ML)**: single-stock predictions, batch predictions, model artifacts, ensemble weights tracking
- **Backtest API**: create/list/retrieve backtest runs, rerun action, and trade history endpoints
- **Users API**: register, verify-email, password-reset, profile, subscriptions, usage stats
- **Authentication**: 3 endpoints (token, refresh, verify)

### Performance
- Redis caching enabled (2-hour cache for static data)
- Database query optimization with `select_related()`
- Celery distributed task processing
- Docker containerization for scalability

---

## 🔮 Future Phases & Roadmap



### Phase 10: Advanced Technical Indicators Expansion (Completed)
Implemented and moved to the completed phases section.

---

### Phase 11: Multi-Factor Alpha Model (Completed)
Implemented and moved to the completed phases section.

---

### Phase 12: Macro & Event-Driven Context Engine (Completed)
Implemented and moved to the completed phases section.

---

### Phase 13: NLP Sentiment & News Intelligence (Completed)
Implemented and moved to the completed phases section.

---

### Phase 14: ML Prediction Engine (Completed)
Implemented and moved to the completed phases section.

---

### Phase 15: Backtesting & Strategy Validation (Completed)
Implemented and moved to the completed phases section.

---

### Phase 16: Frontend Dashboard
**Objective**: 面向用户的可视化操作界面

**核心页面**:
- **首页仪表盘**：当前宏观环境标签、板块热度、今日预测信号汇总
- **个股详情页**：K线图 + 技术指标 + 预测概率 + 情绪趋势
- **底部候选筛选器**：可配置权重的多因子筛选结果列表
- **宏观背景设置**：手动打标当前环境（周期、事件标签）
- **回测工作台**：策略参数配置 + 回测结果展示
- **告警中心**：价格/信号告警管理（对接 Phase 9）

**技术栈**:
- **React 18** + TypeScript
- **TradingView Lightweight Charts** — K线图
- **Recharts / ECharts** — 因子得分、概率可视化
- **WebSocket** — 实时价格更新
- **Tailwind CSS** + shadcn/ui
- **Vite** — 构建工具

---

### Phase 17: Mobile Application
**Objective**: iOS / Android 移动端应用

**核心功能**:
- 个股搜索与收藏
- 实时价格跟踪 + 预测概率查看
- 底部候选推送通知
- 移动端优化图表
- 离线数据缓存

**技术选型**:
- **React Native** — 跨平台首选（复用前端逻辑）
- 或 **Flutter** — 更高性能需求时备选

---

### Phase 18: Production Deployment
**Objective**: 云端部署 + CI/CD 全自动化

**基础设施**:
- 云服务商：AWS / 阿里云（国内用户推荐阿里云）
- 容器编排：Kubernetes (EKS/ACK) 或 Docker Compose（小规模）
- 托管数据库：RDS PostgreSQL
- 托管缓存：ElastiCache / Redis 企业版
- CDN：CloudFront / 阿里云 CDN
- 对象存储：S3 / OSS（模型文件、报告导出）

**DevOps**:
- CI/CD：GitHub Actions
- 蓝绿部署 / 滚动更新
- 监控：Prometheus + Grafana
- 错误追踪：Sentry
- 日志：ELK Stack 或阿里云日志服务
- SSL 证书自动续签

---

### Phase 19: API Documentation & Developer Portal
**Objective**: 完整的 API 文档和开发者生态

**功能**:
- OpenAPI / Swagger 自动生成文档
- 交互式 API 测试界面
- 代码示例（Python / JavaScript / cURL）
- API Key 管理
- 请求量统计与限流监控
- 开发者沙箱环境
- API 变更日志与版本管理

---

## 🚦 Getting Started

### Prerequisites
- Docker and Docker Compose
- Git

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/sean7084/FinanceAnalysis.git
   cd FinanceAnalysis
   ```

2. **Set up environment variables**:
   ```bash
   # Create .envs/.local file with:
   DATABASE_URL=postgres://postgres:postgres@postgres:5432/finance_analysis
   DJANGO_SECRET_KEY=your-secret-key-here
   DJANGO_READ_DOT_ENV_FILE=True
   DJANGO_DEBUG=True
   ```

3. **Build and start services**:
   ```bash
   docker-compose up --build -d
   ```

4. **Run migrations**:
   ```bash
   docker-compose exec django python manage.py migrate
   ```

5. **Create superuser**:
   ```bash
   docker-compose exec django python manage.py createsuperuser
   ```

6. **Import CSI 300 data**:
   ```bash
   docker-compose exec django python manage.py shell
   >>> from apps.markets.tasks import sync_daily_a_shares
   >>> sync_daily_a_shares.delay()
   >>> exit()
   ```

7. **Calculate indicators**:
   ```bash
   docker-compose exec django python manage.py shell
   >>> from apps.analytics.tasks import calculate_indicators_for_all_assets
   >>> calculate_indicators_for_all_assets.delay()
   >>> exit()
   ```

8. **Access the application**:
   - Admin: `http://localhost:8000/admin/`
   - API: `http://localhost:8000/api/v1/`

---

## 📡 API Usage Examples

### Obtain JWT Token
```bash
curl -X POST http://localhost:8000/api/v1/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "your_username", "password": "your_password"}'
```

### List Assets (Authenticated)
```bash
curl http://localhost:8000/api/v1/assets/ \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Get Top RSI Stocks
```bash
curl http://localhost:8000/api/v1/indicators/top_rsi/ \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Search Stocks
```bash
curl "http://localhost:8000/api/v1/assets/?search=平安" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

---

## 📁 Project Structure

```
FinanceAnalysis/
├── apps/
│   ├── analytics/          # Technical indicators
│   │   ├── models.py       # TechnicalIndicator model
│   │   ├── tasks.py        # Indicator calculations
│   │   ├── views.py        # Analytics API
│   │   └── serializers.py  # Indicator serializers
│   ├── core/               # Shared utilities
│   │   └── throttling.py   # Custom rate limiting
│   └── markets/            # Market data
│       ├── models.py       # Market, Asset, OHLCV models
│       ├── tasks.py        # Data ingestion
│       ├── views.py        # Market API
│       └── serializers.py  # Market serializers
├── compose/
│   └── local/
│       ├── django/         # Django Docker config
│       └── nginx/          # Nginx config (production)
├── config/
│   ├── settings/
│   │   ├── base.py        # Core settings
│   │   ├── local.py       # Development settings
│   │   └── production.py  # Production settings
│   ├── celery.py          # Celery configuration
│   └── urls.py            # URL routing
├── requirements/
│   ├── base.txt           # Core dependencies
│   ├── local.txt          # Development dependencies
│   └── production.txt     # Production dependencies
├── docker-compose.yml      # Service orchestration
└── manage.py              # Django management script
```

---

## 🤝 Contributing

This is a personal project, but contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

## 📄 License

This project is private and proprietary.

---

## 👤 Author

**Sean Liu**
- GitHub: [@sean7084](https://github.com/sean7084)

---

## 🙏 Acknowledgments

- **AkShare** - Chinese financial data provider
- **TA-Lib** - Technical analysis library
- **Django & DRF** - Web framework and API tools
- **Celery** - Distributed task queue

---


**Last Updated**: April 12, 2026  
**Version**: 1.1.0 (Phases 1-15 Complete)
