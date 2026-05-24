from decimal import Decimal

from django.db import models

from apps.analytics.technical_staleness import (
    latest_official_trade_date,
    ordered_trading_dates_for_asset,
    stored_indicator_is_fresh,
    trading_date_positions,
)
from apps.analytics.models import TechnicalIndicator
from apps.markets.models import Asset, OHLCV


def _cache_key(*parts):
    return ('historical_features', *parts)


def _parameter_cache_items(parameters):
    return tuple(sorted((str(key), str(value)) for key, value in (parameters or {}).items()))


def _to_decimal(value, default):
    if value is None:
        if default is None:
            return None
        return Decimal(str(default))
    try:
        return Decimal(str(value))
    except Exception:
        if default is None:
            return None
        return Decimal(str(default))


def _asset_trading_context(asset_id, as_of, cache=None):
    cache_entry = _cache_key('asset_trading_context', int(asset_id), str(as_of))
    if cache is not None and cache_entry in cache:
        return cache[cache_entry]

    asset = Asset.objects.select_related('market').get(id=asset_id)
    ordered_trading_dates = ordered_trading_dates_for_asset(asset, as_of)
    position_map = trading_date_positions(ordered_trading_dates)
    current_trade_date = latest_official_trade_date(ordered_trading_dates, as_of)
    resolved = (asset, current_trade_date, position_map)
    if cache is not None:
        cache[cache_entry] = resolved
    return resolved


def _indicator_parameter_score(actual_parameters, expected_parameters, required_keys):
    resolved_actual = dict(actual_parameters or {})
    resolved_expected = dict(expected_parameters or {})
    if not resolved_expected:
        return 1
    if not resolved_actual:
        return 1

    score = 1
    for key in required_keys:
        if key not in resolved_actual:
            return -1
        if resolved_actual.get(key) != resolved_expected.get(key):
            return -1
        score += 1

    for key, value in resolved_expected.items():
        if key in required_keys or key not in resolved_actual:
            continue
        if resolved_actual.get(key) != value:
            return -1
        score += 1
    return score


def _parameter_compatibility_filter(parameter_key, parameter_value):
    if not parameter_key:
        return models.Q()
    return (
        models.Q(**{f'parameters__{parameter_key}': parameter_value})
        | models.Q(parameters={})
        | models.Q(parameters__isnull=True)
    )


def _matching_indicator_rows(
    asset_id,
    as_of,
    indicator_type,
    *,
    expected_parameters=None,
    required_keys=(),
    parameter_key=None,
    parameter_value=None,
    limit=1,
    cache=None,
):
    cache_entry = _cache_key(
        'matching_indicator_rows',
        int(asset_id),
        str(as_of),
        str(indicator_type),
        _parameter_cache_items(expected_parameters),
        tuple(required_keys or ()),
        str(parameter_key or ''),
        '' if parameter_value is None else str(parameter_value),
        int(limit or 1),
    )
    if cache is not None and cache_entry in cache:
        return cache[cache_entry]

    queryset = TechnicalIndicator.objects.filter(
        asset_id=asset_id,
        indicator_type=indicator_type,
        timestamp__date__lte=as_of,
    )
    if parameter_key:
        queryset = queryset.filter(_parameter_compatibility_filter(parameter_key, parameter_value))

    candidates = list(queryset.order_by('-timestamp')[: max(int(limit or 1) * 4, 12)])
    best_rows_by_date = {}
    for indicator in candidates:
        score = _indicator_parameter_score(
            getattr(indicator, 'parameters', None),
            expected_parameters,
            required_keys,
        )
        if score < 0:
            continue
        indicator_date = indicator.timestamp.date()
        existing = best_rows_by_date.get(indicator_date)
        if existing is None or score > existing[0]:
            best_rows_by_date[indicator_date] = (score, indicator)

    ranked_dates = sorted(best_rows_by_date.keys(), reverse=True)
    rows = [best_rows_by_date[indicator_date][1] for indicator_date in ranked_dates[:limit]]
    if cache is not None:
        cache[cache_entry] = rows
    return rows


