# STK-10 (partial): the permission price of the executable universe, measured at benchmark level

- Date: 2026-11-23
- Card: STK-10 — executability cost of the existing CSI500 backtest
- Status: **PARTIAL.** The two benchmark-level quantities the pre-registration declares are
  measured below. The strategy-level equity curves are **not** run, and the measured cost of
  running them is recorded with the reason.
- Pre-registration: `stk10-executable-universe-2026-11-23-v2.md`, digest `d8b09eb37d91`.
  **These numbers were NOT produced by that registered procedure** — they come directly from
  maintained benchmark series, not from the 18-cell backtest matrix. They must not be cited
  under that digest as if they were.
- Descriptive only. No acceptance criteria, no verdict, and per the pre-registration:
  *"No default changes from this run alone."*

## The question

CSI500 has **28.4% of constituents / 34.50% of weight** on boards this account cannot trade
(TuShare `index_weight` 20261030), and ChiNext/STAR stay locked until 2027-10-27. So every
existing CSI500 reading overstates the executable part. How much?

## Result

Compounding `PointInTimeBenchmarkDaily.daily_return` over **2018-01-02 .. 2026-11-20**
(2,155 rows per series, 2,150 open sessions, **0 missing**):

| series | role | end NAV factor | cumulative | CAGR |
| :-- | :-- | --: | --: | --: |
| `CSI500_PIT` | official, full universe | 3.5303 | +253.03% | **15.28%** |
| `CSI500_PIT_EXBOARD` | ex-board, ST included | 3.1341 | +213.41% | 13.75% |
| `CSI500_PIT_EXBOARD_ST` | **executable** (ex-board, ST excluded) | 3.1561 | +215.61% | **13.84%** |

- **Permission price = official − executable = 1.45 percentage points per year**, 37.42 pp
  cumulative over 8.9 years.
- **ST cost = ex-board − ex-board+ST = −0.09 pp/year** — *negative*: excluding ST very slightly
  **improved** returns.

Read plainly: the board-permission constraint costs about **1.45 pp of annual benchmark return**,
and the ST-exclusion layer costs nothing measurable.

## Why the ST cost is structurally ~zero

Not a null result from insufficient data — a structural one. `constituent_count` on the two
ex-board series differs by **at most one name**:

| series | 2018-01-02 | 2026-11-20 | min–max |
| :-- | --: | --: | :-- |
| `CSI500_PIT` | 500 | 500 | 500–500 |
| `CSI500_PIT_EXBOARD` | 465 | 358 | 358–465 |
| `CSI500_PIT_EXBOARD_ST` | 464 | 358 | 358–464 |

Within CSI500 the ST layer removes ≤1 constituent out of ~465, because an ST designation drops
the name at the next index review. It therefore cannot matter materially. This corroborates, from
an independent direction, both the settled comparison's "ST layer marginal and slightly negative"
and a direct probe of `effective_universe_tradeable_asset_ids` on five dates spanning the window
(ST cut 1 / 0 / 0 / 0 / 0). The same probe confirms the board cut 500 → 358 = **28.4%**, matching
the TuShare figure this card exists to price.

## Method validation

Three checks, because an unvalidated difference between two series is not a price.

1. **Comparability.** All three series carry `weighting_method = free_float_market_cap` and
   `benchmark_name = "CSI 500 PIT"`. Had the weighting differed, the 1.45 pp gap would have been
   a weighting artifact rather than a permission price.
2. **Coverage.** Each series spans 2018-01-02..2026-11-20 with 2,155 rows and covers all 2,150
   open sessions from `ExchangeTradingCalendar` (`is_open=True`) with **0 missing**. Residual
   anomaly: 5 rows per series fall on dates the calendar does not mark open.
3. **`nav` must NOT be used for cumulative comparison.** The table stores both `daily_return` and
   `nav`, and they disagree by orders of magnitude if compared naively: compounded 3.5303 /
   3.1341 / 3.1561 versus `nav` ratios 10.4092 / 0.9631 / 0.9637. The cause is that **`nav` is
   re-based to 100,000 at rebuild boundaries** — 3 resets for `CSI500_PIT`, 8 for each ex-board
   series inside the window — so it is segmented. `benchmarking.py:566` seeds a rebuild with
   `initial_nav=previous_row.nav`, and with no previous row it starts at 100,000.
   Testing `nav[t] == nav[t-1] * (1 + daily_return[t])` leaves only **4 inconsistent rows of
   2,155** for `CSI500_PIT` and **8** for each ex-board series, and those rows *are* the resets.
   `daily_return` is internally consistent and bounded — **min −8.82%, max +8.73%, zero rows with
   |r| > 11%** — so no re-basing artifact contaminates the compounding.
   Grepping every consumer confirms this is a **latent trap, not a live defect**: `nav` is only
   written by `apps/markets/benchmarking.py` (`:476`, `:525`) and read by `apps/markets/tests.py`;
   no production code reads it for cumulative performance, and the walk-forward provider's
   `benchmark_returns()` uses `daily_return`.

