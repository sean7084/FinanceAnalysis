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

二、membership 层 audit

这一层查的是：谁在 universe 里、从什么时候开始在、你有没有把未来名单拿回过去用。

A. IndexMembership 数据完整性

• [x] CSI300 历史快照是否覆盖训练窗和回测窗
• [x] A500 历史快照是否只从真实发布时间开始使用
• [x] 每次成分调整是否能落到最近有效日期

你现在尤其要查

• [x] 2010-2026 期间，CSI300 是否连续可用
• [x] 2024-09-23 之后，A500 是否连续可用

当前 DB 审计结果（2026-05-16）：

• `000300.SH`：最早 snapshot = `2010-01-04`，最新 snapshot = `2026-05-06`，distinct snapshot dates = `725`
• `000510.CSI`：最早 snapshot = `2024-09-23`，最新 snapshot = `2026-04-30`，distinct snapshot dates = `20`，`2024-09-23` 之前 row count = `0`
• 对 `2010-01-04 .. 2026-04-30` 的官方开市日运行 `pit_membership_coverage_gaps(...)`，总 gap count = `0`
• 对 `2024-09-23 .. 2026-04-30` 的 A500 专项检查，gap count = `0`
• 所有 snapshot 日期都落在 SSE 开市日；resolver 使用 `latest snapshot <= target_date` 取最近有效快照；两条指数的最大快照 carry gap 均为 `22` 个交易日

因此，Section A 就“历史覆盖、A500 起始边界、连续可用性、最近有效快照可解析”这几个完整性问题可以先关闭。`公告日生效` vs `调样生效日` 的制度解释仍留到 Section B 处理。

───

B. membership 解释规则

• [x] 用的是“公告日生效”还是“调样生效日”
• [x] 对两次 snapshot 之间的日期，是否采用 forward fill 到下次变更前
• [x] 对 snapshot 缺失的日期，是 fail、skip，还是 fallback
• [x] 如果 fallback，是否记录并阻断主流程
• [x] 允许 forward fill 最近一次有效 snapshot
• [x] 不允许在没有历史起点的时期凭空 forward fill
• [x] A500 在 2024-09-23 之前应视为 不存在，不是“空表时默认今天名单”

当前实现规则（2026-05-16 代码审计）：

• `sync_index_constituent_universe()` 直接把 TuShare `index_weight.trade_date` 落到 `IndexMembership.trade_date`；系统没有单独存 `announcement_date` / `effective_date` 双字段。因此当前实现实际上是“按 provider 给出的 snapshot/trade_date 口径生效”，不是“先存公告日再另算生效日”的双口径模型。
• `resolve_effective_index_snapshot_dates()` 对每个指数执行 `trade_date <= target_date` 且取最新一条，所以两次 snapshot 之间采用“最近一次有效快照 forward fill，直到下一次 snapshot 替换”为止。
• `pit_membership_coverage_gaps()` / `ensure_pit_membership_coverage()` 的规则是：如果某个 required index 在目标日期之前从未出现过历史起点，则判为缺失并直接报错；canonical workflow 不是 skip，也不是 fallback 到 all assets。
• `backfill_model_data`、训练特征构建、backtest universe 选择等主流程都先调用 `ensure_pit_membership_coverage(...)`；缺历史时抛 `PITMembershipCoverageError`，命令层再转成 `CommandError` 终止流程。validator 会把 gap 写进 `index_membership_history_gaps`，但不会静默放大 universe。
• 允许 forward fill 最近一次有效 snapshot，但只允许在“已有历史起点”的前提下 forward fill；没有历史起点时，`point_in_time_union_asset_ids()` 最多返回空，而 canonical `effective_universe_asset_ids()` 会先做 coverage guard，不允许凭空造出母集。
• `required_pit_index_codes_for_date()` 明确规定：`2010-01-04 <= date < 2024-09-23` 只要求 `000300.SH`，`date >= 2024-09-23` 才额外要求 `000510.CSI`。因此 A500 在 `2024-09-23` 之前被视为“不存在/不要求”，不是“空表时默认今天名单”。

因此，Section B 可以先按当前实现关闭：规则是“provider `trade_date` 作为生效 snapshot date + 最近有效快照 forward fill + 缺历史即 fail fast”。如果后续要严格区分“中证公告日”与“调样正式生效日”，需要先引入独立字段和对应 source 证据，当前表结构还不支持这两种制度并存。

───

C. effective_universe 验证

• [x] 2024-09-22：只能有 CSI300
• [x] 2024-09-23：开始允许 CSI300 ∪ A500
• [x] 去重逻辑正确，同一股票不会在 union 中重复计数

要输出

• [x] 每个抽查日的 constituent_count
• [x] union_count
• [x] overlap_count
• [x] added_by_a500_count

当前 DB 抽查结果（2026-05-16）：

• `2024-09-22`：`CSI300 snapshot=2024-09-02`，`A500 snapshot=None`，`CSI300 constituent_count=300`，`A500 constituent_count=0`，`union_count=300`，`overlap_count=0`，`added_by_a500_count=0`；`union_equals_csi300=True`
• `2024-09-23`：`CSI300 snapshot=2024-09-02`，`A500 snapshot=2024-09-23`，`CSI300 constituent_count=300`，`A500 constituent_count=500`，`union_count=566`，`overlap_count=234`，`added_by_a500_count=266`
• `2025-01-02`（补一个 post-launch 抽查日）：`CSI300 snapshot=2025-01-02`，`A500 snapshot=2024-12-31`，`CSI300 constituent_count=300`，`A500 constituent_count=500`，`union_count=564`，`overlap_count=236`，`added_by_a500_count=264`
• 三个抽查日都满足 `duplicates=False`；A500 已存在的抽查日满足 `union_count = csi300_count + a500_count - overlap_count`

