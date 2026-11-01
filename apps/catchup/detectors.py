"""Per-series gap detection. Pure database queries -- no provider calls.

Detectors receive a ``CatchUpContext`` and never query the trading calendar
themselves. ``service.run_catch_up`` repairs the calendar first and then rebuilds
``ctx.open_dates``, which is what makes "calendar before everything that scans the
calendar" a structural property rather than a convention someone has to remember.

Because nothing here touches TuShare, ``--dry-run`` is genuinely zero-quota.

Three detection classes, and picking the wrong one is the expensive mistake:

``A`` row-presence
    An open trading day is *supposed* to have a row, so absence is a gap.

``B`` watermark only
    Absence is NOT evidence. ``AssetSuspension`` only holds rows for stocks that
    were actually suspended, so a day with no suspensions legitimately has no rows;
    ``IndexMembership`` comes from ``index_weight``, which publishes on its own
    cadence rather than daily. Scanning these for row presence produces permanent
    false positives and a catch-up that never converges. They are driven instead by
    ``PeriodicTask.last_run_at`` via :mod:`apps.catchup.replay`.

``C`` cross-sectional count
    Per-asset series, where a per-asset x per-day scan would both be slow and collide
    with the structural blackouts documented in runbook-provider-blackout.md §4
    (``margin_detail`` history starts later, ``main_force_net_5d`` starts later,
    some assets have null ``pe_ttm``). Those are permanent and must not be retried.
    A structural blackout removes rows for *some assets on all dates*; a fetch
    failure removes rows for *all assets on some dates*. Aggregating by date and
    comparing against the window's median separates the two.
"""

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db.models import Count

from apps.factors.models import CapitalFlowSnapshot, FundamentalFactorSnapshot
from apps.macro.models import MacroSnapshot
from apps.markets.models import BenchmarkIndexDaily, ExchangeTradingCalendar, OHLCV
from apps.markets.tasks import DEFAULT_INDEX_CODES
from apps.prediction.reconciliation import coverage_gaps

from .replay import missed_window

KIND_DATES = 'dates'
KIND_RANGE = 'range'
KIND_REPLAY = 'replay'

# The calendar detector runs before open_dates is known, so it needs a calendar-day
# window wide enough to contain CATCHUP_LOOKBACK_TRADING_DAYS trading days. Two
# calendar days per trading day covers the longest holiday stretch with room left.
CALENDAR_DAY_PADDING = 2

# Beat entry that owns the daily dispatcher and its post-sync chain. Indicators,
# factor scores and RS_SCORE have no beat entry of their own -- they run inside
# run_post_sync_universal_refresh, which is a chord callback -- so the dispatcher's
# watermark is the only evidence available for them.
DAILY_DISPATCHER_ENTRY = 'sync-a-shares-daily-from-tushare'


@dataclass(frozen=True)
class CatchUpContext:
    as_of: date
    open_dates: tuple
    window_start: date
    exchange_code: str = 'SSE'
    dry_run: bool = False
    trigger: str = 'manual'


@dataclass(frozen=True)
class SeriesGap:
    series: str
    kind: str
    missing_dates: tuple = ()
    window_start: date | None = None
    window_end: date | None = None
    estimated_calls: int = 0
    detail: str = ''

    @property
    def is_empty(self):
        if self.kind == KIND_RANGE:
            return self.window_start is None
        return not self.missing_dates

    @property
    def unit_count(self):
        if self.kind == KIND_RANGE:
            return 0 if self.window_start is None else 1
        return len(self.missing_dates)

    @property
    def span(self):
        """``(first, last)`` of whatever this gap covers, for the run record."""
        if self.kind == KIND_RANGE:
            return (self.window_start, self.window_end)
        if not self.missing_dates:
            return (None, None)
        return (self.missing_dates[0], self.missing_dates[-1])


def calendar_scan_start(as_of):
    """Calendar-day floor for the pre-open_dates calendar scan."""
    lookback = int(getattr(settings, 'CATCHUP_LOOKBACK_TRADING_DAYS', 60))
    return as_of - timedelta(days=lookback * CALENDAR_DAY_PADDING)


def _every_calendar_day(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _month_starts_in(start, end):
    """First-of-month stamps in ``[start, end]``, oldest first.

    Macro snapshots are stored on the first day of the month and populated from the
    first available trade date in that month, so month-start is the expected stamp
    -- not the first open trading day.
    """
    months = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        if cursor >= start:
            months.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)
    return months


