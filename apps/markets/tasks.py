import logging
import time
from collections import defaultdict
from celery import chord, shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
import pandas as pd
from decimal import Decimal
from datetime import date, timedelta

import tushare as ts

from apps.analytics.indicator_warmup import technical_indicator_warmup_prefill_start_date
from apps.analytics.tasks import calculate_indicators_for_all_assets, calculate_signals_for_all_assets
from apps.core.date_floor import get_historical_data_floor
from apps.factors.tasks import calculate_factor_scores_for_date, sync_daily_capital_flow_snapshots
from .benchmarking import refresh_latest_point_in_time_union_benchmark

from .models import Asset, AssetSuspension, BenchmarkIndexDaily, ExchangeTradingCalendar, IndexMembership, OHLCV, Market


logger = logging.getLogger(__name__)


DEFAULT_INDEX_CODES = ('000300.SH', '000510.CSI')
INDEX_CODE_SPECS = {
    '000300.SH': {'name': 'CSI 300', 'tag': 'CSI300'},
    '000510.CSI': {'name': 'CSI A500', 'tag': 'CSIA500'},
}
INDEX_WEIGHT_PROVIDER_CODES = {
    '000300.SH': '399300.SZ',
}
MONTHLY_INDEX_SYNC_LOOKBACK_DAYS = 45
DEFAULT_TRADING_CALENDAR_EXCHANGE_CODES = ('SSE', 'SZSE')
TRADE_CAL_SYNC_WINDOW_DAYS = 365
SUSPEND_D_SYNC_WINDOW_DAYS = 60
SUSPEND_D_PAGE_LIMIT = 5000
FULL_DAY_SUSPEND_TYPE = 'S'
INDEX_CODE_ALIASES = {
    '000300.CSI': '000300.SH',
}
MARKET_SUFFIX_TO_CODE = {
    'SH': 'SSE',
    'SZ': 'SZSE',
    'BJ': 'BSE',
}
# TuShare caps index_weight responses at 6000 rows, so keep windows small enough
# to cover daily 300/500-member snapshots without truncating the front of a range.
INDEX_WEIGHT_SYNC_WINDOW_DAYS = 10
INDEX_WEIGHT_REQUEST_SLEEP_SECONDS = 0.4
INDEX_WEIGHT_RETRY_SLEEP_SECONDS = 15.0
INDEX_WEIGHT_MAX_RETRIES = 5


def _historical_floor_date():
    return get_historical_data_floor()


def _resolve_target_date(target_date=None):
    if target_date:
        try:
            return date.fromisoformat(str(target_date))
        except ValueError:
            return timezone.now().date()
    return timezone.now().date()


def _resolve_date_range(start_date=None, end_date=None, default_start_date=None):
    resolved_end_date = _resolve_target_date(end_date)
    if start_date:
        try:
            resolved_start_date = date.fromisoformat(str(start_date))
        except ValueError:
            resolved_start_date = default_start_date or resolved_end_date
    else:
        resolved_start_date = default_start_date or resolved_end_date

    if resolved_start_date > resolved_end_date:
        raise ValueError('start_date must be on or before end_date.')
    return resolved_start_date, resolved_end_date


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


def _parse_exchange_codes(raw_value):
    if raw_value in (None, ''):
        values = list(DEFAULT_TRADING_CALENDAR_EXCHANGE_CODES)
    elif isinstance(raw_value, (list, tuple, set)):
        values = list(raw_value)
    else:
        values = [token.strip() for token in str(raw_value).split(',') if token.strip()]

    normalized = []
    for value in values:
        exchange_code = str(value or '').strip().upper()
        if exchange_code not in DEFAULT_TRADING_CALENDAR_EXCHANGE_CODES:
            raise ValueError(f'Unsupported exchange code: {exchange_code}')
        if exchange_code not in normalized:
            normalized.append(exchange_code)
    return normalized


def _parse_ts_codes(raw_value):
    if raw_value in (None, ''):
        return []
    if isinstance(raw_value, (list, tuple, set)):
        values = raw_value
    else:
        values = [token.strip() for token in str(raw_value).split(',') if token.strip()]

    normalized = []
    for value in values:
        ts_code = str(value or '').strip().upper()
        if ts_code and ts_code not in normalized:
            normalized.append(ts_code)
    return normalized


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


