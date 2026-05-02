# FinanceAnalysis - Financial Data SaaS Platform

A production-ready Django-based SaaS platform for analyzing Chinese financial markets with bilingual (English/Chinese) support, real-time data ingestion, technical analysis, and RESTful API.

## 🚀 Project Overview

This platform provides comprehensive financial data analysis for Chinese stock markets with benchmark-universe support for CSI 300 and CSI A500, featuring:

- **Real-time Data Ingestion**: Automated synchronization and backfill workflows using AkShare + TuShare
- **Benchmark-Aware Backtests**: point-in-time union benchmark support, official CSI 300 / CSI A500 comparison curves, and comparison rerun workflows
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
- **Assets**: active benchmark constituents across the CSI 300 / CSI A500 union, with per-asset membership tags and historical index snapshots
- **OHLCV Records**: 1,145,611 daily price points in the currently loaded benchmark universe (`2001-07-24` to `2026-04-24`); use the CSI A500 onboarding workflow to expand beyond the initial CSI 300 seed set

### Data Metrics
- **Technical Indicators**: 1,141,393 stored indicator rows across RSI, MACD, BBANDS, SMA, EMA, STOCH, ADX, OBV, FIB_RET, MOM_5D, MOM_10D, MOM_20D, and RS_SCORE
- **Signal/Sentiment Tables**: 2,855 signal events, 44,468 news articles, 1,836,395 sentiment scores, and 88 concept heat rows
- **Factor Tables**: 1,145,013 fundamental snapshots, 1,145,611 capital-flow snapshots, 990,029 raw moneyflow rows, 768,393 raw margin-detail rows, and 1,801,500 factor-score rows
- **Prediction Tables**: 10,804 `PredictionResult` rows for heuristic/LSTM storage surfaces and 446,482 LightGBM prediction rows, with target/stop/risk-reward/trade-score/suggested fields available on prediction outputs
- **Model Monitoring Volume**: 14 LightGBM artifacts (3 active), 23 model-version rows, 475 feature-importance snapshots, and 3 ensemble-weight snapshots
- **Backtest Release Export**: benchmark suites and detailed run exports are generated locally under `reports/` through `run_reference_benchmark_suite` and `export_backtest_runs`, rather than treated as committed source files

### Models
- **Heuristic**: rule-based multi-horizon baseline with trade-decision outputs
- **LightGBM**: multi-class model with PIT-aware training datasets, refreshed `2024-12-31` artifacts, richer artifact metadata, monitoring, and dashboard/backtest comparison surfaces
- **LSTM (PyTorch)**: real retrain pipeline, live inference path, and refreshed `2024-12-31` artifact family (3/7/30 horizons)

### Validation & Reporting
- **Data Quality Validation**: `validate_data_quality` writes actionable CSV/JSON audit reports under `reports/` without mutating historical tables
- **Focused Model Data Audit**: `audit_model_data_quality` inspects default/null buckets in factor, fundamental, capital-flow, and `RS_SCORE` history for debugging
- **Reference Benchmark Suites**: `run_reference_benchmark_suite` and `export_backtest_runs` generate local run summaries, model references, comparison reruns, and benchmark manifests under `reports/`
- **Historical Floor Controls**: a shared `2010-01-01` floor helper plus `purge_pre_floor_historical_data` keep future backfills and retrains from re-expanding stale pre-floor history
- **Local Report Output**: `reports/` is now treated as generated local output and ignored by git rather than maintained as committed source

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
- **Backtest API**: create/list/retrieve backtest runs, rerun action, comparison-curve payloads, and trade history endpoints
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
- The current `v0.1.11` release candidate packages `80` changed files across benchmark-universe orchestration, PIT benchmark infrastructure, backtest comparison UI/API, historical floor controls, tests, and refreshed model artifacts.
- 0.1.11: CSI 300 + CSI A500 constituent sync and onboarding, PIT benchmark fallback plus official benchmark comparison curves, 2010 floor/purge tooling, PIT-aware model/backtest filters, and refreshed LightGBM/LSTM artifacts
- 0.1.10: deterministic LightGBM `core80-v1` retrains, TP/SL trade-decision policy experiments, new validation/audit commands, Indicator Board UI, and refreshed validation report packs for runs 113-136
- 0.1.9: full backfill refresh, runtime backtest validation, northbound field cleanup migration
- 0.1.8: LSTM real retrain + inference, all-model backtest source selection, and backtest/stock page UX upgrades

## Future Phases & Roadmap

14d

###
3. 什么是建议的入场时间，我目前暂时定在周二周四，是否合理？
4. 我们是否要考虑加入历史分钟数据来确定我们的入场成本.

### performance optimization
1. current backfill_model_data takes hours to backfill data between 2000 and 2026. what are the bottlenecks and how can we optimize it for faster iteration?
2. are we using cpu or gpu for the backfill tasks and backrun tasks? for the sake of performance, should be use which one for which task?

###
buy price currently depends on close price

### a500 onboarding checklist:

wire the scheduler/docs/config so the dual-index universe sync becomes the default operational path everywhere.

###


###
we need to add model version selection to our system so we can validate the latest LightGBM and LSTM models against the previous versions.
1. add the selection to page http://localhost:5173/backtest
2. sync the change to the dashboard http://localhost:5173/
3. add the field to the backruns http://localhost:8000/admin/backtest/backtestrun/, default to the latest version automatically
4. update exported reports if needed
5. sync the change to our backend

###
stored MACD, ADX, OBV, SMA/EMA, and RS score analytics do not currently feed into technical score

Broad Django app-label test discovery still hits the project’s namespace-package loader issue

###
1. Clean the stale factors in TechnicalGuide.md so the whole guide matches the current implementation.
2. Refresh the data-range table in TechnicalGuide.md again as the latest backfill finished.
3. update the document so the guide matches the current implementation
4. update readme.md and changelog.md for the 66 file changes, and i am ready to commit as version 0.1.9
5. update /reports for detailed backtest configuration and results for backtest 89-112

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


I did not switch prediction APIs, alerts, or the rest of the project to strict real-time mode. This implementation is scoped to backtests only, 


2. 把heuristic从集成中替换掉
   → 用调好的多因子线性模型替代，作为LightGBM的互补

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

6. **Import benchmark constituents and dispatch market syncs**:
   ```bash
   docker-compose exec django python manage.py shell
   >>> from apps.markets.tasks import sync_daily_a_shares
   >>> sync_daily_a_shares.delay()  # syncs the current CSI 300 + CSI A500 union
   >>> exit()
   ```

   Full CSI A500 rollout workflow:
   ```bash
   docker-compose exec django python manage.py onboard_csi_a500_universe --start-date 2010-01-01 --end-date 2026-04-26
   ```

   Rolling benchmark report bundles only:
   ```bash
   docker-compose exec django python manage.py run_reference_benchmark_suite --start-date 2024-01-01 --end-date 2026-04-26 --output-dir reports/reference_suite_latest
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
