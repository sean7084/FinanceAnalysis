"""Rank IC for one held-out block: the signal-quality half of the ML rematch.

IC answers a narrower question than the portfolio metrics: did the scorer rank the
cross-section in the same order as realised forward returns? It is **not alpha** -- a
positive IC with a bad portfolio construction still loses money, and a high-IC model can
sit in a book whose drawdown is worse than the rule blend's. The two are reported side by
side and never ranked against each other.

The arithmetic matches ``rotation_engine._rank_ic`` on purpose: per-date Spearman
correlation, at least three names and three distinct scores before a date counts. Nothing
here writes a row or activates a model, and nothing a *scorer* is asked to price comes from
after ``block_end``. The one function that reads later bars is
:func:`forward_return_panel`: a forward return is only realised later, and it is used as a
label, never as an input to the predictor.

Two label sources exist. The default is the stored ``fwd_return`` column, built once at
``ETF_LABEL_HORIZON_DAYS``. A horizon sweep (``horizon_days=``) recomputes the same
expression from ``ETFDaily`` instead, because one column cannot hold several horizons.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from statistics import mean

import pandas as pd

from apps.etf_rotation.features import FEATURE_COLUMNS, sessions_to_calendar_days

logger = logging.getLogger(__name__)

__all__ = ['block_rank_ic', 'forward_return_panel']

#: Below this many names (and distinct scores) one date's rank correlation is a coin flip.
MIN_NAMES_FOR_IC = 3


def forward_return_panel(horizon_days: int, *, block_start, block_end) -> pd.DataFrame:
    """``(etf_id, date, fwd_price_return)`` at an arbitrary label horizon.

    The stored ``fwd_return`` column has exactly one horizon, so a horizon sweep cannot reuse
    it. This recomputes the same expression :func:`features.build_forward_return` uses --
    ``adj_close`` ``h`` rows ahead over today's value -- straight from ``ETFDaily``. Bars
    after ``block_end`` are read on purpose: the return is not realised until later, and the
    caller uses it as a label, never as a predictor input.
    """
    from apps.etf_rotation.models import ETFDaily

    horizon = int(horizon_days)
    if horizon < 1:
        raise ValueError(f'horizon_days must be at least 1; got {horizon_days}.')
    tail = block_end + timedelta(days=sessions_to_calendar_days(horizon + 5))
    rows = list(
        ETFDaily.objects.filter(date__gte=block_start, date__lte=tail)
        .values('etf_id', 'date', 'adj_close')
    )
    if not rows:
        return pd.DataFrame(columns=['etf_id', 'date', 'fwd_price_return'])
    frame = pd.DataFrame(rows)
    frame['adj_close'] = pd.to_numeric(frame['adj_close'], errors='coerce')
    prices = frame.pivot_table(
        index='date', columns='etf_id', values='adj_close', aggfunc='last',
    ).sort_index()
    # Same arithmetic as build_forward_return, on the ETF bars rather than a snapshot panel.
    forward = prices.shift(-horizon) / prices - 1.0
    panel = forward.stack().rename('fwd_price_return').reset_index()
    return panel[panel['date'] >= block_start]


def block_rank_ic(predictor, *, block_start, block_end, horizon_days=None,
                  min_names: int = MIN_NAMES_FOR_IC) -> dict:
    """Mean per-date Spearman IC of ``predictor`` inside ``[block_start, block_end]``.

    ``predictor`` is the same ``callable(frame) -> DataFrame[ts_code, score]`` the rotation
    engine consumes, so this measures the artifact that actually scored the backtest rather
    than a re-derivation. Labels come from ``ETFFeatureSnapshot.fwd_return``: dates whose
    horizon is not realised yet (the block's tail) simply drop out of the sample, and the
    returned ``dates`` count says how many sessions were scorable. Pass ``horizon_days`` to
    recompute the label at that horizon from bars instead (see :func:`forward_return_panel`);
    the default ``None`` reads the stored column and leaves every earlier reading intact.
    """
    from apps.etf_rotation.models import ETFFeatureSnapshot

    if predictor is None:
        raise ValueError('block_rank_ic needs a predictor; got None.')
    if block_end < block_start:
        raise ValueError(f'block_end {block_end} is before block_start {block_start}.')
    horizon = None if horizon_days is None else int(horizon_days)

    columns = ('etf_id', 'etf__ts_code', 'date', 'fwd_return', *FEATURE_COLUMNS)
    rows = list(
        ETFFeatureSnapshot.objects.filter(
            is_eligible=True, date__gte=block_start, date__lte=block_end,
        ).values(*columns)
    )
    if not rows:
        return {'dates': 0, 'mean_ic': None, 'per_date_ic': {}, 'horizon_days': horizon}
    frame = pd.DataFrame(rows).rename(columns={'etf__ts_code': 'ts_code'})
    if horizon is None:
        frame['fwd_return'] = pd.to_numeric(frame['fwd_return'], errors='coerce')
        labels = frame[['date', 'ts_code', 'fwd_return']].dropna(subset=['fwd_return'])
        label_column = 'fwd_return'
    else:
        priced = forward_return_panel(horizon, block_start=block_start, block_end=block_end)
        labels = (
            frame[['date', 'etf_id', 'ts_code']]
            .merge(priced, on=['etf_id', 'date'], how='inner')
            .dropna(subset=['fwd_price_return'])
        )
        label_column = 'fwd_price_return'
    if labels.empty:
        return {'dates': 0, 'mean_ic': None, 'per_date_ic': {}, 'horizon_days': horizon}

    # One call over the whole block: the LightGBM scorer is row-wise, and the LSTM scorer
    # honours each row's own date (it loads the trailing window for the block's range
    # once), so a per-date loop would multiply the expensive part without changing the
    # answer.
    dropped = ['fwd_return'] if label_column == 'fwd_return' else ['fwd_return', 'etf_id']
    scored = predictor(frame.drop(columns=dropped))
    if scored is None or len(scored) == 0:
        return {'dates': 0, 'mean_ic': None, 'per_date_ic': {}, 'horizon_days': horizon}
    merged = labels.merge(
        scored[['ts_code', 'score']], on='ts_code', how='inner',
    ).dropna(subset=['score'])

    per_date: dict[str, float] = {}
    for day, group in merged.groupby('date', sort=True):
        if len(group) < min_names or group['score'].nunique() < min_names:
            continue
        matrix = group[['score', label_column]].corr(method='spearman')
        value = matrix.at['score', label_column]
        if pd.notna(value):
            per_date[day.isoformat() if hasattr(day, 'isoformat') else str(day)] = float(value)

    values = list(per_date.values())
    return {
        'dates': len(values),
        'mean_ic': mean(values) if values else None,
        'per_date_ic': per_date,
        'horizon_days': horizon,
    }