因此，Section C 可以先关闭：`2024-09-22` 的 effective universe 仍然严格等于 CSI300，`2024-09-23` 起开始并入 A500，且 union 去重逻辑工作正常，没有重复资产计数。

───

D. 禁止的behavior

• [x] 用 2024-09-23 的 A500 名单外推到 2010-2024
• [x] membership 缺失时静默回退到 all assets
• [x] benchmark 和 candidate pool 使用不同 universe 口径
• [x] training 用一个 universe，daily prediction 用另一个，但没有版本标记

当前代码/样本审计结果（2026-05-16）：

• “用 `2024-09-23` 的 A500 名单外推到 `2010-2024`” 这一红灯当前未发现：`required_pit_index_codes_for_date()` 在 `2024-09-23` 之前只要求 `000300.SH`，Section C 抽查也证明 `2024-09-22` 的 union 仍然严格等于 CSI300。
• “membership 缺失时静默回退到 all assets” 当前未发现：训练特征/标签构建、`backfill_model_data`、factor/RS 刷新、backtest candidate 选择都先调用 `ensure_pit_membership_coverage(...)`；缺历史时会抛 `PITMembershipCoverageError` / `CommandError`，validator 也会把 gap 写入 `index_membership_history_gaps`，不是 silent fallback。
• “benchmark 和 candidate pool 使用不同 universe 口径” 当前样本未发现：backtest candidate pool 先取 `point_in_time_union_asset_ids(dt)` 再与当日可交易 `OHLCV` 交集；PIT benchmark 行也基于同一个 `resolve_point_in_time_union_membership()` 生成。抽查 `2024-09-23`、`2025-01-02`、`2025-12-31` 时，benchmark `constituent_count` / `overlap_count` 与 live PIT resolver 完全一致；benchmark 额外记录的 `weighted_constituent_count` 和 `missing_prices` 只是价格可得性层，不是另一套 membership 规则。
• “training 用一个 universe，daily prediction 用另一个，但没有版本标记” 当前未发现“口径分叉”：训练的 `_create_feature_matrix()` / `_create_labels_for_training()` 用 `ensure_pit_membership_coverage(...) + point_in_time_union_asset_ids_by_dates(...)`，日常 heuristic / LightGBM / LSTM prediction 都用 `effective_universe_assets(as_of, ...)`。这几条路径都落在同一套 PIT helper 上。

当前可保留的 follow-up：

• LightGBM / generic `ModelVersion` metadata 里还没有显式的 `effective_universe_policy` / `universe_version` 字段，所以“当前没有观察到训练/日推 universe 分叉”可以关闭，但“把 universe policy 做成 artifact provenance” 仍应留到训练层 Section 3C 继续补。
• PIT benchmark builder 当前和 candidate pool 使用同一 membership resolver，但 benchmark build path 本身没有额外调用 `ensure_pit_membership_coverage()` 做对称 fail-fast；在当前数据完整时不会造成口径错位，但如果要把 guard 做到完全一致，后续可以再补这一层防线。

───

三、training 层 audit

这一层查的是：模型到底学了什么样本，标签是不是对的，训练时是否混入了不该进来的股票和未来信息。

A. 训练样本过滤

• [x] 每条训练样本 (asset, date) 在该日必须属于 effective_universe(date)
• [x] 对 pre-2024-09-23，不允许 A500-only 资产混入
• [x] 对 post-2024-09-23，允许 CSI300 ∪ A500

要抽查

• [x] 随机抽 50 条训练样本，验证资产是否属于当日 universe
• [x] 抽 10 条 2024-09-23 前的样本，确认没有 A500-only 资产
• [x] 抽 10 条 2024-09-23 后的样本，确认 union 生效

当前代码/样本审计结果（2026-05-16）：

• LightGBM 的 `_create_feature_matrix()` 会先对目标交易日执行 `ensure_pit_membership_coverage(...)`，再用 `point_in_time_union_asset_ids_by_dates(...)` 构造 `membership_by_date`，最后逐行执行 `pit_in_universe = asset_id in membership_by_date[date]` 并过滤非 PIT 成员行；`_create_labels_for_training()` 在写入 label 前也有同一条 `if asset_id not in membership_by_date[target_date]: continue` 守卫。LSTM 训练直接复用这两个 helper，因此样本口径一致。
• `rebuild_lightgbm_pipeline` / `rebuild_lstm_pipeline` 在进入训练前，会先对整个训练窗口做一次 `ensure_pit_membership_coverage(...)`；如果 membership 历史缺口覆盖到训练窗口，命令层会直接失败，不会带着坏 window 继续训练。
• 对当前 live 训练窗口 `2016-06-01 -> 2024-12-31` 抽取 7d 训练标签样本做 read-only 审计：`training_sample_count=635139`，`full_membership_violations=0`。
• 随机抽 50 条训练样本，结果 `random50_bad=0`；50/50 都属于各自日期的 `effective_universe(date)`。
• 抽 10 条 `2024-09-23` 前样本，结果 `pre10_bad=0`，且全量 pre-launch 样本满足 `pre_launch_non_csi300_violations=0`；也就是说 `2024-09-23` 前训练样本仍然严格等于 CSI300 口径，没有 A500-only 资产混入。
• 抽 10 条 `2024-09-23` 后样本时，专门从 “A500 新增而非 CSI300” 的 post-launch 样本里取样，结果 `post_added10_bad=0`；样本中可见 `000009.SZ`、`000690.SZ`、`688099.SH`、`600008.SH` 等 `in_union=True` 且 `in_csi300=False` 的资产。全量 post-launch 训练样本里共有 `post_launch_a500_added_sample_count=17801` 条这类记录，说明 union 已实际进入训练集，不只是代码层声明。

