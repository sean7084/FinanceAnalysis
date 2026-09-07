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
- **Linux-Native Local Stack**: `.venv`-based Django, Celery, PostgreSQL, Redis, and Vite workflows without Docker in the test environment
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
- **Linux Host Services** - Native PostgreSQL 15, Redis 7, and `.venv`-based process orchestration for development/testing
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
- **Prediction Tables**: 10,804 `PredictionResult` rows for heuristic/LSTM daily snapshot storage and 446,482 LightGBM daily snapshot rows, with target/stop/risk-reward/trade-score/suggested fields available on current prediction outputs; backtests regenerate candidates on demand rather than depending on historical prediction coverage
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
- Native Linux services for local development and testing

## Release History

Detailed version-by-version release notes are maintained in [CHANGELOG.md](CHANGELOG.md).

### Latest Highlights
- The current `v0.1.11` release candidate packages `80` changed files across benchmark-universe orchestration, PIT benchmark infrastructure, backtest comparison UI/API, historical floor controls, tests, and refreshed model artifacts.
- 0.1.11: CSI 300 + CSI A500 constituent sync and onboarding, PIT benchmark fallback plus official benchmark comparison curves, 2010 floor/purge tooling, PIT-aware model/backtest filters, and refreshed LightGBM/LSTM artifacts
- 0.1.10: deterministic LightGBM `core80-v1` retrains, TP/SL trade-decision policy experiments, new validation/audit commands, Indicator Board UI, and refreshed validation report packs for runs 113-136
- 0.1.9: full backfill refresh, runtime backtest validation, northbound field cleanup migration
- 0.1.8: LSTM real retrain + inference, all-model backtest source selection, and backtest/stock page UX upgrades

## Future Phases & Roadmap

2. If you want, I can next diff one asset/date across heuristic, LightGBM, and LSTM to show exactly which stored features caused the model outputs to move.

[celery-worker] [2026-05-19 04:48:32,333: INFO/ForkPoolWorker-4] Task apps.backtest.tasks.run_backtest[7afce689-76ec-46f3-8d02-b1aac82c2dd4] succeeded in 2061.959317232s: 'Backtest failed for run_id=578: SoftTimeLimitExceeded()'


extras:
1. close 实际上已经是 qfq 值，而 adj_close 现在也是同一个值，没有保留未复权原始 close。把 OHLCV 的复权语义明确下来：新增 raw_close，避免 close 和 adj_close 现在这种“值一样但名字不同”的状态
2. 没有 limit_up / limit_down 规则. 没有按昨收去判断 10% / 20% / ST 涨跌停板. 没有按交易所制度去区分主板、创业板、科创板、北交所的不同涨跌幅限制. 也没有“超大日收益跳变”这类 return-based price anomaly 规则. 涨跌停是否被标记：否.
3. macro snapshot yeild curve is using the first day data for each month. cny/usd is using monthly open. we should switch to use the previous monthly ohlcv data instead.macro snapshot and market context sync and backfill按发布日期/可得日期对齐，不是按统计期硬贴
4. for macrosnapshot US Dollar Index (DXY): rename it to Dow Jones FXCM Dollar Index Basket (USDOLLAR) since that is the index we are currently syncing
5. 
6. LightGBM and LSTM training artifacts should include: effective_universe_policy version, label_definition, code_version / git commit, data_snapshot_version, schema_version. we need also record the versions during backtests.
7. artifact 保存 feature schema, 推理时严格校验 schema
8. 模型质量与过拟合
9. Persist a full per-date candidate snapshot so unselected names also carry rank, pass/fail, and candidate_selected=False.
10. Add a dedicated prediction-audit workflow that joins stored predictions to realized returns by horizon and writes a report.
11. **in session dev general** 
12. for model rebuild process, should we remove backfill workflow and make it a separate step that only runs during data validation processes?
13. performance boosts for windows:
- Run multiple worker processes, not one. On your current Windows setup, Celery is intentionally using solo with concurrency 1, so you are serialized at the task level. On this hardware, a practical starting point is 3-4 separate solo worker processes for backtests. Do not do this without thread caps, or each worker will try to use all cores internally and you will just oversubscribe the CPU.
- Cap native library threads per worker. Set OMP_NUM_THREADS=1, MKL_NUM_THREADS=1, OPENBLAS_NUM_THREADS=1, and NUMEXPR_NUM_THREADS=1 for backtest workers. Right now one “single” worker is still spreading across many native threads. If you want several workers, each worker needs to behave like a small CPU consumer, not like “use the whole machine.”



