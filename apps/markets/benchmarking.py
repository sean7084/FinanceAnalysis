from bisect import bisect_right
from datetime import date
from decimal import Decimal

from apps.factors.models import FundamentalFactorSnapshot

from .models import Asset, IndexMembership, OHLCV, PointInTimeBenchmarkDaily


DECIMAL_0 = Decimal('0')
DECIMAL_1 = Decimal('1')
DEFAULT_PIT_INDEX_CODES = ('000300.SH', '000510.CSI')
PIT_UNION_BENCHMARK_CODE = 'CSI300_CSIA500_PIT_UNION'
PIT_UNION_BENCHMARK_NAME = 'CSI300 + CSI A500 PIT Union'
PIT_UNION_WEIGHTING_METHOD = 'free_float_market_cap'
ACTIVE_UNION_TAGS = {'CSI300', 'CSIA500'}


def _to_decimal(value):
    return Decimal(str(value))


def resolve_effective_index_snapshot_dates(trade_date, index_codes=None):
    resolved_index_codes = tuple(index_codes or DEFAULT_PIT_INDEX_CODES)
    snapshot_dates = {}
    for index_code in resolved_index_codes:
        snapshot_dates[index_code] = IndexMembership.objects.filter(
            index_code=index_code,
            trade_date__lte=trade_date,
        ).order_by('-trade_date').values_list('trade_date', flat=True).first()
    return snapshot_dates


def resolve_point_in_time_union_membership(trade_date, index_codes=None):
    resolved_index_codes = tuple(index_codes or DEFAULT_PIT_INDEX_CODES)
    snapshot_dates = resolve_effective_index_snapshot_dates(trade_date, index_codes=resolved_index_codes)

    members_by_asset_id = {}
    for index_code in resolved_index_codes:
        snapshot_date = snapshot_dates.get(index_code)
        if snapshot_date is None:
            continue

        rows = list(
            IndexMembership.objects.filter(index_code=index_code, trade_date=snapshot_date)
            .select_related('asset')
            .order_by('asset__ts_code')
        )
        for row in rows:
            current = members_by_asset_id.setdefault(
                row.asset_id,
                {
                    'asset_id': row.asset_id,
                    'symbol': row.asset.symbol,
                    'ts_code': row.asset.ts_code,
                    'name': row.asset.name,
                    'index_codes': [],
                    'snapshot_dates': {},
                    'membership_weights': {},
                },
            )
            if index_code not in current['index_codes']:
                current['index_codes'].append(index_code)
            current['snapshot_dates'][index_code] = snapshot_date.isoformat()
            if row.weight is not None:
                current['membership_weights'][index_code] = float(row.weight)

    constituents = sorted(members_by_asset_id.values(), key=lambda item: item['ts_code'])
    overlap_count = sum(1 for item in constituents if len(item['index_codes']) > 1)

    return {
        'trade_date': trade_date.isoformat(),
        'index_codes': list(resolved_index_codes),
        'snapshot_dates': {
            index_code: snapshot_dates[index_code].isoformat() if snapshot_dates[index_code] else None
            for index_code in resolved_index_codes
        },
        'asset_ids': [item['asset_id'] for item in constituents],
        'constituent_count': len(constituents),
        'overlap_count': overlap_count,
        'constituents': constituents,
    }


def point_in_time_union_asset_ids(trade_date, index_codes=None):
    return resolve_point_in_time_union_membership(trade_date, index_codes=index_codes)['asset_ids']


def point_in_time_union_asset_ids_by_dates(trade_dates, index_codes=None):
    normalized_dates = []
    for trade_date in trade_dates or []:
        if isinstance(trade_date, str):
            trade_date = date.fromisoformat(trade_date)
        if trade_date not in normalized_dates:
            normalized_dates.append(trade_date)
    normalized_dates.sort()
    if not normalized_dates:
        return {}

    resolved_index_codes = tuple(index_codes or DEFAULT_PIT_INDEX_CODES)
    rows = list(
        IndexMembership.objects.filter(
            index_code__in=resolved_index_codes,
            trade_date__lte=normalized_dates[-1],
        )
        .values('asset_id', 'index_code', 'trade_date')
        .order_by('index_code', 'trade_date', 'asset_id')
    )

    snapshots_by_index = {index_code: {} for index_code in resolved_index_codes}
    for row in rows:
        snapshots_by_index.setdefault(row['index_code'], {}).setdefault(row['trade_date'], set()).add(row['asset_id'])

    snapshot_dates_by_index = {
        index_code: sorted(snapshot_map.keys())
        for index_code, snapshot_map in snapshots_by_index.items()
    }

    memberships_by_date = {}
    for target_date in normalized_dates:
        asset_ids = set()
        for index_code in resolved_index_codes:
            snapshot_dates = snapshot_dates_by_index.get(index_code) or []
            snapshot_index = bisect_right(snapshot_dates, target_date) - 1
            if snapshot_index < 0:
                continue
            snapshot_date = snapshot_dates[snapshot_index]
            asset_ids.update(snapshots_by_index[index_code][snapshot_date])
        memberships_by_date[target_date] = asset_ids

    return memberships_by_date


def current_active_union_assets():
    active_assets = list(Asset.objects.filter(listing_status=Asset.ListingStatus.ACTIVE).order_by('id'))
    tagged_assets = [
        asset
        for asset in active_assets
        if ACTIVE_UNION_TAGS.intersection(set(asset.membership_tags or []))
    ]
    return tagged_assets or active_assets


def _latest_fundamental_rows(asset_ids, trade_date):
    latest_rows = {}
    rows = FundamentalFactorSnapshot.objects.filter(
        asset_id__in=asset_ids,
        date__lte=trade_date,
    ).values('asset_id', 'date', 'free_share', 'circ_mv').order_by('asset_id', '-date')

    for row in rows:
        latest_rows.setdefault(row['asset_id'], row)
    return latest_rows


