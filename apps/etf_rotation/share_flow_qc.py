"""Share-flow quality control: separate real creation/redemption from mechanical jumps.

A split or a large dividend changes the share count without any money entering or leaving the
fund. The flow features treat a share change as flow, so a mechanical jump would read as
sentiment. This module identifies those jumps from the adjustment factor already stored on
``ETFDaily`` and, when the switch is on, removes them from the history the features read.
The switch is off by default: with it off the feature values are bit-identical to before.
"""
from __future__ import annotations

SPLIT_TOLERANCE = 0.01
UNEXPLAINED_GROWTH = 0.50


def classify_share_jump(growth: float | None, adjustment_ratio: float | None) -> str:
    """``split`` / ``flow`` / ``unexplained`` for one session's share change.

    ``adjustment_ratio`` is today's adjustment factor over yesterday's; a value away from 1
    is the provider's own record that the price series was re-based, which is what a split or
    a large distribution does.
    """
    if growth is None:
        return 'flow'
    if adjustment_ratio is not None and abs(adjustment_ratio - 1.0) > SPLIT_TOLERANCE:
        return 'split'
    if abs(growth) > UNEXPLAINED_GROWTH:
        return 'unexplained'
    return 'flow'


def classify_history(history, adj_factors) -> list[str]:
    """One classification per history row, ordered like ``history``.

    ``history`` is an iterable of ``(etf_id, date, shares)`` oldest-first per fund;
    ``adj_factors`` maps ``(etf_id, date)`` to the stored adjustment factor.
    """
    rows = list(history)
    labels = []
    previous = {}
    for etf_id, day, shares in rows:
        prior = previous.get(etf_id)
        growth = None
        ratio = None
        if prior is not None and prior[1] not in (None, 0) and shares is not None:
            growth = float(shares) / float(prior[1]) - 1.0
            today_adj = adj_factors.get((etf_id, day))
            prior_adj = adj_factors.get((etf_id, prior[0]))
            if today_adj and prior_adj:
                ratio = float(today_adj) / float(prior_adj)
        labels.append(classify_share_jump(growth, ratio))
        previous[etf_id] = (day, shares)
    return labels


def drop_mechanical_jumps(history, etf_ids):
    """Drop rows whose share change coincides with an adjustment-factor re-basing.

    Dropping the row (rather than zeroing the growth) keeps the feature honest: the window
    after it is measured across the gap, and the split date itself reads as "no observation"
    instead of "a huge creation".
    """
    from apps.etf_rotation.models import ETFDaily

    if history.empty:
        return history
    dates = sorted(set(history['date'].tolist()))
    adj = {
        (row['etf_id'], row['date']): row['adj_factor']
        for row in ETFDaily.objects.filter(
            etf_id__in=etf_ids, date__gte=dates[0], date__lte=dates[-1],
        ).values('etf_id', 'date', 'adj_factor')
        if row['adj_factor'] is not None
    }
    ordered = history.sort_values(['etf_id', 'date'])
    labels = classify_history(
        zip(ordered['etf_id'], ordered['date'], ordered['shares']), adj,
    )
    mechanical = ordered.index[[label == 'split' for label in labels]]
    return history.drop(index=mechanical)


def quality_summary(rows) -> dict:
    """Coverage and jump histogram for a list of ``(etf_id, date, shares)`` rows."""
    total = len(rows)
    funds = {row[0] for row in rows}
    dates = sorted({row[1] for row in rows})
    labels = classify_history(rows, {})
    histogram: dict[str, int] = {}
    for label in labels:
        histogram[label] = histogram.get(label, 0) + 1
    return {
        'rows': total, 'funds': len(funds),
        'first_date': dates[0].isoformat() if dates else None,
        'last_date': dates[-1].isoformat() if dates else None,
        'histogram': histogram,
    }
