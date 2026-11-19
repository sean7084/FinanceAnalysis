# 指标存储改造：执行证据日志（2026-11-18）

> 本文件是**执行证据日志**，逐字保留 40+ 轮执行中的实测、修正与撤回。
> **权威设计见计划文件** `tranquil-sea-rudd.md`；两者冲突时**以计划为准**。
> 撤回段落刻意保留 —— 它们记录了哪些假设被实测否证，是证据链的一部分。

## 执行进展与实测修正（2026-11-18，实施中追加）

### Step 0 已完成 —— GO，且余量比原估算宽

`TABLESAMPLE SYSTEM (0.2)` 实测：345,949 行 / (0.2% × 4,603,165 页) = **37.6 行/页**，
`avg_data_bytes` **77.99**（原估算 77，几乎命中）。元组 108 B → 理论 75.6 行/页 →
**膨胀因子 2.01x**，远高于 1.4 的 go/no-go 阈值。推算总行数 ≈ **173M**，
即 `reltuples` 179.09M 偏高约 3.5% —— **精确 COUNT 不需要**（后台 COUNT 已停止，
`pg_stat_activity` 核实无遗留 backend）。

修正后的占用估算见下方【二次修正】—— 此处原写 heap 14.1 GiB / 总 ~51 GiB，**该数字已撤回**。
`pgstattuple` 无需安装（省掉一次生产写入）：膨胀已由 `TABLESAMPLE` + 精确行数实测确认。

### 【二次修正】heap 估算：我错了，Plan agent 原本是对的

我曾断言 Plan agent 的 20.1 GiB heap 估算"把 parameters 38 B 与 param_key 16 B 重复计入"，
并把它改小到 14.1 GiB。**这个"修正"本身是错的，现撤回。**

原因：campaign A **保留 `parameters` 作为载荷**（BBANDS 的 bands、FIB_RET 的 levels、
序列化器与 DQ 明细都读它），`param_key` 是**新增**列，不是替换。所以行是**变宽**约 15 B，
不是变窄 23 B。

实测（`pg_column_size` 逐列求和）：
- 回填前全局抽样：**77.99 B** 数据
- 回填后 asset 1（94,475 行）：**93.22 B** 数据 → 增加 15.2 B，与新增列宽一致

所以紧凑行 = 24 B 头 + 93.3 B 数据 → MAXALIGN 120 + 4 B ItemId = **124 B/行 → 65.8 行/页**。
180.2M 行（含 SMA-60）/ 65.8 = 2.74M 页 = **20.9 GiB** heap（不是 14.1）。

**修正后的总估算：**
| 项 | GiB |
| :--- | :--- |
| 热表 heap（VACUUM FULL 后） | 20.9 |
| 覆盖唯一索引 | ~12.0 |
| pkey REINDEX | ~4.5 |
| `(indicator_type)` + `(timestamp)`（去重生效 ~2.4 / 失效 ~7.5） | 2.4 – 7.5 |
| 其他关系（已删 0.47，5 表 VACUUM FULL 估 −2） | ~18.9 |
| **合计** | **58.7 – 63.8** |

目标 66.23 GiB → **仍达标，但余量只有 2.4–7.5 GiB**，不是我先前说的 ~15 GiB。
若堆回收不及预期或去重失效同时发生，可能贴线。**Step 5 的条件杠杆因此重新变得相关。**

要把 heap 真正降到 ~14 GiB，必须让 96.5% 的行不再存 `parameters`
（这些行的 payload 纯输入配置、可由 `param_key` 重建）。但 `param_key` 用 `str(value)`
拼写，`timeperiod=50` 无法区分 int 50 / float 50.0 / 字符串 "50"，**重建不无歧义**，
且会改 API 与 DQ 输出 → 属 campaign B 的申报式 diff，不在 A 内做。

### 新发现的缺陷：FIB_RET 是 BBANDS 同类缺陷的第二个实例

