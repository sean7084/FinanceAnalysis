# Universe campaign evidence log (CSI500 → CSI1000)

> 权威设计：`~/.qoder-cn/plans/hushed-gorge-moorhen.md`。**两者冲突时，以计划文件为准。**
> 本文件只放证据（日期 + 测量值 + 命令输出摘要），append-only；更正写进计划正文。

## 2026-11-20 — W0-1 复测 + G 门评估

命令：`bash tmp/w0_verify.sh`（WSL，repo 6113710 = origin/main，工作区干净）。

- CI 地板：`.github/workflows/ci.yml:263 MIN_TEST_COUNT: "1316"`；frontend `:486 "100"`。
- 迁移：`showmigrations analytics markets prediction backtest` → **ALL APPLIED**（无 `[ ]`）。
- Active 股票 fleet（`LightGBMModelArtifact.objects.filter(is_active=True)`，metrics_json）：
  - 3d `lgb-3d-2026-11-09` purged_holdout rank_ic **0.004109426439005** / t **0.36384272515990107** / icir **0.023011434431758772**
  - 7d `lgb-7d-2026-11-09` purged_holdout rank_ic **0.023425059800029336** / t **1.9679510583777697** / icir **0.12446415336425479**
  - 30d `lgb-30d-2026-11-09` purged_holdout rank_ic **0.055947430422276546** / t **3.9600240474305006** / icir **0.2504539115783808**
  - ⇒ 计划 v1 的 "icir≈0.23" 更正为 **0.250**；7d t=1.968 边界噪声（非反向）；30d icir 0.250 < 0.3 仍不合格一条。
- 足迹：`pg_database_size` = **59 GB**（= 54.9 GiB ≤ 66.23 目标；CHANGELOG campaign B 记录一致）。
- Stack：`verify_local_stack.sh` → PostgreSQL ready（192.168.31.8:5432/finance_analysis）、Redis broker/cache ready、deps ok、0 system check issues、migrations all applied。
- 进程：runserver ×2 + celery workers（ops/backtest/train-lightgbm,train-lstm/etf-rotation）+ **beat（DatabaseScheduler）在跑**（10:14 起）；tmux 无会话。
- GitHub：无开放 PR；开放 issues 含审计新立 **#183**（yc_cb P1）/ **#184**（env.md inert）/ **#185**（BACKLOG 索引矛盾）/ **#186**（DQ docstring）/ **#187**（sentiment 残余）/ **#189**（ETF 9-cell grid run 跟踪）。
- 存储 campaign：PR **#188** 已合并（b16a1ed）——param_key 唯一性 + SMA-60 + DQ variant key；CHANGELOG 记录 remaining_null=0、dup 0 groups、0016–0018 applied、VACUUM FULL 48 min、132.46→51.2 GiB（B 后 54.9）、速度偏差诚实记录、全套件 1316 OK、地板 1306→1316。
- G 门：G1–G6 ✅（上条证据）；G7 WSL clone ✅ / Windows clone 待数据运行前核；G8 stack ✅ / 全套件后台跑（`tmp/w0_baseline_suite.log`）。

待补：Windows clone 状态（数据运行前）。

## 2026-11-20 — W0-2 BACKLOG 卫生 PR

- 基线全套件：**Ran 1316 tests in 119.733s, OK**（G8 ✅）。
- gates：reference 0 failures + self-test 双控通过；verbatim 0 paraphrases / 1239 lines + probe（original True / perturbed False）。
- commit eb1c456 → **PR #191**（docs/backlog-index-issue-state）：closed 行移除（M1 #140/#141/#143、M3 #151/#152/#153/#155/#156/#161、Owner #149）、过期断言修正、#183–#187/#189 补录、campaign Current state 行 + 本日志。archive 未动。

## 2026-11-20 — W0-3 立项（去重后）

issues：**#192** STK-04 / **#193** STK-05 / **#194** STK-06 / **#195** STK-03 残余 / **#196** UNI-01..04 /
**#197** UNI-05+STK-11+板块默认-disable 开关 / **#198** STK-12 ST 层 / **#199** 股票 walk-forward / **#200** UNI-V2A（2027-07-01）。
评论：#146（A3 整体覆盖）/ #145（B1 写入侧）/ #144（前提已假，余项 A6；30d icir 0.250<0.3）。

## 2026-11-20 — W0-4 STK-03 审计（只读）

