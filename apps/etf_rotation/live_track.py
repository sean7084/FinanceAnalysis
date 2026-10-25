"""Read-only forward track record for the live ETF books.

The locked holdout was spent once and is closed (plan §7.7), so the only honest
out-of-sample evidence left is the live ledger accruing forward (plan Phase 11). This
module turns that ledger into a report -- realised pnl, win rate, open marks -- and
compares it against a *declared* reference backtest when one is configured.

Two properties are deliberate and load-bearing:

* **Read-only.** Nothing here writes a signal, a position, a parameter or a backtest row.
  The freeze discipline in ``docs/plans/etf-rotation-next-phases-2026-10-24.md`` keeps the
  ledger unbiased only while nobody reacts to it by re-tuning; a module with no write path
  is the mechanical half of that promise.
* **Declared expectation, not an inferred one.** Drift is measured against an explicit
  reference row (``ETF_LIVE_REFERENCE_BACKTEST_ID``) and an explicit tolerance. With no
  reference configured the report states the live numbers and makes no drift claim, rather
  than inventing an expectation from the same data it is judging.
"""

from __future__ import annotations

from decimal import Decimal
from statistics import mean
from typing import Any

from django.conf import settings

from apps.etf_rotation.models import (
    ETFRotationBacktest,
    ETFRotationPosition,
)
from apps.etf_rotation.position_ledger import signal_type_of

__all__ = ['build_live_track', 'dispatch_drift_alert']

#: Independent readings are required before a drift verdict means anything; a handful of
#: closed trades in one regime is noise, not evidence.
DEFAULT_MIN_CLOSED_TRADES = 10


def _book_stats(positions: list) -> dict[str, Any]:
    """Per-book ledger statistics, computed from rows that are already persisted."""
    closed = [
        position for position in positions
        if position.status == ETFRotationPosition.Status.CLOSED
        and position.return_pct is not None
    ]
    open_legs = [
        position for position in positions
        if position.status == ETFRotationPosition.Status.OPEN
    ]
    returns = [float(position.return_pct) for position in closed]
    realised = [
        float(position.pnl) for position in closed if position.pnl is not None
    ]
    open_marks = [
        (position.metadata or {}).get('live_mark') or {} for position in open_legs
    ]
    market_values = [
        float(mark['market_value']) for mark in open_marks if mark.get('market_value') is not None
    ]
    unrealised = [
        float(mark['unrealized_pnl']) for mark in open_marks if mark.get('unrealized_pnl') is not None
    ]
    return {
        'closed_count': len(closed),
        'open_count': len(open_legs),
        'win_rate': (sum(1 for value in returns if value > 0) / len(returns)) if returns else None,
        'mean_return': mean(returns) if returns else None,
        'realised_pnl': sum(realised) if realised else None,
        'realised_pnl_rows': len(realised),
        'open_market_value': sum(market_values) if market_values else None,
        'open_unrealised_pnl': sum(unrealised) if unrealised else None,
        'open_marks_priced': len(market_values),
    }


def _reference_expectation(reference_id: int | None) -> dict[str, Any] | None:
    """The reference backtest's own closed-trade distribution, or ``None`` when unset.

    ``report['closed_trades']`` is the round-trip record; ``trade_log`` is per fill and
    cannot state a trade return. Rows finished before that block existed fall back to the
    stored ``win_rate`` column and leave the mean return unknown rather than recomputing it
    from fills.
    """
    if not reference_id:
        return None
    run = ETFRotationBacktest.objects.filter(pk=int(reference_id)).first()
    if run is None:
        return None
    closed = list((run.report or {}).get('closed_trades') or [])
    returns = [
        float(item['return_pct']) for item in closed
        if item.get('return_pct') is not None
    ]
    if returns:
        return {
            'backtest_id': run.pk,
            'name': run.name,
            'closed_count': len(returns),
            'win_rate': sum(1 for value in returns if value > 0) / len(returns),
            'mean_return': mean(returns),
        }
    return {
        'backtest_id': run.pk,
        'name': run.name,
        'closed_count': int((run.report or {}).get('counters', {}).get('closed_trades', 0) or 0),
        'win_rate': float(run.win_rate) if run.win_rate is not None else None,
        'mean_return': None,
    }


