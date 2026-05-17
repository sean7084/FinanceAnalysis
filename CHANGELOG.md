# Changelog

### version 0.1.13

#### Validation Checklist:

一. fundamental data coverage

B. 因子/特征原始源

• [x] backfill pre-warmup ohlcv data for pre 2010-1-4 and pre index listing for calculation of ohlcv-derived data
1. backfill the pre 20100104 and pre index-listing data for 18 months for technical data calculation and rs_score calculation
• [x] fina_indicator 类季度财务指标是否正确 as-of 对齐，不用未来财报
• [x] 技术指标是否只用到当日及过去数据
• [x] macro snapshot validation and backfill: DXY, CNY/USD, China 6M/1Y/3Y/5Y/7Y/10Y/30Y Yield, PMI Manufacturing, PMI Non-Manufacturing, and CPI YoY
1. backfill the pre 201607 data from manually downloaded csv from gov site
2. switch from curve term = [10, 2] to [0.5, 1, 3, 5, 7, 10, 30]
3. switch runtime yield_curve to cn10y_yield - cn3y_yield
4. stick to curve type = 曲线类型：0-到期
5. rescheduled the syncing to the second day of each month at 0:10 am
6. maps the Yahoo monthly open onto month-start MacroSnapshot.cny_usd rows for 2010-01 through 2012-02 
• [x] market context validation and backfill 
• [x] Capital Flow Snapshots validation and backfill: 1311 gap periods due to upstream data source blackouts. otherwise the coverage should be complete since 2010-01-04 for CSI300 constituents, and since 2024-09-23 for A500 constituents.
• [x] technical indicator validation and backfill
• [x] fundamental snapshot validation and backfill
1. switched pe to pe_ttm
2. 55 assets have null pe_ttm rows due to TuShare upstream data source blackouts
• [x] added stale gate to localy computed indicators
1. refer to the updated technicalguide.md for the stale gate rules for each technical indicator
• [x] added calendar date validation if there are duplicated rows from the upstream
• [x] dispose pretrade_date from the system: `ExchangeTradingCalendar` stores TuShare `cal_date` as `trade_date` plus `is_open`; open-day consumers filter on `is_open=True`


要抽查

• [x] PE/PB/ROE 在 2010-2026 是否不是大面积空值
• [x] 任取一个日期，检查该日 snapshot 是否引用了未来季度财报或未来宏观值：抽查 2024-04-15，300 个 effective-universe assets 均未引用未来 `fina_indicator_ann_date` / report period / daily-basic source date；MacroContext 指向 2024-04-01 MacroSnapshot（当前 MacroSnapshot 尚未存 per-field release date）



───

C. 横截面特征

• [x] rs_score: missing values
• [x] factor score: Composite Score, Bottom Probability Score, Fundamental Score, Capital Flow Score, Technical Score

都必须确认：

• [x] 只在 当日 effective_universe 内计算
• [x] 不是拿全市场算
• [x] 不是拿未来扩大后的 universe 倒推过去
• [x] 不是 membership 缺失时直接退化成 all assets 且无告警

check random 10 dates, 每个日期核查：

• [x] universe size
• [x] rank 分位数分布
• [x] 参与横截面排名的股票清单
• [x] 是否与 membership 一致

抽查 10 个交易日：2010-01-04、2011-05-20、2013-12-31、2016-06-01、2020-03-16、2024-09-20、2024-09-23、2025-01-02、2025-12-31、2026-03-02。

结果：`FactorScore` 五个核心字段（`composite_score`、`bottom_probability_score`、`fundamental_score`、`capital_flow_score`、`technical_score`）在 10 个日期均与 `effective_universe(date)` 精确一致，`unexpected_outside_universe_count = 0`、`missing_from_universe_count = 0`；`RS_SCORE` 仍有少量 missing rows，但 10 个日期均无 outside-universe participants，且所有抽查字段取值均落在 `[0, 1]`。

