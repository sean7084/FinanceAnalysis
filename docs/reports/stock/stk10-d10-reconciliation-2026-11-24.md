# STK-10 D10 simulator-vs-engine reconciliation — 2026-11-24

**Result: FAIL the 1 bp equivalence target.** Coverage is complete (10/10 comparison
points), but every point differs. The difference is structurally attributable to the
vectorized simulator's approximations; this is not evidence that the full engine is wrong,
and it is not evidence that the simulator is equivalent.

## Setup

- Source: run **1571**, h7 / `default` / ST-on / `next_day_open`.
- Window: **2026-10-26..2026-11-20**, 20 sessions.
- Simulator: `simulate_topn_returns`, holding 10, top-N 5, threshold 0.45, same pinned model,
  same candidate policy, same execution model, rate-based costs.
- Engine: full `run_backtest` inside `run_engine_reconciliation`'s short rolled-back
  transaction; no row persisted.
- Fee model supplied to simulator: commission 0.0001, stamp duty 0.0005, transfer fee
  0.00001, slippage 5 bps. The simulator does not model the CNY-5 minimum commission or
  the separate exchange/regulatory fees.

## Headline comparison

| | return | detail |
| :-- | --: | :-- |
| Simulator | **+7.1045%** | 10 filled positions, 10 daily comparison points |
| Engine | **+1.4413%** | NAV 833,703.79 → 845,720.27 over the same dates |
| Max absolute normalized-NAV difference | **0.0416586** | ≈417 bp; all 10 points breach 1 bp |
| Coverage gap | false | 10/10 points compared |

## Structural attribution

The simulator and engine do not implement the same portfolio construction:

1. **Flat start vs carried positions.** The engine entered the window with two open
   positions and sold them on 2026-10-27 (assets 1789 and 565). A simulator that starts
   flat at the window boundary cannot represent this P&L.
2. **Single rebalance vs staggered entries.** The simulator saw candidates on only three
   dates — 2026-10-29 (asset 371), 2026-10-30 (371 and 1534), and 2026-11-02 (541) — and
   rebalanced once, holding asset 371 for the 10-session period. The engine bought 371
   twice plus 1534 on 2026-10-30/11-02, bought 541 on 2026-11-03, and exited 371/1534 on
   2026-11-10 and 541 on 2026-11-13.
3. **Position management.** The engine supports staggered entries, per-position exit dates,
   `max_positions`, and pending fills; the simulator rebalances an equal-weight top-N
   portfolio as one block.
4. **Fees.** The simulator omits the CNY-5 minimum commission and separate exchange and
   regulatory fees. This contributes to the gap but was not isolated quantitatively.

These differences explain why the simulator returned 7.10% while the engine returned
1.44% over the same dates. The D10 target is therefore **not met**. The correct conclusion
is that the vectorized simulator must not be substituted for the full engine for STK-10;
all reported strategy results in the matrix report come from the full engine.

## Evidence

- Machine-readable D10 result: `/tmp/stk10_d10_result.json`.
- Simulator selections: `/tmp/stk10_d10_debug_sim.log`.
- Engine trades: `BacktestTrade` rows for run 1571 in the window.
- Watchdog remained clean throughout; the rolled-back engine transaction left no rows.
