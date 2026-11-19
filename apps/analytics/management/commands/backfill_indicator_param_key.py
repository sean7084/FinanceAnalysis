"""Backfill ``TechnicalIndicator.param_key`` from the stored jsonb payload.

Resumable: the predicate is ``param_key IS NULL``, so a re-run picks up exactly what is
left. The column was added nullable with no default precisely so NULL means "not yet
backfilled" -- a constant default would have made pre-existing rows read as ``''`` and
become indistinguishable from the genuinely empty OBV/RS_SCORE keys.

Canonicalisation runs in SQL to avoid transferring ~179M payloads over the NAS link. The
spelling is cross-checked against the Python helper on a bounded sample before any write,
because a formatting divergence between ``->>`` and ``str()`` would silently produce keys
that do not match what the readers compute.

Dry-run by default; ``--execute`` is an owner-gated production write.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.analytics.indicator_params import canonical_param_key
from apps.analytics.models import TechnicalIndicator
from apps.markets.models import Asset

#: One asset-year is ~5.6k rows at the current density -- small enough to keep a chunk's
#: WAL and lock footprint bounded, large enough that the run is not millions of statements.
YEAR_CHUNK = 1

#: Full-history scope. A run narrower than this is incremental and is not expected to
#: leave zero NULL keys behind.
DEFAULT_START_YEAR = 2008
DEFAULT_END_YEAR = 2027

KEY_SQL = """
    CASE
        WHEN jsonb_typeof(t.parameters) <> 'object' THEN t.parameters::text
        ELSE COALESCE((
            SELECT string_agg(k || '=' || (t.parameters ->> k), ',' ORDER BY k)
            FROM jsonb_object_keys(t.parameters) AS k
            WHERE NOT (
                (t.indicator_type = 'BBANDS' AND k IN ('upper', 'middle', 'lower'))
                OR (t.indicator_type = 'FIB_RET' AND k IN ('high', 'low', 'range', 'levels', 'close'))
            )
        ), '')
    END
