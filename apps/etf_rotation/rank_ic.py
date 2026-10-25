"""Rank IC for one held-out block: the signal-quality half of the ML rematch.

IC answers a narrower question than the portfolio metrics: did the scorer rank the
cross-section in the same order as realised forward returns? It is **not alpha** -- a
positive IC with a bad portfolio construction still loses money, and a high-IC model can
sit in a book whose drawdown is worse than the rule blend's. The two are reported side by
side and never ranked against each other.

The arithmetic matches ``rotation_engine._rank_ic`` on purpose: per-date Spearman
correlation, at least three names and three distinct scores before a date counts. Nothing
here writes a row, activates a model, or reads a bar after ``block_end``.
"""

from __future__ import annotations

import logging
from statistics import mean

import pandas as pd

from apps.etf_rotation.features import FEATURE_COLUMNS

logger = logging.getLogger(__name__)

__all__ = ['block_rank_ic']

#: Below this many names (and distinct scores) one date's rank correlation is a coin flip.
MIN_NAMES_FOR_IC = 3


def block_rank_ic(predictor, *, block_start, block_end,
                  min_names: int = MIN_NAMES_FOR_IC) -> dict:
    """Mean per-date Spearman IC of ``predictor`` inside ``[block_start, block_end]``.

    ``predictor`` is the same ``callable(frame) -> DataFrame[ts_code, score]`` the rotation
    engine consumes, so this measures the artifact that actually scored the backtest rather
    than a re-derivation. Labels come from ``ETFFeatureSnapshot.fwd_return``: dates whose
    horizon is not realised yet (the block's tail) simply drop out of the sample, and the
    returned ``dates`` count says how many sessions were scorable.
    """
    from apps.etf_rotation.models import ETFFeatureSnapshot

    if predictor is None:
        raise ValueError('block_rank_ic needs a predictor; got None.')
    if block_end < block_start:
        raise ValueError(f'block_end {block_end} is before block_start {block_start}.')

    columns = ('etf__ts_code', 'date', 'fwd_return', *FEATURE_COLUMNS)
    rows = list(
        ETFFeatureSnapshot.objects.filter(
            is_eligible=True, date__gte=block_start, date__lte=block_end,
        ).values(*columns)
    )
    if not rows:
        return {'dates': 0, 'mean_ic': None, 'per_date_ic': {}}
    frame = pd.DataFrame(rows).rename(columns={'etf__ts_code': 'ts_code'})
    frame['fwd_return'] = pd.to_numeric(frame['fwd_return'], errors='coerce')
    labels = frame[['date', 'ts_code', 'fwd_return']].dropna(subset=['fwd_return'])
    if labels.empty:
        return {'dates': 0, 'mean_ic': None, 'per_date_ic': {}}

    # One call over the whole block: the LightGBM scorer is row-wise, and the LSTM scorer
    # honours each row's own date (it loads the trailing window for the block's range
    # once), so a per-date loop would multiply the expensive part without changing the
    # answer.
    scored = predictor(frame.drop(columns=['fwd_return']))
    if scored is None or len(scored) == 0:
        return {'dates': 0, 'mean_ic': None, 'per_date_ic': {}}
    merged = labels.merge(
        scored[['ts_code', 'score']], on='ts_code', how='inner',
    ).dropna(subset=['score'])

    per_date: dict[str, float] = {}
    for day, group in merged.groupby('date', sort=True):
        if len(group) < min_names or group['score'].nunique() < min_names:
            continue
        matrix = group[['score', 'fwd_return']].corr(method='spearman')
        value = matrix.at['score', 'fwd_return']
        if pd.notna(value):
            per_date[day.isoformat() if hasattr(day, 'isoformat') else str(day)] = float(value)

    values = list(per_date.values())
    return {
        'dates': len(values),
        'mean_ic': mean(values) if values else None,
        'per_date_ic': per_date,
    }
