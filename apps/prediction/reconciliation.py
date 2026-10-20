"""Which stored predictions are missing, and what it takes to heal them.

The daily sweeps write the whole universe inside a single task, so a sweep that
runs out of time leaves a trading date with a handful of rows rather than none.
Beat cannot tell the difference: it fires on a schedule, and an occurrence it
missed is never replayed with its original target date. So "the daily task ran"
is not evidence that the rows exist -- only that something started.

These helpers answer the question the dashboard actually cares about: for this
open trading date, which universe members have no stored prediction?
"""

from dataclasses import dataclass
from datetime import date

from django.conf import settings

from apps.markets.benchmarking import effective_universe_tradeable_assets
from apps.markets.models import ExchangeTradingCalendar

from .models import LightGBMPrediction, PredictionResult

ENSEMBLE = 'ensemble'
LIGHTGBM = 'lightgbm'

MODELS = {
    ENSEMBLE: PredictionResult,
    LIGHTGBM: LightGBMPrediction,
}

RECONCILE_SOURCES = (ENSEMBLE, LIGHTGBM)


def open_trading_dates(as_of, lookback_trading_days, exchange_code='SSE'):
    """The last `lookback_trading_days` open dates up to and including `as_of`.

    Oldest first, so a repair pass heals the backlog in date order.
    """
    count = max(int(lookback_trading_days or 0), 0)
    if count <= 0:
        return []
    resolved_as_of = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
    dates = (
        ExchangeTradingCalendar.objects.filter(
            exchange_code=exchange_code,
            is_open=True,
            trade_date__lte=resolved_as_of,
        )
        .order_by('-trade_date')
        .values_list('trade_date', flat=True)[:count]
    )
    return list(reversed(dates))


def stored_asset_ids(source, trade_date):
    """Asset ids with at least one stored row for `trade_date`.

    Presence is per asset, not per (asset, horizon): the sweep writes every
    horizon for one asset before moving to the next, and LightGBM legitimately
    skips a horizon when no artifact covers it, so a horizon-shaped gap is
    indistinguishable from a correct one.
    """
    return set(MODELS[source].objects.filter(date=trade_date).values_list('asset_id', flat=True))


@dataclass(frozen=True)
class PredictionGap:
    source: str
    trade_date: date
    universe_size: int
    stored_asset_count: int
    missing_asset_ids: tuple

    @property
    def is_complete(self):
        return not self.missing_asset_ids

    def summary(self):
        return (
            f'{self.trade_date} {self.source}: {self.stored_asset_count}/'
            f'{self.universe_size} assets stored, {len(self.missing_asset_ids)} missing'
        )


def gaps_for_date(trade_date, sources=RECONCILE_SOURCES, universe=None):
    if universe is None:
        universe = effective_universe_tradeable_assets(
            trade_date,
            context=f'Prediction reconciliation for {trade_date}',
        )
    universe_ids = tuple(asset.id for asset in universe)
    universe_id_set = set(universe_ids)
    gaps = []
    for source in sources:
        present = stored_asset_ids(source, trade_date)
        gaps.append(PredictionGap(
            source=source,
            trade_date=trade_date,
            universe_size=len(universe_ids),
            stored_asset_count=len(present & universe_id_set),
            missing_asset_ids=tuple(asset_id for asset_id in universe_ids if asset_id not in present),
        ))
    return gaps


def coverage_gaps(as_of=None, lookback_trading_days=None, sources=RECONCILE_SOURCES):
    """Gaps across the lookback window, oldest date first."""
    resolved_lookback = (
        settings.PREDICTION_RECONCILE_LOOKBACK_TRADING_DAYS
        if lookback_trading_days is None
        else int(lookback_trading_days)
    )
    resolved_as_of = as_of if isinstance(as_of, date) else (as_of or date.today())
    gaps = []
    for trade_date in open_trading_dates(resolved_as_of, resolved_lookback):
        gaps.extend(gaps_for_date(trade_date, sources=sources))
    return gaps