def _latest_indicator_value(
    asset_id,
    as_of,
    indicator_type,
    *,
    default=None,
    expected_parameters=None,
    required_keys=(),
    parameter_key=None,
    parameter_value=None,
    max_gap=None,
    cache=None,
):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    indicators = _matching_indicator_rows(
        asset_id,
        as_of,
        indicator_type,
        expected_parameters=expected_parameters,
        required_keys=required_keys,
        parameter_key=parameter_key,
        parameter_value=parameter_value,
        limit=1,
        cache=cache,
    )
    if not indicators:
        return None if default is None else _to_decimal(default, default)

    if not stored_indicator_is_fresh(
        indicators[0].timestamp.date(),
        current_trade_date,
        position_map,
        indicator_type=indicator_type,
        parameters=expected_parameters,
        max_gap=max_gap,
    ):
        return None if default is None else _to_decimal(default, default)
    return _to_decimal(getattr(indicators[0], 'value', default), default)


def latest_ohlcv(asset_id, as_of, max_gap_trading_days=0, cache=None):
    cache_entry = _cache_key('latest_ohlcv', int(asset_id), str(as_of), int(max_gap_trading_days))
    if cache is not None and cache_entry in cache:
        return cache[cache_entry]

    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    latest = OHLCV.objects.filter(asset_id=asset_id, date__lte=as_of).order_by('-date').first()
    if latest is None:
        if cache is not None:
            cache[cache_entry] = None
        return None
    if not stored_indicator_is_fresh(
        latest.date,
        current_trade_date,
        position_map,
        max_gap=max_gap_trading_days,
    ):
        if cache is not None:
            cache[cache_entry] = None
        return None
    if cache is not None:
        cache[cache_entry] = latest
    return latest


def latest_rsi(asset_id, as_of, timeperiod=14, default=Decimal('50'), cache=None):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    indicators = _matching_indicator_rows(
        asset_id,
        as_of,
        'RSI',
        expected_parameters={'timeperiod': timeperiod},
        required_keys=('timeperiod',),
        parameter_key='timeperiod',
        parameter_value=timeperiod,
        limit=1,
        cache=cache,
    )
    if not indicators:
        return _to_decimal(default, default)

    if not stored_indicator_is_fresh(
        indicators[0].timestamp.date(),
        current_trade_date,
        position_map,
        indicator_type='RSI',
        parameters={'timeperiod': timeperiod},
    ):
        return _to_decimal(default, default)
    return _to_decimal(getattr(indicators[0], 'value', default), default)


def latest_momentum(asset_id, as_of, n_days=5, default=Decimal('0'), cache=None):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    indicators = _matching_indicator_rows(
        asset_id,
        as_of,
        'MOM_5D' if int(n_days) == 5 else f'MOM_{int(n_days)}D',
        expected_parameters={'n_days': int(n_days)},
        required_keys=('n_days',),
        parameter_key='n_days',
        parameter_value=int(n_days),
        limit=1,
        cache=cache,
    )
    if not indicators:
        return _to_decimal(default, default)

    if not stored_indicator_is_fresh(
        indicators[0].timestamp.date(),
        current_trade_date,
        position_map,
        max_gap=0,
    ):
        return _to_decimal(default, default)
    return _to_decimal(getattr(indicators[0], 'value', default), default)


