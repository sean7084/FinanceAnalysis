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
- Native Linux services for local development and testing

## Release History

Detailed version-by-version release notes are maintained in [CHANGELOG.md](CHANGELOG.md).

### Latest Highlights
- The current `v0.1.12` release candidate packages lifecycle-aware historical data governance, strict point-in-time effective-universe enforcement, expanded data-quality audits, richer macro term-structure coverage, and Linux-native local-stack tooling.
- 0.1.12: official trading calendar and suspension ingestion, OHLCV repair/warm-up flows, fail-fast PIT membership coverage, expanded `validate_data_quality` reporting, macro `6M/1Y/3Y/5Y/7Y/10Y/30Y` yields, checkpointed model-data backfills, and native `.venv`/localhost scripts
- 0.1.11: CSI 300 + CSI A500 constituent sync and onboarding, PIT benchmark fallback plus official benchmark comparison curves, 2010 floor/purge tooling, PIT-aware model/backtest filters, and refreshed LightGBM/LSTM artifacts
- 0.1.10: deterministic LightGBM `core80-v1` retrains, TP/SL trade-decision policy experiments, new validation/audit commands, Indicator Board UI, and refreshed validation report packs for runs 113-136
- 0.1.9: full backfill refresh, runtime backtest validation, northbound field cleanup migration
- 0.1.8: LSTM real retrain + inference, all-model backtest source selection, and backtest/stock page UX upgrades

## Future Phases & Roadmap

Workflow Audit Checklist

先定一条总规则

先把这条写进文档、代码注释、测试用例里：

effective_universe(date)

• overall date range: 2010-01-04 <= date < 2026-04-30
• 2010-01-04 <= date < 2024-09-23 → CSI300 only
• date >= 2024-09-23 → CSI300 ∪ A500
• 一切横截面计算、训练样本过滤、回测候选池、benchmark 构建、daily prediction，都必须调用同一套 universe 规则
• 禁止静默 fallback 到 all assets
• 如果发生 fallback，必须：
   • 打 warning

这条是总闸门。没有它，后面都容易脏。

───




B. 因子/特征原始源

• [x] backfill pre-warmup ohlcv data for pre 2010-1-4 and pre index listing for calculation of ohlcv-derived data
1. backfill the pre 20100104 and pre index-listing data for 18 months for technical data calculation and rs_score calculation
• [ ] fina_indicator 类季度财务指标是否正确 as-of 对齐，不用未来财报
• [ ] 技术指标是否只用到当日及过去数据
• [x] macro snapshot validation and backfill: DXY, CNY/USD, China 6M/1Y/3Y/5Y/7Y/10Y/30Y Yield, PMI Manufacturing, PMI Non-Manufacturing, and CPI YoY
1. backfill the pre 201607 data from manually downloaded csv from gov site
2. switch from curve term = [10, 2] to [0.5, 1, 3, 5, 7, 10, 30]
3. switch runtime yield_curve to cn10y_yield - cn3y_yield
4. stick to curve type = 曲线类型：0-到期
5. rescheduled the syncing to the second day of each month at 0:10 am
6. maps the Yahoo monthly open onto month-start MacroSnapshot.cny_usd rows for 2010-01 through 2012-02
• [x] market context validation and backfill
• [ ] Capital Flow Snapshots validation and backfill
• [x] technical indicator validation and backfill

for macrosnapshot US Dollar Index (DXY): rename it to Dow Jones FXCM Dollar Index Basket (USDOLLAR) since that is the index we are currently syncing

要抽查

• [ ] PE/PB/ROE 在 2010-2026 是否不是大面积空值
• [ ] macro-aware 训练起点是否与你之前的安全窗口一致
• [ ] 任取一个日期，检查该日 snapshot 是否引用了未来季度财报或未来宏观值



───

C. 横截面特征

这个最容易脏。

• [ ] rs_score
• [ ] factor score: Composite Score, Bottom Probability Score, Fundamental Score, Capital Flow Score, Technical Score

都必须确认：

