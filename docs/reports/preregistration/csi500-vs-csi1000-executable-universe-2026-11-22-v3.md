# Pre-registration: CSI500 vs CSI1000 on the executable (ex-board, ex-ST) universe

> FINAL draft (owner O1–O7 resolved per recommendation 2026-11-20). At registration: move to
> docs/reports/preregistration/csi500-vs-csi1000-executable-universe-<date>.md, freeze panel_end
> to the last completed session, hash the FULL file text (evidence.preregistration_digest), write
> the registration ledger row BEFORE any run. Amendments before runs: NEW file + NEW hash + NEW
> registration row. Never edit a hashed file. After results: no new panel starts, ever.
>
> **Amendment (v2, 2026-11-22 — before any VALID result).** The v1 registration
> (digest eabcd59d174e) implemented the simulator as a DAILY rebalance applying the full
> round-trip cost every session (~0.251%/day ≈ 63%/year), which over-applies cost
> ~holding-fold and drove OOS alpha to −20…−67%/yr while gross was +15.5%/yr. That v1 CMP
> run is INVALID and discarded. Corrected here to CORE_PROFILES **holding** (5/10/20): the
> portfolio rebalances every `holding` sessions and pays ONE round-trip cost per period,
> amortized to a daily return. `holding` is now declared explicitly per horizon in the
> manifest. No valid result was seen before this correction (per S4/D11). Fixed panel
> starts, horizons, features, and criteria are unchanged.
>
> **Amendment (v3, 2026-11-22 — before any VALID result).** The v2 simulator ranked the
> top-N by raw P(UP) while the acceptance rank IC uses P(UP)−P(DOWN). On live data the
> P(UP)-ranked portfolio was anti-predictive: gross OOS ≈ −12%/yr (negative before cost),
> yet the P(UP)−P(DOWN) rank IC was +0.047. The two scores disagree (P(DOWN) carries the
> signal). Corrected: the selection score is **P(UP)−P(DOWN)** (same as the IC/acceptance),
> and `up_threshold` is on that score (0.0 = UP>DOWN). No valid result was seen before this
> correction (the P(UP)-ranked CMP run is invalid and discarded). Panel starts, horizons,
> features, holdings, costs, and criteria unchanged. This is the owner-O4 "use the model's
> selection" applied consistently with the acceptance metric.

- Declared: 2026-11-22
- Question: does the stock cross-sectional selector perform better on CSI1000 than on CSI500,
  measured on the subset of constituents the account can legally trade?
- Hard prerequisite: SATISFIED — active fleet lgb-{3,7,30}d-2026-11-09 carries
  evaluation_mode='purged_holdout' (3d rank_ic 0.0041 / t 0.364; 7d 0.0234 / t 1.968;
  30d 0.0559 / t 3.960, icir 0.250). NOTE: 30d icir 0.250 < 0.3 already fails one acceptance
  line on CSI500-full. "Both arms fail ⇒ keep CSI500, archive negative result" is FIRST-CLASS.

## Universe definitions (v1, permanent)
- Board layer: BOARD_TRADING_PERMISSIONS excludes ChiNext (300/301/302) and STAR (688/689);
  BSE classified and disabled defensively (O1; measured zero constituents today).
- ST layer: exclude as-of ST / *ST / S*ST / SST / 退市整理期 / 退$ names from TuShare namechange
  (stock_st denied 40203). Look-ahead safe: ann_date <= start_date gate; violators quarantined.
  Enablement gated on archived reconciliation (B3). ST is never a model feature.
- Both layers resolved as-of per trading day via apply_universe_policy at every chokepoint.
  Delisted non-ST assets RETAIN historical membership (filter never reads listing_status).

## Arms (index × arm; the harness selects index via --index-codes, arm via --arm)
1. CSI500 board-only   (index 000905.SH, arm board_only)
2. CSI500 board+ST     (index 000905.SH, arm board_st)
3. CSI1000 board-only  (index 000852.SH, arm board_only)
4. CSI1000 board+ST    (index 000852.SH, arm board_st)
Conditional (pre-authorized ST→cap trigger):
5. CSI500 board+cap    (index 000905.SH, arm cap, smallest 30% circ_mv excluded, ST off)
6. CSI1000 board+cap   (index 000852.SH, arm cap)
Same engine/config/horizons/purge. Only the universe varies. RS_SCORE/FactorScore percentiles
held fixed-engine across arms (recorded caveat). Feature schema v3, 33 features (below).

