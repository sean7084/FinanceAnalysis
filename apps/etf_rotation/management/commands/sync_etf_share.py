"""Backfill ``ETFShareDaily`` for the ETFs that can reach the eligible pool.

``fund_share`` is queried per fund over a date range, so the cost is one call per ETF. Only
funds the snapshot table has ever marked eligible are synced: pulling all 2,200 listed ETFs
would spend the rate limit on funds the strategy can never hold.
"""
from __future__ import annotations

import time

import tushare as ts
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.etf_rotation.models import ETFShareDaily, ETFFeatureSnapshot, ETFUniverse


class Command(BaseCommand):
    help = 'Sync daily ETF shares outstanding for funds that have been eligible.'

    def add_arguments(self, parser):
        parser.add_argument('--start', default='20160101', help='Start date YYYYMMDD.')
        parser.add_argument('--end', default='', help='End date YYYYMMDD (default today).')
        parser.add_argument('--limit', type=int, default=0, help='Only the first N funds.')
        parser.add_argument('--pause', type=float, default=1.1, help='Seconds between calls.')
        parser.add_argument('--only-missing', action='store_true',
                            help='Skip funds that already have share rows.')

    def handle(self, *args, **options):
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is not configured.')
        pro = ts.pro_api(token)

        eligible_ids = set(
            ETFFeatureSnapshot.objects.filter(is_eligible=True)
            .values_list('etf_id', flat=True).distinct()
        )
        pairs = list(
            ETFUniverse.objects.filter(id__in=eligible_ids).values_list('id', 'ts_code')
        )
        if options['only_missing']:
            have = set(ETFShareDaily.objects.values_list('etf_id', flat=True).distinct())
            pairs = [pair for pair in pairs if pair[0] not in have]
            self.stdout.write(f'{len(pairs)} fund(s) still missing share history')
        if options['limit']:
            pairs = pairs[:options['limit']]
        if not pairs:
            raise CommandError('no eligible ETF to sync; build the feature snapshots first.')
        self.stdout.write(f'etf share: {len(pairs)} fund(s)')

        end = options['end'] or time.strftime('%Y%m%d')
        written = failed = 0
        for position, (etf_id, ts_code) in enumerate(pairs, start=1):
            frame = None
            for attempt in range(3):
                try:
                    frame = pro.fund_share(
                        ts_code=ts_code, start_date=options['start'], end_date=end,
                    )
                except Exception as exc:  # network, throttle, unknown code
                    self.stderr.write(f'  {ts_code}: {type(exc).__name__} {str(exc)[:80]}')
                    frame = None
                if frame is not None and not frame.empty:
                    break
                time.sleep(max(3.0, options['pause'] * (attempt + 2)))
            if frame is None or frame.empty:
                failed += 1
                time.sleep(options['pause'])
                continue
            payload = [
                ETFShareDaily(etf_id=etf_id, date=_as_date(row.trade_date),
                              shares=_float(getattr(row, 'fd_share', None)))
                for row in frame.itertuples(index=False)
            ]
            with transaction.atomic():
                ETFShareDaily.objects.bulk_create(
                    payload, batch_size=2000, update_conflicts=True,
                    unique_fields=['etf', 'date'], update_fields=['shares'],
                )
            written += len(payload)
            if position % 50 == 0:
                self.stdout.write(f'  {position}/{len(pairs)} funds, {written} rows')
            time.sleep(options['pause'])
        self.stdout.write(self.style.SUCCESS(
            f'synced {written} row(s) for {len(pairs) - failed} fund(s)'
        ))
        if failed:
            self.stdout.write(self.style.WARNING(f'{failed} fund(s) failed'))


def _as_date(value):
    text = str(value).replace('-', '')
    return f'{text[0:4]}-{text[4:6]}-{text[6:8]}'


def _float(value):
    try:
        if value is None or value == '':
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