• [ ] 只在 当日 effective_universe 内计算
• [ ] 不是拿全市场算
• [ ] 不是拿未来扩大后的 universe 倒推过去
• [ ] 不是 membership 缺失时直接退化成 all assets 且无告警

要抽查

选这 4 个日期：

• [ ] 2024-09-20
• [ ] 2024-09-23
• [ ] 2025-01-02
• [ ] 2025-12-31

对每个日期核查：

• [ ] universe size
• [ ] rank 分位数分布
• [ ] 参与横截面排名的股票清单
• [ ] 是否与 membership 一致

───

D. 数据覆盖率输出

• [ ] 每类 snapshot 都有 coverage report
• [ ] coverage report 至少包含：
  • 日期
  • effective_universe_count
  • feature_non_null_count
  • usable_asset_count
  • dropped_asset_count
  • missing_by_feature

红灯项：

• [ ] 某关键日期 usable_asset_count 突然断崖式下降
• [ ] 某大类特征在长时间段大量空值
• [ ] cross-sectional ranking 的母集和 effective_universe 不一致


───



二、membership 层 audit

这一层查的是：谁在 universe 里、从什么时候开始在、你有没有把未来名单拿回过去用。

A. IndexMembership 数据完整性

• [ ] CSI300 历史快照是否覆盖训练窗和回测窗
• [ ] A500 历史快照是否只从真实发布时间开始使用
• [ ] snapshot 日期是否足够密，不只是孤立几个点
• [ ] 每次成分调整是否能落到最近有效日期

你现在尤其要查

• [ ] 000300.SH 的最早 membership snapshot 日期 = 20100104
• [ ] 000510.CSI 的最早 membership snapshot 日期 = 2024-09-23
• [ ] 2010-2026 期间，CSI300 是否连续可用
• [ ] 2024-09-23 之后，A500 是否连续可用

───

B. membership 解释规则

• [ ] 用的是“公告日生效”还是“调样生效日”
• [ ] 对两次 snapshot 之间的日期，是否采用 forward fill 到下次变更前
• [ ] 对 snapshot 缺失的日期，是 fail、skip，还是 fallback
• [ ] 如果 fallback，是否记录并阻断主流程
• [ ] 允许 forward fill 最近一次有效 snapshot
• [ ] 不允许在没有历史起点的时期凭空 forward fill
• [ ] A500 在 2024-09-23 之前应视为 不存在，不是“空表时默认今天名单”

───

C. effective_universe 验证

• [ ] 2024-09-22：只能有 CSI300
• [ ] 2024-09-23：开始允许 CSI300 ∪ A500
• [ ] 2025-01-01：union size 应合理，不应是全市场
• [ ] 去重逻辑正确，同一股票不会在 union 中重复计数

要输出

• [ ] 每个抽查日的 constituent_count
• [ ] union_count
• [ ] overlap_count
• [ ] added_by_a500_count

───

D. 禁止的错误

• [ ] 用 2024-09-23 的 A500 名单外推到 2010-2024
• [ ] membership 缺失时静默回退到 all assets
• [ ] benchmark 和 candidate pool 使用不同 universe 口径
• [ ] training 用一个 universe，daily prediction 用另一个，但没有版本标记

───

三、training 层 audit

这一层查的是：模型到底学了什么样本，标签是不是对的，训练时是否混入了不该进来的股票和未来信息。

A. 训练样本过滤

• [ ] 每条训练样本 (asset, date) 在该日必须属于 effective_universe(date)
• [ ] 对 pre-2024-09-23，不允许 A500-only 资产混入
• [ ] 对 post-2024-09-23，允许 CSI300 ∪ A500
• [ ] 训练样本表中保存生成时的 universe_version / policy_version

要抽查

• [ ] 随机抽 50 条训练样本，验证资产是否属于当日 universe
• [ ] 抽 10 条 2024-09-23 前的样本，确认没有 A500-only 资产
• [ ] 抽 10 条 2024-09-23 后的样本，确认 union 生效

───

B. 标签生成

• [ ] 3d / 7d / 14d / 30d 的标签逻辑分别明确
• [ ] 标签窗口只使用未来收益本身，不带任何未来特征
• [ ] label 与 feature date 对齐，没有 off-by-one
• [ ] 停牌、退市、缺失价格样本如何处理有明文规则

