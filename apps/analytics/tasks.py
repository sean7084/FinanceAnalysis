from celery import shared_task
from django.utils import timezone
from django.conf import settings
from django.core.mail import send_mail
import pandas as pd
import numpy as np
import talib
from datetime import timedelta
from decimal import Decimal
from urllib import request as urllib_request
from urllib import error as urllib_error
import json

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.markets.models import Asset, OHLCV
from .models import TechnicalIndicator, AlertRule, AlertEvent

def get_ohlcv_df(asset_id, days_history=200):
    """
    Fetches the last N days of OHLCV data for an asset and returns a DataFrame.
    """
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days_history)
    
    ohlcv_qs = OHLCV.objects.filter(
        asset_id=asset_id,
        date__gte=start_date
    ).order_by('date').values('date', 'open', 'high', 'low', 'close', 'volume')
    
    if not ohlcv_qs.exists():
        return pd.DataFrame()
        
    df = pd.DataFrame.from_records(ohlcv_qs)
    df.set_index('date', inplace=True)
    
    # Ensure dtypes are correct for TA-Lib
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)
    df['volume'] = df['volume'].astype(float)
    
    return df

@shared_task
def calculate_rsi_for_asset(asset_id, timeperiod=14):
    """
    Calculates the Relative Strength Index (RSI) for a given asset and saves it.
    """
    df = get_ohlcv_df(asset_id)
    if df.empty or len(df) < timeperiod:
        print(f"Not enough data for RSI calculation for asset {asset_id}.")
        return

    asset = Asset.objects.get(id=asset_id)
    rsi_values = talib.RSI(df['close'], timeperiod=timeperiod)
    
    last_valid_index = rsi_values.last_valid_index()
    if last_valid_index is None:
        print(f"RSI calculation resulted in all NaNs for asset {asset_id}.")
        return
        
    latest_rsi = rsi_values[last_valid_index]
    latest_date = last_valid_index
    
    # Convert date to datetime for TechnicalIndicator model
    from django.utils import timezone as tz
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))
    
    obj, created = TechnicalIndicator.objects.get_or_create(
        asset=asset,
        timestamp=latest_timestamp,
        indicator_type='RSI',
        parameters={'timeperiod': timeperiod},
        defaults={'value': latest_rsi}
    )
    action = "Created" if created else "Updated"
    print(f"{action} RSI for {asset.symbol} on {latest_date}: {latest_rsi}")


@shared_task
def calculate_macd_for_asset(asset_id, fastperiod=12, slowperiod=26, signalperiod=9):
    """
    Calculates the Moving Average Convergence Divergence (MACD) for a given asset and saves it.
    """
    df = get_ohlcv_df(asset_id)
    if df.empty or len(df) < slowperiod:
        print(f"Not enough data for MACD calculation for asset {asset_id}.")
        return

    asset = Asset.objects.get(id=asset_id)
    macd, macdsignal, macdhist = talib.MACD(
        df['close'], fastperiod=fastperiod, slowperiod=slowperiod, signalperiod=signalperiod
    )
    
    last_valid_index = macd.last_valid_index()
    if last_valid_index is None:
        print(f"MACD calculation resulted in all NaNs for asset {asset_id}.")
        return
        
    latest_macd = macd[last_valid_index]
    latest_date = last_valid_index
    
    # Convert date to datetime for TechnicalIndicator model
    from django.utils import timezone as tz
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))

    obj, created = TechnicalIndicator.objects.get_or_create(
        asset=asset,
        timestamp=latest_timestamp,
        indicator_type='MACD',
        parameters={'fastperiod': fastperiod, 'slowperiod': slowperiod, 'signalperiod': signalperiod},
        defaults={'value': latest_macd}
    )
    action = "Created" if created else "Updated"
    print(f"{action} MACD for {asset.symbol} on {latest_date}: {latest_macd}")


