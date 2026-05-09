from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.core.date_floor import get_historical_data_floor
from apps.markets.models import Asset
from apps.markets.tasks import sync_asset_suspensions


class Command(BaseCommand):
    help = 'Backfill daily asset suspension data from TuShare suspend_d.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=get_historical_data_floor().isoformat())
        parser.add_argument('--end-date', default=date.today().isoformat())
        parser.add_argument('--symbols', default='')

    def handle(self, *args, **options):
        symbols = [token.strip() for token in str(options['symbols'] or '').split(',') if token.strip()]
        ts_codes = []
        if symbols:
            ts_codes = list(
                Asset.objects.filter(Q(symbol__in=symbols) | Q(ts_code__in=symbols))
                .order_by('ts_code')
                .values_list('ts_code', flat=True)
            )

        try:
            summary = sync_asset_suspensions(
                start_date=options['start_date'],
                end_date=options['end_date'],
                ts_codes=ts_codes,
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            'Asset suspension backfill complete: '
            f"asset_count={summary['asset_count']}, "
            f"window={summary['start_date']}..{summary['end_date']}, "
            f"rows_written={summary['rows_written']}, full_day_rows={summary['full_day_rows']}"
        ))