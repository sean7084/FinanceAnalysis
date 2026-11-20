"""Verify IndexMembership snapshot continuity for one index (UNI-02, read-only).

Checks per-snapshot row counts against the index-size band and calendar-month
presence from the index's history floor to the last snapshot. Exits non-zero on
any violation: a silent missing month would feed the fail-closed universe contract
partial history.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from apps.markets.models import IndexMembership
from apps.markets.tasks import (
    MEMBERSHIP_CONTINUITY_BANDS,
    MEMBERSHIP_CONTINUITY_FLOORS,
    _membership_continuity_violations,
)


class Command(BaseCommand):
    help = 'Verify per-snapshot row counts and month continuity for an index membership history.'

    def add_arguments(self, parser):
        parser.add_argument('--index-code', default='000852.SH')
        parser.add_argument('--start-date', default='', help='YYYY-MM-DD; default: the index history floor.')
        parser.add_argument('--end-date', default='', help='YYYY-MM-DD; default: the last stored snapshot.')

    def handle(self, *args, **options):
        index_code = options['index_code']
        band = MEMBERSHIP_CONTINUITY_BANDS.get(index_code)
        if band is None:
            raise CommandError(
                f'No continuity band known for {index_code}; known: {sorted(MEMBERSHIP_CONTINUITY_BANDS)}.'
            )
        rows = (
            IndexMembership.objects.filter(index_code=index_code)
            .values('trade_date')
            .annotate(n=Count('id'))
            .order_by('trade_date')
        )
        snapshot_counts = {row['trade_date']: row['n'] for row in rows}
        if not snapshot_counts:
            raise CommandError(f'No IndexMembership rows for {index_code}; sync before verifying.')
        floor = MEMBERSHIP_CONTINUITY_FLOORS.get(index_code, min(snapshot_counts))
        start_date = date.fromisoformat(options['start_date']) if options['start_date'] else floor
        end_date = date.fromisoformat(options['end_date']) if options['end_date'] else max(snapshot_counts)
        violations = _membership_continuity_violations(
            snapshot_counts, start_date=start_date, end_date=end_date, band=band,
        )
        self.stdout.write(
            f'{index_code}: {len(snapshot_counts)} snapshots, {min(snapshot_counts)}..{max(snapshot_counts)}, '
            f'checked {start_date}..{end_date}, band {band}'
        )
        if violations:
            for violation in violations[:50]:
                self.stderr.write(self.style.ERROR(violation))
            raise CommandError(f'{len(violations)} continuity violation(s) for {index_code}.')
        self.stdout.write(self.style.SUCCESS(f'{index_code}: continuity OK (no missing months, all snapshots in band).'))