@shared_task
def calculate_bollinger_bands_for_asset(asset_id, timeperiod=20, nbdevup=2, nbdevdn=2):
    """
    Calculates Bollinger Bands (upper, middle, lower) for a given asset.
    """
    df = get_ohlcv_df(asset_id)
    if df.empty or len(df) < timeperiod:
        print(f"Not enough data for Bollinger Bands calculation for asset {asset_id}.")
        return

    asset = Asset.objects.get(id=asset_id)
    upper, middle, lower = talib.BBANDS(
        df['close'], timeperiod=timeperiod, nbdevup=nbdevup, nbdevdn=nbdevdn
    )
    
    last_valid_index = upper.last_valid_index()
    if last_valid_index is None:
        print(f"Bollinger Bands calculation resulted in all NaNs for asset {asset_id}.")
        return
        
    latest_date = last_valid_index
    from django.utils import timezone as tz
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))
    
    # Store all three bands as separate records or as JSON value
    # Using middle band as primary value, storing all three in parameters
    obj, created = TechnicalIndicator.objects.get_or_create(
        asset=asset,
        timestamp=latest_timestamp,
        indicator_type='BBANDS',
        parameters={'timeperiod': timeperiod, 'nbdevup': nbdevup, 'nbdevdn': nbdevdn},
        defaults={
            'value': float(middle[last_valid_index]),
            'parameters': {
                'timeperiod': timeperiod,
                'nbdevup': nbdevup,
                'nbdevdn': nbdevdn,
                'upper': float(upper[last_valid_index]),
                'middle': float(middle[last_valid_index]),
                'lower': float(lower[last_valid_index])
            }
        }
    )
    if not created:
        obj.value = float(middle[last_valid_index])
        obj.parameters = {
            'timeperiod': timeperiod,
            'nbdevup': nbdevup,
            'nbdevdn': nbdevdn,
            'upper': float(upper[last_valid_index]),
            'middle': float(middle[last_valid_index]),
            'lower': float(lower[last_valid_index])
        }
        obj.save()
    
    action = "Created" if created else "Updated"
    print(f"{action} Bollinger Bands for {asset.symbol} on {latest_date}")


@shared_task
def calculate_sma_for_asset(asset_id, timeperiods=[5, 10, 20, 50, 100, 200]):
    """
    Calculates Simple Moving Averages for multiple periods.
    """
    df = get_ohlcv_df(asset_id)
    if df.empty:
        print(f"Not enough data for SMA calculation for asset {asset_id}.")
        return

    asset = Asset.objects.get(id=asset_id)
    from django.utils import timezone as tz
    
    for period in timeperiods:
        if len(df) < period:
            continue
            
        sma_values = talib.SMA(df['close'], timeperiod=period)
        last_valid_index = sma_values.last_valid_index()
        
        if last_valid_index is None:
            continue
            
        latest_sma = sma_values[last_valid_index]
        latest_date = last_valid_index
        latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))
        
        obj, created = TechnicalIndicator.objects.get_or_create(
            asset=asset,
            timestamp=latest_timestamp,
            indicator_type=f'SMA',
            parameters={'timeperiod': period},
            defaults={'value': latest_sma}
        )
        if not created:
            obj.value = latest_sma
            obj.save()
        
        print(f"{'Created' if created else 'Updated'} SMA-{period} for {asset.symbol}")


@shared_task
def calculate_ema_for_asset(asset_id, timeperiods=[5, 10, 20, 50, 100, 200]):
    """
    Calculates Exponential Moving Averages for multiple periods.
    """
    df = get_ohlcv_df(asset_id)
    if df.empty:
        print(f"Not enough data for EMA calculation for asset {asset_id}.")
        return

    asset = Asset.objects.get(id=asset_id)
    from django.utils import timezone as tz
    
    for period in timeperiods:
        if len(df) < period:
            continue
            
        ema_values = talib.EMA(df['close'], timeperiod=period)
        last_valid_index = ema_values.last_valid_index()
        
        if last_valid_index is None:
            continue
            
        latest_ema = ema_values[last_valid_index]
        latest_date = last_valid_index
        latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))
        
        obj, created = TechnicalIndicator.objects.get_or_create(
            asset=asset,
            timestamp=latest_timestamp,
            indicator_type=f'EMA',
            parameters={'timeperiod': period},
            defaults={'value': latest_ema}
        )
        if not created:
            obj.value = latest_ema
            obj.save()
        
        print(f"{'Created' if created else 'Updated'} EMA-{period} for {asset.symbol}")


@shared_task
def calculate_stochastic_for_asset(asset_id, fastk_period=14, slowk_period=3, slowd_period=3):
    """
    Calculates Stochastic Oscillator (%K and %D lines).
    """
    df = get_ohlcv_df(asset_id)
    required_period = max(fastk_period, slowk_period) + slowd_period
    if df.empty or len(df) < required_period:
        print(f"Not enough data for Stochastic calculation for asset {asset_id}.")
        return

    asset = Asset.objects.get(id=asset_id)
    slowk, slowd = talib.STOCH(
        df['high'], df['low'], df['close'],
        fastk_period=fastk_period,
        slowk_period=slowk_period,
        slowk_matype=0,
        slowd_period=slowd_period,
        slowd_matype=0
    )
    
    last_valid_index = slowk.last_valid_index()
    if last_valid_index is None:
        print(f"Stochastic calculation resulted in all NaNs for asset {asset_id}.")
        return
        
    latest_date = last_valid_index
    from django.utils import timezone as tz
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))
    
    obj, created = TechnicalIndicator.objects.get_or_create(
        asset=asset,
        timestamp=latest_timestamp,
        indicator_type='STOCH',
        parameters={
            'fastk_period': fastk_period,
            'slowk_period': slowk_period,
            'slowd_period': slowd_period
        },
        defaults={
            'value': float(slowk[last_valid_index]),
            'parameters': {
                'fastk_period': fastk_period,
                'slowk_period': slowk_period,
                'slowd_period': slowd_period,
                'slowk': float(slowk[last_valid_index]),
                'slowd': float(slowd[last_valid_index])
            }
        }
    )
    if not created:
        obj.value = float(slowk[last_valid_index])
        obj.parameters = {
            'fastk_period': fastk_period,
            'slowk_period': slowk_period,
            'slowd_period': slowd_period,
            'slowk': float(slowk[last_valid_index]),
            'slowd': float(slowd[last_valid_index])
        }
        obj.save()
    
    action = "Created" if created else "Updated"
    print(f"{action} Stochastic for {asset.symbol} on {latest_date}")


