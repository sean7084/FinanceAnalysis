"""Append-only evidence that the signal job ran, and that the feed behind it was current.

The database used to record only the strategy's output: a refusal raised inside
``generate_rotation_signal`` reached the worker log and nothing else, so "the market said
hold" and "the job never fired" were indistinguishable from the outside. This module writes
one row per attempt (and one per stale feed check) and builds the diagnostics that name
*which* input went dark, so the alert can say something more useful than "failed".

Nothing here decides whether a signal is valid -- it observes an attempt that already
reached a verdict.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db.models import Count, Q

from apps.etf_rotation.models import (
    ETFDaily,
    ETFFeatureSnapshot,
    ETFRotationSignalRun,
)

logger = logging.getLogger(__name__)

#: Feature columns the RULE/ML scorers must have on the newest cross-section. A column that
#: is null for the whole pool is a feed gap, not a thin market, so its null count is the
#: first number a refused run should show.
FEATURE_COLUMNS = (
    'mom_20d', 'mom_60d', 'mom_120d', 'mom_250d',
    'vol_20d', 'rs_vs_benchmark_20d', 'rs_vs_benchmark_60d',
)


def current_task_id() -> str:
    """The Celery task id when this runs inside a worker, else an empty string."""
    try:
        from celery import current_task
    except Exception:  # pragma: no cover - celery is a hard dependency in production
        return ''
    request = getattr(current_task, 'request', None)
    return str(getattr(request, 'id', '') or '')[:64]


def latest_snapshot_date():
    return (
        ETFFeatureSnapshot.objects.filter(is_eligible=True)
        .order_by('-date').values_list('date', flat=True).first()
    )


def latest_bar_date():
    return ETFDaily.objects.order_by('-date').values_list('date', flat=True).first()


def _session_count_after(start, end) -> int:
    if start is None or end is None or start >= end:
        return 0
    return (
        ETFDaily.objects.filter(date__gt=start, date__lte=end)
        .values('date').distinct().count()
    )


def _iso(value):
    """JSONField has no date encoder, so every date in diagnostics is stored as ISO text."""
    return value.isoformat() if value is not None else None


def build_diagnostics(day=None) -> dict:
    """Null counts for the newest (or named) eligible cross-section plus feed dates."""
    from apps.markets.models import BenchmarkIndexDaily

    snapshot_day = day or latest_snapshot_date()
    newest_snapshot = latest_snapshot_date()
    newest_bar = latest_bar_date()
    benchmark_code = str(getattr(settings, 'ETF_BENCHMARK_CODE', '') or '')
    latest_benchmark = None
    if benchmark_code:
        latest_benchmark = (
            BenchmarkIndexDaily.objects.filter(index_code=benchmark_code)
            .order_by('-trade_date').values_list('trade_date', flat=True).first()
        )

    diagnostics = {
        'snapshot_date': _iso(snapshot_day),
        'latest_snapshot_date': _iso(newest_snapshot),
        'latest_bar_date': _iso(newest_bar),
        'benchmark_code': benchmark_code,
        'latest_benchmark_date': _iso(latest_benchmark),
        # A feed with *no* rows has no lag either, so its absence needs its own flag;
        # otherwise "never synced" reads as "current" in every downstream comparison.
        'benchmark_missing': bool(benchmark_code) and latest_benchmark is None,
        'feature_missing': newest_snapshot is None,
        'benchmark_lag_sessions': _session_count_after(latest_benchmark, newest_bar),
        'feature_lag_sessions': _session_count_after(newest_snapshot, newest_bar),
        'eligible_rows': 0,
        'null_counts': {},
    }
    if snapshot_day is None:
        return diagnostics

    aggregates = ETFFeatureSnapshot.objects.filter(
        date=snapshot_day, is_eligible=True,
    ).aggregate(
        eligible_rows=Count('id'),
        **{
            f'null_{column}': Count('id', filter=Q(**{f'{column}__isnull': True}))
            for column in FEATURE_COLUMNS
        },
    )
    diagnostics['eligible_rows'] = int(aggregates.pop('eligible_rows') or 0)
    diagnostics['null_counts'] = {
        column: int(aggregates.get(f'null_{column}') or 0)
        for column in FEATURE_COLUMNS
    }
    return diagnostics


def record_attempt(*, day, signal_type, outcome, reason_code='', message='',
                   diagnostics=None, duration_ms=None):
    """Write one attempt row. Callers must not let a ledger failure mask the real error."""
    return ETFRotationSignalRun.objects.create(
        date=day,
        signal_type=signal_type,
        outcome=outcome,
        reason_code=str(reason_code or '')[:40],
        message=str(message or ''),
        diagnostics=diagnostics or {},
        task_id=current_task_id(),
        duration_ms=None if duration_ms is None else max(int(duration_ms), 0),
    )


def feed_health():
    """Check the benchmark and feature feed against the newest ETF bar.

    A benchmark that lags the bar by even one session is the 2026-10-22 failure mode: every
    eligible fund loses ``rs_vs_benchmark_*`` and the scorer excludes the whole pool. A
    feature run that lags the bar leaves the ranker planning on an old cross-section. Both
    are feed defects, so both write the same ``FEED_STALE`` outcome with a reason code that
    names which one.
    """
    newest_bar = latest_bar_date()
    if newest_bar is None:
        return {'outcome': 'NO_BARS', 'created': False}

    diagnostics = build_diagnostics()
    benchmark_lag = int(diagnostics['benchmark_lag_sessions'])
    feature_lag = int(diagnostics['feature_lag_sessions'])
    if (not diagnostics['benchmark_missing'] and not diagnostics['feature_missing']
            and benchmark_lag == 0 and feature_lag == 0):
        return {'outcome': 'OK', 'created': False, **diagnostics}

    if diagnostics['benchmark_missing']:
        reason_code = 'benchmark_missing'
        message = (
            f'The benchmark {diagnostics["benchmark_code"]} has no rows at all; the '
            f'relative-strength features cannot be computed for any session.'
        )
    elif diagnostics['feature_missing']:
        reason_code = 'feature_missing'
        message = (
            'No eligible ETFFeatureSnapshot exists, so the signal job has no cross-section '
            'to rank; run compute_etf_features for the window.'
        )
    elif benchmark_lag:
        reason_code = 'benchmark_lag'
        message = (
            f'The benchmark {diagnostics["benchmark_code"]} is {benchmark_lag} session(s) '
            f'behind the newest ETF bar ({diagnostics["latest_benchmark_date"]} vs '
            f'{newest_bar.isoformat()}); the relative-strength features cannot be computed.'
        )
    else:
        reason_code = 'feature_lag'
        message = (
            f'The newest eligible feature snapshot is {feature_lag} session(s) behind the '
            f'newest ETF bar ({diagnostics["latest_snapshot_date"]} vs '
            f'{newest_bar.isoformat()}); the signal would rank an old cross-section.'
        )

    existing = ETFRotationSignalRun.objects.filter(
        date=newest_bar,
        signal_type=ETFRotationSignalRun.SignalType.HEALTH,
        outcome=ETFRotationSignalRun.Outcome.FEED_STALE,
        reason_code=reason_code,
    ).order_by('-created_at').first()
    if existing is not None:
        return {'outcome': 'FEED_STALE', 'created': False, 'run_id': existing.pk, **diagnostics}

    run = record_attempt(
        day=newest_bar,
        signal_type=ETFRotationSignalRun.SignalType.HEALTH,
        outcome=ETFRotationSignalRun.Outcome.FEED_STALE,
        reason_code=reason_code,
        message=message,
        diagnostics=diagnostics,
    )
    logger.warning('ETF feed health: %s', message)
    return {'outcome': 'FEED_STALE', 'created': True, 'run_id': run.pk, **diagnostics}


def latest_feed_stale(newest_bar=None):
    """The newest FEED_STALE row, but only while it still describes the newest bar.

    Once bars advance without a new stale row the feed has recovered, so the alert clears
    itself instead of repeating yesterday's defect forever.
    """
    newest_bar = newest_bar or latest_bar_date()
    if newest_bar is None:
        return None
    return (
        ETFRotationSignalRun.objects.filter(
            signal_type=ETFRotationSignalRun.SignalType.HEALTH,
            outcome=ETFRotationSignalRun.Outcome.FEED_STALE,
            date=newest_bar,
        ).order_by('-created_at').first()
    )
