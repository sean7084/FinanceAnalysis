from datetime import date

import pandas as pd
import tushare as ts
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.macro.models import MacroSnapshot
from apps.macro.providers import fetch_macro_snapshot_from_akshare
from apps.macro.tasks import refresh_current_market_context


def _parse_date(value, name):
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc


def _month_start_iter(start_date, end_date):
    cursor = start_date.replace(day=1)
    end_month = end_date.replace(day=1)
    while cursor <= end_month:
        yield cursor
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)


def _to_decimal(value):
    if value in (None, '', 'nan'):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return None


class Command(BaseCommand):
    help = 'Backfill MacroSnapshot monthly data from TuShare with AkShare fallback.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=getattr(settings, 'HISTORICAL_DATA_FLOOR', '2000-01-01'))
        parser.add_argument('--end-date', default=date.today().isoformat())
        parser.add_argument('--disable-fallback', action='store_true')

    def _fetch_tushare_monthly_maps(self, start_date, end_date):
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is not configured.')

        pro = ts.pro_api(token)
        start_month = start_date.strftime('%Y%m')
        end_month = end_date.strftime('%Y%m')

        maps = {
            'cpi': {},
            'ppi': {},
            'pmi': {},
            'yield_2y': {},
            'yield_10y': {},
            'errors': {},
        }

        try:
            cpi_df = pro.cn_cpi(limit=5000)
            if cpi_df is not None and not cpi_df.empty:
                for row in cpi_df.to_dict(orient='records'):
                    month = str(row.get('month') or '')
                    if start_month <= month <= end_month:
                        maps['cpi'][month] = _to_decimal(row.get('nt_yoy'))
        except Exception as exc:
            maps['errors']['cpi'] = str(exc)

        try:
            ppi_df = pro.cn_ppi(limit=5000)
            if ppi_df is not None and not ppi_df.empty:
                for row in ppi_df.to_dict(orient='records'):
                    month = str(row.get('month') or '')
                    if start_month <= month <= end_month:
                        maps['ppi'][month] = _to_decimal(row.get('ppi_yoy'))
        except Exception as exc:
            maps['errors']['ppi'] = str(exc)

        try:
            pmi_df = pro.cn_pmi(limit=5000)
            if pmi_df is not None and not pmi_df.empty:
                for row in pmi_df.to_dict(orient='records'):
                    month = str(row.get('month') or '')
                    if not month and row.get('CREATE_TIME'):
                        parsed = pd.to_datetime(str(row.get('CREATE_TIME')), errors='coerce')
                        if pd.notna(parsed):
                            month = parsed.strftime('%Y%m')
                    if start_month <= month <= end_month:
                        maps['pmi'][month] = {
                            'pmi_manufacturing': _to_decimal(row.get('PMI010000')),
                            'pmi_non_manufacturing': _to_decimal(row.get('PMI030000')),
                        }
        except Exception as exc:
            maps['errors']['pmi'] = str(exc)

        for term, key in [(2, 'yield_2y'), (10, 'yield_10y')]:
            try:
                y_df = pro.yc_cb(
                    curve_term=term,
                    start_date=start_date.strftime('%Y%m%d'),
                    end_date=end_date.strftime('%Y%m%d'),
                    limit=20000,
                )
                if y_df is not None and not y_df.empty:
                    y_sorted = y_df.sort_values('trade_date')
                    for row in y_sorted.to_dict(orient='records'):
                        month = str(row.get('trade_date') or '')[:6]
                        if start_month <= month <= end_month:
                            maps[key][month] = _to_decimal(row.get('yield'))
            except Exception as exc:
                maps['errors'][key] = str(exc)

        return maps

    def handle(self, *args, **options):
        floor_raw = getattr(settings, 'HISTORICAL_DATA_FLOOR', '2000-01-01')
        floor_date = _parse_date(floor_raw, 'HISTORICAL_DATA_FLOOR')
        start_date = max(_parse_date(options['start_date'], 'start-date'), floor_date)
        end_date = _parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        maps = self._fetch_tushare_monthly_maps(start_date, end_date)

        created_count = 0
        updated_count = 0
        fallback_count = 0

        for month_date in _month_start_iter(start_date, end_date):
            month = month_date.strftime('%Y%m')
            defaults = {
                'dxy': None,
                'cny_usd': None,
                'cn10y_yield': maps['yield_10y'].get(month),
                'cn2y_yield': maps['yield_2y'].get(month),
                'pmi_manufacturing': (maps['pmi'].get(month) or {}).get('pmi_manufacturing'),
                'pmi_non_manufacturing': (maps['pmi'].get(month) or {}).get('pmi_non_manufacturing'),
                'cpi_yoy': maps['cpi'].get(month),
                'ppi_yoy': maps['ppi'].get(month),
                'metadata': {
                    'source': 'tushare',
                    'month': month,
                    'errors': maps['errors'],
                },
            }

            has_critical = any(defaults.get(field) is not None for field in ['pmi_manufacturing', 'pmi_non_manufacturing', 'cn10y_yield', 'cn2y_yield', 'cpi_yoy'])
            if not has_critical and not options['disable_fallback']:
                fallback_payload = fetch_macro_snapshot_from_akshare(snapshot_date=month_date)
                fallback_count += 1
                for field in ['dxy', 'cny_usd', 'cn10y_yield', 'cn2y_yield', 'pmi_manufacturing', 'pmi_non_manufacturing', 'cpi_yoy', 'ppi_yoy']:
                    if defaults.get(field) is None and fallback_payload.get(field) is not None:
                        defaults[field] = fallback_payload[field]
                defaults['metadata']['fallback_source'] = 'akshare'
                defaults['metadata']['fallback_payload'] = fallback_payload.get('metadata', {})

            obj, created = MacroSnapshot.objects.update_or_create(date=month_date, defaults=defaults)
            if created:
                created_count += 1
            else:
                updated_count += 1

        latest = MacroSnapshot.objects.order_by('-date').first()
        if latest:
            refresh_current_market_context(snapshot_id=latest.id)

        self.stdout.write(self.style.SUCCESS(
            f'MacroSnapshot backfill completed: created={created_count}, updated={updated_count}, fallback_used={fallback_count}, range={start_date}..{end_date}'
        ))