- MarketContext.macro_phase 源分布：**RECOVERY 178 / OVERHEAT 20 / STAGFLATION 9 / RECESSION 1**（4 distinct；RECESSION 2025-04；OVERHEAT 至 2023-02，STAGFLATION 至 2022-07）。
  ⚠️ 这是源分布，**训练面板是否常数取决于 join 路径**（tasks_lightgbm 默认填充 1.0 的疑点）——代码核实中，结论补记。
- MacroSnapshot：202 行，cn10y/cn3y 各 5 NULL，both_nonnull 197；slope mean 0.40401 / **variance 0.04679849** / min 0.038 / max 1.2229；latest yield **2026-06-01**。
  yield_curve 历史有方差但 **#183 权限持续丢失 ⇒ 按预决必删**（live 不可算，train/serve 分歧）。
- northbound_flow ≡ 0.5 by construction（:1009/:1752/:1772），交互项 = 0.5×mom_5d 精确线性冗余 ⇒ 无条件删。

## 2026-11-20 — W0-6 namechange 探针（2 次只读调用）

- 按个股全史：000001.SZ 1 行（2012 更名平安银行，reason 其他）；字段 ts_code/name/start_date/end_date/ann_date/change_reason ✓。
- 按日期窗（20230420..20230430）：**90 行**；ST/*ST/退市整理期/从ST变为*ST reason 齐全；**ann_date > start_date 行数 = 0**（前视安全再确认）。
- **B2 同步设计输入**：日期窗参数按 **ann_date** 过滤（样本行 ann_date 在窗内、start_date 在其后）⇒ 同步按 ann_date 分窗拉取，as-of 解析用 start_date；
  市场-wide 稀疏（~8 行/日）⇒ 12 月窗 ≈3k 行 < 7000，全史 2009+ ≈17-34 次调用（远少于逐股 ~1900 次）；满页 bisect 仍作安全网。

## 2026-11-20 — W0-2 合并 + W0-4b 裁决 + W0-5 补测

- **PR #191 合并**（584e7b9，CI 双绿，预授权 --admin）。本地 main 曾滞后致 switch 拒绝（日志增量在工作区）；`git branch -f main origin/main` 后切换，增量带入下一 PR。
- **W0-4b（训练精确过滤 context_key=current, is_active=True）**：201 active 月度历史行；post-2016-06 **124 行 / 4 phases**
  （OVERHEAT 2017-09/2020-11/2023-02；STAGFLATION 2019-05..10/2020-02/2022-07；RECESSION 2025-04）⇒ **macro_phase 非常数，保留**（含两交互项），D9 接 release_lag。
  W0-4 无过滤查询（178/20/9/1）是错误总体，以此条为准。context 历史缺 2026-07/08（#171 相关），merge_asof 从 2026-06 填充——记录，非阻塞。
- **A5 终版特征决定：36 − 3 = 33**（删 northbound_flow / northbound_flow_x_mom_5d / yield_curve；保留 macro_phase+2 交互 / pmi×2）。已评论 #195。
- **W0-5 UNI-06**（~27 次 TuShare 调用，tmp/w05_uni06.py）：两指数 2017-01..2025-12 **missing months: NONE**；
  CSI1000 6 月窗恒 6000 行（月快照×1000，无截断）；CSI500 12 月窗无满页。表：tmp/uni06_board_shares.md。
  **2019-07/08/09 两指数科创板成分均 0**（开板 ≠ 入指）⇒ D7 断点 = 首次科创成分快照（定位中），不是 2019-07-22。
  CSI500 不可交易权重 2019 年仅 ~7.3%（36 只创业板）→ 2026-10 34.50%，漂移证实。
- **待 owner**：KB spec §2.1 追加 UNI-06 表 + D7 日期更正（2019-07→2020-12）[PW]。
- **D7 断点定位**：两指数首次科创成分快照均 **20201231**（CSI500 5 只/1.24%；CSI1000 19 只/2.12%）；2019-07..2020-11 恒 0。
  计划正文 D7 已更正。#195 评论已发（特征决定 33 列）。

## 2026-11-20 — A1（STK-04，#192）实施

- 分支 feat/backtest-date-contract-enforcement。双守卫：serializer.validate() + run_backtest 任务入口（命令直建 run 绕过 serializer ⇒ 任务守卫是承重墙）；
  override = parameters.allow_pre_contract_history（布尔校验）+ 四命令 --allow-pre-contract-history（fail-fast，reference suite 透传，rerun 亦继承源 run）。
- 验证：新测试 **9 OK**；apps.backtest 全套件 **96 OK**（无既有 fixture 破坏）；apps.core.tests_date_contract **24 OK**；ruff 通过。
- mutation 证明：删除任务守卫 ⇒ 拒绝测试须挂（控制输出 tmp/a1_mutation.log，rc 非零为通过）。
- **A1 落地**：mutation rc=1（FAILED failures=1）✓ 还原后 9 OK ✓；commands.md +5/−1；sheets --check / makemigrations --check / check / ruff 全绿；
  commit 82c7067 → **PR #201 合并 0fdaef0**（CI 双绿，预授权 --admin）。

## 2026-11-20 — A2（STK-06，#194）实施

- 分支 feat/artifact-activation-guard（从 main，与 A1 无文件重叠）。LightGBMModelArtifact.save() 镜像 ModelVersion.save（ARCHIVED+active 拒；active 过 validate_activation_metrics(metrics_json)）；activation.py docstring 同步。
- 绕过审计：artifact 唯一写路径 = 训练 update_or_create(:2363，走 save())；.update(is_active=False)(:2339) 为去激活方向，安全。
- 验证：apps.prediction 全套件 **163 OK**（无 fixture 冲突）；新测试 **9 OK**；mutation rc=1 ✓ 还原后绿 ✓；gates 全绿；commit 66eaa6c（rebase 到 0fdaef0 后 PR 待开）。

## 2026-11-20 — C1（UNI-01/02，#196）实施

- 分支 feat/csi1000-sync-acceptance（从 main）。INDEX_CODE_SPECS += 000852.SH（CSI1000）；DEFAULT_* 不动（切换是裁决门控的后续卡）。
- _fetch_index_weight_frames：满页（7000）⇒ 二分重取；单日满页 ⇒ ValueError（比 ETF note 更严，spec 要求报错不丢月）；保留 _call_tushare_with_retry。
  窗日按指数 {000905:10, 000852:5}；"6000 cap" 注释更正。
- verify_index_membership_continuity（只读）：按快照行数带（950–1050 / 475–525）+ 月度存在（地板 2014-10 / 2009-12）；违规非零退出。
- 测试 9 个 + mutation + 落地见 PR。
- 修正：首版 BY_INDEX 含 000905:10 覆盖了可 patch 的模块常量 ⇒ 两个旧测试挂（chunks/prefill 断言旧窗口机制）；
  改为仅 000852.SH:5 覆写、000905 回落模块常量（保持测试/运维可 patch），旧测试意图不变、无需改断言。markets 套件复测 **128 OK**。

## 2026-11-20 — A6（#144 余项）实施

- 分支 feat/holdout-provenance。models.md active-artifact 表 +5 列（Eval mode/Rank IC/IC t-stat/ICIR/Holdout start），ModelVersion 表 +4 列；
  训练调用点 ModelVersion.metrics 补 holdout_start/purge_sessions（artifact 已有）。
- 测试 4 个（render×3 + register×1）+ mutation + models.md 再生（live registry 只读）；落地见 PR。
- **A6 落地**：PR #204 合并 ef57183（CI 双绿，预授权）。

## 2026-11-20 — A3（STK-07 / #146）实施

- 分支 feat/backtest-fill-consistency（从 main ef57183；A3 代码曾误落 A5 工作区 → patch 保全后迁移，A5 日志增量单独提交 8bd459d）。
- _resolve_missing_value_strategy 共享优先级（disk→DB→legacy）；backtest runtime 携带策略；serial/batched 传策略 + 共享 _resolve_prediction_feature_value。
- native_nan 下缺失列（无 alias）⇒ raise 点名（#146）；legacy 保持零填充。训练 artifact metadata 增 feature_schema{names,dtype,schema_version}。
- 测试 10 个 + 训练 metadata 断言 + 双 mutation；落地见 PR。
- 修复两跑：mutation1 靶测试改 missing-column raise（.get 对存在 NaN 键返回 NaN，NaN 测试不敏感）；_patch.call_args → p.start() 返回 mock；旧 missing-key→NaN 测试替换为三测试新契约（raise/现键 NaN/legacy 零填充）+ LEGACY import。三跑 **281 OK**，双 mutation rc=1/1。
- **A5 落地**：PR #205 合并 520d525（CI 双绿含日志增量 8bd459d，预授权）。
- **A3 落地**：rebase 到 520d525（tasks_lightgbm 自动合并；tests_lightgbm union v3+feature_schema），复测 **288 OK** → **PR #206**（3a3f35d）。

## 2026-11-20 — B1 核心（UNI-05/STK-11，#197）实施

- 分支 feat/universe-policy-board-switch。**B1 拆两 PR**（流程细化）：B1 核心 = 策略模块 + benchmarking 全卡口穿线 + 地板 + 基准代码方案 + 13 测试；B1b = 调用方覆写（训练/回测/命令）+ provenance 写入。
- universe_policy.py：BOARD_TRADING_PERMISSIONS（chinext/star/bse False，O1）；ST_LAYER_ENABLED False（B3 门）；research_full/research_cap；apply_universe_policy 单一过滤（绝不读 listing_status）。
- benchmarking：单日/批量/effective_*/builder/refresh×2 全穿 policy/st_layer/policy_caches；required_pit_index_codes_for_date 按指数地板（000852→2014-10-31）；pit_benchmark_code_for 5+ 序列共存方案。
- 测试 13 + mutation + 全套件；落地见 PR。
- 修复一跑：coverage 测试误用 2009 日期（<2010 合法不要求覆盖）→ 改 2010-01-04 无快照。复跑 **全套件 1367 OK**（默认板块过滤全仓零既有冲突），mutation rc=1。
- **A3 落地**：PR #206 合并 8b29845（CI 双绿，预授权）。
- **B1 核心落地**：rebase 到 8b29845（journal union；脚本 UU 检查先于 add 的小 bug 已手工补 add）→ **PR #207**（e280607）。

