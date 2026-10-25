# ETF 轮动 — 后续 Phases 6-12：样本外证据、资金基数与 ML 复赛

> 上一轮（Phases 0-5）见 [`etf-rotation-selection-sizing-2026-10-23.md`](etf-rotation-selection-sizing-2026-10-23.md)，
> 实施记录在其 §7。本计划承接 PR #62 合并后的状态（`origin/main` @ `63342b0`），
> 只安排它没有覆盖的开放项。

## 0. 起点与硬纪律

**已经发生的两件事决定了这一轮的形状：**

1. **holdout `2024-01-01..2026-10-20` 已烧掉**（§7.7）。`weighting` 默认值用它裁决过一次，
   计划明文"自此关闭"。任何新证据都不能再查这个窗口。
2. **参考配置被自己的 walk-forward 判为不稳定**（§7.12）：row 256 的
   `BIWEEKLY/p3/mom120/noabs/equal` 在 4 折里被选中 0 次；四折赢家分别是
   `monthly-p3-mom120-abs-equal`、`monthly-p3-mom120-noabs-equal`、
   `monthly-p5-mom120-noabs-score`、`monthly-p8-mom120-abs-equal`。

**贯穿全程的硬纪律：**

- 不查询 holdout `2024-01-01..2026-10-20` 用于排序或选优。需要样本外证据时走
  路线 A（前向）或路线 B（净化 CV，只在 2016-2023 内）。
