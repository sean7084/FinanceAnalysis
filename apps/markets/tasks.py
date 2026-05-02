from collections import defaultdict
from celery import chord, shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
import pandas as pd
from decimal import Decimal
from datetime import date, timedelta

import tushare as ts

from apps.analytics.tasks import calculate_signals_for_all_assets
from apps.core.date_floor import get_historical_data_floor
from apps.factors.tasks import calculate_factor_scores_for_date, sync_daily_capital_flow_snapshots
from .benchmarking import refresh_latest_point_in_time_union_benchmark

from .models import Asset, BenchmarkIndexDaily, IndexMembership, OHLCV, Market


DEFAULT_INDEX_CODES = ('000300.SH', '000510.CSI')
INDEX_CODE_SPECS = {
    '000300.SH': {'name': 'CSI 300', 'tag': 'CSI300'},
    '000510.CSI': {'name': 'CSI A500', 'tag': 'CSIA500'},
}
MONTHLY_INDEX_SYNC_LOOKBACK_DAYS = 45
INDEX_CODE_ALIASES = {
    '000300.CSI': '000300.SH',
}
MARKET_SUFFIX_TO_CODE = {
    'SH': 'SSE',
    'SZ': 'SZSE',
    'BJ': 'BSE',
}


def _historical_floor_date():
    return get_historical_data_floor()


def _resolve_target_date(target_date=None):
    if target_date:
        try:
            return date.fromisoformat(str(target_date))
        except ValueError:
            return timezone.now().date()
    return timezone.now().date()


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


def _ensure_default_markets():
    Market.objects.get_or_create(code='SSE', defaults={'name': 'Shanghai Stock Exchange'})
    Market.objects.get_or_create(code='SZSE', defaults={'name': 'Shenzhen Stock Exchange'})
    Market.objects.get_or_create(code='BSE', defaults={'name': 'Beijing Stock Exchange'})


def _normalize_index_code(value):
    token = str(value or '').strip().upper()
    if not token:
        raise ValueError('Index code cannot be empty.')
    token = INDEX_CODE_ALIASES.get(token, token)
    if token not in INDEX_CODE_SPECS:
        raise ValueError(f'Unsupported index code: {token}')
    return token


def _parse_index_codes(raw_value):
    if raw_value in (None, ''):
        values = list(DEFAULT_INDEX_CODES)
    elif isinstance(raw_value, (list, tuple, set)):
        values = list(raw_value)
    else:
        values = [token.strip() for token in str(raw_value).split(',') if token.strip()]

    normalized = []
    for value in values:
        index_code = _normalize_index_code(value)
        if index_code not in normalized:
            normalized.append(index_code)
    return normalized


def _market_code_for_ts_code(ts_code):
    token = str(ts_code or '').strip().upper()
    if '.' not in token:
        return None
    _, suffix = token.split('.', 1)
    return MARKET_SUFFIX_TO_CODE.get(suffix)


def _fetch_stock_basic_map(pro):
    frames = []
    for list_status in ['L', 'D', 'P']:
        frame = pro.stock_basic(
            exchange='',
            list_status=list_status,
            fields='ts_code,symbol,name,list_date,list_status',
        )
        if frame is not None and not frame.empty:
            frames.append(frame)

    if not frames:
        return {}

    basics_df = pd.concat(frames, ignore_index=True)
    basics_df = basics_df.drop_duplicates(subset=['ts_code'], keep='first')
    basic_map = {}
    for _, row in basics_df.iterrows():
        ts_code = str(row['ts_code']).strip().upper()
        basic_map[ts_code] = {
            'symbol': str(row.get('symbol') or ts_code.split('.', 1)[0]),
            'name': str(row.get('name') or ts_code.split('.', 1)[0]),
            'list_date': row.get('list_date'),
            'listing_status': row.get('list_status'),
        }
    return basic_map