$23.7 / 0.2257 \approx 105$

###
1. 14d model
2. replace lstm with transformer
3. 什么是建议的入场时间，我目前暂时定在周二周四，是否合理？
4. 我们是否要考虑加入历史分钟数据来确定我们的入场成本.

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
- Windows 11 with PowerShell 7 for the primary local workflow
- Python 3.14 with `venv`
- PostgreSQL 15 reachable at the host configured in `.env` (current shared dev host: `192.168.31.8:5432`)
- Redis 7 reachable at the host configured in `.env` (current shared dev host: `192.168.31.8:6379`)
- Node.js and npm
- TA-Lib system library installed on the host
- Git for Windows

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/sean7084/FinanceAnalysis.git
   cd FinanceAnalysis
   ```

2. **Set up environment variables**:
   ```bash
   Copy-Item .env.example .env
   ```

   Update `.env` with your local secrets and service URLs.
   The checked-in example uses the current shared PostgreSQL/Redis host at `192.168.31.8`; replace those URLs if your services live elsewhere.

   `.env` is the only env file required for the host-native test workflow.
   Django and the helper scripts read it directly, and the Compose file also points at it if you still run containers for non-test work.
   The Compose stack no longer starts PostgreSQL or Redis containers; those services must already be running on the host or another reachable Linux machine referenced by `.env`.
   If you still have `.envs/.local` or `compose/local/django/.env`, copy any missing keys into `.env` and remove the legacy files locally.

3. **Create and activate the virtual environment**:
   ```bash
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements/local.txt
   ```

   Recreate `.venv` on Windows. Do not reuse a virtual environment copied from the old Ubuntu VM, because the embedded interpreter path will still point at the Linux host.

4. **Install frontend dependencies**:
   ```bash
   cd frontend
   npm install
   cd ..
   ```

5. **Verify the native stack**:
   ```bash
   pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_local_stack.ps1
   ```

6. **Run migrations and create a superuser**:
   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```

7. **Start local services**:
   ```bash
   # Preferred on Windows: VS Code task -> Run local stack

   # Or run the services in separate PowerShell terminals:
   pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_backend.ps1
   pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_celery_worker.ps1
   pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_celery_beat.ps1
   pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_frontend.ps1
   ```

   The existing `.sh` launchers are still available if you prefer to work from Git Bash.
   On Windows, `run_celery_worker.ps1` defaults to Celery's `solo` pool to avoid `billiard` handle errors; set `CELERY_WORKER_POOL` and optionally `CELERY_WORKER_CONCURRENCY` if you need a different local worker mode.

### WSL2 High-Performance Workflow

For backtests, training, and Celery-heavy workloads, the preferred performance path is WSL2 Ubuntu with the repository cloned inside the WSL ext4 filesystem rather than the Windows mount.

1. Clone the repository inside WSL under your Ubuntu home directory, not under `/mnt/c/...`.
2. Copy the root `.env` into that WSL clone and keep PostgreSQL / Redis pointed at `192.168.31.8` unless your service host changes.
3. Create a fresh Linux `.venv` inside the WSL clone and install `requirements/local.txt` there.
4. Open the WSL clone in VS Code Remote - WSL. The existing task labels now route to the Linux shell launchers when opened in Linux, so `Run backend`, `Run celery worker`, `Run celery beat`, `Run frontend`, and `Run local stack` work there without using the Windows PowerShell wrappers.
5. For a quick smoke, run `./scripts/verify_local_stack.sh` from inside WSL before launching the full stack.

This WSL path keeps Windows as the browser, VS Code UI host, and NVIDIA driver owner, while allowing Celery to use its normal Linux worker model instead of the Windows `solo` path.

8. **Run tests locally**:
   ```bash
   python manage.py test apps.core.tests
   cd frontend
   npm test
   cd ..
   ```

   Use plain `python manage.py test ...` from the activated `.venv`; Docker is no longer part of the default test path.