"""


class Command(BaseCommand):
    help = 'Backfill TechnicalIndicator.param_key (dry-run unless --execute).'

    def add_arguments(self, parser):
        parser.add_argument('--execute', action='store_true',
                            help='Actually write. Without this the command only reports.')
        parser.add_argument('--asset-id', type=int, default=None,
                            help='Restrict to one asset (used by the equivalence sample).')
        parser.add_argument('--max-assets', type=int, default=None,
                            help='Bound the run to the first N assets, for incremental rollout.')
        parser.add_argument('--after-asset-id', type=int, default=None,
                            help='Skip assets with id <= this. Assets are processed in id order '
                                 'and each is completed fully, so a restart can resume past the '
                                 'last logged asset instead of re-scanning every finished chunk.')
        parser.add_argument('--start-year', type=int, default=DEFAULT_START_YEAR)
        parser.add_argument('--end-year', type=int, default=DEFAULT_END_YEAR)
        parser.add_argument('--verify-sample', type=int, default=2000,
                            help='Rows to cross-check SQL canonicalisation against Python before writing.')
        parser.add_argument('--statement-timeout', type=int, default=600)
        parser.add_argument('--by-ctid', action='store_true',
                            help='Sweep physical block ranges instead of asset-year chunks.')
        parser.add_argument('--blocks-per-chunk', type=int, default=20000,
                            help='Block range size for --by-ctid (~156 MiB of heap per chunk).')
        parser.add_argument('--by-id', action='store_true',
                            help='Chunk by primary-key range. Ids are clustered by asset '
                                 '(measured: 5,000 consecutive ids = 1 asset), so this keeps '
                                 'the asset locality that makes asset-year chunking fast while '
                                 'using the pkey instead of the hot index.')
        parser.add_argument('--id-chunk', type=int, default=5000,
                            help='Id-range size for --by-id.')

    def handle(self, *args, **options):
        execute = options['execute']
        asset_ids = ([options['asset_id']] if options['asset_id']
                     else list(Asset.objects.order_by('id').values_list('id', flat=True)))
        if options['max_assets'] is not None:
            asset_ids = asset_ids[:options['max_assets']]
        if options['after_asset_id'] is not None:
            asset_ids = [a for a in asset_ids if a > options['after_asset_id']]
        years = range(options['start_year'], options['end_year'] + 1)

        with connection.cursor() as cursor:
            cursor.execute(f"SET statement_timeout = '{options['statement_timeout']}s'")

        self.stdout.write(f'assets={len(asset_ids)} years={options["start_year"]}..{options["end_year"]} '
                          f'execute={execute}')

        if execute and options['verify_sample'] > 0:
            self._verify_sql_matches_python(asset_ids, options['verify_sample'])

        if options['by_ctid']:
            self._sweep_blocks(execute, options['blocks_per_chunk'])
            return

        if options['by_id']:
            self._sweep_ids(execute, options['id_chunk'])
            return

        total_updated = 0
        started = time.monotonic()
        for asset_id in asset_ids:
            for year in years:
                lower = datetime(year, 1, 1, tzinfo=dt_timezone.utc)
                upper = datetime(year + 1, 1, 1, tzinfo=dt_timezone.utc)
                if not execute:
                    pending = TechnicalIndicator.objects.filter(
                        asset_id=asset_id, timestamp__gte=lower, timestamp__lt=upper,
                        param_key__isnull=True,
                    ).count()
                    if pending:
                        total_updated += pending
                        self.stdout.write(f'  would backfill asset={asset_id} year={year} rows={pending}')
                    continue
                # No pre-COUNT on the execute path: counting param_key IS NULL heap-fetches
                # every row in the chunk, costing about as much as the UPDATE itself.
                # cursor.rowcount gives the same number for free.
                with connection.cursor() as cursor:
                    cursor.execute(
                        f'UPDATE analytics_technicalindicator t SET param_key = {KEY_SQL} '
                        'WHERE t.param_key IS NULL AND t.asset_id = %s '
                        'AND t.timestamp >= %s AND t.timestamp < %s',
                        [asset_id, lower, upper],
                    )
                    updated = cursor.rowcount
                if updated:
                    total_updated += updated
                    self.stdout.write(f'  backfilled asset={asset_id} year={year} rows={updated}')

        elapsed = time.monotonic() - started
        verb = 'updated' if execute else 'would update'
        self.stdout.write(f'{verb}={total_updated} elapsed_seconds={elapsed:.1f}')

        if execute:
            # Only an unscoped run is expected to finish the whole table. A deliberately
            # bounded run leaves the rest NULL by design, and failing it would make the
            # safe incremental path look broken.
            full_scope = (
                options['asset_id'] is None
                and options['max_assets'] is None
                and options['after_asset_id'] is None
                and options['start_year'] <= DEFAULT_START_YEAR
                and options['end_year'] >= DEFAULT_END_YEAR
            )
            if full_scope:
                # Unindexed full scan of ~174M rows (measured >5 min). Only worth paying
                # when the run actually claimed to cover the whole table.
                remaining = TechnicalIndicator.objects.filter(param_key__isnull=True).count()
                self.stdout.write(f'remaining_null={remaining}')
                if remaining:
                    raise CommandError(f'{remaining} rows still have NULL param_key; re-run to resume.')
            else:
                remaining = TechnicalIndicator.objects.filter(
                    asset_id__in=asset_ids,
                    timestamp__gte=datetime(options['start_year'], 1, 1, tzinfo=dt_timezone.utc),
                    timestamp__lt=datetime(options['end_year'] + 1, 1, 1, tzinfo=dt_timezone.utc),
                    param_key__isnull=True,
                ).count()
                self.stdout.write(f'remaining_null_in_scope={remaining}')
                if remaining:
                    raise CommandError(
                        f'{remaining} in-scope rows still have NULL param_key; re-run to resume.'
                    )

    def _sweep_ids(self, execute, chunk):
        """Chunk by primary-key range.

        Ids are clustered by asset -- measured: 5,000 consecutive ids contain exactly 1
        distinct asset -- so an id range keeps the index-insert locality that makes
        asset-year chunking fast, while needing only the pkey. That is what would make it
        safe to drop the 13 GiB hot index.

        Block ranges were the other candidate and were rejected BY MEASUREMENT: 344 rows/s
        against 8,828, because sequential heap reads scatter inserts across assets while an
        id range keeps one asset's index entries adjacent.
        """
        if not execute:
            self.stdout.write('dry-run: --by-id performs no writes.')
            return
        with connection.cursor() as cursor:
            cursor.execute('SELECT min(id), max(id) FROM analytics_technicalindicator')
            lo, hi = cursor.fetchone()
        if lo is None:
            self.stdout.write('table is empty; nothing to do.')
            return
        self.stdout.write(f'id_range={lo}..{hi} chunk={chunk}')
        total = 0
        passes = 0
        while True:
            passes += 1
            pass_total = 0
            for start in range(lo, hi + 1, chunk):
                end = start + chunk
                with connection.cursor() as cursor:
                    cursor.execute(
                        f'UPDATE analytics_technicalindicator t SET param_key = {KEY_SQL} '
                        'WHERE t.param_key IS NULL AND t.id >= %s AND t.id < %s',
                        [start, end],
                    )
                    updated = cursor.rowcount
                if updated:
                    pass_total += updated
            total += pass_total
            self.stdout.write(f'pass={passes} updated={pass_total} cumulative={total}')
            if pass_total == 0:
                break
            if passes >= 5:
                raise CommandError('id sweep did not converge after 5 passes; investigate.')
        remaining = TechnicalIndicator.objects.filter(param_key__isnull=True).count()
        self.stdout.write(f'updated={total} passes={passes} remaining_null={remaining}')
        if remaining:
            raise CommandError(f'{remaining} rows still have NULL param_key after a converged id sweep.')

    def _sweep_blocks(self, execute, blocks_per_chunk):
        """Physical-order sweep, instead of asset-year chunks.

        Asset-year chunking makes the planner use the (asset_id, timestamp, ...) index and
        scatter heap access: EXPLAIN cost 19,096 per chunk across ~38k chunks versus 6.4M
        for one sequential pass, and a wait-event profile of the running backfill showed
        79% IO/DataFileRead against 4% WALSync. Block ranges give Tid Range Scans --
        sequential reads, bounded transactions, still resumable via ``param_key IS NULL``.

        A non-HOT update can move a row to another block, so one forward sweep can miss rows
        that land behind the cursor. The predicate is idempotent, so passes repeat until one
        updates nothing. relpages is re-read each pass because the heap can extend.
        """
        if not execute:
            self.stdout.write('dry-run: --by-ctid performs no writes; run with --execute to sweep.')
            return
        total = 0
        passes = 0
        while True:
            passes += 1
            with connection.cursor() as cursor:
                # pg_relation_size, NOT pg_class.relpages: relpages is a planner estimate
                # that can be 0 or stale, which would make the block loop cover nothing and
                # let the convergence check pass vacuously. A test caught exactly that.
                cursor.execute(
                    "SELECT pg_relation_size('analytics_technicalindicator') / 8192"
                )
                blocks = cursor.fetchone()[0]
            self.stdout.write(f'pass={passes} blocks={blocks} blocks_per_chunk={blocks_per_chunk}')
            pass_total = 0
            for start in range(0, blocks, blocks_per_chunk):
                end = min(start + blocks_per_chunk, blocks)
                with connection.cursor() as cursor:
                    cursor.execute(
                        f'UPDATE analytics_technicalindicator t SET param_key = {KEY_SQL} '
                        'WHERE t.param_key IS NULL AND t.ctid >= %s::tid AND t.ctid < %s::tid',
                        [f'({start},1)', f'({end},1)'],
                    )
                    updated = cursor.rowcount
                if updated:
                    pass_total += updated
                    self.stdout.write(f'  pass={passes} blocks={start}..{end} rows={updated}')
            total += pass_total
            self.stdout.write(f'pass={passes} updated={pass_total} cumulative={total}')
            if pass_total == 0:
                break
            if passes >= 5:
                raise CommandError('block sweep did not converge after 5 passes; investigate.')
        # Converged passes only prove "nothing changed", not "nothing is left". Verify.
        remaining = TechnicalIndicator.objects.filter(param_key__isnull=True).count()
        self.stdout.write(f'updated={total} passes={passes} remaining_null={remaining}')
        if remaining:
            raise CommandError(
                f'{remaining} rows still have NULL param_key after a converged block sweep.'
            )

    def _verify_sql_matches_python(self, asset_ids, sample_size):
        """Prove the SQL key equals the Python key before trusting it on 179M rows.

        A mismatch here is a formatting divergence (``->>`` vs ``str``), not a data
        problem, and it must abort the run rather than write keys the readers will not
        recognise.
        """
        # Spread across the asset list, not the first five: a rare payload shape (JSON null
        # value, boolean, nested dict) would make SQL ``->>`` and Python ``str`` diverge, and
        # there is no reason it would appear in the lowest asset ids.
        step = max(1, len(asset_ids) // 20)
        spread = asset_ids[::step][:20] or asset_ids[:1]
        rows = list(
            TechnicalIndicator.objects.filter(
                asset_id__in=spread, param_key__isnull=True,
            ).values('id', 'indicator_type', 'parameters')[:sample_size]
        )
        if not rows:
            self.stdout.write('verify-sample: no NULL-key rows available to compare; skipped.')
            return
        ids = [row['id'] for row in rows]
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT id, {KEY_SQL} FROM analytics_technicalindicator t WHERE id = ANY(%s)',
                [ids],
            )
            sql_keys = dict(cursor.fetchall())
        mismatches = []
        for row in rows:
            expected = canonical_param_key(row['indicator_type'], row['parameters'])
            actual = sql_keys.get(row['id'])
            if actual != expected:
                mismatches.append((row['id'], row['indicator_type'], expected, actual))
        self.stdout.write(f'verify-sample: compared={len(rows)} mismatches={len(mismatches)}')
        if mismatches:
            for mismatch in mismatches[:10]:
                self.stderr.write(f'  MISMATCH id={mismatch[0]} type={mismatch[1]} '
                                  f'python={mismatch[2]!r} sql={mismatch[3]!r}')
            raise CommandError(
                f'{len(mismatches)} of {len(rows)} sampled keys differ between SQL and Python; '
                'aborting before any write.'
            )