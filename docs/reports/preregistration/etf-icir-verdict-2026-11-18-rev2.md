# Pre-registration — ETF rotation ICIR verdict (`#136`), revision 2

**Write-once.** This file is hashed (`evidence.preregistration_digest`, sha256[:12]) into every
ledger row it governs and into the report filename. Editing it after a measurement voids it;
a changed criterion requires a NEW file, not an edit to this one.

Registered: 2026-11-18, revision 2. **Supersedes `etf-icir-verdict-2026-11-18.md`
(digest `4af198d46063`), which is left untouched.**

## Why there is a revision 2, disclosed before any candidate measurement

Revision 1 was measured once: a plumbing smoke on the baseline cell `rule@2016-01-29`, ledger row
`d7ea558b0022`, run to prove the date-contract blocker was fixed. That smoke exposed two defects
in the instrument itself. Neither was found by comparing arms, and **no candidate arm has been
measured under either revision**. The row's IC fields are empty (RULE had no scorer) and its
Sharpe fields do not depend on the join, so it is not a contaminated candidate reading — but it
is a measurement, and this revision exists after it rather than before it. That is recorded here
so nobody reads revision 2 as a clean slate.

**Defect 1 — the IC join was not date-aligned.** `rank_ic.block_rank_ic` joined scores to labels
on `ts_code` alone over a multi-date block. Both sides carry one row per `(date, ts_code)`, so the
join was many-to-many across dates: each label row matched every date's score for that code, and
the per-date Spearman then correlated one day's labels against the whole block's pooled scores.
Demonstrated on synthetic data with a scorer that is perfect on each date: **IC 0.813 instead of
1.0**, with the sample inflated by the number of dates in the block (3 dates → 3x). Every existing
test used a single-date block, where the join is accidentally correct. Fixed by reattaching the
date positionally — with an assertion that the predictor returned one row per input snapshot in
the input's order — and joining on `['date', 'ts_code']`.

**Defect 2 — the RULE arm had no scorer.** `run_etf_rotation_backtest` set `predictor = None` for
RULE, so its `per_date_ic` was empty. The primary criterion was unevaluable for the baseline, and
the paired arm-minus-baseline leg returned `shared_dates=0` for every arm — it could never produce
a number. Fixed by giving RULE a scorer that reproduces `rotation_engine._rule_scores`, ranking
**per date** (a percentile over a pooled frame is not the cross-section the engine ranks) and
restoring dropped rows as NaN to keep the row-aligned contract. `predictors={}` is unchanged, so
**selection, sizing and execution are untouched**: this exposes the ranking the engine already
uses, it does not add a model to the baseline.

Consequence for the historical ledger: the `mean_ic` stored in the 36 earlier rows was computed
through the same bad join. Those rows were already demoted to a robustness view with no
adjudication authority; that demotion now explicitly covers `mean_ic` as well, not only the
block-median Sharpe verdict. The ledger remains append-only and unedited.

## Why the statistic changed (unchanged from revision 1)

The previous verdict was a **median of six block Sharpes**. Measured from the ledger's own
`results.blocks`: within-start sd(block Sharpe) ≈ 1.006, so the SE of a six-block mean is
≈ 0.411, and a median of six is worse. The instrument could not resolve the 0.1–0.5 effects the
research targets. Re-layout cannot fix it: `WALK_FORWARD_MIN_FOLD_REBALANCES = 20` means 25
blocks would need ≈ 500 rebalance events, while a panel holds ≈ 210 (BIWEEKLY ≈ 26/year, bars
start 2016-01-04). Enlarging the panel into 2024+ would consume the **released holdout**, which
is forbidden. Changing the rebalance frequency would change the arm.

So the statistic changes, not the layout: a **Newey-West t on daily Spearman ICs**, which
`rank_ic.block_rank_ic` returns as `per_date_ic` — now a genuine daily series for every arm.

## Frozen constants (identical to revision 1)

| constant | value | reason |
|---|---|---|
| `NW_LAG` | 20 | label horizon is `ETF_LABEL_HORIZON_DAYS = 20` ⇒ MA(19) overlap |
| `T_MIN` | 2.0 | primary threshold, per panel start |
| `SHARPE_CI_Z` | 1.96 | two-sided 95% normal quantile |
| `MIN_OBSERVATIONS` | 40 | below this no t-statistic is reported |
| `MIN_DISPERSION` | 1e-10 | numerically degenerate dispersion yields no statistic, not a fake one |

