# ETF 轮动 — 后续计划：选股去重、仓位定标与页面透明度

起草日期 2026-10-23。分支 `feat/etf-rotation-followon`（尚未提交，tip 仍是 `277e2b4`）。
本文所有数字都取自当前生产库的实时查询，不是估算。

---

## 0. 先把事实摆出来：今天这条信号长什么样

`ETFRotationSignal` 全表只有 **2 行**。最新一行（id 4）：

```
date          = 2026-10-20          signal_type = MONTHLY
model_type    = RULE                model_version = ''      (空字符串)
max_positions = 5                   weighting   = 'equal'   (在 metadata['config'] 里)
defensive     = 511880.SH @ 0.0%    pool_size   = 488
qualified     = 130                 absolute_momentum_excluded_count = 357
created_at    = 2026-10-20T18:10:10Z
updated_at    = 2026-10-21T19:45:02Z
```

选出的 5 条腿：

| rank | code | name | weight | score | mom_60d |
|---|---|---|---|---|---|
| 1 | 515020.SH | 华夏中证银行ETF | 20% | 0.71270492 | +10.98% |
| 2 | 512700.SH | 南方中证银行ETF | 20% | 0.71024590 | +10.81% |
| 3 | 512820.SH | 汇添富中证银行ETF | 20% | 0.71024590 | +10.77% |
| 4 | 159887.SZ | 富国中证800银行ETF | 20% | 0.70901639 | +11.18% |
| 5 | 515300.SH | 嘉实沪深300红利低波动ETF | 20% | 0.69959016 | +4.76% |

**四条银行 ETF，各 20%，合计 80% 的仓位押在同一个行业因子上。** rank 2 和 rank 3 的
score 到小数点后 8 位完全相同——因为它们跟踪的是同一个指数，特征几乎一样，剩下的差别
由 `ts_code` 字符串排序决定（`rotation_engine.py:633-643`，降序 score、升序 ts_code）。
这不是 bug，是"没有去重约束"的直接后果。

---

## 1. 七个问题的直接回答

### Q1 — `signal date` 是模型的生成日期吗？

**不是。它是"数据截止日"（as-of date），不是生成时间。**

`resolve_trade_date(None)`（`signals.py:81-107`）取的是 `ETFFeatureSnapshot` 中
`is_eligible=True` 且横截面非空的**最新交易日**，然后写进 `ETFRotationSignal.date`
（`signals.py:252`）。生成时间是另一列 `created_at` / `updated_at`，
serializer 已经把它们都吐出来了（`serializers.py:110-118`），但
`EtfRotationPage.tsx:47-50` 只渲染了 `signal.date`，标签写死 `etf.dash.signalDate`，
**`created_at` 一次都没显示**。

所以现在页面上那个 "2026-10-20" 的含义是："这份仓位建议是基于 10-20 收盘的横截面算出来的"。

**但这里藏着一个真问题，比标签歧义严重：**

- `ETFFeatureSnapshot` 里 `is_eligible=True` 的最新日期是 **2026-10-22**，`ETFDaily`
  最新 bar 也是 **2026-10-22**。数据是齐的。
- 信号行的 `updated_at` 停在 **2026-10-21 19:45**，`date` 停在 **2026-10-20**。
- beat 里 `generate-etf-rotation-signal-daily` 是 `45 19 * * * UTC`，也就是说
  10-22 19:45 那一趟**没有写出任何行**（否则会新建一行 `date=2026-10-21` 或 `10-22`，
  因为唯一键是 `(date, signal_type)`）。
- `tmp/beat.log` 的 mtime 是 **10-21 22:30**，而当前 beat 进程已经跑了 17 小时——
  说明现在这个 beat 的日志**没有写到 `tmp/beat.log`**，我们无法确认那趟任务是被派发后
  失败了，还是根本没派发。

结论：页面上显示的日期落后了两个交易日，而且**页面上没有任何东西告诉你它落后了**。
Phase 1 要同时修"标签歧义"和"陈旧可见性"，并且要查清 10-22 那趟任务为什么没落地。

### Q2 — RULE 是什么模型？为什么 `/etf/backtests` 只接受 RULE？

**RULE 不是模型，是一个没有任何拟合过程的确定性打分公式。**

```
score = 0.6 × pct_rank(mom_60d)
      + 0.2 × pct_rank(rs_vs_benchmark_60d)
      − 0.2 × pct_rank(vol_20d)
```

三个权重是硬编码常量（`rotation_engine.py:39-44`），公式在 `_rule_scores`
（`rotation_engine.py:318-367`）。百分位是在**剔除了缺特征的候选之后**才算的，缺值不
填充为 0（注释解释了原因：0 会被读成"最差的那只"）。之后依次是：绝对动量过滤
（`mom ≤ 0` 直接踢掉，今天踢了 357/488）、换手约束（`max_swaps=2`、
`rank_change_threshold=2`）、按广度决定防御仓（`_defensive_allocation`，
`rotation_engine.py:833-865`）、最后按 `equal` 或 `score` 分配权重。

它唯一的"参数"就是 `RotationConfig` 那 9 个旋钮 + 3 个混合权重，没有一个是学出来的。
它之所以在生产上，是因为 Phase D 的门槛把 LSTM 打回去了（alpha 0.0888 vs 0.1821）。

**为什么 API 拒绝 ML：`serializers.py:204-215`，理由是"不可复现"。**

`_resolve_predictor`（`rotation_engine.py:394-425`）在没有显式传入 predictor 时调用
`load_active_predictor()`——它加载的是**worker 执行那一刻** `is_active=True` 的那个
`ModelVersion`。也就是说：

1. 一条 ML 回测行的结果不取决于它自己的参数，而取决于一个可被任何人随时改写的全局状态。
   同一条行今天跑和下周跑会得到不同的净值曲线，而行的 `parameters` 完全一样。
2. 这正是 `INSAMPLE-` 那批污染行（153-161）的成因家族——"哪来的模型"没有被记录在行上。
3. 附带事实：Phase D 结束后**没有任何 active 的 ETF ModelVersion**，所以今天放开 ML，
   请求会在 `found no active LSTM model` 上直接失败，行会变成 FAILED。

所以这个 400 不是"没做完"，是"这个写入口只接受它能复现的配置"。
Phase 5 给出放开它的正确方式：请求必须**钉住具体的 `model_version`**，引擎按版本加载
而不是按 active 加载。

### Q3 — 前三都是中证银行 ETF，是不是应该只留份额最高的那只？

**是，而且这是本计划里最值得做的一件事。但"份额最高"今天做不到——份额数据根本不存在。**

已确认的事实：

- **`ETFUniverse` 没有份额列，也没有 AUM 列。** 全仓库没有任何 `pro.fund_share()` 调用，
  也没有 `pro.fund_nav()`。ingest 只调三个 TuShare 端点：`fund_basic`（`tasks.py:155`）、
  `fund_daily`（`tasks.py:345`）、`fund_adj`（`tasks.py:351`）。
- **`category` 太粗，做不了去重。** 19 只可排名的银行 ETF 全部落在 `sector_finance`，
  而这个桶把银行/证券/保险/券商混在一起（关键词表 `etf_universe.py:78-80`）。
- **`category` 本身还有错。** `MONEY_KEYWORDS = ('货币','现金','理财金','快线')`
  （`etf_universe.py:43`）里的"现金"把**自由现金流 ETF**（股票型指数基金）也匹配成了
  货币型：63 行 `category='money'` 里 **33 行是"XX中证全指自由现金流ETF"**。
  任何依赖 `category` 的新规则上线前必须先修这个。
- **`benchmark` 是唯一能用的聚类键，但引擎根本读不到它。** 实测：

  | benchmark 原文 | 只数 |
  |---|---|
  | `中证银行指数收益率×100%` | 12 |
  | `中证银行指数×100%` | 2 |
  | `中证800银行指数收益率×100%` | 1 |
  | `沪深300非银行金融指数×100%` | 1 |
  | `中证港股通非银行金融主题指数收益率×100%` | 1 |
  | `中证银行AH价格优选指数(人民币)×100%` | 1 |
  | `中证银行AH价格优选指数收益率×100%` | 1 |

  去掉 `指数` / `收益率` / `×100%` / `(人民币)` 这些后缀后，前两组合并成
  **"中证银行" = 14 只**。`benchmark` 存在 `ETFUniverse`（`models.py:53-56`）但
  **不在 ranker 读的那张快照表里**——`backtest_engine.py:387-394` 只带
  `ts_code / name / category / is_eligible` + 特征列。
- **有一个现成的替代指标：`avg_amount_20d`（20 日日均成交额，元）。** 今天这 5 条腿：

  | code | 日均成交额 |
  |---|---|
  | 515020.SH 华夏中证银行 | 1.63 亿 |
  | 512820.SH 汇添富中证银行 | 1.25 亿 |
  | 512700.SH 南方中证银行 | 1.06 亿 |
  | 515300.SH 嘉实红利低波 | 0.98 亿 |
  | 159887.SZ 富国中证800银行 | 0.90 亿 |

  对同指数的一簇 ETF，成交额和规模高度相关，而且它本来就是流动性下限用的那个量。
  但它是**代理**，不是份额。