## What is NOT measured, and why

The pre-registration also declares **three equity curves per horizon** — the strategy's own
return under each universe arm. Those need the 18-cell full-engine matrix, and measurement says
it is not affordable as registered:

- A 60-session full-engine probe ran **5 h 49 m without finishing** (killed; it had held one
  Postgres transaction open that long, and a snapshot that old blocks vacuum database-wide —
  `analytics_technicalindicator` showed 50,687 dead tuples with autovacuum last run 2026-11-19).
  That gives a wall-clock lower bound of **>346 s/session**.
- Attribution: a candidate-level probe measured the **prediction** pass alone at **~75 s per
  date** (4 dates × 2 modes, cold caches, ~10 min total). So roughly **270 s/session is engine
  overhead** — trade simulation, map lookups, per-asset DB reads — and only ~75 s is LightGBM.
- At ≥346 s/session over 2,150 sessions, one prediction surface is ≥8.6 days. The 18-cell matrix
  is **≥138 days** in `arm` mode and **≥128 days** with superset sharing. The original "~1 week"
  estimate was wrong by roughly an order of magnitude; it was priced from
  `report.lightgbm_runtime`'s `asset_loop_seconds + trade_decision_seconds`, which are **compute**
  counters that exclude DB round-trip and NAS IO wait.
- Consequently the superset-sharing optimization (A-4) is worth ~10 days out of ~138, not the 3×
  originally assumed. **The real affordability lever is the engine's per-session IO — open issue
  #139** ("Backtest candidate day: batch the remaining per-asset reads").

The pre-registration's `chunk_trading_days=20` and one-process inline shape are both invalid at
this rate: 20 × 346 s = 6,920 s against a 2,100 s Celery hard limit (`tasks.py:2722`), and RSS
reached 12.3–13.9 GiB within 3 chunks against a 24 GiB cap. Either change requires a **v3
pre-registration with a new digest**, settled before any ledger registration row.

## Bias register (applies to the benchmark-level result)

- **D2 execution model:** not applicable — these are benchmark series, not fills. No
  `same_day_close` optimism is embedded here.
- **D3 cost:** the permission price is **gross of transaction cost**. It is a property of the
  index subsets, so cost does not enter; the strategy-level curves would need it.
- **Survivorship:** the PIT series are point-in-time by construction, and
  `apply_universe_policy` never reads `listing_status` / `delist_date`, so delisted non-ST names
  stay in the historical universe.
- **Window selection:** 2018-01-02 is `STOCK_BACKTEST_START` (`apps/core/date_contract.py:51`),
  not a cherry-picked start. `--allow-pre-contract-history` was not used and
  `allow_pre_contract_history` is absent, so this is not a research-only reading.
- **Unmeasured:** whether the *strategy* loses more or less than the benchmark does when
  restricted. A top-5 selection rule concentrates, so its executable-universe penalty could be
  larger or smaller than 1.45 pp/yr. That is precisely what the matrix would have answered.

## Reproduction

```bash
# coverage + comparability + the result, read-only
python manage.py shell -c "
import datetime as dt
from apps.markets.models import PointInTimeBenchmarkDaily as P
S, E = dt.date(2018,1,2), dt.date(2026,11,20)
for code in ('CSI500_PIT','CSI500_PIT_EXBOARD','CSI500_PIT_EXBOARD_ST'):
    q = P.objects.filter(benchmark_code=code, trade_date__gte=S, trade_date__lte=E).order_by('trade_date')
    nav = 1.0
    for r in q.values_list('daily_return', flat=True): nav *= 1.0 + float(r or 0)
    f = q.first(); l = q.last()
    print(code, len(q), round(nav,4), round((nav**(243.0/len(q))-1)*100,2),
          f.weighting_method, f.constituent_count, l.constituent_count)
"
```

Expected: three series of 2,155 rows, `free_float_market_cap`, NAV factors 3.5303 / 3.1341 /
3.1561, CAGR 15.28 / 13.75 / 13.84, constituent counts 500→500, 465→358, 464→358.

## Action

None. Per the pre-registration, a negative or positive gap here is **recorded, not acted on**:
*"do NOT change any default on that basis alone."* The universe decision is already settled
(keep CSI500, negative result archived, prereg `cabcf49a3c67`), and 1.45 pp/year does not reopen
it.