def sync_benchmark_index_history(index_codes=None, start_date=None, end_date=None):
    normalized_index_codes = _parse_index_codes(index_codes)
    resolved_end_date = _resolve_target_date(end_date)
    if start_date:
        try:
            resolved_start_date = date.fromisoformat(str(start_date))
        except ValueError:
            resolved_start_date = resolved_end_date - timedelta(days=30)
    else:
        resolved_start_date = resolved_end_date - timedelta(days=30)

    token = getattr(settings, 'TUSHARE_TOKEN', None)
    if not token:
        raise ValueError('TUSHARE_TOKEN is not configured.')

    pro = ts.pro_api(token)
    latest_trade_dates = {}
    rows_written = 0

    start_token = resolved_start_date.strftime('%Y%m%d')
    end_token = resolved_end_date.strftime('%Y%m%d')

    for index_code in normalized_index_codes:
        daily_df = pro.index_daily(ts_code=index_code, start_date=start_token, end_date=end_token)
        if daily_df is None or daily_df.empty:
            latest_trade_dates[index_code] = None
            continue

        daily_df = daily_df.copy()
        daily_df['trade_date'] = daily_df['trade_date'].astype(str)
        daily_df = daily_df.dropna(subset=['trade_date', 'close'])
        latest_trade_dates[index_code] = str(daily_df['trade_date'].max())

        benchmark_rows = []
        for _, row in daily_df.iterrows():
            trade_date = _parse_tushare_date(row.get('trade_date'))
            close_value = _safe_decimal(row.get('close'))
            if trade_date is None or close_value is None:
                continue

            benchmark_rows.append(
                BenchmarkIndexDaily(
                    index_code=index_code,
                    index_name=INDEX_CODE_SPECS[index_code]['name'],
                    trade_date=trade_date,
                    open=_safe_decimal(row.get('open')),
                    high=_safe_decimal(row.get('high')),
                    low=_safe_decimal(row.get('low')),
                    close=close_value,
                    source='tushare_index_daily',
                )
            )

        if not benchmark_rows:
            continue

        existing_count = BenchmarkIndexDaily.objects.filter(
            index_code=index_code,
            trade_date__gte=resolved_start_date,
            trade_date__lte=resolved_end_date,
        ).count()

        BenchmarkIndexDaily.objects.bulk_create(
            benchmark_rows,
            batch_size=2000,
            update_conflicts=True,
            update_fields=['index_name', 'open', 'high', 'low', 'close', 'source'],
            unique_fields=['index_code', 'trade_date'],
        )

        updated_count = BenchmarkIndexDaily.objects.filter(
            index_code=index_code,
            trade_date__gte=resolved_start_date,
            trade_date__lte=resolved_end_date,
        ).count()
        rows_written += max(existing_count, updated_count)

    return {
        'index_codes': normalized_index_codes,
        'start_date': resolved_start_date.isoformat(),
        'end_date': resolved_end_date.isoformat(),
        'latest_trade_dates': latest_trade_dates,
        'rows_written': rows_written,
    }


def _dispatch_asset_history_for_ts_codes(asset_map, ts_codes, force_floor_backfill=False):
    dispatched_assets = 0
    for ts_code in sorted(set(ts_codes)):
        asset = asset_map.get(ts_code)
        market_code = _market_code_for_ts_code(ts_code)
        if asset is None or market_code is None:
            continue
        sync_asset_history.delay(
            asset.symbol,
            asset.name,
            market_code,
            force_floor_backfill,
            asset.list_date,
            asset.listing_status,
        )
        dispatched_assets += 1
    return dispatched_assets


def _build_asset_history_signatures(asset_map, ts_codes, force_floor_backfill=False):
    signatures = []
    for ts_code in sorted(set(ts_codes)):
        asset = asset_map.get(ts_code)
        market_code = _market_code_for_ts_code(ts_code)
        if asset is None or market_code is None:
            continue
        signatures.append(
            sync_asset_history.s(
                asset.symbol,
                asset.name,
                market_code,
                force_floor_backfill,
                asset.list_date.isoformat() if asset.list_date else None,
                asset.listing_status,
            )
        )
    return signatures