## Fixed panel-start set (declared, not chosen later)
2018-01-02, 2019-01-02, 2020-01-02, 2021-01-04, 2022-01-04
panel_end (frozen at hashing): 2026-11-20
Between-start spread of the baseline metric reported as a number, never omitted.

## Fold plan
expanding_folds(first_date=STOCK_TRAINING_START=2016-06-01, last_date=panel_end, n_folds=7,
horizon_days=30 tail reserve, min_train_days=session index of panel_start, trade_dates=actual
sessions). Assert min_train_days >= 250. Purge = horizon sessions via train_dates_for_fold.
Embargo 0 (features backward-looking). Evaluation region [panel_start, panel_end - 30 sessions].

## Horizons and aggregation (O3 RESOLVED)
Horizons {3,7,30}. **30d primary** (only horizon with t>=2 today); 3d/7d secondary. An arm passes
iff the PRIMARY horizon passes ALL acceptance lines; cross-horizon disagreement reported explicitly.
Per-horizon verdicts recorded independently.

## Benchmarks (both reported, every run)
- Official full index (000905.SH / 000852.SH).
- Executable subset (ex-board, ex-ST) via CSI500_PIT_EXBOARD_ST / CSI1000_PIT_EXBOARD_ST.
- The gap reported as "the price of the permission constraint".

