# FinanceAnalysis - Financial Data SaaS Platform

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

### Data Scope
- **Markets**: 3 (SSE, SZSE, BSE)
- **Assets**: 300 active listed CSI 300 constituents
- **OHLCV Records**: 1,145,611 daily price points across 300 assets (`2001-07-24` to `2026-04-24`)

### Data Metrics
- **Technical Indicators**: 1,141,393 stored indicator rows across RSI, MACD, BBANDS, SMA, EMA, STOCH, ADX, OBV, FIB_RET, MOM_5D, MOM_10D, MOM_20D, and RS_SCORE
- **Signal/Sentiment Tables**: 2,795 signal events, 40,066 news articles, 1,835,998 sentiment scores, and 80 concept heat rows
- **Factor Tables**: 1,145,013 fundamental snapshots, 1,145,611 capital-flow snapshots, 990,029 raw moneyflow rows, 768,393 raw margin-detail rows, and 1,801,500 factor-score rows
- **Prediction Tables**: 9,901 heuristic prediction rows and 444,980 LightGBM prediction rows, with target/stop/risk-reward/trade-score/suggested fields available on prediction outputs
- **Model Monitoring Volume**: 8 LightGBM artifacts (3 active), 17 model-version rows, 304 feature-importance snapshots, and 3 ensemble-weight snapshots
- **Backtest Release Export**: CSV reports for BacktestRun IDs 89-112 are stored under `reports/backtests_89_112_v0_1_9/`

### Models
- **Heuristic**: rule-based multi-horizon baseline with trade-decision outputs
- **LightGBM**: multi-class model with artifact registry, monitoring, and dashboard comparison
- **LSTM (PyTorch)**: real retrain pipeline and live inference path (3/7/30 horizons)

### API Endpoints
- **Markets API**: 2 endpoints (list, detail)
- **Assets API**: 2 endpoints + search/filter
- **OHLCV API**: 2 endpoints + date range filtering
- **Indicators API**: list/detail + compare/recalculate/fibonacci + ranking endpoints
- **Screeners API**: 4 pre-built screeners + screener templates
- **Dashboard Stocks API**: composite stock board with factor, indicator, sentiment, and dual-model trade-decision fields
- **Alerts API**: alert rules + alert events
- **Signals API**: list/filter/recent/recalculate signal events
- **Factors API**: fundamentals, capital-flows, and bottom-candidates screener
- **Macro API**: snapshots, current context, event-impact statistics
- **Sentiment API**: news ingestion, sentiment scores, latest sentiment, concept heat ranking
- **Prediction API (Heuristic Baseline)**: single-stock prediction, batch prediction, model-version registry, and trade-decision outputs
- **Prediction API (LightGBM ML)**: single-stock predictions, batch predictions, model artifacts, ensemble weights tracking, and trade-decision outputs
- **Prediction API (LSTM ML)**: single-stock predictions, batch predictions, retrain/recalculate actions, and trade-decision outputs
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
- 0.1.9: full backfill refresh, runtime backtest validation, northbound field cleanup migration
- 0.1.8: LSTM real retrain + inference, all-model backtest source selection, and backtest/stock page UX upgrades
- 0.1.7: odds engine, LightGBM trade-decision parity, and dashboard consolidation
- 0.1.6: Realtime auth and sentiment availability hardening
- 0.1.5: Dashboard, stock, macro, and alerts UX fixes

## Future Phases & Roadmap

a500

###

###
after this backfill, i have questions regarding to two sections:

1. many technical scores are .00 or .50, which is looks like default value.
1.1 is it true?
1.2 what are the reasons for that? data issue, calculation issue, or just the nature of the score distribution?

2. many roe and roe qaq fields are null
2.1 ex. 	阿特斯 (688472.SH)	July 14, 2023
2.2 what are the reasons for that? data issue or calculation issue?

3. current backfill_model_data takes hours to backfill data between 2000 and 2026. what are the bottlenecks and how can we optimize it for faster iteration?

###
stored MACD, ADX, OBV, SMA/EMA, and RS score analytics do not currently feed into technical score

Broad Django app-label test discovery still hits the project’s namespace-package loader issue

###
1. Clean the stale factors in TechnicalGuide.md so the whole guide matches the current implementation.
2. Refresh the data-range table in TechnicalGuide.md again as the latest backfill finished.
3. update the document so the guide matches the current implementation
4. update readme.md and changelog.md for the 66 file changes, and i am ready to commit as version 0.1.9
5. prepare me a sheet that includes all detailed backtest configuration and results for backtest 89-112

###
invest in the next layers of trade-decision integration and dashboard consolidation.

Optionally, if you want more value from the macro dataset, promote dxy, cny_usd, cpi_yoy, and ppi_yoy into model/backtest features, because today most of that new history is still just stored, not consumed.

do we need to add moneyflow_hsgt 北向资金（百万元）南向资金（百万元）to MacroSnapshot?

| 指标          | 当前基准（3.2） | 目标                   |
| ----------- | --------- | -------------------- |
| 样本外收益       | +10.66%   | >20%（接近或超过benchmark） |
| 样本外Sharpe   | 0.81      | >1.0                 |
| 样本外胜率       | 50.56%    | >55%                 |
| 样本内外Alpha差距 | ~44%      | <20%                 |


