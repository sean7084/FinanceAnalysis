# 六项待决策事项的背景与选项（2026-11-17）

本文件与六个 issue 上的背景评论**同源**，便于一次读完并横向比较。每项结构一致：
事实（带 file:line）→ 选项 → 推荐 → 反方论据 → 选错的后果。

决策权在 owner；本文件不做决定。测量日期为 2026-11-12 至 2026-11-17，宿主为 WSL2 + NAS 数据层。

| issue | 主题 | 优先级 |
|---|---|---|
| [#136](https://github.com/sean7084/FinanceAnalysis/issues/136) | ETF 测量仪器：分辨率与统计量 | P1，blocks #137 |
| [#140](https://github.com/sean7084/FinanceAnalysis/issues/140) | list_date 刷新范围与 class-B 定时回填 | P2 |
| [#141](https://github.com/sean7084/FinanceAnalysis/issues/141) | 股票 feed 的 universe 范围 | P2 |
| [#154](https://github.com/sean7084/FinanceAnalysis/issues/154) | MacroSnapshot.dxy 改名与否 | P3 |
| [#149](https://github.com/sean7084/FinanceAnalysis/issues/149) | 服务器栈与 lockfile 对齐 | P2，owner decision |
| [#150](https://github.com/sean7084/FinanceAnalysis/issues/150) | GPU 运行时是否支持 | P2，owner decision |

## 阅读前必须知道的三件事

1. **有几条先前写进 issue 的前提已被证伪并更正**：`production.txt` 没有被任何地方安装、gunicorn/uvicorn 从未被调用（#149）；前端零消费 `dxy`（#154）；`~1,760` 在仓库里无依据、CSI500≈500 才是契约（#141）；`list_date` 写入器与停牌区间回填命令都已存在（#140）。
2. **测量环境本身有噪声**：未改动的 DQ phase 计时在不同 session 间可摆动 10×（`universe_and_audit_writes` 7.4 s → 77.8–83.5 s）。任何单次计时都不能当结论；SELECT replay 的噪声地板约 6 s，与要归因的残差同量级，曾把结论引向反方向。
3. **#138 曾被误关两次**：#162 与 #172 的正文里出现了一句**否定式**的关闭指令，GitHub 的关键字解析器不理解否定，把它当成关闭指令执行。两次都已重开；本文件刻意不复现该短语，因为写出它本身就是触发条件。残差已归因但未修。

---


## #136 — ETF 测量仪器：分辨率与统计量

https://github.com/sean7084/FinanceAnalysis/issues/136
### 事实

- ledger 里 36 条读数、9 个 arm/start/purge 组，**每一组 k=6 blocks**（`docs/reports/evidence/etf-rotation-measurements.jsonl`）。
- 块内 Sharpe 的 **median sd = 1.006** → SE ≈ 1.006/√6 = **0.411**。这是**下限**：verdict 用的是 6 个块的中位数（`run_etf_rotation_backtest.py:2163-2166` 生成 `median sharpe X, positive Y%`），中位数的抽样方差大于均值。
- 一个 panel 只有 **~210 次调仓**（2016 start；部分 arm 126，2017 start 186）。要分辨 0.3 需 11 blocks、0.2 需 25、0.1 需 101 —— 25 blocks 意味着每块 ~8 次调仓，那时"块 Sharpe"已是另一个更噪的对象。
- `combinatorial_purged_folds` 产出 C(6,2)=**15 folds**，但每个块在多个 fold 里复用（`walk_forward.py:519` 文档明写 held-out block 是 disjoint contiguous window），所以**独立样本数是 6，不是 15**。
- between-start spread：LIGHTGBM **0.465**、LSTM **0.632**（purge 20）。这是**不同总体**，加 block 数动不了它。
- `etf_rotation` 里**没有任何 SE / t-stat / CI / bootstrap**。但 `rank_ic.block_rank_ic` 已返回 `per_date_ic`（`rank_ic.py:71-142`），股票侧有现成范式：`tasks_lightgbm.py:2073-2075`（`icir=mean/std(ddof=1)`、`rank_ic_tstat=mean/(std(ddof=1)/sqrt(n))`）。`gate.py:87-88` 已经在算跨 start 的 sharpe mean/sd。
- 换统计量必须**新写 write-once 预登记**：`evidence.py:39-46` 的 sha256[:12] digest 进每条 ledger 行与报告文件名；`measure_etf_rotation_sensitivity.py:135-144` 对未声明的 `<arm>@<panel-start>` 硬失败。

### 选项

- **(a) 接受分辨率上限**：声明仪器只能分辨 ~0.8 Sharpe（2×0.411），所有 sub-0.5 结论判为不可判定，P38 按设计永久关闭。零代码。
- **(b) 换统计量**：用 `per_date_ic` 算日频 IC 的 t-stat（移植股票侧范式，约 10 行），block median 降为稳健性视图，**Sharpe 腿保留为次级 gate**。
- **(c) 扩数据**：更长 panel / 更多 start。最贵，且不动 between-start 异质性。

### 推荐：(b)

理由是它把"噪声来自统计量的选择"这件事直接解决掉：同一策略的日频 IC 有数百个观测，而块中位数只有 6 个。成本约 10 行 + 一份新预登记。

### 反方论据（必须一起读）

1. **IC 显著 ≠ 扣费后可交易 Sharpe 为正**。所以不能静默替换 Sharpe 腿，否则会用"信号显著"批准一个净收益为负的策略。
2. 日频 IC 存在**自相关**，朴素 `std/sqrt(n)` 的 t-stat 会虚高；要么上 Newey-West，要么在预登记里明确声明这是未修正的 t 并相应抬高阈值。
3. 换统计量等于**换研究问题**：旧 ledger 读数与新读数不可直接比较，必须新预登记、旧读数作废。这不是"改进测量"，是重新开一轮。

### 选错的后果

- 选 (a)：诚实但研究停滞，P38 永久关闭。
- 选 (c)：花最大成本，between-start 异质性原样保留。
- **不选而继续跑 rematch**：等于继续用噪声当结论——这正是 P38 反复开关的机制。


## #140 — list_date 刷新范围与 class-B 定时回填

https://github.com/sean7084/FinanceAnalysis/issues/140
### 事实

- **日常写入器已存在且会更新既有行**：`sync_index_constituent_universe`（`apps/markets/tasks.py:788-821`，`:812-813`），源自 `stock_basic`（`_fetch_stock_basic_map:257-284`）。但只覆盖拉取窗口内出现在 `index_weight` 的 ts_code（~61 天：`:74`、`:1256`）。
- `sync_asset_history:1036-1065` 也写 list_date，但值来自 `asset.list_date` 自身（`:610`、`:631`）——**循环赋值，不是刷新**。
- `backfill_asset_list_dates.py:107-151` 是全市场刷新，**只能手动跑**，无 beat entry 包装。
- 实测：1,912 资产、`list_date IS NULL` = **2**、`max(list_date)` = **2026-01-29**。正确解读是"此后没有新上市进入 CSI500 快照"，**不是写入器坏了**。
- 两年 EU 窗口的 22 条 critical 里 **0 条与 list_date 有关**，`asset_lifecycle_issues.csv` 为空；critical 主体是 `stale_macro_snapshot`（14）与 index-valuation coverage（4）。
- **停牌区间回填命令已存在**：`backfill_asset_suspensions.py:16-35`，由 `catchup/repairs.py:75-86` 调用；缺的是**定时触发**（当前 watermark 事件驱动：`detectors.py:272-295`、`registry.py:73-81`）。
- 调度：`sync-a-shares-daily-from-tushare` 00:10、`sync-index-memberships-monthly` 每月 1 日 10:15（`base.py:602-609`）；日常同步内部已调成员同步（`:1254`）且已拉全市场 `stock_basic`（`:692`）。
- `namechange` 全库无匹配。

### 选项

- **(a) 保留 universe-scoped 刷新**（对 CSI500 是正确设计）+ 给停牌区间回填加一个**定时**入口 + 把 macro 类 critical 移交 #171。
- **(b) 新增全市场 list_date 刷新 beat entry**（包装现有命令）。
- **(c) 搭车进 `sync_index_constituent_universe`**：复用 `:692` 已有的 `stock_basic` 调用，零新增 provider 调用。

### 推荐：(a)

因为"cadence 坏了"这个前提不成立：写入器在工作，只是范围就是契约范围。真正缺的只有停牌的定时触发，而命令已经写好了。

### 反方论据

1. (c) 看起来最省（复用既有调用），但会把**元数据修复耦合到成员同步的失败面**：union 为空时 dispatcher 在 `:1261-1262` 中止，一次 provider 故障同时停掉两件事。
2. 定时停牌回填会增加 `suspend_d` 调用量；而 watermark 驱动本来就能补。**先量一次"watermark 实际漏了多少"**再定 cadence，否则可能是冗余 job。
3. 若将来扩 universe，(b) 就变成必需——但那是 universe 决策（见 #141），不是 cadence bug。

### 选错的后果

- 选 (c)：把两个不相关的失败面绑在一起，故障时更难定位。
- 直接给"21 criticals"定策略：那 21 条来自另一个窗口，在能跑的窗口里 list_date 贡献为 0，策略会针对不存在的问题。


## #141 — 股票 feed 的 universe 范围

https://github.com/sean7084/FinanceAnalysis/issues/141
### 事实

- **CSI500（≈500）是文档契约**：`README.md:3`、`TECHNICAL_GUIDE.md:31`、`apps/markets/benchmarking.py:5,27,35`、`docs/how-to/csi500-migration-runbook.md:360`（"a few hundred"）。
- **499 行/天 = 当前 CSI500 快照**：`sync_daily_a_shares` 迭代 `current_union_ts_codes`（`apps/markets/tasks.py:1270-1273`），来自 `DEFAULT_MEMBERSHIP_INDEX_CODES = ('000905.SH',)`（`:35`）；新上市只能经指数成员进入（`:793-802`），无全市场发现。
- **1,912 = 2010 年以来历史并集**（`csi500-migration-runbook.md:227,251`）；`docs/reference/metrics.md:43` 只当测量值记录。
- **`~1,760` 在仓库里没有任何依据**——没有文档把它写成目标。本 issue 正文已自行退役该标准。
- detector 是**邻居相对**规则：`cross_sectional_gap_dates` 只在 `count < 0.5 × 邻居中位数` 时报（`CATCHUP_CROSS_SECTION_MIN_RATIO=0.5`），零计数也留在中位数里，所以被 outage 包围的日期仍会被判。它**没有绝对期望**。
- 实测：`detect_ohlcv_gap` → `missing=0`，`cross_sectional_gap_dates` → `()`，81 个开放 session 每天 499 行。
- `effective_universe`（`benchmarking.py:237-268`）是 PIT 成员 ∩ OHLCV 存在性，是**读取侧 gate**，不是 feed scope。

### 选项

- **(a) 按已验证关闭**：499 就是契约，detector 报 0 缺口是正确的，不需要绝对下限。
- **(b) 扩到全市场 feed** + 给 detector 加绝对覆盖下限。
- **(c) 保持 open**，等更多证据。

### 推荐：(a)

"均匀欠覆盖"这个担忧建立在一个不存在的目标（~1,760）上。契约是 CSI500，feed 与契约一致，detector 的行为也正确。

### 反方论据

1. 若你的研究其实需要全市场（小盘、breadth、行业轮动），**CSI500 契约本身就是限制**——但那是研究 scope 决策，不是数据缺口修复。
2. 绝对下限会在退市/ST/universe 重定义时误报，这正是 #140 那批 historical criticals 的成因；加下限等于把一类已知误报重新引入。
3. 我先前说"若目标是 1,760 则 detector 结构上看不见均匀欠覆盖"——这句在逻辑上成立，但**前提数字无依据**，所以不该作为决策依据。

### 选错的后果

- 选 (b)：feed 从 ~500 扩到 ~1,900+，TuShare 调用量与 DQ 扫描量数倍增长；而 #138 已实测两年窗口的 per-asset 阶段就要 116–127 s、全历史跑不进时限。扩 universe 会先把数据质量门禁压垮。


## #154 — MacroSnapshot.dxy 改名与否

https://github.com/sean7084/FinanceAnalysis/issues/154
### 事实

- 存的是 **Dow Jones FXCM Dollar Index Basket**：`apps/macro/providers.py:27` `DXY_TUSHARE_CODES = ['USDOLLAR.FXCM', 'USDOLLAR']`，`:451` 按序尝试；`tests.py:464` 断言 `fx_quote_sources['dxy'] == 'USDOLLAR.FXCM'`，`:426` 钉住旧历史用 legacy code。
- 数据侧佐证：202 行月度（2010-01..2026-11），`dxy` 值域 **93.5300–134.0950**；ICE DXY 同期约 79–110 → 名实不符在数据里也看得见。另有 **12/202 月为 NULL**（见 #171）。
- **前端零消费**：`frontend/src/` 对 `dxy`/`DXY` 无匹配；`lib/api.ts:24-26,562-563,653-654` 只调 `/macro/contexts/`，**没有代码调 `/macro/snapshots/`**；i18n 无美元指数文案。→ **改名不是 UI 破坏性变更**（我先前评论里那条"含前端读的 key"是错的，已在正文更正）。
- API 面：`serializers.py:10`（`:13` 还暴露 `metadata`）、`views.py:13` ModelViewSet、`config/urls.py:79`；schema 由 drf-spectacular 自动生成，`docs/reference/api.md:263` 只列端点不列字段；版本化只在 URL 层（`api.md:21`）。仓库内找不到外部消费者。
- **改名的真实成本在后端与配置**：`config/settings/base.py:397` 的 `MACRO_RELEASE_LAG_DAYS` 默认值含 `'dxy'` 键（连带生成的 `docs/reference/env.md:102`）；`apps/etf_rotation/regime.py:24,94` 的特征名 **`dxy_trend_3m`**；`validate_data_quality.py:192`；backfill 命令 `:378-553`；约 20 处测试；生成的 `metrics.md:171` 会随字段自动重生成。
- **provenance 已逐行记录**：`providers.py:479` 写 `metadata['fx_quote_sources'][field] = ts_code`，但**只有 TuShare 时期的行带它**。
- 无第二处同类错配：`cny_usd` 名实相符（`models.py:10` + `providers.py:127-132,452,534` 正确取反）。

### 选项

- **(a) 改名**：AlterField 迁移 + settings 键 + 特征名 + ~20 测试 + 两份生成文档重生成。
- **(b) 保留名字**，只改 `help_text` / `verbose_name` 记录错配（`models.py:9` 现在写的是 "US Dollar Index (DXY)"，谎名也在人类可读标签里）。零迁移、零 API 变化。
- **(c) 改名 + 保留一版 API 别名**（serializer 里同时输出新旧键）。

### 推荐：(a)

关键论据不是 API，而是**谎名已经繁殖进特征名 `dxy_trend_3m` 与 DQ 规则**。只改 help_text 会让下游继续用错误的语义命名新特征。前端零消费使 (a) 的风险比我先前估计的低得多。

### 反方论据

1. 改名后**历史 ledger / 报告里的字段名与新名不一致**，旧报告不能直接 grep 对照。
2. `metadata.fx_quote_sources` 只有 TuShare 期行有，**早期行无法还原 provenance**；改名而不回填 metadata，反而丢掉"这行到底是什么"的线索。
3. (b) 零风险且完全站得住：如果你不想动 config 与特征名，记录错配是诚实的次优解。

### 选错的后果

- **最危险的遗漏点**：选 (a) 却忘了改 `MACRO_RELEASE_LAG_DAYS` 里的 `'dxy'` 键 → 该序列的 release lag 取不到值而走 fallback，**look-ahead 防护静默失效**。这比名字错更严重。
- 选 (b)：谎名继续繁殖到下一个特征名。


## #149 — 服务器栈与 lockfile 对齐

https://github.com/sean7084/FinanceAnalysis/issues/149
### 事实（这一条推翻了我先前的表述）

- **`production.txt` 没有被任何地方安装**：CI 装 `local.txt`（`.github/workflows/ci.yml:278`），唯一的 Docker 镜像也装 `local.txt`（`compose/local/django/Dockerfile:55`）。
- **gunicorn / uvicorn 从未被调用**：唯一启动路径是 `manage.py runserver`（`compose/local/django/start.sh:6`、`scripts/run_backend.sh:13`、`run_backend.ps1:12`）。没有生产镜像、没有 nginx 配置。
- `README.md:186-189` 明写 ASGI/WSGI 服务器 "**Not implemented** … for whoever wires one up — but nothing here deploys them"。
- `config/asgi.py:21-25` **确实**定义了 channels + websocket 的 ASGI app（`base.py:82,784` channels-redis layer），但没人 serve 它。
- 版本差本身：`local.txt:1183-1187` gunicorn 25.3.0 `# via mlflow`、`:3719-3722` uvicorn 0.54.0 `# via mlflow-skinny`（纯传递）；`production.in:21-22` 直接 pin 23.0.0 / 0.32.0。mlflow 3.12.0 两者都接受。
- **两份 lockfile 都在 Python 3.12 解析**（文件头），CI 跑 **3.14**（`ci.yml:255`）；两份都不含 `python_version`/`sys_platform` marker，都带 22 个 nvidia-* 行。
- 宿主：app 在 WSL2，NAS 只放 Postgres/Redis/MinIO（`.env.example:26,65,69`）。

### 选项

- **先答一个前置问题：要不要跑真服务器？**
- **若不要（今天的现实）**：(d) 删掉 `production.in` 里的 gunicorn/uvicorn/daphne pin，或明确标注 `production.txt` 未使用；并处理解释器错配（把 CI 降到 3.12，或用 `--python-version 3.14` 重编译）。
- **若要**：那是一个部署项目（serve 现有 ASGI app），之后 (a) 对齐 production.txt 到 CI、(b) 在 `.in` 里按意图 pin、(c) 声明分歧有意，才有意义。

### 推荐：先定意图；若短期不上服务器，选 (d) + 用 `--python-version 3.14` 重编译

因为版本差今天是**化妆品级**问题（改一个没人安装的文件），而解释器错配是**唯一真实风险**：hashed install 在不同解释器上可能合法拒绝 marker-excluded 包。

### 反方论据

1. 删 pins 会让将来上服务器时重新解析依赖，mlflow 的传递依赖可能又拉进新版本——保留 pins 今天零成本。
2. 把 CI 降到 3.12 比按 3.14 重编译更省事，但会让 CI 离"未来生产解释器"更远；两条路各有代价。

### 选错的后果

- 继续声称"CI 覆盖生产服务器"是**假的**：CI 覆盖的是 `runserver` 路径。任何基于"生产已测"的部署信心都不成立。
- 只对齐版本而不记录意图：下一次有人读 `production.txt` 仍会以为存在一个生产部署。


## #150 — GPU 运行时是否支持

https://github.com/sean7084/FinanceAnalysis/issues/150
### 事实

- **GPU 探测已实现**：`apps/prediction/tasks_lightgbm.py:268-274` 用 `model.predict(..., device_type='cuda'/'gpu')` 试探并缓存；`tasks_lstm.py:744`、`apps/etf_rotation/train_lstm.py:974` 用 `torch.cuda.is_available()`。
- **失败静默回退**：`apps/backtest/tasks.py:1359-1366` 探测失败即返回 `cpu_serial`；`:1555-1556` 在 `used_gpu` 为假时把 backend 重标为 `cpu_batched`。→ "选了 windows_gpu" 与 "真的用了 GPU" 在结果里**无法区分**。
- `windows_gpu` 是 API/CLI 可选值：`apps/backtest/serializers.py:26` 的 `VALID_LIGHTGBM_INFERENCE_BACKENDS = {'auto','cpu_serial','cpu_batched','windows_gpu'}`（`:103` 否则报参数错误）；**前端无匹配**，不是 UI 可选。
- **没有任何报告证明 GPU 推理跑过**：存在 `core18-20260621-wsl2gpu*` 报告，但 `matrix_manifest.json:6` 只记录 *requested* backend；全部报告里找不到 `used_gpu`/`gpu_device_type`/`cuda`。
- **文档与闭包矛盾**：`docs/how-to/backfill.md:380-381`、`local-setup.md:689-691` 仍称 torch 是 CPU-only build；而 hashed 闭包含 `cuda-toolkit[...]`、`cuda-bindings==13.5.0`、15 个 nvidia-* 包（含 cudnn-cu13/nccl-cu13/cublas）、`triton==3.7.0`。
- **宿主物理上可行**：app 在 WSL2、Windows 侧保留 NVIDIA 驱动、NAS 只放数据（`local-setup.md:8-13`、`.env.example:26,65,69`）。

### 选项

- **(a) 先测再决**：跑一次现有探测，把 `used_gpu`（以及 `torch.cuda.is_available()`）写进报告 manifest，然后决定支持或卸载。
- **(b) 直接支持 `windows_gpu`**：修掉"CPU-only torch"的过期文档，按意图 pin 需要的 CUDA wheel。
- **(c) 删 `windows_gpu`** 并从 lockfile 卸掉 CUDA wheel 集。

### 推荐：(a)

一次探测很便宜，而它**决定 (b)/(c) 哪个有意义**。今天没有任何证据表明 GPU 路径真的工作过，直接选 (b) 或 (c) 都是在猜。

### 反方论据

1. 探测本身可能给假阳性：LightGBM 的 `device_type` 参数在不同版本行为不同，所以要**同时记录两个信号**（`torch.cuda.is_available()` 与实际 `used_gpu`），而不是只信一个。
2. 卸 wheel 会伤到真有 GPU 的环境，并让 `windows_gpu` 变成参数错误——如果 NAS 之外还有别的推理宿主，(c) 的代价比看起来大。
3. CI 今天也装同一份 `local.txt`，即**下载整套 CUDA stack 去跑不需要 GPU 的测试**；这是 runtime 成本问题，不是正确性问题，但值得一起决。

### 选错的后果

- 选 (b) 而不测：继续静默回退，报告里的"GPU"字样不可信，性能结论全部建立在未验证的假设上。
- 选 (c)：真有 GPU 时失去加速路径，且要重新解析依赖才能恢复。
