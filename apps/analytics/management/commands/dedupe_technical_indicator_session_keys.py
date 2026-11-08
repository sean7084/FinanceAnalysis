"""Delete the parallel TechnicalIndicator rows the TIME_ZONE flip created.

Before ``session_key_timestamp`` anchored the write key to a fixed zone, the key was
``make_aware(combine(trade_date, midnight))`` and followed ``settings.TIME_ZONE``. The
2026-11-02 flip from UTC to Asia/Shanghai means one trade date has two possible instants:

    new convention   d 00:00+08  ==  (d-1) 16:00Z
    old convention   d 00:00Z

``unique_together`` includes ``timestamp``, so both can exist. This command removes the
old-convention row when — and only when — its new-convention twin is present for the same
``(asset, indicator_type, parameters, session)``. A lone old-convention row with no twin
is the only record for that date and must survive; the backfill that rewrites it in the
new convention is a separate, larger job.

**Per-asset, not a self-join.** ``b.timestamp = a.timestamp - interval '8 hours'`` over
the whole table cannot use the ``(asset_id, timestamp, indicator_type)`` index and would
seq-scan a nine-figure table once per window. Driving per asset keeps every query inside
the index, and the flip is days old so each asset has only a few thousand candidates.

**A command, not a data migration.** A migration would run this inside ``migrate`` on
every environment, including CI, over CI's own link. Resumability comes from re-running:
a pair already deduped simply does not appear.

**Measured 2026-11-08: zero pairs.** That is the expected result, not evidence the risk
was overstated. A cross-convention pair only appears when a recompute writes a date that
*already* has a row under the other convention — and since the flip every indicator write
has used one convention. Checked one asset's rows since 2026-10-01: all 504 sit at Beijing
hour 0. So the exposure is latent, not realised: this command is the guard for the first
post-fix recompute of a pre-flip date, and it must be run then. The key-space doubling the
backlog warned about had not happened yet.

**Trigger: run this guard ONLY after a recompute that touched pre-flip dates** -- any
backfill_technical_indicators run, or per-indicator task recompute, whose range
includes sessions before 2026-11-02. The historical risk is
backfill_technical_indicators's delete-then-recreate per chunk
(apps/analytics/management/commands/backfill_technical_indicators.py:415-424), which is
the one writer that revisits history. Always --dry-run first; run for real only if it
reports pairs.

Verification afterwards -- idle link only, wedge watcher up, inside an explicit
statement_timeout, because timestamp is not the leading column of the
(asset_id, timestamp, indicator_type) index and this scans wide on a 179M-row table::

    SET statement_timeout = '10min';
    SELECT (timestamp AT TIME ZONE 'Asia/Shanghai')::time AS beijing_tod, count(*)
      FROM analytics_technicalindicator
     WHERE timestamp >= '2026-10-01' AND timestamp < '2026-11-10'
     GROUP BY 1;

Only 00:00:00 may appear. Any 08:00:00 bucket is an old-convention (pre-flip UTC)
row; a lone one is kept by design, since deletion happens only when the new-convention
twin (d-1) 16:00Z exists for the same (asset, indicator_type, parameters, session_day).

Usage::

    python manage.py dedupe_technical_indicator_session_keys --dry-run
    python manage.py dedupe_technical_indicator_session_keys --batch-size 5000
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import json

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from apps.analytics.models import TechnicalIndicator
from apps.core.date_floor import session_key_timestamp
from apps.markets.models import Asset

#: The first trade date whose key could have been written under both conventions.
DEFAULT_START_DATE = date(2026, 11, 2)


class Command(BaseCommand):
    help = (
        'Delete TechnicalIndicator rows written under the pre-2026-11-02 UTC key when a '
        'new-convention twin exists for the same asset, type, parameters and session.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report the duplicate set without deleting anything.')
        parser.add_argument('--start-date', default=DEFAULT_START_DATE.isoformat(),
                            help=f'First affected trade date (default {DEFAULT_START_DATE}).')
        parser.add_argument('--batch-size', type=int, default=5000,
                            help='Rows per DELETE statement (default 5000).')
        parser.add_argument('--asset-batch-size', type=int, default=200,
                            help='Assets per progress checkpoint (default 200).')

    def handle(self, *args, **options):
        start_date = date.fromisoformat(options['start_date'])
        dry_run = options['dry_run']
        batch_size = max(int(options['batch_size']), 1)
        asset_batch_size = max(int(options['asset_batch_size']), 1)

        # One day before, so the (d-1)16:00Z form of the first affected date is included.
        floor = session_key_timestamp(start_date - timedelta(days=1))

        # Drive from Asset, not from a DISTINCT over TechnicalIndicator. A timestamp-only
        # filter has no leading index column (the index is asset_id, timestamp,
        # indicator_type), so selecting the candidate asset ids from the 179M-row table
        # would seq-scan it once -- the exact shape that wedged the link on 2026-11-05.
        # There are ~1,900 assets, and every per-asset probe below stays inside the index.
        asset_ids = list(Asset.objects.values_list('id', flat=True).order_by('id'))
        self.stdout.write(f'{len(asset_ids)} asset(s) to probe for rows on or after {start_date}')

        pairs = 0
        deleted = 0
        samples = []
        for offset in range(0, len(asset_ids), asset_batch_size):
            chunk = asset_ids[offset:offset + asset_batch_size]
            doomed = []
            for asset_id, rows in self._rows_by_asset(chunk, floor).items():
                pairs += len(rows)
                doomed.extend(rows)
                if len(samples) < 20:
                    samples.extend(rows[:20 - len(samples)])

            if dry_run:
                self.stdout.write(
                    f'  assets up to id {chunk[-1]}: {pairs} duplicate pair(s) so far')
                continue

            for start in range(0, len(doomed), batch_size):
                window = doomed[start:start + batch_size]
                deleted += self._delete(window)
            self.stdout.write(
                f'  assets up to id {chunk[-1]}: {pairs} pair(s), {deleted} row(s) deleted')

        if dry_run:
            self.stdout.write(f'dry run: {pairs} duplicate row(s) would be deleted')
            for row in samples:
                self.stdout.write(
                    f'  asset={row["asset_id"]} {row["indicator_type"]} '
                    f'params={row["parameters"]} session={row["session"]} '
                    f'old_timestamp={row["timestamp"]}')
        else:
            self.stdout.write(f'done: {pairs} pair(s) found, {deleted} row(s) deleted')

    def _rows_by_asset(self, asset_ids, floor):
        """Old-convention ids that have a new-convention twin, keyed by asset."""
        rows = TechnicalIndicator.objects.filter(
            asset_id__in=asset_ids, timestamp__gte=floor,
        ).values('id', 'asset_id', 'timestamp', 'indicator_type', 'parameters')

        grouped = defaultdict(lambda: defaultdict(list))
        for row in rows:
            session_day = timezone.localtime(row['timestamp']).date()
            # parameters is a JSONField, so it is a dict on real rows and unhashable.
            # Canonicalise it: the same column is part of unique_together, so two rows
            # only collide when the JSON is genuinely equal.
            parameters_key = json.dumps(row['parameters'], sort_keys=True, default=str)
            grouped[row['asset_id']][
                (row['indicator_type'], parameters_key, session_day)
            ].append(row)

        duplicates = defaultdict(list)
        for asset_id, sessions in grouped.items():
            for (indicator_type, parameters_key, session_day), entries in sessions.items():
                new_key = session_key_timestamp(session_day)
                old_key = new_key + timedelta(hours=8)
                has_new = any(entry['timestamp'] == new_key for entry in entries)
                if not has_new:
                    # A lone old-convention row is the only record for this session.
                    continue
                for entry in entries:
                    if entry['timestamp'] == old_key:
                        duplicates[asset_id].append({
                            'id': entry['id'],
                            'asset_id': asset_id,
                            'indicator_type': indicator_type,
                            'parameters': parameters_key,
                            'session': session_day,
                            'timestamp': entry['timestamp'],
                        })
        return duplicates

    def _delete(self, rows):
        ids = [row['id'] for row in rows]
        if not ids:
            return 0
        with connection.cursor() as cursor:
            cursor.execute(
                'DELETE FROM analytics_technicalindicator WHERE id = ANY(%s)', [ids])
            return cursor.rowcount