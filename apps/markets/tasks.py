from celery import shared_task
from django.conf import settings
from django.utils import timezone
import pandas as pd
from decimal import Decimal
from datetime import date, timedelta

import tushare as ts

from .models import Asset, OHLCV, Market


def _historical_floor_date():
    floor_raw = getattr(settings, 'HISTORICAL_DATA_FLOOR', '2000-01-01')
    try:
        return date.fromisoformat(str(floor_raw))
    except ValueError:
        return date(2000, 1, 1)


def _safe_decimal(value, default=None):
    if pd.isna(value):
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _safe_int(value, default=0):
    if pd.isna(value):
        return default
    try:
        return int(float(value))
    except Exception:
        return default


def _parse_tushare_date(value):
    if value in (None, ''):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    parsed = pd.to_datetime(str(value), format='%Y%m%d', errors='coerce')
    if pd.isna(parsed):
        parsed = pd.to_datetime(str(value), errors='coerce')
    if pd.isna(parsed):
        return None
    return parsed.date()


def _normalize_listing_status(value):
    token = str(value or '').strip().upper()
    if token == 'D':
        return Asset.ListingStatus.DELISTED
    return Asset.ListingStatus.ACTIVE


@shared_task
def sync_asset_history(
    stock_code,
    stock_name,
    market_code,
    force_floor_backfill=False,
    list_date=None,
    listing_status=Asset.ListingStatus.ACTIVE,
):
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
        resolved_list_date = _parse_tushare_date(list_date)
        resolved_listing_status = _normalize_listing_status(listing_status)

        asset, created = Asset.objects.get_or_create(
            ts_code=ts_code,
            defaults={
                'market': market,
                'symbol': stock_code,
                'name': stock_name,
                'listing_status': resolved_listing_status,
                'list_date': resolved_list_date,
            }
        )

        if created:
            print(f"Created new asset: {asset}")
        else:
            update_values = {}
            if asset.listing_status != resolved_listing_status:
                update_values['listing_status'] = resolved_listing_status
            if resolved_list_date is not None and asset.list_date != resolved_list_date:
                update_values['list_date'] = resolved_list_date
            if update_values:
                Asset.objects.filter(pk=asset.pk).update(**update_values)
                for field_name, field_value in update_values.items():
                    setattr(asset, field_name, field_value)

        floor_date = _historical_floor_date()
        latest = OHLCV.objects.filter(asset=asset).order_by('-date').first()
        if latest and not force_floor_backfill:
            start_dt = latest.date + timedelta(days=1)
        else:
            start_dt = floor_date

        start_date = start_dt.strftime('%Y%m%d')
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
            open_value = _safe_decimal(ohlcv_row.get('open'))
            high_value = _safe_decimal(ohlcv_row.get('high'))
            low_value = _safe_decimal(ohlcv_row.get('low'))
            close_value = _safe_decimal(ohlcv_row.get('close'))
            amount_value = _safe_decimal(ohlcv_row.get('amount'), Decimal('0'))
            if None in (open_value, high_value, low_value, close_value):
                continue

            trade_date = pd.to_datetime(str(ohlcv_row['trade_date'])).date()
            rows.append(
                OHLCV(
                    asset=asset,
                    date=trade_date,
                    open=open_value,
                    high=high_value,
                    low=low_value,
                    close=close_value,
                    volume=_safe_int(ohlcv_row.get('vol'), default=0) * 100,
                    adj_close=close_value,
                    amount=amount_value,
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

        basics_df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,list_date,list_status')
        basic_map = {}
        if basics_df is not None and not basics_df.empty:
            for _, row in basics_df.iterrows():
                basic_map[str(row['ts_code'])] = {
                    'name': str(row['name']),
                    'list_date': row.get('list_date'),
                    'listing_status': _normalize_listing_status(row.get('list_status')),
                }

        print(f"Fetched {len(latest_df)} CSI 300 stocks from TuShare ({latest_trade_date}).")

        # 3. Dispatch individual tasks for each stock
        task_count = 0
        for _, row in latest_df.iterrows():
            ts_code = str(row['con_code'])
            if '.' not in ts_code:
                continue
            stock_code, suffix = ts_code.split('.', 1)
            basic_info = basic_map.get(ts_code, {})
            stock_name = basic_info.get('name', stock_code)

            if suffix == 'SH':
                market_code = 'SSE'
            elif suffix == 'SZ':
                market_code = 'SZSE'
            elif suffix == 'BJ':
                market_code = 'BSE'
            else:
                continue

            # Dispatch the task
            sync_asset_history.delay(
                stock_code,
                stock_name,
                market_code,
                False,
                basic_info.get('list_date'),
                basic_info.get('listing_status', Asset.ListingStatus.ACTIVE),
            )
            task_count += 1

        print(f"Dispatched {task_count} sync tasks.")
        return f"Dispatched {task_count} tasks at {timezone.now()}"

    except Exception as e:
        print(f"An unexpected error occurred during dispatch: {e}")
        return f"Dispatch failed: {str(e)}"

