"""Record the rate series this token may actually read.

The entitlement probe (2026-10-27) found ``yc_cb`` and ``monetary_policy`` denied but
``shibor`` available. This command writes SHIBOR fixings into each snapshot's
``metadata['shibor']`` rather than inventing yield columns: the bond-curve source is still
unavailable, and a sparse rate series in metadata is honest about that.
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.core import tushare_client
from apps.macro.models import MacroSnapshot


class Command(BaseCommand):
    help = 'Fetch SHIBOR fixings and store them on the matching monthly snapshots.'

    def add_arguments(self, parser):
        parser.add_argument('--start', default=None, help='YYYYMMDD start (default: newest snapshot month).')
        parser.add_argument('--end', default=None, help='YYYYMMDD end (default: today).')

    def handle(self, *args, **options):
        newest = MacroSnapshot.objects.order_by('-date').values_list('date', flat=True).first()
        start = options['start'] or (newest.strftime('%Y%m%d') if newest else None)
        end = options['end'] or date.today().strftime('%Y%m%d')
        if not start:
            raise CommandError('no snapshot exists and no --start was given.')
        frame = tushare_client.call_with_retry(
            lambda: tushare_client.pro().shibor(start_date=start, end_date=end),
            'shibor',
        )
        if frame is None or frame.empty:
            self.stdout.write('no SHIBOR rows returned; nothing written.')
            return
        updated = 0
        for row in frame.to_dict('records'):
            trade_date = row.get('date')
            if not trade_date:
                continue
            month = date(int(str(trade_date)[:4]), int(str(trade_date)[4:6]), 1)
            snapshot = MacroSnapshot.objects.filter(date=month).first()
            if snapshot is None:
                continue
            metadata = dict(snapshot.metadata or {})
            metadata['shibor'] = {
                'date': str(trade_date),
                'on': row.get('on'), '1w': row.get('1w'), '1m': row.get('1m'),
                '3m': row.get('3m'), '1y': row.get('1y'),
                'source': 'tushare:shibor',
            }
            snapshot.metadata = metadata
            snapshot.save(update_fields=['metadata'])
            updated += 1
        self.stdout.write(f'SHIBOR fixings written onto {updated} snapshot(s); yc_cb remains denied.')
