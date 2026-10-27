"""Macro regime inputs and per-regime attribution.

Macro series are a gate, never a ranking feature: a date-level value cannot tell two funds
apart. This module builds the five declared inputs from the published snapshot with their own
release lags, labels each session with a *data-available* regime, and slices a measurement's
fold facets by that label. Yield-based phases are reported as unavailable while the bond-curve
source is denied.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta

from apps.macro.models import MacroSnapshot, release_lag_days

#: The five declared inputs plus the yield slope. None of these may ever enter
#: ``features.FEATURE_COLUMNS`` -- a date constant cannot separate two funds.
REGIME_INPUT_NAMES = (
    'pmi_level', 'pmi_delta_3m', 'cpi_ppi_scissors', 'dxy_trend_3m', 'cny_trend_3m',
    'yield_slope',
)

_SERIES_FIELDS = (
    'pmi_manufacturing', 'cpi_yoy', 'ppi_yoy', 'dxy', 'cny_usd', 'cn10y_yield', 'cn3y_yield',
)


def load_snapshots() -> list[dict]:
    """Every snapshot ordered oldest first; one query instead of five per session."""
    return list(MacroSnapshot.objects.order_by('date').values('date', *_SERIES_FIELDS))


def _published(rows, dates, field, session):
    cutoff = session - timedelta(days=release_lag_days(field))
    index = bisect_right(dates, cutoff) - 1
    return rows[index] if index >= 0 else None


def _recent(rows, dates, field, session, count=4):
    cutoff = session - timedelta(days=release_lag_days(field))
    index = bisect_right(dates, cutoff) - 1
    values = []
    while index >= 0 and len(values) < count:
        value = rows[index].get(field)
        if value is not None:
            values.append(float(value))
        index -= 1
    return values


def _trend(values):
    if len(values) < 4 or values[3] == 0:
        return None
    return values[0] / values[3] - 1.0


def macro_regime_inputs(session: date, *, rows=None) -> dict:
    """The five declared inputs, each read as of its own publication lag.

    ``pmi_level`` and ``pmi_delta_3m`` come from the monthly snapshot; ``cpi_ppi_scissors`` is
    CPI minus PPI; the FX trends are 3-month changes. ``yield_slope`` is ``None`` while the
    bond-curve source is denied -- the status field says why instead of pretending to a value.
    """
    rows = load_snapshots() if rows is None else rows
    dates = [row['date'] for row in rows]

    pmi_now = _published(rows, dates, 'pmi_manufacturing', session)
    pmi_values = _recent(rows, dates, 'pmi_manufacturing', session, count=4)
    pmi_delta = (pmi_values[0] - pmi_values[3]) if len(pmi_values) >= 4 else None
    cpi = _published(rows, dates, 'cpi_yoy', session)
    ppi = _published(rows, dates, 'ppi_yoy', session)
    scissors = (
        float(cpi['cpi_yoy']) - float(ppi['ppi_yoy'])
        if cpi and ppi and cpi['cpi_yoy'] is not None and ppi['ppi_yoy'] is not None else None
    )
    ten_year = _published(rows, dates, 'cn10y_yield', session)
    three_year = _published(rows, dates, 'cn3y_yield', session)
    if (
        ten_year and three_year
        and ten_year['cn10y_yield'] is not None and three_year['cn3y_yield'] is not None
    ):
        yield_slope = float(ten_year['cn10y_yield']) - float(three_year['cn3y_yield'])
        yield_status = 'available'
    else:
        yield_slope = None
        yield_status = 'unavailable: yc_cb denied'
    return {
        'pmi_level': (
            float(pmi_now['pmi_manufacturing'])
            if pmi_now and pmi_now['pmi_manufacturing'] is not None else None
        ),
        'pmi_delta_3m': pmi_delta,
        'cpi_ppi_scissors': scissors,
        'dxy_trend_3m': _trend(_recent(rows, dates, 'dxy', session)),
        'cny_trend_3m': _trend(_recent(rows, dates, 'cny_usd', session)),
        'yield_slope': yield_slope,
        'yield_slope_status': yield_status,
    }


def regime_label(inputs: dict) -> str:
    """Three states from the data that is actually available.

    ``risk_on`` when PMI is at or above 50 and not falling; ``risk_off`` when PMI is below 50
    and falling; ``neutral`` otherwise. Yield-based recession/stagflation labels are not
    produced while the slope is unavailable -- they would be fabricated.
    """
    pmi = inputs.get('pmi_level')
    delta = inputs.get('pmi_delta_3m')
    if pmi is None:
        return 'unknown'
    if pmi < 50 and delta is not None and delta < 0:
        return 'risk_off'
    if pmi >= 50 and (delta is None or delta >= 0):
        return 'risk_on'
    return 'neutral'


def session_labels(start, end, *, rows=None) -> dict:
    """Regime label per trading session in ``[start, end]`` (ISO date strings)."""
    rows = load_snapshots() if rows is None else rows
    return {
        day: regime_label(macro_regime_inputs(date.fromisoformat(day), rows=rows))
        for day in _session_range(start, end)
    }


def attribution(facets, session_labels) -> list[dict]:
    """Slice sub-window facets by regime, refusing to judge a regime with < 20 rebalances.

    ``facets`` are fold rows with start/end/rebalances; a facet is attributed to the regime
    that covers most of its sessions, and a regime whose attributed facets hold fewer than 20
    rebalances is reported as ``decidable: False`` rather than ranked.
    """
    grouped: dict = {}
    for facet in facets or []:
        labels = [
            session_labels.get(day) for day in _session_range(facet['start'], facet['end'])
        ]
        labels = [label for label in labels if label and label != 'unknown']
        if not labels:
            continue
        label = max(set(labels), key=labels.count)
        cell = grouped.setdefault(label, {'rebalances': 0, 'facets': 0, 'returns': []})
        cell['facets'] += 1
        cell['rebalances'] += int(facet.get('rebalances') or 0)
        cell['returns'].append(float(facet.get('total_return') or 0.0))
    rows = []
    for label, cell in sorted(grouped.items()):
        rows.append({
            'regime': label, 'facets': cell['facets'], 'rebalances': cell['rebalances'],
            'mean_return': sum(cell['returns']) / len(cell['returns']) if cell['returns'] else None,
            'decidable': cell['rebalances'] >= 20,
        })
    return rows


def regime_table(rows, *, snapshots=None) -> list[dict]:
    """Attribute every ledger row that stores fold blocks, per arm, to its regimes.

    Reads measurements that already exist; nothing is re-run. Rows whose context has no fold
    plan (single continuous runs) are skipped because their blocks are folds, not sub-windows.
    """
    snapshots = load_snapshots() if snapshots is None else snapshots
    table = []
    for record in rows:
        context = record.get('context') or {}
        blocks = (record.get('results') or {}).get('blocks') or []
        if not blocks:
            continue
        arm = f"{context.get('model_type', '?')}{'+hybrid' if context.get('hybrid') else ''}"
        panel = f"{context.get('panel_start')}..{context.get('panel_end')}"
        labels: dict = {}
        for block in blocks:
            for day, label in session_labels(block['start'], block['end'], rows=snapshots).items():
                labels[day] = label
        table.append({
            'id': record.get('id', ''), 'arm': arm, 'panel': panel,
            'fold_plan': context.get('fold_plan') or '?',
            'label': context.get('label') or '-',
            'regimes': attribution(blocks, labels),
        })
    return table


def _session_range(start: str, end: str):
    from apps.etf_rotation.models import ETFDaily

    for (day,) in ETFDaily.objects.filter(
        date__gte=start, date__lte=end,
    ).values_list('date').distinct().order_by('date'):
        yield day.isoformat()