[apps/analytics/tasks.py:563-581](file:///\\wsl.localhost\Ubuntu-24.04\home\cliu\FinanceAnalysis-wsl2\apps\analytics\tasks.py#L563)
把 `high/low/range/levels/close`（含嵌套 `levels` 字典）写进 `parameters`，
查找键却只用 `{'lookback_days': N}` —— 与 BBANDS 同样丧失幂等性，且规范键会**超出 varchar(120)**。

**原计划漏了它**，因为单资产普查显示 FIB_RET 只有 1 个参数字典、21 B。
跨全库 0.2% 页抽样（~344k 行）复核后确认：
**生产里只有 BBANDS 带输出载荷**（11,421 行 / 11,348 个不同字典 / max 114 字符），
FIB_RET 存量行全是干净的（1 个字典 / 21 B）—— 但**写入端代码不是**，
下次该任务创建行时就会产生超长键。已由回归测试实际触发（`StringDataRightTruncation`）。

修正：`COMPUTED_OUTPUT_KEYS` 改为按类型映射，BBANDS 与 FIB_RET 都剥离输出；
`param_key` 只存输入键，**载荷 `parameters` 原样保留**（消费者从中读 bands 与 levels）。
另：jsonb 合法地可以是标量，生产抽样 0/343,695 为标量，但 helper 与回填 SQL 都做了非 object 兜底，
避免一行标量就让 173M 行的回填中途报错。

### 新增前置阻塞：唯一键切换前必须证明无重复规范键

唯一性从 `parameters` 换到 `param_key` 后，同一 `(asset, timestamp, indicator_type)` 的
多条 BBANDS/FIB_RET 行会**塌缩成同一个键** → `CREATE UNIQUE INDEX` 直接失败；
切换之后每日 `get_or_create` 写入端会开始抛 `IntegrityError`。

**因此写入端查找键修复必须与约束切换同批或更早，不能像原计划那样放到 campaign B 之后。**
抽样检查（0.2% 页，按规范键分组）：**0 个重复组** —— 隐患未显现，
但抽样不能证明全库无重复，**全量检查仍须在窗口内、建索引之前完成**。见任务 #9。

### 已落地并验证的代码（campaign A 的加法阶段）

- `apps/analytics/indicator_params.py`：`canonical_param_key` + `COMPUTED_OUTPUT_KEYS`。
- 迁移 `0011_technicalindicator_param_key`（`null=True`，**无 default** ——
  原计划的 `default=''` 会让存量行经 `attmissingval` 读成 `''`，
  与 OBV/RS_SCORE 真正的空键无法区分，**摧毁回填的可续跑标记**）。
- `TechnicalIndicator.save()` 派生键 → **自动覆盖全部 12 个 `get_or_create` 写入端**。
  生产只有 **2 个** `bulk_create` 站点（`analytics/tasks.py:725`、
  `backfill_technical_indicators.py:426`）；原计划列的 `backfill_model_data.py:530/537`
  实为 **FactorScore**，不是本表。两处已在 choke point 显式赋键。
- `apps/analytics/management/commands/backfill_indicator_param_key.py`：
  按资产×年分块、dry-run 默认、`param_key IS NULL` 可续跑、
  **写前**用样本交叉校验 SQL 与 Python 的键拼写，分歧即 `CommandError` 中止。
- 可 sarg 化：`historical_features.py` 新增 `_session_upper_bound`，替换 `:119` 与 `:445`。
  `apps/core/date_floor.py:20-31` 的 `session_midnight` 文档早已指出 `timestamp__date`
  非 sargable 会强制顺序扫描 —— 本次是沿用它自己的既定修法，不是新发明。
  仪表盘 `views.py:628-634` 也早因同一病理修过（**实测 151.7 s**）。
- `Meta.ordering = []`（迁移 `0012_alter_technicalindicator_options`）。
  **screener 的 `macd_qs[:limit]` 必须补显式排序**，否则不只是顺序变、
  而是**取到不同的行**；已补 `order_by('-timestamp','asset_id')`。
  已核实 DQ 技术预取在 `validate_data_quality.py:2303` 显式排序，故 CSV 字节不受影响。
- 序列化器加 `param_key`（加法，`parameters` 与 `id` 不变）。

### 测试证据

- `tests_indicator_params.py` 10 项，**变异证明**：破坏 BBANDS 剥离 → rc=1，恢复 → rc=0，
  同文件 `false`/`true` 对照读 1/0。
- `tests_sargable_indicator_bound.py` 4 项，含**跨两种写入键约定的行集等价**。
- `tests_backfill_param_key.py` 7 项，含**分歧中止**（证明交叉校验能失败，且在任何写入之前中止）。
- 五应用回归 570 项 **OK**（`suite_rc=0`，对照 1/0）。
  首轮曾 **12 errors**，全部由上述 FIB_RET 超长键与标量载荷引起，
  **未修改任何既有测试**即修复。注意：后台任务自报 exit 0 而脚本内 `suite_rc=1` ——
  又一次印证退出码必须来自带对照的脚本文件。
- 预登记 `docs/reports/preregistration/technical-storage-stage1-2026-11-18.md`，
  digest **`3cfb7668905e`**（用仓库自己的 `preregistration_file_digest`）。
  只登记 campaign A；训练器臂**未登记**（产出 5,703,455 行 RSI 的 argv/窗口尚未取得）。

### 已在生产落地并实测（2026-11-18）

**冗余索引删除：132 GB → 124 GB（−8 GB），索引数 11 → 7。**
- `markets` 0016：删 OHLCV 与唯一约束**完全重复**的 `(asset_id,date)` 索引（373 MB）
  + FK 独立索引（101 MB，被唯一索引前缀覆盖）。
- `analytics` 0013：删热表 FK 独立索引 `(asset_id)`（3,885 MB，被热索引前缀覆盖）。
- `analytics` 0014：删 `indicator_type varchar_pattern_ops`（4,013 MB）。
  该索引**不在任何模型或迁移里**（手工建的），故用 RunSQL 删除、无状态漂移；
  全仓库 grep 确认**零** `indicator_type__startswith/contains/regex`、零 raw SQL / `.extra()` / `RawSQL`，
  即它不可能有消费者。lifetime `idx_scan` 671 vs 普通索引 94,480。

**关键工程修正：不能用 Django 生成的 `AlterField(db_index=False)`。**
`sqlmigrate` 显示它会 **DROP 再 ADD 外键约束**，而重新添加会**逐行重新校验**——
在 174M 行的热表上等于一次 ACCESS EXCLUSIVE 下的全表校验。
改用 `SeparateDatabaseAndState`：state 侧记 `db_index=False`，database 侧只发一条
`DROP INDEX`（瞬时）。**实测两条外键约束仍在且 `convalidated=t`**，证明约束未被动过。

**回填空间行为实测（asset 1 全量，88,903 行，19.9 s）：**
`pg_database_size` 与 `pg_relation_size` **前后逐字节相同**（133,444,910,103 / 37,709,127,680）。
但 `n_tup_hot_upd` 只增加 **880 / 88,903 ≈ 1%** —— **"HOT 更新所以不占空间"的假设是错的**，
真正原因是页内既有空闲空间吸收了新版本。因此**不能**把单资产的零增长直接外推到全表：
99% 非 HOT 意味着每次更新都会往**每个剩余索引**插新条目，全量回填的索引增长才是真风险。
已加 `--max-assets` 以做 ~1% 规模（20 资产）的增量实测再决定。

**写入端查找键修复已实现（BBANDS + FIB_RET）**，`get_or_create` 改按
`param_key=canonical(inputs)` 查找，`defaults` 仍带完整载荷。
两个幂等性测试通过，任务日志出现 `Created → Updated`，**证明原死代码分支现在真的会执行**。
**必须的时序**：回填完成 → 部署写入端修复 → 全量重复键检查 → 建覆盖唯一索引。
（回填前旧行 `param_key` 为 NULL，查找会落空；修复若早于回填会反而制造新行。）

**覆盖唯一索引迁移 0015 已写好但未应用**，列序定为
`(asset_id, indicator_type, timestamp, param_key) INCLUDE (value)`。
理由不是猜测：决定性的消费者是 DQ 分块批量读，它要**窗口内全部变体**；
若把 `param_key` 放到 `timestamp` 前面，窗口就不再是索引边界，扫描会覆盖 16 年而不是所请的几个月。
当前列序下 DQ 恰好读其窗口、训练器单变体读为索引-only、逐资产降序在索引内过滤 param_key
（7 变体中取 1 约 84 条），SMA-60 落库后原病理消失。迁移文档里写明了三条前置条件。

### 回填空间安全性：已实测，不需要 zpool 余量确认

20 资产 / **1,738,422 行**（约全表 1%）/ 337.5 s：
- `pg_database_size` 133,444,910,103 → 133,445,098,519 = **+188,416 B（184 KiB）**
- **只有热索引增长**（13,847,535,616 → 13,847,724,032，差值与总量完全相同）；
  其余四个索引与堆**逐字节不变**。

即 99.95% 的新索引条目复用了既有空闲空间。线性外推到 174M 行 ≈ **18 MiB**，不是 GiB。
**"99% 非 HOT 会让索引暴涨"的担忧被实测否证**，回填本身是空间安全的。
（仍保留增量推进 + 每批复测尺寸的做法，因为外推假设空闲空间均匀可用，而 autovacuum 会持续回收。）

**吞吐实测 ~5,150 行/s → 全表约 9 小时。** 其中约一半是我自己的冗余开销：
执行路径上每个分块先 `COUNT(param_key IS NULL)`，而该计数要**逐行回表**，
成本与 UPDATE 本身相当。已删除该预计数，改用 `cursor.rowcount`（免费得到同一数字）。

**另修一处自身缺陷**：命令结尾的完整性检查原本无条件做
`filter(param_key__isnull=True).count()` —— **无资产过滤的全表扫描**，
实测单次 **7 分 18 秒**（`pg_stat_activity` 抓到 `SELECT COUNT(*) … WHERE param_key IS NULL`，
等待 `DataFileRead`）。现改为：仅 full-scope 运行才做全表计数；
有界运行只数本范围内（走热索引，快）。日志"卡住"其实是 Python 块缓冲，不是停滞。

### 两处对原计划的设计修正（均由测试抓出）

**1. `NULLS NOT DISTINCT` 是错的，改用默认 `NULLS DISTINCT`。**
原计划要求过渡期用 NULLS NOT DISTINCT 防止重复 NULL 键。但 SMA/EMA **每个
(asset, timestamp) 合法地有 6 个变体**，回填前它们的键全是 NULL ——
把 NULL 视为相等会让这些**合法行互相冲突**，索引在回填前根本建不起来。
测试实测报错：`Key (asset_id, indicator_type, "timestamp", param_key)=(1, SMA, …, null) already exists`。
改为 NULLS DISTINCT 后：回填前状态合法，回填后由真实键强制唯一。**严格更优，且少一条前置条件。**

**2. 取消 `param_key` 的 NOT NULL 翻转（原 0014 / 现 0017）。**
理由：(a) NOT NULL 列下**无法构造回填前状态**，回填命令变得不可测试；
(b) `SET NOT NULL` 要全表校验 174M 行；(c) 对速度与体积**零收益** ——
`save()` 与两个 bulk_create choke point 已保证必然赋值，且行内无 NULL 时 Postgres 不存 null bitmap。
保留 nullable 反而让"NULL = 尚未回填"这个可续跑标记永久可用。
0017 改为仅更新 help_text（原文只提 BBANDS，现也含 FIB_RET）。

### 消费者切换的精确设计（回填完成前不得部署）

**为什么必须等回填**：旧行 `param_key` 为 NULL。若消费者提前改读 param_key，
NULL 既不等于 `''` 也不等于期望键 → 打分为 −1 → **整张特征矩阵被静默清空**。
另外部分测试 fixture 用 `bulk_create` 建行且不设键（如 `apps/core/tests.py:1748`），
切换后这些测试会失败，须同批修正。

**训练器（`tasks_lightgbm.py`）—— 不能简单把等值下推 SQL。**
`_indicator_frame_from_queryset:1147-1168` 的语义是：空参数记 1 分、精确匹配记 2 分、
其余 −1；随后按 `(asset_id, timestamp, parameter_score)` 排序并
`drop_duplicates(keep='last')` —— 即**同一 (asset, timestamp) 上精确匹配胜过无参数行**。
若直接把 `param_key='timeperiod=14'` 下推 SQL，就丢掉了 1 分与 2 分的区分，**tie-break 会变**。

正确改法：把投影里的 `parameters` **换成** `param_key`（不是删掉过滤），
打分改由 param_key 推导：`'' → 1`、`== canonical → 2`、其余 −1。
语义逐字保持，且 param_key 在覆盖索引里 → **索引-only 扫描成立**。
期望键用 `canonical_param_key(None, {parameter_key: parameter_value})` 构造
（None 表示不做输出剥离；训练器读的 9 个类型都是单键输入参数）。
RS_SCORE 已经只投影 `('asset_id','timestamp','value')`，无需改。
训练器的窗口边界**已经**是可 sarg 的 `session_midnight`，也已显式排序 —— 无需改。

**DQ（`validate_data_quality.py`）—— 索引-only 改写与 BBANDS 缺陷修复是同一个变更，无法拆分。**
实测：`:1903` 明细 CSV 写的是**期望变体**的 parameters（来自 `:1865` 的
`for indicator_type, parameters in expected_variants`），**不是存量行的**。
存量 `parameters` 列**只**用于 `:1856-1857` 构造分组键。
所以改用 param_key 分组后，BBANDS 的分组键会与期望键**相等** →
**602 行假告警消失** → CSV 改变。因此 DQ 的索引-only 改写天然属 campaign B 的申报式 diff，
**不能**放进 campaign A 的逐字节等价门。原计划把两者分开的设想在这一点上不成立。

### 容量阻塞已用实测解除（无需 NAS shell）

NAS 自己跑着 **netdata（:19999，LAN scoped）**，见 HomeServer `docs/reference/live-state.md`。
这是**只读监控 API**，不是绕过 SSH 2FA 的手段。实测 `disk_space./DefaultMirror`
与 `disk_space./DefaultMirror/psql`：

| 项 | 实测 |
| :--- | :--- |
| 池可用 | **2,335.3 GiB（2.28 TiB）** |
| 池已用 | 6,128.3 GiB → **72.4%**（低于 homenas-monitor 的 80% 告警线） |
| `psql` 数据集**物理**占用 | **70.8 GiB** |

→ VACUUM FULL 所需 ~2× 表大小与 ~12 GiB 覆盖索引**都绰绰有余**。
→ 同时得到计划要求的**物理基线**：124 GiB 逻辑 → 70.8 GiB 物理 = **lz4 压缩比 ~1.75x**，
与文档记载的 ~1.8x 一致。**减半目标对应物理 ~38 GiB。**

**仍未取得**（需业主自己的 NAS 会话，SSH `192.168.31.8:49153`，publickey + 2FA）：
`zfs snapshot DefaultMirror/psql@pre_stage1`（主回滚点）与 `systemctl restart postgresql`（真冷配对）。
但 campaign A **不删任何数据**，且每步都可由迁移的 `reverse_sql` 回退，
故快照属额外保险而非数据丢失防护。

### 回填吞吐：实测瓶颈后改为物理序扫描（数量级优化）

资产×年分块跑了 13 分钟只到 asset 72/1912，外推 **6–8 小时**。没有猜原因，直接量：

**等待事件剖面**（24 次采样，0.5 s 间隔）：**19/24 = 79% `IO/DataFileRead`**，
4 次 CPU，**仅 1 次 `WALSync`（4%）**。→ **读瓶颈，不是 WAL 瓶颈**，
所以"合并事务减少 WAL flush"和"删索引减少写放大"都不是主要杠杆。

**planning-only EXPLAIN 对比**：
| 形状 | 计划 | cost |
| :--- | :--- | :--- |
| 资产×年分块 | **Index Scan**（热索引） | 19,096 / 块 × ~38,240 块 ≈ **730M** |
| 全表一次 | **Seq Scan** | **6.4M** |
| 块范围 20,000 | **Tid Range Scan** | 27,785 / 块 × 230 块 ≈ **6.4M** |

即分块方式比顺序访问**贵约 114 倍** —— 索引驱动的离散堆访问正是 79% DataFileRead 的来源。

**改法**：新增 `--by-ctid --blocks-per-chunk N`，按**物理块范围**扫描
（`ctid >= '(b,1)'::tid AND ctid < '(b2,1)'::tid` → Tid Range Scan），
顺序读 + 有界事务 + 仍靠 `param_key IS NULL` 可续跑。
非 HOT 更新会把行移到别的块，故**重复整轮直到某轮更新 0 行**（谓词幂等，安全）。

**测试抓出的真 bug**：原实现用 `pg_class.relpages` 取块数 —— 那是**规划器估算**，
可能为 0 或过期。小表上 `range(0,0)` 为空 → 一轮都没扫 → "收敛"检查**空转通过**，
行仍是 NULL。正是"验证必须能在零覆盖时失败"那类陷阱。
改用 **`pg_relation_size(...) / 8192`**（权威物理块数），
并在收敛后**再数一次 NULL**，非零即 `CommandError` ——
"收敛"只证明"没有变化"，不证明"没有剩余"。10 项测试通过。

### 【撤回】物理序扫描不是优化，是劣化 —— 我用 cost 当成了吞吐

上面那节把 `--by-ctid` 称为"数量级优化"，**依据是 EXPLAIN cost（6.4M vs 730M）。实测否证了它。**

实测：第一个 20,000 块分块（约 756k 行）**连续跑 356 s 仍未结束**
（7 次采样 `query_start` 恒为 10:46:08，等待恒为 `DataFileRead`）。
按 ~7 min/块 × 230 块 ≈ **27 小时**，比它替换掉的资产×年方式（实测 ~5,950 行/s ≈ 8 小时）**更慢**。

原因：756k 行挤在**一个事务**里 → WAL 与约 380 万次离散索引插入一次性堆积、
缓冲区抖动、且中途不提交。资产×年分块每块约 4,300 行、频繁提交，工作集小。
**cost 是规划器估算，不是吞吐**；这正是本仓库反复记录的那类错误
（"用隔离形状 replay 代替真实相位"、"把估算当实测"）。已回退到资产×年分块。

`--by-ctid` 代码与其测试保留（小分块下可能仍有用），但**不作为默认路径**。

**另一条实测教训：杀客户端不等于停服务端。**
`kill` 掉 Python 进程后，其服务端 UPDATE **继续跑了 7 m 53 s**，
并以 `transactionid` 锁**阻塞了下一轮**。必须同时
`pg_terminate_backend(pid)`。已验证清理后 `pg_stat_activity` 无残留 active UPDATE。

**还有一个无效测量要记下来**：用 `pg_stat_user_tables.n_tup_upd` 差分测吞吐，
在长事务进行中读数为 **0** —— 该计数器**只在事务提交时刷新**。
测在飞分块的吞吐必须看 `query_start` 是否推进，不能看提交计数器。

### 独立代码审查（adversarial）抓出的真 bug 与处置

**F1 HIGH — 训练器把 NULL 键当成"无参数"，会静默污染特征。**
我原本写 `row['param_key'] or ''` → NULL 记 1 分。但未回填的**错误变体**行
（如 RSI timeperiod=7）因此被**保留**而非丢弃，还可能赢得 `drop_duplicates` 的 tie-break。
旧代码是直接排除错误变体的。我的测试全用 `.create()`（`save()` 必然填键），**从未覆盖 NULL 路径**。
已改为 **NULL → −1（排除）**：回填前宁可产出空矩阵而明显失败，也不要静默错。
新增 2 项测试，**变异证明**：改回 `or ''` → 失败，恢复 → 通过。

**F4 MEDIUM — `_session_upper_bound` 对 aware datetime 不等价。**
ORM 的 `__date` 会**先转到当前时区再取 date**，而我写的是裸 `.date()`。
在 Asia/Shanghai 下，UTC 16:00–24:00 的 instant 会**少一天**。
原测试用 15:30 UTC，恰在分歧窗口之外，所以对着 bug 也是绿的。
已改为 `timezone.localtime(as_of).date()`，并新增 20:00 UTC 的分歧窗口测试，
**变异证明**：改回裸 `.date()` → 失败，恢复 → 通过。对 `date` 输入则跨翻转完全等价（已核边界算术）。

**F2 HIGH / F3 MEDIUM — 部署时序是硬约束，不是建议。**
正确顺序：**回填完成 → 同 timestamp 重复键去重（若有）→ 部署写入端查找键修复 → 0015 → 0016**，
全程停写。理由：
- 写入端修复若早于回填：旧行键为 NULL，新查找落空 → INSERT 撞上**仍在的旧唯一约束** →
  载荷相同则 `IntegrityError`，载荷不同则又插一条重复行。
- 回填后若存在同 `(asset, timestamp)` 的多条 BBANDS/FIB_RET 行，它们会拿到**同一个 param_key** →
  `get_or_create` 的 `.get()` 抛 `MultipleObjectsReturned`，且 0015 的 `CREATE UNIQUE INDEX` 直接失败。
  现有 `dedupe_technical_indicator_session_keys` **只处理 UTC/Shanghai 时间戳翻转，不处理同时间戳载荷重复** ——
  所以若全量重复检查非零，**必须新增一个去重步骤**（保留最大 id），这是 0015 的前置交付物。

**F5 MEDIUM — 我在 0016 注释里写的"strict prefix"是错的。**
`(asset_id, timestamp, indicator_type)` **不是** `(asset_id, indicator_type, timestamp, param_key)`
的前缀（第 2、3 列互换了）。已改正注释：冗余性成立的真实理由是**所有资产维度的指标读取都同时约束
`indicator_type`**（降序探测、训练器、DQ 分块、仪表盘的 `indicator_type__in`），
故 type 作为第 2 列时 timestamp 窗口仍是索引边界；只约束 timestamp 的读取走保留的 `(timestamp)` 索引。
**但仪表盘那个曾 25 s 超时的端点必须在 0016 之后重测** —— "索引-only 可以抵消计划变化"是论证，不是测量。

**F6/F7/F8 LOW — 已修**：交叉校验样本从"前 5 个资产"改为**跨资产列表均匀取 20 个**
（稀有载荷形状不保证出现在最小 id 里）；按年缩放的完整性检查**补上年份边界**
（否则 `--start-year 2026` 对有更早 NULL 行的资产会永远报"re-run to resume"）；
删掉模型注释里残留的 `NULLS NOT DISTINCT` 说法（实际是 NULLS DISTINCT）。

**审查确认无误的部分**：`makemigrations --check` 干净；0013/markets-0016 的
`SeparateDatabaseAndState` 确实避免了 FK 全表重校验，reverse 正确；0015 先建后删无唯一性空窗；
写入路径覆盖完整（10 个 `get_or_create` 全走 `save()`，两个 `bulk_create` choke point 都赋键，
recalculate API 转发到 tasks，dedupe 只 DELETE，无 raw INSERT / `queryset.update`）；
BBANDS/FIB_RET 创建路径重推导的键与查找键相等（无漂移）；
`canonical_param_key(None, {k: v})` 与 8 个训练器调用点的存量单整数键载荷一致。
**一个潜在陷阱**：`tasks.py:660` 的 `save(update_fields=['value'])` 会推导但不持久化 param_key ——
今天无害，但若将来有人在受限 `update_fields` 下改 `parameters` 就会漂移。
**另注**：大批量 UPDATE 会清掉 visibility bit，**索引-only 扫描需要回填后再 VACUUM** 才生效。

### 并发污染：我自己反复踩的坑（实测 6x）

同一回填，两种条件下的实测吞吐：
- **干净**（20 资产探针，无其他作业）：1,738,422 行 / 337.5 s = **5,151 行/s**
- **与全量测试套件并发**：60 s 内 53,002 行 = **883 行/s**

**并发让吞吐掉到 1/6。** 全量套件本身也从常态 ~110 s 拉长到 160 s+。
这正是本仓库记录过的"长 DB 作业期间不要跑其他重活"。
→ **回填期间不得再跑测试套件或 DB 探针**；需要验证时先停回填（可续跑，零损失），验完再续。
按干净速率 5,151 行/s，剩余 ~159M 行约需 **8.6 小时** uninterrupted。

### 提前删除 jsonb 唯一索引：体积大胜，速度小胜（实测，含一次过度推断的纠正）

依据"该索引占 65% 物理索引读"把它提前删掉（迁移重排为 0015 先删唯一约束、
0016 建覆盖索引、0017 删热索引、0018 help_text；**热索引必须保留到回填之后**，
它是资产×年分块的访问路径，提前删会让每块变成顺序扫描）。

**相邻 A/B（无其他作业并发，各 60 s 提交计数差分）：**
| 项 | 前 | 后 |
| :--- | :--- | :--- |
| `pg_database_size` | **124 GB** | **84 GB（−40 GB）** |
| 回填吞吐 | 7,615 行/s | 8,828 行/s |

**速度只有 1.16x，不是我推测的 ~2.8x。** "占 65% 的读"并没有按比例变成吞吐 ——
又一次说明**归因份额 ≠ 效应量**。而且这只是**单次未换序的 60 s 配对**，
按本仓库标准属弱证据，可能落在噪声内。体积收益则是确定且巨大的。

干净吞吐 ~8,800 行/s → 剩余 ~157M 行约 **4.9 小时**（原估 8.6 小时）。

**当前占用 84 GB = 78.2 GiB**，距目标 66.23 GiB 还差 ~12 GiB。剩余杠杆：
VACUUM FULL 堆（35.12 → ~20.9 GiB，−14）、建覆盖索引（+12）、删热索引（−13）、
5 张表 VACUUM FULL（−2）→ 净 **~61 GiB，达标**。

**注意：唯一性此刻是缺失的**（0015 已删、0016 未建）。仅在停写的维护窗口内可接受，
**回填完成后必须尽快建 0016**。回滚需重建 40 GiB 索引，这是重排时序的代价。

### 索引-only 覆盖性：静态核验（不是假设）

覆盖索引列 = `(asset_id, indicator_type, timestamp, param_key) INCLUDE (value)`。

**训练器新投影** `.values('asset_id','timestamp','value','param_key')`，
过滤 `asset_id__in` + `indicator_type=` + `timestamp__gte/__lt`，排序 `(asset_id, timestamp)`：
- 四个投影列**全部**在索引内（value 由 INCLUDE 提供）→ **无需回表**。
- `asset_id` 等值/IN + `indicator_type` 等值 + `timestamp` 范围 → 三列都能成为 Index Cond。
- `indicator_type` 固定时索引序即 `(asset_id, timestamp)` → **排序也被索引满足，无需 Sort 节点**。

**DQ 分块预取**投影 `asset_id, timestamp__date, indicator_type, param_key, value`：
`timestamp__date` 是索引内列的表达式，可由索引求值 → 同样无需回表。
（但 DQ 改用 param_key 分组会消除 602 行 BBANDS 假告警，属 campaign B 的申报式 diff，见前文。）

**生效前提**：visibility map 必须 all-visible。回填的 174M 次 UPDATE 会清掉 VM 位，
所以**索引-only 只有在回填后再 VACUUM 才成立** —— 这正是 reclaim 步骤里
`VACUUM (FULL, VERBOSE, ANALYZE)` 的作用，顺序不可颠倒。

### reclaim 脚本里两个会在最关键一步炸掉的陷阱（已修并实证）

**1. `psql -c "SET statement_timeout=0; VACUUM ..."` 必然失败。**
单个 `-c` 里的多条语句被当作**一条 simple-query 消息**发送，Postgres 将其包在
**隐式事务**里，而 `VACUUM cannot run inside a transaction block`。
实证：旧写法 → `ERROR: VACUUM cannot run inside a transaction block`；
改用 `PGOPTIONS='-c statement_timeout=0' psql -c "VACUUM ..."`（单条语句不被包裹）→ `VACUUM` 成功。

**2. 管道到 `tail` 会把失败伪装成成功。**
`psql ... | tail -12; echo rc=$?` 取到的是 **tail 的退出码**，恒为 0。
实证脚本里两种写法都打印 `rc=0`，尽管旧写法明明报了 ERROR。
已改为**重定向到文件再 tail**，`$?` 才是 psql 的真实退出码。
这与本会话早先 `dq_compare.sh` 的尾部 `echo` 掩码是同一类错误。

### 小块范围扫描也不行 —— 决定性否证，瓶颈是索引写局部性而非堆读局部性

补测了之前没试过的**小**块范围（200 块 ≈ 7.5k 行，频繁提交）：
**344 行/s，比资产×年的 8,828 行/s 慢 25 倍。**

所以块范围扫描的问题**不是事务太大**（20,000 块失败时我归因于此），而是：
顺序读堆时，一个块里的行属于**许多不同资产**，于是 4 个索引的插入点**全面离散**；
资产×年分块只碰一个资产，其索引条目在索引里**相邻**、几乎全在缓存里。

**结论：主导成本是索引写局部性，不是堆读局部性。**
这也解释了为什么"删掉占 65% 读的索引"只换来 1.16x —— 读份额不是效应量。
三次优化尝试（大块范围、提前删索引的速度部分、小块范围）中两次明确失败，
**资产×年分块是实测最优，不再尝试其他形状**。已回退并继续。

### 等价性：campaign A 的代码改动已实证逐字节相同（6 个月 EU 窗口）

基线 = 改动前 HEAD 的 detached worktree `/tmp/baseline-indicator`；候选 = 当前工作树。
**两者跑在同一生产 schema 上**（0011–0015 已应用），因此隔离的是**代码**改动 ——
主要是 `Meta.ordering` 移除，它是唯一可能悄悄改变 DQ CSV 行序的那一项。

| 项 | 基线 | 候选 |
| :--- | :--- | :--- |
| `dq_rc` | 0 | 0 |
| `critical_issues` | 16 | 16 |
| `detail_rows_written` | 19974 | 19974 |
| `detail_rows_dropped` | 0 | 0 |
| CSV sha256 | 23 个 | **23 个，0 个不同** |

`dq_compare.sh` → **EQUIVALENT，compare_rc=0**（该比较器已变异证明：扰动一个哈希即 rc=1）。

**计时不可用于速度声明，必须明说**：基线跑时回填在并发，
`per_asset_prefetch=694.411 s`；候选跑时回填已暂停，`per_asset_prefetch=16.966 s`、
`per_asset_scans=28.162 s`。**两者条件不同，比值无意义。**
候选的 28.162 s 也不能与历史记录里的 34.1 s 相比 —— 那是跨会话相减，本仓库明令禁止。
速度结论必须等覆盖索引建成后，按预注册做**同条件换序相邻配对**。

**操作教训**：两个重活并发时双方都慢约 6 倍，基线因此跑了 12 分钟（干净约 58 s）。
有了 `--after-asset-id` 水位线后，正确做法是**暂停回填让等价性干净跑完再续跑**，
而不是让两者互相拖。已按此执行，回填从 asset 242 续跑。

### 全量重复键检查：**尚未取得结论**，必须在窗口内跑

只有抽样证据（0.2% 页，按规范键分组 → 0 个重复组）。全量检查
（`tmp/check_duplicate_keys.sh`，扫 BBANDS+FIB_RET 约 7% 行，**从 parameters 规范化**，
故其结论与回填进度无关、回填后无需重跑）**两次启动都在返回前被终止**：
第一次在暂停回填的情况下跑了 11 分钟仍未出结果（1800 s 超时），随后被主动停掉。

**停掉的理由是优先级判断，不是失败**：0016 只能在回填完成（数小时后）之后跑，
所以这个检查**有充裕slack**；而回填是长杆。为检查暂停回填是错误的优先级。
**正确做法：窗口内 DB 空闲时跑，几分钟即可，而不是半小时。**

若 `dup_groups > 0`，**必须在 0016 之前新增去重步骤**（每组保留最大 id）——
现有 `dedupe_technical_indicator_session_keys` 只处理 UTC/Shanghai 时间戳翻转，
**不处理同时间戳的载荷重复**。

**又一个 harness 自伤教训**：`pg_terminate_backend` 的 WHERE 文本若与自己的 LIKE 模式相同，
会**终止自己的连接**（本会话踩了两次：`%BBANDS%`、以及早先的 pkill 自匹配）。
**永远加 `pid <> pg_backend_pid()`。**

## 交接状态（2026-11-18 13:53 UTC 更新）

**目标未达成，不得声称完成。**

| 验收项 | 状态 | 证据 |
| :--- | :--- | :--- |
| 等价性（campaign A 代码改动） | **已证明** | 6 个月 EU 窗口，基线 worktree vs 工作树，23 个 CSV sha256 **0 不同** |
| 索引-only 机制 | **已证明**（3 条路径） | 真表 + VACUUM 后：训练器形状 / 逐资产降序 / DQ 日期窗全是 `Index Only Scan`，且无 Sort |
| VACUUM 是提速硬前提 | **已证明** | 未 VACUUM 的临时表 → `Bitmap Heap Scan + Sort`；VACUUM 后 → `Index Only Scan` |
| 0016 DDL | **已验证** | 临时表：语法 OK、双 NULL 键共存、相同真实键被拒 |
| 占用 ≤66.23 GiB | **未达成：78.2 GiB（84 GB）** | 起点 132.46 GiB |
| 读取 ≥2x（按相位） | **未测量** | 机制已证明，数字待覆盖索引 |
| 全量重复键检查 | **未取得结论** | 仅 0.2% 抽样为 0 |
| 回填 | **进行中 ~30%** | asset 524/1912；抽检 asset 300/400/500 均 0 个 NULL 键 |
| 全量测试 | **1306 OK** | 含 0011–0018 全链 |

**在飞作业（均 setsid 脱离，可跨会话存活）**：
1. 回填 `--after-asset-id 486`，`/tmp/bf_full.log`。**续跑**：
   `bash tmp/launch_detached.sh <最后日志里的 asset id>`。
2. 手动 `VACUUM (ANALYZE, VERBOSE)`，`/tmp/vacuum_indicator.log`。
   实测处于 `vacuuming indexes`、**`index_vacuum_count=0`（第 1/4 个索引，已 17+ 分钟）**，
   WAL-bound（无 SLOG）。**按此速率 4 个索引 + 堆清理预计 1–1.5 小时。**
   **⚠️ 队列里的 autovacuum reloptions 实际上没有落地** ——
   `ALTER TABLE … SET (…)` 需要 `ShareUpdateExclusive`，与 VACUUM 冲突，
   20 s 的 `lock_timeout` 到期后放弃；`pg_class.reloptions` 仍为 `(none)`。
   **必须在 VACUUM 结束后重跑** `bash tmp/apply_table_autovacuum.sh` 并核实
   `reloptions` 非空，否则 autovacuum 仍会跟不上、死元组会重新涨回 20%。
   （等待期间死元组从 47.2M 涨到 51.6M，正说明这一点。）
3. **实测速率**：VACUUM 并发期间 **1,492 行/s**（更早一次 2,814）、退化时 1,775、最好 8,828。
**即等待期间吞吐没有改善，反而更低** —— VACUUM 的收益只在**完成时**才兑现
（死元组回收到 0、索引扫描变短），此前它只是在争 I/O。
死元组在等待期间仍在涨（50.8M），因为回填持续制造新的。
**不要用资产/分钟估 ETA**（单资产 1.1 万–11.4 万行，跨 10 倍）。

**下一步（按序，每步单独批准）**：
1. 回填跑完（`remaining_null=0`）。
2. DB 空闲时 `bash tmp/check_duplicate_keys.sh`（几分钟；非零则先加去重）。
3. 部署写入端查找键修复。
4. `bash tmp/window.sh index`（0016 覆盖唯一索引 → 0017 删热索引 → 0018）。
   **唯一性此刻缺失，须尽快闭合。**
5. `bash tmp/window.sh reclaim`（VACUUM FULL + 5 表 + 台账）。
6. 按预注册 `3cfb7668905e` 做同条件换序相邻配对的逐相位速度 A/B。
7. campaign B（SMA-60 + DQ 变体键 + 仪表盘 DISTINCT ON）——**在此之前不要改 `SMA_PERIODS`**。
8. 任务 #11：`export_documentation_facts` 重生成（`commands.md` 因新增命令已过期）、
   BACKLOG 索引、`check-archive-verbatim.py`、ruff DTZ。

**仍需业主 NAS 会话**（SSH `192.168.31.8:49153`，publickey+2FA）：
`zfs snapshot DefaultMirror/psql@pre_stage1` 与 `systemctl restart postgresql`（真冷配对）。
容量已由 netdata 实测解除阻塞：池可用 2,335 GiB、psql 数据集物理 70.8 GiB（lz4 ≈1.75x）。

## 原始交接（2026-11-18 12:13 UTC）

| 验收项 | 状态 | 证据 |
| :--- | :--- | :--- |
| 等价性（campaign A 代码改动） | **已证明** | 6 个月 EU 窗口，基线 worktree vs 工作树，23 个 CSV sha256 **0 不同**；critical_issues=16、detail_rows_written=19974、dropped=0 两侧一致 |
| 占用 ≤66.23 GiB | **未达成：78.2 GiB（84 GB）** | 起点 132.46 GiB，已降 41% |
| 读取 ≥2x（按相位） | **未测量** | 需覆盖索引建成后的同条件换序配对 |
| 全量重复键检查 | **未取得结论** | 仅 0.2% 抽样为 0；全量两次启动均在返回前被主动停止 |
| 回填 | **进行中 14%** | 24,386,684 / 174,015,330 行；asset 259/1912 |
| 全量测试 | **1306 OK** | 含 0011–0018 全链 |

**在飞作业**：`bash tmp/backfill_full.sh 254`（后台）。干净吞吐 ~8,800 行/s，
剩余 ~150M 行约 **4.7 小时**。**可续跑**：中断后用
`bash tmp/backfill_full.sh <最后日志里的 asset id>` 续，水位线已测试。
若会话结束导致客户端被杀，**服务端 UPDATE 可能继续跑**——
续跑前先 `bash tmp/stop_backfill.sh`（它会 `pg_terminate_backend`，且排除自身 pid）。

**下一步（按序，每步单独批准）**：
1. 等回填跑完（`remaining_null=0`）。
2. DB 空闲时跑 `bash tmp/check_duplicate_keys.sh`（几分钟）。非零则先加去重步骤。
3. 部署写入端查找键修复（BBANDS/FIB_RET，已实现并测试）。
4. `bash tmp/window.sh index` → 0016 覆盖唯一索引 + 0017 删热索引。**唯一性此刻缺失，须尽快闭合。**
5. `bash tmp/window.sh reclaim` → VACUUM FULL + 台账（脚本两个陷阱已修并实证）。
6. 按预注册 `3cfb7668905e` 做同条件换序相邻配对的逐相位速度 A/B。
7. campaign B：SMA-60 落库 + DQ 变体键修复 + 仪表盘 DISTINCT ON 修复（申报式 diff，业主裁决）。
   **在此之前不要改 `SMA_PERIODS`** —— DQ 导入该常量，会污染 campaign A 的等价性门。
8. 任务 #11 的文档/CI 义务（重生成 metrics.md、发现地板 1306、BACKLOG 索引）。

**仍需业主自己的 NAS 会话**（SSH 192.168.31.8:49153，publickey+2FA）：
`zfs snapshot DefaultMirror/psql@pre_stage1`（主回滚点）与 `systemctl restart postgresql`（真冷配对）。
容量已由 netdata 实测解除阻塞：池可用 2,335 GiB，psql 数据集物理 70.8 GiB。

### 回填的存活问题已修：必须 `setsid` 脱离，否则每次会话暂停就被杀

前两次会话暂停都把回填杀了（第一次停在 asset 254、第二次停在 473 ——
不过第二次之前已推进到 **44.1M/174M 行 = 25%**）。
根因：用工具的 background 模式启动时，进程仍挂在 wrapper 的进程组里，会话结束即被回收。

**已改为 `setsid nohup ... > log 2>&1 < /dev/null &`**，并**验证**：
进程 `SID == PID`（自成一个 session），wrapper 退出后仍存活并继续推进。
**用普通重定向而不是 `tee`** —— 本仓库记录过 `exec > >(tee -a log)` 在 setsid+nohup 下
会以 SIGPIPE 退出（141）。

**注意 PPID 不一定是 1**：父 shell 退出后才 reparent 到 init；
判断脱离成功看 **SID == PID**，不要看 PPID。

续跑命令：`bash tmp/launch_detached.sh <最后日志里的 asset id>`（内部会先停旧的，
停的时候会 `pg_terminate_backend` 且排除自身 pid）。

### 文档/CI 义务进展

- **CI 发现地板 1272 → 1306**（`.github/workflows/ci.yml:246`），并写明这 34 项新测试的构成。
- **`CONTRIBUTING.md:36` 的过期数字 1023/87 → 1306/100**。
- 仍待做：`export_documentation_facts` 重生成（`metrics.md` 因新增 `param_key` 已过期），
  需要 DB 扫描，留到窗口内做以免与回填争 I/O。

### 0016 的 DDL 已在临时表上验证（三条语义全过）

在 TEMP 表上跑**与 0016 完全相同的语句**（只换表名），零生产影响：

| 检验 | 结果 |
| :--- | :--- |
| `CREATE UNIQUE INDEX … INCLUDE (value)` 语法 | **CREATE INDEX 成功** |
| 两条 param_key **NULL** 的同 (asset,type,ts) 行 | **共存（INSERT 0 2）** —— 回填前 SMA/EMA 六变体必需 |
| 两条**相同真实键** | **被拒**：`ERROR: duplicate key value violates unique constraint`，DETAIL 打出完整键 |

即：`NULLS DISTINCT`（默认）语义正确 —— 回填前合法、回填后强制唯一。
**这消除了 0016 在窗口内因语法或语义出错而失败的风险**，不是靠读文档推断的。

（又一次管道掩码：`psql … | tail; echo rc=$?` 取到 tail 的 0。证据是 ERROR 文本本身。）

### 索引-only 机制：**实测三条路径全部成立**，且证明 VACUUM 是硬前提

先得到一次**否定结果**再纠正它，两次都写下来：

**第一次（临时表）**：训练器形状 → `Bitmap Heap Scan + Sort`，**不是** index-only。
我此前"覆盖索引即得 index-only"的说法**未经证实且在此测试下是错的**。

**但那是测试的构造性缺陷**：Postgres **不能 VACUUM 临时表**，新建临时表没有 all-visible 页，
index-only 在原理上就不可能被选中。不是设计问题。

**第二次（真实表 + `VACUUM (ANALYZE)`，100 万行，随后 DROP 并核实 `pg_class` 计数为 0）**：

| 路径 | 计划 |
| :--- | :--- |
| A 训练器（asset IN + type= + 时间窗 + 投影 4 列） | **Index Only Scan**，**无 Sort 节点** |
| B 逐资产降序 `LIMIT 12` | **Index Only Scan Backward**，无回表 |
| C DQ 日期窗（全部变体，无 param_key 谓词） | **Index Only Scan** |

**结论**：覆盖索引 `(asset_id, indicator_type, timestamp, param_key) INCLUDE (value)`
确实让三条验收路径都成为索引-only，且排序也被索引满足。

**更重要的连带结论：`VACUUM` 是读取提速的硬前提，不只是省空间的手段。**
回填的 1.7 亿次 UPDATE 会清掉 visibility map 位；**不 VACUUM，读取会退回 Bitmap Heap Scan + Sort**
（即实测的第一次结果）。所以 reclaim 步骤里的 `VACUUM (FULL, VERBOSE, ANALYZE)`
必须与建索引同批完成，**顺序不可颠倒**，否则 2x 目标拿不到。

### 工作树卫生：有两处**不属于本工作**的改动，提交时不要顺手带上

`git status` 里混着另一个会话（ETF ICIR）的产物：
- **modified** `docs/reports/evidence/etf-rotation-measurements.jsonl` —— 不是我改的
- **untracked** `docs/reports/sensitivity/etf-icir-verdict-2026-11-18-4af198d46063.md`
- **untracked** `docs/reports/sensitivity/etf-icir-verdict-2026-11-18-rev2-7cb8179bc078.md`

本工作自己的改动：
- modified：`.github/workflows/ci.yml`、`CHANGELOG.md`、`CONTRIBUTING.md`、
  `apps/analytics/{models,serializers,tasks,views,tests}.py`、
  `apps/analytics/management/commands/backfill_technical_indicators.py`、
  `apps/markets/models.py`、`apps/prediction/{historical_features,tasks_lightgbm}.py`
- untracked：`indicator_params.py`、`backfill_indicator_param_key.py`、
  迁移 `analytics/0011–0018` + `markets/0016`、
  四个测试文件、`docs/reports/preregistration/technical-storage-stage1-2026-11-18.md`
- `tmp/` 下 140 个脚本全部 gitignored，是操作工具，不入库。

**注意 `apps/analytics/tests.py` 与 `apps/analytics/views.py` 也被本工作改过**
（幂等性测试、screener 显式排序），不是只有新文件。

### 第四次分块形状尝试也失败：id 区间分块 3,725 行/s（基线 8,828）

我先前实测"5,000 个连续 id 只含 1 个资产"，据此推断**按 id 区间分块**能既保住
资产局部性、又只用 pkey（从而可以提前删掉 13 GiB 热索引）。**实测否证：**
id 区间分块 **3,725 行/s**，比资产×年的 **8,828 行/s 慢 2.4 倍**。

原因：id 的**连续性**不等于**物理相邻性**。表经过多轮"删除+重插"式回填，
`min(id)` 已达 **949,767,216** 而表只有 174M 行 —— 约 5.4 倍 churn ——
所以 id 序与堆序并不对应。而资产×年的分块查询能走热索引
`(asset_id, timestamp, indicator_type)`，返回顺序对缓存友好。

**决定性结论：资产×年分块 + 热索引是实测最优，热索引不是"冗余"而是最佳访问路径。**
因此**绝不能在回填完成前删它**；迁移顺序 0016（建覆盖索引）→ 0017（删热索引）必须保持。
至此四次形状/索引优化尝试中三次明确失败（大块范围、小块范围、id 区间），
一次部分成功（提前删 jsonb 唯一索引：**省 40 GB，速度仅 1.16x**）。
**不再尝试任何形状优化。**

### 回填会拖慢自己：47M 死元组把吞吐从 8,828 打到 1,775 行/s

在**没有任何其他作业**的情况下实测，回填吞吐从 8,828 掉到 **1,775 行/s**（5 倍）。
查 `pg_stat_user_tables` 找到原因：

| 指标 | 值 |
| :--- | :--- |
| `n_live_tup` | 172,902,396 |
| **`n_dead_tup`** | **47,128,122（21.4%）** |
| `last_autovacuum` | **2026-11-04**（两周前） |
| `last_vacuum` | NULL |

即：回填自己的 4700 万次 UPDATE 产生等量死元组，而 **autovacuum 没跟上**
（21.4% 刚过 20% 阈值，且长跑的短事务队列让它拿不到时机），
于是每次索引扫描都要穿过数千万条死条目 —— **回填在拖慢自己，且会持续恶化**。

**处理：普通 `VACUUM (ANALYZE)`，不是 FULL。**
普通 VACUUM 只取 `SHARE UPDATE EXCLUSIVE`，**可与回填的 `ROW EXCLUSIVE` UPDATE 并发**；
VACUUM FULL 会阻塞整表重写，而且把页填满 100% 会让剩余更新只能扩堆 —— 更糟。
已用 `setsid` detached 启动，与回填并行跑。

**这条应该写进窗口手册**：大规模 UPDATE 之前先确认 autovacuum 能跟上，
或在中途手动 VACUUM 一次，否则吞吐会随死元组单调下降。

**根因不是"autovacuum 被关掉"，而是它跟不上。** 实测：
`pg_class.reloptions` **为空**（没有按表禁用）、`autovacuum=on`、
`max_workers=3`、`naptime=60s`、`scale_factor=0.2`、`cost_limit=-1`（即用 `vacuum_cost_limit=200`）。
即它是开着的，只是被 cost 节流、且与回填抢 I/O，35 GB / 47M 死元组的扫描根本追不上。

**关键差别**：**手动 `VACUUM` 用 `vacuum_cost_delay=0`（不节流），autovacuum 用
`autovacuum_vacuum_cost_delay`**。所以手动 VACUUM 是这里正确的干预手段，
而不是去改全局 autovacuum 参数。

**对将来大规模 UPDATE 的建议（写进窗口手册）**：要么中途手动 VACUUM，
要么给这类表设 `autovacuum_vacuum_scale_factor`（如 0.02）让它更早更频繁地触发；
否则吞吐会随死元组单调下降，这是本会话把 8,828 拖到 1,775 行/s 的原因。

### 【撤回】VACUUM 没有被中断 —— 是我的检查用错了 pgrep 模式

我先前判断"VACUUM 被中断"（依据：wrapper 日志缺 `vacuum_rc`、`last_vacuum` 仍为 NULL），
**这个结论是错的**。真相：`pgrep -f 'VACUUM (ANALYZE, VERBOSE)'` 里
**括号是正则元字符**，模式匹配不到字面文本，于是 `pgrep` 恒失败、被误读为"进程已结束"。
VACUUM 从头到尾都在跑（已 8+ 分钟）；`last_vacuum` 为 NULL 只是因为它**还没跑完**。
我因此又启动了一个重复的 VACUUM（会阻塞在锁上），已立即 kill 掉第二个。

**规则**：`pgrep -f` 的模式是**正则**，带 `(` `)` `[` `]` `{}` 的模式必须转义，
或改用 `ps -eo args | grep -F '<字面量>'`。本会话已因自匹配/正则误匹配踩了多次。

**另一条**：`setsid nohup bash script.sh` 里若 script 被杀，其 psql 子进程会继续跑，
但脚本不会打印 rc —— 这会让"完成"与"中断"看起来一样。
**后台长任务应把最终执行体本身 setsid，而不是包一层 shell**。

### 回填正确性抽检（运行中，不是等跑完才验）

对已跑过的 3 个资产直接查（走热索引，便宜）：

| asset_id | 行数 | `param_key IS NULL` |
| :--- | :--- | :--- |
| 300 | 11,481 | **0** |
| 400 | 104,482 | **0** |
| 500 | 114,231 | **0** |

即**已完成资产的键是完整的**，不只是"跑得快"。行数跨度 1.1 万–11.4 万，
这也解释了资产/分钟的速率为什么波动很大 —— **用"资产数/分钟"估 ETA 会失真**，
应按行数算。

`--after-asset-id` 水位线的语义是安全的：资产按 id 顺序整块完成，
所以跳过已完成 id 不会漏行；万一某资产只做了部分年份，重跑该 id 也是幂等的。

### VACUUM 的精确进度与"是否该 kill"的算术

`pg_stat_progress_vacuum` 实测：`phase=vacuuming indexes`、
`heap_blks_scanned = heap_blks_total = 4,603,165（100%）`、`heap_blks_vacuumed=0`、
`num_dead_tuples = 47,167,769`、`index_vacuum_count = 0`。
即**堆已扫完，正在清理 4 个索引里的死条目**（约 4,700 万 × 4 ≈ 1.88 亿条），
`index_vacuum_count=0` 说明还在第 1 个索引。索引清理没有细粒度进度视图，只能等。

**为什么让它跑完而不是 kill：**
- 让它跑完：再花 ~30 min 争 I/O（损失约 3M 行进度），之后 1.2 亿行按恢复后的
  ~8,800 行/s ≈ **3.8 小时**。
- 现在 kill：死元组留在 5,000 万，回填保持 ~2,800 行/s → 1.24 亿行 ≈ **12.3 小时**。
→ **让它跑完。**

**回填速率实测（非估算）**：VACUUM 并发期间 **2,814 行/s**；退化时 1,775；最好 8,828。
**不要用"资产/分钟"估 ETA** —— 单个资产行数从 1.1 万到 11.4 万，跨度 10 倍。

### VACUUM 慢的真因：**WAL 受限**，不是堆 I/O

`pg_stat_activity` 实测（单一 VACUUM + 2 个 parallel worker）：
`client backend` 等 **`LWLock/WALWrite`**，worker 等 **`IO/WALSync`** 与 `LWLock/WALWrite`。
即 **VACUUM 卡在 WAL 同步上**，而不是读堆。
原因：清理索引条目要写 WAL，而这台 NAS 的 ZFS 池 **没有 SLOG**，
WALSync 落在机械盘上 —— 这与本会话早先回填时"WALSync 只占 4%"不同，
因为 `VACUUM` 的 WAL 模式与 `UPDATE` 不同（索引清理 + ANALYZE 的 WAL 更密集）。

**教训**：在这台机器上，任何"扫全表 4 个索引"的维护操作都会 WAL-bound；
预期时间要按 WAL 带宽估，不能按磁盘顺序读带宽估。

**⚠️ 对窗口计划的直接影响：`VACUUM FULL` 的 15–40 分钟估计很可能严重偏低。**
VACUUM FULL 要**重写整表 + 重建全部索引**，WAL 量比普通 VACUUM 还大；
在无 SLOG 的机械盘池上可能是**数小时**。
**开工前先 `SET wal_compression=on`（已有此计划）**，并预留远大于 40 分钟的窗口；
停机时长要按这个重新向业主报备，不能沿用原估算。

**另修**：发现**第二个被阻塞的 VACUUM**（我先前 `relaunch_vacuum.sh` 遗留，
在 relation 锁上等了 6 分钟）。它会等第一个跑完后**再全表重扫一遍**，纯浪费。
已 `pg_terminate_backend` 取消，现只剩一个。

### 尚未做（需业主批准的生产写入）

迁移 0013-0015（覆盖唯一索引 + 删冗余索引 + `null=False`）、生产回填、
VACUUM FULL/REINDEX、逐字节等价性证明、按相位 A/B、campaign B（SMA-60 与缺陷修复）。
**索引列序（`timestamp` 与 `param_key` 谁在前）仍须在新索引存在后由 planning-only EXPLAIN 定。**

---

## 2026-11-19 · campaign A 的逐相位 A/B：测了，两臂都是 ~1.0x，原因是**臂设计**而非实现

预注册 `3cfb7668905e` 要求"同条件换序相邻配对"。臂的定义由 `tmp/dq_run.sh` 给出：
**A = `/tmp/baseline-indicator`（ff67842，改造前代码），B = main 工作树**，
两臂打**同一个数据库**。仓库中不存在把新读取路径做成开关的配置项，所以这是唯一可执行的臂定义。

**先给结论**：campaign A **没有**改动 DQ 与回测引擎的代码，因此这两个已登记相位
测出来必然是 ~1.0x。这不是回归，是"拿没改的代码去证明改动的收益"。

### 归零证明（不是推测）

| 检查 | 结果 |
| :--- | :--- |
| `diff baseline/main apps/core/.../validate_data_quality.py` | **0 行**（逐字节相同） |
| `diff -q baseline/main apps/backtest/tasks.py` | **IDENTICAL** |
| 决策日 `INDICATOR_PREFETCH_STATS`（两臂每次） | `installed=0, cache_hit=0, fallback=0` —— 该路径根本没被调用 |

### DQ-6M（2026-05-01..2026-11-06，`--effective-universe-only`）

| run | 臂 | `per_asset_prefetch` | `per_asset_scans` | `per_asset_loop_body` |
| :--- | :--- | ---: | ---: | ---: |
| a1 | baseline（**冷**） | 78.218 | 90.383 | 12.165 |
| b1 | main | 15.831 | 27.827 | 11.996 |
| b2 | main | 16.397 | 27.893 | 11.496 |
| a2 | baseline（热） | 17.101 | 29.257 | 12.156 |
| b3/b4/b5 | main（热） | 16.661 / 16.541 / 16.181 | — | — |
| a3/a4/a5 | baseline（热） | 16.877 / 16.209 / 15.933 | — | — |

热态中位：main **16.397**，baseline **16.543** → **1.01x**。未改动相位 `loop_body` 全部 ~12 s。

### DQ-2Y（2024-11-13..2026-11-12）

| run | 臂 | `per_asset_prefetch` | `per_asset_scans` | `per_asset_loop_body` |
| :--- | :--- | ---: | ---: | ---: |
| a1 | baseline（**冷**） | 181.895 | 225.988 | 44.094 |
| b1 | main | 76.354 | 121.060 | 44.706 |
| b2 | main | 77.739 | 121.666 | 43.927 |
| a2 | baseline（热） | **76.926** | **120.379** | 43.453 |

热态：main 77.047 vs baseline 76.926 → **0.998x**。`loop_body` 四轮 43.5–44.7 s，稳定。

**这张表是本文件最重要的一条纠正**：a1 的 181.9 s 与 b1 的 76.4 s 差 **2.4x**，
若只跑 A,B 两轮就会把它写成"代码提速 2.4x"。**同一 baseline 的第二次运行回落到 76.9 s**
—— 全部是 shared_buffers 预热。6M 的 78.2 → 15.8 同理（4.8x）。**冷启动比值不得作为代码收益引用。**

### 决策日（2026-11-06，`replay_decision_day`，回滚事务内）

| run | 臂 | `elapsed_seconds` |
| :--- | :--- | ---: |
| a1 | baseline | 4.836 |
| b1 | main | 5.244 |
| b2 | main | 4.975 |
| a2 | baseline | 5.176 |

中位：A **5.006**、B **5.110** → **0.98x**。总时长已接近预注册写明的
"~3,000 次非指标查询 ≈ 5.1 s 地板"，且 prefetch 全为 0 —— **该臂无法承载指标读取的收益**。

### 等价性（顺带重新证明，且在 VACUUM 之后的库上）

| 窗口 | 四个 run 的 CSV 集合 sha256 | 结论 |
| :--- | :--- | :--- |
| DQ-6M | `180b32d76bd30a1c` ×4 | 逐字节相同 |
| DQ-2Y | `818b466caa2b0743` ×4 | 逐字节相同 |

### 因此

- **campaign A 的"≥2x 逐相位"验收项按当前预注册不可达**：已登记的三个臂里，两个的代码
  逐字节未变，第三个（决策日）不走该路径。要拿到真实比值，必须把 DQ 的读取路径**确实改掉**
  —— 那正是 campaign B，见 `technical-storage-campaign-b-2026-11-19.md`。
- **能归给本次改造的证据仍然是 schema 侧的**：visibility map 100%、`Heap Fetches: 0`、
  整表同口径扫描 202 s → 78 s（2.6x）、有界探针 21x/423x，以及占用 132.46 → 51.2 GiB。
- **不能做的事**：把 campaign B 的收益记到 campaign A 头上，或用冷启动首轮比值充当代码收益。