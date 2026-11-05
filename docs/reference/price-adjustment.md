# Price adjustment semantics

How the stored OHLCV prices relate to traded prices, and why that matters.

Nothing else in the repository documents this. The columns are named `open`,
`high`, `low`, `close`, which reads as if they were the prices that traded on the
day. They are not.

## What is stored today

`apps/markets.models.OHLCV` is written only by
[`sync_asset_history`](../../apps/markets/tasks.py), which historically fetched
`ts.pro_bar(adj='qfq')` and stored the **forward-adjusted (前复权)** result in
`open`/`high`/`low`/`close`. `adj_close` was set to the same value, so the two
columns carried identical information and the unadjusted traded price was
discarded. `adj_factor` was never stored.

`volume` is shares (TuShare returns 手; the writer multiplies by 100).
`amount` is in **千元** — TuShare's raw unit, unconverted. Note that
`ETFDaily.amount` stores **元** (multiplied by `TUSHARE_AMOUNT_QIAN_YUAN_TO_YUAN`
in `apps/etf_rotation/tasks.py`), so the two tables differ by 1000x. Do not join
them on `amount` without converting.

## The drift mechanism

TuShare's `pro_bar(adj='qfq')` re-bases to the **latest adjustment factor it
knows at query time** — not to the factor as of `end_date`. Verified
empirically (see below): asking for the same historical dates under two different
`end_date` values returns different prices for every one of those dates, the
ratio between the two answers being constant across the window.

The consequence for an append-only writer: after any ex-dividend or split event,
every price TuShare would return for an earlier date moves. Rows already in the
database were written under the old basis; rows fetched afterward are written
under the new one. The series is no longer internally consistent — there is a
step change at each seam, and it looks exactly like a real price move.

**What `max_deviation` means.** It is the total spread of bases inside one asset's
series — the distance between its oldest surviving row and the basis the newest rows
use — not one day's phantom return. An asset that accumulated three dividends since
its last full re-fetch shows the compounded effect of all three. A single event's
step is visible in the provider's own factor ratio (603737.SH: 71.4% total spread,
of which the most recent event alone accounts for 20.2%).

Full census, 2026-11-03, 479 trading dates over 1,767 measurable assets:

| Population | Drifted | Median spread |
|---|---|---|
| whole universe | 679 / 1767 (38%) | 1.93% |
| synced universe (index-tagged) | 268 / 500 (**54%**) | 1.81% |
| outside the sync scope | 411 / 1267 (32%) | 2.00% |

p90 spread 5.33%, max 71.4%. **657 of the 679 have more than half their rows
off-basis** — so the damage is not one corrupted day but a majority of the series
sitting on a basis the newest rows no longer use.

Cross-checks that agree with the provider's own factor history: `000001.SZ` 3.2907%
(census) against 3.28% from an independent per-asset scan; `300803.SZ` 45.0207%
against factor 1.4555 → 2.1106 = 45.01%, with the off-basis span ending exactly on
2026-09-21, the session before the factor moved.

## Why a price-limit test cannot find it

The obvious detector — flag any one-session move larger than the board's limit —
does not work, and was tried. A re-basing shifts the series by roughly the dividend
yield, **1% to 3%**, which is smaller than the narrowest limit the market imposes
(ST main board at ±5%). On 600519.SH's 2026-06-25 seam the stock had genuinely risen
0.37%; the stored series records −1.95%, because the row was written after the basis
moved. Nothing about that row is illegal, so a limit test reports the file clean.

The only exceptions are bonus issues, where the factor can nearly double. Those
stand out; ordinary cash dividends, which are the overwhelming majority, do not.

## Detecting it

A row satisfies `stored = raw × adj_factor ÷ B`, so with the provider's raw close and
factor the basis `B` a row was written under is recoverable. Compare every row against
one reference basis and the populations separate:

```
deviation = stored_close / (raw_close × adj_factor ÷ reference)
```

`reference` must be the factor on the asset's **newest stored row**, not the newest
factor the provider knows. The writer used "latest at write time", so if the provider
moved after the last sync, every single row reads as off by that step and the asset
looks uniformly contaminated. Using the provider's newest factor is what made a first
attempt report 100% of a test asset's rows as drifted.

Clean assets stay under ~0.3% deviation; drifted ones sit at 1% to 49%. The two
populations do not overlap, because the residual is the provider's two-decimal price
rounding — roughly `0.005 ÷ price`, which the test has to clear per row.

**Coverage must be dense.** A seam is a step that both sides keep, so a sample lying
entirely on one side of it sees perfect agreement. An evenly-spaced nine-date census
reported 5% on a universe a dense pass put at 37%. Truncate the window to save
calls; never thin it.

`apps/markets/adjustment_basis.py` implements the comparison and
`manage.py audit_adjustment_drift` censuses the universe against it. Cost is
two TuShare calls per *trading day* — `daily(trade_date=D)` and
`adj_factor(trade_date=D)` each return every listed stock — so a year of dense
coverage for 1,763 assets runs about 500 calls rather than 3,500.

## Reproducing qfq locally

`qfq = raw_price × adj_factor ÷ base`, with `base` the latest factor for the
asset. Because `adj_factor` is stored per row, re-basing the whole history after
an ex-dividend event is a single local `UPDATE` — no provider calls.

Spike: fetch `pro.daily` and `pro.adj_factor` for an asset, compute
`raw × adj_factor ÷ latest_factor`, and compare row by row against
`ts.pro_bar(adj='qfq')` over the same window. Run it across deliberately different
price levels — three assets at ≈11, ≈40 and ≈1275 CNY were used — because the
distinguishing evidence is that the residual does **not** grow with price.

The maximum absolute difference between locally computed qfq and
`pro_bar(adj='qfq')` was **0.00499 CNY on every asset** — capped just under half
a cent regardless of price. That is not a formula mismatch; it is TuShare
rounding its *own output* to 2 decimal places. Since the error does not scale
with price, the local formula is exact and the residual is entirely the vendor's
display precision. The storage column is `Decimal(12,4)`, so we keep more
precision than the provider publishes.

Two corollaries:

- Locally stored qfq will not bit-match the values shown on the TuShare website.
  That is expected and is the better trade.
- Do not judge a re-implementation against an absolute tolerance of `0.0001`.
  The vendor cannot supply that. Compare return series, or compare with a
  tolerance of at least `0.005`.

## Triggering a re-base at write time

The census above compares whole populations. The writer needs a cheaper, sharper
question: has this asset just gone ex-dividend? Compare the newly fetched
`adj_factor` against the stored `adj_factor` on the asset's most recent row. It is a
direct comparison with no division and no precision loss, and it is exact.

Recovering a historical basis from `raw_close × adj_factor ÷ close` works for the
census, where one reference is compared against many rows, but it inherits the
two-decimal rounding of `close` and is loose for a low-priced asset with a large
factor (`000001.SZ`: factor 142.07, recovered to ±0.066). Do not use it for the
write-path trigger; use it only with the per-row noise band the test already applies.

## Re-basing is all-or-nothing per asset

Forward adjustment is a property of the whole series, not of one row. Re-basing
only the rows that happen to carry `raw_close` leaves the rest on the old basis
and **adds** a seam rather than removing one. Never partially re-base an asset:
either every row has `raw_close` and the whole series is re-based, or existing
rows are left untouched and the asset is marked for full backfill.
