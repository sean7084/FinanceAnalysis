from celery import shared_task
from django.utils import timezone
import akshare as ak
import pandas as pd
from decimal import Decimal

from .models import Asset, OHLCV


@shared_task
def sync_daily_a_shares():
    """
    Synchronizes daily A-share data from AkShare to the database.
    """
    print("Starting daily A-share synchronization...")

    try:
        # 1. Fetch the list of all A-shares
        stock_info_df = ak.stock_info_a_code_name()
        print(f"Fetched {len(stock_info_df)} stocks from AkShare.")

        # 2. Iterate through each stock and fetch its daily OHLCV data
        for index, row in stock_info_df.iterrows():
            stock_code = row['code']
            stock_name = row['name']
            print(f"Processing {stock_code} - {stock_name}...")

            try:
                # Get or create the Asset
                asset, created = Asset.objects.get_or_create(
                    symbol=stock_code,
                    defaults={'name': stock_name, 'asset_type': 'stock'}
                )
                if created:
                    print(f"Created new asset: {asset}")

                # Fetch daily data
                # AkShare's 'stock_zh_a_hist' returns data for the last ~1 year by default
                ohlcv_df = ak.stock_zh_a_hist(symbol=stock_code, adjust="qfq") # qfq: forward-adjusted prices

                if ohlcv_df.empty:
                    print(f"No historical data found for {stock_code}. Skipping.")
                    continue

                # 3. Prepare and save the data
                ohlcv_records = []
                for i, ohlcv_row in ohlcv_df.iterrows():
                    # Convert date string to datetime object
                    dt_object = pd.to_datetime(ohlcv_row['日期']).tz_localize('Asia/Shanghai')

                    # Use update_or_create to ensure idempotency
                    obj, created = OHLCV.objects.update_or_create(
                        asset=asset,
                        timestamp=dt_object,
                        defaults={
                            'open': Decimal(str(ohlcv_row['开盘'])),
                            'high': Decimal(str(ohlcv_row['最高'])),
                            'low': Decimal(str(ohlcv_row['最低'])),
                            'close': Decimal(str(ohlcv_row['收盘'])),
                            'volume': int(ohlcv_row['成交量']),
                        }
                    )
                    if created:
                        ohlcv_records.append(obj)

                if ohlcv_records:
                    print(f"Saved {len(ohlcv_records)} new OHLCV records for {stock_code}.")

            except Exception as e:
                print(f"Error processing {stock_code}: {e}")
                continue # Continue to the next stock

    except Exception as e:
        print(f"An unexpected error occurred during the sync process: {e}")

    print("Daily A-share synchronization finished.")
    return f"Completed sync at {timezone.now()}. Processed {len(stock_info_df)} stocks."
