"""As-of ST history derived from namechange records (B2 / STK-12, plan D5).

namechange records CHANGES, not states: a stock already ST before its first
record never appears. ``reconcile_st_history`` bounds that blind spot against
current stock_basic names; the ST layer stays disabled until the reconciliation
is archived (card B3). The derivation never reads listing_status / delist_date:
delisted non-ST names stay in the historical universe (survivorship discipline).
"""
from bisect import bisect_right
from datetime import datetime

from django.db import transaction

from apps.markets.models import AssetNameChange, AssetSTInterval
from apps.markets.price_limits import is_st_name

#: change_reason values that denote a special-treatment / delisting-consolidation
#: state. '从ST变为*ST' and '撤消*ST并实行ST' REMAIN ST (the name still carries it).
ST_CHANGE_REASONS = frozenset({'ST', '*ST', '从ST变为*ST', '撤消*ST并实行ST', '退市整理期'})


def is_st_record(name, change_reason):
    """Whether one namechange row denotes an ST-state interval start/continuation."""
    reason = (change_reason or '').strip()
    if reason in ST_CHANGE_REASONS:
        return True
    text = (name or '').strip()
    if is_st_name(text):
        return True
    return text.endswith('退')


def derive_st_intervals(records):
    """Merge per-asset namechange rows (sorted by start_date) into ST intervals.

    Consecutive ST-state rows merge into one interval; a non-ST row closes the
    open interval at its start_date (the new name takes effect then); a trailing
    ST row with no successor stays open-ended. Returns interval dicts ready for
    AssetSTInterval(**interval).
    """
    intervals = []
    open_interval = None
    for record in records:
        if is_st_record(record['name'], record['change_reason']):
            if open_interval is None:
                open_interval = {
                    'start_date': record['start_date'],
                    'end_date': None,
                    'ann_date': record.get('ann_date'),
                    'change_reason': record.get('change_reason') or '',
                }
        elif open_interval is not None:
            open_interval['end_date'] = record['start_date']
            intervals.append(open_interval)
            open_interval = None
    if open_interval is not None:
        intervals.append(open_interval)
    return intervals


def rebuild_asset_st_intervals(asset_id):
    """Recompute one asset's intervals from its raw records (transactional caller)."""
    records = list(
        AssetNameChange.objects.filter(asset_id=asset_id)
        .order_by('start_date', 'id')
        .values('start_date', 'end_date', 'name', 'change_reason', 'ann_date')
    )
    intervals = derive_st_intervals(records)
    AssetSTInterval.objects.filter(asset_id=asset_id).delete()
    AssetSTInterval.objects.bulk_create([
        AssetSTInterval(asset_id=asset_id, **interval) for interval in intervals
    ])
    return len(intervals)


def st_asset_ids_as_of(trade_date):
    """Asset ids ST as-of ``trade_date``: start_date <= date < end_date (or open)."""
    rows = AssetSTInterval.objects.filter(start_date__lte=trade_date).values_list('asset_id', 'end_date')
    return {asset_id for asset_id, end_date in rows if end_date is None or trade_date < end_date}


def st_asset_ids_by_dates(trade_dates):
    """Batch resolution: load intervals once, bisect per date (mirrors membership)."""
    dates = sorted(set(trade_dates))
    if not dates:
        return {}
    rows = list(
        AssetSTInterval.objects.filter(start_date__lte=dates[-1])
        .values_list('asset_id', 'start_date', 'end_date')
        .order_by('asset_id', 'start_date')
    )
    by_asset = {}
    for asset_id, start_date, end_date in rows:
        by_asset.setdefault(asset_id, []).append((start_date, end_date))
    result = {d: set() for d in dates}
    for asset_id, intervals in by_asset.items():
        starts = [start for start, _ in intervals]
        for d in dates:
            idx = bisect_right(starts, d) - 1
            if idx < 0:
                continue
            _, end_date = intervals[idx]
            if end_date is None or d < end_date:
                result[d].add(asset_id)
    return result


def _parse_day(value):
    return datetime.strptime(str(value), '%Y%m%d').date()


def ingest_namechange_records(records, asset_ids_by_ts_code):
    """Store gated namechange records; return (stored, quarantined, touched_assets).

    The look-ahead gate: ann_date > start_date rows are quarantined, NEVER stored.
    Unknown ts_codes are skipped (not in the asset universe). Shared by the sync
    command and the daily dispatcher refresh.
    """
    stored = 0
    quarantined = []
    touched = set()
    for record in records:
        ts_code = str(record.get('ts_code') or '').strip().upper()
        asset_id = asset_ids_by_ts_code.get(ts_code)
        if asset_id is None:
            continue
        try:
            row_start = _parse_day(record['start_date'])
        except (KeyError, TypeError, ValueError):
            quarantined.append({**record, 'quarantine_reason': 'missing/invalid start_date'})
            continue
        ann_raw = record.get('ann_date')
        ann = None
        if ann_raw not in (None, '', 'None'):
            try:
                ann = _parse_day(ann_raw)
            except (TypeError, ValueError):
                ann = None
        if ann is not None and ann > row_start:
            quarantined.append({**record, 'quarantine_reason': 'ann_date > start_date (look-ahead)'})
            continue
        end_raw = record.get('end_date')
        row_end = None
        if end_raw not in (None, '', 'None'):
            try:
                row_end = _parse_day(end_raw)
            except (TypeError, ValueError):
                row_end = None
        with transaction.atomic():
            AssetNameChange.objects.update_or_create(
                asset_id=asset_id,
                start_date=row_start,
                name=str(record.get('name') or '').strip(),
                defaults={
                    'end_date': row_end,
                    'ann_date': ann,
                    'change_reason': str(record.get('change_reason') or '').strip(),
                    'source': 'tushare_namechange',
                },
            )
        stored += 1
        touched.add(asset_id)
    return stored, quarantined, touched