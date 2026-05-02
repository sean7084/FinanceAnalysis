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

from apps.markets.benchmarking import point_in_time_union_asset_ids
from apps.markets.models import Asset, OHLCV
from .models import TechnicalIndicator, AlertRule, AlertEvent, SignalEvent

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
def calculate_fibonacci_retracement_for_asset(asset_id, lookback_days=60):
    """
    Calculates Fibonacci retracement levels using the recent high/low range.
    """
    if lookback_days <= 1:
        print(f"Invalid lookback_days={lookback_days} for Fibonacci calculation.")
        return

    candles = list(
        OHLCV.objects.filter(asset_id=asset_id).order_by('-date')[:lookback_days]
    )
    if len(candles) < 2:
        print(f"Not enough data for Fibonacci calculation for asset {asset_id}.")
        return

    asset = Asset.objects.get(id=asset_id)
    period_high = max(float(c.high) for c in candles)
    period_low = min(float(c.low) for c in candles)
    price_range = period_high - period_low
    if price_range <= 0:
        print(f"Invalid price range for Fibonacci calculation for asset {asset_id}.")
        return

    latest = candles[0]
    latest_date = latest.date
    from django.utils import timezone as tz
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))

    levels = {
        '0.236': period_high - price_range * 0.236,
        '0.382': period_high - price_range * 0.382,
        '0.500': period_high - price_range * 0.500,
        '0.618': period_high - price_range * 0.618,
        '0.786': period_high - price_range * 0.786,
    }

    params = {
        'lookback_days': lookback_days,
        'high': period_high,
        'low': period_low,
        'range': price_range,
        'levels': levels,
        'close': float(latest.close),
    }

    obj, created = TechnicalIndicator.objects.get_or_create(
        asset=asset,
        timestamp=latest_timestamp,
        indicator_type='FIB_RET',
        parameters={'lookback_days': lookback_days},
        defaults={
            'value': levels['0.500'],
            'parameters': params,
        }
    )
    if not created:
        obj.value = levels['0.500']
        obj.parameters = params
        obj.save()

    action = "Created" if created else "Updated"
    print(f"{action} Fibonacci retracement for {asset.symbol} on {latest_date}")


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
        calculate_fibonacci_retracement_for_asset.delay(asset_id=asset_id)
    
    return f"Successfully queued calculations for {len(asset_ids)} assets."


# ---------------------------------------------------------------------------
# Phase 10: Signal Event helpers and tasks
# ---------------------------------------------------------------------------

def _save_signal_event(asset, signal_type, timestamp, description, metadata):
    """Create or update a SignalEvent record (idempotent on unique_together)."""
    obj, created = SignalEvent.objects.get_or_create(
        asset=asset,
        timestamp=timestamp,
        signal_type=signal_type,
        defaults={'description': description, 'metadata': metadata},
    )
    return obj, created


