# Pre-registration: STK-10 executable-universe rerun of the existing CSI500 backtest

> DRAFT. Gate B first deliverable — DESCRIPTIVE, no acceptance criteria, no verdict.
> At run: move to docs/reports/preregistration/stk10-executable-universe-<date>.md, hash, ledger row first.

- Declared: 2026-11-21
- Purpose: quantify how much of the existing CSI500 backtest reading is untradeable today.
  CSI500 has 28.4% of constituents / 34.50% of weight on boards the account cannot trade
  (TuShare index_weight 20261030). Every existing CSI500 reading already overstates the
  executable part — independent of CSI1000.
- Model: the CURRENT active fleet lgb-*-2026-11-09 (fixed; no retrain).
- Window: 2018-01-02 .. 2026-11-20.

## Curves (three) and benchmarks (two)
- universe_policy = research_full (full CSI500)
- universe_policy = default (ex-board)
- universe_policy = default + st_layer=True (ex-board + ex-ST; requires B3)
- Benchmarks: official 000905.SH AND CSI500_PIT_EXBOARD_ST (executable).
- execution_model: next_day_open (primary) AND same_day_close (legacy D2), dual-reported.

## Reported quantities
- Three equity curves + two benchmark curves.
- Permission price = official − executable (annualized + cumulative), reported as a number.
- ST cost = ex-board − ex-board+ST.
- If the gap is negative (executable underperforms full): record "the permission constraint
  has a cost"; do NOT change any default on that basis alone.
- Bias register D1-D10 accompanies the report; ledger ids cited.

## Forbidden
- No acceptance verdict here (this is descriptive; the verdict is the comparison prereg).
- No default changes from this run alone.
- No reading without a ledger row carrying this file's hash.

```json
{
  "runs": [
    {"policy": "research_full", "st": false, "execution": "next_day_open"},
    {"policy": "research_full", "st": false, "execution": "same_day_close"},
    {"policy": "default", "st": false, "execution": "next_day_open"},
    {"policy": "default", "st": false, "execution": "same_day_close"},
    {"policy": "default", "st": true, "execution": "next_day_open"},
    {"policy": "default", "st": true, "execution": "same_day_close"}
  ],
  "window": {"start": "2018-01-02", "end": "2026-11-20"},
  "model": "active fleet (fixed)",
  "benchmarks": ["000905.SH", "CSI500_PIT_EXBOARD_ST"]
}
```