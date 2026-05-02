import json

import tushare as ts
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.date_floor import get_historical_data_floor
from apps.macro.models import MacroSnapshot
from apps.macro.providers import fetch_earliest_macro_availability
from apps.markets.models import OHLCV


class Command(BaseCommand):
    help = 'Check earliest available macro snapshot and OHLCV data from sources and local database.'

    def handle(self, *args, **options):
        report = {
            'project_floor': get_historical_data_floor().isoformat(),
            'source_macro': fetch_earliest_macro_availability(
                primary=getattr(settings, 'MACRO_SYNC_PRIMARY_PROVIDER', 'tushare')
            ),
        }

        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if token:
            try:
                pro = ts.pro_api(token)
                stock_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,list_date')
                if stock_basic is not None and not stock_basic.empty:
                    report['source_ohlcv'] = {
                        'stock_basic_active_min_list_date': str(stock_basic['list_date'].astype(str).min()),
                        'stock_basic_active_max_list_date': str(stock_basic['list_date'].astype(str).max()),
                    }
                else:
                    report['source_ohlcv'] = {'error': 'stock_basic returned empty data.'}
            except Exception as exc:
                report['source_ohlcv'] = {'error': str(exc)}
        else:
            report['source_ohlcv'] = {'error': 'TUSHARE_TOKEN is not configured.'}

        report['local_db'] = {
            'macro_snapshot_min_date': str(MacroSnapshot.objects.order_by('date').values_list('date', flat=True).first()),
            'macro_snapshot_max_date': str(MacroSnapshot.objects.order_by('-date').values_list('date', flat=True).first()),
            'ohlcv_min_date': str(OHLCV.objects.order_by('date').values_list('date', flat=True).first()),
            'ohlcv_max_date': str(OHLCV.objects.order_by('-date').values_list('date', flat=True).first()),
        }

        self.stdout.write(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
