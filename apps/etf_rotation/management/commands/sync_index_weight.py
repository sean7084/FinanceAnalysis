"""Backfill ``IndexWeightSnapshot`` for the indices the eligible pool tracks.

``index_weight`` answers with a capped page (7,000 rows observed), and a broad index
publishes hundreds of constituents a month, so a multi-month request can silently truncate.
Every window is therefore verified: a page that comes back at the cap is split in half and
re-fetched until each piece is below the cap. A single month still at the cap is reported
loudly -- that index's weights would be incomplete and its breadth features would lie.

Only indices reachable from an ETF that has ever been eligible are fetched. That is the
whole point of the mapping table: a breadth feature for an index no fund tracks is a row
nobody can join to.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import tushare as ts
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.etf_rotation.models import ETFIndexMapping, ETFFeatureSnapshot, IndexWeightSnapshot

#: TuShare answers at most this many rows per call; hitting it means the window was cut.
PAGE_LIMIT = 7000


def _parse_day(value: str) -> date:
    return datetime.strptime(str(value), '%Y%m%d').date()


def _windows(start: date, end: date, months: int):
    cursor = start
    while cursor <= end:
        # Calendar-month stepping is good enough: TuShare filters on the publication date.
        year = cursor.year + (cursor.month - 1 + months) // 12
        month = (cursor.month - 1 + months) % 12 + 1
        nxt = date(year, month, min(cursor.day, 28)) - timedelta(days=1)
        yield cursor, min(nxt, end)
        cursor = min(nxt, end) + timedelta(days=1)


def _fetch(pro, code, start: date, end: date, pause: float, notes: list) -> list[dict]:
    """All weight rows for one window, splitting the window when it hits the page cap."""
    frame = None
    for attempt in range(3):
        try:
            frame = pro.index_weight(
                index_code=code,
                start_date=start.strftime('%Y%m%d'),
                end_date=end.strftime('%Y%m%d'),
            )
            break
        except Exception as exc:  # rate limit or transient network
            if attempt == 2:
                notes.append(f'{code} {start}..{end}: {type(exc).__name__}: {exc}')
            else:
                time.sleep(2.0 * (attempt + 1))
    time.sleep(pause)
    if frame is None:
        return []
    if len(frame) >= PAGE_LIMIT:
        if start >= end:
            notes.append(
                f'{code} {start}: still {len(frame)} rows at a single month -- this index '
                'exceeds the page cap; its breadth history would be partial.'
            )
            return frame.to_dict('records')
        middle = start + (end - start) // 2
        return (
            _fetch(pro, code, start, middle, pause, notes)
            + _fetch(pro, code, middle + timedelta(days=1), end, pause, notes)
        )
    return frame.to_dict('records')


class Command(BaseCommand):
    help = 'Sync monthly index constituent weights for the indices the eligible pool tracks.'

    def add_arguments(self, parser):
        parser.add_argument('--start', default='20160101', help='Start date YYYYMMDD.')
        parser.add_argument('--end', default='', help='End date YYYYMMDD (default today).')
        parser.add_argument('--limit', type=int, default=0, help='Only the first N indices.')
        parser.add_argument('--pause', type=float, default=0.35, help='Seconds between calls.')
        parser.add_argument('--window-months', type=int, default=6,
                            help='Months per request (default 6; a year can hit the page limit).')
        parser.add_argument('--only-missing', action='store_true',
                            help='Skip indices that already have weight rows.')

    def handle(self, *args, **options):
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is not configured.')
        pro = ts.pro_api(token)

        eligible_ids = set(
            ETFFeatureSnapshot.objects.filter(is_eligible=True)
            .values_list('etf_id', flat=True).distinct()
        )
        # Ordered by how many eligible funds track the index, so a run that has to stop early
        # still leaves the pool covered as far as it got. Alphabetical order spends the first
        # hour on the 上证 family, which no thematic ETF needs.
        counts = {}
        for code in ETFIndexMapping.objects.filter(etf_id__in=eligible_ids) \
                .values_list('index_code', flat=True):
            if code:
                counts[code] = counts.get(code, 0) + 1
        codes = [code for code, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
        if options['only_missing']:
            have = set(IndexWeightSnapshot.objects.values_list('index_code', flat=True).distinct())
            codes = [code for code in codes if code not in have]
        if options['limit']:
            codes = codes[:options['limit']]
        if not codes:
            raise CommandError('no mapped index to sync; build the ETF-index mapping first.')
        self.stdout.write(f'index weight: {len(codes)} index(es)')

        start = _parse_day(options['start'])
        end = _parse_day(options['end']) if options['end'] else date.today()
        written = failed = 0
        for position, code in enumerate(codes, start=1):
            index_rows = 0
            notes: list[str] = []
            for window_start, window_end in _windows(start, end, int(options['window_months'])):
                records = _fetch(pro, code, window_start, window_end, float(options['pause']), notes)
                if not records:
                    continue
                rows = [
                    IndexWeightSnapshot(
                        index_code=str(record['index_code']).strip(),
                        con_code=str(record['con_code']).strip(),
                        trade_date=datetime.strptime(str(record['trade_date']), '%Y%m%d').date(),
                        weight=(None if record.get('weight') is None
                                or str(record.get('weight')) in ('nan', '')
                                else float(record['weight'])),
                    )
                    for record in records
                ]
                with transaction.atomic():
                    IndexWeightSnapshot.objects.bulk_create(rows, ignore_conflicts=True)
                written += len(rows)
                index_rows += len(rows)
            for note in notes:
                failed += 1
                self.stderr.write(self.style.ERROR(note))
            self.stdout.write(
                f'  [{position}/{len(codes)}] {code}: {index_rows} row(s), total {written}'
            )
        self.stdout.write(self.style.SUCCESS(
            f'index weight: {written} row(s); {failed} problem(s) reported above.'
        ))