D. 数据覆盖率输出

• [x] 每类 snapshot 都有 coverage report
• [x] 每类 snapshot 至少包含：
  • 日期
  • effective_universe_count
  • feature_non_null_count
  • usable_asset_count
  • dropped_asset_count
  • missing_by_feature

当前这些字段已统一出现在 `effective_universe_daily_coverage.csv` 中。

红灯项：

• [x] 未发现某关键日期 usable_asset_count 突然断崖式下降
• [x] 某大类特征在长时间段大量空值: `margin_balance_change_5d` 和 `pe_ttm_percentile_score` 主要来自 TuShare/source-side 缺口；抽查到的 `RS_SCORE` missing 则是停牌导致 exact-window 不成立
• [x] cross-sectional ranking 的母集和 effective_universe 不一致

当前 bundle 未出现 `usable_asset_count_cliff` 红灯；但 `margin_balance_change_5d` 仍存在长时间段稀疏覆盖，`null_reason_buckets.csv` 记录了 `820934` 条 `missing_margin_detail_source_row` 和总计 `826243` 条 `margin_balance_change_5d.expected_field_null`。这类缺口主要是原始 `margin_detail` 源缺失，5 日差分 warmup 已由单独的 `margin_diff_5_warmup_insufficient` reason 统计。`pe_ttm_percentile_score` 的抽查 missing 则来自最新 `FundamentalFactorSnapshot.pe_ttm = NULL`，对应 TuShare `daily_basic.pe_ttm` 的 source-side null / blackout。另一方面，当前抽查到的 `RS_SCORE` missing participants 不是 universe 泄漏，而是 full-day suspension 让 exact 20-trading-day window 不成立；这部分属于预期跳过而不是 TuShare blackout。

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

### version 0.1.12

**Objective**: package the current release candidate around lifecycle-aware historical data governance, strict point-in-time universe enforcement, expanded data-quality audits, richer macro term-structure coverage, and Linux-native local operations.

**Implemented Features**:

- Added lifecycle-aware market data surfaces and repair tooling:
  - extended `Asset` with `delist_date`; added official `ExchangeTradingCalendar` and `AssetSuspension` models, admin surfaces, and API exposure
  - added `backfill_trading_calendar`, `backfill_asset_suspensions`, and `reconcile_suspension_ohlcv_overlaps`
  - updated `sync_daily_a_shares` and `sync_asset_history` to sync official trading calendars, suspension days, explicit repair windows, and asset lifecycle metadata
  - expanded `backfill_ohlcv_history` to support report-driven repair windows, technical-indicator warm-up prefills, and effective-universe-entry warm-up backfills

- Enforced one canonical point-in-time universe contract across scoring, training, prediction, and backtests:
  - documented and codified `effective_universe(date)` as `CSI300` from `2010-01-04` through `2024-09-22`, then `CSI300 ∪ CSI A500` from `2024-09-23` onward
  - added `ensure_pit_membership_coverage`, required-index resolution, and explicit PIT coverage gap helpers in `apps/markets/benchmarking.py`
  - removed silent fallback-to-all-assets behavior from daily RS score refresh, factor scoring, runtime prediction, backtest asset selection, model-data backfill, and LightGBM/LSTM training windows
  - routed daily prediction through date-aware `effective_universe_assets(...)` instead of current tag-based asset selection

- Expanded data-quality validation from simple gap scans into lifecycle and dependency audits:
  - rewrote `validate_data_quality` around official exchange calendars instead of inferring trading dates from OHLCV
  - added reports for index-membership history gaps, monthly blank membership windows, benchmark and PIT benchmark gaps, OHLCV excused gaps, price anomalies, lifecycle issues, feature dependency gaps, feature-source as-of issues, effective-universe daily coverage, cross-sectional participant audits, and optional report scoping
  - added sampled upstream fundamental reconciliation against shared TuShare `daily_basic` / `fina_indicator` materialization logic
  - distinguished expected vs suspicious capital-flow nulls and labeled continuity gap reasons for moneyflow- and margin-derived features

