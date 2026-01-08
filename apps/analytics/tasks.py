from celery import shared_task
from django.utils import timezone
import pandas as pd
import numpy as np
import talib
from datetime import timedelta

from apps.markets.models import Asset, OHLCV
from .models import TechnicalIndicator

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
def calculate_indicators_for_all_assets():
    """
    Triggers RSI and MACD calculations for all assets.
    """
    asset_ids = Asset.objects.values_list('id', flat=True)
    print(f"Queueing indicator calculations for {len(asset_ids)} assets.")
    for asset_id in asset_ids:
        calculate_rsi_for_asset.delay(asset_id=asset_id)
        calculate_macd_for_asset.delay(asset_id=asset_id)
    
    return f"Successfully queued calculations for {len(asset_ids)} assets."
