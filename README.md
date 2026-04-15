# FinanceAnalysis - Bilingual Financial Data SaaS Platform

A production-ready Django-based SaaS platform for analyzing Chinese financial markets with bilingual (English/Chinese) support, real-time data ingestion, technical analysis, and RESTful API.

## 🚀 Project Overview

This platform provides comprehensive financial data analysis for Chinese stock markets (CSI 300 index universe), featuring:

- **Real-time Data Ingestion**: Automated synchronization and backfill workflows using AkShare + TuShare
- **Technical Analysis**: RSI, MACD, and extensible indicator calculations using TA-Lib
- **RESTful API**: Secure, rate-limited API with JWT authentication
- **Bilingual Support**: Full English/Chinese translation support
- **SaaS-Ready**: Multi-tier rate limiting and caching for scalability
- **Production Infrastructure**: Fully containerized with Docker Compose
- **Frontend Dashboard**: React + TypeScript dashboard for operations, monitoring, and strategy views

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
- **TuShare** - Historical backfill and alternate market data source
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

## 📊 Current System Status

### Data Metrics
- **Snapshot Date**: April 15, 2026
- **Markets**: 3 (SSE, SZSE, BSE)
- **Assets**: 300 synced constituents
- **OHLCV Records**: 647,164 daily price points
- **Technical Indicators**: RSI, MACD, BBANDS, SMA, EMA, STOCH, ADX, OBV, FIB_RET, MOM_5D, MOM_10D, MOM_20D, RS_SCORE
- **Signal Events**: 15 signal types (MA, Bollinger, Volume, Momentum, Reversal)
- **Feature Table Volume**: 6,297 technical indicators, 360 signal events, 296 news articles, 8,831 sentiment scores, 5 concept heat rows, 8,700 factor scores, and 900 heuristic prediction rows
- **LightGBM Monitoring Volume**: 2 trained LightGBM artifacts, 2 LightGBM model versions, 600 LightGBM prediction rows, 78 feature-importance snapshots, and 1 ensemble-weight snapshot
- **Coverage**: 300/300 assets with OHLCV data; latest synced OHLCV date is April 14, 2026
- **Backtest Engine**: async strategy simulation with trade logs and performance metrics is wired, but the current dataset still has 0 completed validation runs

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
- **Developer Portal API**: API key management, sandbox keys, key rotation, changelog
- **Schema / Docs API**: OpenAPI 3.0 schema, Swagger UI, ReDoc
- **Users API**: register, verify-email, password-reset, profile, subscriptions, usage stats
- **Authentication**: 3 endpoints (token, refresh, verify) + `X-API-Key` header

### Performance
- Redis caching enabled (2-hour cache for static data)
- Database query optimization with `select_related()`
- Celery distributed task processing
- Docker containerization for scalability

## Release History

Detailed version-by-version release notes are maintained in [CHANGELOG.md](CHANGELOG.md).

### Latest Highlights
- 0.1.7: LightGBM monitoring, comparison views, and live training hardening
- 0.1.6: Realtime auth and sentiment availability hardening
- 0.1.5: Dashboard, stock, macro, and alerts UX fixes

## Future Phases & Roadmap

### Priority 1: Validate and Close the Model Loop

The system now produces heuristic and LightGBM predictions, but the validation loop is still incomplete. This is the highest-priority future work because model confidence is not trustworthy without repeated historical evaluation.

| Workstream | Why it matters | Planned implementation |
| ---------- | -------------- | ---------------------- |
| Backtest coverage | The current dataset still has no completed backtest runs, so prediction quality is not yet verified under repeated market conditions | Run systematic backtests by model, horizon, and historical window; store run summaries and benchmark comparisons |
| Backtest trade detail UI | The backtest page does not yet expose transaction-level fills and exits | Extend `/backtest` frontend views to show per-trade records, fees, slippage, and realized PnL |
| Prediction accuracy tracking | Predictions are stored, but there is no automatic “predicted up vs actual outcome” evaluation loop | Add daily accuracy attribution by symbol, horizon, and model version, plus drift alerts and degradation reporting |
| 30-day LightGBM readiness | 3-day and 7-day LightGBM models are trained, but the 30-day model still lacks enough prepared historical feature rows | Backfill older factor-score and sentiment dates further into February or earlier so 30-day labels can train consistently |

### Priority 2: Turn Probabilities Into Trade Decisions

Current outputs estimate direction probabilities, but the next product step is to decide whether a trade is worth taking. The core idea is to combine win probability with potential reward and risk rather than ranking by probability alone.

> **期望值 = 胜率 × 盈利幅度 − 败率 × 亏损幅度**

| Workstream | Planned implementation |
| ---------- | ---------------------- |
| Odds engine | Estimate target price and stop-loss price from technical resistance/support zones such as recent highs, Bollinger bands, round-number levels, recent lows, and MA60 |
| Risk/reward scoring | Compute reward, risk, and `risk_reward_ratio`, then derive `trade_score = p_up * reward / ((1 - p_up) * risk)` |
| API extensions | Add `target_price`, `stop_loss_price`, `risk_reward_ratio`, `trade_score`, and `suggested` to `/api/v1/prediction/{stock_code}/` |
| Frontend trade signal UI | Add a dedicated trade-signal card on stock detail and support screener ranking by `trade_score` |
| Backend support | Introduce `apps/prediction/odds.py` and extend `PredictionResult` persistence for odds-engine outputs |

### Priority 3: Strengthen Data and Monitoring Discipline

The platform needs a stronger operational validation layer so data issues and model drift can be detected before they silently affect downstream ranking and prediction quality.

| Workstream | Planned implementation |
| ---------- | ---------------------- |
| Full data consistency checks | Validate cross-table relationships, time continuity, missing-date gaps, and symbol-level coverage |
| Replay verification | Recalculate selected historical trading days and compare regenerated outputs against stored snapshots |
| Drift and anomaly monitoring | Detect sudden jumps, missing assets, provider failures, and abnormal daily distribution shifts |
| Data-quality alerting | Trigger compensation sync or operator review when AkShare or TuShare feeds degrade |

### Priority 4: Higher-Leverage Model and Signal Upgrades

These items improve model quality and signal usefulness, but they depend on the validation and monitoring foundation above.

| Priority | Module | Planned implementation |
| -------- | ------ | ---------------------- |
| High | Real NLP sentiment | Replace the current rule-based neutral-heavy fallback with a finance-oriented Chinese BERT sentiment model |
| High | Feature engineering upgrade | Continue beyond the current LightGBM feature set with stronger temporal features, additional cross terms, and industry-relative strength features |
| Medium | Position sizing guidance | Add Kelly-based or fixed-risk position sizing so the system can suggest “how much” instead of only “whether” |
| Medium | Sector rotation signals | Add industry and theme rotation context to improve stock selection directionality |
| Low | Policy text analysis | Parse CSRC, NDRC, and related policy documents to infer sector-level directional impact |
| Low | Personal holdings tracking | Allow users to input their own cost basis and position size and receive portfolio-specific suggestions |


---

### Production Deployment
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

9. **frontend**
   ```bash
   cd frontend
   npm install --no-bin-links
   npm run dev
   ```
---

## 📡 API Documentation

http://localhost:8000/api/v1/schema/swagger-ui/
http://localhost:8000/api/v1/schema/redoc/

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

- **TuShare** - Chinese financial data provider
- **TA-Lib** - Technical analysis library
- **Django & DRF** - Web framework and API tools
- **Celery** - Distributed task queue

---


**Last Updated**: April 15, 2026  
**Version**: v0.1.7