- Refined feature backfills and technical-indicator persistence:
  - extracted shared fundamental materialization helpers so backfill and audit paths recompute the same as-of rows
  - made capital-flow backfills reuse existing raw lookback windows for single-day reruns instead of losing derived 5-day calculations
  - added `backfill_technical_indicators` for non-`RS_SCORE` indicator history and widened `TechnicalIndicator.value` precision so large `OBV` rows persist without overflow
  - added indicator warm-up helpers, RS-score prefill logic, and checkpoint/resume support with stage timing summaries for long-running `backfill_model_data` runs

- Expanded macro history and runtime macro semantics:
  - replaced the old `10Y - 2Y` slope with a fuller China yield surface (`6M/1Y/3Y/5Y/7Y/10Y/30Y`) and runtime `10Y - 3Y` spread
  - backfilled pre-`2016-07` monthly government yields from ChinaBond CSV, and pre-`2012-03` `CNY/USD` month-start rows from Yahoo CSV before TuShare takes over
  - constrained TuShare yield ingestion to `curve_type=0`, persisted per-field source metadata, and normalized monthly macro rows onto month-start dates
  - updated admin, serializer, runtime phase inference, and regression tests to match the new yield fields

- Improved native local-dev and ops workflows outside Docker:
  - moved Django settings and env loading onto `.envs/.local` plus `.venv` defaults, including `TUSHARE_TOKEN` and localhost Redis broker/cache defaults
  - added native helper scripts for backend, Celery worker, Celery beat, environment bootstrapping, and local stack verification
  - updated Vite/API/WebSocket defaults to use relative `/api` and `/ws` paths with local proxying
  - rewired smoke and staged-news-backfill shell helpers to run against the native virtualenv stack instead of `docker compose exec`

- Refreshed operator documentation:
  - updated `README.md` for the Linux-native local stack, benchmark-aware system status, and validated command inventory for backfill, validation, and training flows
  - updated `TechnicalGuide.md` to reflect the canonical effective-universe rules and the expanded macro yield surface



**Current Notes**:

- `validate_data_quality` now treats `ExchangeTradingCalendar` as the official source of opening days; OHLCV continuity excludes pre-listing dates, on/after-delist dates, and suspension-covered dates instead of inferring expectations from stored bars.
- Normal PIT-aware workflows now fail fast when required `IndexMembership` history is missing rather than silently widening to the full asset table.
- Historical warm-up repairs may intentionally reach before the shared floor only when the command is explicitly running a bounded repair or feature warm-up window.
- Stored `PredictionResult` and `LightGBMPrediction` rows remain available as the live daily snapshot layer for stock-level prediction flows, but backtests continue to regenerate heuristic, `LightGBM`, and `LSTM` candidates at runtime and no longer rely on prediction-history list/admin surfaces.

**Key Files**:
- `apps/markets/benchmarking.py`
- `apps/markets/tasks.py`
- `apps/markets/models.py`
- `apps/markets/management/commands/backfill_ohlcv_history.py`
- `apps/core/management/commands/validate_data_quality.py`
- `apps/analytics/management/commands/backfill_technical_indicators.py`
- `apps/factors/fundamental_materialization.py`
- `apps/macro/management/commands/backfill_macro_snapshots.py`
- `apps/prediction/management/commands/backfill_model_data.py`
- `scripts/_native_env.sh`
- `README.md`
- `TechnicalGuide.md`

**Focused Coverage Added**:
- Added or expanded regression coverage across `apps.core.tests`, `apps.markets.tests`, `apps.analytics.tests`, `apps.factors.tests`, `apps.macro.tests`, `apps.prediction.tests`, `apps.prediction.tests_lightgbm`, `apps.backtest.tests`, and `apps.sentiment.tests` for lifecycle-aware validation, PIT coverage enforcement, macro backfills, technical-indicator backfills, capital-flow warmups, checkpointed model-data backfills, and native runtime behavior.


