from decimal import Decimal, ROUND_CEILING

from apps.analytics.models import TechnicalIndicator
from apps.markets.models import OHLCV


def _to_decimal(value, default='0'):
    if value is None:
        return Decimal(str(default))
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(str(default))


def _quantize_price(value):
    return value.quantize(Decimal('0.0001'))


def _quantize_ratio(value):
    return value.quantize(Decimal('0.000001'))


def _round_price_ceiling(price):
    if price < Decimal('10'):
        step = Decimal('0.5')
    elif price < Decimal('50'):
        step = Decimal('1')
    elif price < Decimal('100'):
        step = Decimal('2')
    elif price < Decimal('500'):
        step = Decimal('5')
    else:
        step = Decimal('10')
    return ((price / step).to_integral_value(rounding=ROUND_CEILING)) * step


def _latest_indicator(asset_id, as_of, indicator_type, timeperiod=None):
    queryset = TechnicalIndicator.objects.filter(
        asset_id=asset_id,
        indicator_type=indicator_type,
        timestamp__date__lte=as_of,
    )
    if timeperiod is not None:
        queryset = queryset.filter(parameters__timeperiod=timeperiod)
    return queryset.order_by('-timestamp').first()


def estimate_trade_decision(asset_id, as_of, horizon_days, up_probability, predicted_label):
    latest_bar = OHLCV.objects.filter(asset_id=asset_id, date__lte=as_of).order_by('-date').first()
    if latest_bar is None:
        return {
            'target_price': None,
            'stop_loss_price': None,
            'risk_reward_ratio': None,
            'trade_score': None,
            'suggested': False,
        }

    current_close = _to_decimal(latest_bar.close, '0')
    if current_close <= 0:
        return {
            'target_price': None,
            'stop_loss_price': None,
            'risk_reward_ratio': None,
            'trade_score': None,
            'suggested': False,
        }

    recent_rows = list(
        OHLCV.objects.filter(asset_id=asset_id, date__lte=as_of)
        .order_by('-date')
        .values('high', 'low')[:60]
    )

    highs_20 = [_to_decimal(row['high'], current_close) for row in recent_rows[:20]]
    highs_60 = [_to_decimal(row['high'], current_close) for row in recent_rows]
    lows_20 = [_to_decimal(row['low'], current_close) for row in recent_rows[:20]]
    lows_60 = [_to_decimal(row['low'], current_close) for row in recent_rows]

    bbands = _latest_indicator(asset_id, as_of, 'BBANDS')
    sma_60 = _latest_indicator(asset_id, as_of, 'SMA', timeperiod=60) or _latest_indicator(asset_id, as_of, 'SMA', timeperiod=50)

    upper_band = _to_decimal((bbands.parameters if bbands else {}).get('upper'), current_close)
    lower_band = _to_decimal((bbands.parameters if bbands else {}).get('lower'), current_close)
    moving_average_support = _to_decimal(getattr(sma_60, 'value', None), current_close)

    resistance_candidates = [
        value for value in [
            max(highs_20, default=current_close),
            max(highs_60, default=current_close),
            upper_band,
            _round_price_ceiling(current_close * Decimal('1.01')),
        ] if value > current_close
    ]
    support_candidates = [
        value for value in [
            min(lows_20, default=current_close),
            min(lows_60, default=current_close),
            lower_band,
            moving_average_support,
        ] if value < current_close
    ]

    upside_fallback = {3: Decimal('0.03'), 7: Decimal('0.06'), 30: Decimal('0.12')}.get(int(horizon_days), Decimal('0.05'))
    downside_fallback = {3: Decimal('0.02'), 7: Decimal('0.04'), 30: Decimal('0.08')}.get(int(horizon_days), Decimal('0.03'))

    target_price = min(resistance_candidates) if resistance_candidates else current_close * (Decimal('1') + upside_fallback)
    stop_loss_price = max(support_candidates) if support_candidates else current_close * (Decimal('1') - downside_fallback)

    reward = max(target_price - current_close, current_close * Decimal('0.005'))
    risk = max(current_close - stop_loss_price, current_close * Decimal('0.005'))
    risk_reward_ratio = reward / risk if risk > 0 else Decimal('0')

    up_prob = _to_decimal(up_probability, '0')
    down_risk = max(Decimal('0.05'), Decimal('1') - up_prob)
    trade_score = (up_prob * reward) / (down_risk * risk) if risk > 0 else Decimal('0')
    suggested = predicted_label == 'UP' and risk_reward_ratio >= Decimal('1.5') and trade_score >= Decimal('1')

    return {
        'target_price': _quantize_price(target_price),
        'stop_loss_price': _quantize_price(stop_loss_price),
        'risk_reward_ratio': _quantize_ratio(risk_reward_ratio),
        'trade_score': _quantize_ratio(trade_score),
        'suggested': suggested,
    }