## 2026-11-20 — A4 设计细化（未实现）

- 分支 feat/backtest-execution-realism 已切（从 8b29845）。读引擎：price_map(:589) 标量 close、_close_positions_for_date(:2006)、_open_positions_for_date(:2085)、主循环(:2491+)。
- 机制推导已写入计划 A4 卡（并行 detail map / pending_entry+pending_exit 进 runtime_state / session 开头成交处理 / 锁死重试≤3 / is_st 占位待 B2 / legacy 逐字节不变）。
- 待实现（下一上下文窗口按计划卡执行）。B1 #207 CI 绿后合并（预授权）。
- **B1 合并**：#207 CI 双绿 → --admin 合并（main 前进）。CI 判读修正：in-progress conclusion 是空串非 null，jq 须 `if . == null or . == "" then "running"`。
- **A4 part 1（增量，已测未开 PR）**：limit_pct_for is_st kwarg（as-of 覆写，主板 ST 5% / ChiNext/STAR ST 保持板限）；_build_ohlcv_detail_map（open/close/raw_close/adj_factor，独立缓存，raw_open=open×raw_close/close，legacy null→None）+ clear 纳入；serializer execution_model 校验（same_day_close 默认 / next_day_open / 非法拒）。
  测试 apps/backtest/tests_execution.py 8 个 + 既有 price_limits；backtest+price_limits **135 OK**（fixture 补 adj_close 非空）。