def _provider_index_code_for_membership(index_code):
    return INDEX_WEIGHT_PROVIDER_CODES.get(index_code, index_code)


def _call_tushare_with_retry(fn, label):
    attempts = 0
    while True:
        try:
            result = fn()
            if INDEX_WEIGHT_REQUEST_SLEEP_SECONDS > 0:
                time.sleep(INDEX_WEIGHT_REQUEST_SLEEP_SECONDS)
            return result
        except Exception as exc:
            message = str(exc)
            attempts += 1
            if '频率超限' not in message or attempts >= INDEX_WEIGHT_MAX_RETRIES:
                raise
            logger.warning(
                '%s: TuShare rate limit encountered, sleeping %.0fs before retry %s/%s.',
                label,
                INDEX_WEIGHT_RETRY_SLEEP_SECONDS,
                attempts,
                INDEX_WEIGHT_MAX_RETRIES,
            )
            time.sleep(INDEX_WEIGHT_RETRY_SLEEP_SECONDS)


def _iter_date_windows(start_date, end_date, window_days):
    current_start = start_date
    max_span = max(int(window_days), 1)
    while current_start <= end_date:
        current_end = min(current_start + timedelta(days=max_span - 1), end_date)
        yield current_start, current_end
        current_start = current_end + timedelta(days=1)


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
            fields='ts_code,symbol,name,exchange,list_date,delist_date,list_status',
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
            'exchange': str(row.get('exchange') or ''),
            'list_date': row.get('list_date'),
            'delist_date': row.get('delist_date'),
            'listing_status': row.get('list_status'),
        }
    return basic_map


def sync_exchange_trading_calendar(exchange_codes=None, start_date=None, end_date=None):
    normalized_exchange_codes = _parse_exchange_codes(exchange_codes)
    resolved_start_date, resolved_end_date = _resolve_date_range(start_date=start_date, end_date=end_date)

    token = getattr(settings, 'TUSHARE_TOKEN', None)
    if not token:
        raise ValueError('TUSHARE_TOKEN is not configured.')

    pro = ts.pro_api(token)
    rows_written = 0
    latest_trade_dates = {exchange_code: None for exchange_code in normalized_exchange_codes}

    for exchange_code in normalized_exchange_codes:
        for window_start, window_end in _iter_date_windows(
            resolved_start_date,
            resolved_end_date,
            TRADE_CAL_SYNC_WINDOW_DAYS,
        ):
            calendar_df = _call_tushare_with_retry(
                lambda exchange_code=exchange_code, window_start=window_start, window_end=window_end: pro.trade_cal(
                    exchange=exchange_code,
                    start_date=window_start.strftime('%Y%m%d'),
                    end_date=window_end.strftime('%Y%m%d'),
                    is_open='1',
                ),
                label=f'trade_cal:{exchange_code}:{window_start}:{window_end}',
            )

            ExchangeTradingCalendar.objects.filter(
                exchange_code=exchange_code,
                trade_date__gte=window_start,
                trade_date__lte=window_end,
            ).delete()

            if calendar_df is None or calendar_df.empty:
                continue

            calendar_rows = []
            for _, row in calendar_df.iterrows():
                trade_date = _parse_tushare_date(row.get('cal_date'))
                if trade_date is None:
                    continue
                previous_trade_date = _parse_tushare_date(row.get('pretrade_date'))
                calendar_rows.append(
                    ExchangeTradingCalendar(
                        exchange_code=exchange_code,
                        trade_date=trade_date,
                        previous_trade_date=previous_trade_date,
                        source='tushare_trade_cal',
                    )
                )
                latest_trade_dates[exchange_code] = max(latest_trade_dates[exchange_code] or trade_date, trade_date)

            if not calendar_rows:
                continue

            ExchangeTradingCalendar.objects.bulk_create(calendar_rows, batch_size=2000)
            rows_written += len(calendar_rows)

    return {
        'exchange_codes': normalized_exchange_codes,
        'start_date': resolved_start_date.isoformat(),
        'end_date': resolved_end_date.isoformat(),
        'latest_trade_dates': {
            exchange_code: latest_trade_dates[exchange_code].isoformat() if latest_trade_dates[exchange_code] else None
            for exchange_code in normalized_exchange_codes
        },
        'rows_written': rows_written,
    }