**Autocorrelation decision (frozen, not selectable at run time):** Newey-West with a Bartlett
kernel at lag 20. The alternative — declaring the t uncorrected and raising the threshold by
√20 (t ≥ 8.9) — was **rejected**: it assumes worst-case unit MA coefficients and discards power
at n ≈ 1405. Effective n under MA(19) overlap is ≈ 1405/20 ≈ 70; this is recorded so nobody
reads the raw daily count as independent evidence.

## Criteria (identical to revision 1)

**Primary — REJECT, not select.** An arm avoids rejection on a panel start only if
`NW t >= 2.0` **AND** `mean daily IC > 0`. Failing to clear this selects nothing; it only
declines to reject. No arm is promoted by this grid.

**Secondary — Sharpe gate.** Net-of-fee Sharpe point estimate > 0 **AND** its 95% confidence
lower bound > 0 (`point − 1.96·SE > 0`). The point estimate is the **mean of the disjoint block
Sharpes**, and its SE is `sqrt(Σ se²)/k` where each block's `se` comes from **session-level
equity returns** (Lo 2002 IID part, inflated by the same Bartlett-weighted autocorrelation sum).
This is deliberately **not** the block median. The session-level sampling unit was confirmed by
the owner on 2026-11-18 and is unchanged from revision 1. Rationale: an IC signal can be
significant while the strategy loses money after fees, so significance alone must never approve
an arm.

**Paired differences.** Arm-minus-baseline IC differences on shared dates within the same
blocks. Legitimate because `walk_forward.combinatorial_purged_folds` documents that
`panel_tail_days` overrides the tail **without changing the purge**, so arms land on the same
blocks. Dropped (unshared) dates are reported, never silently discarded. **This leg is only
computable now that the baseline has an IC series** — under revision 1 it was structurally inert.

**Pooling across starts.** DerSimonian-Laird random effects, reporting `tau2` and `I2`
alongside the pooled mean. **Frozen caveat:** when `tau2` dominates, pooling cannot manufacture
independence; a large `I2` means the starts measure different things and the pooled number
describes that heterogeneity rather than sharpening one common effect.

## Grid — every cell that may run (identical to revision 1)

Panel end `2023-12-31` (the released holdout starts 2024-01-01 and is **not** touched).
Blocks 6. Frequency BIWEEKLY, `max_positions=3`, `momentum_lookback=120` — frozen by
`measure_etf_rotation_sensitivity.py`, not adjustable here.

```
rule@2016-01-29
rule@2017-01-03
rule@2018-01-02
lgb-veto@2016-01-29
lgb-veto@2017-01-03
lgb-veto@2018-01-02
lstm-veto@2016-01-29
lstm-veto@2017-01-03
lstm-veto@2018-01-02
```

Baseline arm: `rule`. A "win" means beating RULE on the **same** panel start.

`rule@2016-01-29` already has a revision-1 row (`d7ea558b0022`). Re-running it under this
revision appends a new row; the old one is not edited or deleted, and the two are distinguished
by their `preregistration` digest.

## Pre-contract declaration

All three starts precede `ETF_BACKTEST_START = 2021-01-13`. Every cell is therefore
**research-only pre-contract history**, run with `allow_pre_contract_history=True`, and
**must not be reported alongside production metrics**. Without that override
`enforce_date_contract` raises and the grid cannot run at all — which was a reproducibility
regression, since these are the very starts the existing ledger rows were measured on.

## Status of the previous ledger

The 36 existing block-median readings in `docs/reports/evidence/etf-rotation-measurements.jsonl`
are **demoted to a robustness view and lose adjudication authority**, and that demotion covers
their `mean_ic` as well (defect 1 above). The file is append-only and is not edited or deleted.

- **P35 stays no-go.**
- **P38** moves from "permanently closed" to "has an adjudicable instrument". It is not opened
  by this registration; opening it still requires an arm to clear both criteria and replicate.

## What would void this registration

Changing any frozen constant, adding or removing a grid cell, changing the panel end, or
selecting the autocorrelation treatment after seeing results. Any of these requires a new
write-once file with a new digest.