- **A4 part 2 待做**：pending_entry/pending_exit 进 runtime_state + session 开头成交处理 + 锁死重试 + 停牌 + legacy 不变测试 + provenance（机制见计划 A4 卡）。
- part 2 序列化核实：position spread ⇒ pending_exit 零改动过 chunk；pending_entries 新键 + Decimal→str；四同步点（load/save/fresh-resume/chunk-save）已定位。part 1 commit eb12ec0。
- **本窗口收尾**：8 PR 合并（#191/#201/#202/#203/#204/#205/#206/#207 → main 32fb40f）；A4 part 1 在分支待 part 2；B1b/B2/B3/C2/地板待做。下一窗口按计划 A4 卡实现 part 2。
- **A4 part 2 实现完成（同窗口续）**：pending_exit（position spread 天然过 chunk）+ pending_entries（runtime_state 新键，Decimal→str）+ _session_fill_lock（raw 价涨跌停/停牌/缺 bar）+ _process_pending_fills（开盘成交、重试≤3、卖不强平）+ 主循环 session 开头处理 + report.execution_model。
  修复三跑：cls.run 遮蔽 TestCase.run 方法 → backtest_run；sessions_since_listing 窗口前首 bar ⇒ 视为已过免费期（保守近似，PR 记录 caveat）；chunked 续跑需循环调用 run_backtest（delay 不 eager）。
  **156 OK**（含 next_day 集成 + cross-chunk final_value 不变）。mutation + 全套件 + 落地见 PR。