def sync_asset_suspensions(start_date=None, end_date=None, ts_codes=None):
    resolved_start_date, resolved_end_date = _resolve_date_range(start_date=start_date, end_date=end_date)
    normalized_ts_codes = _parse_ts_codes(ts_codes)

    target_assets_qs = Asset.objects.order_by('ts_code')
    if normalized_ts_codes:
        target_assets_qs = target_assets_qs.filter(ts_code__in=normalized_ts_codes)

    target_assets = list(target_assets_qs)
    asset_map = {asset.ts_code.upper(): asset for asset in target_assets}
    asset_ids = [asset.id for asset in target_assets]
    if not asset_ids:
        return {
            'asset_count': 0,
            'start_date': resolved_start_date.isoformat(),
            'end_date': resolved_end_date.isoformat(),
            'rows_written': 0,
            'full_day_rows': 0,
        }

    token = getattr(settings, 'TUSHARE_TOKEN', None)
    if not token:
        raise ValueError('TUSHARE_TOKEN is not configured.')

    pro = ts.pro_api(token)
    rows_written = 0
    full_day_rows = 0

    for window_start, window_end in _iter_date_windows(
        resolved_start_date,
        resolved_end_date,
        SUSPEND_D_SYNC_WINDOW_DAYS,
    ):
        suspension_frames = []
        offset = 0
        while True:
            suspension_df = _call_tushare_with_retry(
                lambda window_start=window_start, window_end=window_end, offset=offset: pro.suspend_d(
                    start_date=window_start.strftime('%Y%m%d'),
                    end_date=window_end.strftime('%Y%m%d'),
                    # Pull all suspend_d rows; timed suspensions also excuse continuity gaps.
                    suspend_type='',
                    offset=offset,
                    limit=SUSPEND_D_PAGE_LIMIT,
                ),
                label=f'suspend_d:{window_start}:{window_end}:offset={offset}',
            )
            if suspension_df is None or suspension_df.empty:
                break
            suspension_frames.append(suspension_df)
            if len(suspension_df) < SUSPEND_D_PAGE_LIMIT:
                break
            offset += SUSPEND_D_PAGE_LIMIT

        AssetSuspension.objects.filter(
            asset_id__in=asset_ids,
            trade_date__gte=window_start,
            trade_date__lte=window_end,
        ).delete()

        if not suspension_frames:
            continue

        suspension_df = pd.concat(suspension_frames, ignore_index=True)

        deduped_suspension_rows = {}
        for _, row in suspension_df.iterrows():
            ts_code = str(row.get('ts_code') or '').strip().upper()
            asset = asset_map.get(ts_code)
            if asset is None:
                continue

            trade_date = _parse_tushare_date(row.get('trade_date'))
            if trade_date is None:
                continue

            suspend_type = str(row.get('suspend_type') or FULL_DAY_SUSPEND_TYPE).strip().upper()
            raw_timing = row.get('suspend_timing')
            suspend_timing = None if pd.isna(raw_timing) else str(raw_timing).strip() or None
            if suspend_timing == 'None':
                suspend_timing = None
            is_full_day = suspend_type == FULL_DAY_SUSPEND_TYPE and suspend_timing is None
            row_key = (asset.id, trade_date)
            row_payload = {
                'asset': asset,
                'trade_date': trade_date,
                'suspend_type': suspend_type,
                'suspend_timing': suspend_timing,
                'is_full_day': is_full_day,
                'source': 'tushare_suspend_d',
            }
            existing_payload = deduped_suspension_rows.get(row_key)
            if existing_payload is None:
                deduped_suspension_rows[row_key] = row_payload
                continue

            if row_payload['is_full_day'] and not existing_payload['is_full_day']:
                deduped_suspension_rows[row_key] = row_payload
                continue
            if existing_payload['is_full_day']:
                continue

            merged_timings = []
            for timing_value in (existing_payload['suspend_timing'], row_payload['suspend_timing']):
                if not timing_value:
                    continue
                for timing_part in timing_value.split(';'):
                    normalized_part = timing_part.strip()
                    if normalized_part and normalized_part not in merged_timings:
                        merged_timings.append(normalized_part)
            deduped_suspension_rows[row_key] = {
                **existing_payload,
                'suspend_timing': '; '.join(merged_timings)[:40] if merged_timings else None,
            }

        if not deduped_suspension_rows:
            continue

        suspension_rows = []
        for row_payload in deduped_suspension_rows.values():
            if row_payload['is_full_day']:
                full_day_rows += 1
            suspension_rows.append(AssetSuspension(**row_payload))

        AssetSuspension.objects.bulk_create(suspension_rows, batch_size=2000)
        rows_written += len(suspension_rows)

    return {
        'asset_count': len(asset_ids),
        'start_date': resolved_start_date.isoformat(),
        'end_date': resolved_end_date.isoformat(),
        'rows_written': rows_written,
        'full_day_rows': full_day_rows,
    }


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
            asset.delist_date,
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
                asset.delist_date.isoformat() if asset.delist_date else None,
            )
        )
    return signatures


