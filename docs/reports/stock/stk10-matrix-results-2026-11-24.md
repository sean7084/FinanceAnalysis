# STK-10 executable-universe matrix results — v3 `504688d8bfb1`

**Descriptive measurement. No verdict and no default changes.**

## Matrix completion

- 18/18 cells `COMPLETED`; no `runtime_state` remains.
- Window: 2018-01-02..2026-11-20 (2,150 open sessions).
- Superset prediction sharing: the first cell of each horizon built the surface; every later cell recorded 2,140–2,150 matrix-cache hits.
- One NAS Postgres interruption was resumed from `runtime_state`; no matrix rows were recreated.

## Strategy results

| horizon | policy | ST | execution | run | trades | final value | total return | max DD | Sharpe | cache hits |
| --: | :-- | :-- | :-- | --: | --: | --: | --: | --: | --: | --: |
| 7 | research_full | false | next_day_open | 1567 | 3548 | 858971.25 | 329.49% | 34.23% | 1.27 | 0 |
| 7 | research_full | false | same_day_close | 1568 | 3519 | 821246.17 | 310.62% | 41.10% | 1.14 | 2147 |
| 7 | default | false | next_day_open | 1569 | 3264 | 821128.41 | 310.56% | 29.87% | 1.36 | 2147 |
| 7 | default | false | same_day_close | 1570 | 3322 | 796252.37 | 298.13% | 30.81% | 1.26 | 2147 |
| 7 | default | true | next_day_open | 1571 | 3251 | 845720.27 | 322.86% | 27.03% | 1.43 | 2147 |
| 7 | default | true | same_day_close | 1572 | 3258 | 828745.75 | 314.37% | 28.62% | 1.35 | 2147 |
| 3 | research_full | false | next_day_open | 1573 | 1663 | 477439.27 | 138.72% | 18.49% | 1.12 | 0 |
| 3 | research_full | false | same_day_close | 1574 | 1665 | 488311.63 | 144.16% | 19.07% | 1.08 | 2150 |
| 3 | default | false | next_day_open | 1575 | 1509 | 428791.68 | 114.40% | 16.83% | 1.02 | 2150 |
| 3 | default | false | same_day_close | 1576 | 1509 | 429170.77 | 114.59% | 18.17% | 0.95 | 2150 |
| 3 | default | true | next_day_open | 1577 | 1496 | 416671.03 | 108.34% | 16.24% | 0.97 | 2150 |
| 3 | default | true | same_day_close | 1578 | 1496 | 417550.65 | 108.78% | 17.13% | 0.91 | 2150 |
| 30 | research_full | false | next_day_open | 1579 | 3517 | 1226585.37 | 513.29% | 32.45% | 1.28 | 0 |
| 30 | research_full | false | same_day_close | 1580 | 3227 | 1021764.64 | 410.88% | 37.58% | 1.08 | 2140 |
| 30 | default | false | next_day_open | 1581 | 3545 | 1235024.61 | 517.51% | 30.43% | 1.35 | 2140 |
| 30 | default | false | same_day_close | 1582 | 3157 | 1016983.64 | 408.49% | 36.88% | 1.15 | 2140 |
| 30 | default | true | next_day_open | 1583 | 3780 | 1510762.22 | 655.38% | 30.98% | 1.57 | 2140 |
| 30 | default | true | same_day_close | 1584 | 3667 | 1358236.89 | 579.12% | 35.78% | 1.37 | 2140 |

## Benchmark-level quantities

| series | CAGR | cumulative | constituents first → last (min–max) |
| :-- | --: | --: | :-- |
| CSI500_PIT | 15.25% | 253.03% | 500 → 500 (500–500) |
| CSI500_PIT_EXBOARD | 13.72% | 213.41% | 465 → 358 (358–465) |
| CSI500_PIT_EXBOARD_ST | 13.81% | 215.61% | 464 → 358 (358–464) |

- **Permission price:** 1.44 pp/year; 37.42 pp cumulative.
- **ST cost:** -0.09 pp/year.
- These benchmark-level numbers are a separate descriptive measurement; they are not produced by the 18-cell matrix.

## D10 reconciliation

D10 used source run **1571**, window **2026-10-26..2026-11-20** (20 sessions).

- Simulator filled positions: 10; compared points: 10; coverage gap: false.
- Simulator return: **+7.1045%**; engine return: **+1.4413%**.
- Max absolute normalized-NAV difference: **0.0416586** (≈417 bp); all 10 points breach 1 bp.
- **D10 does not meet the 1 bp target.** The difference is structurally attributed to the
  simulator's flat start, single-rebalance equal-weight construction, and omitted minimum
  commission/exchange fees; see `stk10-d10-reconciliation-2026-11-24.md`. All strategy
  results above come from the full engine, not the simulator.

## Bias register D1–D10

- D1 universe: PIT membership and as-of ST; nested arms verified on five dates.
- D2 execution model: both `next_day_open` and `same_day_close` reported.
- D3 ST layer: explicit per-run override; ST cut measured 1/0/0/0/0 on five dates.
- D4 board permission: full/research vs ex-board arms separated.
- D5 model pin: active artifacts 46/47/48; no retrain.
- D6 date contract: window starts exactly at `STOCK_BACKTEST_START`; override absent.
- D7 fees: engine defaults with CNY-5 minimum commission retained.
- D8 benchmark handling: cumulative returns use `daily_return`; re-based `nav` is not used.
- D9 ledger: v3 registration precedes all runs; 18 result rows carry the digest.
- D10 simulator/engine reconciliation: see above.

## Interpretation

The matrix quantifies how the existing strategy reading changes under executable-universe constraints. Negative or positive gaps are recorded as numbers; no default is changed from this measurement alone.