def latest_bbands(asset_id, as_of, timeperiod=20, nbdevup=2, nbdevdn=2, cache=None):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    parameters = {'timeperiod': timeperiod, 'nbdevup': nbdevup, 'nbdevdn': nbdevdn}
    indicators = _matching_indicator_rows(
        asset_id,
        as_of,
        'BBANDS',
        expected_parameters=parameters,
        required_keys=('timeperiod',),
        parameter_key='timeperiod',
        parameter_value=timeperiod,
        limit=1,
        cache=cache,
    )
    if not indicators:
        return None

    if not stored_indicator_is_fresh(
        indicators[0].timestamp.date(),
        current_trade_date,
        position_map,
        indicator_type='BBANDS',
        parameters=parameters,
    ):
        return None

    latest = indicators[0]
    stored_parameters = dict(getattr(latest, 'parameters', None) or {})
    if 'upper' not in stored_parameters or 'lower' not in stored_parameters:
        return None
    return {
        'upper': _to_decimal(stored_parameters.get('upper'), '0'),
        'middle': _to_decimal(stored_parameters.get('middle', getattr(latest, 'value', None)), '0'),
        'lower': _to_decimal(stored_parameters.get('lower'), '0'),
    }


def latest_sma(asset_id, as_of, timeperiod=60, default=None, cache=None):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    parameters = {'timeperiod': timeperiod}
    indicators = _matching_indicator_rows(
        asset_id,
        as_of,
        'SMA',
        expected_parameters=parameters,
        required_keys=('timeperiod',),
        parameter_key='timeperiod',
        parameter_value=timeperiod,
        limit=1,
        cache=cache,
    )
    if not indicators:
        return None if default is None else _to_decimal(default, default)

    if not stored_indicator_is_fresh(
        indicators[0].timestamp.date(),
        current_trade_date,
        position_map,
        indicator_type='SMA',
        parameters=parameters,
    ):
        return None if default is None else _to_decimal(default, default)
    return _to_decimal(getattr(indicators[0], 'value', default), default or '0')


def latest_rs_score(asset_id, as_of, default=Decimal('0.5'), cache=None):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of, cache=cache)
    cache_entry = _cache_key('latest_rs_score_indicator', int(asset_id), str(as_of))
    if cache is not None and cache_entry in cache:
        indicator = cache[cache_entry]
    else:
        indicator = TechnicalIndicator.objects.filter(
            asset_id=asset_id,
            indicator_type='RS_SCORE',
            timestamp__date__lte=as_of,
        ).order_by('-timestamp').first()
        if cache is not None:
            cache[cache_entry] = indicator

    if indicator is None:
        return _to_decimal(default, default)
    if not stored_indicator_is_fresh(
        indicator.timestamp.date(),
        current_trade_date,
        position_map,
        indicator_type='RS_SCORE',
    ):
        return _to_decimal(default, default)
    return _to_decimal(getattr(indicator, 'value', default), default)


def latest_return(asset_id, as_of, n_days=3, default=Decimal('0'), cache=None):
    resolved_days = int(n_days)
    return _latest_indicator_value(
        asset_id,
        as_of,
        f'RETURN_{resolved_days}D',
        default=default,
        expected_parameters={'n_days': resolved_days},
        required_keys=('n_days',),
        parameter_key='n_days',
        parameter_value=resolved_days,
        max_gap=0,
        cache=cache,
    )


def latest_relative_volume(asset_id, as_of, n_days=5, default=Decimal('1'), cache=None):
    resolved_days = int(n_days)
    return _latest_indicator_value(
        asset_id,
        as_of,
        f'RELATIVE_VOLUME_{resolved_days}D',
        default=default,
        expected_parameters={'n_days': resolved_days},
        required_keys=('n_days',),
        parameter_key='n_days',
        parameter_value=resolved_days,
        max_gap=0,
        cache=cache,
    )


def latest_realized_volatility(asset_id, as_of, window=5, default=Decimal('0'), cache=None):
    resolved_window = int(window)
    return _latest_indicator_value(
        asset_id,
        as_of,
        f'REALIZED_VOLATILITY_{resolved_window}D',
        default=default,
        expected_parameters={'window': resolved_window},
        required_keys=('window',),
        parameter_key='window',
        parameter_value=resolved_window,
        max_gap=0,
        cache=cache,
    )