def _build_asset_history_warmup_signatures(asset_map, ts_codes, target_date):
    signatures = []
    floor_date = _historical_floor_date()
    resolved_target_date = _resolve_target_date(target_date)
    repair_start_date = technical_indicator_warmup_prefill_start_date(resolved_target_date).isoformat()
    repair_end_date = resolved_target_date.isoformat()

    for ts_code in sorted(set(ts_codes)):
        asset = asset_map.get(ts_code)
        market_code = _market_code_for_ts_code(ts_code)
        if asset is None or market_code is None or asset.list_date is None:
            continue
        if asset.list_date >= floor_date:
            continue
        signatures.append(
            sync_asset_history.s(
                asset.symbol,
                asset.name,
                market_code,
                True,
                asset.list_date.isoformat(),
                asset.listing_status,
                asset.delist_date.isoformat() if asset.delist_date else None,
                repair_start_date=repair_start_date,
                repair_end_date=repair_end_date,
                allow_pre_floor_repair=True,
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

    for index_code in normalized_index_codes:
        provider_index_code = _provider_index_code_for_membership(index_code)
        weight_frames = []
        for window_start, window_end in _iter_date_windows(
            resolved_start_date,
            resolved_end_date,
            INDEX_WEIGHT_SYNC_WINDOW_DAYS,
        ):
            weights_df = _call_tushare_with_retry(
                lambda provider_index_code=provider_index_code, window_start=window_start, window_end=window_end: pro.index_weight(
                    index_code=provider_index_code,
                    start_date=window_start.strftime('%Y%m%d'),
                    end_date=window_end.strftime('%Y%m%d'),
                ),
                label=f'index_weight:{provider_index_code}:{window_start}:{window_end}',
            )
            if weights_df is None or weights_df.empty:
                continue
            weight_frames.append(weights_df.copy())

        if not weight_frames:
            latest_trade_dates[index_code] = None
            current_counts[index_code] = 0
            continue

        weights_df = pd.concat(weight_frames, ignore_index=True)
        weights_df['con_code'] = weights_df['con_code'].astype(str).str.upper()
        weights_df['trade_date'] = weights_df['trade_date'].astype(str)
        weights_df = weights_df.dropna(subset=['con_code', 'trade_date'])
        weights_df = weights_df.drop_duplicates(subset=['trade_date', 'con_code'])

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
        delist_date = _parse_tushare_date(basic_info.get('delist_date'))
        listing_status = _normalize_listing_status(basic_info.get('listing_status'))

        asset = existing_assets.get(ts_code)
        if asset is None:
            asset = Asset.objects.create(
                market=market,
                symbol=symbol,
                ts_code=ts_code,
                name=name,
                list_date=list_date,
                delist_date=delist_date,
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
            if asset.delist_date != delist_date:
                update_values['delist_date'] = delist_date
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
    new_current_union_ts_codes = set()
    tagged_assets_updated = 0
    for asset in affected_assets:
        current_tags = set(asset.membership_tags or [])
        current_managed_tags = current_tags.intersection(managed_tags)
        desired_managed_tags = current_tags_by_ts_code.get(asset.ts_code, set())
        desired_tags = sorted((current_tags - managed_tags) | desired_managed_tags)
        if current_managed_tags != desired_managed_tags and desired_managed_tags:
            changed_current_ts_codes.add(asset.ts_code)
            if not current_managed_tags:
                new_current_union_ts_codes.add(asset.ts_code)
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
        'new_current_union_ts_codes': sorted(new_current_union_ts_codes),
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
    delist_date=None,
    repair_start_date=None,
    repair_end_date=None,
    allow_pre_floor_repair=False,
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
        resolved_delist_date = _parse_tushare_date(delist_date)
        resolved_listing_status = _normalize_listing_status(listing_status)

        asset, created = Asset.objects.get_or_create(
            ts_code=ts_code,
            defaults={
                'market': market,
                'symbol': stock_code,
                'name': stock_name,
                'listing_status': resolved_listing_status,
                'list_date': resolved_list_date,
                'delist_date': resolved_delist_date,
            }
        )

        if created:
            print(f"Created new asset: {asset}")
        else:
            update_values = {}
            if asset.listing_status != resolved_listing_status:
                update_values['listing_status'] = resolved_listing_status
            if asset.list_date != resolved_list_date:
                update_values['list_date'] = resolved_list_date
            if asset.delist_date != resolved_delist_date:
                update_values['delist_date'] = resolved_delist_date
            if update_values:
                Asset.objects.filter(pk=asset.pk).update(**update_values)
                for field_name, field_value in update_values.items():
                    setattr(asset, field_name, field_value)

        floor_date = _historical_floor_date()
        requested_start_dt = _parse_tushare_date(repair_start_date)
        requested_end_dt = _parse_tushare_date(repair_end_date)
        if requested_start_dt and requested_start_dt < floor_date and not allow_pre_floor_repair:
            requested_start_dt = floor_date

        latest = OHLCV.objects.filter(asset=asset).order_by('-date').first()
        if requested_start_dt is not None:
            start_dt = requested_start_dt
        elif latest and not force_floor_backfill:
            start_dt = latest.date + timedelta(days=1)
        else:
            start_dt = floor_date

        end_dt = requested_end_dt or timezone.now().date()
        if asset.delist_date and asset.delist_date < end_dt:
            end_dt = asset.delist_date

        if end_dt < start_dt:
            return f"No sync window for {ts_code}: start={start_dt} end={end_dt}"

        start_date = start_dt.strftime('%Y%m%d')
        end_date = end_dt.strftime('%Y%m%d')

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
    technical_indicator_result = calculate_indicators_for_all_assets()
    signal_result = calculate_signals_for_all_assets()
    synced_assets = len(sync_results or [])
    return (
        f"Post-sync refresh queued for {as_of}: synced_assets={synced_assets}; "
        f"pit_benchmark={pit_benchmark_result}; capital_flow={capital_flow_result}; "
        f"factor_scores={factor_result}; technical_indicators={technical_indicator_result}; signals={signal_result}"
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
        calendar_summary = sync_exchange_trading_calendar(
            exchange_codes=DEFAULT_TRADING_CALENDAR_EXCHANGE_CODES,
            start_date=today,
            end_date=today,
        )
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

        suspension_summary = sync_asset_suspensions(
            start_date=today,
            end_date=today,
            ts_codes=summary.get('current_union_ts_codes', []),
        )

        asset_map = Asset.objects.in_bulk(summary.get('current_union_ts_codes', []), field_name='ts_code')
        signatures = _build_asset_history_signatures(
            asset_map,
            summary.get('current_union_ts_codes', []),
            force_floor_backfill=False,
        )
        signatures.extend(
            _build_asset_history_warmup_signatures(
                asset_map,
                summary.get('new_current_union_ts_codes', []),
                today,
            )
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
        print(
            f"Synchronized official calendars: latest_trade_dates={calendar_summary['latest_trade_dates']} "
            f"rows_written={calendar_summary['rows_written']}"
        )
        print(
            f"Synchronized suspension days: rows_written={suspension_summary['rows_written']} "
            f"full_day_rows={suspension_summary['full_day_rows']}"
        )
        print(f"Dispatched {len(signatures)} unique sync tasks and queued post-sync refresh.")
        return (
            f"Dispatched {len(signatures)} tasks and queued post-sync refresh at {timezone.now()}; "
            f"calendar_rows={calendar_summary['rows_written']}; suspension_rows={suspension_summary['rows_written']}"
        )

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