def _drift(live: dict[str, Any], reference: dict[str, Any] | None, *,
           win_rate_tolerance: float, mean_return_tolerance: float,
           min_closed_trades: int) -> dict[str, Any]:
    """Compare one book with the reference, or return an explicit "not measured" verdict."""
    if reference is None:
        return {'measured': False, 'reason': 'no reference backtest configured'}
    if live['closed_count'] < min_closed_trades:
        return {
            'measured': False,
            'reason': (
                f'{live["closed_count"]} closed trade(s) < the {min_closed_trades} needed '
                'for a drift reading'
            ),
            'min_closed_trades': min_closed_trades,
        }
    win_rate_drift = (
        live['win_rate'] - reference['win_rate']
        if live['win_rate'] is not None and reference.get('win_rate') is not None
        else None
    )
    mean_return_drift = (
        live['mean_return'] - reference['mean_return']
        if live['mean_return'] is not None and reference.get('mean_return') is not None
        else None
    )
    reasons = []
    if win_rate_drift is not None and abs(win_rate_drift) > win_rate_tolerance:
        reasons.append(
            f'win rate {live["win_rate"]:.4f} vs {reference["win_rate"]:.4f} '
            f'(tolerance {win_rate_tolerance})'
        )
    if mean_return_drift is not None and abs(mean_return_drift) > mean_return_tolerance:
        reasons.append(
            f'mean trade return {live["mean_return"]:.4f} vs {reference["mean_return"]:.4f} '
            f'(tolerance {mean_return_tolerance})'
        )
    return {
        'measured': True,
        'win_rate_drift': win_rate_drift,
        'mean_return_drift': mean_return_drift,
        'breached': bool(reasons),
        'reasons': reasons,
        'tolerances': {
            'win_rate': win_rate_tolerance,
            'mean_return': mean_return_tolerance,
        },
        'min_closed_trades': min_closed_trades,
    }


def build_live_track(*, reference_backtest_id: int | None = None,
                     win_rate_tolerance: float | None = None,
                     mean_return_tolerance: float | None = None,
                     min_closed_trades: int | None = None) -> dict[str, Any]:
    """The forward record, one book per ``signal_type``, plus an optional drift verdict.

    ``ETF_LIVE_REFERENCE_BACKTEST_ID`` names the row the live book is judged against. With it
    unset the report is descriptive only -- that is the honest default, because comparing the
    live book with a number derived from the same live book would always agree.
    """
    if reference_backtest_id is None:
        reference_backtest_id = int(getattr(settings, 'ETF_LIVE_REFERENCE_BACKTEST_ID', 0) or 0)
    if win_rate_tolerance is None:
        win_rate_tolerance = float(
            getattr(settings, 'ETF_LIVE_DRIFT_WIN_RATE_TOLERANCE', 0.20),
        )
    if mean_return_tolerance is None:
        mean_return_tolerance = float(
            getattr(settings, 'ETF_LIVE_DRIFT_MEAN_RETURN_TOLERANCE', 0.03),
        )
    if min_closed_trades is None:
        min_closed_trades = int(
            getattr(settings, 'ETF_LIVE_DRIFT_MIN_CLOSED_TRADES', DEFAULT_MIN_CLOSED_TRADES),
        )

    reference = _reference_expectation(reference_backtest_id)
    grouped: dict[str, list] = {}
    for position in ETFRotationPosition.objects.select_related('etf', 'signal').all():
        grouped.setdefault(signal_type_of(position) or 'UNKNOWN', []).append(position)

    books = {}
    for signal_type, positions in sorted(grouped.items()):
        stats = _book_stats(positions)
        stats['drift'] = _drift(
            stats, reference,
            win_rate_tolerance=win_rate_tolerance,
            mean_return_tolerance=mean_return_tolerance,
            min_closed_trades=min_closed_trades,
        )
        books[signal_type] = stats

    return {
        'reference': reference,
        'books': books,
        'production': books.get('MONTHLY', {}),
        'shadow': books.get('SHADOW', {}),
        'any_breach': any(book['drift'].get('breached') for book in books.values()),
    }


def dispatch_drift_alert(summary: dict[str, Any]) -> int | None:
    """Write one ``LIVE_DRIFT`` alert event and hand it to the existing dispatch task.

    Returns the event id, or ``None`` when there is nothing to dispatch. Uses the same
    asset-less alert plumbing (and channel derivation) as the feed-health rule; the price
    loop skips this condition type, so the event is only ever created here.
    """
    if not summary.get('any_breach'):
        return None
    from apps.analytics.models import AlertEvent, AlertRule
    from apps.analytics.tasks import alert_notification_channels, send_alert_notifications

    rule = AlertRule.objects.filter(
        condition_type=AlertRule.ConditionType.LIVE_DRIFT,
    ).first()
    if rule is None:
        rule = AlertRule.objects.create(
            asset=None,
            name='ETF live versus backtest drift',
            condition_type=AlertRule.ConditionType.LIVE_DRIFT,
            threshold=Decimal('0'),
            channels=alert_notification_channels(),
            cooldown_minutes=60,
            is_active=True,
        )
    lines = []
    for signal_type, book in summary['books'].items():
        drift = book['drift']
        if drift.get('breached'):
            lines.append(f'{signal_type}: ' + '; '.join(drift['reasons']))
    message = '; '.join(lines) or 'live book breached its drift tolerance'
    event = AlertEvent.objects.create(
        alert_rule=rule,
        asset=None,
        trigger_value=None,
        message=message,
        metadata={
            'condition_type': AlertRule.ConditionType.LIVE_DRIFT,
            'reference': summary.get('reference'),
            'books': {
                name: {
                    'closed_count': book['closed_count'],
                    'win_rate': book['win_rate'],
                    'realised_pnl': book['realised_pnl'],
                    'drift': book['drift'],
                }
                for name, book in summary['books'].items()
            },
        },
    )
    rule.last_triggered_at = event.created_at
    rule.save(update_fields=['last_triggered_at'])
    send_alert_notifications.delay(event.id)
    return event.pk
