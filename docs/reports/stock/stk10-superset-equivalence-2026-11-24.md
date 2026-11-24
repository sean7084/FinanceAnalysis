# STK-10 superset equivalence gate — 2026-11-24

**Verdict: PASS.** The full-engine arm and superset paths produced identical 60-session
equity curves before v3 registration.

## Parameters

- h7, `universe_policy=default`, `st_layer=true`, `execution_model=next_day_open`
- Window `2025-01-02..2025-04-03`; exactly 60 calendar and 60 engine trading sessions
- No outer transaction; two diagnostic rows retained; process caches cleared between modes
- `run_backtest.delay` patched for in-process chunk continuation
- Machine-readable evidence: `/tmp/stk10_equivalence.json`

## Runs

| mode | run id | status | curve points | closed trades | final value |
| :-- | --: | :-- | --: | --: | --: |
| arm | 1565 | COMPLETED | 60 | 38 | 226,921.21 |
| superset | 1566 | COMPLETED | 60 | 38 | 226,921.21 |

`predicted_asset_count`: arm 20,058; superset 25,976. The wider prediction surface produced
the same traded portfolio after arm filtering.

## Result

- `max_abs_diff = 0.0`
- `max_bp_of_initial_capital = 0.0`
- trade counts identical (38)
- both curves nonempty, nondegenerate, and finite
- `pass = true`

## Incidental finding

The first gate attempt (run **1564**) exposed a real executor defect: `_execute_inline`
re-added a `RUNNING` id after the patched `.delay` had already enqueued the next chunk,
so the queue grew and `current_index` cycled 20 ↔ 40. The run was stopped and retained as
FAILED evidence. PR #234 fixed the executor with a failing-first regression test; the gate
was rerun on merged `main b198047`, producing runs 1565/1566 above.