因此，Section 3A 的前三条样本过滤要求和三个抽查项可以先关闭

───

B. 标签生成

• [x] 3d / 7d / 14d / 30d 的标签逻辑分别明确
• [x] 标签窗口只使用未来收益本身，不带任何未来特征
• [x] label 与 feature date 对齐，没有 off-by-one
• [x] 停牌、退市、缺失价格样本如何处理有明文规则

要抽查

• [x] 任取一个 asset/date，手工算一次 3d 和 7d label
• [x] 对边界日（如年末、停牌前）检查 label 是否异常
• [x] 标签生成前后样本数变化是否合理

当前代码/样本审计结果（2026-05-16）：

• `_create_labels_for_training(start_date, end_date, horizon_days)` 的公式是统一的：先取 `(asset, target_date)` 当日 `close` 作为 `start_price`，再取该 asset 第一条满足 `date >= target_date + horizon_days` 的未来 `close` 作为 `end_price`，只用 `forward_return = (end_price - start_price) / start_price` 打标签：`>= +2% -> UP`，`<= -2% -> DOWN`，其余为 `FLAT`。因此 3d / 7d / 14d / 30d 的标签定义本质上是同一公式，只是 `horizon_days` 不同。
• 需要区分“helper 能算”与“当前训练入口支持”：直接调用 `_create_labels_for_training(..., 14)` 时，当前 DB 可生成 `635056` 条 14d label；但 `train_lightgbm_models()`、`train_lstm_models()`、`rebuild_lightgbm_pipeline`、`rebuild_lstm_pipeline` 以及当前 API / 推理入口都只 expose `3 / 7 / 30`，不接受 14d 作为 active horizon。
• 标签窗口只使用 future price 本身，不引用任何 future feature：LightGBM 训练先构造 `labels_df(date, asset_id, label)`，再用 `X_df.merge(labels_df, on=['date', 'asset_id'], how='left')` 对齐并丢掉 `label is null` 的行；LSTM `_build_sequences()` 则拿“以 `target_date` 结尾的历史 sequence”去查 `labels_dict[(target_date, asset_id, horizon)]`。因此 label key 与 feature row 使用的是同一个 `(date, asset_id)`，没有 off-by-one，也没有把未来特征拼进训练样本。
• 对 live 训练窗口 `2016-06-01 -> 2024-12-31` 做 read-only 审计，PIT feature rows 共 `635229` 条；对应标签数分别是 `3d=635185`、`7d=635139`、`30d=634850`。样本减少仅 `44 / 90 / 379` 条，占比约 `0.0069% / 0.0142% / 0.0597%`，随 horizon 变长而增加，变化量合理。
• 上述缺 label 的行，当前样本里本质上都属于“在 helper 查询窗口 `end_date + horizon_days + 7` 内找不到满足 `date >= target_date + horizon_days` 的 future OHLCV” 而被跳过；`start_price <= 0` 当前样本里为 `0`。因此对停牌、退市、缺失价格、以及训练窗口尾部靠近年末的样本，当前规则是：拿得到未来价格就按第一条满足阈值的 future price 打 label，拿不到就直接 drop，不会硬造 label。
• 手工抽查一个普通样本：`601899.SH` 在 `2016-06-01` 的 `start_price=2.51`。3d 标签使用 `future_threshold=2016-06-04` 之后第一条价格 `2016-06-06 -> 2.55`，`forward_return=+1.5936%`，所以是 `FLAT`；7d 标签使用 `future_threshold=2016-06-08 -> 2.54`，`forward_return=+1.1952%`，也是 `FLAT`。
• 年末边界抽查：`300308.SZ` 在 `2024-12-31` 的 `start_price=122.89`。3d 标签使用 `2025-01-03 -> 122.39`，`forward_return=-0.4069%`，为 `FLAT`；7d 标签使用 `2025-01-07 -> 127.71`，`forward_return=+3.9222%`，为 `UP`。跨年没有观察到 off-by-one 异常。
• 缺 future price 的边界样本也能对上代码规则：`002123.SZ` 在 `2024-12-30` 的 3d label 需要 `future_threshold=2025-01-02` 之后的价格，但该 asset 下一条 OHLCV 到 `2025-01-16` 才出现，已经超出 3d helper 的查询上界 `2025-01-10`；因此这条样本会被视为“无可用 future price”而跳过。7d 下同理，`2024-12-25` 到 `2024-12-30` 的几条末端样本也都会因下一条价格晚到 `2025-01-16` 而被 drop。

