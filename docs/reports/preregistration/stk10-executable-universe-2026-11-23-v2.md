# Pre-registration v2: STK-10 executable-universe rerun of the existing CSI500 backtest

> DESCRIPTIVE. No acceptance criteria, no verdict, no default changes.
> Supersedes v1 (2026-11-21, digest `615f2853c82e`). v1 declared the matrix but left the
> horizon ambiguous and the parameter vector unspecified; resolving both is a design
> addition after a hash, so per S4/D11 this is a NEW file with a NEW hash rather than an
> in-place edit. No STK-10 run was executed under v1, so no result is being reinterpreted.

- Declared: 2026-11-23
- Purpose: quantify how much of the existing CSI500 backtest reading is untradeable today.
  CSI500 has 28.4% of constituents / 34.50% of weight on boards the account cannot trade
  (TuShare `index_weight` 20261030). Every existing CSI500 reading already overstates the
  executable part — independent of the CSI1000 comparison, which is settled (keep CSI500,
  negative result archived, prereg `cabcf49a3c67`).
- Model: the active fleet, **pinned by version** — `lgb-3d-2026-11-09` (artifact id 46),
  `lgb-7d-2026-11-09` (id 47), `lgb-30d-2026-11-09` (id 48). No retrain. `run_stk10_matrix`
  refuses to create rows if the active artifact has moved off these versions.
- Window: **2018-01-02 .. 2026-11-20**. The start is exactly `STOCK_BACKTEST_START`
  (`apps/core/date_contract.py:51`), and `enforce_date_contract` returns a request unchanged
  when `requested_start >= boundary`, so `--allow-pre-contract-history` is **NOT** passed and
  `allow_pre_contract_history` is **absent** from every run's parameters. A run carrying that
  key is not admissible under this registration: it would be labelled research-only and must
  not be reported alongside production metrics, which is the opposite of this measurement's
  purpose.
- Horizons: **3, 7 and 30 — all three.** v1's "active fleet (fixed)" was ambiguous because a
  single `BacktestRun` carries one `horizon_days`; this resolves it to **18 cells, not 6**.
  h7 is the production-consumed horizon (dashboard API default `analytics/views.py:113`,
  cache-prewarm pin `analytics/tasks.py:1382`, engine default `backtest/tasks.py:2988`), so
  the h7 cells are the decision-relevant ones; h3 and h30 are coverage.

## Curves (three per horizon) and benchmarks (two)

- `universe_policy = research_full` — full CSI500, all boards, ST **included** (research
  presets force the ST exclusion layer off).
- `universe_policy = default, st_layer = false` — ex-board, ST **included**.
- `universe_policy = default, st_layer = true` — ex-board, ST **excluded**.
- There is **no** `research_full + ST` cell: `resolve_st_layer` forces ST off for research
  presets, so that combination is not expressible. The matrix is 3 arms x 2 execution models,
  **not** 2 x 2 x 2.
- The three arms **nest**: research_full ⊃ default/st-off ⊃ default/st-on.
- Benchmarks: official `000905.SH` AND `CSI500_PIT_EXBOARD_ST` (executable).
  Coverage VERIFIED read-only on 2026-11-23: the window holds **2,150 open sessions**
  (`ExchangeTradingCalendar`, `is_open=True`), and `CSI500_PIT`, `CSI500_PIT_EXBOARD` and
  `CSI500_PIT_EXBOARD_ST` each carry 2,155 rows spanning 2018-01-02..2026-11-20 with
  **0 missing sessions** against that calendar. So `CSI500_PIT_EXBOARD` — although not
  daily-maintained (historical from DR7 pass1 on a frozen panel) — does cover this window,
  and the st-off arms may report against their own matching benchmark in addition to the two
  declared ones. Residual anomaly, to be stated in the report: each series has 5 rows on dates
  the calendar does not mark open.
- `IndexMembership` for `000905.SH` is monthly by design: 106 distinct snapshot dates /
  53,000 rows in the window (2018-01-31..2026-10-30), 500 constituents each; the table
  reaches back to 2010-01-04, so as-of resolution before the first in-window snapshot
  resolves to the prior month rather than failing closed.
- `execution_model`: `next_day_open` (primary) AND `same_day_close` (legacy D2), dual-reported.

## Reported quantities

- Three equity curves per horizon + two benchmark curves.
- **Permission price** = official − executable (annualized + cumulative), reported as a number.
- **ST cost** = ex-board − ex-board+ST.
- If the gap is negative (executable underperforms full): record "the permission constraint
  has a cost"; do **NOT** change any default on that basis alone.