### version 0.1.11

**Objective**: package the current `80`-file release candidate into a commit-ready `v0.1.11` focused on dual-index benchmark operations, point-in-time benchmark infrastructure, benchmark-aware backtest comparison, historical data-floor hardening, and refreshed model artifacts.

**Implemented Features**:

- Expanded the runtime universe from a single CSI 300 path into a CSI 300 + CSI A500 operational workflow:
  - added `membership_tags` on assets plus historical `IndexMembership`, official `BenchmarkIndexDaily`, and internal `PointInTimeBenchmarkDaily` storage
  - added `sync_index_constituents`, `sync_benchmark_index_history`, `build_pit_union_benchmark`, `onboard_csi_a500_universe`, and `rollout_csi_a500_universe`
  - rewired `sync_daily_a_shares` so it refreshes benchmark history, fans out OHLCV syncs, and then runs a post-sync universal refresh for PIT benchmark, capital flow, factor scores, and signals
  - added a monthly membership refresh path so constituent changes can backfill only affected assets instead of replaying the full universe every time

- Made model inputs and backtests respect the effective benchmark universe instead of the broad stored asset table:
  - factor scoring, heuristic predictions, LightGBM/LSTM runtime inference, RS score generation, and model-data backfills now filter to active or point-in-time union assets when membership history exists
  - LightGBM feature-matrix and label generation now apply PIT membership filters by trade date
  - backtest candidate selection now routes through one eligible-asset helper so heuristic, LightGBM, LSTM, and bottom-candidate flows all exclude out-of-universe assets on historical dates
  - unified `HIGH_RS_SCORE` persistence so historical backfills generate the same indicator and signal pattern as the daily RS score task

- Added an end-to-end benchmark comparison workflow for backtests:
  - new `comparison_curve` payloads now combine the selected run, stored compare target, extra overlay runs, and official CSI 300 / CSI A500 benchmark series
  - serializer validation now enforces compatible `compare_backtest_run_id` targets before a rerun is created
  - added `rerun_backtests_for_comparison` and `run_reference_benchmark_suite` to clone comparison reruns and export rolling benchmark bundles under `reports/`
  - Backtest Workbench now renders comparison charts, keeps compare-target state derived from reused runs, and supports chart-only extra overlays without mutating the stored run config

- Hardened historical data governance around one shared project floor:
  - centralized `get_historical_data_floor()` with default `2010-01-01`
  - backfill, rebuild, validation, and macro helpers now clamp or reject pre-floor windows consistently
  - added `purge_pre_floor_historical_data` for dry-run or destructive cleanup of legacy rows before the floor date
  - extended `FundamentalFactorSnapshot` with free-float and market-cap fields so PIT benchmark weighting and reprocessing no longer depend on stale partial snapshots

- Refreshed tracked model artifacts and local reporting conventions:
  - updated checked-in LightGBM `3d/7d/30d` artifacts with richer metadata, explicit version fields, and the current `2016-06-01..2024-12-31` training window
  - refreshed LSTM `3d/7d/30d` metrics and summary outputs for the current `2024-12-31` artifact family
  - `reports/` remains the local output root for benchmark suites and exports, and is now treated as generated workspace output instead of committed source

**Current Notes**:

- When PIT benchmark coverage is complete, backtests now use the precomputed `CSI300_CSIA500_PIT_UNION` equity curve; otherwise they fall back to the equal-weight daily-return benchmark.
- The frontend comparison panel is chart-only for extra overlays: adding extra runs does not mutate the stored backtest configuration.
- The default historical floor is now `2010-01-01`; older rows can be audited and purged, but new backfill and retrain windows will not expand below that floor without code changes.