def sync_index_constituent_universe(
    index_codes=None,
    start_date=None,
    end_date=None,
    dispatch_assets=True,
    force_floor_backfill=False,
    dispatch_changed_assets_only=False,
):
    _ensure_default_markets()

    normalized_index_codes = _parse_index_codes(index_codes)
    resolved_end_date = end_date or timezone.now().date()
    resolved_start_date = start_date or (resolved_end_date - timedelta(days=30))

    token = getattr(settings, 'TUSHARE_TOKEN', None)
    if not token:
        raise ValueError('TUSHARE_TOKEN is not configured.')

    pro = ts.pro_api(token)
    basic_map = _fetch_stock_basic_map(pro)

    all_ts_codes = set()
    current_tags_by_ts_code = defaultdict(set)
    current_counts = {}
    latest_trade_dates = {}
    membership_snapshots = []

    start_token = resolved_start_date.strftime('%Y%m%d')
    end_token = resolved_end_date.strftime('%Y%m%d')

    for index_code in normalized_index_codes:
        weights_df = pro.index_weight(index_code=index_code, start_date=start_token, end_date=end_token)
        if weights_df is None or weights_df.empty:
            latest_trade_dates[index_code] = None
            current_counts[index_code] = 0
            continue

        weights_df = weights_df.copy()
        weights_df['con_code'] = weights_df['con_code'].astype(str).str.upper()
        weights_df['trade_date'] = weights_df['trade_date'].astype(str)
        weights_df = weights_df.dropna(subset=['con_code', 'trade_date'])

        latest_trade_date = str(weights_df['trade_date'].max())
        latest_trade_dates[index_code] = latest_trade_date

        latest_df = weights_df[weights_df['trade_date'] == latest_trade_date].copy()
        latest_df = latest_df.drop_duplicates(subset=['con_code'])
        current_counts[index_code] = len(latest_df)

        for ts_code in latest_df['con_code'].tolist():
            current_tags_by_ts_code[ts_code].add(INDEX_CODE_SPECS[index_code]['tag'])

        for _, row in weights_df.iterrows():
            ts_code = str(row['con_code']).strip().upper()
            if _market_code_for_ts_code(ts_code) is None:
                continue
            trade_date = _parse_tushare_date(row.get('trade_date'))
            if trade_date is None:
                continue
            all_ts_codes.add(ts_code)
            membership_snapshots.append({
                'ts_code': ts_code,
                'index_code': index_code,
                'index_name': INDEX_CODE_SPECS[index_code]['name'],
                'trade_date': trade_date,
                'weight': _safe_decimal(row.get('weight')),
            })

    if not all_ts_codes:
        return {
            'index_codes': normalized_index_codes,
            'latest_trade_dates': latest_trade_dates,
            'current_constituent_counts': current_counts,
            'overlap_count': 0,
            'current_union_count': 0,
            'new_assets': 0,
            'existing_assets': 0,
            'historical_membership_rows_seen': 0,
            'membership_rows_created': 0,
            'tagged_assets_updated': 0,
            'dispatched_assets': 0,
        }

    existing_assets = Asset.objects.in_bulk(all_ts_codes, field_name='ts_code')
    existing_ts_codes = set(existing_assets.keys())
    markets = {market.code: market for market in Market.objects.filter(code__in=MARKET_SUFFIX_TO_CODE.values())}
    asset_map = {}
    new_assets = 0

    for ts_code in sorted(all_ts_codes):
        market_code = _market_code_for_ts_code(ts_code)
        if market_code is None:
            continue
        market = markets[market_code]
        basic_info = basic_map.get(ts_code, {})
        symbol = str(basic_info.get('symbol') or ts_code.split('.', 1)[0])
        name = str(basic_info.get('name') or symbol)
        list_date = _parse_tushare_date(basic_info.get('list_date'))
        listing_status = _normalize_listing_status(basic_info.get('listing_status'))

        asset = existing_assets.get(ts_code)
        if asset is None:
            asset = Asset.objects.create(
                market=market,
                symbol=symbol,
                ts_code=ts_code,
                name=name,
                list_date=list_date,
                listing_status=listing_status,
            )
            new_assets += 1
        else:
            update_values = {}
            if asset.market_id != market.id:
                update_values['market'] = market
            if asset.symbol != symbol:
                update_values['symbol'] = symbol
            if asset.name != name:
                update_values['name'] = name
            if asset.list_date != list_date:
                update_values['list_date'] = list_date
            if asset.listing_status != listing_status:
                update_values['listing_status'] = listing_status
            if update_values:
                Asset.objects.filter(pk=asset.pk).update(**update_values)
                for field_name, field_value in update_values.items():
                    setattr(asset, field_name, field_value)
        asset_map[ts_code] = asset

    membership_rows_created = 0
    if membership_snapshots:
        before_count = IndexMembership.objects.filter(
            index_code__in=normalized_index_codes,
            trade_date__gte=resolved_start_date,
            trade_date__lte=resolved_end_date,
        ).count()

        membership_rows = []
        for snapshot in membership_snapshots:
            asset = asset_map.get(snapshot['ts_code'])
            if asset is None:
                continue
            membership_rows.append(
                IndexMembership(
                    asset=asset,
                    index_code=snapshot['index_code'],
                    index_name=snapshot['index_name'],
                    trade_date=snapshot['trade_date'],
                    weight=snapshot['weight'],
                    source='tushare_index_weight',
                )
            )

        if membership_rows:
            IndexMembership.objects.bulk_create(
                membership_rows,
                batch_size=2000,
                update_conflicts=True,
                update_fields=['index_name', 'weight', 'source'],
                unique_fields=['asset', 'index_code', 'trade_date'],
            )

        after_count = IndexMembership.objects.filter(
            index_code__in=normalized_index_codes,
            trade_date__gte=resolved_start_date,
            trade_date__lte=resolved_end_date,
        ).count()
        membership_rows_created = max(0, after_count - before_count)

    managed_tags = {INDEX_CODE_SPECS[index_code]['tag'] for index_code in normalized_index_codes}
    affected_assets = Asset.objects.filter(
        Q(ts_code__in=all_ts_codes) | Q(index_memberships__index_code__in=normalized_index_codes)
    ).distinct()

    changed_current_ts_codes = set()
    tagged_assets_updated = 0
    for asset in affected_assets:
        current_tags = set(asset.membership_tags or [])
        current_managed_tags = current_tags.intersection(managed_tags)
        desired_managed_tags = current_tags_by_ts_code.get(asset.ts_code, set())
        desired_tags = sorted((current_tags - managed_tags) | desired_managed_tags)
        if current_managed_tags != desired_managed_tags and desired_managed_tags:
            changed_current_ts_codes.add(asset.ts_code)
        if list(asset.membership_tags or []) != desired_tags:
            Asset.objects.filter(pk=asset.pk).update(membership_tags=desired_tags)
            tagged_assets_updated += 1
            if asset.ts_code in asset_map:
                asset_map[asset.ts_code].membership_tags = desired_tags

    dispatched_assets = 0
    if dispatch_assets:
        dispatch_ts_codes = changed_current_ts_codes if dispatch_changed_assets_only else current_tags_by_ts_code.keys()
        dispatched_assets = _dispatch_asset_history_for_ts_codes(
            asset_map,
            dispatch_ts_codes,
            force_floor_backfill=force_floor_backfill,
        )

    current_union_count = len(current_tags_by_ts_code)
    overlap_count = sum(1 for tags in current_tags_by_ts_code.values() if len(tags) > 1)
    new_current_assets = sum(1 for ts_code in current_tags_by_ts_code if ts_code not in existing_ts_codes)

    return {
        'index_codes': normalized_index_codes,
        'latest_trade_dates': latest_trade_dates,
        'current_constituent_counts': current_counts,
        'current_union_ts_codes': sorted(current_tags_by_ts_code.keys()),
        'overlap_count': overlap_count,
        'current_union_count': current_union_count,
        'new_assets': new_current_assets,
        'existing_assets': max(0, current_union_count - new_current_assets),
        'historical_membership_rows_seen': len(membership_snapshots),
        'membership_rows_created': membership_rows_created,
        'tagged_assets_updated': tagged_assets_updated,
        'dispatched_assets': dispatched_assets,
    }


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
def run_post_sync_universal_refresh(sync_results=None, target_date=None):
    """
    Refresh cross-asset daily metrics after all OHLCV sync fan-out tasks complete.
    """
    as_of = _resolve_target_date(target_date)
    pit_benchmark_result = refresh_latest_point_in_time_union_benchmark(target_date=as_of.isoformat())
    capital_flow_result = sync_daily_capital_flow_snapshots(target_date=as_of.isoformat())
    factor_result = calculate_factor_scores_for_date(target_date=as_of.isoformat())
    signal_result = calculate_signals_for_all_assets()
    synced_assets = len(sync_results or [])
    return (
        f"Post-sync refresh queued for {as_of}: synced_assets={synced_assets}; "
        f"pit_benchmark={pit_benchmark_result}; capital_flow={capital_flow_result}; "
        f"factor_scores={factor_result}; signals={signal_result}"
    )


