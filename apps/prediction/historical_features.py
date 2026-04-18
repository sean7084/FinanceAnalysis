from decimal import Decimal

import pandas as pd
import talib

from apps.analytics.models import TechnicalIndicator
from apps.markets.models import OHLCV


def _to_decimal(value, default):
    if value is None:
        return Decimal(str(default))
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(str(default))


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


def latest_ohlcv(asset_id, as_of):
    return OHLCV.objects.filter(asset_id=asset_id, date__lte=as_of).order_by('-date').first()


def latest_rsi(asset_id, as_of, timeperiod=14, default=Decimal('50')):
    frame = _ohlcv_frame(asset_id, as_of, max(timeperiod * 5, timeperiod + 5))
    if frame.empty or len(frame) < timeperiod:
        return _to_decimal(default, default)

    values = talib.RSI(frame['close'], timeperiod=timeperiod)
    valid = values.dropna()
    if valid.empty:
        return _to_decimal(default, default)
    return _to_decimal(valid.iloc[-1], default)


def latest_momentum(asset_id, as_of, n_days=5, default=Decimal('0')):
    rows = _ohlcv_rows(asset_id, as_of, n_days + 1)
    if len(rows) <= n_days:
        return _to_decimal(default, default)

    current_close = _to_decimal(rows[-1]['close'], default)
    past_close = _to_decimal(rows[-(n_days + 1)]['close'], default)
    if past_close == 0:
        return _to_decimal(default, default)
    return (current_close - past_close) / past_close


def latest_bbands(asset_id, as_of, timeperiod=20, nbdevup=2, nbdevdn=2):
    frame = _ohlcv_frame(asset_id, as_of, max(timeperiod * 4, timeperiod + 5))
    if frame.empty or len(frame) < timeperiod:
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
    frame = _ohlcv_frame(asset_id, as_of, max(timeperiod * 3, timeperiod + 5))
    if frame.empty or len(frame) < timeperiod:
        return None if default is None else _to_decimal(default, default)

    values = talib.SMA(frame['close'], timeperiod=timeperiod)
    valid = values.dropna()
    if valid.empty:
        return None if default is None else _to_decimal(default, default)
    return _to_decimal(valid.iloc[-1], default or '0')


def latest_rs_score(asset_id, as_of, default=Decimal('0.5')):
    indicator = TechnicalIndicator.objects.filter(
        asset_id=asset_id,
        indicator_type='RS_SCORE',
        timestamp__date__lte=as_of,
    ).order_by('-timestamp').first()
    return _to_decimal(getattr(indicator, 'value', default), default)