**必须说清楚的一个局限：光做"同指数只留一只"，今天这个组合会从 4 只银行变成 2 只，
不是 1 只。** 因为 159887 跟踪的是 `中证800银行`，和 `中证银行` 是两个不同的指数，
归一化不会把它们并到一起——尽管它们相关性极高。要真的压到 1 只，需要的是
**行业/主题层面的上限**（或者按相关性聚类），而不是指数层面的去重。
Phase 2 因此是两层约束：指数簇内择优 + 每个 category 最多 N 只。

**还有一件事：改了选股规则就是换了一个策略。** 现在生产上那个 "+18.21% alpha"
是 144 组 RULE 网格在 2024-01-01..2026-10-20 上选出来的。加上去重约束后，
那个数字对新策略**不再成立**，必须在调优窗口（2016..2023-12-31）上重新跑网格、
重新选参，holdout 继续保持只花一次的纪律。

### Q4 — 买入比例没有按赔率/胜率调整过，为什么？

**因为现在的仓位分配是纯序数的，只有两种模式，都不含任何风险或优势信息。**

`allocate_weights`（`rotation_engine.py:868-905`）：

- `equal` → 每条腿 `1/N`。**今天生产信号用的就是这个。**
- `score` → 按 rank 线性衰减，再和等权 70/30 混合（`SCORE_WEIGHT_SCORE_SHARE = 0.7`），
  下限 `SCORE_WEIGHT_FLOOR = 0.01`。

代码注释解释了为什么不用原始 score 加权：RULE 的合成分在减掉波动惩罚后**可能是负数**，
"按负数比例分配权重"没有意义。这个理由成立——**但它只否定了"按 score 加权"，
没有否定"按波动率加权"或"按优势调整总仓位"。**

两个位置本该有赔率信息，现在都没有：

1. **腿之间的分配**：完全不考虑各腿波动率。今天 5 条腿的 `vol_20d` 分别是
   0.138 / 0.1375 / 0.1349 / 0.1491 / 0.1013——最高和最低差 47%，却各拿 20%。
2. **总风险暴露**：`_defensive_allocation`（`rotation_engine.py:833-865`）由**广度**
   决定（合格只数 / `min_positions_for_risk_on=3`），不由预期优势决定。
   今天 `defensive_reason = 'risk_on'`，风险预算 1.0，因为合格的有 130 只。
   换句话说：只要市场里有足够多的东西在涨，就满仓，不管这个策略最近赢得多不多。

**数据其实是够的**，不需要新建表：

- 每笔平仓在 `trade_log` 里带 `realized_pnl` 和 `return_pct`
  （`backtest_engine.py:1116-1124`）→ 胜率 p 和盈亏比 b（平均盈利/平均亏损）都能算。
- `finance_metrics.win_rate()` 已经存在，`win_rate` 已经是 run 上的一列
  （`models.py:385`）并进 report（`backtest_engine.py:1568`）。
  **盈亏比还没有**，需要加一个指标函数。
- `vol_20d` / `vol_60d` 已经在 ranker 读的那张快照表里。

**Phase 3 拆成两件独立的事，因为它们的证据强度差很远：**

- **3a 逆波动率加权**（低风险、高确定性）：新增 `weighting='invvol'`，
  `w_i ∝ 1/vol_i`，并对等权做 0.5×–1.5× 的夹逼以免单腿过度集中。用的全是现成数据，
  不需要估计任何统计量。
- **3b 分数 Kelly 调总暴露**（中风险，必须严格 walk-forward）：用**同一配置**在
  调优窗口上的历史 `trade_log` 估 p 和 b，`f* = (p·b − (1−p)) / b`，
  实际暴露 = `clamp(κ · f*, floor, 1.0)`，κ 取 0.25 左右。
  **只能在组合层面估，不能按腿估**：整个 holdout 也就 150 笔左右的交易，
  按单只 ETF 拆完全是噪声。而且必须只用**严格早于当前调仓日**的成交记录，
  否则就是未来函数。

**对 3b 的诚实保留：** Kelly 假设赌注可重复、相互独立、且 p/b 已知。
一个月度调仓、5 条腿高度相关的动量组合三条都违反（Q3 说的相关性正是违反点）。
所以 3b 应该被定位成**暴露的限速器**，不是优化器；上限要压得很低，
并且要在调优窗口上验证，不能拿 holdout 试。如果 3b 的收益不显著，
就只留 3a，别为了"用了 Kelly"而用。

### Q5 — 页面需要看到当前选择的模型和具体参数

**好消息：数据已经在 API 响应里了，这几乎是纯前端工作。**

`ETFRotationSignalSerializer` 已经返回 `metadata`（`serializers.py:110-118`），
而实时行的 `metadata` 里已经有：

```json
config: {max_positions: 5, momentum_lookback: 60, weighting: "equal",
         absolute_momentum_filter: true, all_negative_to_defensive: true,
         defensive_asset: "511880.SH", rank_change_threshold: 2, max_swaps: 2,
         min_positions_for_risk_on: 3}
scoring_metadata: {model_type: "RULE",
         rule_weights: {momentum: 0.6, relative_strength: 0.2, volatility_penalty: -0.2},
         momentum_column: "mom_60d", relative_strength_column: "rs_vs_benchmark_60d",
         available_momentum_lookbacks: [5,10,20,60,120,250],
         momentum_column_substituted: false, excluded_missing_features: 0}
weight_method: "equal"      qualified_count: 130     target_count: 5
selected_count: 5           risk_budget: 1.0         pool_size: 488
defensive_reason: "risk_on" absolute_momentum_excluded_count: 357
benchmark_code: "H00905.CSI"  turnover: {...}  previous_book: [...]
```

页面现在只渲染 5 个 metric card（date / model_type / max_positions / defensive /
benchmark20d，`EtfRotationPage.tsx:46-68`），上面这些**一个都没显示**。

要补的三个缺口：

1. **参数面板**：把 `metadata.config` + `scoring_metadata.rule_weights` 渲染成一张
   "当前生效配置"表。零后端改动。
2. **`weighting` 不一致**：实时信号用 `equal`（引擎 dataclass 默认值，
   `rotation_engine.py:105`），而工作台新建行的**列默认值是 `score`**
   （`models.py:365`）。`views.py:172-178` 的 docstring 明确记录了这个差异。
   结果是：**页面上看到的信号，和工作台默认跑出来的回测，用的是两套权重规则。**
   这个必须统一，否则用户在页面上对照不上自己的回测。
3. **RULE 没有版本号**：`model_version` 是空字符串，`model_versions` 是 `{}`。
   所以"当前模型"这一栏永远显示不出东西。需要一个稳定的 RULE 版本标签
   （例如 `rule-v1:mom60-equal`，由 config 派生），写进信号行。

### Q6 — 之前说 Phase B 要 5-6 天，为什么一个晚上就做完了？

**估时是我给的，错了大约一个数量级。原因是估算单位选错了，不是因为活儿做得糙。**

三条具体原因：

1. **Phase B 大约八成是"照抄一个已经跑通的实现"。** 计划自己写的是
   "copy the stock semantics precisely"——`apps/backtest/views.py:111-205`、
   `tasks.py:2198-2312`、`models.py:40-51`、`task_health.py:43` 都是现成的。
   照抄一个已知正确的模式是工程里最便宜的一类活；我却按"从零设计生命周期语义"
   给它估了时间。
2. **Phase B 没有 GPU、没有长任务、没有外部数据依赖。** 它的关键路径是
   "改代码 → 跑测试"，一次迭代几秒。相比之下 Phase C 的 32 组扫描光 GPU 就
   48.6 分钟，Phase D 的重训 95 秒——那些阶段的墙钟时间主要由等待构成。
3. **"人日"这个单位隐含了一堆 agent 会话没有的开销**：上下文重载、等 review、开会、
   在两个任务之间切换。用 person-day 估 agent 批量作业，本身就是错的量纲。

**但必须把话说回来：快 ≠ 已验证。** Phase B 敢说做完了，靠的不是速度，是
49 个后端测试 + 一次真实 HTTP 生命周期跑通（14 项 + 7 项检查）。而且**快速照抄有它
自己的残余风险**：并发上限和 429 那条路径是被测试覆盖的，不是在真实资源争抢下验证的；
pause 是在一次真实运行上验证的，但只在一个窗口上。

给后续估算的校准结论：**照抄类工作按小时估，新语义/GPU 类工作按天估。**
本计划下面的估时就按这个来。

### Q7 — 防御资产能不能默认改成"华泰零钱宝+"？

**不能，而且这个想法背后真正要解决的问题是另一个 bug。**