- The CNY-5 minimum commission that the vectorized simulator omits is captured here: the
  engine fee model keeps `commission_min = 5.0` (discharges CMP prereg v3 §94).
- Bias register D1–D10 accompanies the report; ledger ids cited.
- Recorded as a finding in its own right: before this matrix, **no run in the 444-row
  `BacktestRun` table had `universe_policy` or `execution_model` set**, so every prior
  reading is default-policy / `same_day_close`.

## Fixed parameter vector

Identical across all 18 cells except `horizon_days`, `top_n`, `holding_period_days`,
`top_n_metric`, the artifact pin, and the three declared axes.

```
strategy_type      = PREDICTION_THRESHOLD
start_date         = 2018-01-02
end_date           = 2026-11-20
initial_capital    = 200000.00            # model column, not a parameter

candidate_mode              = top_n
prediction_source           = lightgbm
up_threshold                = 0.45
capital_fraction_per_entry  = 0.2
use_macro_context           = true
enable_stop_target_exit     = false
lightgbm_inference_backend  = cpu_serial
lightgbm_batch_size         = 256
chunk_trading_days          = 20
slippage_bps                = 5
pit_index_codes             = ["000905.SH"]   # explicit, not defaulted
fees                        = engine defaults (cn_a_share_default), commission_min 5.0 ON
allow_pre_contract_history  = ABSENT          # see Window above
```

Per horizon (CORE_PROFILES `base`, matching the CMP pre-registration O4):

| horizon | top_n | holding_period_days | top_n_metric | artifact |
| :-- | --: | --: | :-- | :-- |
| 3 | 5 | 5 | `up_prob_3d` | `lgb-3d-2026-11-09` (46) |
| 7 | 5 | 10 | `up_prob_7d` | `lgb-7d-2026-11-09` (47) |
| 30 | 3 | 20 | `up_prob_30d` | `lgb-30d-2026-11-09` (48) |

## Execution

- **One process, inline, bucketed by horizon**; caches cleared between buckets. Within a
  bucket each arm's two execution models are **adjacent** so they share one signal surface —
  legitimate because `execution_model` changes when a fill happens, not what was predicted,
  and `_matrix_signal_cache_key` now carries `(universe_policy, st_layer, pit_index_codes)`.
  Inline is required: the Celery soft/hard limits are 1800 s / 2100 s and a full-window run
  is ~43 h.
- `BACKTEST_MATRIX_SIGNAL_CACHE_MAX_ENTRIES >= 2400` at launch. The window has ~2,158
  sessions and the default bound is 2048, so a sharing pair would otherwise lose its
  earliest dates. The bound is already env-overridable and `_bounded_cache_get` is true LRU.
- `chunk_trading_days = 20`: ~1,420 s/chunk at the measured ~71 s/session, under the 1,800 s
  soft limit; 60 would be ~4,260 s and a certain hard-limit kill leaving the run stuck
  `RUNNING` for the `task_health` sweep.
- `lightgbm_inference_backend = cpu_serial`. Measured, not assumed: the `windows_gpu` run
  1456 recorded `asset_loop_seconds = 12,208` against cpu_serial run 1462's `12,530` — 2.6%
  better. The loop is feature-assembly/IO bound on the NAS, so the GPU is not a lever and
  comparability with all 444 prior runs wins.
- Cost model, measured from completed 243-session runs (`report.lightgbm_runtime`):
  h3 5.01 h, h7 4.82 h, h30 5.17 h → ~71–77 s/session. The window holds **2,150** open
  sessions (verified, not estimated). Scaled and weighted by eligible-universe size
  (research_full 1.00x, ex-board 0.716x, ex-board+ST 0.70x): **9 prediction surfaces
  ≈ 320 h ≈ 13.3 days**.
- tmux only (a prior DR7 run was killed by a session reconnect); `scripts/db_wedge_watch.py`
  alongside. Resume after a kill is **manual**: re-invoke `run_backtest(run.id)`, which
  continues from `report.runtime_state.current_index`. A FAILED run does not resume itself.
- **Superset sharing is NOT part of this registration.** Predicting the `research_full`
  superset once per horizon and filtering at candidate time is mathematically identical
  (per-asset predictions are universe-independent and `estimate_trade_decision` takes no
  cross-sectional input) and would cut 9 surfaces to 3 ≈ 133 h ≈ 5.5 days. It is a separate
  gated change requiring a 60-session <1 bp equivalence proof against the per-arm path. If it
  lands and passes before launch, a **v3** registration declares it; this matrix does not
  assume it.

