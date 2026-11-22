# KB sync draft — CSI500 vs CSI1000 executable-universe negative result (2026-11-22)

> **Staged for owner application.** These are the exact texts to append to the investment-kb
> (`C:\Users\sean_\OneDrive\财务\investment-kb`). Applying them is a **[PW] KB write** — owner
> approval required; this file is only the prepared content. Append-only, dated. Source of
> evidence: `docs/reports/stock/csi500-vs-csi1000-comparison-2026-11-22.md` (FinanceAnalysis,
> PR #227), prereg digest `cabcf49a3c67`, 60 ledger rows in
> `docs/reports/evidence/stock-model-measurements.jsonl`.

## 1) Append to `knowledge/quantative.md` — §二.3 (CSI500 vs CSI1000 executable-universe)

Add a new dated rule section (mirrors the `##### from {日期}` + Background pattern of `strategy/current.md`):

```
##### from 2026-11-22 — 宇宙对比结论：保留 CSI500（CSI1000 不切换）

**Background**：为决定股票横截面选股模型的宇宙是否从 CSI500（000905.SH）切到 CSI1000（000852.SH），
在"可执行宇宙"（板块默认剔除创业板/科创板/北交所 + as-of ST 剔除；对应主账号 2027-10-27 前的法律约束）上
做了预登记的多折 walk-forward 对比（P1 v3，digest cabcf49a3c67；固定 5 起点 2018-01…2022-01，
panel_end 2026-11-20，7 折 expanding，horizons 3/7/30，execution next_day_open，费率成本+holding 5/10/20）。
4 臂 = {CSI500, CSI1000} × {board-only, board+ST}。

**结论：两指数均未通过验收 → 保留 CSI500；CSI1000 作为负结果归档，不切换宇宙。**

- 30d 主：所有 4 臂 alpha（对可执行基准）**−3.75%…−5.37%/年**，OOS Sharpe **−0.32…−0.84**，
  **0/5 起点**达到 ≥5/7 fold 跑赢基准；虽然 rank IC（0.031–0.049）与 ICIR（0.30–0.36）过线，
  但弱信号未转化为组合 alpha。
- 严重过拟合：IS Sharpe ≈6–7 vs OOS ≈−0.3…−0.8。模型能记忆训练段，不能泛化。
- CSI1000 并不优于 CSI500（两者都败）；ST 层边际影响小且略偏负（board+ST 略差于 board-only）。
- 结论对模拟器口径稳健：alpha 需 +9% 才能触及 +5% 验收线；引擎口径（P(UP)）与 IC 口径（P(UP)−P(DOWN)）
  两种选择均 fail。

**生产发现（独立 follow-up，非宇宙决定）**：全引擎回测按 `up_probability`=P(UP) 选股，而模型评估的
rank IC 用 P(UP)−P(DOWN)。P(UP) 单独选股在实时数据上反预测（gross OOS ≈ −12%/年），而 P(UP)−P(DOWN)
携带 +0.05 IC。服务端选股口径应与评估口径一致——这是"模型选择"层面的改动，与"宇宙"无关，另立 issue。

**Caveat / 未完成**：向量化模拟器尚未与全引擎抽样对账（D10）；结论为 provisional，但失败幅度大
（需 +9% 翻转），稳健。STK-10（现有 CSI500 回测的可执行性代价）为独立交付，另排期。
```

## 2) Append to `memo/2026_memo.md` — dated negative-result note

```
### 2026-11-22

CSI500 → CSI1000 宇宙切换：**负结果，保留 CSI500**。
预登记（cabcf49a3c67）在可执行宇宙（剔创业板/科创板 + as-of ST）上跑了 4 臂 × 5 起点 × 3 horizon
的 walk-forward 对比。两指数的 board-only / board+ST 臂**都没过验收**（alpha −4~−5%/年、OOS Sharpe
−0.3~−0.8、0/5 起点跑到 ≥5/7 fold）；IC/ICIR 过线但没转化成组合 alpha，IS/OOS 缺口巨大（过拟合）。
CSI1000 不比 CSI500 好——所以不动宇宙。负结果已归档（FinanceAnalysis PR #227）。
附带发现：引擎按 P(UP) 选股、评估用 P(UP)−P(DOWN)，两口径不一致，P(UP) 单独反预测；这是模型选择
层面的问题，另议，不是宇宙问题。模拟器还没跟全引擎对账（provisional），但失败幅度大到不影响结论。
```

## 3) `BACKLOG.md` §5.1/§5.6 — update the CSI1000-switch item status

- Mark the **CSI500 → CSI1000 universe switch** as **resolved: not switching** (negative result,
  prereg `cabcf49a3c67`, FinanceAnalysis PR #227). CSI1000 data + PIT series retained for research.
- Add a new backlog item: **"align serving selection score (P(UP)) with evaluation score
  (P(UP)−P(DOWN))"** — model-selection follow-up, independent of the universe decision.