@shared_task
def calculate_adx_for_asset(asset_id, timeperiod=14):
    """
    Calculates Average Directional Index (ADX) for trend strength.
    """
    df = get_ohlcv_df(asset_id)
    if df.empty or len(df) < timeperiod * 2:
        print(f"Not enough data for ADX calculation for asset {asset_id}.")
        return

    asset = Asset.objects.get(id=asset_id)
    adx = talib.ADX(df['high'], df['low'], df['close'], timeperiod=timeperiod)
    plus_di = talib.PLUS_DI(df['high'], df['low'], df['close'], timeperiod=timeperiod)
    minus_di = talib.MINUS_DI(df['high'], df['low'], df['close'], timeperiod=timeperiod)
    
    last_valid_index = adx.last_valid_index()
    if last_valid_index is None:
        print(f"ADX calculation resulted in all NaNs for asset {asset_id}.")
        return
        
    latest_date = last_valid_index
    from django.utils import timezone as tz
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))
    
    obj, created = TechnicalIndicator.objects.get_or_create(
        asset=asset,
        timestamp=latest_timestamp,
        indicator_type='ADX',
        parameters={'timeperiod': timeperiod},
        defaults={
            'value': float(adx[last_valid_index]),
            'parameters': {
                'timeperiod': timeperiod,
                'adx': float(adx[last_valid_index]),
                'plus_di': float(plus_di[last_valid_index]),
                'minus_di': float(minus_di[last_valid_index])
            }
        }
    )
    if not created:
        obj.value = float(adx[last_valid_index])
        obj.parameters = {
            'timeperiod': timeperiod,
            'adx': float(adx[last_valid_index]),
            'plus_di': float(plus_di[last_valid_index]),
            'minus_di': float(minus_di[last_valid_index])
        }
        obj.save()
    
    action = "Created" if created else "Updated"
    print(f"{action} ADX for {asset.symbol} on {latest_date}")


@shared_task
def calculate_obv_for_asset(asset_id):
    """
    Calculates On-Balance Volume (OBV) indicator.
    """
    df = get_ohlcv_df(asset_id)
    if df.empty or len(df) < 2:
        print(f"Not enough data for OBV calculation for asset {asset_id}.")
        return

    asset = Asset.objects.get(id=asset_id)
    obv_values = talib.OBV(df['close'], df['volume'])
    
    last_valid_index = obv_values.last_valid_index()
    if last_valid_index is None:
        print(f"OBV calculation resulted in all NaNs for asset {asset_id}.")
        return
        
    latest_obv = obv_values[last_valid_index]
    latest_date = last_valid_index
    from django.utils import timezone as tz
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))
    
    obj, created = TechnicalIndicator.objects.get_or_create(
        asset=asset,
        timestamp=latest_timestamp,
        indicator_type='OBV',
        parameters={},
        defaults={'value': latest_obv}
    )
    if not created:
        obj.value = latest_obv
        obj.save()
    
    action = "Created" if created else "Updated"
    print(f"{action} OBV for {asset.symbol} on {latest_date}: {latest_obv}")


@shared_task
def calculate_indicators_for_all_assets():
    """
    Triggers all indicator calculations for all assets.
    """
    asset_ids = Asset.objects.values_list('id', flat=True)
    print(f"Queueing indicator calculations for {len(asset_ids)} assets.")
    
    for asset_id in asset_ids:
        # Original indicators
        calculate_rsi_for_asset.delay(asset_id=asset_id)
        calculate_macd_for_asset.delay(asset_id=asset_id)
        
        # New Phase 8 indicators
        calculate_bollinger_bands_for_asset.delay(asset_id=asset_id)
        calculate_sma_for_asset.delay(asset_id=asset_id)
        calculate_ema_for_asset.delay(asset_id=asset_id)
        calculate_stochastic_for_asset.delay(asset_id=asset_id)
        calculate_adx_for_asset.delay(asset_id=asset_id)
        calculate_obv_for_asset.delay(asset_id=asset_id)
    
    return f"Successfully queued calculations for {len(asset_ids)} assets."