>>
9. **Import benchmark constituents and dispatch market syncs**:
   ```bash
   python manage.py shell
   >>> from apps.markets.tasks import sync_daily_a_shares
   >>> sync_daily_a_shares.delay()  # syncs the current CSI 300 + CSI A500 union
   >>> exit()
   ```

   Full CSI A500 rollout workflow:
   ```bash
   python manage.py onboard_csi_a500_universe --start-date 2010-01-01 --end-date 2026-04-26
   ```

   Rolling benchmark report bundles only:
   ```bash
   python manage.py run_reference_benchmark_suite --start-date 2024-01-01 --end-date 2026-04-26 --output-dir reports/reference_suite_latest
   ```

10. **Calculate indicators**:
   ```bash
   python manage.py shell
   >>> from apps.analytics.tasks import calculate_indicators_for_all_assets
   >>> calculate_indicators_for_all_assets.delay()
   >>> exit()
   ```

11. **Access the application**:
   - Admin: `http://localhost:8000/admin/`
   - API: `http://localhost:8000/api/v1/`

12. **Frontend**
   ```bash
   cd frontend
   npm run dev
   ```

13. **For changes in Python code**
   ```bash
   # Restart the local processes that load Python code:
   ./scripts/run_backend.sh
   ./scripts/run_celery_worker.sh
   ./scripts/run_celery_beat.sh
   ```
---

### data backfill, validation, and model training

Validated against the current Django management command signatures under `apps/**/management/commands`.

Key fixes from the older block:

- `backfill_news` is the missing news ingester for `NewsArticle`; use `--run-pipeline` if you also want `SentimentScore` and `ConceptHeat` refreshed after ingest.
- `sync_index_constituents`, `sync_benchmark_index_history`, and `build_pit_union_benchmark` should be included in the benchmark-universe workflow.
- `backfill_technical_indicators` backfills non-RS stored technical/model metrics from OHLCV, including `RETURN_3D/5D/10D`, `RELATIVE_VOLUME_5D/20D`, and `REALIZED_VOLATILITY_5D`; `RS_SCORE` still comes from `backfill_model_data`.
- The correct trainer command name is `rebuild_lightgbm_pipeline`.

#### backfill commands and recommended run order

1. Universe and market foundation:
   ```bash
   python manage.py sync_index_constituents --start-date 2010-01-04 --end-date 2026-04-30 --skip-sync-dispatch
   python manage.py backfill_ohlcv_history --start-date 2010-01-04 --end-date 2026-04-30 --technical-indicator-warmup
   python manage.py backfill_ohlcv_history --start-date 2010-01-04 --end-date 2026-04-30 --effective-universe-entry-warmup
   python manage.py backfill_asset_list_dates
   python manage.py backfill_asset_suspensions --start-date 2001-01-01 --end-date 2026-04-30
   python manage.py backfill_trading_calendar --start-date 2001-01-01 --end-date 2026-04-30
   python manage.py sync_benchmark_index_history --start-date 2001-01-01 --end-date 2026-04-30
   python manage.py build_pit_union_benchmark --start-date 2010-01-04 --end-date 2026-04-30
   ```

2. Raw factor, macro, and news backfills:
   ```bash
   python manage.py backfill_fundamental_snapshots --start-date 2010-01-04 --end-date 2026-04-30 --repair-same-announcement-roe
   python manage.py backfill_capital_flow_snapshots --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py backfill_macro_snapshots --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py backfill_news --start-at "2010-01-04 00:00:00" --end-at "2026-04-30 23:59:59" --run-pipeline
   ```

3. OHLCV-derived, macro snapshots derived, and model-input backfills:
   ```bash
   python manage.py backfill_market_context --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py backfill_signal_events --start-date 2010-01-04 --end-date 2026-04-30 --chunk-size-days 120 --checkpoint-file reports/ops_logs/signal_event_backfill_20260514_2.json
   python manage.py backfill_technical_indicators --start-date 2010-01-04 --end-date 2026-04-30 --chunk-size-days 120 --checkpoint-file reports/ops_logs/technical_indicator_backfill_20260513.json
   python manage.py backfill_model_data --start-date 2010-01-04 --end-date 2026-04-30 --checkpoint-file reports/ops_logs/backfill_model_data_20260510_1.json
   ```

   `backfill_technical_indicators` is now the storage surface for the shared OHLCV-derived model metrics used by LightGBM/LSTM/runtime parity: RSI, MOM variants, BBANDS, SMA, `RETURN_3D/5D/10D`, `RELATIVE_VOLUME_5D/20D`, and `REALIZED_VOLATILITY_5D`.

