from decimal import Decimal, ROUND_CEILING

from apps.markets.models import OHLCV
from .historical_features import latest_bbands, latest_ohlcv, latest_sma


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


def estimate_trade_decision(asset_id, as_of, horizon_days, up_probability, predicted_label):
    latest_bar = latest_ohlcv(asset_id, as_of)
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

    bbands = latest_bbands(asset_id, as_of)
    sma_60 = latest_sma(asset_id, as_of, timeperiod=60)
    sma_50 = latest_sma(asset_id, as_of, timeperiod=50)

    upper_band = _to_decimal((bbands or {}).get('upper'), current_close)
    lower_band = _to_decimal((bbands or {}).get('lower'), current_close)
    moving_average_support = _to_decimal(sma_60 or sma_50, current_close)

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