def persist_ranked_rs_scores(ranked_scores, timestamp, update_existing=False):
    """Persist RS_SCORE indicators and HIGH_RS_SCORE events from a descending ranking."""
    total = len(ranked_scores)
    if total == 0:
        return {
            'indicator_rows': 0,
            'signal_rows': 0,
            'top_cutoff': 0,
            'total_ranked_assets': 0,
        }

    top_cutoff = max(1, int(total * 0.2))
    asset_map = Asset.objects.in_bulk([int(asset_id) for asset_id, _ in ranked_scores])

    if update_existing:
        indicator_rows = 0
        signal_rows = 0
        for rank, (asset_id, momentum_20d) in enumerate(ranked_scores, start=1):
            asset = asset_map.get(int(asset_id))
            if asset is None:
                continue

            rs_score = Decimal(str(1.0 - ((rank - 1) / total)))
            obj, created = TechnicalIndicator.objects.get_or_create(
                asset=asset,
                timestamp=timestamp,
                indicator_type='RS_SCORE',
                parameters={},
                defaults={'value': rs_score},
            )
            if not created:
                obj.value = rs_score
                obj.save(update_fields=['value'])
            indicator_rows += 1

            if rank <= top_cutoff:
                _, signal_created = _save_signal_event(
                    asset,
                    'HIGH_RS_SCORE',
                    timestamp,
                    f'Top 20% RS: rank #{rank}/{total}, 20d return={momentum_20d:.1%}, score={float(rs_score):.3f}',
                    {
                        'rank': rank,
                        'total': total,
                        'momentum_20d': momentum_20d,
                        'rs_score': float(rs_score),
                    },
                )
                signal_rows += int(signal_created)

        return {
            'indicator_rows': indicator_rows,
            'signal_rows': signal_rows,
            'top_cutoff': top_cutoff,
            'total_ranked_assets': total,
        }

    indicators = []
    signals = []
    for rank, (asset_id, momentum_20d) in enumerate(ranked_scores, start=1):
        asset_id = int(asset_id)
        if asset_id not in asset_map:
            continue

        rs_score = Decimal(str(1.0 - ((rank - 1) / total)))
        indicators.append(
            TechnicalIndicator(
                asset_id=asset_id,
                timestamp=timestamp,
                indicator_type='RS_SCORE',
                value=rs_score,
                parameters={},
            )
        )

        if rank <= top_cutoff:
            signals.append(
                SignalEvent(
                    asset_id=asset_id,
                    signal_type='HIGH_RS_SCORE',
                    timestamp=timestamp,
                    description=(
                        f'Top 20% RS: rank #{rank}/{total}, '
                        f'20d return={momentum_20d:.1%}, score={float(rs_score):.3f}'
                    ),
                    metadata={
                        'rank': rank,
                        'total': total,
                        'momentum_20d': momentum_20d,
                        'rs_score': float(rs_score),
                    },
                )
            )

    if indicators:
        TechnicalIndicator.objects.bulk_create(indicators, batch_size=5000, ignore_conflicts=True)
    if signals:
        SignalEvent.objects.bulk_create(signals, batch_size=5000, ignore_conflicts=True)

    return {
        'indicator_rows': len(indicators),
        'signal_rows': len(signals),
        'top_cutoff': top_cutoff,
        'total_ranked_assets': total,
    }


@shared_task
def calculate_ma_signals_for_asset(asset_id):
    """
    Computes MA5/10/20/60 and detects golden cross, death cross, and alignment signals.
    """
    df = get_ohlcv_df(asset_id, days_history=200)
    if df.empty or len(df) < 62:
        return

    asset = Asset.objects.get(id=asset_id)
    from django.utils import timezone as tz

    ma5 = talib.SMA(df['close'], timeperiod=5)
    ma10 = talib.SMA(df['close'], timeperiod=10)
    ma20 = talib.SMA(df['close'], timeperiod=20)
    ma60 = talib.SMA(df['close'], timeperiod=60)

    if pd.isna(ma5.iloc[-1]) or pd.isna(ma20.iloc[-1]):
        return
    if pd.isna(ma5.iloc[-2]) or pd.isna(ma20.iloc[-2]):
        return

    today_ma5 = float(ma5.iloc[-1])
    today_ma20 = float(ma20.iloc[-1])
    prev_ma5 = float(ma5.iloc[-2])
    prev_ma20 = float(ma20.iloc[-2])
    today_ma10 = float(ma10.iloc[-1]) if not pd.isna(ma10.iloc[-1]) else None
    today_ma60 = float(ma60.iloc[-1]) if not pd.isna(ma60.iloc[-1]) else None

    latest_date = df.index[-1]
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))
    close_today = float(df['close'].iloc[-1])

    # Golden cross: MA5 crosses above MA20
    if prev_ma5 <= prev_ma20 and today_ma5 > today_ma20:
        _save_signal_event(
            asset, 'GOLDEN_CROSS', latest_timestamp,
            f'Golden Cross: MA5={today_ma5:.2f} crossed above MA20={today_ma20:.2f}',
            {'ma5': today_ma5, 'ma20': today_ma20, 'close': close_today},
        )

    # Death cross: MA5 crosses below MA20
    if prev_ma5 >= prev_ma20 and today_ma5 < today_ma20:
        _save_signal_event(
            asset, 'DEATH_CROSS', latest_timestamp,
            f'Death Cross: MA5={today_ma5:.2f} crossed below MA20={today_ma20:.2f}',
            {'ma5': today_ma5, 'ma20': today_ma20, 'close': close_today},
        )

    # Bull / bear MA alignment
    if today_ma10 and today_ma60:
        if today_ma5 > today_ma10 > today_ma20 > today_ma60:
            _save_signal_event(
                asset, 'MA_BULL_ALIGN', latest_timestamp,
                (f'Bull MA Alignment: MA5={today_ma5:.2f} > MA10={today_ma10:.2f} '
                 f'> MA20={today_ma20:.2f} > MA60={today_ma60:.2f}'),
                {'ma5': today_ma5, 'ma10': today_ma10, 'ma20': today_ma20, 'ma60': today_ma60},
            )
        elif today_ma5 < today_ma10 < today_ma20 < today_ma60:
            _save_signal_event(
                asset, 'MA_BEAR_ALIGN', latest_timestamp,
                (f'Bear MA Alignment: MA5={today_ma5:.2f} < MA10={today_ma10:.2f} '
                 f'< MA20={today_ma20:.2f} < MA60={today_ma60:.2f}'),
                {'ma5': today_ma5, 'ma10': today_ma10, 'ma20': today_ma20, 'ma60': today_ma60},
            )

    print(f"MA signals calculated for {asset.symbol}")


