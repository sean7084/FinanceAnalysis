"""Benchmark comparison curves for an ETF rotation backtest.

The payload is field-for-field the shape ``apps.backtest.comparison`` produces, so one
chart component can render either module's runs. ``compare_target`` is always ``None``
here: an ETF row records no comparison run in ``parameters``, and the key stays present
only so a shared consumer can read either payload without branching.

Benchmark levels are recomputed from ``markets.BenchmarkIndexDaily`` on every call
rather than read off ``report``. ``_finalize_metrics`` reduces each benchmark to scalars
and never persists its series, so recomputation is the only way an existing row gets a
curve without rerunning it -- which is also why the rescale target is the strategy's
opening equity, not any stored number. Each benchmark is then forward-filled onto the
strategy's own sessions, so a hole in the ETF bar feed shortens both curves together
instead of leaving the benchmark measured over days the strategy never saw.

The equal-weight all-eligible-ETF proxy is deliberately absent. It is a monthly
rebalance over every fund in the point-in-time pool, so rebuilding it means reading
bars for hundreds of codes across every session of the window; the engine pays that
once per run and puts the result in ``report['benchmark']`` as scalars. A read endpoint
that ran on a chart hover could not, so the proxy stays out of this payload.
"""

from datetime import date as date_cls
from decimal import Decimal

from apps.markets.models import BenchmarkIndexDaily

from .models import ETFRotationBacktest

DECIMAL_0 = Decimal('0')
DECIMAL_1 = Decimal('1')

SELECTED_RUN_KEY = 'selected_run'
PRIMARY_BENCHMARK_KIND = 'primary'
SECONDARY_BENCHMARK_KIND = 'secondary'


def _to_decimal(value):
    return Decimal(str(value))


def _benchmark_label(kind, code):
    """Legend text matching ``_finalize_metrics``, so a chart and a report agree."""
    return (
        f'Primary benchmark {code}'
        if kind == PRIMARY_BENCHMARK_KIND
        else f'Secondary benchmark {code}'
    )


def _series_key(code):
    """Stable series id for an index code, e.g. ``H00905.CSI`` -> ``h00905_csi``."""
    return ''.join(char if char.isalnum() else '_' for char in str(code)).lower()


def _scale_values(values, baseline_value):
    if not values:
        return []
    if baseline_value is None:
        return list(values)
    first_value = values[0]
    if first_value <= 0:
        return values
    scale = baseline_value / first_value
    return [value * scale for value in values]


def _build_drawdown(values):
    peak = None
    drawdowns = []
    for value in values:
        peak = value if peak is None else max(peak, value)
        if peak is None or peak <= 0:
            drawdowns.append(DECIMAL_0)
        else:
            drawdowns.append((value / peak) - DECIMAL_1)
    return drawdowns


def _series_points(dates, values):
    drawdowns = _build_drawdown(values)
    return [
        {
            'date': dt.isoformat(),
            'value': float(value),
            'drawdown': float(drawdown),
        }
        for dt, value, drawdown in zip(dates, values, drawdowns)
    ]


def _build_series(dates, values, baseline_value, *, key, label, kind, **extra):
    """One scaled series, or None if the raw pair is unusable.

    ``values`` are already decimals in oldest-first order; ``baseline_value`` anchors
    point zero so every series on the chart starts at the strategy's opening equity.
    """
    if not dates or not values or len(dates) != len(values):
        return None
    scaled = _scale_values(values, baseline_value)
    if not scaled or scaled[0] <= 0:
        return None
    series = {
        'key': key,
        'label': label,
        'kind': kind,
        'points': _series_points(dates, scaled),
        'total_return': float((scaled[-1] - scaled[0]) / scaled[0]),
        'max_drawdown': float(min(_build_drawdown(scaled))),
    }
    series.update(extra)
    return series


def _strategy_pair(run):
    """(dates, equity) from the stored curve, oldest first, non-numeric points dropped.

    The engine writes ``date`` as an ISO string on each point, so the curve is its own
    calendar and no exchange-calendar lookup is needed to place it.
    """
    curve = run.equity_curve if isinstance(run.equity_curve, list) else []
    dates = []
    values = []
    for point in curve:
        if not isinstance(point, dict):
            continue
        raw_date = point.get('date')
        if isinstance(raw_date, str):
            try:
                parsed = date_cls.fromisoformat(raw_date[:10])
            except ValueError:
                continue
        elif isinstance(raw_date, date_cls):
            parsed = raw_date
        else:
            continue
        try:
            value = _to_decimal(point.get('equity'))
        except Exception:
            continue
        dates.append(parsed)
        values.append(value)
    return dates, values


def _build_strategy_series(run, dates, values, baseline_value):
    return _build_series(
        dates, values, baseline_value,
        key=SELECTED_RUN_KEY, label=f'#{run.id} {run.name}', kind='backtest',
        run_id=run.id, model_type=run.model_type, benchmark_code=run.benchmark_code,
    )


