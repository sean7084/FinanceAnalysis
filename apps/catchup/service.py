"""The bounded catch-up loop.

Shaped after ``reconcile_prediction_coverage``: oldest hole first, one unit per
series per run, a hard budget, and a resumable design that needs no checkpoint
file. The detectors are data-level, so the database *is* the checkpoint -- the next
run re-detects whatever is still missing. ``CatchUpRun.units_remaining`` is
reporting, not state.

Two budgets bound a run because the failure modes differ. TuShare quotas are per
minute and per tier, and runbook-provider-blackout.md §3 is explicit that a
throttled half-finished backfill is worse than no backfill: it "leaves partial
coverage that looks like a blackout". The wall-clock budget exists so the hourly
beat entry fits inside its Celery time limit.
"""

import logging
import time
from dataclasses import replace

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.core.tushare_client import is_throttle_error
from apps.markets.calendar_utils import open_trading_dates
from apps.core.timez import session_today

from . import registry
from .detectors import CatchUpContext, calendar_scan_start
from .models import CatchUpRun
from .replay import classify_cause
from .repairs import RepairResult

logger = logging.getLogger(__name__)

LOCK_KEY = 'catchup_run_lock'

# Extra seconds of lock life beyond the run's own wall clock. The lock must outlive
# the run: a TTL shorter than max_seconds lets the hourly beat entry start a second
# pass while the startup hook is still working, and both then spend the same
# per-minute TuShare quota.
LOCK_MARGIN_SECONDS = 300

# Seconds kept in hand when deciding whether to start another unit, so a repair
# that runs long is stopped by the budget rather than by the worker's hard limit.
TIME_MARGIN_SECONDS = 60


class _StopCatchUp(Exception):
    """Raised internally to abandon the rest of this run.

    Carries the row that caused the stop: the repair was recorded before the raise,
    and a caller that only appends on a normal return would silently drop the one
    piece of evidence explaining why the run ended.
    """

    def __init__(self, row):
        super().__init__(f'{row.series}: {row.outcome}')
        self.row = row