## Execution model and costs (O4 RESOLVED)
execution_model = next_day_open for acceptance (rebalance every `holding` sessions: rank at T close,
enter T+1 open ± slippage, exit at T+1+holding open; raw-price limit-lock with exchange 0.01 half-up
rounding; AssetSuspension deferral; retry<=3; as-of ST via intervals). same_day_close is D2 legacy.
Simulator selection + holding params = CORE_PROFILES base, PER HORIZON. The portfolio REBALANCES every
`holding` sessions and pays ONE round-trip cost per holding period, amortized to a daily return (NOT
daily rebalance — that over-applies cost ~holding-fold). The selection score is the model's directional
score **P(UP) − P(DOWN)** (the SAME score the acceptance rank IC uses), NOT raw P(UP) — ranking by
P(UP) alone was anti-predictive (gross OOS ≈ −12%/yr) while P(UP)−P(DOWN) carries the +0.05 IC signal.
The `up_threshold` is on that score (0.0 = UP > DOWN):
- h3: top_n=5, up_threshold=0.0, holding=5
- h7: top_n=5, up_threshold=0.0, holding=10
- h30: top_n=3, up_threshold=0.0, holding=20
Costs (rate-based, identical across arms): slippage 5bps, commission 0.00025, stamp duty 0.001
(sell-only), transfer fee 0.00001. cost_rate = 2·slip + 2·commission + stamp + transfer.
**DELIBERATELY OMITTED from the vectorized simulator: the CNY-5 minimum commission** (position-size
dependent, engine-level) — captured by the STK-10 full-engine runs + sampled reconciliation.
IS-Sharpe = train-slice vectorized top-N daily net-return Sharpe, annualized **sqrt(252)** (the
draft's sqrt(244) is corrected here), net of the same costs.
Reconciliation: sampled fold windows through the full engine inside
decision_replay.run_in_rolled_back_transaction; daily NAV difference < 1bp or attributed.

## Acceptance (momentum_rotation_alpha.md §3.4, one amendment)
- OOS annualised alpha > 5% after costs, **vs the EXECUTABLE benchmark** (not the official index)
- OOS/IS Sharpe > 0.5 (< 0.3 ⇒ severe overfit)
- Fold consistency: >= 5/7 folds beat the benchmark
- Rank IC mean > 0.03, ICIR > 0.3
- No catastrophic fold in any regime
- Beta/Alpha decomposition mandatory (O5 below); > 20%/yr ⇒ suspect small-cap beta first.

## Beta/Alpha attribution (O5 RESOLVED — transcribed from quantative.md 第一部分第六节)
Decompose P&L into four items: **Beta / Alpha / 执行偏差 (execution deviation) / 运气 (luck)**.
Only Alpha and execution deviation are improvable; Beta and luck are not.
- Beta: market exposure (regression beta vs official + executable benchmark) PLUS the size/liquidity
  premium (circ_mv small-minus-big spread; beta_size). >20%/yr alpha ⇒ suspect small-cap beta first.
- Alpha: intercept vs the EXECUTABLE benchmark, after costs (the improvable skill component).
- 执行偏差: fill slippage / limit-lock unfillability vs ideal (engine vs simulator reconciliation).
- 运气: residual (regime × performance matrix; not attributed to skill).

## Coexistence clause (declared before running)
Coexistence admitted ONLY IF CSI1000 fails AND CSI500 passes. Both fail ⇒ keep CSI500, negative
result. Both pass ⇒ replace with CSI1000. Coexistence is a verdict, never a preference.

## ST → market-cap arm trigger (declared before running)
Step 1 (coverage): ST layer removes < 1% of eligible ROWS on ex-board ⇒ add cap arms immediately.
Step 2 (effect): board-only vs board+ST per-fold rank IC on >= 3 starts. "Not measurable" iff BOTH
|delta mean IC| < 0.01 AND sign inconsistent across starts ⇒ add cap arms (same criteria/starts/purge,
one change at a time). Forbidden: skipping the IC criterion because an ST arm "looks better on Sharpe";
adjudicating on < 3 starts; changing ST and cap together.

## ST blind-spot protocol (declared before running)
Assets currently ST with no namechange history (DR6 diff) run two variants: exclude (dropped from all
history) vs include (treated non-ST). Report |delta mean rank IC|; < 0.01 ⇒ bound negligible, ST
enableable with report archived; otherwise ST stays disabled pending explanation.

## Bias register (accompanies every reading)
D1 in-sample accuracy favours larger universe [MITIGATED: purged-holdout fleet]
D2 same-day-close fill least executable in thin small caps [MITIGATED: next_day_open primary]
D3 limit-up/down unfillability [MITIGATED: A4 limit-lock + suspension]
D4 official benchmark contains untradeable weight [dual benchmark reported]
D5 small-cap beta decomposed from alpha [decomposition mandatory]
D6 LSY(2019) shell-value contamination [ST layer; cap arm conditional]
D7 STAR launch break — CORRECTED: first STAR constituents in both indices 2020-12-31 (measured
   UNI-06); post-2020-12 training-start arm is a SEPARATE preregistration if pursued
D8 untradeable weight time-varying [as-of filter]
D9 multiple testing [ledger context_repeats; frozen starts; coexistence only as verdict]
D10 macro_phase/yield_curve degeneracy [yield_curve removed v3; macro_phase non-constant;
    release_lag wired; yc_cb outage #183 recorded]

## Forbidden
- Any reading without a ledger row carrying this file's hash.
- Adding panel starts after seeing results.
- Reporting a winner while D1–D3 remain unmitigated.
- Editing this file after hashing (amendments = new file + new hash).

## Machine-readable manifest
```json
{
  "arms": ["board_only", "board_st"],
  "conditional_arms": ["cap"],
  "index_codes": ["000905.SH", "000852.SH"],
  "panel_starts": ["2018-01-02", "2019-01-02", "2020-01-02", "2021-01-04", "2022-01-04"],
  "panel_end": "2026-11-20",
  "fold_plan": {"type": "expanding", "n_folds": 7, "min_train_sessions": 250, "tail_reserve": 30},
  "horizons": [3, 7, 30],
  "primary_horizon": 30,
  "schema_version": "v3",
  "execution_model": "next_day_open",
  "legacy_execution_model": "same_day_close",
  "feature_names": ["rsi", "mom_5d", "rs_score", "rsi_lag_3d", "rsi_delta_3d", "mom_5d_delta_3d", "rs_score_delta_3d", "rsi_lag_5d", "rsi_delta_5d", "mom_5d_delta_5d", "rs_score_delta_5d", "rsi_lag_10d", "rsi_delta_10d", "mom_5d_delta_10d", "rs_score_delta_10d", "return_3d", "return_5d", "return_10d", "relative_volume_5d", "relative_volume_20d", "realized_volatility_5d", "pe_ttm_percentile", "pb_percentile", "roe_trend", "main_force_flow", "margin_flow", "factor_composite", "macro_phase", "pmi_manufacturing", "pmi_non_manufacturing", "rsi_x_relative_volume_5d", "rsi_x_macro_phase", "pe_ttm_percentile_x_macro_phase"],
  "simulator_params": {"common": {"slippage_bps": 5, "commission_rate": 0.00025, "stamp_duty_rate": 0.001, "transfer_fee_rate": 0.00001}, "3": {"top_n": 5, "up_threshold": 0.0, "holding": 5}, "7": {"top_n": 5, "up_threshold": 0.0, "holding": 10}, "30": {"top_n": 3, "up_threshold": 0.0, "holding": 20}},
  "st_blind_spot_variants": ["exclude", "include"],
  "benchmarks": {"official": ["000905.SH", "000852.SH"], "executable": ["CSI500_PIT_EXBOARD_ST", "CSI1000_PIT_EXBOARD_ST"]}
}
```