因此，Section 3B 当前可以先关闭：label 定义本身是“同日 feature + 未来第一条满足阈值的价格”的纯 future-return 规则，没有观察到 off-by-one 或 future-feature 泄漏；需要额外记住的实现细节只有两点，一是 active horizon 仍然只有 `3 / 7 / 30`，二是长停牌/退市/窗口尾部缺未来价时样本会被直接丢弃。

───

C. 特征 schema 一致性

• [x] 训练时 feature list 固化
• [x] 不允许 feature 缺失后静默补 0 而不记录

需要保存到 artifact 的信息
• [x] feature_names
• [x] feature_count
• [x] training_window_start/end

当前代码/实现状态（2026-05-16，null-handling 改造已落代码，但 active artifact 仍需重训才能完全切到新口径）：

• 对“训练时 feature list 固化”这一条，当前 real ML 训练路径可以先关闭：`_create_feature_matrix()` 在返回的 DataFrame attrs 里固定 `feature_names`；LightGBM 训练对 `selected_feature_names` 精确取列训练，并把同一份列表写进磁盘 `metadata.json`、`LightGBMModelArtifact.feature_names`、以及按 horizon 注册的 `ModelVersion.feature_schema`。当前 active `LightGBMModelArtifact` 的 `lgb-3d/7d/30d-2024-12-31` 三个 artifact 都是 `feature_name_count=39`，且都满足 `feature_schema_size=39`、`training_window_start=2016-06-01`、`training_window_end=2024-12-31`。
• LightGBM 新训练路径现在会显式使用 `missing_value_strategy='native_nan'`：upstream missing、财报未披露、warm-up gap、跨表 join miss、以及 stale technical value 不再被统一改写成 `0.0 / 0.5 / 1.0 / 50.0`，而是作为 `NaN` 留给树模型自己学 split direction。与此同时，artifact metadata / disk `metadata.json` 会记录 `missing_value_strategy`，因此新 artifact 已经具备缺失值口径的 provenance 字段。
• LSTM 训练不再直接吞 raw `NaN` tensor，而是复用同一份 raw-NaN feature matrix 后，把每个 base feature 扩成 `[feature, feature__is_missing]` 两列；numeric branch 在 scaler 之后才把 `NaN -> 0.0`，missingness 本身通过 mask 列保留下来。因此 LSTM 现在的 missingness contract 是“mask-and-zero-impute after scaling”，不是旧版的无区分 `0.0` / `0.5` neutral fill。
• LSTM 训练也因此不再只是复用“同一份 feature schema”，而是复用“同一份 base feature schema + 明确的 missingness mask 扩展”。新的 `ModelVersion.feature_schema` 会包含这些 `__is_missing` 列，metadata 里也会记录 `missing_value_strategy='mask_and_zero_impute'`。线上运行是否已经切到这套 schema，取决于环境里当前 active/READY 的真实 LSTM artifact；如果数据库里还残留历史 stub row，LSTM runtime 现在会优先跳过 stub，而 `train_prediction_models()` 也会在刷新 ensemble baseline 时把 active stub 退役。
• 因此，`feature_names` / `feature_count` / `training_window_start/end` / `horizon` 这些信息，在当前 real LightGBM / LSTM artifact surface 上已经实际落盘或落库；一个代表性 host 路径 `models/lightgbm/3d_lgb-3d-2024-12-31/metadata.json` 当前存在，键集至少包括 `feature_names`、`horizon_days`、`training_window_start`、`training_window_end`、`version`、`trained_at`、`lgb_params`、`pruning`。
• 但 “artifact 保存 feature schema、训练窗口、universe policy、horizon” 这一整条还不能关闭，因为 `effective_universe_policy` 还没有被显式保存；与此同时，`label_definition`、`code_version` / `git_commit`、`data_snapshot_version` 也都没有出现在当前 active `LightGBMModelArtifact` metadata 里。
• 推理侧仍然不满足 strict schema validation。新 LightGBM artifact 会按 `missing_value_strategy` 走 raw-NaN path，而 legacy artifact 仍保持旧 sentinel fallback；LSTM 新 artifact 会把缺失值编码成 mask + finite tensor，但这仍然不是“遇到 schema drift 直接报错”的 fail-fast contract。也就是说，“strictly validate schema” 这一条还不能关闭。
• “不允许 feature 缺失后静默补 0 而不记录” 这条目前也只能算部分修复：LightGBM 新 artifact 已不再把 genuine missingness 无差别补 `0.0`，LSTM 新 artifact 会显式记录 `missing_value_strategy` 并把 missingness 编进 `__is_missing` 列；但 legacy artifact 仍会沿用旧行为，而且当前代码还没有统一的 warning / audit event 去记录所有 runtime schema mismatch。
• registry split 的持续污染源已经移除：`train_prediction_models()` 不再生成 `phase14_training_stub` 的 generic LightGBM/LSTM `ModelVersion` 行，而是只刷新 ensemble baseline；同时，它会把历史 active stub 行退役。对于老数据库里残留的 inactive stub 历史记录，现在也可以用一次性管理命令 `python manage.py purge_prediction_model_stubs --apply` 直接清掉。因此，这个问题现在主要收缩成 provenance 字段仍不完整的审计问题，而不是 runtime 还会继续被新的假 registry 行污染。