class Budget:
    """Per-run provider-call and wall-clock accounting."""

    def __init__(self, max_calls, deadline, lock_ttl, holder):
        self.remaining_calls = int(max_calls)
        self.deadline = deadline
        self.lock_ttl = lock_ttl
        self.holder = holder
        self.spent_calls = 0

    def can_afford(self, calls, margin_seconds=TIME_MARGIN_SECONDS):
        return (self.remaining_calls >= int(calls)
                and time.monotonic() + margin_seconds < self.deadline)

    def spend(self, calls):
        self.remaining_calls -= int(calls)
        self.spent_calls += int(calls)
        # Refresh rather than re-add: this run already holds the lock, and letting
        # it lapse mid-run is exactly what the margin exists to prevent.
        cache.set(LOCK_KEY, self.holder, self.lock_ttl)
        return self.remaining_calls

    def affordable_assets(self, calls_per_asset):
        return max(1, self.remaining_calls // max(int(calls_per_asset), 1))

    def take_dates(self, dates, cost_per_unit):
        limit = max(1, self.remaining_calls // max(int(cost_per_unit), 1))
        return list(dates[:limit])


def lock_ttl_for(max_seconds):
    """The lock lifetime for a run of ``max_seconds``. Never shorter than the run."""
    return max(int(getattr(settings, 'CATCHUP_LOCK_SECONDS', 1500)),
               int(max_seconds) + LOCK_MARGIN_SECONDS)


def run_catch_up(*, as_of=None, series=None, max_calls=None, max_seconds=None,
                 dry_run=False, trigger=CatchUpRun.Trigger.MANUAL):
    """Repair what the outage cost, oldest first, within budget.

    Returns one ``CatchUpRun`` per series considered. Under ``dry_run`` the rows are
    built but not saved and no provider call is made.
    """
    as_of = as_of or session_today()
    max_calls = settings.CATCHUP_MAX_CALLS_PER_RUN if max_calls is None else int(max_calls)
    max_seconds = (settings.CATCHUP_MAX_SECONDS_PER_RUN if max_seconds is None
                   else int(max_seconds))
    lock_ttl = lock_ttl_for(max_seconds)

    if not cache.add(LOCK_KEY, trigger, lock_ttl):
        logger.info('catch-up already in flight (holder=%s); skipping trigger=%s',
                    cache.get(LOCK_KEY), trigger)
        return []

    budget = Budget(max_calls=max_calls, deadline=time.monotonic() + max_seconds,
                    lock_ttl=lock_ttl, holder=trigger)
    # Detector results are memoised per run because dependency gating re-asks about
    # upstream series, and a cross-sectional count over the universe is not free.
    memo = {}
    runs = []
    try:
        ctx = CatchUpContext(
            as_of=as_of, open_dates=(), window_start=calendar_scan_start(as_of),
            dry_run=dry_run, trigger=trigger,
        )
        # Calendar first, unconditionally: every other detector scans open trading
        # days, so a calendar hole makes their answers meaningless.
        calendar = registry.BY_NAME[registry.CALENDAR_SERIES]
        if _selected(calendar, series):
            try:
                runs.append(_repair_series(calendar, ctx, budget, memo))
            except _StopCatchUp as stop:
                runs.append(stop.row)
                return runs
        ctx = _with_open_dates(ctx)
        if not ctx.open_dates:
            logger.warning('no open trading dates up to %s; nothing downstream can be '
                           'scanned until the calendar is repaired', as_of)
            return runs

        for descriptor in registry.ordered(series):
            if descriptor.name == registry.CALENDAR_SERIES:
                continue
            if not budget.can_afford(descriptor.calls_per_unit):
                runs.append(_record(descriptor, ctx, None, memo, RepairResult(
                    units_done=0, units_remaining=1, estimated_calls_spent=0,
                    outcome=CatchUpRun.Outcome.PARTIAL,
                    detail='budget exhausted; next run continues')))
                break
            # Dry run is a survey, not a plan: nothing gets repaired, so gating on
            # upstream would report every dependent as blocked and hide the gaps the
            # run was asked to show.
            blocked = [] if ctx.dry_run else _blocking_upstream(descriptor, ctx, memo)
            if blocked:
                runs.append(_record(descriptor, ctx, None, memo, RepairResult(
                    units_done=0, units_remaining=1, estimated_calls_spent=0,
                    outcome=CatchUpRun.Outcome.BLOCKED_UPSTREAM,
                    detail=f'waiting on {", ".join(blocked)}')))
                continue
            try:
                runs.append(_repair_series(descriptor, ctx, budget, memo))
            except _StopCatchUp as stop:
                runs.append(stop.row)
                break
        return runs
    finally:
        cache.delete(LOCK_KEY)


def _selected(descriptor, series):
    return not series or descriptor.name in set(series)


def _with_open_dates(ctx):
    lookback = int(getattr(settings, 'CATCHUP_LOOKBACK_TRADING_DAYS', 60))
    dates = tuple(open_trading_dates(ctx.as_of, lookback, ctx.exchange_code))
    window_start = dates[0] if dates else calendar_scan_start(ctx.as_of)
    return replace(ctx, open_dates=dates, window_start=window_start)


def _detect(descriptor, ctx, memo):
    if descriptor.name not in memo:
        memo[descriptor.name] = descriptor.detector(ctx)
    return memo[descriptor.name]


def _blocking_upstream(descriptor, ctx, memo):
    """Upstream series that still have a gap after their own repair attempt."""
    blocked = []
    for name in descriptor.depends_on:
        upstream = registry.BY_NAME.get(name)
        if upstream is None:
            continue
        gap = _detect(upstream, ctx, memo)
        if gap is not None and not gap.is_empty:
            blocked.append(name)
    return blocked


def _repair_series(descriptor, ctx, budget, memo):
    gap = _detect(descriptor, ctx, memo)
    if gap.is_empty:
        return _record(descriptor, ctx, gap, memo, RepairResult(
            units_done=0, units_remaining=0, estimated_calls_spent=0,
            outcome=CatchUpRun.Outcome.NO_GAP, detail=gap.detail or 'complete'))
    if ctx.dry_run:
        return _record(descriptor, ctx, gap, memo, RepairResult(
            units_done=0, units_remaining=gap.unit_count, estimated_calls_spent=0,
            outcome=CatchUpRun.Outcome.SKIPPED, detail=f'dry run: {gap.detail}'))
    try:
        result = descriptor.repair(ctx, gap, budget)
    except SoftTimeLimitExceeded:
        raise _StopCatchUp(_record(descriptor, ctx, gap, memo, RepairResult(
            units_done=0, units_remaining=gap.unit_count, estimated_calls_spent=0,
            outcome=CatchUpRun.Outcome.PARTIAL,
            detail='soft time limit; next run continues')))
    except Exception as exc:
        # Being throttled and then continuing to spend quota is precisely how a
        # backfill ends up half-written and mistaken for a provider blackout.
        throttled = is_throttle_error(exc)
        row = _record(descriptor, ctx, gap, memo, RepairResult(
            units_done=0, units_remaining=gap.unit_count, estimated_calls_spent=0,
            outcome=CatchUpRun.Outcome.FAILED,
            cause=CatchUpRun.Cause.THROTTLED if throttled else '',
            detail=f'{type(exc).__name__}: {exc}'[:1000]))
        if throttled:
            raise _StopCatchUp(row)
        return row
    # The gap this detector found is now stale -- the repair acted on it. Drop it so
    # a downstream dependency check re-detects against the repaired data instead of
    # blocking on a hole that was just filled.
    memo.pop(descriptor.name, None)
    return _record(descriptor, ctx, gap, memo, result)


def _record(descriptor, ctx, gap, memo, result):
    """Persist one ``CatchUpRun``, deduping the outcomes that mean "nothing happened".

    An hourly tick across seventeen series would otherwise write hundreds of
    identical NO_GAP rows a day. Everything that is evidence -- OK, PARTIAL,
    BLOCKED_UPSTREAM, FAILED -- always inserts.
    """
    gap_start, gap_end = gap.span if gap is not None else (None, None)
    row = CatchUpRun(
        series=descriptor.name,
        triggered_by=ctx.trigger,
        outcome=result.outcome,
        cause=result.cause or classify_cause(
            descriptor.beat_task_name, ctx.as_of, 1, gap),
        gap_start=gap_start,
        gap_end=gap_end,
        repaired_dates=[day.isoformat() for day in result.repaired_dates],
        units_done=result.units_done,
        units_remaining=result.units_remaining,
        estimated_calls=result.estimated_calls_spent,
        detail=result.detail,
        finished_at=timezone.now(),
    )
    if not ctx.dry_run and not _already_recorded_today(row):
        row.save()
    memo[('__row__', descriptor.name)] = row
    return row


def _already_recorded_today(row):
    if row.outcome not in CatchUpRun.DEDUPED_OUTCOMES:
        return False
    return CatchUpRun.objects.filter(
        series=row.series,
        outcome=row.outcome,
        started_at__date=timezone.localdate(),
    ).exists()