@shared_task
def sync_daily_a_shares(target_date=None):
    """
    Dispatcher task: Fetches CSI 300 + CSI A500 lists, queues unique OHLCV sync tasks,
    and schedules a post-sync universal metric refresh after the fan-out completes.
    """
    print("Starting CSI 300 + CSI A500 synchronization dispatcher from TuShare...")

    try:
        today = _resolve_target_date(target_date)
        benchmark_summary = sync_benchmark_index_history(
            index_codes=DEFAULT_INDEX_CODES,
            start_date=today - timedelta(days=30),
            end_date=today,
        )
        summary = sync_index_constituent_universe(
            index_codes=DEFAULT_INDEX_CODES,
            start_date=today - timedelta(days=30),
            end_date=today,
            dispatch_assets=False,
            force_floor_backfill=False,
        )
        if summary['current_union_count'] == 0:
            return 'Dispatch failed: no CSI 300 / CSI A500 constituents from TuShare.'

        asset_map = Asset.objects.in_bulk(summary.get('current_union_ts_codes', []), field_name='ts_code')
        signatures = _build_asset_history_signatures(
            asset_map,
            summary.get('current_union_ts_codes', []),
            force_floor_backfill=False,
        )

        if signatures:
            chord(signatures)(run_post_sync_universal_refresh.s(target_date=today.isoformat()))
        else:
            run_post_sync_universal_refresh.delay(target_date=today.isoformat())

        print(
            f"Fetched current constituents: union={summary['current_union_count']} overlap={summary['overlap_count']} "
            f"counts={summary['current_constituent_counts']}"
        )
        print(
            f"Synchronized benchmark series: latest_trade_dates={benchmark_summary['latest_trade_dates']} "
            f"rows_written={benchmark_summary['rows_written']}"
        )
        print(f"Dispatched {len(signatures)} unique sync tasks and queued post-sync refresh.")
        return f"Dispatched {len(signatures)} tasks and queued post-sync refresh at {timezone.now()}"

    except Exception as e:
        print(f"An unexpected error occurred during dispatch: {e}")
        return f"Dispatch failed: {str(e)}"