- **"华泰零钱宝+"不是场内 ETF。** 全库 `name` 含"零钱"的行数是 **0**。
  它是券商现金管理类产品（场外），没有 `ETFDaily` 行情，引擎无法表示它。
- **华泰系场内货币 ETF 有两只，但都过不了流动性下限。** 实测最近 5 个交易日的
  `amount`：

  | code | 名称 | 日成交额 | is_eligible |
  |---|---|---|---|
  | 511880.SH | 银华货币ETF-A（**当前默认**） | **275.83 亿** | True |
  | 511990.SH | 华宝现金添益货币ETF-A | **185.66 亿** | True |
  | 511670.SH | 华泰紫金天天金货币ETF-A | **约 36 万元** | False |
  | 511830.SH | 华泰柏瑞货币ETF-A | **约 14 万元** | False |

- **换上去会发生什么：防御仓被静默清零，变成 0% 收益的闲置现金。**
  `_apply_liquidity_floor`（`backtest_engine.py:884-899`）对防御资产单独做一次检查，
  要求 `日均成交额 ≥ 防御权重 × 净值 × liquidity_floor_multiple(10)`；不满足就
  `kept.pop(defensive)`——**整个防御仓被丢掉**，只留一条 `REJECTED_LIQUIDITY` 日志。
  按 511670 的 36 万元算，20% 的防御仓要过关，账户净值不能超过 **18 万元**。
- **顺带纠正一个我一开始怀疑、验证后发现不成立的问题：** 货币 ETF 的原始价格在
  ¥100 附近几乎不动（511880 从 2016-01-04 到 2026-10-22 原始收盘只涨了 **+0.82%**），
  而 adj_close 涨了 **+25.90%**（adj_factor 1.1113 → 1.3878，年化约 2.15%）。
  引擎**确实**把这部分收益算进去了：`_accrue_dividends`
  （`backtest_engine.py:1305-1337`）把 adj_factor 的变化按每股现金分红计入 cash，
  report 里的 `price_basis` 字符串也写明了这一点（`:1609`）。
  所以"防御仓白拿 0 收益"这个担心对 511880 **不成立**——它的货币收益是被正确计入的。

**真正该修的是这两条：**

1. **`defensive_asset` 完全没有校验。** `BacktestConfig.validate()`
   （`backtest_engine.py:237-259`）检查了日期、model_type、频率、资金、费率、
   持仓数、动量窗口、chunk 大小——**唯独不检查防御资产是否真的存在**。
   没有外键、没有 choices、没有 `clean()`，只有一个 `max_length=20`。
   工作台那个字段还是自由文本（`EtfBacktestWorkbenchPage.tsx:464-466`，
   placeholder 就是 `511880.SH`）。**打错一个字，得到的不是报错，是一条安静地
   赚 0% 的曲线。**
2. **防御仓被拒后应该按一个明示的现金利率计息，而不是 0%。**
   即使保持 511880，这条也值得做：它把"模型决定避险"和"数据不够所以买不进去"
   这两种完全不同的情况在净值曲线上区分开了。

**建议：默认值保持 `511880.SH` 不变**（它日均 275 亿，是全库流动性最好的资产之一，
且货币收益已被正确计入），把功夫花在校验 + 计息 + 把自由文本换成下拉上。
如果你的诉求是"实盘时现金要落在华泰账户的零钱宝里"——那是**实盘执行**层面的事，
回测表示不了它；回测里最接近的可表示代理就是 511880 / 511990。

---

## 2. 分期计划

估时按 Q6 的校准：照抄/纯前端按小时，新语义/需要重训重跑的按天。

### Phase 1 — 页面透明度 + 信号陈旧可见性（约 4-6 小时，几乎纯前端）

对应 Q1、Q5。价值最高、风险最低，先做。

- **1.1** `EtfRotationPage.tsx` 的信号卡改成三个时间概念分开显示：
  **数据截止日**（`date`）、**生成时间**（`created_at`）、**最后更新**（`updated_at`）。
  i18n 两套字典都加 key（`etf.dash.asOfDate` / `generatedAt` / `updatedAt`）。
- **1.2 陈旧告警**：把 `date` 和 `ETFFeatureSnapshot` 的最新合格日期比对
  （新增一个只读端点，或者挂到现有 dashboard 聚合上），落后 ≥ 1 个交易日就在卡片上
  显示一条醒目提示。今天这种情况（落后 2 个交易日而页面毫无表示）必须不可能再发生。
- **1.3 参数面板**：渲染 `metadata.config` 全部 9 个旋钮 +
  `scoring_metadata.rule_weights` 三个混合权重 + `momentum_column` /
  `relative_strength_column` / `weight_method` / `qualified_count` /
  `absolute_momentum_excluded_count` / `defensive_reason`。全部来自已有响应。
- **1.4 统一 `weighting` 默认值**：让引擎 dataclass、模型列、settings、
  工作台表单四处一致。先决定用哪个（建议 `score`，因为它是列默认值、也是网格跑出来的
  赢家配置的一部分），再改另外三处。**改完必须重跑一次实时信号**，因为
  `equal` → `score` 会改变每条腿的权重。
- **1.5 RULE 版本标签**：给 RULE 一个由 config 派生的稳定 `model_version`
  （`rule-v1:mom{lookback}-{weighting}`），写进 `resolve_model_version`
  的返回值，页面就能显示"当前模型"。
- **1.6 查清 10-22 19:45 那趟信号任务为什么没写行**：先确认当前 beat 的日志到底写到哪
  （`tmp/beat.log` 已经 10-21 22:30 就没动过，而 beat 进程跑了 17 小时——这本身就是
  一个可观测性缺口，顺手修掉）。

**Done 标准**：操作员在 `/etf` 上能一眼看到"这是基于哪一天的数据、什么时候算的、
用的是哪个模型的哪套参数、以及它是否已经过期"。

### Phase 2 — 同标的去重 + 份额数据（约 1.5-2 天，含重跑网格）

对应 Q3。这是改变策略行为的一期，必须配回测验证。

- **2.1 先修 `category` 误分类**（前置，不修后面都不可信）：
  `MONEY_KEYWORDS` 里的 `'现金'` 会命中"自由现金流"。改成要求 `invest_type='货币型'`
  或者把关键词收紧成 `'货币'`/`'现金宝'`/`'理财金'`/`'快线'` 并显式排除 `'现金流'`。
  受影响的 33 行要在 `sync_etf_universe` 重跑后重新分类（`tasks.py:206-210` 的
  `update_fields` 已经包含 category，重跑即覆盖）。加一个测试断言
  `招商中证800自由现金流ETF` **不是** money。
- **2.2 新增 `track_index_key` 归一化列**：从 `benchmark` 派生，规则是去掉
  `指数` / `收益率` / `×100%` / 空白 / `(人民币)` 等份额类后缀，`benchmark` 为空时
  退化到 `name` 去掉公司前缀。写成一个纯函数 + 单元测试覆盖上面那张实测表的全部 7 种形态。
  在 `sync_etf_universe` 里填充，并加进 `update_fields`。
- **2.3 摄入份额**：新增 `pro.fund_share()` 同步任务和 `ETFUniverse.shares_outstanding`
  列，顺带算 `aum = close × shares` 存成派生列或 metadata。
  **这是新的 TuShare 端点，要先确认当前 token 有没有权限、频率限制是多少**——
  没有权限就退化成用 `avg_amount_20d` 做簇内择优，计划里两条路都要留着。
- **2.4 选择层两层约束**（`build_rotation_plan`，`rotation_engine.py:906-1050`）：
  - **指数簇内择优**：同一个 `track_index_key` 只保留一只，择优顺序
    `份额 → 日均成交额 → score`。
  - **category 上限**：每个 `category` 最多 `max_per_category` 只（新增 config 旋钮，
    默认建议 2）。这一层才是把"4 只银行"压到"≤2 只银行"的东西——
    指数去重只能压掉 `中证银行` 那一簇，压不掉 `中证800银行`。
  - 两个旋钮都进 `RotationConfig`，都要能被 `BacktestConfig` 透传，
    都要出现在 `metadata['config']` 里（这样 Phase 1 的参数面板自动就能显示）。
- **2.5 tie-break 改掉**：`_ordered_by_score`（`rotation_engine.py:633-643`）现在的
  次级排序键是 `ts_code` 字符串，纯属任意。有了份额就改成 `score 降序 → 份额降序 →
  ts_code`，让今天这种同分情况有个有经济含义的裁决。
- **2.6 把 `track_index_key` 送进 ranker 的快照表**：
  `backtest_engine.py:387-394` 目前只带 4 个非特征列，2.4 需要看到 index key。
  注意**不能**把它混进特征列白名单（`FEATURE_PREFIXES`），它是选择约束不是因子。
- **2.7 重新验证**：在调优窗口 2016-01-29..2023-12-31 上重跑 RULE 网格
  （`run_etf_rotation_backtest --grid`，144 组），加上新旋钮的少量组合。
  **holdout 不碰。** 产出一个新的生产参考行，替换掉现在这个
  "+18.21% 但 80% 押银行" 的 row 82。