## Pre-run probes (read-only, 2026-11-23 — recorded BEFORE any run)

**Arm nesting, verified against the live contract.** `effective_universe_tradeable_asset_ids`
called directly for `000905.SH` on five dates spanning the window:

| date | research_full | default/st-off | default/st-on | board cut | ST cut |
| :-- | --: | --: | --: | --: | --: |
| 2018-01-02 | 463 | 435 | 434 | 28 | 1 |
| 2020-06-30 | 498 | 453 | 453 | 45 | 0 |
| 2022-04-26 | 499 | 442 | 442 | 57 | 0 |
| 2024-09-30 | 499 | 387 | 387 | 112 | 0 |
| 2026-11-20 | 500 | 358 | 358 | 142 | 0 |

Both nestings hold on every date (`st-off ⊆ research_full`, `st-on ⊆ st-off`), which is the
precondition for superset sharing. The board cut at 2026-11-20 is 500 → 358 = **28.4%**, matching
the TuShare `index_weight` figure this measurement exists to price.

**Expected ST cost is near zero, and that is recorded here rather than discovered later.** The
ST cut is 1/0/0/0/0 across 8.9 years of sampled dates: CSI500 constituents are almost never
ST-flagged, because an ST designation removes the name at the next index review. So the
`default/st-on` arm is expected to be very close to `default/st-off`, and the declared "ST cost"
quantity is expected to come out ≈0. This is consistent with the settled comparison's finding
that the ST layer's marginal impact was small. **The matrix is NOT reduced on this basis** — a
measured near-zero ST cost is a result, five sampled dates are not the whole window, and the
registration predates the runs. It is recorded so that a near-zero ST cost cannot later be
misread as a broken arm, and so the owner can price the ~2.6 days those 6 cells cost.

## Forbidden

- No acceptance verdict here (descriptive; the verdict belonged to the comparison prereg,
  now settled).
- No default changes from this run alone.
- No reading without a ledger row carrying this file's hash. The registration row is written
  **before** the first run is created.
- No retrain of the pinned fleet between registration and launch.
- No `allow_pre_contract_history` key on any cell.

```json
{
  "schema_version": "stk10-v2",
  "supersedes": "615f2853c82e",
  "window": {"start": "2018-01-02", "end": "2026-11-20"},
  "horizons": [3, 7, 30],
  "allow_pre_contract_history": false,
  "model": {
    "pin": "version",
    "artifacts": {
      "3": {"id": 46, "version": "lgb-3d-2026-11-09"},
      "7": {"id": 47, "version": "lgb-7d-2026-11-09"},
      "30": {"id": 48, "version": "lgb-30d-2026-11-09"}
    }
  },
  "arms": [
    {"universe_policy": "research_full", "st_layer": false},
    {"universe_policy": "default", "st_layer": false},
    {"universe_policy": "default", "st_layer": true}
  ],
  "execution_models": ["next_day_open", "same_day_close"],
  "benchmarks": ["000905.SH", "CSI500_PIT_EXBOARD_ST"],
  "parameters": {
    "candidate_mode": "top_n",
    "prediction_source": "lightgbm",
    "up_threshold": 0.45,
    "capital_fraction_per_entry": 0.2,
    "use_macro_context": true,
    "enable_stop_target_exit": false,
    "lightgbm_inference_backend": "cpu_serial",
    "lightgbm_batch_size": 256,
    "chunk_trading_days": 20,
    "slippage_bps": 5,
    "pit_index_codes": ["000905.SH"],
    "fee_model": "cn_a_share_default",
    "commission_min": 5.0
  },
  "horizon_profiles": {
    "3": {"top_n": 5, "holding_period_days": 5, "top_n_metric": "up_prob_3d"},
    "7": {"top_n": 5, "holding_period_days": 10, "top_n_metric": "up_prob_7d"},
    "30": {"top_n": 3, "holding_period_days": 20, "top_n_metric": "up_prob_30d"}
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
  ],
  "superset_sharing": {
    "declared": false,
    "reason": "separate gated change; requires a 60-session <1bp equivalence proof",
    "nesting_precondition_verified": true,
    "surfaces_if_absent": 9,
    "sessions_in_window": 2150,
    "estimated_hours": 320
  },
  "expected_st_cost": "near zero; ST cut measured 1/0/0/0/0 on five sampled dates"
}
```