**Key Files**:
- `apps/markets/tasks.py`
- `apps/markets/benchmarking.py`
- `apps/markets/management/commands/onboard_csi_a500_universe.py`
- `apps/markets/management/commands/rollout_csi_a500_universe.py`
- `apps/backtest/tasks.py`
- `apps/backtest/comparison.py`
- `apps/backtest/management/commands/run_reference_benchmark_suite.py`
- `apps/backtest/management/commands/rerun_backtests_for_comparison.py`
- `apps/prediction/tasks_lightgbm.py`
- `apps/core/management/commands/purge_pre_floor_historical_data.py`
- `frontend/src/pages/BacktestWorkbenchPage.tsx`
- `frontend/src/components/charts/BacktestComparisonChart.tsx`

**Focused Coverage Added**:
- Added regressions across `apps.markets.tests`, `apps.backtest.tests`, `apps.prediction.tests`, `apps.analytics.tests`, `apps.factors.tests`, and `apps.core.tests` for index sync, PIT benchmark refresh, comparison payloads, PIT candidate filtering, RS-score backfill parity, fundamental market-cap backfills, and pre-floor purge behavior.


### version 0.1.10

**Objective**: package the current `97`-file release candidate into a commit-ready `v0.1.10` with deterministic LightGBM retrains, trade-decision policy experiments, operator-facing dashboard improvements, data-quality validation tooling, and refreshed validation/report artifacts.

**Implemented Features**:

- Strengthened the LightGBM retrain path for repeat experiments on the same data window:
  - tightened the default regularization profile to `num_leaves=15`, `min_data_in_leaf=50`, `feature_fraction=0.6`, `lambda_l1=1.0`, and `lambda_l2=1.0`
  - added `version_tag` support to rebuild/training flows so `2016-06-01..2024-12-31` can be retrained without overwriting earlier artifact families
  - persisted tagged retrain metadata and saved parallel `core80-v1` and `regstrong-v1` artifact families under `models/lightgbm/`
  - added snapshot-driven cumulative-importance pruning with the `latest_snapshot_cumulative_80_core20_25` rule, producing active `core80-v1` artifacts with `20` retained features per horizon

- Extended trade-decision experimentation through the full backtest stack:
  - added policy-aware TP/SL controls (`include_near_round_target`, `min_target_return_pct`, `min_stop_distance_pct`) to backtest parameters, candidate generation, and stored signal payloads
  - applied the same policy path across heuristic, LightGBM, and LSTM runtime candidates so policy experiments stay comparable across model families
  - tightened serializer validation and regression tests around propagated TP/SL payloads and policy bounds

- Expanded release validation and report artifacts for current experiments:
  - generated validation packs under `reports/backtests_113_116_lightgbm_core80_v1/`, `reports/backtests_113_128_tpsl_rerun_20260427/`, `reports/backtests_117_128_grid12_tpsl_rerun_20260427/`, and `reports/tpsl_policy_experiment_123_118_20260427/`
  - exported run summary/config, model references, active LightGBM artifact lineage, macro monthly rows, comparison CSVs, and trade-level detail where needed
  - aligned the checked-in `reports/backtests_89_112_v0_1_9/` folder with the current light-export default instead of keeping stale detail-export files there

- Added operational data-quality validation tooling:
  - new command `apps/core/management/commands/validate_data_quality.py` writes CSV/JSON audit reports under `reports/` without mutating model data
  - added `apps/core/tests.py` coverage for report generation, alert delivery, and fail-on-critical behavior
  - added `apps/factors/management/commands/audit_model_data_quality.py` for targeted factor/fundamental/capital-flow/default-bucket audits during model debugging

- Improved the operator-facing frontend workflow:
  - added `IndicatorBoardPage` with sortable/filterable all-stocks monitoring across factor, indicator, and heuristic/LightGBM trade-decision columns
  - extracted reusable dashboard/backtest filter helpers into `frontend/src/lib/dashboardCandidateFilters.ts`
  - refreshed dashboard/backtest routing, API glue, i18n labels, and test coverage so dashboard candidate views stay aligned with the current backtest configuration model

