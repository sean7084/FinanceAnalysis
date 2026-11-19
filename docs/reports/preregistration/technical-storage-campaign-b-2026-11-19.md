# 预登记：指标表存储改造 Campaign B（SMA-60 / DQ 变体键 / 仪表盘 DISTINCT ON）

- 登记时间：2026-11-19，**在任何 campaign B 测量之前**。
- 与 `technical-storage-stage1-2026-11-18.md`（campaign A）**分开**。campaign A 的判据是
  **逐字节相同**；campaign B **故意改变输出**，判据是**申报式 diff**，并须经业主裁决。
- 上游依据：业主已明确选择"**真的计算并存储 SMA-60（会改变策略输出）**"。

## 三项改动

1. **SMA-60 落库**：`SMA_PERIODS` 由 `(5,10,20,50,100,200)` 改为
   `(5,10,20,50,60,100,200)`。历史行用 `backfill_technical_indicators
   --technical-indicators SMA --sma-periods 60 --insert-only` 补齐：
   **不删任何存量行**，靠覆盖唯一索引把已存在的键当冲突跳过。
2. **DQ 变体键**：`_write_technical_indicator_continuity_gaps` 的分组键由
   `(indicator_type, jsonb 全量 payload)` 改为 `(indicator_type, param_key)`。
   BBANDS/FIB_RET 的载荷含**计算输出**，永远不等于"只有输入"的期望载荷，
   因此每个期望日期都被记成缺失 —— 这就是 **602 行 `missing_pct=1.000000` 假告警**的成因。
   同时把扫描投影从 `parameters` 换成 `param_key`，使 DQ 预取可走**索引-only**。
3. **仪表盘 DISTINCT ON**：`distinct('asset_id','indicator_type')` 对 SMA 而言只保留
   **任意一个**变体；抽到 5/10/20/100/200 时会被 `timeperiod not in {50,60}` 丢弃，
   于是看板**完全没有 SMA**。改为把 `param_key` 纳入 DISTINCT ON，按变体各留最新一行。

## 预期输出变化（申报式 diff 的申报部分）

| 变化 | 预期方向 | 判据 |
| :--- | :--- | :--- |
| SMA 行数 | **增加** ~6.2M（每个资产多一个 timeperiod=60 变体） | `param_key='timeperiod=60'` 计数从 0 起 |
| `technical_indicator_snapshot_continuity_gaps.csv` | BBANDS/FIB_RET 的 `missing_pct=1.000000` 行**消失** | 602 行应全部归零 |
| 其余 CSV | **不变** | 逐字节相同 |
| 仪表盘 `sma_60` | 由偶发为空 → **稳定为 timeperiod=60 的值** | 单元测试 + 抽样 |

**任何不在这张表里的 CSV 变化都算未申报差异，必须逐行解释后才能采信。**

## 速度判据（campaign B 自己的一对）

campaign A 的 A/B 是"baseline 代码树 vs main 代码树"，两臂**共用同一份新 schema**，
因此无法把 schema 收益算到代码头上（实测两臂都是 ~1.0x，DQ 命令逐字节相同）。
campaign B 的 DQ 改动**真的改了读取路径**，所以可以做成真正相邻的配对：

- **A 臂**：campaign B 之前的 DQ 代码（投影 `parameters`，分组用 jsonb 全量键）。
- **B 臂**：campaign B 的 DQ 代码（投影 `param_key`，分组用 param_key）。
- 同一棵树、同一数据库、同一窗口、相邻、换序重复。
- 判据：`per_asset_prefetch` 相位配对中位比，并同报告记录**未改动相位**
  （`per_asset_loop_body`、`null_field_scans`、`universe_and_audit_writes`）的波动作噪声底。
- **冷/热必须声明**：shared_buffers 无法在本 harness 内清空（需 NAS 重启，业主 2FA）。
  因此只能做**热配对**；冷结论一律不宣称。

## 已知限制（不得事后放宽）

- SMA-60 落库后 `pg_database_size` 会增加（约 +0.4–0.6 GiB），
  **campaign A 的 51.2 GiB 需在 B 之后复测**，判据仍是 ≤66.23 GiB。
- DQ 的 `technical_indicator_reconciliation_audit`（默认关闭）**不改**：
  其重算比对需要区分变体，改成 canonical 键会在多输出指标上把多个 dict 条目折叠成一个。
  该路径不在本次判据内，缺陷另行登记。
- 训练器臂仍未登记（产出 5,703,455 行 RSI 的 argv/窗口未取得），不具裁决效力。
- **禁止**引用 campaign A 的冷启动大比值（6M 4.8x / 2Y 2.4x）作为代码收益 ——
  实测那是缓存预热（同一 baseline 的第二次运行即回落到 ~77 s）。

## 结果写入

台账逐行带本文件的 `sha256[:12]`。**任何编辑即作废本登记 → 另写 rev2。**
