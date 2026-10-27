# 预登记：标签 horizon 扫描（P25）

- 登记时间：2026-10-25（运行之前）
- 臂：`lgb-select`（`--model LIGHTGBM --cpcv-retrain`，`--label rank_pct`，per-fold retrain，
  6 blocks × 1 held-out，purge=20，embargo=5）
- 面板：2016-01-29 .. 2023-12-31，`--frequency BIWEEKLY --max-positions 3
  --momentum-lookback 120 --no-absolute-momentum-filter --weighting equal`
- 扫描：同一批逐折分数，对 h ∈ {20, 40, 60} 个 session 的价格远期收益计算 rank IC。
  **不重训**：这是"分数对多长窗口仍有排序能力"的测量，不是"用 60 日标签训练是否更好"。
- 判据（唯一允许的结论来源）：
  1. `ICIR(h) = mean(block IC) / sd(block IC)`；
  2. 覆盖率：h 可用的块数 ≥ 0.8 × h=20 的块数（块尾未实现的标签会被丢掉）；
  3. 采纳 h 当且仅当 `ICIR(h) ≥ ICIR(20)` 且 `mean IC(h) ≥ 0.9 × mean IC(20)` 且判据 2 成立；
  4. 若只有 h=40 满足，记录为候选但不改默认；只有 ICIR 优势 ≥ 25% 才进入训练标签改造；
  5. 任何情况下不得用组合收益挑 horizon。
- 已知限制：训练标签仍是 20 日。若判据选中更长 horizon，必须另开一轮把训练标签也改过去
  并按折重训，才能宣称"换标签有增益"。
- 结果写入 `docs/reports/etf-rotation-cpcv-model-comparison-2026-10-25.md` §21。