- **A4 落地**：mutation rc=1 ✓；backtest 全套件 **194 OK** ✓；gates 绿；commit 34d7662 → rebase 到 32fb40f（CHANGELOG union A4+B1；脚本 union 失败一次 → 手工 resolve_a4_changelog.py）→ **PR #208**（e8259c3，3 commits）。
- **Gate A 全卡完成**（A1✅ A2✅ A3✅ A4⏳CI）。剩余 Wave 1：B1b（调用方覆写+provenance）/ B2（ST 层）/ B3（启用门）/ C2（双 PIT）/ 地板 PR。

## 2026-11-20 — B1b part 1（backtest per-run policy）实施

- 分支 feat/backtest-universe-policy（从 3a86e3e；编辑曾误落 B2 分支 → patch 迁移）。
- parameters.universe_policy/pit_index_codes（serializer 校验）→ candidate_cache 注入（零调用方改动）→ _eligible_backtest_asset_ids（cache key 含 policy+codes）→ report.universe{policy,index_codes,benchmark_code}（#145 读取侧）。
- 测试 5 个；mutation rc=1（内联 heredoc 引号曾致假 rc=0 → 脚本文件版复验）；backtest 全套件 **199 OK**。
- part 2 待做：训练/命令 --universe-policy flags + artifact/ModelVersion provenance 写入侧。PR 待 B2 #209 合并后开。
- **B1b part 1 提交**：4f71fbd（gates 绿：sheets --check / makemigrations / check / ruff）。C2 设计细化已写入计划（日 refresh 维护 legacy full + 默认 ex-board 双系列）。
- **B2 合并**：#209 CI 双绿 → --admin → main 2ffea20（**10 PR 合并**）。
- **B1b part 1 落地**：rebase（CHANGELOG union B1b+B2；journal 自动合并）→ **PR #210**（aef0a24）。
- 下一窗口：C2 实现（设计在计划 C2 卡）→ B1b part 2（训练 flags + provenance）→ 地板 PR（CI 实测 Ran N）→ B3/DR 待 owner。
- 注：本地 main 引用曾滞后（合并 #201/#202 后只 fetch 未 ff），本分支切自旧 main 584e7b9；已 `git branch -f main origin/main`，PR 前 rebase。

## 2026-11-20 — A5（STK-03 残余 + STK-08，#195）实施

- 分支 feat/feature-schema-v3。v3 schema = TRAINING_BASE_FEATURES_V3(30) + 3 交互 = **33**；删 northbound_flow / northbound_flow_x_mom_5d / yield_curve；macro_phase 保留（W0-4b 非常数）。
- **设计细化（记录）**：extractor 保留 legacy keys（northbound/yield_curve）——生产 v2 fleet 按工件 feature_names 取特征，删 extractor 键会断 v2 服务；只从训练选择侧删列。满足计划的 v2 兼容测试。
- STK-08：_prepare_macro_dataframe 按 release_lag 平移（PMI 1d / yield 2d）+ 单日推理同 lag 过滤。v2 工件在月界有 ≤2 天取值偏移（caveat，Wave-5 重训替换）。
- engineered_feature_version v2→v3。测试 7 个 + mutation + 落地见 PR。
- 修复两跑：空 object-dtype date 列 + Timedelta ⇒ TypeError（11 errors）→ 非空守卫；yield_curve 旧测试钉无 lag 行为 → fixture 移早 2 天 + 新增"当日未发布快照不得读"断言；v2 版本号 pin → v3。三跑 **179 OK**。

## 2026-11-20 — 合并时间线（预授权 --admin，CI 双绿）

- #191 W0-2 → 584e7b9；#201 A1 → 0fdaef0；#202 A2 → 6bb995a；#203 C1 → d6acac3；#204 A6 → ef57183。
- A5 → **PR #205**（619ed4c，rebase 到 ef57183；journal union；CHANGELOG/tasks_lightgbm 自动合并）。
- 过程缺陷记录：A6 rebase 脚本曾把含冲突标记的 CHANGELOG 提交（python 报错未阻断 git add）→ 0e74cc5 修复 union；后续脚本已加 marker 检查。
- 本地 main 引用两次滞后（合并后只 fetch）→ 每次合并后 `git branch -f main origin/main`。

## 2026-11-20 — B2（STK-12，#198）实施