因此，Section 3C 当前仍然只能部分关闭：真实训练 artifact 已经能固定并保存 feature list / feature count / training window，并且新代码已经把 genuine missingness 从“统一 sentinel fill”推进到了 “LightGBM raw NaN / LSTM mask-aware finite tensor” 的更合理契约；`train_prediction_models()` 这条 registry pollution 路径也已经被移除。但 strict schema validation、统一 warning/audit、以及 `effective_universe_policy` / `label_definition` / `code_version` / `data_snapshot_version` 等 provenance 字段都还没补齐。另一个实际落地动作仍然是：需要重训并切换 active artifact，才能让 live 预测全面采用这套新 missingness contract。

───

D. 模型质量

所以要查：

• [x] 训练集/验证集/样本外测试集是否严格分时: manually checked
• [x] 没有用 2025 的信息去选 2024 前的模型
• [x] 不同 horizon 没有共用错误标签或错误 artifact

如果前 3 个漂亮，第 4 个长期不行，问题大概率还是：

• 特征无效
• 标签不对
• 或横截面口径脏

当前代码/样本审计结果（2026-05-17）：

• LightGBM 当前不满足“训练集 / 验证集 / 样本外测试集严格分时”。`train_lightgbm_models()` 会先把整窗 `X_df` 与 `labels_df` 对齐成 `X_train_aligned`，随后直接对全量矩阵执行 `scaler.fit_transform(X_train)`、`lgb.train(...)`，最后再在同一份 `X_train_scaled` 上回算 `accuracy = mean(y_pred == y_train)`。代码里没有独立 validation split，也没有 out-of-sample test slice。因此当前 active `LightGBMModelArtifact.metrics_json['accuracy']` 本质上是 in-sample training accuracy，不是严格时间切分下的 validation / OOS 指标。
• LightGBM 的 calibration 也不是 time-aware validation：训练后直接 `CalibratedClassifierCV(model, method='sigmoid', cv=5)` 作用在同一份训练矩阵上，代码里没有传入任何基于日期的 splitter。也就是说，即使这里不一定显式 random shuffle，它仍然不是“按时间顺序切 train / val / test”的协议。
• LSTM 的 split 逻辑相对更干净：`_train_single_horizon_lstm()` 会先按 `sample_dates` 升序排序，再做前 80% 训练、后 20% 验证；scaler 也只在 `X_train` 上 fit，再 transform `X_train / X_val`。因此它满足“训练 / 验证严格先后切开”，但仍然没有独立样本外测试集，所以第一条在系统层面还不能关闭。
• LSTM 仍然存在训练阶段的随机打乱：虽然 train / val 边界是按时间切开的，但 `DataLoader(train_ds, batch_size=128, shuffle=True)` 会把训练切片内部的 batch 顺序打乱。因此如果 checklist 的要求是“整个训练过程完全不打乱时间顺序”，第二条也还不能关闭；当前最多只能说“没有观察到 train / val 穿越式泄漏”。
• “没有用 2025 的信息去选 2024 前的模型” 当前主路径未发现明显时间穿越：active LightGBM artifacts 是 `lgb-3d/7d/30d-2024-12-31`，训练窗口都结束于 `2024-12-31`；LSTM registry 虽然仍挂着 active stub `lstm-2026-05-16`，但 runtime `_resolve_lstm_model_version(2026-05-16)` 实际解析到真实训练行 `lstm-2024-12-31`，其 `training_window_end` 同样是 `2024-12-31`。因此“post-2024 data 直接参与当前 main ML runtime artifact selection” 这一条当前未观察到。
• “不同 horizon 没有共用错误 label / artifact” 这一条当前可以先关闭：LightGBM 每个 horizon 都单独调用 `_create_labels_for_training(..., horizon)`、单独生成 `lgb-{horizon}d-...` version、单独注册 `LightGBMModelArtifact(horizon_days=...)`；LSTM 则按 `labels_by_horizon[horizon]` 构造标签，并在同一 version 目录下分别落 `3d_model.pt`、`7d_model.pt`、`30d_model.pt`。当前 host 文件系统里也确实存在 `models/lstm/lstm-2024-12-31/3d_model.pt`、`7d_model.pt`、`30d_model.pt` 三套独立 artifact。

因此，Section 3D 当前只能部分关闭：`不同 horizon 串 label / 串 artifact` 这一类错误当前未发现，`post-2024 information 直接选 pre-2025 model` 在主路径上也未发现；但更根本的验证纪律仍然不达标，因为 LightGBM 还没有真正的时间切分 validation / OOS，LSTM 也还没有独立 OOS test，而且训练阶段仍有 batch shuffle。换句话说，Section 3D 现在暴露的主问题不是 horizon 混线，而是 validation protocol 还不够硬。

───

四、backtest 层 audit

这一层查的是：回测有没有按你以为的方式执行。

A. 候选池生成

• [x] 每个交易日的 candidate pool 只来自 effective_universe(date)
• [x] top_n_metric 与 horizon 一致
• [x] up_threshold 真正生效
• [x] candidate_mode=top_n 真正生效
• [x] trade_score_scope=independent 真正生效

要抽查

• [x] 随机抽 5 个交易日，导出候选股票清单
• [x] 检查候选股票是否都属于当日 universe
• [x] 检查排序分数是否来自对应模型输出

当前代码/样本审计结果（2026-05-17）：

