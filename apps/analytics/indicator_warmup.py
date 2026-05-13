from datetime import timedelta


TECHNICAL_INDICATOR_WARMUP_LOOKBACK_TRADING_DAYS = {
    'ADX': 27,
    'BBANDS': 19,
    'EMA': 4,
    'FIB_RET': 1,
    'MACD': 33,
    'MOM_10D': 10,
    'MOM_20D': 20,
    'MOM_5D': 5,
    'OBV': 1,
    'RSI': 14,
    'SMA': 4,
    'STOCH': 17,
}
DEFAULT_WARMUP_TECHNICAL_INDICATORS = tuple(TECHNICAL_INDICATOR_WARMUP_LOOKBACK_TRADING_DAYS.keys())
WARMUP_CALENDAR_BUFFER_MULTIPLIER = 2
MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS = 548


def _resolve_positive_int_parameter(parameters, key, default):
    if parameters is None:
        return max(int(default or 0), 0)
    return max(int(parameters.get(key, default) or 0), 0)


def technical_indicator_variant_warmup_lookback(indicator_type, parameters=None):
    resolved_indicator_type = str(indicator_type or '').strip().upper()
    if not resolved_indicator_type:
        raise ValueError('indicator_type must be provided.')

    if resolved_indicator_type in {'EMA', 'SMA'}:
        return max(_resolve_positive_int_parameter(parameters, 'timeperiod', 0) - 1, 0)
    if resolved_indicator_type == 'BBANDS':
        return max(_resolve_positive_int_parameter(parameters, 'timeperiod', 20) - 1, 0)
    if resolved_indicator_type == 'MACD':
        fastperiod = _resolve_positive_int_parameter(parameters, 'fastperiod', 12)
        slowperiod = _resolve_positive_int_parameter(parameters, 'slowperiod', 26)
        signalperiod = _resolve_positive_int_parameter(parameters, 'signalperiod', 9)
        return max(max(fastperiod, slowperiod) + signalperiod - 2, 0)
    if resolved_indicator_type == 'STOCH':
        fastk_period = _resolve_positive_int_parameter(parameters, 'fastk_period', 14)
        slowk_period = _resolve_positive_int_parameter(parameters, 'slowk_period', 3)
        slowd_period = _resolve_positive_int_parameter(parameters, 'slowd_period', 3)
        return max(fastk_period + slowk_period + slowd_period - 3, 0)
    if resolved_indicator_type == 'ADX':
        return max((_resolve_positive_int_parameter(parameters, 'timeperiod', 14) * 2) - 1, 0)
    if resolved_indicator_type == 'RSI':
        return _resolve_positive_int_parameter(parameters, 'timeperiod', 14)
    if resolved_indicator_type in {'FIB_RET', 'OBV'}:
        return 1
    if resolved_indicator_type.startswith('MOM_'):
        return _resolve_positive_int_parameter(
            parameters,
            'n_days',
            TECHNICAL_INDICATOR_WARMUP_LOOKBACK_TRADING_DAYS.get(resolved_indicator_type, 0),
        )
    if resolved_indicator_type == 'RS_SCORE':
        return 20
    if resolved_indicator_type in TECHNICAL_INDICATOR_WARMUP_LOOKBACK_TRADING_DAYS:
        return TECHNICAL_INDICATOR_WARMUP_LOOKBACK_TRADING_DAYS[resolved_indicator_type]
    raise ValueError(f'Unsupported technical indicator type: {resolved_indicator_type}')


def minimum_history_prefill_start_date(start_date, calendar_days=MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS):
    resolved_calendar_days = int(calendar_days or 0)
    if resolved_calendar_days <= 0:
        return start_date
    return start_date - timedelta(days=resolved_calendar_days)


def max_technical_indicator_warmup_lookback(indicator_types=None):
    resolved_indicator_types = tuple(dict.fromkeys(
        item.strip().upper()
        for item in (indicator_types or DEFAULT_WARMUP_TECHNICAL_INDICATORS)
        if str(item or '').strip()
    ))
    if not resolved_indicator_types:
        return 0

    unknown = [
        indicator_type
        for indicator_type in resolved_indicator_types
        if indicator_type not in TECHNICAL_INDICATOR_WARMUP_LOOKBACK_TRADING_DAYS
    ]
    if unknown:
        raise ValueError(
            f'Unsupported technical indicator warm-up type(s): {", ".join(unknown)}'
        )

    return max(
        TECHNICAL_INDICATOR_WARMUP_LOOKBACK_TRADING_DAYS[indicator_type]
        for indicator_type in resolved_indicator_types
    )


def technical_indicator_warmup_prefill_calendar_days(
    indicator_types=None,
    minimum_calendar_days=MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS,
):
    lookback = max_technical_indicator_warmup_lookback(indicator_types)
    return max(
        int(minimum_calendar_days or 0),
        lookback * WARMUP_CALENDAR_BUFFER_MULTIPLIER,
    )


def technical_indicator_warmup_prefill_start_date(
    start_date,
    indicator_types=None,
    minimum_calendar_days=MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS,
):
    return minimum_history_prefill_start_date(
        start_date,
        calendar_days=technical_indicator_warmup_prefill_calendar_days(
            indicator_types,
            minimum_calendar_days=minimum_calendar_days,
        ),
    )