def _latest_price(asset_id):
    latest = OHLCV.objects.filter(asset_id=asset_id).order_by('-date').first()
    if not latest:
        return None
    return Decimal(str(latest.close))


def _latest_indicator_value(asset_id, indicator_type):
    latest = TechnicalIndicator.objects.filter(
        asset_id=asset_id,
        indicator_type=indicator_type,
    ).order_by('-timestamp').first()
    if not latest:
        return None
    return Decimal(str(latest.value))


def _is_cooldown_passed(rule):
    if rule.last_triggered_at is None:
        return True
    cooldown_delta = timedelta(minutes=rule.cooldown_minutes)
    return timezone.now() >= (rule.last_triggered_at + cooldown_delta)


def _evaluate_alert_rule(rule):
    threshold = Decimal(str(rule.threshold))
    current_value = None
    should_trigger = False
    label = ''

    if rule.condition_type == AlertRule.ConditionType.PRICE_ABOVE:
        current_value = _latest_price(rule.asset_id)
        should_trigger = current_value is not None and current_value > threshold
        label = 'price'
    elif rule.condition_type == AlertRule.ConditionType.PRICE_BELOW:
        current_value = _latest_price(rule.asset_id)
        should_trigger = current_value is not None and current_value < threshold
        label = 'price'
    elif rule.condition_type == AlertRule.ConditionType.INDICATOR_ABOVE:
        current_value = _latest_indicator_value(rule.asset_id, rule.indicator_type)
        should_trigger = current_value is not None and current_value > threshold
        label = rule.indicator_type
    elif rule.condition_type == AlertRule.ConditionType.INDICATOR_BELOW:
        current_value = _latest_indicator_value(rule.asset_id, rule.indicator_type)
        should_trigger = current_value is not None and current_value < threshold
        label = rule.indicator_type

    return should_trigger, current_value, label


def _send_sms_via_webhook(payload):
    sms_enabled = getattr(settings, 'ALERTS_ENABLE_SMS', False)
    webhook_url = getattr(settings, 'SMS_WEBHOOK_URL', '')
    if not sms_enabled or not webhook_url:
        return False

    req = urllib_request.Request(
        webhook_url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as response:
            return 200 <= response.status < 300
    except urllib_error.URLError:
        return False


@shared_task
def send_alert_notifications(event_id):
    event = AlertEvent.objects.select_related('alert_rule', 'alert_rule__owner', 'asset').get(id=event_id)
    rule = event.alert_rule
    owner = rule.owner
    channels = rule.channels or []
    sent_channels = []

    if 'email' in channels and owner.email:
        send_mail(
            subject=f'Alert Triggered: {rule.name}',
            message=event.message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[owner.email],
            fail_silently=True,
        )
        sent_channels.append('email')

    if 'sms' in channels:
        sms_ok = _send_sms_via_webhook(
            {
                'user_id': owner.id,
                'asset': event.asset.symbol,
                'alert_name': rule.name,
                'message': event.message,
            }
        )
        if sms_ok:
            sent_channels.append('sms')

    if 'websocket' in channels:
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                f'alerts_user_{owner.id}',
                {
                    'type': 'alert.message',
                    'event_id': event.id,
                    'asset_symbol': event.asset.symbol,
                    'alert_name': rule.name,
                    'message': event.message,
                    'created_at': event.created_at.isoformat(),
                },
            )
            sent_channels.append('websocket')

    event.dispatched_channels = sent_channels
    event.notified_at = timezone.now()
    event.status = AlertEvent.Status.SENT if sent_channels else AlertEvent.Status.FAILED
    event.save(update_fields=['dispatched_channels', 'notified_at', 'status'])


@shared_task
def check_alert_rules():
    rules = AlertRule.objects.filter(is_active=True).select_related('asset', 'owner')
    triggered_count = 0

    for rule in rules:
        if not _is_cooldown_passed(rule):
            continue

        should_trigger, current_value, current_label = _evaluate_alert_rule(rule)
        if not should_trigger:
            continue

        message = (
            f"{rule.name}: {rule.asset.symbol} {current_label} {current_value} "
            f"met condition {rule.condition_type} with threshold {rule.threshold}."
        )
        event = AlertEvent.objects.create(
            alert_rule=rule,
            asset=rule.asset,
            trigger_value=current_value,
            message=message,
            metadata={
                'condition_type': rule.condition_type,
                'indicator_type': rule.indicator_type,
                'threshold': str(rule.threshold),
            },
        )
        rule.last_triggered_at = timezone.now()
        rule.save(update_fields=['last_triggered_at'])
        send_alert_notifications.delay(event.id)
        triggered_count += 1

    return f"Triggered {triggered_count} alert events"
