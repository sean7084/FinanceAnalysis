from celery import shared_task
from django.conf import settings
from django.utils import timezone
import pandas as pd
from decimal import Decimal
from datetime import timedelta

import tushare as ts

from .models import Asset, OHLCV, Market


@shared_task
def sync_asset_history(stock_code, stock_name, market_code):
    """
    Synchronizes historical data for a single stock.
    This is a worker task that processes one asset at a time.
    """
    try:
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            return 'TUSHARE_TOKEN is not configured.'

        pro = ts.pro_api(token)

        market = Market.objects.get(code=market_code)

        market_suffix = 'SH' if market_code == 'SSE' else 'SZ' if market_code == 'SZSE' else 'BJ'
        ts_code = f"{stock_code}.{market_suffix}"

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

        latest = OHLCV.objects.filter(asset=asset).order_by('-date').first()
        if latest:
            start_date = (latest.date + timedelta(days=1)).strftime('%Y%m%d')
        else:
            start_date = (timezone.now().date() - timedelta(days=365 * 10)).strftime('%Y%m%d')
        end_date = timezone.now().date().strftime('%Y%m%d')

        ohlcv_df = ts.pro_bar(
            ts_code=ts_code,
            adj='qfq',
            start_date=start_date,
            end_date=end_date,
            api=pro,
        )

        if ohlcv_df is None or ohlcv_df.empty:
            print(f"No historical data found for {ts_code}. Skipping.")
            return f"No data for {ts_code}"

        rows = []
        for _, ohlcv_row in ohlcv_df.iterrows():
            trade_date = pd.to_datetime(str(ohlcv_row['trade_date'])).date()
            rows.append(
                OHLCV(
                    asset=asset,
                    date=trade_date,
                    open=Decimal(str(ohlcv_row['open'])),
                    high=Decimal(str(ohlcv_row['high'])),
                    low=Decimal(str(ohlcv_row['low'])),
                    close=Decimal(str(ohlcv_row['close'])),
                    volume=int(float(ohlcv_row['vol']) * 100),
                    adj_close=Decimal(str(ohlcv_row['close'])),
                    amount=Decimal(str(ohlcv_row['amount'])),
                )
            )

        before = OHLCV.objects.filter(asset=asset).count()
        OHLCV.objects.bulk_create(rows, batch_size=2000, ignore_conflicts=True)
        after = OHLCV.objects.filter(asset=asset).count()
        ohlcv_count = max(0, after - before)

        print(f"Processed {ts_code}: Saved {ohlcv_count} new OHLCV records.")
        return f"Completed {ts_code}: {ohlcv_count} records"

    except Exception as e:
        print(f"Error processing {stock_code}: {e}")
        return f"Error for {stock_code}: {str(e)}"


@shared_task
def sync_daily_a_shares():
    """
    Dispatcher task: Fetches CSI 300 list and dispatches individual sync tasks.
    This task completes quickly - the actual work is done by sync_asset_history tasks.
    """
    print("Starting CSI 300 synchronization dispatcher from TuShare...")

    try:
        # 1. Ensure Markets exist
        Market.objects.get_or_create(code='SSE', defaults={'name': 'Shanghai Stock Exchange'})
        Market.objects.get_or_create(code='SZSE', defaults={'name': 'Shenzhen Stock Exchange'})
        Market.objects.get_or_create(code='BSE', defaults={'name': 'Beijing Stock Exchange'})

        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            return 'Dispatch failed: TUSHARE_TOKEN is not configured.'

        pro = ts.pro_api(token)

        # 2. Fetch CSI 300 constituents from TuShare index weights (latest rebalance snapshot)
        today = timezone.now().date()
        start = (today - timedelta(days=30)).strftime('%Y%m%d')
        end = today.strftime('%Y%m%d')
        weights_df = pro.index_weight(index_code='000300.SH', start_date=start, end_date=end)

        if weights_df is None or weights_df.empty:
            return 'Dispatch failed: no CSI300 constituents from TuShare.'

        latest_trade_date = str(weights_df['trade_date'].max())
        latest_df = weights_df[weights_df['trade_date'] == latest_trade_date].copy()
        latest_df = latest_df.drop_duplicates(subset=['con_code'])

        basics_df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name')
        name_map = {}
        if basics_df is not None and not basics_df.empty:
            for _, row in basics_df.iterrows():
                name_map[str(row['ts_code'])] = str(row['name'])

        print(f"Fetched {len(latest_df)} CSI 300 stocks from TuShare ({latest_trade_date}).")

        # 3. Dispatch individual tasks for each stock
        task_count = 0
        for _, row in latest_df.iterrows():
            ts_code = str(row['con_code'])
            if '.' not in ts_code:
                continue
            stock_code, suffix = ts_code.split('.', 1)
            stock_name = name_map.get(ts_code, stock_code)

            if suffix == 'SH':
                market_code = 'SSE'
            elif suffix == 'SZ':
                market_code = 'SZSE'
            elif suffix == 'BJ':
                market_code = 'BSE'
            else:
                continue

            # Dispatch the task
            sync_asset_history.delay(stock_code, stock_name, market_code)
            task_count += 1

        print(f"Dispatched {task_count} sync tasks.")
        return f"Dispatched {task_count} tasks at {timezone.now()}"

    except Exception as e:
        print(f"An unexpected error occurred during dispatch: {e}")
        return f"Dispatch failed: {str(e)}"