def _benchmark_pair(code, start_date, end_date):
    rows = list(
        BenchmarkIndexDaily.objects.filter(
            index_code=code,
            trade_date__gte=start_date,
            trade_date__lte=end_date,
        ).order_by('trade_date').values_list('trade_date', 'close')
    )
    dates = []
    values = []
    for trade_date, close in rows:
        try:
            value = _to_decimal(close)
        except Exception:
            continue
        if value <= 0:
            continue
        dates.append(trade_date)
        values.append(value)
    return dates, values


def _align_pair_to_sessions(sessions, close_dates, close_values):
    """Forward-fill benchmark closes onto the strategy's own session grid.

    Mirrors ``backtest_engine._benchmark_returns``, which aligns the same way before
    computing the row's alpha and IR: an index row that is merely missing must not become
    a 0% day, and a hole in the ETF bar feed must not give the benchmark observations the
    strategy could never have traded against. Aligning here is what makes the plotted
    curve the same series the stored metrics were computed from, and gives the overlay one
    point per session instead of two different date grids joined on the client.
    """
    if not sessions or not close_dates:
        return [], []
    dates = []
    values = []
    cursor = -1
    for session in sessions:
        while cursor + 1 < len(close_dates) and close_dates[cursor + 1] <= session:
            cursor += 1
        if cursor < 0:
            continue
        dates.append(session)
        values.append(close_values[cursor])
    return dates, values


def _build_benchmark_series(code, kind, start_date, end_date, sessions, baseline_value):
    close_dates, close_values = _benchmark_pair(code, start_date, end_date)
    dates, values = _align_pair_to_sessions(sessions, close_dates, close_values)
    return _build_series(
        dates, values, baseline_value,
        key=_series_key(code), label=_benchmark_label(kind, code), kind='benchmark',
        index_code=code,
    )


def _benchmark_specs(run):
    """(code, kind) pairs from the row: ``benchmark_code`` plus the stored secondaries.

    ``secondary_benchmark_codes`` lives in ``parameters`` because ``BacktestConfig``
    writes the whole dataclass there, and a run must be charted against the benchmarks
    it was actually compared to rather than the settings of whatever host serves this.
    """
    primary = str(run.benchmark_code or '').strip()
    specs = [(primary, PRIMARY_BENCHMARK_KIND)] if primary else []
    for code in (run.parameters or {}).get('secondary_benchmark_codes') or []:
        code = str(code or '').strip()
        if code and code != primary:
            specs.append((code, SECONDARY_BENCHMARK_KIND))
    return specs


def _resolve_extra_runs(run, extra_compare_run_ids):
    if not extra_compare_run_ids:
        return []
    requested = [run_id for run_id in dict.fromkeys(extra_compare_run_ids) if run_id != run.id]
    if not requested:
        return []
    run_map = {
        extra.id: extra
        for extra in ETFRotationBacktest.objects.filter(
            id__in=requested, status=ETFRotationBacktest.Status.COMPLETED
        )
    }
    return [run_map[run_id] for run_id in requested if run_id in run_map]


def build_etf_comparison_payload(run, extra_compare_run_ids=None):
    """Strategy-plus-benchmark curves for one row, rescaled onto its opening equity."""
    payload = {
        'run': {
            'id': run.id,
            'name': run.name,
            'status': run.status,
            'start_date': run.start_date.isoformat(),
            'end_date': run.end_date.isoformat(),
            'initial_capital': float(run.initial_capital),
            'model_type': run.model_type,
            'benchmark_code': run.benchmark_code,
        },
        'series': [],
        # Present on every return path, including the two below that short-circuit: the
        # frontend Dto declares it required, so an absent key is a type error at render.
        'available_series_keys': [],
        'compare_target': None,
        'message': None,
    }

    if run.status != ETFRotationBacktest.Status.COMPLETED:
        payload['message'] = 'Comparison curves are available after the run completes.'
        return payload

    dates, values = _strategy_pair(run)
    if not values:
        payload['message'] = 'No equity curve is stored for this run yet.'
        return payload

    baseline_value = values[0]
    selected_series = _build_strategy_series(run, dates, values, baseline_value)
    if selected_series is None:
        payload['message'] = 'No equity curve is stored for this run yet.'
        return payload
    payload['series'].append(selected_series)

    for extra_run in _resolve_extra_runs(run, extra_compare_run_ids or []):
        extra_dates, extra_values = _strategy_pair(extra_run)
        extra_series = _build_series(
            extra_dates, extra_values, baseline_value,
            key=f'extra_run_{extra_run.id}', label=f'#{extra_run.id} {extra_run.name}',
            kind='backtest', run_id=extra_run.id, model_type=extra_run.model_type,
            benchmark_code=extra_run.benchmark_code,
        )
        if extra_series is not None:
            payload['series'].append(extra_series)

    missing = []
    for code, kind in _benchmark_specs(run):
        benchmark_series = _build_benchmark_series(
            code, kind, run.start_date, run.end_date, dates, baseline_value
        )
        if benchmark_series is None:
            missing.append(code)
            continue
        payload['series'].append(benchmark_series)

    if missing:
        payload['message'] = (
            'No rows in markets.BenchmarkIndexDaily for: '
            + ', '.join(missing)
            + '. Sync the index history to plot it.'
        )

    payload['available_series_keys'] = [series['key'] for series in payload['series']]
    return payload
