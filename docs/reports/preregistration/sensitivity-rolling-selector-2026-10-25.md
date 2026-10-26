# 预登记：滚动窗口下的选择器（2026-10-25）

运行前写死；修改任何一项即作废本次预登记。

## 问题

P33 在同一批验证块上发现：LightGBM 选择器在 expanding 训练窗口下 Sharpe 中位数 −0.7320，
在 rolling（固定 750 日）下 +0.4945；RULE 在两个计划上的读数逐位相同（它不训练）。
若这是真实效应，说明"选择器失败"部分是**训练窗口形状**的问题，而不是信号本身的问题。

## 固定项

- 分位、配置、折数、purge 与 embargo 与 P33 完全一致：BIWEEKLY / p3 / mom120 / 绝对动量关闭 /
  equal；6 折；purge=20（ML）/1（RULE）；embargo=5；`--rolling-train-days 750`。
- 折计划固定 `rolling`（两种臂都用它，否则基线与被测臂不在同一日历上）。
- 面板终点 `2023-12-31`。

## 格栅

- `rule-rolling@2016-01-29`
- `rule-rolling@2017-01-03`
- `rule-rolling@2018-01-02`
- `lgb-select-rolling@2016-01-29`
- `lgb-select-rolling@2017-01-03`
- `lgb-select-rolling@2018-01-02`

## 判据

对同一面板起点，比较 `lgb-select-rolling` 与 `rule-rolling`：

1. Sharpe 中位数更高；
2. 正收益块占比不低于基线；
3. maxDD 中位数不高于基线 + 2pp。

**3/3 个面板起点通过**才算"滚动选择器效应复现"。

## 已知先行结果

- 2016-01-29 起点（P33）：rolling LGB 0.4945 / 66.67% / 14.24% vs rolling RULE 0.0791 / 66.67% / 19.24%，三条 leg 全胜。
- expanding 下同一臂是 −0.7320，因此本次检验的是"rolling 是否让它稳定通过"，不是"再找一个能过的臂"。

## 允许的结论

- 3/3 → 该臂取得进入前向观察的资格（仍需计划 §9 的 forward leg，且需 LSTM 对照）。
- 1-2/3 → 未复现，记录为负结果，RULE 保持生产。
- 0/3 → P33 的 2016 读数是起点产物。