add a column to Data Metrics Sheet in techinicalguide.md that explains what these metrics are used for, how they feed into models or trade decisions, and what the implications of missing data are.


I did not switch prediction APIs, alerts, or the rest of the project to strict real-time mode. This implementation is scoped to backtests only, 

现在值得做：
1. 补充财务因子数据（PE/PB/ROE，现在全是空的）
   → 让多因子模型真正跑起来

2. 把heuristic从集成中替换掉
   → 用调好的多因子线性模型替代，作为LightGBM的互补

将来值得做：
3. 引入TFT替代LSTM
   → 真正有效的时序模型

A股预测效果最佳的五种模型

按综合表现排名：

第一：XGBoost / LightGBM（梯度提升树）

会在里面，而且是第一梯队。

• A股实战中表现最稳定的ML模型
• 可解释性强，特征重要性清晰
• 对噪声数据鲁棒，不容易过拟合
• 你的LightGBM 3日准确率已达58.6%，这个数字在A股里属于相当不错的水平
• 唯一缺点：无法捕捉时序依赖，需要靠lag特征弥补

───

第二：Transformer / Temporal Fusion Transformer（TFT）

不在你系统里，但是当前学术和工业界公认最强的时序预测架构。

• 专门为时序预测设计，同时处理多个时间尺度
• 能同时消化价格序列+宏观因子+情绪数据
• 有注意力机制，自动识别哪些历史时间点对当前预测最重要
• 比LSTM强在：不会遗忘远期信息，训练更稳定
• 缺点：计算成本高，调参复杂
───

第四：多因子线性模型（Alpha Factor Model）

不是ML，但在A股实战中持续有效，尤其在中低频策略中。

• 学术界验证过的因子：动量、反转、低波动、价值、质量
• A股特有有效因子：北向资金、融资余额变化、龙虎榜、涨停效应
• 优点：稳定、可解释、不过拟合、换手率低
• 你的系统Phase 11已经在做这个，但财务因子数据还是空的（N/A），这是目前最大的数据短板
• 这类模型的预测不给"涨跌概率"，而是给"相对排名"，配合你的筛选器逻辑天然契合

───

第三：集成模型（Ensemble / Stacking）

你的系统已经有雏形，但还没做完。

• 单模型都有盲区，集成多个互补模型可以平滑误差
• 最有效的组合：LightGBM（特征工程强）+ Transformer（时序强）+ 因子模型（基本面强）
• 你现在的heuristic+LightGBM集成是对的方向，但heuristic太弱，拉低了整体
• 真正有效的集成是把几个各有所长的强模型合并，而不是强模型+规则模型

关于仓位大小

固定每股2万不是最优解。赔率好的时候应该多投，赔率差的时候少投。

简化版Kelly公式：

建议仓位比例 = (胜率 × 赔率 - 败率) ÷ 赔率

举例：胜率60%，赔率3:1

= (0.6 × 3 - 0.4) ÷ 3 = 1.4 ÷ 3 ≈ 46%

但Kelly公式得出的数字通常过于激进，实际用半Kelly更保守安全，即上面结果再除以2，约23%仓位。

你可以在系统里设定：

• trade_score 10-12 → 每笔¥10,000
• trade_score 12-14 → 每笔¥15,000
• trade_score 14+ → 每笔¥20,000

### Priority 1: Validate and Close the Model Loop

Add compact validation summaries and recurring drift checks so each model refresh can be compared quickly against heuristic, LightGBM, and LSTM baselines before deeper trade-decision integration.

### Priority 2: Turn Probabilities Into Trade Decisions

The core trade-decision layer is now live for both heuristic and LightGBM predictions, and the dashboard has absorbed the old screener workflow. The next step is to turn that into a tighter operator workflow rather than adding another parallel page.

| Workstream | Planned implementation |
| ---------- | ---------------------- |
| Dashboard action queue | Add stronger dashboard presets for `suggested only`, per-model ranking, and quick switching between heuristic-first and LightGBM-first candidate views |
| Indicator board UX hardening | Refine the all-stocks indicator board for smaller screens, denser tables, and optional column presets or toggles |
| Multi-horizon dashboard visibility | Reintroduce clearer 3-day and 30-day visibility on the dashboard where it improves decision-making instead of keeping the view effectively 7-day-only |
| Ranking parity beyond comparison views | Extend the persisted LightGBM trade-decision fields into more ranking and list surfaces where heuristic trade fields are already used |
| Position sizing guidance | Build on the current target/stop/R:R outputs with position-sizing suggestions rather than only binary `suggested` flags |

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

10. **for changes in python codes**
   ```bash
   docker compose restart celery_beat celery_worker django_
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
- **AKShare** - Chinese financial data provider
- **TA-Lib** - Technical analysis library
- **Django & DRF** - Web framework and API tools
- **Celery** - Distributed task queue

---


**Last Updated**: April 15, 2026  
**Version**: v0.1.7
