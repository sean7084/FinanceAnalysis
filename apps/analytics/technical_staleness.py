from bisect import bisect_right
from datetime import date

from apps.markets.models import ExchangeTradingCalendar

from .indicator_warmup import technical_indicator_variant_warmup_lookback


BASE_MAX_GAP_TRADING_DAYS = {
    'ADX': 5,
    'BBANDS': 5,
    'FIB_RET': 10,
    'MACD': 3,
    'OBV': 7,
    'REALIZED_VOLATILITY_5D': 0,
    'RELATIVE_VOLUME_20D': 0,
    'RELATIVE_VOLUME_5D': 0,
    'RETURN_10D': 0,
    'RETURN_3D': 0,
    'RETURN_5D': 0,
    'RSI': 3,
    'RS_SCORE': 5,
    'STOCH': 5,
}
MOVING_AVERAGE_MAX_GAP_BUCKETS = (
    (5, 2),
    (10, 3),
    (20, 5),
    (50, 7),
    (100, 10),
    (10 ** 9, 15),
)


def _resolved_indicator_type(indicator_type):
    return str(indicator_type or '').strip().upper()


def _resolve_positive_int_parameter(parameters, key, default):
    if parameters is None:
        return max(int(default or 0), 0)
    return max(int(parameters.get(key, default) or 0), 0)


def _moving_average_max_gap(timeperiod):
    resolved_period = max(int(timeperiod or 0), 0)
    for max_period, max_gap in MOVING_AVERAGE_MAX_GAP_BUCKETS:
        if resolved_period <= max_period:
            return max_gap
    return MOVING_AVERAGE_MAX_GAP_BUCKETS[-1][1]


def asset_exchange_code(asset):
    ts_code = str(getattr(asset, 'ts_code', '') or '').upper()
    if ts_code.endswith('.SH'):
        return 'SSE'
    if ts_code.endswith('.SZ'):
        return 'SZSE'
    if ts_code.endswith('.BJ'):
        return 'BSE'
    return str(getattr(getattr(asset, 'market', None), 'code', '') or '').upper()


def technical_indicator_max_gap_trading_days(indicator_type, parameters=None):
    resolved_indicator_type = _resolved_indicator_type(indicator_type)
    if resolved_indicator_type in {'EMA', 'SMA'}:
        return _moving_average_max_gap(_resolve_positive_int_parameter(parameters, 'timeperiod', 0))
    return BASE_MAX_GAP_TRADING_DAYS.get(resolved_indicator_type)


def technical_indicator_gap_window_points(indicator_type, parameters=None):
    resolved_indicator_type = _resolved_indicator_type(indicator_type)
    if resolved_indicator_type == 'FIB_RET':
        return max(_resolve_positive_int_parameter(parameters, 'lookback_days', 60), 2)
    if resolved_indicator_type == 'RS_SCORE':
        return 21
    return max(technical_indicator_variant_warmup_lookback(resolved_indicator_type, parameters) + 1, 2)


def _ordered_trading_dates(exchange_code, end_date_iso):
    if not exchange_code:
        return tuple()
    end_date = date.fromisoformat(str(end_date_iso))
    return tuple(
        ExchangeTradingCalendar.objects.filter(
            exchange_code=exchange_code,
            trade_date__lte=end_date,
            is_open=True,
        )
        .order_by('trade_date')
        .values_list('trade_date', flat=True)
    )


def ordered_trading_dates_for_exchange(exchange_code, end_date):
    return _ordered_trading_dates(str(exchange_code or '').upper(), end_date.isoformat())


def ordered_trading_dates_for_asset(asset, end_date):
    return ordered_trading_dates_for_exchange(asset_exchange_code(asset), end_date)


def latest_official_trade_date(ordered_trading_dates, as_of):
    index = bisect_right(ordered_trading_dates, as_of)
    if index <= 0:
        return None
    return ordered_trading_dates[index - 1]


def trading_date_positions(ordered_trading_dates):
    return {trade_date: index for index, trade_date in enumerate(ordered_trading_dates)}


def trading_day_distance(position_map, earlier_date, later_date):
    earlier_position = position_map.get(earlier_date)
    later_position = position_map.get(later_date)
    if earlier_position is None or later_position is None or later_position < earlier_position:
        return None
    return later_position - earlier_position


def trailing_indicator_window_is_fresh(
    actual_dates,
    current_trade_date,
    position_map,
    *,
    indicator_type=None,
    parameters=None,
    required_points=None,
    max_gap=None,
):
    resolved_actual_dates = list(actual_dates or [])
    if not resolved_actual_dates or current_trade_date is None:
        return False

    resolved_max_gap = technical_indicator_max_gap_trading_days(indicator_type, parameters) if max_gap is None else int(max_gap)
    if resolved_max_gap is None:
        return False

    resolved_required_points = int(required_points or technical_indicator_gap_window_points(indicator_type, parameters))
    if resolved_required_points <= 0 or len(resolved_actual_dates) < resolved_required_points:
        return False

    latest_actual_date = resolved_actual_dates[-1]
    latest_gap = trading_day_distance(position_map, latest_actual_date, current_trade_date)
    if latest_gap is None or latest_gap > resolved_max_gap:
        return False

    window_dates = resolved_actual_dates[-resolved_required_points:]
    for previous_date, next_date in zip(window_dates, window_dates[1:]):
        gap = trading_day_distance(position_map, previous_date, next_date)
        if gap is None or gap > resolved_max_gap:
            return False
    return True


def exact_trading_window_available(actual_dates, current_trade_date, position_map, periods):
    resolved_periods = max(int(periods or 0), 0)
    resolved_actual_dates = list(actual_dates or [])
    if not resolved_actual_dates or current_trade_date is None:
        return False
    if resolved_actual_dates[-1] != current_trade_date:
        return False

    required_points = resolved_periods + 1
    if len(resolved_actual_dates) < required_points:
        return False

    anchor_date = resolved_actual_dates[-required_points]
    return trading_day_distance(position_map, anchor_date, current_trade_date) == resolved_periods


def stored_indicator_is_fresh(
    indicator_date,
    current_trade_date,
    position_map,
    *,
    indicator_type=None,
    parameters=None,
    max_gap=None,
):
    if indicator_date is None or current_trade_date is None:
        return False

    resolved_max_gap = technical_indicator_max_gap_trading_days(indicator_type, parameters) if max_gap is None else int(max_gap)
    if resolved_max_gap is None:
        return False

    age = trading_day_distance(position_map, indicator_date, current_trade_date)
    return age is not None and age <= resolved_max_gap