**Done 标准**：新的一条实时信号里，同一个跟踪指数最多出现一只；`sector_finance`
最多 `max_per_category` 只；新的调优窗口参考行有完整指标，且换手率没有因为约束而爆掉
（约束会改变持仓，`max_swaps=2` 的换手逻辑要重新看一眼是否还合理）。

### Phase 3 — 仓位按赔率/胜率定标（约 1-1.5 天）

对应 Q4。**3a 和 3b 可以独立上线，3a 先做。**

- **3a 逆波动率加权**
  - `WEIGHTING_MODES` 增加 `'invvol'`（`rotation_engine.py:34`），
    `allocate_weights` 增加分支：`w_i ∝ 1/max(vol_i, ε)`，再对等权做
    `[0.5×, 1.5×]` 夹逼，最后走现有的残差归一逻辑（`rotation_engine.py:897-904`）。
  - `weighting` 的 choices 要在**四处**同步加：`models.py:365` 的 `Weighting`、
    serializer 的可写字段、引擎、工作台表单选项。加一次 migration。
  - 波动率列缺失时**退化到 equal 并在 `weight_method` 里写明**，不要静默用 0 填充
    （这和 `_rule_scores` 的缺值策略保持一致）。
  - 验证：在调优窗口上跑 `equal` / `score` / `invvol` 三组同参数对比，
    看 maxDD、Sharpe、换手率。**预期是 maxDD 下降、alpha 变化不大**；
    如果 alpha 明显下降，就把它作为可选项而不是默认值。
- **3b 分数 Kelly 调总暴露**
  - 先加一个 `payoff_ratio()` 到 `apps/core/finance_metrics.py`
    （`win_rate()` 已经在那儿），从 `trade_log` 的 `return_pct` 序列算
    平均盈利/平均亏损。
  - `BacktestConfig` 增加 `kelly_fraction`（默认 0 = 关闭）、`kelly_cap`、`kelly_floor`、
    `kelly_lookback_trades`。默认关闭很重要：**不改变任何现有回测的结果**，
    已有的 202 行 ETF 回测不会因为这个改动而失效。
  - 在 `build_rotation_plan` 之前算 `f*`，用它缩放 `risk_budget`，
    剩下的自动落到防御仓——这个管道已经存在（`_defensive_allocation` 返回
    `risk_budget`，`allocate_weights` 拿它当预算），所以 3b 只是给 `risk_budget`
    换了个来源。
  - **严格 walk-forward**：只用调仓日之前已平仓的成交。实现上要么把历史成交按日期
    切好传进引擎，要么在引擎里维护一个"截至当前 session 的已实现收益列表"。
    后者更不容易写出未来函数，倾向前者但要加一个显式测试断言
    "第 k 次调仓用的成交记录里没有第 k 次之后的"。
  - **只在组合层面估 p/b，不按腿估**，理由见 Q4。
  - 验证：同样在调优窗口上跑 `kelly_fraction ∈ {0, 0.15, 0.25, 0.5}`，
    看 maxDD 和 alpha 的权衡曲线。**如果 0.25 和 0 的差别在噪声范围内，
    就不要上线 3b**——把它留在代码里默认关闭，并在 CHANGELOG 里写明"试过了，
    没有可测量的收益"，这也是一种交付。

### Phase 4 — 防御资产的校验与计息（约 4-6 小时）

对应 Q7。**不改默认值。**

- **4.1 校验**：`BacktestConfig.validate()` 增加一条——`defensive_asset` 必须是
  `ETFUniverse` 里存在且有行情覆盖回测窗口的行；不满足就抛
  `RotationEngineError`。同时在 `create` 路径上提前拦（serializer `validate`），
  返回 **400 并说明原因**，不要让行进了队列再 FAILED。
- **4.2 被拒的防御仓计息**：`_apply_liquidity_floor` 现在把不合格的防御仓
  `pop` 掉变成 0% 现金（`backtest_engine.py:884-899`）。改成：
  新增 `cash_yield_annual` 配置（默认 0.0，保持向后兼容），
  被 pop 的权重按日计息进 cash，并在 report 里记一条
  `defensive_sleeve_uninvested` 计数 + 累计计息金额。
  这样"模型选择避险"和"想买但买不进去"在曲线上可区分。
- **4.3 前端换成下拉**：工作台的 `defensive_asset` 自由文本改成下拉，
  候选 = `category='money'` 且 `is_rankable` 且 `avg_amount_20d ≥ ETF_MIN_AVG_AMOUNT_YUAN`
  的行，显示名称 + 日均成交额，**这样操作员能自己看出为什么 511670 不在列表里**。
  保留一个"自定义代码"入口给高级用户，但走 4.1 的校验。
- **4.4 顺手把四处硬编码默认值收敛成一个**：`rotation_engine.py:37`、
  `config/settings/base.py:288`、`models.py:359`、`backtest_engine.py:194`
  各自写了一遍 `'511880.SH'`。留 settings 一处，其余引用它。

### Phase 5 — （可选）让 API 接受 ML 回测（约 4-6 小时）

对应 Q2 的后续。**只有在确实需要从页面跑 ML 时才做。**

- 请求必须带 `model_version`（一个具体的 `ModelVersion.version` 字符串），
  serializer 校验它存在且 `model_type` 匹配。
- `_resolve_predictor` 增加"按版本加载"的分支，绕开 `load_active_predictor()`
  （`rotation_engine.py:394-425`），并把版本号写进 run 的 `parameters` 和 report。
  **这样行自身就完整决定了结果**，可复现性问题解决。
- 并发上限沿用现有的 `ETF_MAX_CONCURRENT_BACKTESTS=2`；ML 行还要额外受
  Phase 0 那个 train worker 并发约束的语义保护（不能因为一个 API 请求把 VRAM 打爆）。
- 保持 `activate=False` 纪律：从 API 跑 ML 回测**绝不**改变任何 `ModelVersion` 的
  `is_active`。

---

## 3. 建议的执行顺序

```
Phase 1  (4-6h)   页面透明度 + 陈旧告警 + 查 10-22 信号任务   ← 先做，零策略风险
Phase 4  (4-6h)   防御资产校验 + 计息                          ← 独立，纯防御性修复
Phase 2  (1.5-2d) category 修复 → track_index_key → 份额 → 两层约束 → 重跑网格
Phase 3a (0.5d)   逆波动率加权                                  ← 依赖 Phase 2 的新参考行
Phase 3b (0.5-1d) 分数 Kelly（可能结论是"不上线"）
Phase 5  (4-6h)   可选，按需
```

Phase 1 和 Phase 4 可以并行，都不碰选股逻辑。
Phase 2 必须先于 Phase 3，因为 3 的验证要有 2 产出的新参考行做对照。

---

## 4. 风险与诚实的保留意见

1. **Phase 2 会让现有的生产参考行作废。** row 82 的 +18.21% alpha 是"80% 押银行"
   这个组合的 alpha。加了去重和行业上限之后那是另一个策略，数字必须重估，
   而且**很可能变差**——集中押一个正在涨的行业本来就是收益的来源之一。
   要提前接受"更分散、alpha 更低、但风险更真实"这个结果，否则会在看到数字时
   忍不住把约束调松到形同不存在。
2. **holdout 已经部分烧掉了。** RULE 网格本身就是在 2024-01-01..2026-10-20 上选的，
   Phase D 又花了一次。Phase 2 的新参考行只能在 2016..2023-12-31 上选，
   2024 之后继续保持"只花一次"的纪律，别为了验证去重效果反复跑 holdout。
3. **份额数据可能拿不到。** `pro.fund_share()` 是新端点，当前 token 的权限未知。
   Phase 2.3 必须先做一次可行性探测再决定走份额还是走成交额代理，
   不要把后面的工作建在一个没验证的数据源上。
4. **`category` 修完会有一批行改分类**，33 行 money → equity。这会影响
   任何按 category 过滤的现有查询和前端筛选，要全局搜一遍。
5. **逆波动率加权在低波动资产上会放大权重。** 今天 515300（红利低波，vol 0.1013）
   在 invvol 下会拿到明显超过 20% 的权重。夹逼区间 `[0.5×, 1.5×]` 是必要的，
   不是可选的。
6. **Kelly 的前提在这个策略上不成立**（相关赌注、p/b 未知、样本 ~150 笔）。
   3b 的正确定位是暴露限速器。如果验证下来没有可测量的好处，
   **不上线也是一个合格的结论**，写进 CHANGELOG 就行。
7. **统一 `weighting` 默认值会改变实时信号的权重**（Phase 1.4）。
   这是一个面向用户的变更，要在 CHANGELOG 和页面上都说明，
   并且改完必须重跑一次信号任务确认新权重落地。
