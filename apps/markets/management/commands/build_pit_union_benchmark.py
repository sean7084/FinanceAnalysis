from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from apps.markets.benchmarking import refresh_point_in_time_union_benchmark


class Command(BaseCommand):
    help = 'Build or refresh the internal point-in-time CSI300 + CSI A500 union benchmark.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', required=True, help='Inclusive start date (YYYY-MM-DD).')
        parser.add_argument('--end-date', required=True, help='Inclusive end date (YYYY-MM-DD).')
        parser.add_argument('--initial-nav', default='100000', help='Initial NAV used for the first benchmark row.')

    def _parse_date(self, value, name):
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc

    def handle(self, *args, **options):
        start_date = self._parse_date(options['start_date'], 'start-date')
        end_date = self._parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        summary = refresh_point_in_time_union_benchmark(
            start_date=start_date,
            end_date=end_date,
            initial_nav=Decimal(str(options['initial_nav'])),
        )
        self.stdout.write(
            self.style.SUCCESS(
                'benchmark_code={benchmark_code}; rows_written={rows_written}; start_date={start_date}; end_date={end_date}'.format(
                    benchmark_code=summary['benchmark_code'],
                    rows_written=summary['rows_written'],
                    start_date=summary['start_date'],
                    end_date=summary['end_date'],
                )
            )
        )