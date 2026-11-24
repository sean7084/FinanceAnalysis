# Pre-registration v3: STK-10 executable-universe rerun of the existing CSI500 backtest

> DESCRIPTIVE. No acceptance criteria, no verdict, no default changes.
> Supersedes v2 (2026-11-23, digest `d8b09eb37d91`). v2 declared superset sharing as a
> separate gated change; that 60-session equivalence gate has now passed and this v3
> declares superset as the execution mode. No STK-10 strategy result existed before this
> registration, so no result is being reinterpreted.

- Declared: 2026-11-24
- Purpose: quantify how much of the existing CSI500 backtest reading is untradeable today.
  CSI500 has 28.4% of constituents / 34.50% of weight on boards this account cannot trade
  (TuShare `index_weight` 20261030). Every existing CSI500 reading overstates the executable
  part. This measurement is independent of the settled CSI1000 comparison (keep CSI500).
- Model pin: `lgb-3d-2026-11-09` (id 46), `lgb-7d-2026-11-09` (id 47),
  `lgb-30d-2026-11-09` (id 48). No retrain. `run_stk10_matrix` refuses to create rows if
  the active artifact moves off these versions.
- Window: **2018-01-02 .. 2026-11-20**, exactly 2,150 open sessions. The start equals
  `STOCK_BACKTEST_START`, so `allow_pre_contract_history` is **absent** from every run.
- Horizons: **3, 7, 30 — all three**, giving 18 cells, not 6. h7 is the production-consumed
  horizon; h3/h30 are coverage.

## Curves, arms, and benchmarks

- `universe_policy=research_full` — full CSI500, all boards, ST included.
- `universe_policy=default, st_layer=false` — ex-board, ST included.
- `universe_policy=default, st_layer=true` — ex-board, ST excluded.
- There is no `research_full + ST` cell: research presets force ST off.
- The arms nest: research_full ⊃ default/st-off ⊃ default/st-on. Verified against the live
  contract on five dates spanning 2018-2026: 463/435/434, 498/453/453, 499/442/442,
  499/387/387, 500/358/358.
- Benchmarks: official `000905.SH` and executable `CSI500_PIT_EXBOARD_ST`. Coverage was
  rechecked before launch: 2,150 open sessions, three PIT series each with 2,155 in-window
  rows, 0 missing open sessions, 5 extra non-open rows per series.
- `IndexMembership` is monthly by design: 53,000 rows / 106 snapshot dates / 500 names each.

## Reported quantities

- Three equity curves per horizon plus benchmark curves.
- Permission price = official − executable, annualized and cumulative.
- ST cost = ex-board − ex-board+ST.
- Negative gaps are recorded as numbers; no default changes.
- Engine fee model keeps `commission_min = 5.0` (CNY-5 minimum).
- Bias register D1–D10 accompanies the report; D10 uses the bounded 20-session
  simulator-vs-engine reconciliation defined below.

## Fixed parameter vector

```
strategy_type      = PREDICTION_THRESHOLD
start_date         = 2018-01-02
end_date           = 2026-11-20
initial_capital    = 200000.00

candidate_mode              = top_n
prediction_source           = lightgbm
prediction_universe         = superset
up_threshold                = 0.45
capital_fraction_per_entry  = 0.2
use_macro_context           = true
enable_stop_target_exit     = false
lightgbm_inference_backend  = cpu_serial
lightgbm_batch_size         = 256
chunk_trading_days          = 20
slippage_bps                = 5
pit_index_codes             = ["000905.SH"]
allow_pre_contract_history  = ABSENT
```

| horizon | top_n | holding_period_days | top_n_metric | artifact |
| :-- | --: | --: | :-- | :-- |
| 3 | 5 | 5 | `up_prob_3d` | `lgb-3d-2026-11-09` (46) |
| 7 | 5 | 10 | `up_prob_7d` | `lgb-7d-2026-11-09` (47) |
| 30 | 3 | 20 | `up_prob_30d` | `lgb-30d-2026-11-09` (48) |

## Superset equivalence gate (passed before this registration)

`docs/reports/stock/stk10-superset-equivalence-2026-11-24.md` records the proof required by
v2: h7 / `default` / ST-on / `next_day_open`, window **2025-01-02..2025-04-03**, exactly
60 trading sessions, arm then superset in one process with process caches cleared between
modes and `run_backtest.delay` patched for inline continuation.

- arm run id **1565**: COMPLETED, 60 points, 38 closed trades, final value 226,921.21.
- superset run id **1566**: COMPLETED, 60 points, 38 closed trades, final value 226,921.21.
- `max_abs_diff = 0.0`; `max_bp_of_initial_capital = 0.0`; pass = true.
- A pre-fix run **1564** is retained as FAILED evidence for the inline queue-growth bug,
  fixed by PR #234 before this gate was rerun. It contributes no gate result.

