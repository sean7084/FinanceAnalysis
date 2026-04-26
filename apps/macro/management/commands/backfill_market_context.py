from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.macro.models import MacroSnapshot
from apps.macro.tasks import sync_market_context_for_snapshot


def _parse_date(value, name):
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc


class Command(BaseCommand):
    help = 'Backfill MarketContext history from monthly MacroSnapshot data.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=getattr(settings, 'HISTORICAL_DATA_FLOOR', '2000-01-01'))
        parser.add_argument('--end-date', default=date.today().isoformat())

    def handle(self, *args, **options):
        floor_raw = getattr(settings, 'HISTORICAL_DATA_FLOOR', '2000-01-01')
        floor_date = _parse_date(floor_raw, 'HISTORICAL_DATA_FLOOR')
        start_date = max(_parse_date(options['start_date'], 'start-date'), floor_date)
        end_date = _parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        snapshots = list(
            MacroSnapshot.objects.filter(
                date__gte=start_date,
                date__lte=end_date,
                pmi_manufacturing__isnull=False,
            )
            .order_by('date')
        )
        if not snapshots:
            raise CommandError('No MacroSnapshot rows with PMI manufacturing data were found in the requested range.')

        created_count = 0
        updated_count = 0
        for snapshot in snapshots:
            _context, created = sync_market_context_for_snapshot(
                snapshot,
                note='Backfilled from macro snapshot history.',
                metadata_source='macro_snapshot_backfill',
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'MarketContext backfill completed: created={created_count}, updated={updated_count}, range={start_date}..{end_date}'
        ))