- 任何 `ETF_*` `ModelVersion` 在通过新的诚实闸门前保持 `is_active=False`。
- CI 测试下限只升不降（当前后端 685、前端 77）。
- 每期跑 [§5](#5-验证基线) 的基线验证。
- 本环境无浏览器；涉及页面的结论只能到 vitest + `tsc --noEmit` + 静态基线 + API 契约。

---

## 1. A / B / C 三条样本外证据路线（详解）

### 1.1 背景：holdout 是什么，为什么烧掉了

- **调优窗口 `2016-01-29..2023-12-31`**：允许用来搜参数的区间。144 格 RULE 网格在这里
  选出 row 256。因为看着数据选，漂亮数字含**过拟合**成分。
- **holdout `2024-01-01..2026-10-20`**：选参数时故意不看的数据；锁定配置后只跑一次，
  得到最接近真实交易的样本外估计。§7.7 正是靠它发现 `equal` 在调优窗口碾压
  （+99.69%/Sharpe 0.5357）却在 holdout 落败（−7.15%/0.0913）——这是全轮最有价值的
  过拟合证据。
- **为什么烧掉**：holdout 只有查一次才诚实。一旦看结果又回头改参数，它就泄漏进选择
  过程、退化成第二个调优窗口。于是现在的处境是：参考配置被自己的 walk-forward 判为
  不稳定，而唯一诚实的样本外窗口已用完。

### 1.2 路线 A — 前向模拟盘累积（paper trading）

- **是什么**：把实时账本当成一个**从今天起向前生长的新 holdout**。每交易日信号触发、
  账本盯市，数月后攒出"在选配置时还不存在的数据"上的真实战绩。
- **优点**：最诚实（真样本外，构造上不可能有未来函数）；机器已现成
  （`mark_etf_rotation_positions_daily`、carry-forward、`/etf` 账本卡）；零污染。
- **缺点**：**慢**。攒够有统计意义的调仓次数需数月（walk-forward 下限约每折 20 次；
  BIWEEKLY 一年约 26 次）；只能答"当前配置往后好不好"，不能回头比较备选。
- **落地**：Phase 6（份额/人民币盈亏，A 的前置）+ Phase 11（冻结纪律 + 月度复核 +
  偏离告警）+ Phase 10 的 10b 步（RULE 与 ML 并排前向）。

### 1.3 路线 B — 净化组合交叉验证（CPCV，只在 2016-2023 内）

- **是什么**：在调优窗口上用同一段历史生成多组训练/测试切分，测试块之间加 embargo
  （隔离带 = label horizon），复用 `walk_forward.py` 的强制 purge，补上"组合式多切分 +
  embargo"。
- **优点**：**快**；产出样本外 Sharpe 的**分布**（中位数、四分位、正收益切分占比）而非
  单个脆弱数字，直接回应"row 256 是窗口特异、0/4 折"；可看跨切分稳定性。
- **缺点**：仍全在 2016-2023 内，共享那个时代的行情特征——跨 2016-2023 稳不代表
  2024+ 稳；若拿它挑赢家就沦为"同一段数据再搜一遍"。
- **落地**：Phase 8（框架 + row 256 稳定性分布）+ Phase 9（max_positions 5→3 的证据）+
  Phase 10 的 10a 步（ML vs RULE 的 CV 复赛）。

### 1.4 路线 C — 工程/产品化地基

- **是什么**：把力气花在让已交付功能真正可用、把开放项收口，明确不再跑参数选优搜索。
- **优点**：零过拟合风险；交付看得见的价值（账本显示人民币盈亏、queued sized run
  可用、榜单不再混入 dedup-off 行）；与计划对 Kelly/band 的既有结论一致。
- **缺点**：单独用 C 会把核心疑点留在原地；所以 C 是**地基**，为 A、B 提供可用的账本与
  列，不单独回答"该不该上"。
- **落地**：Phase 6（资金基数/份额）+ Phase 7（Kelly 列）+ Phase 12（清理、文档、
  可选 ML-via-API）。

### 1.5 三者如何咬合

```
C（Phase 6/7/12）搭地基：账本能算人民币盈亏、sizing 旋钮能排队、数据可信
        │ 使能
        ├──────────────► B（Phase 8/9/10-10a）：快速、诚实的稳定性分布，只在 2016-2023 内
        └──────────────► A（Phase 6→10-10b→11）：缓慢、真实的前向样本外，从今天起生长
```

B 给"现在就能读"的稳健性证据，A 给"越攒越硬"的真实证据，C 让载体真正可用——同时通过
**永不再查 2024-2026** 尊重 holdout 纪律。

---

## 2. 分期计划

### Phase 6 — 实时账本资金基数与份额定标（C 地基，使能 A）

**问题：** `mark_etf_rotation_positions_daily` 只在持仓行带 `shares` 时才算人民币
`pnl`；信号生成器故意写 `shares=NULL`，因为系统没有"账户总资金"输入。

**做法：**

- `config/settings/base.py` 新增 `ETF_SIM_CAPITAL_BASE`（默认 **1,000,000**；设 0 退回旧
  的 `shares=NULL`/`pnl=NULL` 行为）。
- `signals.persist_signal` 在基数为正时按
  `shares = floor(base × weight / entry_price / 100) × 100` 写入新持仓；整手向下取整，
  绝不花超过目标名义的钱。
- carry-forward 的持仓**保留**原份额：它刷新的是目标权重，持仓大小与成本是历史，不能用
  另一个基数在原始价格上重述。
- 改完需要重跑一次实时信号，让份额落地（与 §7.6 统一 weighting 默认值时同一处理）。

**Done：** 基数为 0 时账本逐位不变；默认基数下新信号带整手份额，次日盯市产出非空人民币
`pnl`，页面显示真实数字而非 `--`。

### Phase 7 — 把 sizing/execution 旋钮提升为回测列（C）

**问题：** `kelly_*` 与 `cash_yield_annual` 不是 `ETFRotationBacktest` 的列，`--queue`
会拒绝它们——排队跑无法携带 sized book（BACKLOG "The Kelly governor…" (b)）。

**做法：**

- 新增 5 列：`kelly_fraction` / `kelly_cap` / `kelly_floor` /
  `kelly_lookback_trades` / `cash_yield_annual`，默认值逐位复现旧引擎。
- `_QUEUE_KNOB_FIELDS`、`views._backtest_config_for`、`tasks.run_etf_rotation_backtest`、
  序列化器同步；`--queue` 不再拒绝这些旋钮。
- 指纹会改变，旧 PAUSED 行从头重跑（与 `min_trade_notional_fraction`、band/capacity
  三列同一处理），在 CHANGELOG 与迁移注释写明。

**Done：** 带 `kelly_fraction>0` 的配置能排队跑完；`kelly_fraction=0` 时逐位一致。

### Phase 8 — 净化组合交叉验证框架（B）

**问题：** row 256 在 walk-forward 里 0/4 折被选中；holdout 已烧掉。需要不依赖单一
holdout、不查 2024+ 的稳健性度量。

**做法：**

- `walk_forward.combinatorial_purged_folds`：把面板切成 `n_blocks` 个连续块，
  取 `test_blocks` 块的组合作为验证集，其余日期经 label-window purge + 后置 embargo
  组成训练集；`audit_combinatorial_folds` 重新推导并核对。
- CLI：`run_etf_rotation_backtest --cpcv-blocks N --cpcv-test-blocks K --cpcv-embargo D`，
  ephemeral、不写库；对固定配置跑一次连续曲线，按每个 held-out 块切片产出
  return / maxDD / Sharpe 的**分布**（min/q1/median/q3/max/正收益占比）。
- 硬护栏：任何 `--end >= 2024-01-01` 直接拒绝；拒绝 `--grid`/`--queue`/`--backtest-id`/
  ML model/`--name`。定位是**量稳定性，不选优**。

**Done：** 测试断言训练日期与 held-out 块之间至少隔 embargo、且不越过 2023-12-31；
CLI 对固定配置产出分布表；不写行、不激活模型。

### Phase 9 — 实盘选择默认值对齐：max_positions 5 → 3（B 提供证据，C 落地）

**问题：** 实盘默认 `max_positions=5`，但生产参考行 row 256 是 `p3`，§7.7 holdout 裁决
也用 `p3`——实盘默认与已验证配置不一致。

**定位：** 主依据是**与参考行对齐**，不是"p3 被证明更优"。§7.12 四折分别选
p3/p3/p5/p8，row 256 的 p3 是 0/4 折，所以"更优"无证据；CPCV 只陈述 p3 vs p5 的稳定性
分布，不据此宣称赢家。

**做法：**

- `ETF_MAX_POSITIONS` 默认 5→3；`rotation_engine.DEFAULT_MAX_POSITIONS`、
  `RotationConfig`、`BacktestConfig`（改为读 settings）、两个模型列默认值、
  `train_lstm` 的 Top-N fallback 全部对齐。
- 改完重跑实时信号（面向用户的权重变更），确认 `sum(weight)=1` 与去重/类别上限仍成立。
- 不改历史回测行的 `max_positions` 列；只改新建行的默认。

**Done：** 五处默认一致为 3；一条测试钉住"dataclass/列/settings 同源"，且部署方
`ETF_MAX_POSITIONS` 覆盖仍然优先。

### Phase 10 — ML 模型公平复赛（A + B）

**背景：** ML 已训练并在 holdout 上落败——Phase D 闸门 FAIL（LSTM alpha 0.0888 vs
RULE 0.1821、beta 1.10、maxDD 0.46；LightGBM/ENSEMBLE 三配置全输）。三个 `ETF_*` 均
`READY` + `is_active=False`。**但 LSTM 的信号层 mean rank IC ≈ 0.065、5/6 折为正**——
失败在组合构建，不在选股信号。

**假设：** ML 的 rank 信号有微弱 merit；若套上与 RULE 参考行**完全相同**的防御/风险
叠加，组合指标可能改善。用 10a（B）+ 10b（A）检验，不再碰 holdout。

**做法：**

- **10a 净化 CV 复赛**：用 Phase 8 的切分，对 LIGHTGBM/LSTM 跑"ML 选股 + RULE 同款
  风险叠加"，与 row 256 的分布并排比较。ephemeral、不写库、不激活。
- **10b 前向并排模拟盘**：新增 `signal_type=SHADOW` 的并行账本。`ETF_ML_SHADOW_MODEL`
  为空时 beat 任务是 no-op；设为 LIGHTGBM/LSTM/ENSEMBLE 后每交易日 19:50 生成影子信号，
  账本按 `signal_type` 分开平仓。API 默认隐藏 SHADOW，`?signal_type=SHADOW` 才可见。
- **闸门**：只有 10a 的分布**且** 10b 累积到足够调仓次数后同时优于 RULE，才考虑一次
  单发激活；否则"仍不敌、保持 inactive"是合格结论。任务 fail-closed：没有 active
  工件时写 `REFUSED/model_registry_missing` 并抛错，绝不静默退回 RULE。

**Done：** 影子账本与生产账本互不污染（份额、平仓、API、告警四路隔离）；没有 active
ML 工件时任务显式失败；任何阶段无 `ModelVersion` 被激活。

### Phase 11 — 前向样本外监控纪律（A）

**问题：** holdout 已烧掉，唯一能持续产出诚实样本外证据的是向前生长的实时账本。

**做法：**

- 冻结纪律成文（本文件 §1.2）：账本自启动日起 **6 个月内不得据其结果改任何默认参数**。
- `report_etf_rotation_live_track` 只读报告：按 `signal_type` 分书汇总已实现/浮动盈亏、
  胜率、均值收益，并与**声明的**参考回测（`ETF_LIVE_REFERENCE_BACKTEST_ID`）的
  已平仓分布比较；没有声明参考时只描述、不做 drift 判定。
- 偏离告警：`AlertRule.ConditionType.LIVE_DRIFT`（asset-less），由月度 beat 任务传
  `--alert` 时写入；5 分钟价格循环显式跳过该类型。
- 样本下限 `ETF_LIVE_DRIFT_MIN_CLOSED_TRADES`（默认 10）：少量成交不构成 drift 证据。

**Done：** 报告无任何写路径（信号/持仓/参数/回测行计数不变）；告警只在 breach 且样本
足够时写入；冻结纪律在本文件成文。

### Phase 12 — 清理、文档与可选产品面（C）

- **dedup-off 行移出榜单**：不按名字硬编码排除；`/etf/backtests/` 新增
  `?dedup_track_index=true|false`，行用自己的列自描述。默认不过滤（有意的对照行 355
  保持可见）。
- **`position_fees_paid` 语义断代**：在 `backtest_engine` 的 closed-trade 记录处写明
  pre-0.1.23 只含佣金、之后含滑点，跨版本比较用 `total_fees`/`total_slippage_cost`。
- **band/capacity 的 scale 模式证据**：本轮**不补**真实窗口表，默认保持
  `0.0/reject/0.0`；理由是无法在不查询 holdout 的前提下把"scale 更好"与窗口特异区分开，
  且 band 证据已是误差级（§7.11）。
- **防御默认值**：`ETF_DEFENSIVE_ASSET` 已是唯一 settings 源；`models.py` /
  `backtest_engine.py` 的字面量只是 settings 未定义时的 `getattr` fallback，且读同一个
  键。把 `rotation_engine` 引入 `models.py` 会让迁移导入 pandas，收益不抵成本，故保留。
- **Phase 5 ML-via-API**：不建。门控条件是 Phase 10 产出一个可部署 ML 候选；当前没有
  active ML 工件，API 继续用 400 拒绝 ML。

---

## 3. 依赖与执行顺序

```
Phase 6  (资金基数 ¥1M/份额)  ← 先做，C 地基，使能 A 与 Phase 10b
Phase 7  (Kelly/现金收益列)   ← 独立，使能排队 sized run
Phase 8  (净化 CV 框架)       ← B，为 Phase 9/10a 提供诚实度量
Phase 9  (max_positions 5→3)  ← 依赖 8 的证据；对齐参考行 row 256
Phase 10 (ML 复赛)            ← 依赖 8（CV）与 6（前向账本）
Phase 11 (前向监控纪律)       ← 依赖 6；启动后长期运行
Phase 12 (清理/文档/可选 API) ← 独立；ML-via-API 受 10 门控
```

- 6、7、8 可并行启动（互不依赖）。
- 9、10 都必须在 8 之后；10b 还在 6 之后。
- 11 在 6 之后启动并持续运行。
- 12 随时可做，其中 ML-via-API 等 10 的结论。

---

## 4. 风险与诚实保留

1. **资金基数默认 ¥1,000,000 改变账本默认行为**（不再写 NULL pnl）。这是有意的产品
   默认；设 0 可回退。基数是名义模拟值，不代表真实账户资金。
2. **max_positions 5→3 是对齐参考行、不是"证明更优"。** walk-forward 0/4 折选 p3 的
   事实必须在页面/CHANGELOG 里如实说明；CPCV 只陈述分布。
3. **净化 CV 仍全在 2016-2023 内**，共享那个时代的行情特征；不能外推到 2024+。
4. **ML 复赛很可能再次不敌 RULE。** Phase D 已失败过一次；"仍不敌、保持 inactive"是
   合格结论。
5. **前向监控慢。** 攒够有统计意义的调仓次数需数月；Phase 11 的价值是纪律与告警。
6. **指纹变更代价。** Phase 7 的列进入 `to_parameters()` 会让旧 PAUSED 行从头重跑。
7. **无浏览器验证缺口**贯穿所有涉及页面的 phase；要么安排人工过页面，要么显式声明限制。

---

## 5. 验证基线

```
DJANGO_READ_DOT_ENV_FILE=True .venv/bin/python manage.py check
DJANGO_READ_DOT_ENV_FILE=True .venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_READ_DOT_ENV_FILE=True .venv/bin/python manage.py test --noinput
DJANGO_READ_DOT_ENV_FILE=True .venv/bin/python manage.py export_documentation_facts --only commands,celery,env
DJANGO_READ_DOT_ENV_FILE=True .venv/bin/python manage.py export_documentation_facts --check
cd frontend && npm test && npx tsc --noEmit && node scripts/check-static-baseline.mjs lint && node scripts/check-static-baseline.mjs build
```

分期额外验证：

- **Phase 6**：`ETF_SIM_CAPITAL_BASE=0` 时账本逐位不变；默认基数下新信号整手、次日
  人民币 `pnl` 非空；carry-forward 保留原份额。
- **Phase 7**：`--queue` 接受 Kelly/现金收益旋钮；默认 0 时逐位一致。
- **Phase 8**：训练日期与 held-out 块之间至少隔 embargo；`--end` 触达 2024 被拒；
  分布表落档。
- **Phase 9**：五处默认一致为 3；部署方 settings 覆盖优先。
- **Phase 10**：影子账本与生产账本隔离；没有 active ML 时任务显式失败。
- **Phase 11**：报告无写路径；LIVE_DRIFT 只在 breach 且样本足够时写入。
- **Phase 12**：`?dedup_track_index=true` 只返回生产规则行。

---

## 6. 关键文件

- `apps/etf_rotation/signals.py`（`_sized_shares`、`persist_signal`）
- `apps/etf_rotation/models.py`（Kelly/现金收益列、`SignalType.SHADOW`、默认 3）
- `apps/etf_rotation/backtest_engine.py`（`max_positions` 读 settings、`position_fees_paid` 注释）
- `apps/etf_rotation/walk_forward.py`（`combinatorial_purged_folds`、`audit_combinatorial_folds`）
- `apps/etf_rotation/management/commands/run_etf_rotation_backtest.py`（`--cpcv-*`）
- `apps/etf_rotation/management/commands/report_etf_rotation_live_track.py`（只读前向报告）
- `apps/etf_rotation/live_track.py`（`build_live_track`、`dispatch_drift_alert`）
- `apps/etf_rotation/tasks.py`（影子信号、月度前向报告）
- `apps/etf_rotation/views.py`（SHADOW 隔离、`dedup_track_index` 过滤、`_backtest_config_for`）
- `apps/analytics/models.py` / `tasks.py`（`LIVE_DRIFT` 条件、通知渠道）
- `config/settings/base.py`（`ETF_SIM_CAPITAL_BASE`、`ETF_ML_SHADOW_MODEL`、`ETF_LIVE_*`、beat/routes）

---

## 7. 实施记录（2026-10-24）

### 7.1 Phase 6：资金基数与份额定标

- `ETF_SIM_CAPITAL_BASE` 默认 **1,000,000**；`signals._sized_shares` 整手向下取整，
  `persist_signal` 只给**新**持仓写份额，carry-forward 保留原份额。
- 测试（4 条）：整手取整且不超目标名义、有份额后账本能算人民币 pnl、基数为 0 时
  `shares=NULL`、carry-forward 在不同基数下保留原份额。
- **待操作员执行**：下一次实时信号生成会写入份额；在此之前账本仍显示 `--`，属预期。

### 7.2 Phase 7：Kelly/现金收益列

- `ETFRotationBacktest` 新增 5 列（migration `0009`），`_QUEUE_KNOB_FIELDS`、
  `_backtest_config_for`、任务、序列化器同步；`--queue` 接受这些旋钮。
- 默认值 `0/1.0/0/60/0` 与旧引擎逐位一致；指纹改变，旧 PAUSED 行从头重跑。
- 测试（3 条）：列往返进配置、默认值复现旧引擎、单跑 override 全部有可排序列。

### 7.3 Phase 8：CPCV 框架

- `walk_forward.combinatorial_purged_folds`：块切分用 `divmod` 均摊（`ceil` 会静默少块），
  purge 用与 `expanding_folds` 相同的双约定，embargo 按 session 计；每块被 hold out
  `C(n-1, k-1)` 次。
- `audit_combinatorial_folds` 重新推导训练集并核对计数与日期序列。
- CLI `--cpcv-blocks/--cpcv-test-blocks/--cpcv-embargo`；拒绝 2024+、`--grid`、`--queue`、
  `--backtest-id`、`--name`。ML 臂在 10a 中放开，但必须同时钉住 `--model-version`。
- 测试（9 条）：组合数、训练/验证不重叠、embargo 与 purge 计数、审计拒绝篡改、无短尾块、
  面板过短拒绝、CLI 五类护栏、块切片指标、分布统计。
- **后续已执行**：row 256 的真实数据分布见 §7.9（六块、中位 Sharpe 0.1620）；
  p3 vs p5 见 §7.10；ML 臂见 §7.11。

### 7.4 Phase 9：max_positions 5→3

- 五处对齐：`ETF_MAX_POSITIONS`、`DEFAULT_MAX_POSITIONS`、`RotationConfig`、
  `BacktestConfig`（改读 settings）、两个模型列默认值；`train_lstm` 的 Top-N fallback 同步。
  migration `0010` 只改列默认值，不改任何已有行。
- CLI 回显与默认命名由 5 改为 3。
- 测试（2 条）：五处同源为 3；`override_settings(ETF_MAX_POSITIONS=5)` 时部署方覆盖优先。
- **待操作员执行**：重跑一次实时信号确认 p3 落地；历史行保持原值。

### 7.5 Phase 10：ML 复赛机制

- `SignalType.SHADOW`（migration `0011`）+ `ETF_ML_SHADOW_MODEL`（默认空）+
  19:50 beat 任务 + `etf-rotation` 路由。
- API 隔离：`/etf/signals/`、`/etf/signals/latest/` 默认排除 SHADOW；
  `/etf/positions/` 默认排除影子账本的持仓（同时匹配 `signal.signal_type` 与
  `metadata.signal_type`），`?signal_type=SHADOW` 才可见。账本按 `signal_type`
  分组结算，所以影子信号不会关闭生产账本的持仓（a shadow signal cannot close a
  production leg）。
- 任务 fail-closed：`ETF_ML_SHADOW_MODEL` 为空时 no-op；配置为 ML 但无 active 工件时
  由信号生成器写 `REFUSED/model_registry_missing` 并抛错。
- 测试（5 条）：空配置 no-op、任务按名请求 SHADOW、latest 默认隐藏、positions 默认隔离、
  影子信号不会关闭生产账本的持仓
  （`test_a_shadow_signal_cannot_close_a_production_leg`）。
- **门控未开**：10b 的前向证据需要数月才能累积；三个 `ETF_*` 仍 `is_active=False`，
  在通过完整门控前不激活。这正是计划预期的状态，不是遗漏。
- **10a 的入口已建，LSTM 可裁决且未通过**：`load_predictor_for_version`（两个 trainer）
  + CLI `--model-version` 使未激活工件可被评分，rank IC 也已接入；LSTM 在带内样本优势下
  仍输掉 Sharpe 与一致性两条（§7.11）。LightGBM 因训练窗口覆盖全部被测块而不可裁决。
  剩余缺口是按折重训，已记入 BACKLOG。

### 7.6 Phase 11：前向监控

- `live_track.build_live_track` 按 `signal_type` 分书汇总；drift 只对**声明的**参考行
  （`ETF_LIVE_REFERENCE_BACKTEST_ID`）计算，没有参考时明确 `measured=False`。
- `report_etf_rotation_live_track` 命令默认只读；`--alert` 才写 `LIVE_DRIFT` 事件。
  月度 beat（每月 1 日 21:00）是唯一传 `--alert` 的调用方。
- `AlertRule.ConditionType.LIVE_DRIFT`（asset-less，migration `analytics/0009`）；
  `check_alert_rules` 显式跳过该类型，避免 5 分钟循环把资产当条件。
- 通知渠道派生抽成公开的 `analytics.tasks.alert_notification_channels`，feed-health 与
  drift 共用一份。
- 测试（6 条）：无参考不判 drift、双容差 breach、样本不足不判定、报告无写路径、
  alert 复用单条规则并逐次写事件、价格循环跳过 LIVE_DRIFT。
- **冻结纪律**：自账本启动日起 6 个月内不得据其结果改任何默认参数（§1.2）。

### 7.7 Phase 12：清理与文档

- `/etf/backtests/` 支持 `?dedup_track_index=true|false`；测试（1 条）钉住默认不过滤、
  过滤后只返回生产规则行。
- `position_fees_paid` 的断代说明写进 closed-trade 记录处。
- scale 模式真实窗口表：本轮不补，默认保持 `0.0/reject/0.0`（理由见 §2 Phase 12）。
- 防御默认值：核对确认 settings 是唯一源，剩余字面量只是 fallback；不引入
  `rotation_engine` 到 `models.py`（迁移会因此导入 pandas）。
- Phase 5 ML-via-API：不建，门控未通过。

### 7.8 验证

- 全量后端 **650 → 685**，`OK`；`manage.py check` 无问题；`makemigrations --check` 干净。
- `export_documentation_facts --only commands,celery,env` 已重写三张表，`--check` 通过。
- 前端 **77** 不变；`npm test`、`tsc --noEmit`、lint/build 静态基线全部通过。
- 新迁移：`etf_rotation/0009`（Kelly/现金收益列）、`0010`（默认 3）、`0011`（SHADOW）、
  `analytics/0009`（LIVE_DRIFT 条件）。前三个只加列/改默认/加枚举值；`analytics/0009`
  只改 choices，均无数据迁移。

### 7.9 CPCV：row 256 的跨块分布（2026-10-25）

配置固定为 `BIWEEKLY / p3 / mom120 / 绝对动量关 / equal`，请求窗口
`2016-01-29..2023-12-31`，面板实际到 `2023-12-29`（12-31 是周日）。6 块取 2、purge 1 session、
embargo 5，15 个切分全部审计通过；每个块被 hold out 5 次。**一次连续运行 12.0 秒，不写行、
不激活模型。**

| 块 | 区间 | 交易 session | 调仓 | 收益 | maxDD | Sharpe |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2016-01-29..2017-05-26 | 321 | 36 | +6.54% | 6.84% | 0.6643 |
| 2 | 2017-05-31..2018-09-13 | 321 | 34 | +1.50% | 17.96% | 0.1620 |
| 3 | 2018-09-14..2020-01-10 | 321 | 37 | +19.58% | 19.23% | 0.8053 |
| 4 | 2020-01-13..2021-05-13 | 321 | 35 | +62.53% | 29.95% | 1.2789 |
| 5 | 2021-05-14..2022-09-02 | 321 | 36 | −5.85% | 26.25% | −0.0741 |
| 6 | 2022-09-05..2023-12-28 | 320 | 35 | +0.98% | 11.97% | 0.1300 |

| 指标 | min | q1 | 中位数 | q3 | max | 正收益块 |
| --- | --- | --- | --- | --- | --- | --- |
| Sharpe | −0.0741 | 0.1300 | **0.1620** | 0.8053 | 1.2789 | 83.33% |
| 收益 | −5.85% | +0.98% | +1.50% | +19.58% | +62.53% | 83.33% |
| maxDD | 6.84% | — | 17.96% | — | 29.95% | — |

**读法（只陈述，不判定）。** 全窗口 Sharpe 0.5357 不是"整段都有效"：六块的中位数只有
0.1620，收益几乎全部来自块 4（2020–2021），块 5 为负。这与 §7.10 的三等分曲线和 §7.12 的
四折 walk-forward 同向——参考配置的表现在时间上高度不均匀。**块不是独立样本**：它们是同一条
连续曲线的切片，资金路径相连，分布读作"分段稳健性"而不是"六个独立试验"。全部块都在
2016–2023 内，不能外推到 2024+。**没有薄块**：每块调仓 34–37 次，高于 §7.12 的 20 次
下限，所以读数至少不是"几次交易凑出来的"；但这不改变"非独立样本"那条边界。

### 7.10 CPCV：p3 vs p5（2026-10-25）

两臂除 `--max-positions` 外逐字段相同，同窗口、同 15 个切分、同 embargo。

| 臂 | Sharpe 中位数 | 正收益块占比 | maxDD 中位数 | Sharpe min / max |
| --- | --- | --- | --- | --- |
| p3 | 0.1620 | **83.33%** | 17.96% | −0.0741 / 1.2789 |
| p5 | **0.2725** | 66.67% | **14.54%** | −0.3772 / 1.1874 |

**预登记裁决：默认值保持 3。** 判据依次为 Sharpe 中位数 → 正收益块占比 → maxDD 中位数，
要求 p5 三项全胜才改回 5。p5 的 Sharpe 更高、回撤更浅，但把两个块做成负收益
（2021–2022 −11.77%、2022–2023 −8.74%），正收益块占比从 83.33% 掉到 66.67%。**三项未全胜，
因此不改默认值。** 这条记录的价值不在"p3 更好"，而在"更高的中位 Sharpe 是用一致性换来的"；
单看中位数会把 p5 误读成升级。

### 7.11 Phase 10-10a：ML 复赛（CV leg）

**入口已建。** `train_lightgbm.load_predictor_for_version` /
`train_lstm.load_predictor_for_version` 按版本号加载 READY 工件，**不看 `is_active`**；
`--cpcv-blocks --model LIGHTGBM --model-version <v>` 把该 predictor 传进同一个旋转配置。
缺版本号、RULE 带版本号、单跑带版本号都会被拒；加载前后 active 集合逐位不变（有测试）。

**先修掉一个让 LSTM 测量失效的接缝。** `rotation_engine._call_predictor` 只传
`['ts_code', *features]`，把 `date` 一起丢掉了；LSTM 的评分契约需要每行的 as-of 日期去取
尾部窗口，没有它就会退回“最新 eligible 快照”——在历史回测里就是未来函数（LSTM 自己会
打 warning，但 warning 不是修复）。现在 `date` 随帧一起传（LightGBM 按训练列 reindex，
忽略多余键，数值逐位不变）；测试钉住“有 date 时传递、无 date 时仍可评分”。修复后 LSTM 臂
的 82 秒运行中 **0 条** "no date column" 告警。

**LightGBM 臂（28 秒）：**

| 块 | 收益 | maxDD | Sharpe | rank IC |
| --- | --- | --- | --- | --- |
| 1 | +18.38% | 4.19% | 1.8774 | 0.1941 |
| 2 | +23.79% | 7.56% | 1.5077 | 0.1045 |
| 3 | +132.60% | 7.64% | 3.2659 | 0.0802 |
| 4 | +222.92% | 12.99% | 3.3147 | 0.1179 |
| 5 | +189.09% | 19.86% | 3.1412 | 0.0297 |
| 6 | +85.25% | 7.42% | 2.4839 | 0.0795 |

**这些数字不能用来裁决 R3。** 钉住的工件
`etf-lgb-20d-lambdarank-2023-11-30-oos23` 的训练窗口是 **2016-01-29..2023-11-30**，
覆盖了块 1–5 的全部日期和块 6 的绝大部分——模型是在自己的训练集上被评分的。Sharpe 1.5–3.3
和 IC 最高 0.194 正是这种内样本拟合的样子，不是样本外能力。**LightGBM 臂：不可裁决。**

**LSTM 臂（82 秒）：**

| 块 | 收益 | maxDD | Sharpe | rank IC |
| --- | --- | --- | --- | --- |
| 1 | −5.67% | 9.47% | −0.6700 | 0.0485 |
| 2 | −15.39% | 25.16% | −0.7456 | 0.0692 |
| 3 | +20.03% | 11.74% | 0.8844 | 0.0126 |
| 4 | +53.54% | 15.93% | 1.2639 | 0.0533 |
| 5 | +53.35% | 31.08% | 1.2305 | 0.0214 |
| 6 | −0.38% | 28.82% | 0.1238 | 0.0109 |

分布：Sharpe 中位 **0.1238**（min −0.7456 / max 1.2639）、正收益块 **66.67%**、maxDD 中位
**15.93%**；IC 中位 0.0214、六块全正。

**训练窗口的更正（关键）。** serving artifact 是最后一折的模型（`metrics.json` 的 fold 5），
实际训练到 **2022-10-21**，而不是注册表 `training_window` 写的 2023-12-31。所以块 1–5 是
内样本，**块 6（2022-09-05..2023-12-28）基本是真正的样本外**。块 6 上 LSTM 为
−0.38% / 28.82% / 0.1238，RULE 为 +0.98% / 11.97% / 0.1300：Sharpe 几乎持平，回撤差
16.85pp。

**R3 裁决（LSTM 可裁决，LightGBM 不可）：**

| 臂 | Sharpe 中位 | 正收益块 | maxDD 中位 | 判据 |
| --- | --- | --- | --- | --- |
| RULE | 0.1620 | 83.33% | 17.96% | 基准 |
| LSTM | 0.1238 | 66.67% | 15.93% | Sharpe 负、正收益块负、maxDD 胜 → **三项未全胜** |
| LightGBM | 2.4839 | 100% | 7.56% | 全部内样本 → **不可裁决** |

**CV leg未通过。** LSTM 在带内样本优势的情况下仍输掉 Sharpe 与一致性两条；唯一胜出的
maxDD 不足以构成“CV leg通过”。**没有任何 `ModelVersion` 被激活。**

完整闸门的另一半是 10b：影子账本需要累积至少 20 次调仓（数月），本轮不可能满足；
所以即使 CV leg通过也不会激活，何况它没有通过。

**关于 Phase D 的旧 LSTM 数字。** row 209 也走 `_call_predictor`，丢掉了 `date`；LSTM 因此对
每个历史 session 都按“最新 eligible 快照”取尾部窗口，而它的分数只依赖那个快照日——
结果是**每个 session 拿到同一份分数向量，整段回测实际持有一个静态组合**。这解释了
row 209 的 beta 1.1038 与 maxDD 0.4567：那不是模型的风险特征，是“两年半不换仓”的产物。
因此它的 alpha 0.0888 不应再被当作有效 LSTM 测量引用。用修复后的代码重跑需要再查一次
已烧掉的 holdout，纪律不允许；所以这里只记录“旧数字失效”，不补新数字。

**四臂的完整对比、有效性判定与注册表 holdout 记录的交叉印证，见
[`docs/reports/etf-rotation-cpcv-model-comparison-2026-10-25.md`](../reports/etf-rotation-cpcv-model-comparison-2026-10-25.md)。**

**要做得更好，缺的是按折重训。** `train_walk_forward` 目前只接受 `expanding_folds` 的 Fold；
CPCV 的每个训练集是不连续的日期集合，需要把它接进 `train_walk_forward`（或另写一个按
`CombinatorialFold.train_dates` 取行的训练入口），让每个块用未见过它的模型评分。在那之前，
LightGBM 臂的分布只能当上限读。已记入 BACKLOG。

**边界确认（跑后核对）：** `ETFRotationBacktest` 仍 354 行、信号仍 3 行、active ETF 模型集合
仍为空、最新 holdout 行的 `created_at` 仍为 2026-10-22 20:48:11 UTC——没有写行、没有激活、
没有查询 2024+。

### 7.12 缺口收口验证（2026-10-25）

- 廉价缺口：前端 form 默认 5→3（含 vitest）、份额合计与"无份额不伪造 pnl"两条测试、
  `--queue` sized book 端到端测试、CPCV 不写库/不激活测试、`position_fees_paid` 的
  serializer + api.md 断代说明。
- 2026-10-25 追加：`_call_predictor` 传递 as-of 日期（LSTM 历史回测未来函数修复）
  与每块调仓计数/薄块护栏，均含测试。
- 后端 **685 → 704**；后端下限抬到 **704**；前端 **77 → 79**，下限抬到 79。
- `manage.py check`、`makemigrations --check`、`export_documentation_facts --check` 均通过。
- 仍无人工浏览器点击；UI 结论止于 vitest、类型检查与静态基线。

---

## 8. holdout 释放记录（2026-10-25）

**决策**：`2024-01-01..2026-10-20` 这个锁定 holdout 正式释放进训练池，同时封存"2026-10-25 起"的前向窗口作为此后唯一诚实的样本外来源。

**为什么释放**：该窗口已被读过两次——§7.7 的 weighting 裁决，以及 Phase D 的单发闸门。被看过的数据不可能再当干净测试集；把它挡在训练池外买不到任何东西，只是丢掉最近 2.7 年、也正是最该让模型知道的那段行情。

**代价（必须同时接受）**：从此不可能再做出任何"干净的历史样本外"声明。已有的 §7.7 与 Phase D 记录仍是历史事实，但不能再被当作可复现的样本外证据。唯一诚实的前向证据是实时账本。

**四个条件**：

1. **记录在案**：本节的释放日期与释放窗口即记录；任何工件都能据此标注"含释放后数据"。
2. **封存前向窗口**：起点 `2026-10-25`；起点起 6 个月内不得据账本结果改任何默认参数。
3. **不重裁旧决定**：不用释放后的数据重跑 Phase D 式闸门并当作新证据，也不重做 §7.7 的 weighting 裁决。
4. **工件独立标签**：释放后重训的工件带 `post-release` 标签，不与释放前工件混在同一张对比表里。

**边界**：释放**不会改变** row 256 的不稳定结论。那个发现来自调优窗口自身（walk-forward + CPCV），与 holdout 无关。

**代码影响**（本版落地）：

- `run_etf_rotation_backtest` 的 `LOCKED_HOLDOUT_START` → `RELEASED_HOLDOUT_START` + `HOLDOUT_RELEASED_ON`；2024+ 窗口从"拒绝"改为"标注"，reading rule 行重复标注。
- `sweep_etf_rotation_lstm` 同构改造；`--allow-holdout-overlap` 旗标退役（不再需要逃生口）。`TUNING_WINDOW_END` 保留为默认值，使不带 `--end` 的调用逐位复现 2026-10 那次 sweep。
- `train_etf_rotation_model --no-activate`：该命令的 trainer 默认 `activate=True`，释放后第一次重训会静默接管 serving——这是闸门明确禁止的状态。
- `scripts/_check_migrations.sh`：迁移未 apply 时启动即失败（2026-10-25 的告警根因：测试库由迁移新建，永远一致，dev 库落后四个迁移，前端只显示"加载 ETF 轮动数据失败"）。

**释放后的工件**（均 `is_active=False`，尚无有效样本外证据，仅作 serving 候选）：

| 模型 | 版本 | 面板 | pooled rank IC | 正 IC 折 |
| :--- | :--- | :--- | ---: | ---: |
| LightGBM | `etf-lgb-20d-lambdarank-2026-09-17-post-release` | 2016-01-29..2026-10-23 | 0.0648 | 5/6 |
| LSTM | `etf-lstm-2026-09-17-post-release` | 2016-03-03..2026-10-23 | 0.0318 | 4/6 |

这两个折内 IC 是**面板内 walk-forward**，其验证块包含 2024-2026——属于调优窗口读数，不是样本外证据。

**本轮已执行的操作员动作（2026-10-25）**：

- `generate_etf_rotation_signal` 已按最新 session（2026-10-23）重跑：写入 1 条 MONTHLY 信号 + 3 条持仓（159887.SZ 373,000 股 / 510230.SH 287,600 股 / 515300.SH 75,800 股，均整手），权重和 1.0，`max_positions=3`，陈旧告警随之清除。
- `ETF_LIVE_REFERENCE_BACKTEST_ID=256` 已写入 `.env`：`report_etf_rotation_live_track` 现在能对照 row 256（233 笔已平仓，胜率 45.06%，均值 +1.13%）。实时簿 0 笔已平仓，drift 读数仍为 `n/a`（样本不足），符合设计。
- **影子账本仍未开启**：按决策等按折重训结论。§11.5/§11.6 的两个否决臂结论相反，所以现在开影子只会得到一个不可解释的对照组。

---

## 9. Phase 17 闸门（预登记，2026-10-25）

**唯一允许改生产的选择规则：**

1. **CV leg**：某个 ML 臂在按折重训复赛里通过 R3'（Sharpe 中位数 > RULE、正收益块 ≥ RULE、maxDD 中位数 ≤ RULE + 2pp）。截至本轮，只有"RULE 选股 + LightGBM 否决"通过，且 LSTM 否决的相反结果说明它未获独立复现（报告 §11.5-§11.6）。
2. **forward leg**：该臂以影子账本形式累积 ≥20 次调仓，且胜率与均值收益都不劣于 row 256 的对照读数（`report_etf_rotation_live_track --alert`）。
3. 两条 leg**同时**成立，才允许一次单发激活：写入新 `ModelVersion`、切换 `ETF_ML_SHADOW_MODEL`、保留一条回滚路径（旧的 RULE 路径不变，激活是加一个可随时撤掉的选择器）。
4. 通过后仍先当一轮影子（再 ≥20 次调仓）才动生产。

**闸门未满足时的唯一动作是记录"再次不敌"**，不是调参重试。参数一旦为了过闸而调整，CV leg就不再是预登记的检验。

**当前状态**：无任何 `ETF_*` 模型被激活；`max_positions=3`；生产选择器仍是 RULE；影子账本未开启。