## Execution

- One process, inline, bucketed **7 → 3 → 30**; caches cleared between buckets.
- `chunk_trading_days=20`: at the measured 24.6 s/session this is ~492 s per chunk.
- `BACKTEST_MATRIX_SIGNAL_CACHE_MAX_ENTRIES=8000`; the superset surface is one per horizon.
- After h7, every later h7 cell must report `matrix_cache_hits > 0`; otherwise stop before
  h3/h30 and debug.
- tmux only; observe-only `scripts/db_wedge_watch.py` alongside. Resume unfinished runs
  from `report.runtime_state.current_index`; never recreate the 18 rows.
- RSS guard: warn at 22 GiB, stop cleanly at 23 GiB under the 24 GiB cap.

## Cost projection

Measured 150-session probe: 24.6 s/session, 24.9 s/build, flat RSS ~13.9 GiB; the
prediction build is ~98% of session time. Projections for 2,150 sessions, explicitly
estimates rather than guarantees: no sharing ~11.0 days; execution-model pair sharing
~5.6 days; superset sharing ~1.9 days. This registration uses superset.

## D10 reconciliation

After the matrix, run `/tmp/stk10_d10_reconcile.py` on the h7/default/ST-on/next_day_open
cell over 20 sessions chosen around actual trades. Require nonempty candidates and trades,
no coverage gap, and `within_tolerance=True` at 1 bp, or list every breach with an
attributable cause. The engine executes inside `run_engine_reconciliation`'s short
rolled-back transaction; no long transaction is permitted.

## Manifest

```json
{
  "schema_version": "stk10-v3",
  "supersedes": "d8b09eb37d91",
  "prediction_universe": "superset",
  "window": ["2018-01-02", "2026-11-20"],
  "sessions_in_window": 2150,
  "horizons": [3, 7, 30],
  "chunk_trading_days": 20,
  "execution_shape": "one_process_inline",
  "bucket_order": [7, 3, 30],
  "allow_pre_contract_history": "ABSENT",
  "artifacts": {
    "3": {"id": 46, "version": "lgb-3d-2026-11-09"},
    "7": {"id": 47, "version": "lgb-7d-2026-11-09"},
    "30": {"id": 48, "version": "lgb-30d-2026-11-09"}
  },
  "superset_sharing": {
    "declared": true,
    "nesting_precondition_verified": true,
    "surfaces_if_present": 3,
    "surfaces_if_absent": 9,
    "sessions_in_window": 2150,
    "gate_window": ["2025-01-02", "2025-04-03"],
    "gate_run_ids": {"arm": 1565, "superset": 1566},
    "gate_max_bp_of_initial_capital": 0.0,
    "gate_passed": true
  },
  "d10": {
    "planned": true,
    "sessions": 20,
    "tolerance_bp": 1,
    "engine_writes": "short_rolled_back_transaction"
  },
  "runs": [
    {"horizon": 3, "policy": "research_full", "st": false, "execution": "next_day_open"},
    {"horizon": 3, "policy": "research_full", "st": false, "execution": "same_day_close"},
    {"horizon": 3, "policy": "default", "st": false, "execution": "next_day_open"},
    {"horizon": 3, "policy": "default", "st": false, "execution": "same_day_close"},
    {"horizon": 3, "policy": "default", "st": true, "execution": "next_day_open"},
    {"horizon": 3, "policy": "default", "st": true, "execution": "same_day_close"},
    {"horizon": 7, "policy": "research_full", "st": false, "execution": "next_day_open"},
    {"horizon": 7, "policy": "research_full", "st": false, "execution": "same_day_close"},
    {"horizon": 7, "policy": "default", "st": false, "execution": "next_day_open"},
    {"horizon": 7, "policy": "default", "st": false, "execution": "same_day_close"},
    {"horizon": 7, "policy": "default", "st": true, "execution": "next_day_open"},
    {"horizon": 7, "policy": "default", "st": true, "execution": "same_day_close"},
    {"horizon": 30, "policy": "research_full", "st": false, "execution": "next_day_open"},
    {"horizon": 30, "policy": "research_full", "st": false, "execution": "same_day_close"},
    {"horizon": 30, "policy": "default", "st": false, "execution": "next_day_open"},
    {"horizon": 30, "policy": "default", "st": false, "execution": "same_day_close"},
    {"horizon": 30, "policy": "default", "st": true, "execution": "next_day_open"},
    {"horizon": 30, "policy": "default", "st": true, "execution": "same_day_close"}
  ]
}
```