8. **CI 测试下限只能升不能降**：后端 `MIN_TEST_COUNT=549`、前端 `66`
   （`.github/workflows/ci.yml:119, :257`）。每期落地后把下限往上抬。
9. **前端静态基线门禁**：`frontend/scripts/check-static-baseline.mjs` 必须带 gate 参数
   （`<build|lint>`），每次前端提交前跑。
10. **没有浏览器可用。** 上一轮的 UI 验证只做到 vitest + `tsc --noEmit` +
    静态基线 + API 契约检查。Phase 1 全是前端，这个缺口会最明显——
    要么接受"测试通过但没人点过"，要么在计划里显式安排一次人工过页面。

---

## 5. 验证

每期都要跑的基线（和上一轮一样）：

```
DJANGO_READ_DOT_ENV_FILE=True .venv/bin/python manage.py check
DJANGO_READ_DOT_ENV_FILE=True .venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_READ_DOT_ENV_FILE=True .venv/bin/python manage.py test apps.etf_rotation --noinput
DJANGO_READ_DOT_ENV_FILE=True .venv/bin/python manage.py export_documentation_facts --only commands,celery,env
DJANGO_READ_DOT_ENV_FILE=True .venv/bin/python manage.py export_documentation_facts --check
cd frontend && npm test && npx tsc --noEmit && node scripts/check-static-baseline.mjs lint && node scripts/check-static-baseline.mjs build
```

分期额外验证：

- **Phase 1**：`/etf` 上能看到 as-of date、generated at、完整 config 面板；
  人为把 `ETFFeatureSnapshot` 推后一天，页面出现陈旧告警；
  `curl /api/v1/etf/signals/latest/` 的 `metadata.config` 与页面渲染一致；
  查明 10-22 信号任务的下落，并把 beat 的日志路径修到可查。
- **Phase 2**：`classify_etf('招商中证800自由现金流ETF', '被动指数型', ...)` 不返回 money；
  归一化函数对上表 7 种 benchmark 形态的单元测试；
  新生成的实时信号里同一 `track_index_key` 只出现一次、`sector_finance` ≤ `max_per_category`；
  调优窗口网格重跑完成，新参考行指标齐全，且**没有任何 `ModelVersion` 被激活**。
- **Phase 3a**：三种 weighting 在调优窗口上的对比表（maxDD / Sharpe / alpha / 换手）；
  权重和仍精确等于 `1 − defensive_allocation`（现有的残差归一逻辑不能破）。
- **Phase 3b**：一个显式测试断言"第 k 次调仓的 p/b 估计里没有第 k 次及之后的成交"；
  `kelly_fraction=0` 时结果与改动前逐位一致（默认关闭的回归保证）。
- **Phase 4**：`defensive_asset='999999.SH'` 在 create 时返回 400 且带上原因；
  一个流动性不足的防御资产触发 `defensive_sleeve_uninvested` 计数并按
  `cash_yield_annual` 计息；`cash_yield_annual=0` 时与改动前逐位一致。
- **Phase 5**：带 `model_version` 的 ML 请求跑出的结果，在切换 active 模型之后重跑
  **完全一致**（这就是可复现性的证明）；不带 `model_version` 的 ML 请求仍然 400。

---

## 6. 关键文件

- `apps/etf_rotation/rotation_engine.py`（打分 `:318-367`、排序 `:633-643`、
  防御仓 `:833-865`、权重分配 `:868-905`、组合构建 `:906-1050`、config `:96-131`）
- `apps/etf_rotation/backtest_engine.py`（快照帧 `:387-394`、`validate` `:237-259`、
  流动性下限 `:861-899`、分红计入 `:1305-1337`、trade_log `:1116-1124`、
  `price_basis` `:1609`）
- `apps/etf_rotation/signals.py`（`resolve_trade_date` `:81-107`、
  `persist_signal` `:241-300`、metadata 组装 `:346-357`）
- `apps/etf_rotation/models.py`（信号 `:225-267`、回测列默认值 `:355-367`）
- `apps/etf_rotation/serializers.py`（信号字段 `:105-118`、RULE-only 校验 `:204-215`）
- `apps/etf_rotation/views.py`（create 与并发上限 `:505-534`、config 重建 `:170-205`）
- `apps/etf_rotation/etf_universe.py`（分类器 `:154-186`、关键词表 `:23-102`）
- `apps/etf_rotation/tasks.py`（universe 同步 `:144-227`、daily 同步 `:331-420`、
  信号 beat 入口 `:492-501`）
- `apps/core/finance_metrics.py`（`win_rate` 已存在，`payoff_ratio` 待加）
- `frontend/src/pages/EtfRotationPage.tsx`（信号卡 `:39-124`）
- `frontend/src/pages/EtfBacktestWorkbenchPage.tsx`（防御资产输入 `:464-466`）
- `config/settings/base.py:275-304`（全部 ETF_* 配置）
- `.github/workflows/ci.yml:119, :257`（测试下限）

---

## 7. 验证结果（2026-10-23 实施记录）

全部数字来自调优窗口 `2016-01-29..2023-12-31`。**holdout 未触碰**：159 行
holdout（`start_date >= 2024-01-01`）里最新的 `created_at` 仍是 2026-10-22
20:48:11 UTC；今天新建的 151 行全部从 2016-01-29 开始。**没有任何
`ModelVersion` 被激活**：`ETF_*` 全部 `is_active=False`（仅有的 5 个 active 行是
9 月的股票模型）。

### 7.1 Phase 2.7 网格

144 组（4 个调仓频率 `WEEKLY, BIWEEKLY, MONTHLY, TRIGGER` × 3 个持仓数
3/5/8 × 3 个 lookback 20/60/120 × 2 个绝对动量开关 × 2 种 weighting），
行 id 210–353，耗时 2181.5s。**144 行全部 `COMPLETED`，
指标缺失 0 行**，75/144 的 alpha 为正，全部 `dedup_track_index=True`。

| 频率 | n | 最佳 alpha | 该行 |
| --- | --- | --- | --- |
| BIWEEKLY | 36 | **+9.39%** | 256 |
| MONTHLY | 36 | +5.89% | 292 |
| WEEKLY | 36 | +5.00% | 221 |
| TRIGGER | 36 | −1.30%（全族为负） | — |

Sharpe 前 5 名全是 BIWEEKLY：256（equal/无绝对动量）、250（equal/绝对动量）、
276（equal/p8/mom20）、257（score/无绝对动量）、251（score/绝对动量）。

**新生产参考行 = id 256** `grid-dedup-rule-biweekly-p3-mom120-noabs-equal`：
BIWEEKLY / max_positions 3 / momentum_lookback 120 / 绝对动量关闭 /
weighting equal。总收益 +99.69%、年化 +9.48%、maxDD 35.41%、Sharpe 0.5357、
Calmar 0.2676、alpha +9.39%、IR 0.3962、换手 22.85x/年、beta 0.5558、
胜率 45.06%、费用 39,524.03、597 笔成交 / 213 次调仓 / 1926 个 session。
它替换掉旧的 row 82（"+18.21% 但 80% 押银行"）。

### 7.2 选择约束是净改善（Done 标准）

同一组参数，把两个约束关掉（`--no-dedup-track-index --max-per-category 0`，
row 355）：

| | 总收益 | maxDD | Sharpe | alpha | 换手 |
| --- | --- | --- | --- | --- | --- |
| 约束开（256） | **+99.69%** | **35.41%** | **0.5357** | **+9.39%** | **22.85x** |
| 约束关（355） | +33.13% | 45.04% | 0.2806 | +4.37% | 23.44x |

约束不但没有把换手推高，反而**同时**降低了回撤和换手——去重把重复的银行腿换成
了真正不同的暴露。**`max_swaps=2` 复查**（同一参数，进程内跑）：swaps=1 →
+87.86% / dd 41.64% / sh 0.4941（562 笔）；**swaps=2 → +99.69% / 35.41% /
0.5357（597 笔）**；swaps=99（不限量）→ +95.88% / 38.12% / 0.5228（599 笔）。
2 是三者最好，取消上限要付约 3.8 个百分点收益，所以现有默认保留。

### 7.3 Phase 3a：三种 weighting（同参数、同一执行路径）

| weighting | 总收益 | maxDD | Sharpe | alpha | 换手 |
| --- | --- | --- | --- | --- | --- |
| equal | **+99.69%** | 35.41% | **0.5357** | **+9.39%** | **22.85x** |
| invvol | +81.86% | **35.24%** | 0.4961 | +8.03% | 24.45x |
| score | +71.73% | 44.53% | 0.4437 | +7.45% | 25.32x |

计划里的预期是"maxDD 下降、alpha 变化不大"：**方向对了一半**——invvol 的 maxDD
确实最低（比 equal 低 0.17pp，几乎打平），但 alpha 让出 1.36pp，且换手升 1.6x。
结论：**invvol 保留为可选项，不做默认**；equal 在收益/Sharpe/换手三个轴上领先。
权重和恒等式有测试兜底（三种模式各花掉恰好 `risk_budget`，残差归到最强腿）。

