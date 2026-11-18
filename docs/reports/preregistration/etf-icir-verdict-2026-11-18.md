# Pre-registration — ETF rotation ICIR verdict (`#136`)

**Write-once.** This file is hashed (`evidence.preregistration_digest`, sha256[:12]) into every
ledger row it governs and into the report filename. Editing it after a measurement voids it;
a changed criterion requires a NEW file, not an edit to this one.

Registered: 2026-11-18, before any cell of this grid has been measured.

## Why the statistic changed

The previous verdict was a **median of six block Sharpes**. Measured from the ledger's own
`results.blocks`: within-start sd(block Sharpe) ≈ 1.006, so the SE of a six-block mean is
≈ 0.411, and a median of six is worse. The instrument could not resolve the 0.1–0.5 effects the
research targets. Re-layout cannot fix it: `WALK_FORWARD_MIN_FOLD_REBALANCES = 20` means 25
blocks would need ≈ 500 rebalance events, while a panel holds ≈ 210 (BIWEEKLY ≈ 26/year, bars
start 2016-01-04). Enlarging the panel into 2024+ would consume the **released holdout**, which
is forbidden. Changing the rebalance frequency would change the arm.

So the statistic changes, not the layout: a **Newey-West t on daily Spearman ICs**, which
`rank_ic.block_rank_ic` already returns as `per_date_ic`.

## Frozen constants

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

## Criteria

**Primary — REJECT, not select.** An arm avoids rejection on a panel start only if
`NW t >= 2.0` **AND** `mean daily IC > 0`. Failing to clear this selects nothing; it only
declines to reject. No arm is promoted by this grid.

**Secondary — Sharpe gate.** Net-of-fee Sharpe point estimate > 0 **AND** its 95% confidence
lower bound > 0 (`point − 1.96·SE > 0`). The point estimate is the **mean of the disjoint block
Sharpes**, and its SE is `sqrt(Σ se²)/k` where each block's `se` comes from **session-level
equity returns** (Lo 2002 IID part, inflated by the same Bartlett-weighted autocorrelation sum).
This is deliberately **not** the block median. Rationale: an IC signal can be significant while
the strategy loses money after fees, so significance alone must never approve an arm.

**Paired differences.** Arm-minus-baseline IC differences on shared dates within the same
blocks. Legitimate because `walk_forward.combinatorial_purged_folds` documents that
`panel_tail_days` overrides the tail **without changing the purge**, so arms land on the same
blocks. Dropped (unshared) dates are reported, never silently discarded.

**Pooling across starts.** DerSimonian-Laird random effects, reporting `tau2` and `I2`
alongside the pooled mean. **Frozen caveat:** when `tau2` dominates, pooling cannot manufacture
independence; a large `I2` means the starts measure different things and the pooled number
describes that heterogeneity rather than sharpening one common effect.

## Grid — every cell that may run

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

## Pre-contract declaration

All three starts precede `ETF_BACKTEST_START = 2021-01-13`. Every cell is therefore
**research-only pre-contract history**, run with `allow_pre_contract_history=True`, and
**must not be reported alongside production metrics**. Without that override
`enforce_date_contract` raises and the grid cannot run at all — which was a reproducibility
regression, since these are the very starts the existing ledger rows were measured on.

## Status of the previous ledger

The 36 existing block-median readings in `docs/reports/evidence/etf-rotation-measurements.jsonl`
are **demoted to a robustness view and lose adjudication authority**. The file is append-only
and is not edited or deleted; the demotion is recorded here and in the report.

- **P35 stays no-go.**
- **P38** moves from "permanently closed" to "has an adjudicable instrument". It is not opened
  by this registration; opening it still requires an arm to clear both criteria and replicate.

## What would void this registration

Changing any frozen constant, adding or removing a grid cell, changing the panel end, or
selecting the autocorrelation treatment after seeing results. Any of these requires a new
write-once file with a new digest.