@shared_task
def sync_monthly_index_memberships():
    """
    Refresh benchmark memberships at month open and enqueue history syncs only for
    assets whose current managed membership changed.
    """
    print("Starting monthly CSI 300 + CSI A500 membership refresh from TuShare...")

    try:
        today = timezone.now().date()
        summary = sync_index_constituent_universe(
            index_codes=DEFAULT_INDEX_CODES,
            start_date=today - timedelta(days=MONTHLY_INDEX_SYNC_LOOKBACK_DAYS),
            end_date=today,
            dispatch_assets=True,
            force_floor_backfill=False,
            dispatch_changed_assets_only=True,
        )
        if summary['current_union_count'] == 0:
            return 'Monthly membership sync failed: no CSI 300 / CSI A500 constituents from TuShare.'

        print(
            f"Refreshed current constituents: union={summary['current_union_count']} overlap={summary['overlap_count']} "
            f"counts={summary['current_constituent_counts']}"
        )
        print(f"Dispatched {summary['dispatched_assets']} membership-change sync tasks.")
        return f"Dispatched {summary['dispatched_assets']} membership-change tasks at {timezone.now()}"

    except Exception as e:
        print(f"An unexpected error occurred during monthly membership refresh: {e}")
        return f"Monthly membership sync failed: {str(e)}"


@shared_task
def sync_official_benchmark_index_history(index_codes=None, start_date=None, end_date=None):
    summary = sync_benchmark_index_history(index_codes=index_codes, start_date=start_date, end_date=end_date)
    return (
        f"Synchronized official benchmark history for {', '.join(summary['index_codes'])}; "
        f"window={summary['start_date']}..{summary['end_date']}; rows_written={summary['rows_written']}"
    )