### 7.4 Phase 3b：分数 Kelly —— 上线但默认关闭

* **默认关闭的回归保证成立**：`--kelly-fraction 0` 与不带该参数的参考跑**逐位一致**
  （+99.69% / 35.41% / 0.5357 / 22.8514x/年）。row 256 的
  `report['counters']['exposure_governor']` 显示
  `enabled=False, mean_scale=1.0, scaled_sessions=0`，`cash_yield_yuan=0.0`。
* **实测赔率给出的边界是负的**。在 2023-12-22 那次调仓上（`kelly_fraction=0.25`）：
  已平仓 51 笔，win_rate 43.14%、payoff_ratio 1.2911，
  `f* = W − (1−W)/b = 0.4314 − 0.5686/1.2911 = **−0.0091**`，
  `raw_scale = −0.0023` 被 `kelly_floor=0` 夹到 0；213 次调仓里 164 次被压缩，
  `mean_scale = 0.238`。这正是 Q4 的答案：这套 RULE 组合**自己的已实现分布不支持
  任何 Kelly 意义上的风险敞口**，按赔率定标会把仓位压向现金而不是"精调"它。
* floor 扫描（fraction 固定 0.25）单调且可读：

  | floor | 总收益 | maxDD | Sharpe | alpha | 换手 | beta |
  | --- | --- | --- | --- | --- | --- | --- |
  | 0（默认） | +35.68% | **6.84%** | **0.9670** | +3.98% | **2.13x** | 0.032 |
  | 0.5 | +72.12% | 17.43% | 0.6921 | +6.74% | 12.13x | 0.293 |
  | 0.75 | +86.32% | 26.55% | 0.5878 | +8.00% | 17.43x | 0.424 |
  | 关 | **+99.69%** | 35.41% | 0.5357 | **+9.39%** | 22.85x | 0.556 |

  floor 0 是个**熔断器而不是油门**：Sharpe 0.967 主要来自 beta 0.03（几乎空仓），
  不是来自更好的选股。floor 0.75 那一档值得注意——它用 13.4pp 的收益换回
  8.9pp 的回撤并把 Sharpe 抬到 0.5878——但这只是**调优窗口上的观察**，
  holdout 没跑过，所以不据此改默认。
* 因此按计划 3b 的裁决规则处理：**代码留着、默认 `kelly_fraction=0`、CHANGELOG 里
  写明"试过了"**，并附上面的边界为负这一条证据。
* 显式测试断言"第 k 次调仓只用它之前已平仓的成交"在
  `ExposureGovernorTests.test_only_trades_closed_before_the_session_are_visible`。

### 7.5 实施过程中修掉的一个坑（数字可比性）

`run_etf_rotation_backtest` 里 `overrides['dedup_track_index'] =
bool(options['dedup_track_index'])`：`--no-dedup-track-index` 是
`action='store_false', default=None` 的三态开关，而 **`bool(None) is False`**，
于是任何不带该 flag 的命令行跑都被静默改成了"去重关闭"。表现是同一份配置下
命令行给 +55.61%/dd 47.72%/sh 0.3788，而网格行 256 给 +99.69%——两边数字一度
不可比。修法是"未指定就不写进 `config_overrides`"，让 `BacktestConfig` 决策，
并加了 `test_an_unset_selection_flag_leaves_the_config_default_alone`。
本节所有表格都在修好之后的同一条路径上重跑（本地 scratch 日志
`tmp/grid/phase3-fixed.log`，`tmp/` 在 `.gitignore` 内，所以上面的表才是记录）。

**受影响的持久化行**：354（invvol）、356–359（Kelly）、360–361 都是在
`dedup_track_index=False` 下写的（355 是有意为之的对照）。它们的列如实记录了
自己关掉过去重，因此**只作为"去重关闭 + 各自 sizing"的测量集保留，不作对比基准**；
上文 7.3 / 7.4 的表格用的是修好之后的临时跑（`--no-persist`，ephemeral 与持久化
已验证同一条代码路径给出同一数字）。

### 7.6 实时信号上的约束落地（Phase 2 Done 标准 + 风险 7）

`ETFRotationSignal` 最新行 2026-10-22（`rule-v1:mom60-score:dedup:cap2`），5 条腿：

| 代码 | track_index_key | category | weight |
| --- | --- | --- | --- |
| 159887.SZ | 中证800银行 | sector_finance | 0.293023 |
| 512800.SH | 中证银行 | sector_finance | 0.246512 |
| 515300.SH | 沪深300红利低波动 | style_dividend | 0.200000 |
| 511130.SH | 上证30年期国债 | bond | 0.153488 |
| 511090.SH | 中债-30年期国债 | bond | 0.106977 |

同一个 `track_index_key` 出现次数最多 1（159887 与 512800 跟的是**不同**指数，
所以由 `max_per_category=2` 而不是去重来约束，实测 `sector_finance` 恰好 2、
`bond` 恰好 2）；`sum(weight) = 1.0` 精确成立。这一行也是"统一 weighting 默认值
要重跑信号任务确认"（风险 7）的那次重跑产物：`metadata.config` 里
`weighting=score`、`dedup_track_index=true`、`max_per_category=2`、`max_swaps=2`
都在，页面参数面板渲染的就是这 10 个键。

**Phase 5 没有做**，按计划自己的条件（"只有在确实需要从页面跑 ML 时才做"）：
目前三个 `ETF_*` 模型全部 `is_active=False`，从 API 跑 ML 回测没有可跑的对象，
而且它需要 `_resolve_predictor` 增加按版本加载的分支——等真要用页面调 ML 的时候
再启动，比现在建一条没人走的路好。

### 7.7 Phase 0c：weighting 默认值的 holdout 裁决（2026-10-24，窗口自此关闭）

事前登记（跑之前写死）：三臂 `equal / score / invvol`，其余参数与 row 256 完全一致
（BIWEEKLY / p3 / mom120 / 绝对动量关闭 / dedup 与 cap 用默认值），窗口
`2024-01-01..2026-10-20`，ephemeral 运行；判据依次为 Sharpe、maxDD、换手；
**只有 `equal` 同时在调优窗口与 holdout 胜出才改默认值**。

| weighting | 总收益 | maxDD | Sharpe | 换手 | alpha | 成交笔数 |
| --- | --- | --- | --- | --- | --- | --- |
| equal | −7.15% | 28.80% | 0.0913 | 29.16x | −9.00% | 273 |
| **score** | **+1.70%** | **26.02%** | **0.1987** | 32.18x | **−5.01%** | 318 |
| invvol | −7.78% | 26.30% | 0.0782 | 30.53x | −9.11% | 314 |

**裁决：保持 `weighting=score` 默认不变。** `equal` 在调优窗口的三项全胜
（+99.69% / 0.5357 / 22.85x 对 +71.73% / 0.4437 / 25.32x）**没有**在 holdout 上复现：
它的 Sharpe 只有 `score` 的 46%，收益比 `score` 低 8.85pp，回撤反而更深。
两个窗口结论相反，按预案不做任何默认值改动（三处默认值 `models.py:398`、
`backtest_engine.py:213`、`base.py:305` 均未动，也不需重跑实盘信号）。
**这条相反本身就是本轮关于过拟合最有价值的记录：调优窗口上 28pp 的收益差在样本外
翻成了 8.9pp 的反向差，任何"从单窗口网格直接部署赢家"的做法都应被视为未经验证。**
holdout 自此关闭；本计划后续各期不再查询该窗口，Phase 5 有测试钉住账本任务。

### 7.8 Phase 0b：页面核对（无浏览器，代码/测试层面）

四项逐一核对，未发现页面与接口不一致，故未新增 vitest 用例：
- 信号卡三个日期互相独立：as-of 用 `signal.date` 原样、`created_at`/`updated_at`
  本地化，`EtfRotationPage.test.tsx` 已有断言 `generatedAt != updatedAt`。
- 陈旧告警只在 `freshness.is_stale` 为真时渲染（`EtfRotationPage.tsx:155-166`），
  lag 0 的用例断言卡片里没有告警文本。
- `metadata.config` 由 `Object.entries` 全量渲染（`:267-271`），测试专门放入一个
  前端从未见过的键并断言其出现。
- 防御资产下拉显示 `avg_amount_20d`（`EtfBacktestWorkbenchPage.tsx:76,535`），
  fixture 覆盖了大小两种成交额。
**限制：本环境没有可用浏览器，未做人工点击，只做了接口返回 + 前端测试 + 静态审查。**

### 7.9 Phase 1：信号留痕与数据源健康告警（2026-10-24）