- Refreshed release documentation:
  - updated `TechnicalGuide.md` with the new Data Metrics Sheet structure, explicit TP/SL mechanism/formulas, current export semantics, and current model/input coverage
  - updated `README.md` system-status counts and release highlights for the current `v0.1.10` candidate

**Current Notes**:

- The active `core80-v1` LightGBM family prunes `northbound_flow` and `northbound_flow_x_mom_5d` from all horizons while preserving older artifact families for audit.
- `PredictionResult` is now effectively the generic stored prediction surface for heuristic-style and LSTM outputs; stored LightGBM history remains in `LightGBMPrediction`.
- `export_backtest_runs` now defaults to light export (`run_summary.csv`, `run_config_results.csv`, `model_references.csv`); detailed trade/macro/comparison CSVs are opt-in.

**Key Files**:
- `apps/prediction/tasks_lightgbm.py`
- `apps/prediction/management/commands/rebuild_lightgbm_pipeline.py`
- `apps/prediction/odds.py`
- `apps/backtest/tasks.py`
- `apps/backtest/serializers.py`
- `apps/backtest/tests.py`
- `apps/backtest/management/commands/export_backtest_runs.py`
- `apps/core/management/commands/validate_data_quality.py`
- `apps/core/tests.py`
- `apps/factors/management/commands/audit_model_data_quality.py`
- `frontend/src/pages/DashboardPage.tsx`
- `frontend/src/pages/IndicatorBoardPage.tsx`
- `frontend/src/lib/dashboardCandidateFilters.ts`
- `TechnicalGuide.md`
- `README.md`

**Test Coverage / Verification**:
- `docker exec -i finance_analysis_django python manage.py test apps.backtest.tests.Phase15BacktestTests.test_run_backtest_supports_lightgbm_prediction_source apps.backtest.tests.Phase15BacktestTests.test_lightgbm_top_n_stop_target_exit_uses_propagated_levels apps.backtest.tests.Phase15BacktestTests.test_open_positions_backfills_missing_prediction_trade_decision_fields`
- `docker exec -i finance_analysis_django python manage.py test apps.factors.tests`
- Live Django-shell coverage snapshots refreshed on `2026-04-27` for `README.md` and `TechnicalGuide.md`
- `git --no-pager diff --check -- TechnicalGuide.md`

### version 0.1.9: Full Backfill + Runtime Backtest Validation ✓
**Objective**: 收尾 backfill/backtest 阶段，让数据、模型、文档和导出报告与当前实现保持一致，准备 v0.1.9 提交

**Implemented Features**:

- Refreshed the production data surface after the latest backfill:
  - confirmed 300 active listed assets with complete `list_date` coverage
  - extended OHLCV to 1,145,611 rows through `2026-04-24`
  - populated fundamental, moneyflow, margin-detail, factor-score, macro, sentiment, and LightGBM prediction history
  - refreshed TechnicalGuide data ranges with usage and missing-data impact notes
- Cleaned stale per-stock northbound fields:
  - removed `northbound_net_5d`, `northbound_net_10d`, and `northbound_net_20d` from `CapitalFlowSnapshot`
  - removed `northbound_flow_score` from `FactorScore`, dashboard DTOs, fixtures, and factor/backfill code paths
  - added cleanup migration `apps/factors/migrations/0004_remove_northbound_fields.py`
  - kept LightGBM `northbound_flow=0.5` as a neutral artifact-compatibility placeholder until active artifacts are retrained without that feature name
- Hardened release migration state:
  - pinned existing `FeatureImportanceSnapshot` index names to avoid generated-name drift
  - applied the northbound cleanup migration successfully in the live Docker database
- Completed model/backtest validation reporting:
  - documented active LightGBM artifacts trained on `2016-06-01..2024-12-31` with 3d/7d/30d accuracies `0.569238`, `0.513034`, and `0.593609`
  - documented active LSTM registry metrics: aggregate `0.465278`, 3d `0.547333`, 7d `0.428000`, 30d `0.420500`
  - captured post-retrain validation backtests 107-112 and comparison deltas against 101-106
