"""Daily bookkeeping for the live rotation book: mark open legs, close displaced ones.

The signal row records what the strategy *says* to hold; ``ETFRotationPosition`` records
what the simulated book paid for. Without a daily writer those rows only ever said OPEN,
so the live track record could not be computed at all. This module supplies the missing
half: every open leg is marked to the newest close at or before the mark date, and a leg
a newer signal of the same type no longer selects is closed at that close with its
realised pnl.

Semantics follow the backtest's closed trade (``backtest_engine._sell``): the entry price
is the raw close the signal was priced at, the exit price is the raw close on the exit
session, and ``pnl`` is net of both commissions -- the buy fee is part of the cost basis,
the sell fee is subtracted from proceeds. ETFs pay no stamp duty. A leg with no recorded
shares cannot state a yuan pnl; it is still closed, with ``pnl=None`` and a price return,
rather than inventing a position size.

No bar or signal after the mark date is ever read: the trailing close and the
displacing signal are both resolved with ``date__lte=mark_date``, which is what
keeps a replay of an old session honest.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Sum

from apps.etf_rotation.models import ETFDaily, ETFRotationPosition, ETFRotationSignal

__all__ = ['mark_positions']


def _as_date(value) -> date | None:
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _latest_bar_date() -> date | None:
    return ETFDaily.objects.order_by('-date').values_list('date', flat=True).first()


def _latest_closes(etf_ids: set[int], mark_date: date) -> dict[int, tuple[date, Any]]:
    """Newest raw close per ETF at or before ``mark_date``. Never reads a later bar."""
    if not etf_ids:
        return {}
    rows = (
        ETFDaily.objects.filter(etf_id__in=sorted(etf_ids), date__lte=mark_date)
        .order_by('etf_id', '-date').distinct('etf_id')
        .values_list('etf_id', 'date', 'close')
    )
    return {etf_id: (bar_date, close) for etf_id, bar_date, close in rows}


def _commission(notional: float) -> float:
    """One fill's commission under the live fee model (same shape as ``etf_commission``)."""
    rate = float(getattr(settings, 'ETF_COMMISSION_RATE', 0.0003))
    minimum = float(getattr(settings, 'ETF_MIN_COMMISSION', 5))
    return max(notional * rate, minimum)


def _signal_type_of(position: ETFRotationPosition) -> str:
    metadata = position.metadata if isinstance(position.metadata, dict) else {}
    value = metadata.get('signal_type')
    if value:
        return str(value)
    return str(position.signal.signal_type) if position.signal_id else ''


def _selected_codes(signal: ETFRotationSignal) -> set[str]:
    return {
        str(item.get('ts_code')) for item in (signal.selected_etfs or [])
        if isinstance(item, dict) and item.get('ts_code')
    }


def _close_position(
    position: ETFRotationPosition, close: Any, price_date: date,
    displacing_signal: ETFRotationSignal,
) -> dict[str, Any]:
    entry_price = float(position.entry_price)
    exit_price = float(close)
    shares = int(position.shares) if position.shares else None
    exit_fee = None
    entry_fee = None
    pnl = None
    if shares:
        entry_notional = entry_price * shares
        entry_fee = _commission(entry_notional)
        exit_fee = _commission(exit_price * shares)
        cost_basis = entry_notional + entry_fee
        pnl = (exit_price - entry_price) * shares - entry_fee - exit_fee
        return_pct = (pnl / cost_basis) if cost_basis > 0 else None
    else:
        return_pct = (exit_price / entry_price - 1.0) if entry_price > 0 else None

    metadata = dict(position.metadata or {})
    metadata['exit'] = {
        'displacing_signal': displacing_signal.pk,
        'displacing_signal_date': displacing_signal.date.isoformat(),
        'entry_commission': round(entry_fee, 4) if entry_fee is not None else None,
        'exit_commission': round(exit_fee, 4) if exit_fee is not None else None,
        'pnl_basis': 'shares' if shares else 'price_only_no_shares',
    }
    position.exit_date = price_date
    position.exit_price = close
    position.pnl = pnl
    position.return_pct = return_pct
    position.status = ETFRotationPosition.Status.CLOSED
    position.exit_reason = 'DISPLACED_BY_SIGNAL'
    position.metadata = metadata
    position.save(update_fields=[
        'exit_date', 'exit_price', 'pnl', 'return_pct', 'status', 'exit_reason', 'metadata',
    ])
    return {
        'position_id': position.pk,
        'ts_code': position.etf.ts_code,
        'name': position.etf.name,
        'entry_date': position.entry_date.isoformat(),
        'entry_price': entry_price,
        'exit_date': price_date.isoformat(),
        'exit_price': exit_price,
        'shares': shares,
        'pnl': pnl,
        'return_pct': return_pct,
        'fees': (entry_fee + exit_fee) if entry_fee is not None else None,
        'exit_reason': position.exit_reason,
        'displacing_signal_date': displacing_signal.date.isoformat(),
    }