**留痕。** 新表 `ETFRotationSignalRun`（migration `0005`）只追加：任务的每次尝试写
一行 `OK / REFUSED / FAILED`，`check_etf_rotation_feed_health` 在基准或特征落后于
最新 `ETFDaily` 时写一行 `HEALTH / FEED_STALE`；基准或特征完全没有行时也判 stale
（`benchmark_missing` / `feature_missing`），否则“从未同步”会以 lag 0 混过检查。
`REFUSED` 写完继续抛
`CommandError`，所以 Celery 仍是 FAILED；`--dry-run` 一行都不写。`diagnostics`
记录快照日、最新快照/bar/基准日、基准与特征的滞后 session 数、可排名行数以及
`FEATURE_COLUMNS` 每一列的 null 数；日期统一存 ISO 文本（JSONField 无日期编码器，
首轮测试就是在这里炸的）。`task_id` 在 worker 内取 Celery request id，手工命令为空。
借用的诊断逻辑来自 `validate_data_quality` 的基准连续性规则，没有复制实现。

**告警。** `AlertRule.asset` / `AlertEvent.asset` 改为可空，`condition_type` 增加
`FEED_STALE`，并把 email/websocket 派发链路全部改成 null-safe（`tasks.py` 四处、
两个 serializer 的 `asset_symbol`/`asset_name`）。`check_alert_rules` 在每次运行
开头 `get_or_create` 一条 `asset=NULL` 的 `ETF data feed health` 规则，channels
取自 `ALERT_NOTIFY_EMAILS` 与 websocket 开关，沿用 `cooldown_minutes=60` 和
`dispatched_channels`。事件 `metadata` 带 `signal_run_id / reason_code / diagnostics`
的裁剪版。自愈条件：只读取日期等于最新 bar 的 `FEED_STALE` 行，bar 前进而缺陷未
重复时告警自动消失，不会把昨天的坏数据永久重播。

**接口与页面。** `/etf/signals/freshness/` 增加 `last_attempt`（最近一次尝试，含
outcome/reason/时间）与 `feed_stale`（仅最新 bar 上的缺陷），两者都与 `is_stale`
（信号行自身的 as-of 滞后）独立。`/etf` 信号卡对非 OK 尝试和 feed 缺陷分别渲染
告警；告警中心对 `asset=null` 的 `FEED_STALE` 事件显示“数据源健康”而非 null。
防御资产下拉在快照落后最新 bar 超过 5 个 session 时清空 20 日成交额，避免把旧
成交额当成今天的容量证据。

**验证（无浏览器）：**
- 手工执行 `generate_etf_rotation_signal --date 1900-01-01` → 恰好一条
  `REFUSED / no_eligible_rows` 行，`eligible_rows=0`、`snapshot_date=1900-01-01`、
  基准与特征滞后均为 0，命令退出码 1；随后删除这条验证用行，表恢复为空。
- `check_etf_rotation_feed_health.run()` 在当前数据上返回 `OK / created=false`：
  bar 与快照同为 2026-10-22，基准已到 2026-10-23，未写健康行。
- 定向测试 20 条通过（4 留痕、5 健康、6 freshness、3 无资产告警派发、2 既有
  Phase 9 告警回归）；全量后端 **629**（614 → 629），前端 **76**（72 → 76），
  `makemigrations --check` 干净，`tsc --noEmit`、lint/build 静态基线通过，
  `export_documentation_facts --check` 通过。
- 股票价格告警的既有测试一条未改地通过，改动面没有超出计划列出的四处。
- **限制：** 依旧没有可用浏览器，未做人工点击；告警派发的真实验证在单元测试中
  用 locmem email 与 fake channel layer 完成。新 beat 条目需要重启 beat/worker
  才会被调度，部署时按 runbook 重启。

### 7.10 Phase 2：只度量、不改行为（2026-10-24）

**逐位一致。** 参考配置（row 256）沿同一条引擎路径重跑：`equity_curve`
1926 个点的 sha256 为 `44090509…6012`，与持久化行一致；597 笔成交的 sha256
为 `e8bd3712…1cf2`，也一致。`signals_generated=213`、`rebalances=213`、
`filled=597`、`min_commission_bound_fills=113`。`closed_trades` 只有
`position_fees_paid` 一个字段变化（见下），`pnl`、现金、净值全部逐位不变。

**费用行的语义变化（只动报告）。** 已平仓腿的 `position_fees_paid` 现在包含
滑点，而不只是佣金：233 条已平仓合计 39,243.23 → 168,901.10，差额
129,657.86 正是这些腿的滑点（全窗口滑点 130,592.93，差额来自期末未平的腿）。
`pnl` 早已把滑点含在成交价里，因此不动；旧行不回填，跨版本比较应使用
`total_fees` / `total_slippage_cost` 两个指标列。

**新增报告块（不参与计算）。**

- `report['rebalances']`：每构建一次计划写一条，携带选择层自己的
  `mode / swaps_used / entries / exits / refills / displacements`。参考跑 213 条；
  一笔未成交的计划也有记录，这是 `trade_log` 无法表达的。
- `report['turnover']`：按成交 `reason` 拆分，`rebalance_up/down` = 漂移，
  `rotated_out` = 自主换仓，买入与卖出两侧都计。参考跑：

  | 桶 | 年化换手 | 名义金额（元） | 占比 |
  | --- | --- | --- | --- |
  | 漂移 | 11.60x | 66,282,200.00 | 50.75% |
  | 自主换仓 | 11.25x | 64,311,252.17 | 49.25% |
  | 其他 | 0.00x | 0.00 | 0.00% |
  | **合计** | **22.8514x** | **130,593,452.17** | 100% |

  合计与 `turnover_annual` 精确对账。**一半的换手来自漂移**，不是主动换仓：
  这直接回答了"换手是不是被漂移被迫产生"的问题（答案是"大约一半"）。
- `report['sub_windows']`：从已持久化曲线计算的三个等分、互不重叠的块：

  | 块 | 区间 | 收益 | maxDD | Sharpe | 换手 |
  | --- | --- | --- | --- | --- | --- |
  | 1 | 2016-01-29..2018-09-13 | +8.14% | 17.96% | 0.3697 | 11.45x |
  | 2 | 2018-09-14..2021-05-13 | +94.35% | 29.95% | 1.0673 | 22.17x |
  | 3 | 2021-05-14..2023-12-29 | −4.99% | 34.33% | 0.0090 | 29.19x |

  这不是三个独立样本（相邻块共享一个基点，收益可链式复合回全窗口），但足以
  说明全窗口 Sharpe 0.5357 不是"整段都有效"：块 2 贡献了几乎全部收益，块 3
  回撤最深且换手最高。
- `report['model_versions']`：RULE 为 `{'RULE': None}`。ML 的 `ModelVersion`
  id/version/type 由 loader 附在 callable 上，`_call_predictor` 写进
  `attrs['rotation']`，报告读取实际参与打分的那个版本，而不是再查一次"当前
  active 行"；ENSEMBLE 逐源记录，`skipped_sources` 显式为 `None`。
- `counters['min_commission_bound_fills']`（113）与
  `counters['band_suppressed_deltas']`（0；Phase 3 才会写入）。

**费率敏感性（同配置、同一执行路径、ephemeral）。**

| 成本假设 | 成交 | 触底笔数 | 佣金 | 滑点 | 按腿费用（含滑点） | 换手 | 总收益 | maxDD | Sharpe | alpha |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 万三 / 免五 | 597 | 113 | 39,524.03 | 130,592.93 | 168,901.10 | 22.85x | +99.69% | 35.41% | 0.5357 | +9.39% |
| 万一 / 不免五 | 597 | 128 | 13,805.52 | 132,776.26 | 145,520.28 | 22.90x | +105.88% | 34.55% | 0.5547 | +9.79% |

佣金从 3.95 万降到 1.38 万（省 2.57 万），但**触底笔数从 113 升到 128**：
费率越低，越多小单落在 5 元地板上，佣金节省并没有按费率比例兑现。滑点与换手
略升，因为佣金变化改变了现金与整手取整路径。净效果 +6.19pp 收益、+0.0190
Sharpe、−0.86pp 回撤。**这是单窗口观察，不据此改变任何默认值**；它量的是
费用假设的敏感度，不是"该换券商"。

**CLI 与指纹。** 新增五个成本旋钮（`--commission-rate`、`--min-commission`、
`--slippage`、`--liquidity-floor-multiple`、`--min-trade-notional-fraction`）；
`--grid` 拒绝它们（网格必须共享一套成本），单跑与 `--backtest-id` 接受，
显式值与行不一致时报 mixed。`min_trade_notional_fraction` 现在是列
（migration `0006`，默认 0.005，等于被替换的硬编码值）并进入
`to_parameters()`，因此**续跑指纹改变**：旧 PAUSED 行会从头重跑，这是计划
预期的代价；默认值本身不改任何数字。

**验证。** 后端 **629 → 636**（新增 7 条，包括逐位一致/账本恒等、漂移与自主
换手拆分、滑点费用、默认阈值、子窗口覆盖、模型版本、报告每条计划一条）；
`makemigrations --check` 干净；`export_documentation_facts --check` 通过；
前端无改动（**76** 不变），vitest、`tsc --noEmit`、lint/build 静态基线通过。