def _benchmark_weight(row, current_close):
    if row is None or current_close is None or current_close <= 0:
        return None, None

    free_share = row.get('free_share')
    if free_share is not None:
        return _to_decimal(free_share) * current_close, 'free_share_x_close'

    circ_mv = row.get('circ_mv')
    if circ_mv is not None:
        return _to_decimal(circ_mv), 'circ_mv'

    return None, None


def build_point_in_time_union_benchmark_rows(start_date, end_date, initial_nav=Decimal('100000'), index_codes=None):
    trading_dates = list(
        OHLCV.objects.filter(date__gte=start_date, date__lte=end_date)
        .values_list('date', flat=True)
        .distinct()
        .order_by('date')
    )
    if not trading_dates:
        return []

    resolved_index_codes = tuple(index_codes or DEFAULT_PIT_INDEX_CODES)
    rows = []
    nav = _to_decimal(initial_nav)

    for index, trade_date in enumerate(trading_dates):
        membership = resolve_point_in_time_union_membership(trade_date, index_codes=resolved_index_codes)
        asset_ids = membership['asset_ids']
        daily_return = DECIMAL_0
        weighted_constituent_count = 0
        weight_sources = {}
        missing_prices = 0
        missing_market_cap = 0

        if index > 0 and asset_ids:
            previous_trade_date = trading_dates[index - 1]
            current_prices = {
                row['asset_id']: _to_decimal(row['close'])
                for row in OHLCV.objects.filter(asset_id__in=asset_ids, date=trade_date).values('asset_id', 'close')
            }
            previous_prices = {
                row['asset_id']: _to_decimal(row['close'])
                for row in OHLCV.objects.filter(asset_id__in=asset_ids, date=previous_trade_date).values('asset_id', 'close')
            }
            fundamentals = _latest_fundamental_rows(asset_ids, trade_date)

            weighted_sum = DECIMAL_0
            total_weight = DECIMAL_0
            for asset_id in asset_ids:
                current_close = current_prices.get(asset_id)
                previous_close = previous_prices.get(asset_id)
                if current_close is None or previous_close is None or previous_close <= 0:
                    missing_prices += 1
                    continue

                weight, weight_source = _benchmark_weight(fundamentals.get(asset_id), current_close)
                if weight is None or weight <= 0:
                    missing_market_cap += 1
                    continue

                asset_return = (current_close - previous_close) / previous_close
                weighted_sum += weight * asset_return
                total_weight += weight
                weighted_constituent_count += 1
                weight_sources[asset_id] = weight_source

            if total_weight > 0:
                daily_return = weighted_sum / total_weight
                nav *= (DECIMAL_1 + daily_return)
        
        rows.append(
            PointInTimeBenchmarkDaily(
                benchmark_code=PIT_UNION_BENCHMARK_CODE,
                benchmark_name=PIT_UNION_BENCHMARK_NAME,
                trade_date=trade_date,
                daily_return=daily_return,
                nav=nav,
                constituent_count=membership['constituent_count'],
                overlap_count=membership['overlap_count'],
                weighting_method=PIT_UNION_WEIGHTING_METHOD,
                metadata={
                    'index_codes': list(resolved_index_codes),
                    'snapshot_dates': membership['snapshot_dates'],
                    'weighted_constituent_count': weighted_constituent_count,
                    'missing_prices': missing_prices,
                    'missing_market_cap': missing_market_cap,
                    'weight_sources': weight_sources,
                },
            )
        )

    return rows


def refresh_point_in_time_union_benchmark(start_date, end_date, initial_nav=Decimal('100000'), index_codes=None):
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)

    rows = build_point_in_time_union_benchmark_rows(
        start_date=start_date,
        end_date=end_date,
        initial_nav=initial_nav,
        index_codes=index_codes,
    )
    if not rows:
        return {
            'benchmark_code': PIT_UNION_BENCHMARK_CODE,
            'rows_written': 0,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
        }

    PointInTimeBenchmarkDaily.objects.bulk_create(
        rows,
        batch_size=1000,
        update_conflicts=True,
        unique_fields=['benchmark_code', 'trade_date'],
        update_fields=['benchmark_name', 'daily_return', 'nav', 'constituent_count', 'overlap_count', 'weighting_method', 'metadata', 'updated_at'],
    )
    return {
        'benchmark_code': PIT_UNION_BENCHMARK_CODE,
        'rows_written': len(rows),
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'last_trade_date': rows[-1].trade_date.isoformat(),
    }


def refresh_latest_point_in_time_union_benchmark(target_date, initial_nav=Decimal('100000'), index_codes=None):
    if isinstance(target_date, str):
        target_date = date.fromisoformat(target_date)

    previous_row = (
        PointInTimeBenchmarkDaily.objects.filter(
            benchmark_code=PIT_UNION_BENCHMARK_CODE,
            trade_date__lt=target_date,
        )
        .order_by('-trade_date')
        .first()
    )
    if previous_row is None:
        summary = refresh_point_in_time_union_benchmark(
            start_date=target_date,
            end_date=target_date,
            initial_nav=initial_nav,
            index_codes=index_codes,
        )
        summary['refresh_mode'] = 'seed'
        return summary

    summary = refresh_point_in_time_union_benchmark(
        start_date=previous_row.trade_date,
        end_date=target_date,
        initial_nav=previous_row.nav,
        index_codes=index_codes,
    )
    summary['refresh_mode'] = 'incremental'
    summary['seed_trade_date'] = previous_row.trade_date.isoformat()
    summary['seed_nav'] = str(previous_row.nav)
    return summary