要抽查

• [ ] 任取一个 asset/date，手工算一次 3d 和 7d label
[5/3/26 12:15 AM] 阿金: • [ ] 对边界日（如年末、停牌前）检查 label 是否异常
• [ ] 标签生成前后样本数变化是否合理

───

C. 特征 schema 一致性

• [ ] 训练时 feature list 固化
• [ ] artifact 保存 feature schema、训练窗口、universe policy、horizon
• [ ] 推理时严格校验 schema
• [ ] 不允许 feature 缺失后静默补 0 而不记录

需要保存到 artifact 的信息
[5/3/26 12:15 AM] 阿金: • [ ] feature_names
• [ ] feature_count
• [ ] training_window_start/end
• [ ] effective_universe_policy
• [ ] label_definition
• [ ] code_version / git commit
• [ ] data_snapshot_version

───

D. 模型质量与过拟合

你之前的经验已经很清楚：LightGBM 样本内强，样本外容易掉。

所以要查：

• [ ] 训练集/验证集/样本外测试集是否严格分时
• [ ] 没有随机打乱破坏时间顺序
• [ ] 没有用 2025 的信息去选 2024 前的模型
• [ ] 不同 horizon 没有共用错误标签或错误 artifact

要看 4 个结果

• [ ] in-sample
• [ ] validation
• [ ] sample-out backtest
• [ ] vs benchmark alpha

如果前 3 个漂亮，第 4 个长期不行，问题大概率还是：

• 特征无效
• 标签不对
• 或横截面口径脏

───

四、backtest 层 audit

这一层查的是：回测有没有按你以为的方式执行。

A. 候选池生成

• [ ] 每个交易日的 candidate pool 只来自 effective_universe(date)
• [ ] top_n_metric 与 horizon 一致
• [ ] up_threshold 真正生效
• [ ] candidate_mode=top_n 真正生效
• [ ] trade_score_scope=independent 真正生效

要抽查

• [ ] 随机抽 5 个交易日，导出候选股票清单
• [ ] 检查候选股票是否都属于当日 universe
• [ ] 检查排序分数是否来自对应模型输出

───

B. 交易执行逻辑

• [ ] 开仓日只在 entry_weekdays
• [ ] 平仓优先级明确：先 TP/SL 还是先持有期结束
• [ ] holding_period_days 真正生效
• [ ] 仓位上限、单笔资金比例、手续费、滑点都生效
• [ ] 停牌/无法成交的处理有规则

重点排查

你之前已经踩过：

• [ ] TP/SL 配置传下去了没有
• [ ] LightGBM 路径和 heuristic 路径执行逻辑是否一致
• [ ] 参数写进 report 但实际上没执行，这种假生效必须排除

───

C. benchmark 构建

• [ ] benchmark universe 与策略 universe 口径一致
• [ ] 使用同一套 membership / PIT 规则
• [ ] constituent_count、weighted_constituent_count、missing_market_cap、missing_prices 都有输出
• [ ] 没有“coverage complete 但 equity_curve 全平”的假完成

要抽查

• [ ] 2025-01-02 的 benchmark constituent_count
• [ ] 2025-06-30 的 benchmark constituent_count
• [ ] 2025-12-31 的 weighted_constituent_count
• [ ] benchmark equity_curve 是否真实波动

红灯项

• [ ] equity_curve = [100000, 100000, ...]
• [ ] coverage_status = complete 但 weighted_constituent_count = 0
• [ ] 策略使用 union，benchmark 却用 synthetic all-universe 或 equal-weight fallback

───

D. 结果可追溯性

每个 backtest run 最好都能追到：

• [ ] compare_backtest_run_id
• [ ] model artifact id
• [ ] horizon
• [ ] feature schema version
• [ ] universe policy version
• [ ] benchmark build version

这样你以后看到结果变化，能知道是：

• 数据变了
• membership 变了
• 训练变了
• 还是回测逻辑变了

───

五、daily prediction 层 audit