• 候选池的总闸门是 `_eligible_backtest_asset_ids(dt, cache)`：先取当日有 `OHLCV` 的 `asset_id`，再执行 `ensure_pit_membership_coverage([dt], ...)`，最后只保留 `point_in_time_union_asset_ids(dt)` 内的资产。`_build_heuristic_prediction_map()`、`_build_lightgbm_prediction_map()`、`_build_lstm_prediction_map()` 三条 prediction-source 路径都逐个迭代这同一个 helper，因此 candidate pool 的 universe gate 在 heuristic / LightGBM / LSTM 三条路径上是一致的。
• `top_n_metric` / `horizon` 对齐当前可以关闭：serializer 层 `BacktestRunSerializer.validate()` 会把 `up_prob_3d/7d/30d` 对齐到对应 `horizon_days`；runtime 侧 `_top_n_metric(run, fallback_horizon)` 也会再次把 `metric_horizon` 解析成 `3 / 7 / 30`。已有 regression `Phase15BacktestTests.test_backtest_serializer_aligns_horizon_with_top_n_metric` 覆盖了这条规范化逻辑。
• `up_threshold` 当前确实生效：`_build_heuristic_candidates()`、`_build_lightgbm_candidates()`、LSTM top-n 分支、以及 `trade_score` 分支都会先过滤 `up_probability < up_threshold` 的资产。对 read-only 样本日 `2024-02-06`，同一套 heuristic top-n 配置在 `up_threshold=0.55` 时返回 `5` 个 candidate，而把阈值提高到 `0.80` 后只剩 `3` 个，说明阈值不是写在参数里但没执行。
• `candidate_mode=top_n` 当前也确实生效：top-n 路径直接按 `top_n` 截断，`_max_positions()` 只在 `trade_score` 模式下生效。已有 regression `Phase15BacktestTests.test_top_n_mode_ignores_max_positions_but_trade_score_mode_honors_it` 已经覆盖“`top_n=2`、`max_positions=1` 仍返回 2 笔 top-n candidate，而 trade_score 模式只保留 1 笔”的差异；对样本日 `2024-02-06` 的 unsaved run 复核也得到 `rows=2`、`candidate_mode=['top_n']`。
• `trade_score_scope=independent` 当前可以关闭：`_build_trade_score_candidates()` 在 `scope != 'combined'` 时会明确写入 `trade_score_scope='independent'`，并按单一 prediction source 的 `trade_score` 排序。已有 regression `Phase15BacktestTests.test_trade_score_mode_supports_runtime_lstm_candidates_without_stored_predictions` 明确断言 payload 里的 `trade_score_scope='independent'`；对样本日 `2024-02-06` 的 unsaved heuristic run 复核结果是 `rows=3`，且所有 payload scope 都等于 `independent`。
• 对 `2024-01-01 .. 2026-04-30` 的 distinct trading dates 设 `random.seed(42)` 做 read-only 抽样，得到 5 个样本日：`2024-02-06`、`2024-06-26`、`2024-12-12`、`2025-01-14`、`2025-03-06`。使用 unsaved heuristic run（`candidate_mode='top_n'`、`top_n_metric='up_prob_7d'`、`top_n=5`、`horizon_days=7`、`up_threshold=0.55`）直接调用 `_pick_candidates()`，各日结果分别是：
• `2024-02-06`：`candidate_count=5`，`eligible_universe_count=300`，`violations=0`；candidate list = `688041, 300308, 300896, 688036, 300760`
• `2024-06-26`：`candidate_count=5`，`eligible_universe_count=300`，`violations=0`；candidate list = `688041, 002594, 688008, 600941, 002371`
• `2024-12-12`：`candidate_count=5`，`eligible_universe_count=566`，`violations=0`；candidate list = `603236, 002607, 600115, 002292, 300442`
• `2025-01-14`：`candidate_count=5`，`eligible_universe_count=563`，`violations=0`；candidate list = `688099, 603986, 002050, 603129, 002625`
• `2025-03-06`：`candidate_count=5`，`eligible_universe_count=562`，`violations=0`；candidate list = `688041, 002195, 300454, 603882, 688072`
• 上述 25 条 sampled candidates 都满足 `rank_value == signal_payload.up_probability`，同时 `signal_payload.top_n_metric='up_prob_7d'`、`signal_payload.horizon_days=7`，说明排序分数确实来自对应模型输出而不是别的列。另做一条 mismatch 复核：在 `2024-02-06` 上显式传 `top_n_metric='up_prob_30d'`、`horizon_days=7` 的 unsaved run，返回 payload 明确是 `top_n_metric='up_prob_30d'` 且 `horizon_days=30`，说明 top-n 排序真正跟 metric 对应 horizon 走，不会悄悄退回 7d。
• 当前唯一还值得记住的限制不是 candidate picker 本身，而是 validation harness coverage：`run_validation_backtests` 命令默认只生成 `top_n + horizon_days + up_threshold + prediction_source` 这一条基本路径，不会主动覆盖 `candidate_mode='trade_score'` 或自定义 `top_n_metric` 组合。这些非默认分支当前是靠单元测试 + 上面的 read-only manual audit 兜住的，而不是默认验证命令每天都跑到。

───

B. 交易执行逻辑

