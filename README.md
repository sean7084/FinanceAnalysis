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

## 📊 Current System Status

### Data Metrics
- **Markets**: 3 (SSE, SZSE, BSE)
- **Assets**: 334 CSI 300 stocks
- **OHLCV Records**: ~100,000+ daily price points
- **Technical Indicators**: 668 (334 RSI + 334 MACD)

### API Endpoints
- **Markets API**: 2 endpoints (list, detail)
- **Assets API**: 2 endpoints + search/filter
- **OHLCV API**: 2 endpoints + date range filtering
- **Indicators API**: 4 endpoints (list, detail, top_rsi, bottom_rsi)
- **Authentication**: 3 endpoints (token, refresh, verify)

### Performance
- Redis caching enabled (2-hour cache for static data)
- Database query optimization with `select_related()`
- Celery distributed task processing
- Docker containerization for scalability

---

## 🔮 Future Phases & Roadmap

### Phase 7: User Management & Subscriptions
**Objective**: Multi-tenant user system with subscription tiers

**Planned Features**:
- User registration and profile management
- Subscription tier model (Free, Pro, Premium)
- Payment integration (Stripe/Alipay)
- User dashboard with usage analytics
- Admin panel for user management
- Email verification and password reset

**Technical Implementation**:
- Extend Django User model with subscription fields
- Create `Subscription` and `UserProfile` models
- Implement middleware for tier enforcement
- Add webhook handlers for payment processing

---

### Phase 8: Advanced Technical Indicators
**Objective**: Expand analytical capabilities

**Planned Indicators**:
- **Bollinger Bands**: Volatility indicator
- **Moving Averages**: SMA, EMA (multiple periods)
- **Stochastic Oscillator**: Momentum indicator
- **ADX (Average Directional Index)**: Trend strength
- **OBV (On-Balance Volume)**: Volume-price indicator
- **Fibonacci Retracement**: Support/resistance levels

**Features**:
- Configurable indicator parameters
- Historical indicator values (time series)
- Indicator comparison charts
- Custom indicator combinations

---

### Phase 9: Stock Screeners & Alerts
**Objective**: Automated screening and notification system

**Screener Features**:
- Pre-built screeners:
  - Overbought/Oversold stocks
  - Breakout candidates
  - High volume stocks
  - Trend reversal signals
- Custom screener builder
- Saved screener templates
- Real-time screening results

**Alert System**:
- Price alerts (above/below threshold)
- Indicator alerts (RSI > 70, MACD crossover)
- Custom condition alerts
- Multi-channel notifications (Email, SMS, WebSocket)
- Alert history and management

**Technical Implementation**:
- WebSocket integration for real-time updates
- Celery periodic tasks for alert checking
- Email/SMS provider integration
- Alert state management in Redis

---

### Phase 10: Advanced Analytics & Backtesting
**Objective**: Strategy development and testing framework

**Features**:
- **Backtesting Engine**:
  - Custom strategy builder
  - Historical performance simulation
  - Risk metrics (Sharpe ratio, max drawdown)
  - Portfolio optimization
- **Pattern Recognition**:
  - Candlestick patterns
  - Chart patterns (head & shoulders, triangles)
  - Support/resistance detection
- **Correlation Analysis**:
  - Stock correlation matrix
  - Sector analysis
  - Market sentiment indicators

---

### Phase 11: Frontend Dashboard (React/Vue)
**Objective**: Modern web interface for data visualization

**Planned Features**:
- Interactive stock charts (TradingView-style)
- Real-time price updates (WebSocket)
- Technical indicator overlays
- Screener results visualization
- User portfolio tracking
- Alert management interface
- Subscription and billing pages

**Technology Stack**:
- **React** or **Vue 3** - Frontend framework
- **Chart.js** or **D3.js** - Data visualization
- **WebSocket** - Real-time updates
- **Tailwind CSS** - UI styling
- **Vite** - Build tool

---

### Phase 12: Mobile Application
**Objective**: iOS and Android mobile apps

**Features**:
- Stock search and favorites
- Real-time price tracking
- Push notifications for alerts
- Mobile-optimized charts
- Offline data caching

**Technology Options**:
- **React Native** - Cross-platform development
- **Flutter** - High-performance native apps
- **Native iOS/Android** - Maximum performance

---

### Phase 13: Production Deployment
**Objective**: Cloud deployment with CI/CD

**Infrastructure**:
- **Cloud Provider**: AWS, Azure, or GCP
- **Services**:
  - Container orchestration (Kubernetes/ECS)
  - Managed PostgreSQL (RDS/Cloud SQL)
  - Managed Redis (ElastiCache/MemoryStore)
  - CDN for static assets (CloudFront/CloudFlare)
  - Load balancing
  - Auto-scaling groups

**DevOps**:
- CI/CD pipeline (GitHub Actions/GitLab CI)
- Automated testing (unit, integration, E2E)
- Blue-green deployment
- Database migration automation
- Monitoring and logging (Prometheus, Grafana, Sentry)
- SSL certificates and domain management

---

### Phase 14: API Documentation & Developer Portal
**Objective**: Comprehensive API documentation for third-party developers

**Features**:
- OpenAPI/Swagger documentation
- Interactive API explorer
- Code examples (Python, JavaScript, cURL)
- API key management
- Rate limit monitoring
- Developer sandbox environment
- API changelog and versioning

---

### Phase 15: Internationalization Expansion
**Objective**: Support additional languages and markets

**Languages**:
- Simplified Chinese (completed)
- Traditional Chinese
- Japanese
- Korean
- English (completed)

**Markets**:
- Hong Kong Stock Exchange (HKEX)
- Taiwan Stock Exchange (TWSE)
- Global indices (S&P 500, NASDAQ)

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

**Last Updated**: January 9, 2026  
**Version**: 1.0.0 (Phases 1-6 Complete)
