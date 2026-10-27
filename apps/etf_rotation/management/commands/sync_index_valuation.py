"""Backfill ``IndexValuationDaily`` for the indices the ETF pool actually tracks.

One call per index covers its whole history, so the cost is the number of *distinct* tracked
indices (a few hundred), not the number of ETFs. Only mapped indices are synced: pulling the
whole index universe would spend the rate limit on codes no ETF references.
"""
from __future__ import annotations

import time

import tushare as ts
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.etf_rotation.models import ETFIndexMapping, IndexValuationDaily

NUMERIC = ('pe_ttm', 'pb', 'turnover_rate', 'total_mv', 'float_mv')


class Command(BaseCommand):
    help = 'Sync index valuation history for every index mapped from an ETF.'

    def add_arguments(self, parser):
        parser.add_argument('--start', default='20100101', help='Start date YYYYMMDD.')
        parser.add_argument('--end', default='', help='End date YYYYMMDD (default today).')
        parser.add_argument('--limit', type=int, default=0, help='Only the first N indices.')
        parser.add_argument('--pause', type=float, default=1.2,
                            help='Seconds between calls (default 1.2; the free tier throttles '
                                 'above roughly one call per second and then returns empty '
                                 'frames instead of an error).')
        parser.add_argument('--only-missing', action='store_true',
                            help='Skip index codes that already have valuation rows.')

    def handle(self, *args, **options):
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is not configured.')
        pro = ts.pro_api(token)

        codes = list(
            ETFIndexMapping.objects.order_by('index_code')
            .values_list('index_code', flat=True).distinct()
        )
        if options['only_missing']:
            have = set(IndexValuationDaily.objects.values_list('index_code', flat=True).distinct())
            codes = [code for code in codes if code not in have]
            self.stdout.write(f'{len(codes)} index code(s) still missing valuation')
        if options['limit']:
            codes = codes[:options['limit']]
        if not codes:
            raise CommandError(
                'no ETF -> index mapping yet; run build_etf_index_mapping first.'
            )
        self.stdout.write(f'index valuation: {len(codes)} index code(s)')

        end = options['end'] or time.strftime('%Y%m%d')
        written = failed = 0
        for position, code in enumerate(codes, start=1):
            frame = None
            # The free tier answers a throttled call with an *empty frame*, not an error, so
            # "no rows" is ambiguous between "this index has no valuation" and "slow down".
            # Retry twice with a longer backoff before believing it.
            for attempt in range(3):
                try:
                    frame = pro.index_dailybasic(
                        ts_code=code, start_date=options['start'], end_date=end,
                    )
                except Exception as exc:  # network, rate limit, unknown code
                    self.stderr.write(f'  {code}: {type(exc).__name__} {str(exc)[:80]}')
                    frame = None
                if frame is not None and not frame.empty:
                    break
                time.sleep(max(3.0, options['pause'] * (attempt + 2)))
            if frame is None or frame.empty:
                failed += 1
                self.stdout.write(f'  {code}: no rows after retries')
                continue
            payload = []
            for row in frame.itertuples(index=False):
                payload.append(IndexValuationDaily(
                    index_code=str(row.ts_code),
                    date=_as_date(row.trade_date),
                    **{name: _float(getattr(row, name, None)) for name in NUMERIC},
                ))
            with transaction.atomic():
                IndexValuationDaily.objects.bulk_create(
                    payload, batch_size=2000,
                    update_conflicts=True,
                    unique_fields=['index_code', 'date'],
                    update_fields=list(NUMERIC),
                )
            written += len(payload)
            if position % 25 == 0:
                self.stdout.write(f'  {position}/{len(codes)} indices, {written} rows')
            time.sleep(options['pause'])
        self.stdout.write(
            self.style.SUCCESS(f'synced {written} row(s) for {len(codes) - failed} index code(s)')
        )
        if failed:
            self.stdout.write(self.style.WARNING(f'{failed} index code(s) failed'))


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