这一层查的是：你每天真正给出的信号，跟训练/回测是不是同一种东西。

A. 当日 universe

• [ ] daily prediction 的输入 universe 使用 effective_universe(today)
• [ ] pre-2024-09-23 和 post-2024-09-23 逻辑一致
• [ ] 不允许 default all-assets 扩大推理池

要抽查

• [ ] 随机选一个历史日重放 daily prediction
• [ ] 检查该日输入股票池是否与 backtest 同口径

───

B. 当日特征快照

• [ ] 推理时读取的 feature snapshot 与训练 schema 一致
• [ ] 缺失值处理与训练一致
• [ ] macro/factor 数据使用当日可得版本
• [ ] 没有因为某列缺失而整批 silent fallback

───

C. 模型选择

• [ ] daily prediction 使用的 artifact id 明确
• [ ] 3d/7d/30d/14d 不会串模型
• [ ] LightGBM、LSTM、Transformer 版本切换有 registry 记录
• [ ] active model registry 与实际调用一致

要抽查

• [ ] 一次 daily prediction 结果里打印：
  • model_version
  • artifact_id
  • horizon
  • feature_count
  • universe_size

───

D. 输出结果与落库

• [ ] 每只股票的预测分数落库
• [ ] 入选 top_n 的原因可解释
• [ ] 预测结果可回放
• [ ] 第二天可以对照真实收益做 prediction audit

最好额外保存

• [ ] raw score
• [ ] rank
• [ ] threshold pass/fail
• [ ] candidate selected true/false
• [ ] prediction timestamp
• [ ] input data version

───

六、建议你加一个“红灯清单”

只要出现以下任何一项，当轮结果不进入结论层：

• [ ] membership 缺失后 fallback 到 all assets
• [ ] benchmark coverage 假完整
• [ ] feature schema 与训练不一致
• [ ] TP/SL 参数写进 report 但未执行
• [ ] effective_universe(date) 未统一调用
• [ ] 横截面特征不是按当日 universe 算
• [ ] daily prediction 用的 active artifact 与回测不一致
• [ ] A500 在 2024-09-23 前被提前使用

───

七、建议你最后产出 4 份审计结果

别只“心里确认”，要落文件。

1. audit_data_coverage.md

写：

• 原始数据覆盖率
• 特征缺失率
• 关键日期抽查结果

2. audit_membership_policy.md

写：

• effective_universe 规则
• snapshot 覆盖范围
• fallback policy
• pre/post A500 切换规则

3. audit_training_backtest_consistency.md

写：

• training、backtest、daily prediction 是否同口径
• 哪些地方已经修复
• 哪些地方还有风险

4. audit_red_flags.md

专门列：

• 已发现问题
• 修复状态
• 是否影响历史结果可用性

───

如果你愿意，我下一条可以继续帮你写：

effective_universe(date) 统一设计草案

直接拆成：

• 数据接口
• feature ranking
• training sample builder
• backtest candidate builder
• benchmark builder
• daily prediction service

让你能直接落代码。


rs_score, factor score, percentile, etc. 不能跨制度硬算

ex. 2020 年某只股票的 rs_score 不能拿它去跟 2025 年加入 A500 后的扩展股票池一起排

1. 2020 年只在 2020 年当时的有效 universe 内做横截面排名。
2010-2024-09-22
• 所有横截面特征基于 CSI300 only
2024-09-23 之后
• 横截面特征基于 CSI300 ∪ A500

2. all universal metrics should use the latest list
ex. for a training/backtest run on 20250128 should use the latest csi a500 and 300 membership information on 2025-01-27

extras:
1. close 实际上已经是 qfq 值，而 adj_close 现在也是同一个值，没有保留未复权原始 close。把 OHLCV 的复权语义明确下来：新增 raw_close，避免 close 和 adj_close 现在这种“值一样但名字不同”的状态
2. 没有 limit_up / limit_down 规则. 没有按昨收去判断 10% / 20% / ST 涨跌停板. 没有按交易所制度去区分主板、创业板、科创板、北交所的不同涨跌幅限制. 也没有“超大日收益跳变”这类 return-based price anomaly 规则. 涨跌停是否被标记：否.
3. macro snapshot yeild curve is using the first day data for each month. cny/usd is using monthly open. we should switch to use the previous monthly ohlcv data instead.macro snapshot and market context sync and backfill按发布日期/可得日期对齐，不是按统计期硬贴