• [x] 开仓日只在 entry_weekdays
• [x] 平仓优先级明确：先 TP/SL 还是先持有期结束
• [x] holding_period_days 真正生效
• [x] 仓位上限、单笔资金比例、手续费、滑点都生效
• [x] 停牌/无法成交的处理有规则
• [x] create a fee model for transaction fees replacing the current .1% flat fee

你之前已经踩过：

• [x] TP/SL 配置传下去了没有
• [x] LightGBM 路径和 heuristic 路径执行逻辑是否一致
• [x] 参数写进 report 但实际上没执行，这种假生效必须排除

当前代码/测试审计结果（2026-05-17）：

• `run_backtest()` 的日循环固定是“先平仓，再判断是否开仓”。开仓只经过 `_should_enter_position(current_date, entry_weekdays)`；`entry_weekdays` 由 `_normalize_entry_weekdays()` 统一成 weekday index。现有 regression `test_backtest_supports_tuesday_thursday_top3_seven_day_hold` 复核到 buy dates 只出现在 `2026-04-07`、`2026-04-09` 两个 Tue/Thu 日，report 里也记录 `entry_weekdays=[1, 3]`。
• `holding_period_days` 当前真实生效：`_resolve_exit_date()` 会取“第一条 `>= entry_date + holding_period_days` 的交易日”作为计划平仓日，而不是简单按自然日强平。上面的 Tue/Thu 样本里，`holding_period_days=7` 对应 sell dates 是 `2026-04-14`、`2026-04-16`，与计划持有天数一致。
• 平仓优先级当前可以明确关闭：`_close_positions_for_date()` 先读当日 close；若启用 `enable_stop_target_exit`，先判 `STOP_LOSS`，再判 `TARGET_PRICE`，最后才落到 `SCHEDULED`。因此 TP/SL 在计划平仓日也会优先于普通持有期卖出。已有/新增 regressions 分别覆盖了 `TARGET_PRICE` 和 `STOP_LOSS` 分支。
• “停牌/无法成交” 现在也有明确规则，而且这一轮刚修掉一个真实缺口：开仓日如果 `buy_close` 缺失或 `<= 0`，`_open_positions_for_date()` 会直接 skip，不造假成交；平仓扫描日如果 `sell_close` 缺失或 `<= 0`，仓位会继续保留，直到后续出现第一条可成交 close 再执行卖出。原先这里存在“错过 scheduled exit 后不再重试”的缺陷，现已修复，并由 focused regression `test_scheduled_exit_retries_on_next_tradeable_price_after_invalid_close` 锁住。
• 仓位/成本参数当前确实执行，但要区分“默认真实费率”与“legacy override”两层语义：默认 backtest fee model 现在按 A 股现货费率走，买入是 `max(佣金 0.1‰, 5)` 加上 `经手费 0.0341‰ + 监管费 0.02‰ + 过户费 0.01‰`，卖出则在此基础上额外加 `印花税 0.5‰`；如果显式传 `fee_rate`，则回退到旧的对称 flat-fee override 供历史实验兼容。`capital_fraction_per_entry` 仍然通过 `deployable_capital = min(cash, initial_capital * capital_fraction)` 生效；`max_positions` 只在 `candidate_mode='trade_score'` 时作为并发持仓上限生效，`top_n` 模式仍由 `top_n` 控制。focused regressions 现在同时覆盖了默认 A 股费率（含 `5` 元最低佣金与卖出单边印花税）和 `fee_rate=1%` 的 legacy flat-fee override；后者样本里单笔总买入成本约等于 `25000`，买入成交价从 `10.00` 变成 `10.10`，卖出成交价从 `11.00` 变成 `10.89`。
• “TP/SL 配置传下去了没有” 当前可以关闭：LightGBM / heuristic / LSTM 三条 candidate builder 都把 `target_price` / `stop_loss_price` / `trade_score` 归一到同一份 `signal_payload`，`_open_positions_for_date()` 在落 BUY 前还会调用 `_backfill_prediction_trade_decision()` 补齐缺字段。现有 regressions `test_lightgbm_top_n_stop_target_exit_uses_propagated_levels`、`test_lightgbm_top_n_applies_trade_decision_policy_to_payload`、`test_open_positions_backfills_missing_prediction_trade_decision_fields` 都覆盖了这一点。
• “LightGBM 路径和 heuristic 路径执行逻辑是否一致” 当前未发现分叉：source-specific 差异只在 candidate/prediction payload 生成，真正的成交、持仓、TP/SL、scheduled exit、fee/slippage 都走同一个 `_open_positions_for_date()` / `_close_positions_for_date()` 执行层。上面的 LightGBM target test 和 heuristic stop-loss / invalid-entry tests 实际都落在同一执行分支。
• “参数写进 report 但实际上没执行” 当前未观察到假生效：`entry_weekdays` / `holding_period_days` / `enable_stop_target_exit` 仍会写进 `run.report`；这一轮又把 `fee_model` 和 `fee_parameters` 也写进了 report，同时 BUY/SELL trade metadata 会保存 `fee_breakdown`。`capital_fraction_per_entry`、structured fee 参数、legacy `fee_rate`、以及 `slippage_bps` 仍然由 `run_backtest()` 直接读取执行，`export_backtest_runs` 也会把 fee 参数导出到 `run_config_results.csv`。上面的 focused regressions 已经直接验证这些参数会改变成交价、手续费组成、和平仓后的实际 PnL。

───