@shared_task
def calculate_bollinger_signals_for_asset(asset_id, timeperiod=20, nbdevup=2, nbdevdn=2):
    """
    Detects Bollinger Band squeeze, price breakouts, and combined RSI+BB overbought/oversold.
    """
    df = get_ohlcv_df(asset_id, days_history=100)
    if df.empty or len(df) < timeperiod + 14:
        return

    asset = Asset.objects.get(id=asset_id)
    from django.utils import timezone as tz

    upper, middle, lower = talib.BBANDS(
        df['close'], timeperiod=timeperiod, nbdevup=nbdevup, nbdevdn=nbdevdn
    )
    rsi = talib.RSI(df['close'], timeperiod=14)

    latest_date = df.index[-1]
    if pd.isna(upper[latest_date]) or pd.isna(lower[latest_date]):
        return

    close = float(df['close'][latest_date])
    u = float(upper[latest_date])
    m = float(middle[latest_date])
    l = float(lower[latest_date])
    bandwidth = (u - l) / m if m > 0 else 0
    rsi_val = float(rsi[latest_date]) if not pd.isna(rsi[latest_date]) else None
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))

    if bandwidth < 0.05:
        _save_signal_event(
            asset, 'BB_SQUEEZE', latest_timestamp,
            f'Bollinger Band Squeeze: bandwidth={bandwidth:.4f} (< 5%)',
            {'upper': u, 'middle': m, 'lower': l, 'bandwidth': bandwidth, 'close': close},
        )

    if close > u:
        _save_signal_event(
            asset, 'BB_BREAKOUT_UP', latest_timestamp,
            f'Price breakout above upper band: close={close:.2f} > upper={u:.2f}',
            {'upper': u, 'middle': m, 'lower': l, 'close': close},
        )

    if close < l:
        _save_signal_event(
            asset, 'BB_BREAKOUT_DOWN', latest_timestamp,
            f'Price breakout below lower band: close={close:.2f} < lower={l:.2f}',
            {'upper': u, 'middle': m, 'lower': l, 'close': close},
        )

    if rsi_val is not None and close >= u * 0.98 and rsi_val > 70:
        _save_signal_event(
            asset, 'BB_RSI_OVERBOUGHT', latest_timestamp,
            f'Overbought: close={close:.2f} near upper={u:.2f}, RSI={rsi_val:.1f}',
            {'upper': u, 'lower': l, 'close': close, 'rsi': rsi_val},
        )

    if rsi_val is not None and close <= l * 1.02 and rsi_val < 30:
        _save_signal_event(
            asset, 'BB_RSI_OVERSOLD', latest_timestamp,
            f'Oversold: close={close:.2f} near lower={l:.2f}, RSI={rsi_val:.1f}',
            {'upper': u, 'lower': l, 'close': close, 'rsi': rsi_val},
        )

    print(f"Bollinger Band signals calculated for {asset.symbol}")