###
1. 14d model
2. replace lstm with transformer
3. 什么是建议的入场时间，我目前暂时定在周二周四，是否合理？
4. 我们是否要考虑加入历史分钟数据来确定我们的入场成本.

### performance optimization
1.
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
- Linux
- Python 3.12 with `venv`
- PostgreSQL 15 running on `localhost:5432`
- Redis 7 running on `localhost:6379`
- Node.js and npm
- TA-Lib system library installed on the host
- Git

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/sean7084/FinanceAnalysis.git
   cd FinanceAnalysis
   ```

2. **Set up environment variables**:
   ```bash
   # Create .envs/.local as the single local env file with:
   DATABASE_URL=postgres://finance_analysis:finance_analysis@localhost:5432/finance_analysis
   CELERY_BROKER_URL=redis://localhost:6379/0
   REDIS_URL=redis://localhost:6379/1
   DJANGO_SECRET_KEY=your-secret-key-here
   DJANGO_DEBUG=True
   DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
   DJANGO_READ_DOT_ENV_FILE=True
   TUSHARE_TOKEN=your-tushare-token-here

   # Optional helpers
   SMOKE_USERNAME=your-smoke-test-username
   SMOKE_PASSWORD=your-smoke-test-password
   ```

   `.envs/.local` is the only env file required for the host-native test workflow.
   Django and the helper scripts read it directly, and the Compose file also points at it if you still run containers for non-test work.
   If you still have `.env` or `compose/local/django/.env`, copy any missing keys into `.envs/.local` and remove the legacy files locally.

3. **Create and activate the virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements/local.txt
   ```

4. **Install frontend dependencies**:
   ```bash
   cd frontend
   npm install
   cd ..
   ```

5. **Verify the native stack**:
   ```bash
   ./scripts/verify_local_stack.sh
   ```

6. **Run migrations and create a superuser**:
   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```

7. **Start local services**:
   ```bash
   ./scripts/run_backend.sh
   ./scripts/run_celery_worker.sh
   ./scripts/run_celery_beat.sh
   ```

8. **Run tests locally**:
   ```bash
   python manage.py test apps.core.tests
   cd frontend
   npm test
   cd ..
   ```

   Use plain `python manage.py test ...` from the activated `.venv`; Docker is no longer part of the default test path.

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
- `backfill_technical_indicators` does not support `RS_SCORE`; `RS_SCORE` is backfilled through `backfill_model_data`.
- The correct trainer command name is `rebuild_lightgbm_pipeline`.

#### recommended run order

1. Universe and market foundation:
   ```bash
   python manage.py sync_index_constituents --start-date 2010-01-04 --end-date 2026-04-30 --skip-sync-dispatch
   python manage.py backfill_ohlcv_history --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py backfill_asset_list_dates
   python manage.py backfill_asset_suspensions --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py backfill_trading_calendar --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py sync_benchmark_index_history --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py build_pit_union_benchmark --start-date 2010-01-04 --end-date 2026-04-30
   ```

2. Raw factor, macro, and news backfills:
   ```bash
   python manage.py backfill_fundamental_snapshots --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py backfill_capital_flow_snapshots --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py backfill_macro_snapshots --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py backfill_news --start-at "2010-01-04 00:00:00" --end-at "2026-04-30 23:59:59" --run-pipeline
   ```

3. OHLCV-derived, macro snapshots derived, and model-input backfills:
   ```bash
   python manage.py backfill_market_context --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py backfill_technical_indicators --start-date 2010-01-04 --end-date 2026-04-30
   python manage.py backfill_model_data \
     --start-date 2010-01-04 \
     --end-date 2026-04-30 \
     --checkpoint-file reports/ops_logs/backfill_model_data_20260504_1.json
   ```

4. Validation, audits, and benchmark checks:
   ```bash
   python manage.py validate_data_quality --start-date 2010-01-04 --end-date 2026-04-30 --effective-universe-only --include-delisted --output-dir reports/
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
| `backfill_trading_calendar` | Backfill official SSE/SZSE trading calendar rows | `--start-date`, `--end-date`, `--exchange-codes` |
| `sync_benchmark_index_history` | Backfill official CSI 300 / CSI A500 index daily history | `--index-codes`, `--start-date`, `--end-date` |
| `build_pit_union_benchmark` | Build or refresh the internal PIT union benchmark | `--start-date`, `--end-date`, `--initial-nav` |