- Added detailed backtest CSV export tooling:
  - new command `export_backtest_runs` exports run summary, flattened config/results, trades, macro monthly rows, model references, and comparison deltas
  - generated `reports/backtests_89_112_v0_1_9/` with 6 CSV files for BacktestRun IDs 89-112
  - summary/config sheets explicitly mark missing IDs 95-100 instead of silently skipping them
- Updated release documentation:
  - removed stale admin error text and stale factor assumptions from TechnicalGuide
  - cleaned README scratch notes and refreshed release status/counts
  - added v0.1.9 changelog notes for commit readiness

**Current Notes**:
- Backtests now generate heuristic, LightGBM, and LSTM candidates at runtime; stored LightGBM prediction coverage remains useful for API/dashboard/history but is not required by the backtest runner.
- `FactorScore.sentiment_score` is still stored, but default composite weighting keeps sentiment at `0.0` unless weights are changed.
- `northbound_flow` in LightGBM features is deliberately neutral and should disappear after the next artifact family is trained on a schema that omits it.

**Key Files**:
- `TechnicalGuide.md`
- `README.md`
- `apps/factors/models.py`
- `apps/factors/tasks.py`
- `apps/factors/migrations/0004_remove_northbound_fields.py`
- `apps/backtest/management/commands/export_backtest_runs.py`
- `apps/prediction/tasks_lightgbm.py`
- `apps/prediction/models_lightgbm.py`
- `apps/prediction/management/commands/backfill_model_data.py`
- `frontend/src/lib/api.ts`
- `reports/backtests_89_112_v0_1_9/`

**Test Coverage / Verification**:
- `docker exec -i finance_analysis_django python manage.py makemigrations --check`: no changes detected after pinning existing prediction index names
- `docker exec -i finance_analysis_django python manage.py migrate --plan`: confirmed only `factors.0004_remove_northbound_fields`
- `docker exec -i finance_analysis_django python manage.py migrate`: applied `factors.0004_remove_northbound_fields` successfully
- Database introspection: confirmed removed northbound columns are absent from `factors_capitalflowsnapshot` and `factors_factorscore`
- `docker exec -i finance_analysis_django python manage.py export_backtest_runs --start-id 89 --end-id 112 --output-dir reports/backtests_89_112_v0_1_9`: generated 6 CSV files; summary/config 24 requested run rows, comparison 6 rows, trades 11,255 data rows
- `docker exec -i finance_analysis_django python manage.py test apps.factors.tests apps.prediction.tests apps.prediction.tests_lightgbm apps.backtest.tests apps.macro.tests apps.markets.tests`: ran 64 tests, OK
- `cd frontend && npm test`: 1 test file passed, 3 tests passed
- Broad app-label Django test discovery still hits a namespace-package loader issue, so release verification uses explicit test modules.

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
_Historical note: git history contains two distinct releases tagged as `0.1.7`; both entries are preserved below instead of being renumbered retroactively._
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

### version 0.1.2: Dynamic Series Completion ✓
  - Dashboard probability chart now uses live prediction series (top screener symbol fallback)
  - Stock Detail removed static fallback chart/probability data; now displays API-driven series only
  - Chart components now show explicit empty-state messages when API data is unavailable

### version 0.1.1: Real Backend Data Wiring ✓
  - Dashboard metrics now fetched from live endpoints (`macro`, `concept heat`, `signals`, `alert-events`)
  - Stock Detail page now fetches live asset lookup, OHLCV candles, prediction probabilities, and sentiment scores
  - Bottom Screener now renders live `/screener/bottom-candidates/` results
  - Macro Context page now renders live `/macro/contexts/` entries
  - Backtest Workbench now renders live `/backtest/` runs
  - Alert Center now combines live WebSocket stream + `/alert-events/` API history

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
