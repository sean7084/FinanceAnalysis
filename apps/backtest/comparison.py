from decimal import Decimal

from apps.markets.models import BenchmarkIndexDaily, OHLCV

from .models import BacktestRun


DECIMAL_0 = Decimal('0')
DECIMAL_1 = Decimal('1')
BENCHMARK_SERIES_SPECS = (
    {'code': '000300.SH', 'key': 'csi300', 'label': 'CSI 300'},
    {'code': '000510.CSI', 'key': 'csia500', 'label': 'CSI A500'},
)


def _to_decimal(value):
    return Decimal(str(value))


def _run_trading_dates(run):
    return list(
        OHLCV.objects.filter(date__gte=run.start_date, date__lte=run.end_date)
        .values_list('date', flat=True)
        .distinct()
        .order_by('date')
    )


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


def _build_backtest_series(run, key, label, baseline_value):
    raw_curve = (run.report or {}).get('equity_curve') or []
    if not isinstance(raw_curve, list):
        return None

    cleaned_dates = []
    cleaned_values = []
    for dt, raw_value in zip(_run_trading_dates(run), raw_curve):
        try:
            cleaned_value = _to_decimal(raw_value)
        except Exception:
            continue
        cleaned_dates.append(dt)
        cleaned_values.append(cleaned_value)

    if not cleaned_values:
        return None

    scaled_values = _scale_values(cleaned_values, baseline_value)
    total_return = float((scaled_values[-1] - scaled_values[0]) / scaled_values[0]) if scaled_values[0] else None
    max_drawdown = min(_build_drawdown(scaled_values)) if scaled_values else DECIMAL_0
    prediction_source = str((run.report or {}).get('prediction_source') or (run.parameters or {}).get('prediction_source') or '').lower()

    return {
        'key': key,
        'label': label,
        'kind': 'backtest',
        'run_id': run.id,
        'prediction_source': prediction_source,
        'points': _series_points(cleaned_dates, scaled_values),
        'total_return': total_return,
        'max_drawdown': float(max_drawdown),
    }


def _build_benchmark_series(code, key, label, start_date, end_date, baseline_value):
    rows = list(
        BenchmarkIndexDaily.objects.filter(
            index_code=code,
            trade_date__gte=start_date,
            trade_date__lte=end_date,
        )
        .order_by('trade_date')
        .values_list('trade_date', 'close')
    )
    if not rows:
        return None

    dates = [trade_date for trade_date, _ in rows]
    close_values = [_to_decimal(close) for _, close in rows]
    scaled_values = _scale_values(close_values, baseline_value)
    total_return = float((scaled_values[-1] - scaled_values[0]) / scaled_values[0]) if scaled_values[0] else None
    max_drawdown = min(_build_drawdown(scaled_values)) if scaled_values else DECIMAL_0

    return {
        'key': key,
        'label': label,
        'kind': 'benchmark',
        'index_code': code,
        'points': _series_points(dates, scaled_values),
        'total_return': total_return,
        'max_drawdown': float(max_drawdown),
    }


def _resolve_compare_run(compare_run_id):
    if compare_run_id in (None, ''):
        return None
    try:
        return BacktestRun.objects.filter(id=int(compare_run_id)).first()
    except (TypeError, ValueError):
        return None


def _resolve_extra_compare_runs(run, compare_run, extra_compare_run_ids):
    if not extra_compare_run_ids:
        return []

    excluded_ids = {run.id}
    if compare_run is not None:
        excluded_ids.add(compare_run.id)

    requested_ids = []
    seen_ids = set(excluded_ids)
    for run_id in extra_compare_run_ids:
        if run_id in seen_ids:
            continue
        seen_ids.add(run_id)
        requested_ids.append(run_id)

    if not requested_ids:
        return []

    run_map = {
        extra_run.id: extra_run
        for extra_run in BacktestRun.objects.filter(id__in=requested_ids, status=BacktestRun.Status.COMPLETED)
    }
    return [run_map[run_id] for run_id in requested_ids if run_id in run_map]


def build_backtest_comparison_payload(run, extra_compare_run_ids=None):
    payload = {
        'run': {
            'id': run.id,
            'name': run.name,
            'status': run.status,
            'start_date': run.start_date.isoformat(),
            'end_date': run.end_date.isoformat(),
            'initial_capital': float(run.initial_capital),
            'prediction_source': str((run.report or {}).get('prediction_source') or (run.parameters or {}).get('prediction_source') or '').lower(),
            'compare_backtest_run_id': (run.parameters or {}).get('compare_backtest_run_id'),
        },
        'series': [],
        'compare_target': None,
        'message': None,
    }

    if run.status != BacktestRun.Status.COMPLETED:
        payload['message'] = 'Comparison curves are available after the run completes.'
        return payload

    selected_series = _build_backtest_series(run, 'selected_run', f'#{run.id} {run.name}', None)
    if selected_series is None or not selected_series['points']:
        payload['message'] = 'No equity curve is stored for this run yet.'
        return payload

    baseline_value = _to_decimal(selected_series['points'][0]['value'])
    selected_series = _build_backtest_series(run, 'selected_run', f'#{run.id} {run.name}', baseline_value)
    if selected_series is not None:
        payload['series'].append(selected_series)

    compare_run_id = (run.parameters or {}).get('compare_backtest_run_id')
    compare_run = _resolve_compare_run(compare_run_id)

    if compare_run is not None and compare_run.status == BacktestRun.Status.COMPLETED:
        compare_series = _build_backtest_series(compare_run, 'compare_run', f'#{compare_run.id} {compare_run.name}', baseline_value)
        if compare_series is not None:
            payload['series'].append(compare_series)
            payload['compare_target'] = {
                'id': compare_run.id,
                'name': compare_run.name,
                'status': compare_run.status,
            }
    elif compare_run_id not in (None, ''):
        payload['compare_target'] = {
            'id': compare_run_id,
            'name': '',
            'status': compare_run.status if compare_run is not None else '',
        }

    for extra_run in _resolve_extra_compare_runs(run, compare_run, extra_compare_run_ids or []):
        extra_series = _build_backtest_series(extra_run, f'extra_run_{extra_run.id}', f'#{extra_run.id} {extra_run.name}', baseline_value)
        if extra_series is not None:
            payload['series'].append(extra_series)

    for spec in BENCHMARK_SERIES_SPECS:
        benchmark_series = _build_benchmark_series(
            spec['code'],
            spec['key'],
            spec['label'],
            run.start_date,
            run.end_date,
            baseline_value,
        )
        if benchmark_series is not None:
            payload['series'].append(benchmark_series)

    payload['available_series_keys'] = [series['key'] for series in payload['series']]
    return payload