##### raw factor, macro, and news sources

| Command | Purpose | Handles |
| --- | --- | --- |
| `backfill_fundamental_snapshots` | Backfill PE/PB/share-count/market-cap/ROE snapshots from TuShare | `--start-date`, `--end-date`, `--symbols`, `--limit-assets` |
| `backfill_capital_flow_snapshots` | Backfill Main Force Net 5D, Margin Balance Change 5D | `--start-date`, `--end-date`, `--symbols`, `--limit-assets` |
| `backfill_macro_snapshots` | Backfill monthly US Dollar Index (DXY), CNY/USD, China 6M/1Y/3Y/5Y/7Y/10Y/30Y yields, PMI Manufacturing, PMI Non-Manufacturing, and CPI YoY | `--start-date`, `--end-date`, `--disable-fallback`, `--resume-yields` |
| `backfill_market_context` | Recompute `MarketContext` from `MacroSnapshot` history | `--start-date`, `--end-date` |
| `backfill_news` | Fetch and ingest historical or recent news | `--providers`, `--limit-per-provider`, `--start-at`, `--end-at`, `--chunk-days`, `--sleep-seconds`, `--max-retries`, `--dry-run`, `--queue`, `--run-pipeline` |
| `check_earliest_data` | Diagnostic report for earliest available source/local data | no CLI handles |

##### OHLCV-derived analytics and model inputs

| Command | Purpose | Handles |
| --- | --- | --- |
| `backfill_technical_indicators` | Backfill non-RS technical indicators from OHLCV | `--start-date`, `--end-date`, `--symbols`, `--limit-assets`, `--technical-indicators` |
| `backfill_model_data` | Backfill `SentimentScore`, `RS_SCORE`, and `FactorScore` inputs for training/inference | `--start-date`, `--end-date`, `--sentiment-weight`, `--skip-sentiment`, `--checkpoint-file`, `--resume-from-checkpoint` |

##### validation, audit, repair, and cleanup

| Command | Purpose | Handles |
| --- | --- | --- |
| `validate_data_quality` | Full data-quality validation with PIT/effective-universe checks and CSV/JSON reports | `--start-date`, `--end-date`, `--symbols`, `--include-delisted`, `--effective-universe-only`, `--output-dir`, `--technical-indicators`, `--cross-section-audit-dates`, `--macro-max-age-days`, `--max-detail-rows`, `--only-report`, `--alert`, `--alert-recipients`, `--fail-on-critical` |
| `audit_model_data_quality` | Quick audit of default/null buckets in model-data tables | `--start-date`, `--end-date`, `--symbol`, `--sample-size` |
| `run_validation_backtests` | Rolling heuristic/LightGBM/LSTM validation runs | `--start-date`, `--end-date`, `--window-days`, `--step-days`, `--sources`, `--top-n`, `--horizon-days`, `--entry-weekdays`, `--holding-period-days`, `--capital-fraction-per-entry`, `--min-up-probability`, `--name-prefix`, `--user-email`, `--queue` |
| `run_reference_benchmark_suite` | Wrap validation runs plus export a benchmark/report bundle under `reports/` | `--start-date`, `--end-date`, `--window-days`, `--step-days`, `--sources`, `--top-n`, `--horizon-days`, `--entry-weekdays`, `--holding-period-days`, `--capital-fraction-per-entry`, `--min-up-probability`, `--name-prefix`, `--user-email`, `--queue`, `--output-dir`, `--suite-name`, `--include-active-lightgbm-artifacts` |
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
