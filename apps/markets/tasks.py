from celery import shared_task
from django.utils import timezone
import akshare as ak
import pandas as pd
from decimal import Decimal

from .models import Asset, OHLCV, Market


@shared_task
def sync_asset_history(stock_code, stock_name, market_code):
    """
    Synchronizes historical data for a single stock.
    This is a worker task that processes one asset at a time.
    """
    try:
        # Get the market
        market = Market.objects.get(code=market_code)
        
        # Determine market suffix
        market_suffix = 'SH' if market_code == 'SSE' else 'SZ' if market_code == 'SZSE' else 'BJ'
        ts_code = f"{stock_code}.{market_suffix}"

        # Get or create the Asset
        asset, created = Asset.objects.get_or_create(
            ts_code=ts_code,
            defaults={
                'market': market,
                'symbol': stock_code,
                'name': stock_name,
            }
        )
        
        if created:
            print(f"Created new asset: {asset}")

        # Fetch daily data
        ohlcv_df = ak.stock_zh_a_hist(symbol=stock_code, adjust="qfq")

        if ohlcv_df.empty:
            print(f"No historical data found for {stock_code}. Skipping.")
            return f"No data for {stock_code}"

        # Prepare and save the data
        ohlcv_count = 0
        for i, ohlcv_row in ohlcv_df.iterrows():
            dt_object = pd.to_datetime(ohlcv_row['日期']).tz_localize('Asia/Shanghai')
            date_only = dt_object.date()

            # Use get_or_create to avoid modeltranslation conflict
            # For initial sync, we only create new records
            obj, ohlcv_created = OHLCV.objects.get_or_create(
                asset=asset,
                date=date_only,
                defaults={
                    'open': Decimal(str(ohlcv_row['开盘'])),
                    'high': Decimal(str(ohlcv_row['最高'])),
                    'low': Decimal(str(ohlcv_row['最低'])),
                    'close': Decimal(str(ohlcv_row['收盘'])),
                    'volume': int(ohlcv_row['成交量']),
                    'adj_close': Decimal(str(ohlcv_row.get('后复权价', ohlcv_row['收盘']))),
                    'amount': Decimal(str(ohlcv_row.get('成交额', 0))),
                }
            )
            
            if ohlcv_created:
                ohlcv_count += 1

        print(f"Processed {stock_code}: Saved {ohlcv_count} new OHLCV records.")
        return f"Completed {stock_code}: {ohlcv_count} records"

    except Exception as e:
        print(f"Error processing {stock_code}: {e}")
        return f"Error for {stock_code}: {str(e)}"


@shared_task
def sync_daily_a_shares():
    """
    Dispatcher task: Fetches CSI 300 list and dispatches individual sync tasks.
    This task completes quickly - the actual work is done by sync_asset_history tasks.
    """
    print("Starting CSI 300 synchronization dispatcher...")

    try:
        # 1. Ensure Markets exist
        Market.objects.get_or_create(code='SSE', defaults={'name': 'Shanghai Stock Exchange'})
        Market.objects.get_or_create(code='SZSE', defaults={'name': 'Shenzhen Stock Exchange'})
        Market.objects.get_or_create(code='BSE', defaults={'name': 'Beijing Stock Exchange'})

        # 2. Fetch CSI 300 constituent stocks
        stock_info_df = ak.index_stock_cons_csindex(symbol="000300")
        print(f"Fetched {len(stock_info_df)} CSI 300 stocks from AkShare.")

        # 3. Dispatch individual tasks for each stock
        task_count = 0
        for index, row in stock_info_df.iterrows():
            stock_code = row['成分券代码']
            stock_name = row['成分券名称']
            
            # Determine market from stock code
            if stock_code.startswith('6'):
                market_code = 'SSE'
            elif stock_code.startswith('0') or stock_code.startswith('3'):
                market_code = 'SZSE'
            elif stock_code.startswith('8') or stock_code.startswith('4') or stock_code.startswith('9'):
                market_code = 'BSE'
            else:
                print(f"Could not determine market for {stock_code}. Skipping.")
                continue

            # Dispatch the task
            sync_asset_history.delay(stock_code, stock_name, market_code)
            task_count += 1

        print(f"Dispatched {task_count} sync tasks.")
        return f"Dispatched {task_count} tasks at {timezone.now()}"

    except Exception as e:
        print(f"An unexpected error occurred during dispatch: {e}")
        return f"Dispatch failed: {str(e)}"

