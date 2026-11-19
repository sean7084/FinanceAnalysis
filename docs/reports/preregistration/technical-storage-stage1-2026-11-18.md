# 预登记：指标表存储改造与读取提速（Stage 1）

- 登记时间：2026-11-18（任何 A/B 运行之前）
- 对象：`analytics_technicalindicator`。改造 = `parameters` jsonb 退出唯一键、改由 `param_key`
  承担；新建覆盖唯一索引；删除冗余索引；VACUUM FULL / REINDEX 回收膨胀。
- **两个campaign必须分开，不得合并宣布加速比：**
  - **A（本文件登记）**：存储与读取路径改造。等价性判据 = **逐字节相同**。
  - **B（不在本文件内）**：SMA-60 落库 + BBANDS 缺陷修复 + 仪表盘 DISTINCT ON 修复。
    这些**改变输出**，属申报式 diff，须另立预注册与基线，并经业主裁决。

## 已实测基线（2026-11-18，改造前）

- `pg_database_size` = 142,225,292,311 B = **132.46 GiB**。
- 热表 heap **35.12 GiB**（4,603,165 页）、indexes **76.03 GiB**；其余关系合计 ~21.4 GiB。
- `TABLESAMPLE SYSTEM (0.2)`：345,949 行 / 0.2% × 4,603,165 页 = **37.6 行/页**；
  `avg_data_bytes` = **77.99**，`avg_param_bytes` = **38.64**。
- 元组 = 24 B 头 + 78 B 数据 + 4 B ItemId = 108 B → 理论 75.6 行/页 → **膨胀因子 2.01x**。
- 推算总行数 ≈ **173M**（`reltuples` 179,093,920 偏高约 3.5%；精确 COUNT 未取得，也不需要）。
- 单资产普查：94,475 行 / 19 类型；SMA 与 EMA 各 19,835 行、各 6 个变体 {5,10,20,50,100,200}；
  **不存在 timeperiod=60**；BBANDS 3,350 行含 3,346 个不同参数字典、平均 110 B。
- planning-only EXPLAIN：降序探测的 `Index Cond` 只含 `(asset_id, indicator_type)`，
  而 `timestamp::date` 与 jsonb 判断均为**回表后 Filter**。

## 判据（唯一允许的结论来源）

1. **占用**：`pg_database_size` ≤ **66.23 GiB**（逻辑字节）。ZFS `used`/`compressratio` 一并记录，
   但**不作为判据**（压缩收益只落在物理层）。
2. **速度**：每个 acceptance 相位各自满足 **配对中位比 ≥ 2.0** 且 **|效应| > 2 × 该相位噪声底**。
   任一相位不满足即如实记为未达标，**不得用其他相位的收益抵补**。
3. **等价性（A campaign）**：DQ 全部 CSV 的 sha256、决策日 payload 哈希、
   **逐资产逐签名有序 id 比对 0 不符**（`fallback=0`）全部成立。
   计数相同不算等价 —— #139 有"同计数不同内容"的前科。
4. **噪声底**：同代码同会话重复同一形状的差值，逐相位报告。
   **禁止跨会话相减求分量成本；禁止跨工作负载搬运单查询常数。**
5. **未改动相位的波动必须同报告记录**（前例：`universe_and_audit_writes` 7.4 s vs 77.8–83.5 s；
   `default_bucket_scans` 206.7/217.3/326.6/387.3 s）。这是读者能信或弃这个数的理由。

## 臂与窗口（argv 原文须进台账）

- **DQ-6M**：`manage.py validate_data_quality --start-date 2026-05-01 --end-date 2026-11-06
  --effective-universe-only --output-dir reports/<label>`。相位：`per_asset_scans` 及其
  `technical` 预取子计时器。
- **DQ-2Y**：同上，`--start-date 2024-11-13 --end-date 2026-11-12`。
  总体 = 时点并集资产（前例 687），**不是全库 1,912**。
- **决策日**：`2026-11-06` 候选日重放，**在回滚事务内、autocommit off** ——
  该路径会回填交易决策，不是只读的。相位：`prime_indicator_candidates` + hydration 子计时器，
  并报告 `INDICATOR_PREFETCH_STATS` 的 installed/cache_hit/fallback。
- **训练器臂不在本文件登记**：产出 5,703,455 行 RSI 的那次运行的 argv 与窗口尚未取得，
  须先从 `tmp/retrain/` 日志恢复并另立预注册，否则该臂不具裁决效力。

## 配对与"冷"的定义

- 最少 **A,B,B,A 相邻、换序、重复**；链路上其他作业全停（beat/worker 停、catchup 锁持有、
  仪表盘预热关），并以 `pg_stat_activity` 核实。
- 冷配对需重启 Postgres（业主批准）。**必须声明**：重启清 `shared_buffers` 但**保留 ZFS ARC**，
  故本仓库的"冷"= sb 冷 / ARC 温。真正 ARC 冷需重启 NAS 或临时压 `arc_max`；
  头条结论应含一对 NAS 重启冷配对，否则只标注为 sb 冷。
- 计时器惰性须证明：禁用（no-op）vs 启用做相邻配对，|Δ| ≤ 噪声底，且断言查询数不变。

## 已知限制（不得事后放宽）

- **DQ 整相位不会 2x**：`per_asset_scans` 含 OHLCV 等 7 个其他读取（11.2 s）与 39–41 s 循环体，
  本次不动 → 整相位要 2x，`technical` 部分需 ≥2.4x。
- **逐资产 OHLCV 连续性相位读的是另一张表，不做任何承诺。**
- **禁止引用全区间加速比**（`apps/core/db_scans.py:71-72`）：全区间门禁 90 分钟仍跑不完
  （`VALIDATE_RC=124`、`WALL_SECONDS=5401`）。
- 决策日另有 ~3,000 次非指标查询（~1.7 ms/次 ≈ 5.1 s 地板），整日不会 2x。
- 索引-only 扫描会随日常写入弄脏 visibility map 而静默退化 → 验收用历史窗口并记录 all-visible 比例。
- 占用估算的两个不确定项：堆膨胀回收量（已实测 2.01x）与低基数索引 btree 去重是否生效（未实测）。
  即使去重完全失效，估算仍落在 ~57 GiB。
- 业主已决定**不删除任何数据**，且 B campaign 会**增加** ~6.2M 行 SMA-60。两者互相拉扯，
  占用结论须在 B 之后复测。

## 结果写入

台账逐行带本文件的 `sha256[:12]`（`apps/etf_rotation/evidence.py:preregistration_digest`）。
**任何编辑即作废本登记 → 另写新文件（rev2），不得原地修改。**