4. Validation, audits, and benchmark checks:
   ```bash
   python manage.py validate_data_quality --start-date 2010-01-04 --end-date 2026-04-30 --effective-universe-only --include-delisted --output-dir 
   python manage.py audit_model_data_quality --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py run_validation_backtests --start-date 2024-01-01 --end-date 2026-04-30 --sources heuristic,lightgbm,lstm
   python manage.py run_reference_benchmark_suite --start-date 2024-01-01 --end-date 2026-04-30 --output-dir reports/reference_suite_latest
   ```

5. Model training:
   ```bash
   python manage.py rebuild_lightgbm_pipeline --start-date 2016-06-01 --end-date 2024-12-31
   python manage.py rebuild_lstm_pipeline --start-date 2016-06-01 --end-date 2024-12-31
   ```

#### command handles

##### universe and market data

| Command | Purpose | Handles |
| --- | --- | --- |
| `sync_index_constituents` | Sync CSI 300 / CSI A500 memberships, tags, and optional asset dispatch | `--index-codes`, `--start-date`, `--end-date`, `--skip-sync-dispatch`, `--force-floor-backfill`, `--dispatch-changed-assets-only` |
| `backfill_ohlcv_history` | Backfill OHLCV history or targeted continuity-gap windows | `--start-date`, `--end-date`, `--csv-file`, `--symbols`, `--limit-assets`, `--queue`, `--effective-universe-entry-warmup`, `--technical-indicator-warmup` |
| `backfill_asset_list_dates` | Backfill `Asset.list_date`, `delist_date`, and `listing_status` | `--symbols`, `--limit-assets` |
| `backfill_asset_suspensions` | Backfill full-day/partial-day suspension data | `--start-date`, `--end-date`, `--symbols` |
| `backfill_trading_calendar` | Backfill SSE/SZSE `trade_cal` rows, preserving each `cal_date` and `is_open` flag | `--start-date`, `--end-date`, `--exchange-codes` |
| `sync_benchmark_index_history` | Backfill official CSI 300 / CSI A500 index daily history | `--index-codes`, `--start-date`, `--end-date` |
| `build_pit_union_benchmark` | Build or refresh the internal PIT union benchmark | `--start-date`, `--end-date`, `--initial-nav` |

##### raw factor, macro, and news sources

| Command | Purpose | Handles |
| --- | --- | --- |
| `backfill_fundamental_snapshots` | Backfill PE/PB/share-count/market-cap/ROE snapshots from TuShare | `--start-date`, `--end-date`, `--symbols`, `--limit-assets`, `--repair-same-announcement-roe` |
| `backfill_capital_flow_snapshots` | Backfill Main Force Net 5D, Margin Balance Change 5D | `--start-date`, `--end-date`, `--symbols`, `--limit-assets` |
| `backfill_macro_snapshots` | Backfill monthly US Dollar Index (DXY), CNY/USD, China 6M/1Y/3Y/5Y/7Y/10Y/30Y yields, PMI Manufacturing, PMI Non-Manufacturing, and CPI YoY | `--start-date`, `--end-date`, `--disable-fallback`, `--resume-yields` |
| `backfill_market_context` | Recompute `MarketContext` from `MacroSnapshot` history | `--start-date`, `--end-date` |
| `backfill_news` | Fetch and ingest historical or recent news | `--providers`, `--limit-per-provider`, `--start-at`, `--end-at`, `--chunk-days`, `--sleep-seconds`, `--max-retries`, `--dry-run`, `--queue`, `--run-pipeline` |
| `check_earliest_data` | Diagnostic report for earliest available source/local data | no CLI handles |

##### OHLCV-derived analytics and model inputs

