"""Decide whether one asset's stored prices were written under a single adjustment basis.

TuShare's forward-adjusted price is rebased to the newest adjustment factor it knows at
query time, so a row satisfies ``stored = raw * adj_factor / B`` for whatever ``B`` was
current when the row was written. An asset that was synced once is internally consistent;
an asset that kept gaining rows across an ex-dividend event has rows on two or more
bases, and every return spanning the boundary carries a phantom step.

The step is roughly the dividend yield -- 1% to 3%, and up to tens of percent for a
bonus issue -- which is *smaller than the narrowest daily price limit*. That is why this
compares against a reference basis rather than looking for limit breaches: a breach test
cannot see the defect at all.

Dividing by a stored price that the provider rounded to two decimals injects noise of
about ``0.005 / price``, so a row only counts as off-basis when its deviation clears
both an absolute floor and that per-row noise band.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

#: Half the 0.01 tick TuShare prices are rounded to.
PRICE_TICK_HALF = Decimal('0.005')

#: Below this a deviation is not distinguishable from rounding on a moderately priced
#: stock, so it is not evidence of a second basis.
MIN_DEVIATION = Decimal('0.003')

#: How many noise bands wide the per-row test has to be before a row is called off-basis.
NOISE_BANDS = Decimal('3')


@dataclass(frozen=True)
class BasisFinding:
    """What one asset's stored prices say about the basis they were written under."""

    ts_code: str
    rows_compared: int
    rows_skipped: int
    reference_factor: Decimal | None
    off_basis_rows: int
    max_deviation: Decimal
    first_off_basis_date: date | None
    last_off_basis_date: date | None
    contiguous_prefix: bool
    exdiv_dates_in_span: int

    @property
    def drifted(self) -> bool:
        return self.off_basis_rows > 0

    @property
    def off_basis_share(self) -> Decimal:
        if not self.rows_compared:
            return Decimal(0)
        return Decimal(self.off_basis_rows) / Decimal(self.rows_compared)

    def as_row(self):
        return {
            'ts_code': self.ts_code,
            'rows_compared': self.rows_compared,
            'rows_skipped': self.rows_skipped,
            'reference_factor': self.reference_factor,
            'off_basis_rows': self.off_basis_rows,
            'off_basis_share': self.off_basis_share.quantize(Decimal('0.0001')),
            'max_deviation_pct': (self.max_deviation * 100).quantize(Decimal('0.0001')),
            'first_off_basis_date': self.first_off_basis_date,
            'last_off_basis_date': self.last_off_basis_date,
            'contiguous_prefix': self.contiguous_prefix,
            'exdiv_dates_in_span': self.exdiv_dates_in_span,
            'verdict': 'drifted' if self.drifted else 'consistent',
        }


def deviation_of(stored_close, raw_close, adj_factor, reference_factor):
    """How far one row sits from ``reference_factor`` as its basis.

    ``1.0`` means the row was written under the reference basis. ``1.05`` means it was
    written when the newest factor was 5% lower, i.e. a re-basing has happened since.
    ``None`` when the row cannot be judged at all.
    """
    if not stored_close or not raw_close or not adj_factor or not reference_factor:
        return None
    if stored_close <= 0 or raw_close <= 0 or adj_factor <= 0 or reference_factor <= 0:
        return None
    expected = raw_close * adj_factor / reference_factor
    if expected <= 0:
        return None
    return stored_close / expected


def audit_asset_basis(ts_code, stored_closes, raw_closes, factors):
    """Compare stored OHLCV closes against raw prices and adjustment factors.

    ``stored_closes``, ``raw_closes`` and ``factors`` all map a trade date to a
    ``Decimal``. Only dates present in all three are judged; ``raw_closes`` and
    ``factors`` come from the provider, so a provider gap simply shrinks the sample.

    The reference is the factor on the newest date the asset actually has a stored row
    for, not the newest factor the provider knows: the writer used "latest at write
    time", and a factor that moved after the last sync would otherwise tilt every row.
    """
    reference_dates = [day for day in stored_closes if day in factors]
    if not reference_dates:
        return BasisFinding(ts_code, 0, len(stored_closes), None, 0, Decimal(0),
                            None, None, True, 0)
    reference_factor = factors[max(reference_dates)]

    off_dates = []
    compared = 0
    skipped = 0
    max_deviation = Decimal(0)

    for trade_date in sorted(reference_dates):
        raw_close = raw_closes.get(trade_date)
        deviation = deviation_of(stored_closes[trade_date], raw_close,
                                 factors[trade_date], reference_factor)
        if deviation is None:
            skipped += 1
            continue
        compared += 1
        distance = abs(deviation - 1)
        if distance > max_deviation:
            max_deviation = distance
        # A cheap stock carries proportionally more of the provider's two-decimal
        # rounding, so its noise band is wider than an expensive one's.
        noise = PRICE_TICK_HALF / stored_closes[trade_date]
        if distance > max(MIN_DEVIATION, NOISE_BANDS * noise):
            off_dates.append(trade_date)

    contiguous = False
    if off_dates:
        ordered = [day for day in sorted(stored_closes) if day in factors]
        first_index = ordered.index(off_dates[0])
        last_index = ordered.index(off_dates[-1])
        contiguous = last_index - first_index + 1 == len(off_dates) and first_index == 0

    exdiv_in_span = 0
    if off_dates:
        window_start, window_end = off_dates[0], off_dates[-1]
        previous_factor = None
        for trade_date in sorted(factors):
            if trade_date < window_start:
                previous_factor = factors[trade_date]
                continue
            if trade_date > window_end:
                break
            if previous_factor is not None and factors[trade_date] != previous_factor:
                exdiv_in_span += 1
            previous_factor = factors[trade_date]

    return BasisFinding(
        ts_code=ts_code,
        rows_compared=compared,
        rows_skipped=skipped,
        reference_factor=reference_factor,
        off_basis_rows=len(off_dates),
        max_deviation=max_deviation,
        first_off_basis_date=off_dates[0] if off_dates else None,
        last_off_basis_date=off_dates[-1] if off_dates else None,
        contiguous_prefix=contiguous,
        exdiv_dates_in_span=exdiv_in_span,
    )