def _mark_position(position: ETFRotationPosition, close: Any, price_date: date) -> None:
    """Stamp the newest close and, when shares are known, its unrealised pnl on the leg."""
    mark: dict[str, Any] = {
        'date': price_date.isoformat(),
        'price': round(float(close), 4),
    }
    if position.shares:
        shares = int(position.shares)
        entry_price = float(position.entry_price)
        entry_notional = entry_price * shares
        entry_fee = _commission(entry_notional)
        cost_basis = entry_notional + entry_fee
        unrealized = (float(close) - entry_price) * shares - entry_fee
        mark['market_value'] = round(float(close) * shares, 4)
        mark['unrealized_pnl'] = round(unrealized, 4)
        mark['unrealized_return_pct'] = round(unrealized / cost_basis, 6) if cost_basis > 0 else None
    metadata = dict(position.metadata or {})
    metadata['live_mark'] = mark
    position.metadata = metadata
    position.save(update_fields=['metadata'])


@transaction.atomic
def mark_positions(trade_date=None) -> dict[str, Any]:
    """Mark every OPEN leg and close the ones a newer signal has displaced.

    The newest signal is resolved per ``signal_type``: the live book is one strategy per
    type, and comparing a MONTHLY leg against the WEEKLY book would close holdings that
    signal never spoke about. A leg is displaced when the newest signal of its type is
    strictly newer than the signal that opened it and no longer selects the fund. The
    latest signal's *selected* codes are the comparison, not its priced positions: a fund
    the signal chose but could not price should not silently keep an old leg open.
    """
    mark_date = _as_date(trade_date) or _latest_bar_date()
    if mark_date is None:
        return {'date': None, 'closed': [], 'marked_count': 0, 'skipped_no_price': [], 'latest_signals': {}}

    open_positions = list(
        ETFRotationPosition.objects.select_related('etf', 'signal')
        .filter(status=ETFRotationPosition.Status.OPEN)
    )
    closes = _latest_closes({position.etf_id for position in open_positions}, mark_date)
    types = {kind for kind in (_signal_type_of(position) for position in open_positions) if kind}
    latest_signals = {
        # A backdated mark must not learn from a signal published after it. The beat runs
        # on the newest session, where this bound is a no-op, but ``--date`` replays a past
        # session and there the future signal is exactly the leak the price bound avoids.
        kind: ETFRotationSignal.objects.filter(signal_type=kind, date__lte=mark_date)
        .order_by('-date', '-created_at').first()
        for kind in sorted(types)
    }

    closed: list[dict[str, Any]] = []
    marked = 0
    skipped: list[str] = []
    for position in open_positions:
        bar = closes.get(position.etf_id)
        if bar is None:
            # No bar at or before the mark date: a mark would be a fabricated price.
            skipped.append(position.etf.ts_code)
            continue
        price_date, close = bar
        signal_type = _signal_type_of(position)
        latest = latest_signals.get(signal_type)
        entry_signal_date = position.signal.date if position.signal_id else None
        displaced = (
            latest is not None
            and entry_signal_date is not None
            and latest.date > entry_signal_date
            and position.etf.ts_code not in _selected_codes(latest)
        )
        if displaced:
            closed.append(_close_position(position, close, price_date, latest))
        else:
            _mark_position(position, close, price_date)
            marked += 1

    realised = (
        ETFRotationPosition.objects.filter(status=ETFRotationPosition.Status.CLOSED)
        .aggregate(total=Sum('pnl'))['total']
    )
    return {
        'date': mark_date.isoformat(),
        'closed_count': len(closed),
        'marked_count': marked,
        'closed': closed,
        'skipped_no_price': skipped,
        'latest_signals': {
            kind: (signal.date.isoformat() if signal is not None else None)
            for kind, signal in latest_signals.items()
        },
        'realised_pnl_total': float(realised) if realised is not None else 0.0,
    }
