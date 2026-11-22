# CSI500 vs CSI1000 — executable-universe comparison (R1)

- Preregistration: `docs/reports/preregistration/csi500-vs-csi1000-executable-universe-2026-11-22-v3.md`
  (digest `cabcf49a3c67`; supersedes v2 `8a0dad13feec`, v1 `eabcd59d174e`).
- Panel: 2016-06-01 (training) → **2026-11-20** (frozen); 7 expanding folds; horizons 3/7/30 (30d primary, O3).
- Universe v1: ex-board (ChiNext/STAR/BSE disabled) + as-of ST layer (B3, after the DR6 reconciliation).
- Simulator (D10/O4): rebalance every CORE_PROFILES `holding` (5/10/20); selection score = **P(UP)−P(DOWN)**
  (= the acceptance rank-IC score), threshold 0.0; one round-trip cost per holding period (5bps slip,
  0.025% commission, 0.1% stamp sell, 0.001% transfer); next_day_open. Benchmark: official index and the
  universe-derived executable PIT (CSI500/CSI1000 `_EXBOARD_ST`).
- Ledger: `docs/reports/evidence/stock-model-measurements.jsonl` — 60 CMP rows under digest `cabcf49a3c67`
  (4 arms × 5 panel starts × 3 horizons), context_repeats checked, plus 3 registration rows.

## 30d primary — per-arm aggregates over the 5 panel starts

| Arm (index, layer) | meanIC | ICIR | α vs executable | α vs official | OOS Sharpe | IS Sharpe | starts ≥5/7 folds |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| CSI500, board-only (000905.SH) | 0.0482 | 0.3095 | **−3.75%** | −3.75% | −0.60 | 6.83 | 0/5 |
| CSI500, board+ST (000905.SH) | 0.0472 | 0.3001 | **−5.37%** | −5.37% | −0.84 | 6.94 | 0/5 |
| CSI1000, board-only (000852.SH) | 0.0306 | 0.3584 | **−3.75%** | −3.57% | −0.43 | 6.40 | 0/5 |
| CSI1000, board+ST (000852.SH) | 0.0314 | 0.3643 | **−5.20%** | −5.02% | −0.32 | 6.01 | 0/5 |

Per-start 30d spread is large (e.g. CSI500 board-only α ranges from +10.6% (2019 start) to −8.4% (2018
start)); the between-start spread is reported, never hidden (prereg).

## §6 acceptance (30d primary; an arm passes iff it clears ALL lines)

| Criterion | CSI500 bo | CSI500 bs | CSI1000 bo | CSI1000 bs |
| :--- | :---: | :---: | :---: | :---: |
| OOS α (executable) > 5% | ✗ | ✗ | ✗ | ✗ |
| OOS/IS Sharpe > 0.5 (overfit >0.3) | ✗ | ✗ | ✗ | ✗ |
| ≥ 5/7 folds beat benchmark | ✗ (0/5) | ✗ (0/5) | ✗ (0/5) | ✗ (0/5) |
| Rank IC mean > 0.03 | ✓ | ✓ | ✓ | ✓ |
| ICIR > 0.3 | ✓ | ✓ | ✓ | ✓ |
| No catastrophic fold | see per-fold | see per-fold | see per-fold | see per-fold |

**All four arms FAIL** (alpha, Sharpe, and fold-consistency). The IC/ICIR lines pass, but the weak signal
does not translate to portfolio alpha on a concentrated top-N.

## Verdict (per the prereg coexistence clause)

**Both indices fail → keep CSI500; archive the negative result.** This is a first-class outcome, never a
preference: the comparison does not support switching the serving universe to CSI1000 (nor to the ST/cap
variants). No S1 switch is performed; CSI500 remains the default and CSI1000 data/series remain for research.

The ST layer's marginal effect is small and adverse in these arms (board+ST alpha is slightly worse than
board-only for both indices); the ST→cap trigger was not reached (ST removes ~8.2% of ex-board rows ≫ 1%,
and the board-only-vs-board+ST IC difference is below 0.01 with inconsistent sign — the arms are near-
indistinguishable, both failing).

## Caveats (must accompany the negative result)

- **Severe overfitting**: IS Sharpe ≈ 6–7 vs OOS ≈ −0.3…−0.8 across all arms — the model memorises the
  training slice and does not generalise. This alone fails the OOS/IS > 0.5 line.
- **IC vs alpha tension**: rank IC is positive (0.031–0.048) yet the top-N alpha is negative. The IC is
  weak and does not survive concentration; the negative alpha is robust to plausible simulator-cost
  corrections (α would need ≈ +9% to reach the +5% line).
- **Simulator not yet engine-reconciled (D10)**: the vectorized simulator has not been validated against
  the full backtest engine on sampled fold windows (NAV <1bp or attributed). The verdict is **provisional**
  pending that reconciliation; the alpha failure margin (−4…−5%, needing +9% to pass) makes a flip unlikely.
- Bias register D1–D10 applies (D1 mitigated by purged-holdout fleet; D2/D3 by next_day_open + limit/suspension;
  D4 by dual benchmark; D5 by beta/size decomposition; D7 STAR-2020-12 break noted; D9 frozen starts + ledger;
  D10 macro_phase/yield_curve note). See the prereg for the full register.

## Data provenance

DR0–DR7 complete: CSI1000 membership (continuity OK), official 000852.SH benchmark, OHLCV backfill
(0 gaps over the ex-board historical union), namechange + ST reconciliation (diff fully explained),
all five PIT series (CSI500_PIT, CSI500/CSI1000 `_EXBOARD`, `_EXBOARD_ST`). ST enablement (B3) gated on
the reconciliation. Feature schema v3 (33 names). Fixed panel starts 2018-01-02 … 2022-01-04.