@shared_task
def calculate_volume_signals_for_asset(asset_id, avg_period=20, spike_multiplier=2.0):
    """
    Detects volume spikes and volume-price divergence signals using OBV.
    """
    df = get_ohlcv_df(asset_id, days_history=60)
    if df.empty or len(df) < avg_period + 2:
        return

    asset = Asset.objects.get(id=asset_id)
    from django.utils import timezone as tz

    avg_volume = df['volume'].iloc[-(avg_period + 1):-1].mean()
    latest_volume = float(df['volume'].iloc[-1])
    latest_close = float(df['close'].iloc[-1])
    latest_date = df.index[-1]
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))

    if avg_volume > 0:
        volume_ratio = latest_volume / avg_volume
        if volume_ratio >= spike_multiplier:
            _save_signal_event(
                asset, 'VOLUME_SPIKE', latest_timestamp,
                f'Volume spike: {volume_ratio:.1f}x average ({latest_volume:.0f} vs avg {avg_volume:.0f})',
                {'volume': latest_volume, 'avg_volume': avg_volume, 'ratio': volume_ratio, 'close': latest_close},
            )

    # Volume-price divergence via 5-day OBV trend vs price trend
    if len(df) >= 7:
        obv = talib.OBV(df['close'], df['volume'])
        obv_5d_ago = float(obv.iloc[-6])
        obv_now = float(obv.iloc[-1])
        close_5d_ago = float(df['close'].iloc[-6])
        price_5d_return = (latest_close - close_5d_ago) / close_5d_ago if close_5d_ago > 0 else 0
        obv_change = obv_now - obv_5d_ago

        if price_5d_return >= 0.03 and obv_change < 0:
            _save_signal_event(
                asset, 'VOLUME_PRICE_DIVERGENCE', latest_timestamp,
                f'Bearish divergence: price +{price_5d_return:.1%} over 5d but OBV declining',
                {'price_5d_return': price_5d_return, 'obv_change': obv_change, 'type': 'bearish'},
            )
        elif price_5d_return <= -0.03 and obv_change > 0:
            _save_signal_event(
                asset, 'VOLUME_PRICE_DIVERGENCE', latest_timestamp,
                f'Bullish divergence: price {price_5d_return:.1%} over 5d but OBV rising',
                {'price_5d_return': price_5d_return, 'obv_change': obv_change, 'type': 'bullish'},
            )

    print(f"Volume signals calculated for {asset.symbol}")


@shared_task
def calculate_momentum_signals_for_asset(asset_id):
    """
    Calculates 5/10/20-day momentum as TechnicalIndicator values.
    Flags MOMENTUM_UP_5D / MOMENTUM_DOWN_5D when 5-day move exceeds ±5%.
    """
    df = get_ohlcv_df(asset_id, days_history=60)
    if df.empty or len(df) < 22:
        return

    asset = Asset.objects.get(id=asset_id)
    from django.utils import timezone as tz

    latest_close = float(df['close'].iloc[-1])
    latest_date = df.index[-1]
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))

    for n_days in [5, 10, 20]:
        if len(df) <= n_days:
            continue
        past_close = float(df['close'].iloc[-(n_days + 1)])
        if past_close <= 0:
            continue
        momentum = (latest_close - past_close) / past_close

        obj, created = TechnicalIndicator.objects.get_or_create(
            asset=asset,
            timestamp=latest_timestamp,
            indicator_type=f'MOM_{n_days}D',
            parameters={'n_days': n_days},
            defaults={'value': momentum},
        )
        if not created:
            obj.value = momentum
            obj.save()

    # Signal flags for 5-day momentum
    past_close_5d = float(df['close'].iloc[-6])
    if past_close_5d > 0:
        momentum_5d = (latest_close - past_close_5d) / past_close_5d
        if momentum_5d >= 0.05:
            _save_signal_event(
                asset, 'MOMENTUM_UP_5D', latest_timestamp,
                f'Strong upward 5-day momentum: +{momentum_5d:.1%}',
                {'momentum': momentum_5d, 'close': latest_close, 'close_5d_ago': past_close_5d},
            )
        elif momentum_5d <= -0.05:
            _save_signal_event(
                asset, 'MOMENTUM_DOWN_5D', latest_timestamp,
                f'Strong downward 5-day momentum: {momentum_5d:.1%}',
                {'momentum': momentum_5d, 'close': latest_close, 'close_5d_ago': past_close_5d},
            )

    print(f"Momentum signals calculated for {asset.symbol}")


