from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.core.date_floor import get_historical_data_floor
from apps.markets.tasks import sync_exchange_trading_calendar


class Command(BaseCommand):
    help = 'Backfill official exchange trading calendar data from TuShare trade_cal.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=get_historical_data_floor().isoformat())
        parser.add_argument('--end-date', default=date.today().isoformat())
        parser.add_argument('--exchange-codes', default='SSE,SZSE')

    def handle(self, *args, **options):
        try:
            summary = sync_exchange_trading_calendar(
                exchange_codes=options['exchange_codes'],
                start_date=options['start_date'],
                end_date=options['end_date'],
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            'Trading calendar backfill complete: '
            f"exchange_codes={','.join(summary['exchange_codes'])}, "
            f"window={summary['start_date']}..{summary['end_date']}, "
            f"rows_written={summary['rows_written']}"
        ))