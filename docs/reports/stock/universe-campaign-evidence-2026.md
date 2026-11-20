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