@shared_task
def calculate_reversal_signals_for_asset(asset_id, timeperiod_bb=20, timeperiod_rsi=14):
    """
    Detects oversold reversal combinations: RSI < 30 AND price near lower BB AND low volume.
    """
    df = get_ohlcv_df(asset_id, days_history=100)
    if df.empty or len(df) < max(timeperiod_bb, timeperiod_rsi) + 5:
        return

    asset = Asset.objects.get(id=asset_id)
    from django.utils import timezone as tz

    upper, middle, lower = talib.BBANDS(df['close'], timeperiod=timeperiod_bb)
    rsi = talib.RSI(df['close'], timeperiod=timeperiod_rsi)

    latest_date = df.index[-1]
    if pd.isna(lower[latest_date]) or pd.isna(rsi[latest_date]):
        return

    close = float(df['close'][latest_date])
    l = float(lower[latest_date])
    rsi_val = float(rsi[latest_date])
    avg_volume = df['volume'].iloc[-21:-1].mean()
    latest_volume = float(df['volume'].iloc[-1])
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(latest_date, pd.Timestamp.min.time()))

    near_lower = close <= l * 1.02
    volume_contraction = avg_volume > 0 and (latest_volume < avg_volume * 0.8)

    if rsi_val < 30 and near_lower and volume_contraction:
        _save_signal_event(
            asset, 'OVERSOLD_COMBINATION', latest_timestamp,
            (f'Oversold combination: RSI={rsi_val:.1f} < 30, '
             f'close={close:.2f} near lower BB={l:.2f}, '
             f'volume={latest_volume:.0f} < 80% avg={avg_volume:.0f}'),
            {'rsi': rsi_val, 'close': close, 'lower_bb': l,
             'volume': latest_volume, 'avg_volume': avg_volume},
        )

    print(f"Reversal signals calculated for {asset.symbol}")


@shared_task
def calculate_rs_scores_for_all_assets():
    """
    Cross-asset task: ranks all assets by 20-day return and assigns RS_SCORE.
    Top 20% also receive a HIGH_RS_SCORE SignalEvent.
    """
    from django.utils import timezone as tz

    today = timezone.now().date()
    union_asset_ids = point_in_time_union_asset_ids(today)
    if union_asset_ids:
        asset_ids = list(union_asset_ids)
    else:
        asset_ids = list(Asset.objects.values_list('id', flat=True))
    if not asset_ids:
        return

    scores = []

    for asset_id in asset_ids:
        candles = list(OHLCV.objects.filter(asset_id=asset_id).order_by('-date')[:21])
        if len(candles) < 21:
            continue
        close_today = float(candles[0].close)
        close_20d = float(candles[20].close)
        if close_20d <= 0:
            continue
        momentum_20d = (close_today - close_20d) / close_20d
        scores.append((asset_id, momentum_20d, close_today))

    if not scores:
        return

    scores.sort(key=lambda x: x[1], reverse=True)
    latest_timestamp = tz.make_aware(pd.Timestamp.combine(today, pd.Timestamp.min.time()))
    result = persist_ranked_rs_scores(
        [(asset_id, momentum_20d) for asset_id, momentum_20d, _close in scores],
        latest_timestamp,
        update_existing=True,
    )

    print(
        f"RS Scores: {result['top_cutoff']} top assets flagged out of {result['total_ranked_assets']}"
    )


@shared_task
def calculate_signals_for_all_assets():
    """
    Phase 10 dispatcher: queues all signal calculation tasks for every asset.
    """
    asset_ids = list(Asset.objects.values_list('id', flat=True))
    for asset_id in asset_ids:
        calculate_ma_signals_for_asset.delay(asset_id)
        calculate_bollinger_signals_for_asset.delay(asset_id)
        calculate_volume_signals_for_asset.delay(asset_id)
        calculate_momentum_signals_for_asset.delay(asset_id)
        calculate_reversal_signals_for_asset.delay(asset_id)
    calculate_rs_scores_for_all_assets.delay()
    return f"Queued signal calculations for {len(asset_ids)} assets"


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