### 7.11 Phase 3：no-trade band 与容量缩仓（2026-10-24）

两个旋钮都默认关闭：`drift_band_fraction=0.0`、`liquidity_mode='reject'`、
`adv_participation_cap=0.0`。默认路径重跑 row 256：`curve_len_match`、
`curve_digest_match`、`log_len_match`、`log_digest_match` 全部为真（1926 点
净值、597 笔成交）。下面的表**只陈述证据，不寻优**；Phase 3 不改变任何默认值。

**no-trade band（调优窗口 2016-01-29..2023-12-31，ephemeral）。**

| 费用假设 | band | 成交 | 漂移成交 | 触底笔数 | 被带抑制 | 佣金 | 滑点 | 换手 | 总收益 | maxDD | Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 万三 / 免五 | 0 | 597 | 364 | 113 | 0 | 39,524 | 130,593 | 22.851x | +99.69% | 35.41% | 0.5357 |
| 万三 / 免五 | 1% | 597 | 364 | 113 | 0 | 39,524 | 130,593 | 22.851x | +99.69% | 35.41% | 0.5357 |
| 万三 / 免五 | 2% | 556 | 323 | 72 | 49 | 39,342 | 130,519 | 22.822x | +99.88% | 35.48% | 0.5362 |
| 万一 / 不免五 | 0 | 597 | 364 | 128 | 0 | 13,806 | 132,776 | 22.904x | +105.88% | 34.55% | 0.5547 |
| 万一 / 不免五 | 1% | 597 | 364 | 128 | 0 | 13,806 | 132,776 | 22.904x | +105.88% | 34.55% | 0.5547 |
| 万一 / 不免五 | 2% | 558 | 325 | 89 | 55 | 13,623 | 132,760 | 22.878x | +106.10% | 34.59% | 0.5553 |

读法有三点。其一，**1% band 在这个三腿等权账本上一次都没有生效**：阈值是
`1% x 每条腿约 33% 的目标` ≈ 组合的 0.33%，低于 0.5% 的最小交易额下限，
所以 0 与 1% 的曲线逐位一致——band 只在 2% 时越过下限。其二，2% band 压制
49（万三）/55（万一）个小额漂移单，漂移成交 364 → 323/325，而自主换仓
（`rotated_out`）始终 233 笔，符合"强制退出永不压制"的设计。其三，效果是
**误差级**：收益 +0.19pp/+0.22pp、回撤 +0.07pp/+0.04pp、Sharpe
+0.0005/+0.0006；佣金因少付地板价下降 182/183 元，但换手几乎不动（只删掉了
小单）。没有证据支持打开 band，默认保持 0。

**容量缩仓。** `scale` 模式把每条腿夹到 `adv_participation_cap x` 20 日平均
成交额，被剪掉的权重先按余量比例分给其他腿，剩余才落到防御仓；无法测量
成交额的腿仍被拒绝。`reject` 模式对容量旋钮完全不敏感（设置 cap 与不设置
结果一致，已有测试钉住）。缩仓只在计划阶段改目标权重，成交与记账路径不变；
`capacity_scalings` 逐腿记录计划权重、容量权重与观察到的成交额，续跑状态
一并持久化。**本阶段没有在真实窗口上跑 scale 模式的收益表**，因为计划只要求
band 证据；scale 的语义由单元测试覆盖。

**指纹与迁移。** 三个字段进入 `to_parameters()`，因此指纹改变，旧 PAUSED
行从头重跑；migration `0007` 加三列，默认值逐位复现今天。CLI 新增
`--drift-band-fraction`、`--liquidity-mode`、`--adv-participation-cap`，
`--grid` 拒绝它们（网格共享一套执行模型）。

**验证。** 后端 **636 → 641**（5 条：band=0 无抑制、强制退出不被压制、
暂停后续跑一致、容量重分配先于防御回退、reject 模式不受容量旋钮影响）；
`makemigrations --check` 干净；前端无改动（**76** 不变）。`_apply_liquidity_floor`
的防御仓闲置路径被抽成 `_idle_defensive_check`，拒绝记录与计数器逐字段保持
原样，默认重跑的逐位一致是它的回归证明。

### 7.12 Phase 4：RULE 参数 walk-forward（2026-10-24）

CLI-only、ephemeral：`--grid --walk-forward-steps N`，不写任何
`ETFRotationBacktest` 行。折由 `walk_forward.expanding_folds` 生成
（session 列表作 `trade_dates`、`horizon_days=1`、`min_train_days=250`），
每折只在 `[train_start, train_end]` 上跑完整 144 格，按 Sharpe（同分取低
换手）选赢家，再用该参数交易 `[valid_start, valid_end]`。

**折数从 6 降到 4。** 先按计划请求 N=6：第 1 折（训练
2016-01-29..2017-02-13、交易 2017-02-14..2018-04-03）赢家
`monthly-p3-mom120-abs-equal`，样本外只有 **14 次调仓**，触发计划预先写死的
"任一折 < 20 次调仓 → 降到 N=4" 护栏；该折数字不进入下表。N=4 的折 1 恰为
**20**，其余 21/21/20，全部达到下限。没有为了凑数补边界，也没有再降。

**每折选择与样本外（面板 2016-01-29..2023-12-31，4 折，耗时 2,955s）。**

| 折 | 训练窗口 | 交易窗口 | 选中配置 | 训练 Sharpe | 训练调仓 | 收益 | 年化 | maxDD | Sharpe | alpha | 换手 | 调仓 | 等于 row 256 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2016-01-29..2017-02-13 | 2017-02-14..2018-10-30 | monthly-p3-mom120-abs-equal | 1.3171 | 13 | +0.98% | +0.59% | 16.45% | 0.1090 | +7.16% | 9.3237 | 20 | 否 |
| 2 | 2016-01-29..2018-10-30 | 2018-10-31..2020-07-21 | monthly-p3-mom120-noabs-equal | 0.4904 | 33 | +75.26% | +40.25% | 25.05% | 1.4493 | +12.86% | 13.3196 | 21 | 否 |
| 3 | 2016-01-29..2020-07-21 | 2020-07-22..2022-04-13 | monthly-p5-mom120-noabs-score | 1.0044 | 54 | -2.32% | -1.41% | 27.67% | 0.0746 | +4.45% | 14.9174 | 21 | 否 |
| 4 | 2016-01-29..2022-04-13 | 2022-04-14..2023-12-28 | monthly-p8-mom120-abs-equal | 0.7262 | 75 | -27.75% | -17.83% | 29.58% | -1.4334 | -17.07% | 13.6006 | 20 | 否 |

**读法。** 四折的赢家全部是 `MONTHLY` + `mom120`，但持仓数在 3/5/8 之间跳、
绝对动量 2 开 2 关、weighting 3 次 equal 1 次 score；**row 256 的
`BIWEEKLY/p3/mom120/noabs/equal` 在 0/4 折被选中**。按计划这就是结论：
参考配置的参数是 2016–2023 整段窗口的产物，不是逐折选择会稳定复现的参数；
**不改任何默认值**，也不因为它样本外表现去追参数。四折样本外 Sharpe 为
0.1090 / 1.4493 / 0.0746 / −1.4334，中位数约 0.09，最好与最差相差极大——
单折 20 次调仓的读数不足以支撑任何"更优"的宣称；计划只要求陈述，不寻优。
四折各自从初始资金重开，表内收益不可直接复利成一条曲线（若强行复利约为
+24.9%，仅作量级参考）。

**为什么不是 row 256 的对照。** row 256 是同一段 2016–2023 全窗口一次跑完
（+99.69% / 35.41% / 0.5357）；walk-forward 的每折只交易自己的样本外块，
训练段不产生收益，四块之间也没有资金连续。两者不是同一条曲线的两个读数，
不能相减当作"walk-forward 损失"。它回答的是另一个问题：*如果每折都重新选
参数，会选到什么、然后表现如何*。

**护栏与薄度。** 折 1/4 的 20 次调仓正好压线；训练窗口最短 13 次调仓
（折 1）、最长 75 次（折 4）。计划的风险条目已预期"单折约 31 次调仓、Sharpe
置信区间宽"，实测更薄。因此这张表只支持两个结论：参考配置不具备逐折稳定性；
以及逐折选择本身在该面板上并不产生一致正收益（后两折为负）。它不支持调参。

**验证与边界。** CLI 拒绝非 RULE、`--backtest-id`、`--queue`、成本旋钮与
N<2；`--grid-plan` 只打印计划不执行；短面板减少折数而不补边界（有测试）。
后端 **641 → 645**，前端不变（**76**）。新增 4 条测试钉住：短面板不补折、
每折选择窗口不越过自己的交易窗口、赢家由声明的 Sharpe→换手准则决定、分折
表带自己的调仓次数。命令不写库，页面无入口。
