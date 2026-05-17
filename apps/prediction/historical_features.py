from decimal import Decimal

import pandas as pd
import talib

from apps.analytics.technical_staleness import (
    exact_trading_window_available,
    latest_official_trade_date,
    ordered_trading_dates_for_asset,
    stored_indicator_is_fresh,
    trading_date_positions,
    trailing_indicator_window_is_fresh,
)
from apps.analytics.models import TechnicalIndicator
from apps.markets.models import Asset, OHLCV


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


def _asset_trading_context(asset_id, as_of):
    asset = Asset.objects.select_related('market').get(id=asset_id)
    ordered_trading_dates = ordered_trading_dates_for_asset(asset, as_of)
    position_map = trading_date_positions(ordered_trading_dates)
    current_trade_date = latest_official_trade_date(ordered_trading_dates, as_of)
    return asset, current_trade_date, position_map


def _ohlcv_rows(asset_id, as_of, limit):
    rows = list(
        OHLCV.objects.filter(asset_id=asset_id, date__lte=as_of)
        .order_by('-date')
        .values('date', 'open', 'high', 'low', 'close', 'volume')[:limit]
    )
    rows.reverse()
    return rows


def _ohlcv_frame(asset_id, as_of, limit):
    rows = _ohlcv_rows(asset_id, as_of, limit)
    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame.from_records(rows)
    frame.set_index('date', inplace=True)
    for column in ['open', 'high', 'low', 'close', 'volume']:
        frame[column] = frame[column].astype(float)
    return frame


def latest_ohlcv(asset_id, as_of, max_gap_trading_days=0):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of)
    latest = OHLCV.objects.filter(asset_id=asset_id, date__lte=as_of).order_by('-date').first()
    if latest is None:
        return None
    if not stored_indicator_is_fresh(
        latest.date,
        current_trade_date,
        position_map,
        max_gap=max_gap_trading_days,
    ):
        return None
    return latest


def latest_rsi(asset_id, as_of, timeperiod=14, default=Decimal('50')):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of)
    frame = _ohlcv_frame(asset_id, as_of, max(timeperiod * 5, timeperiod + 5))
    if frame.empty or len(frame) < timeperiod:
        return _to_decimal(default, default)

    if not trailing_indicator_window_is_fresh(
        list(frame.index),
        current_trade_date,
        position_map,
        indicator_type='RSI',
        parameters={'timeperiod': timeperiod},
    ):
        return _to_decimal(default, default)

    values = talib.RSI(frame['close'], timeperiod=timeperiod)
    valid = values.dropna()
    if valid.empty:
        return _to_decimal(default, default)
    return _to_decimal(valid.iloc[-1], default)


def latest_momentum(asset_id, as_of, n_days=5, default=Decimal('0')):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of)
    rows = _ohlcv_rows(asset_id, as_of, n_days + 1)
    if len(rows) <= n_days:
        return _to_decimal(default, default)

    if not exact_trading_window_available(
        [row['date'] for row in rows],
        current_trade_date,
        position_map,
        n_days,
    ):
        return _to_decimal(default, default)

    current_close = _to_decimal(rows[-1]['close'], default)
    past_close = _to_decimal(rows[-(n_days + 1)]['close'], default)
    if past_close == 0:
        return _to_decimal(default, default)
    return (current_close - past_close) / past_close


def latest_bbands(asset_id, as_of, timeperiod=20, nbdevup=2, nbdevdn=2):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of)
    frame = _ohlcv_frame(asset_id, as_of, max(timeperiod * 4, timeperiod + 5))
    if frame.empty or len(frame) < timeperiod:
        return None

    if not trailing_indicator_window_is_fresh(
        list(frame.index),
        current_trade_date,
        position_map,
        indicator_type='BBANDS',
        parameters={'timeperiod': timeperiod, 'nbdevup': nbdevup, 'nbdevdn': nbdevdn},
    ):
        return None

    upper, middle, lower = talib.BBANDS(
        frame['close'],
        timeperiod=timeperiod,
        nbdevup=nbdevup,
        nbdevdn=nbdevdn,
    )
    valid_index = upper.dropna().index
    if len(valid_index) == 0:
        return None

    index = valid_index[-1]
    return {
        'upper': _to_decimal(upper.loc[index], '0'),
        'middle': _to_decimal(middle.loc[index], '0'),
        'lower': _to_decimal(lower.loc[index], '0'),
    }


def latest_sma(asset_id, as_of, timeperiod=60, default=None):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of)
    frame = _ohlcv_frame(asset_id, as_of, max(timeperiod * 3, timeperiod + 5))
    if frame.empty or len(frame) < timeperiod:
        return None if default is None else _to_decimal(default, default)

    if not trailing_indicator_window_is_fresh(
        list(frame.index),
        current_trade_date,
        position_map,
        indicator_type='SMA',
        parameters={'timeperiod': timeperiod},
    ):
        return None if default is None else _to_decimal(default, default)

    values = talib.SMA(frame['close'], timeperiod=timeperiod)
    valid = values.dropna()
    if valid.empty:
        return None if default is None else _to_decimal(default, default)
    return _to_decimal(valid.iloc[-1], default or '0')


def latest_rs_score(asset_id, as_of, default=Decimal('0.5')):
    _asset, current_trade_date, position_map = _asset_trading_context(asset_id, as_of)
    indicator = TechnicalIndicator.objects.filter(
        asset_id=asset_id,
        indicator_type='RS_SCORE',
        timestamp__date__lte=as_of,
    ).order_by('-timestamp').first()
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