D. 结果可追溯性

每个 backtest run 最好都能追到：

• [x] compare_backtest_run_id
• [x] model artifact id / model version id
• [x] horizon

这样你以后看到结果变化，能知道是：

• 数据变了
• membership 变了
• 训练变了
• 还是回测逻辑变了

当前代码/测试审计结果（2026-05-17）：

• `compare_backtest_run_id` 当前可以关闭：serializer 已经强校验 compare target 必须存在、已完成、同 strategy type、且 prediction source 兼容；comparison payload 也会把它回传给前端。这一轮又把它补进了 `run.report['compare_backtest_run_id']`，并补进 `export_backtest_runs -> run_config_results.csv`，所以 run-level 和 export-level 两条 traceability 路径现在是一致的。
• `horizon` 当前可以关闭：`BacktestRun.parameters['horizon_days']` 仍是 canonical config，trade signal payload 里每笔 BUY/SELL 也都带 `horizon_days`；这一轮又把归一化后的 `run.report['horizon_days']` 固化下来，因此单看 run 本身也能知道这是 3d / 7d / 30d 的哪一档回测。
• “model artifact id” 这一条在当前系统里应该更准确地理解为 “model artifact / model version reference”：LightGBM 路径用 `model_artifact_id`，heuristic / LSTM 路径用 `model_version_id`。这两类 reference 原本就在 trade `signal_payload` 里、也会被 `model_references.csv` 导出；这一轮又新增了 `run.report['model_references']` + `model_reference_count`，把当前 run 实际打到的 registry reference、version、horizon、feature_count、training_window_start/end 聚合到 run-level summary 上。因此这条可以按“reference id 可追溯”关闭，而不是狭义地只盯 LightGBM artifact id。
• benchmark 侧当前只能算部分可追：`run.report['benchmark']` 已经会保存 `strategy`、`source`、`benchmark_code`、`weighting_method`，足以区分“预计算 PIT benchmark” vs “runtime equal-weight fallback”；但还没有独立的 `benchmark_build_version` / benchmark artifact id，所以“benchmark build version” 仍不能关闭。
• feature schema / universe policy 也仍是部分可追：新加的 `run.report['model_references']` 会尽量从 registry metadata 里带出 `schema_version`、`effective_universe_policy`、`universe_version`、`label_definition`、`data_snapshot_version`、`code_version` / `git_commit` 等字段；但这些键是否存在取决于上游 LightGBM / LSTM artifact metadata 本身。当前 active artifacts 还没有把这些 provenance 字段补齐到位，所以 4D 后三条仍应继续保持 open。
• focused regressions 已覆盖这轮 traceability 改动：`test_run_backtest_supports_lightgbm_prediction_source` 现在会断言 run report 里确实写入 `compare_backtest_run_id`、`horizon_days`、`model_references`；`test_export_backtest_runs_includes_compare_backtest_run_id` 会断言 compare target 真实导出到 `run_config_results.csv`。因此，Section 4D 当前可以按“基础比较/模型/周期 provenance 已落 run-level + export-level，schema/universe/benchmark version 仍未补齐”来收口。

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

七、建议你最后产出 4 份审计结果



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


extras:
1. close 实际上已经是 qfq 值，而 adj_close 现在也是同一个值，没有保留未复权原始 close。把 OHLCV 的复权语义明确下来：新增 raw_close，避免 close 和 adj_close 现在这种“值一样但名字不同”的状态
2. 没有 limit_up / limit_down 规则. 没有按昨收去判断 10% / 20% / ST 涨跌停板. 没有按交易所制度去区分主板、创业板、科创板、北交所的不同涨跌幅限制. 也没有“超大日收益跳变”这类 return-based price anomaly 规则. 涨跌停是否被标记：否.
3. macro snapshot yeild curve is using the first day data for each month. cny/usd is using monthly open. we should switch to use the previous monthly ohlcv data instead.macro snapshot and market context sync and backfill按发布日期/可得日期对齐，不是按统计期硬贴
4. for macrosnapshot US Dollar Index (DXY): rename it to Dow Jones FXCM Dollar Index Basket (USDOLLAR) since that is the index we are currently syncing
5. Runtime recomputation → Stored TechnicalIndicator
6. LightGBM and LSTM training artifacts should include: effective_universe_policy version, label_definition, code_version / git commit, data_snapshot_version, schema_version. we need also record the versions during backtests.
7. artifact 保存 feature schema, 推理时严格校验 schema
8. 模型质量与过拟合

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
   python manage.py backfill_signal_events --start-date 2010-01-04 --end-date 2026-04-30 --chunk-size-days 120 --checkpoint-file reports/ops_logs/signal_event_backfill_20260514.json
   python manage.py backfill_model_data --start-date 2010-01-04 --end-date 2026-04-30 --checkpoint-file reports/ops_logs/backfill_model_data_20260510_1.json
   ```

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
| `backfill_technical_indicators` | Backfill non-RS technical indicators from OHLCV | `--start-date`, `--end-date`, `--symbols`, `--limit-assets`, `--technical-indicators` |
| `backfill_signal_events` | Backfill non-RS historical `SignalEvent` families from OHLCV | `--start-date`, `--end-date`, `--symbols`, `--limit-assets`, `--signal-types`, `--chunk-size-days`, `--checkpoint-file`, `--resume-from-checkpoint` |
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