NEIGHBOUR_WINDOW = 3


def cross_sectional_gap_dates(model, ctx, *, date_field='date', min_ratio=None):
    """Open dates whose row count collapsed against the dates on either side.

    The reference is deliberately local. A per-asset series drifts its whole
    universe across a 60-day window -- delistings, ST filtering, a redefined
    effective universe -- and a single window median then reads *every* date after
    the shift as broken. That failure mode is unrecoverable and permanent: no repair
    can restore assets that no longer exist, so the gap never clears and every
    dependent series blocks upstream forever.

    Comparing against both sides separates the two cases. A one-day fetch failure is
    low against its neighbours on each side; a regime shift is low against the past
    but in step with the future. A date with no rows at all is an unambiguous gap
    either way, so it never depends on the reference.
    """
    ratio = (
        float(getattr(settings, 'CATCHUP_CROSS_SECTION_MIN_RATIO', 0.5))
        if min_ratio is None else float(min_ratio)
    )
    if not ctx.open_dates:
        return []
    rows = (
        model.objects.filter(**{
            f'{date_field}__gte': ctx.window_start,
            f'{date_field}__lte': ctx.as_of,
        })
        .values(date_field)
        .annotate(n=Count('id'))
        .values_list(date_field, 'n')
    )
    counts = dict(rows)
    sizes = [counts.get(day, 0) for day in ctx.open_dates]

    def reference(index, step):
        """Median count of the NEIGHBOUR_WINDOW dates on one side.

        Zero counts stay in the median: a date ringed by an outage has no usable
        reference on that side, and a lowered median is safer than a missing one.
        """
        span = range(index + step, index + step * (NEIGHBOUR_WINDOW + 1), step)
        present = [sizes[i] for i in span if 0 <= i < len(sizes)]
        return statistics.median(present) if present else None

    gaps = []
    for index, count in enumerate(sizes):
        if count == 0:
            gaps.append(ctx.open_dates[index])
            continue
        sides = [ref for ref in (reference(index, -1), reference(index, 1)) if ref]
        if not sides:
            continue
        # Requiring *every* side to be higher is what makes a step change safe: at
        # the boundary the forward side is already in the new regime.
        if all(count < ref * ratio for ref in sides):
            gaps.append(ctx.open_dates[index])
    return gaps


# --- Class A: row presence -----------------------------------------------------

def detect_calendar_gap(ctx):
    """Whether the calendar itself has holes. Both exchanges are fetched together,
    so checking SSE alone is enough -- and everything downstream depends on this."""
    start = ctx.window_start
    expected = list(_every_calendar_day(start, ctx.as_of))
    stored = set(ExchangeTradingCalendar.objects.filter(
        exchange_code=ctx.exchange_code,
        trade_date__gte=start,
        trade_date__lte=ctx.as_of,
    ).values_list('trade_date', flat=True))
    missing = tuple(d for d in expected if d not in stored)
    return SeriesGap(
        'trading_calendar', KIND_DATES, missing_dates=missing, estimated_calls=2,
        detail=f'{len(missing)} of {len(expected)} calendar days absent for {ctx.exchange_code}',
    )


def detect_benchmark_gap(ctx):
    """Both benchmark codes, including the H00905.CSI total-return series.

    That series fails silently: TuShare returns an empty frame rather than raising,
    so a one-session lag nulls ``rs_vs_benchmark_*`` for the whole ETF pool and the
    RULE scorer reports a 100%-defensive book as though it were a market call. See
    runbook-sync-failure.md §5.1 for the 2026-10-22 incident.
    """
    missing = []
    for code in DEFAULT_INDEX_CODES:
        stored = set(BenchmarkIndexDaily.objects.filter(
            index_code=code,
            trade_date__gte=ctx.window_start,
            trade_date__lte=ctx.as_of,
        ).values_list('trade_date', flat=True))
        missing.extend((code, day) for day in ctx.open_dates if day not in stored)
    dates = tuple(sorted({day for _, day in missing}))
    detail = '; '.join(f'{code} missing {day}' for code, day in missing[:5])
    return SeriesGap(
        'benchmark_index', KIND_DATES, missing_dates=dates,
        estimated_calls=len(DEFAULT_INDEX_CODES),
        detail=detail or 'complete',
    )