| Command | Purpose | Handles |
| --- | --- | --- |
| `backfill_technical_indicators` | Backfill non-RS technical indicators and stored OHLCV-derived model metrics from OHLCV | `--start-date`, `--end-date`, `--symbols`, `--limit-assets`, `--technical-indicators`, `--chunk-size-days`, `--checkpoint-file`, `--resume-from-checkpoint` |
| `backfill_signal_events` | Backfill non-RS historical `SignalEvent` families from OHLCV | `--start-date`, `--end-date`, `--symbols`, `--limit-assets`, `--signal-types`, `--chunk-size-days`, `--checkpoint-file`, `--resume-from-checkpoint` |
| `backfill_model_data` | Backfill `SentimentScore`, `RS_SCORE`, and `FactorScore` inputs for training/inference | `--start-date`, `--end-date`, `--sentiment-weight`, `--skip-sentiment`, `--checkpoint-file`, `--resume-from-checkpoint` |

##### validation, audit, repair, and cleanup

| Command | Purpose | Handles |
| --- | --- | --- |
| `validate_data_quality` | Full data-quality validation with PIT/effective-universe checks and CSV/JSON reports | `--start-date`, `--end-date`, `--symbols`, `--include-delisted`, `--effective-universe-only`, `--output-dir`, `--technical-indicators`, `--cross-section-audit-dates`, `--macro-max-age-days`, `--max-detail-rows`, `--only-report`, `--alert`, `--alert-recipients`, `--fail-on-critical` |
| `audit_model_data_quality` | Quick audit of default/null buckets in model-data tables | `--start-date`, `--end-date`, `--symbol`, `--sample-size` |
| `run_validation_backtests` | Rolling heuristic/LightGBM/LSTM validation runs | `--start-date`, `--end-date`, `--window-days`, `--step-days`, `--sources`, `--top-n`, `--horizon-days`, `--entry-weekdays`, `--holding-period-days`, `--capital-fraction-per-entry`, `--min-up-probability`, `--name-prefix`, `--user-email`, `--queue` |
| `run_reference_benchmark_suite` | Wrap validation runs plus export a benchmark/report bundle under `reports/` | `--start-date`, `--end-date`, `--window-days`, `--step-days`, `--sources`, `--top-n`, `--horizon-days`, `--entry-weekdays`, `--holding-period-days`, `--capital-fraction-per-entry`, `--min-up-probability`, `--name-prefix`, `--user-email`, `--queue`, `--output-dir`, `--suite-name`, `--include-active-lightgbm-artifacts` |
| `run_core_backtest_matrix` | Create/export the core heuristic/LightGBM matrix; fastest local path is inline execution with matrix-scoped signal caching | `--start-date`, `--end-date`, `--variants`, `--sources`, `--name-prefix`, `--user-email`, `--queue`, `--execute-inline`, `--chunk-trading-days`, `--dry-run`, `--output-dir`, `--include-active-lightgbm-artifacts` |
| `reconcile_suspension_ohlcv_overlaps` | Verify and optionally delete OHLCV rows that overlap full-day suspensions | `--csv-file`, `--symbols`, `--output-file`, `--baidu-cookie`, `--execute` |
| `purge_pre_floor_historical_data` | Dry-run or delete rows before the configured historical floor | `--before-date`, `--execute` |

##### model training and end-to-end orchestration

| Command | Purpose | Handles |
| --- | --- | --- |
| `rebuild_lightgbm_pipeline` | Backfill inputs if needed and retrain LightGBM artifacts | `--start-date`, `--end-date`, `--horizons`, `--skip-backfill`, `--skip-sentiment`, `--version-tag`, `--use-snapshot-pruning` |
| `rebuild_lstm_pipeline` | Backfill inputs if needed and retrain LSTM artifacts | `--start-date`, `--end-date`, `--horizons`, `--sequence-length`, `--asset-chunk-size`, `--max-samples-per-horizon`, `--skip-backfill`, `--skip-sentiment` |

Notes:

- `backfill_news` uses datetime handles (`--start-at`, `--end-at`) instead of date-only handles.
- `backfill_technical_indicators` should not be used for `RS_SCORE`; use `backfill_model_data` instead.
- `rebuild_lightgbm_pipeline` and `rebuild_lstm_pipeline` will call `backfill_model_data` internally unless `--skip-backfill` is set.
- `run_reference_benchmark_suite` is the easiest way to produce validation bundles under `reports/` after retraining.
- For large local matrix runs, prefer `run_core_backtest_matrix --execute-inline --chunk-trading-days 60`; this keeps daily heuristic/LightGBM signal surfaces in the command process, alternates queued chunks across the matrix, and avoids per-run benchmark generation. Official CSI comparison remains available through the backtest comparison API.

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
