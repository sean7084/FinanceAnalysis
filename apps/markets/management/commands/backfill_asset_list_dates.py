import pandas as pd
import tushare as ts
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from apps.markets.models import Asset


AKSHARE_CURRENT_LISTING_SPECS = (
    ('stock_info_sh_name_code', '证券代码', '上市日期', 'SH'),
    ('stock_info_sz_name_code', 'A股代码', 'A股上市日期', 'SZ'),
    ('stock_info_bj_name_code', '证券代码', '上市日期', 'BJ'),
)


def _parse_date(value):
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


def _get_akshare_module():
    try:
        import akshare as ak
    except ImportError:
        return None
    return ak


def _normalize_security_code(value):
    token = str(value or '').strip()
    if not token:
        return ''
    return token.zfill(6) if token.isdigit() else token


def _fetch_akshare_current_listing_map():
    ak = _get_akshare_module()
    if ak is None:
        return {}

    listing_map = {}
    for function_name, code_field, list_date_field, suffix in AKSHARE_CURRENT_LISTING_SPECS:
        function = getattr(ak, function_name, None)
        if function is None:
            continue
        try:
            frame = function()
        except Exception:
            continue
        if frame is None or frame.empty:
            continue
        for row in frame.to_dict(orient='records'):
            code = _normalize_security_code(row.get(code_field))
            if not code:
                continue
            listing_map[f'{code}.{suffix}'] = {
                'list_date': _parse_date(row.get(list_date_field)),
                'delist_date': None,
                'listing_status': Asset.ListingStatus.ACTIVE,
            }
    return listing_map


class Command(BaseCommand):
    help = 'Backfill Asset.list_date, delist_date, and listing_status from TuShare stock_basic with AkShare current-listing fallback.'

    def add_arguments(self, parser):
        parser.add_argument('--symbols', default='')
        parser.add_argument('--limit-assets', type=int, default=0)

    def handle(self, *args, **options):
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is not configured.')

        symbols = [token.strip() for token in str(options['symbols'] or '').split(',') if token.strip()]
        queryset = Asset.objects.order_by('ts_code')
        if symbols:
            queryset = queryset.filter(symbol__in=symbols)

        limit_assets = int(options['limit_assets'] or 0)
        if limit_assets > 0:
            queryset = queryset[:limit_assets]

        pro = ts.pro_api(token)
        frames = []
        for list_status in ['L', 'D', 'P']:
            frame = pro.stock_basic(exchange='', list_status=list_status, fields='ts_code,list_date,delist_date,list_status')
            if frame is not None and not frame.empty:
                frames.append(frame)

        tushare_basics = {}
        if frames:
            basics_df = pd.concat(frames, ignore_index=True)
            basics_df = basics_df.drop_duplicates(subset=['ts_code'], keep='first')
            tushare_basics = {
                str(row['ts_code']).strip().upper(): {
                    'list_date': _parse_date(row.get('list_date')),
                    'delist_date': _parse_date(row.get('delist_date')),
                    'listing_status': _normalize_listing_status(row.get('list_status')),
                }
                for row in basics_df.to_dict(orient='records')
            }

        akshare_basics = _fetch_akshare_current_listing_map()
        basics = dict(tushare_basics)
        for ts_code, payload in akshare_basics.items():
            basics.setdefault(ts_code, payload)

        if not basics:
            raise CommandError('stock_basic returned empty data for all requested statuses, and AkShare fallback returned no current listings.')

        akshare_fallback_hits = 0

        processed = 0
        updated = 0
        missing = 0
        for asset in queryset:
            processed += 1
            ts_code = str(asset.ts_code).strip().upper()
            payload = basics.get(ts_code)
            if payload is None:
                missing += 1
                continue
            if ts_code not in tushare_basics and ts_code in akshare_basics:
                akshare_fallback_hits += 1

            update_values = {}
            if asset.list_date != payload['list_date']:
                update_values['list_date'] = payload['list_date']
            if asset.delist_date != payload['delist_date']:
                update_values['delist_date'] = payload['delist_date']
            if asset.listing_status != payload['listing_status']:
                update_values['listing_status'] = payload['listing_status']
            if update_values:
                Asset.objects.filter(pk=asset.pk).update(**update_values)
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Asset list-date backfill complete: processed={processed}, updated={updated}, missing={missing}, akshare_fallback_hits={akshare_fallback_hits}'
        ))