import pandas as pd
import tushare as ts
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from apps.markets.models import Asset


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


class Command(BaseCommand):
    help = 'Backfill Asset.list_date and listing_status from TuShare stock_basic.'

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
            frame = pro.stock_basic(exchange='', list_status=list_status, fields='ts_code,list_date,list_status')
            if frame is not None and not frame.empty:
                frames.append(frame)

        if not frames:
            raise CommandError('stock_basic returned empty data for all requested statuses.')

        basics_df = pd.concat(frames, ignore_index=True)
        basics_df = basics_df.drop_duplicates(subset=['ts_code'], keep='first')
        basics = {
            str(row['ts_code']): {
                'list_date': _parse_date(row.get('list_date')),
                'listing_status': _normalize_listing_status(row.get('list_status')),
            }
            for row in basics_df.to_dict(orient='records')
        }

        processed = 0
        updated = 0
        missing = 0
        for asset in queryset:
            processed += 1
            payload = basics.get(asset.ts_code)
            if payload is None:
                missing += 1
                continue

            update_values = {}
            if asset.list_date != payload['list_date']:
                update_values['list_date'] = payload['list_date']
            if asset.listing_status != payload['listing_status']:
                update_values['listing_status'] = payload['listing_status']
            if update_values:
                Asset.objects.filter(pk=asset.pk).update(**update_values)
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Asset list-date backfill complete: processed={processed}, updated={updated}, missing={missing}'
        ))