- 分支 feat/st-layer-namechange（从 32fb40f）。AssetNameChange+AssetSTInterval（markets/0017）+ st_history（状态机/bisect/ingest gate）+ sync_asset_namechanges（ann_date 窗/bisect/隔离报告）+ reconcile_st_history（双 diff/非零退出/B3 门报告）+ daily 60 天刷新+分歧检查（现有 beat 内，无新 beat 行）+ universe_policy ST lazy-load。
- 测试 24 个（转换矩阵/as-of 边界/批量等价/退市保留/幂等/摄入门/bisect/每日分歧）。mutation + markets 套件 + 落地见 PR。
- A4 合并 #208 → main 3a86e3e（9 PR 合并）；B2 rebase 时 union。
- 修复：helper 插入把 @shared_task 装饰器挤到 sync_recent_namechanges 上 ⇒ sync_daily_a_shares 失注册（core.E002）→ 装饰器移回。复跑 markets **163 OK**，mutation rc=1，gates 绿。
- **B2 落地**：commit ed025cf → rebase（CHANGELOG union）→ **PR #209**（0389864）。剩余 Wave 1：B1b / B3（DR6 门）/ C2 / 地板。
- **B1b part1 合并** #210 → 89e80d0；**C2** → PR #211（81f4195，markets 167 OK，mutation rc=1）。

## 2026-11-20 — B1b part 2 核心（训练穿线 + provenance）

- 分支 feat/training-universe-provenance（从 89e80d0）。matrix/labels/train 穿 universe_policy/pit_index_codes；artifact_metadata += 4 provenance 字段（自动入 ModelVersion.metadata）。
- 训练 metadata 测试 += 4 断言（默认 → CSI500_PIT_EXBOARD）；prediction **182 OK**；ruff/makemigrations 绿；commit fcb07cc（文档补提交）。
- part 2b 待做：rebuild/backfill 命令 flags + _reference_metadata_subset 读取侧 + 测试 → 地板 PR → owner-gated DR/B3。

- **B2 合并**：#209 → main 2ffea20。B1b part1 → **PR #210**（aef0a24）。

## 2026-11-20 — C2（UNI-03，#196）实施

- 分支 feat/dual-pit-series（从 2ffea20）。ACTIVE_PIT_BENCHMARK_SPECS（legacy full + 默认 ex-board）；run_post_sync 逐 spec refresh；BENCHMARK_SERIES_SPECS += 000852.SH。
- 测试 4 个（共存独立 constituent_count / 按序列增量 seed 保留 seed return / 循环逐 spec / comparison specs）。首跑 4 OK。
- mutation + markets 套件 + gates + 落地见 PR（待 B1b #210 合并后开）。
- 修复：既有 test_run_post_sync_universal_refresh_executes_metric_refresh_order 钉旧单调用 → 更新钉新循环（逐 spec 参数+顺序）+ import ACTIVE_PIT_BENCHMARK_SPECS。
- **B1b 合并**：#210 CI 双绿 → --admin → main 89e80d0（**11 PR 合并**）。
- **C2 落地**：mutation rc=1；markets **167 OK**；gates 绿；commit 8b91406 → rebase（CHANGELOG union 脚本自动）→ **PR #211**（81f4195）。
- 剩余 Wave 1 代码：B1b part 2（训练/命令 --universe-policy + artifact/ModelVersion provenance 写入侧）+ 地板 PR（CI 实测 Ran N）。之后 B3/DR1-7 待 owner 逐次批准。
- **B1b p2b**：rebuild --universe-policy/--pit-index-codes + reader subset += pit_index_codes/pit_benchmark_code；backfill 无 flags（默认继承 ex-board + fixed-engine D7）。测试 4 个（TestCase + enforce passthrough 修复）。prediction+backtest 套件绿；commands.md 再生。
- **C2 合并** #211 → a2555f7（**12 PR 合并**）。B1b 全卡 rebase 后开 PR。
- **B1b 全卡落地**：p2b commit 571208c（prediction+backtest **385 OK**，gates 绿，commands.md 再生）；rebase 3 commits（journal union×2）；push 首次瞬断（curl 7，已知路径现象）→ ls-remote 确认远端无分支 → 重试成功 → **PR #212**（19d96e9）。
- **Wave 1 代码卡全部完成**。剩地板 PR：#212 合并后读 CI 实测 Ran N（measured−4 惯例，注释写明测量来源）。之后 owner-gated：DR1-7 → B3 → H1/H2 → STK-10 → P1 → CMP → R1 → S1。
- DR 执行脚本已备好（**未执行**，待 owner 批准）：tmp/dr1_sync_csi1000.sh（DR1-3 年分块 + 连续性 + 官方基准）、tmp/dr6_namechange_reconcile.sh（DR6 全史 + 对账，diff 非零退出）。bash -n 通过。
