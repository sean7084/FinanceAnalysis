"""Sync TuShare namechange history and rebuild as-of ST intervals (B2 / STK-12, #198).

Windows step by ANNOUNCEMENT date: the API's start_date/end_date parameters
filter ann_date (measured 2026-11-20, W0-6), while a row's start_date — the
as-of effective date — can fall outside the window. Full pages bisect; a single
day still at the cap raises rather than silently dropping records. Rows that
violate ann_date <= start_date are quarantined to a report and NEVER stored
(the look-ahead gate). Interval rebuild is transactional per touched asset.
"""
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import tushare as ts
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.markets.models import Asset
from apps.markets.st_history import ingest_namechange_records, rebuild_asset_st_intervals

PAGE_LIMIT = 7000


def _parse_day(value):
    return datetime.strptime(str(value), '%Y%m%d').date()


def _windows(start, end, months):
    cursor = start
    while cursor <= end:
        year = cursor.year + (cursor.month - 1 + months) // 12
        month = (cursor.month - 1 + months) % 12 + 1
        nxt = date(year, month, 1)
        yield cursor, min(nxt - timedelta(days=1), end)
        cursor = min(nxt - timedelta(days=1), end) + timedelta(days=1)


def _fetch(pro, start, end, pause):
    frame = pro.namechange(start_date=start.strftime('%Y%m%d'), end_date=end.strftime('%Y%m%d'))
    time.sleep(pause)
    if frame is None or frame.empty:
        return []
    if len(frame) >= PAGE_LIMIT:
        if start >= end:
            raise CommandError(
                f'namechange {start} returned {len(frame)} rows at the {PAGE_LIMIT}-row page cap '
                'for a single day; history would be partial. Refusing to sync silently.'
            )
        middle = start + (end - start) // 2
        return _fetch(pro, start, middle, pause) + _fetch(pro, middle + timedelta(days=1), end, pause)
    return frame.to_dict('records')


class Command(BaseCommand):
    help = 'Sync namechange history (ann_date windows) and rebuild as-of ST intervals.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default='2009-01-01', help='Announcement-window start (YYYY-MM-DD).')
        parser.add_argument('--end-date', default='', help='Announcement-window end (default today).')
        parser.add_argument('--window-months', type=int, default=12)
        parser.add_argument('--pause', type=float, default=0.4)
        parser.add_argument('--skip-rebuild', action='store_true', help='Ingest only; rebuild intervals separately.')
        parser.add_argument('--quarantine-dir', default='docs/reports/stock')

    def handle(self, *args, **options):
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is not configured.')
        pro = ts.pro_api(token)

        start = date.fromisoformat(options['start_date'])
        end = date.fromisoformat(options['end_date']) if options['end_date'] else timezone.localdate()
        asset_ids_by_ts_code = dict(Asset.objects.values_list('ts_code', 'id'))

        stored = 0
        quarantined = []
        touched_assets = set()
        for win_start, win_end in _windows(start, end, int(options['window_months'])):
            records = _fetch(pro, win_start, win_end, float(options['pause']))
            self.stdout.write(f'namechange {win_start}..{win_end}: {len(records)} row(s)')
            stored_now, quarantined_now, touched_now = ingest_namechange_records(records, asset_ids_by_ts_code)
            stored += stored_now
            quarantined.extend(quarantined_now)
            touched_assets.update(touched_now)

        if quarantined:
            out_dir = Path(options['quarantine_dir'])
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f'namechange-quarantine-{timezone.localdate().isoformat()}.md'
            lines = [
                f'# namechange quarantine — {timezone.localdate().isoformat()}',
                '',
                f'{len(quarantined)} row(s) violated the ingestion gate and were NOT stored.',
                '',
                '| ts_code | name | start_date | ann_date | reason | gate |',
                '| :--- | :--- | :--- | :--- | :--- | :--- |',
            ]
            for record in quarantined[:500]:
                lines.append(
                    f"| {record.get('ts_code', '')} | {record.get('name', '')} | {record.get('start_date', '')} "
                    f"| {record.get('ann_date', '')} | {record.get('change_reason', '')} | {record['quarantine_reason']} |"
                )
            out_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            self.stderr.write(self.style.WARNING(f'{len(quarantined)} quarantined row(s) -> {out_path}'))

        if not options['skip_rebuild']:
            rebuilt = 0
            for asset_id in sorted(touched_assets):
                with transaction.atomic():
                    rebuilt += rebuild_asset_st_intervals(asset_id)
            self.stdout.write(f'rebuilt intervals for {len(touched_assets)} asset(s), {rebuilt} interval(s)')

        self.stdout.write(self.style.SUCCESS(
            f'namechange: {stored} row(s) stored, {len(quarantined)} quarantined, {len(touched_assets)} asset(s) touched.'
        ))