def detect_etf_daily_gap(ctx):
    """Reuses the existing calendar-vs-rows scan rather than reimplementing it.

    ``missing_etf_daily_dates`` is already correct; the only thing wrong with the
    daily path is that ``sync_etf_daily_recent`` bounds it to a trailing 10 calendar
    days. Widening the window here leaves that beat entry alone.
    """
    from apps.etf_rotation.tasks import missing_etf_daily_dates

    dates = tuple(missing_etf_daily_dates(ctx.window_start, ctx.as_of, ctx.exchange_code))
    return SeriesGap(
        'etf_daily', KIND_DATES, missing_dates=dates,
        estimated_calls=2 * len(dates),   # fund_daily + fund_adj per date
        detail=f'{len(dates)} ETF trading dates absent',
    )


def detect_macro_gap(ctx):
    """Month-start stamps with no ``MacroSnapshot`` row."""
    months = tuple(_month_starts_in(ctx.window_start, ctx.as_of))
    stored = set(MacroSnapshot.objects.filter(
        date__gte=ctx.window_start, date__lte=ctx.as_of,
    ).values_list('date', flat=True))
    missing = tuple(m for m in months if m not in stored)
    return SeriesGap(
        'macro_core', KIND_DATES, missing_dates=missing, estimated_calls=5,
        detail=f'missing month-starts: {[m.isoformat() for m in missing]}' if missing else 'complete',
    )


# --- Class B: watermark --------------------------------------------------------

def watermark_detector(series_name, beat_entry, grace_days, estimated_calls=0, kind=KIND_RANGE):
    """Build a detector driven by ``PeriodicTask.last_run_at``.

    ``grace_days`` must be at least the entry's natural cadence or every run looks
    like an outage: a daily entry needs a day, a monthly one needs its whole month.
    """
    def detect(ctx):
        window = missed_window(beat_entry, ctx.as_of, grace_days)
        if window is None:
            return SeriesGap(series_name, kind, detail='watermark fresh')
        start, end = window
        if kind == KIND_REPLAY:
            dates = tuple(d for d in ctx.open_dates if start.date() <= d <= end.date())
            return SeriesGap(
                series_name, KIND_REPLAY, missing_dates=dates,
                estimated_calls=estimated_calls,
                detail=f'{len(dates)} trading days missed since {start:%Y-%m-%d}',
            )
        return SeriesGap(
            series_name, KIND_RANGE, window_start=start.date(), window_end=end.date(),
            estimated_calls=estimated_calls,
            detail=f'{beat_entry} last dispatched {start:%Y-%m-%d}',
        )
    return detect


# --- Class C: cross-sectional --------------------------------------------------

def detect_ohlcv_gap(ctx):
    dates = tuple(cross_sectional_gap_dates(OHLCV, ctx))
    return SeriesGap(
        'ohlcv', KIND_DATES, missing_dates=dates, estimated_calls=0,
        detail=(f'{len(dates)} dates with a collapsed cross-section; one '
                'sync_daily_a_shares run repairs all of them'),
    )


def detect_capital_flow_gap(ctx):
    dates = tuple(cross_sectional_gap_dates(CapitalFlowSnapshot, ctx))
    return SeriesGap(
        'capital_flow', KIND_DATES, missing_dates=dates, estimated_calls=2,
        detail=f'{len(dates)} dates with a collapsed cross-section',
    )


def detect_fundamentals_gap(ctx):
    dates = tuple(cross_sectional_gap_dates(FundamentalFactorSnapshot, ctx))
    return SeriesGap(
        'fundamentals', KIND_DATES, missing_dates=dates, estimated_calls=3,
        detail=f'{len(dates)} dates with a collapsed cross-section',
    )


# --- Derived layer -------------------------------------------------------------

def detect_prediction_gap(ctx):
    """Stored-prediction coverage, via the reconcile module that already owns it.

    Deliberately not a watermark: ``reconcile-prediction-coverage-hourly`` fires
    every hour and self-heals, so its ``last_run_at`` is never stale and would hide
    exactly the holes this exists to find. ``coverage_gaps`` is the data-level
    question and it is already written and tested.
    """
    gaps = coverage_gaps(as_of=ctx.as_of,
                         lookback_trading_days=settings.PREDICTION_RECONCILE_LOOKBACK_TRADING_DAYS)
    dates = tuple(sorted({g.trade_date for g in gaps if g.missing_asset_ids}))
    return SeriesGap(
        'predictions', KIND_REPLAY, missing_dates=dates, estimated_calls=0,
        detail=f'{len(dates)} trading dates with missing stored predictions',
    )
