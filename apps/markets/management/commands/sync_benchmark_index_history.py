from django.core.management.base import BaseCommand

from apps.markets.tasks import sync_benchmark_index_history


class Command(BaseCommand):
    help = 'Sync official benchmark index history for CSI300 and CSIA500.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--index-codes',
            dest='index_codes',
            default=None,
            help='Comma-separated benchmark index codes. Defaults to 000300.SH,000510.CSI.',
        )
        parser.add_argument(
            '--start-date',
            dest='start_date',
            default=None,
            help='Start date in YYYY-MM-DD format.',
        )
        parser.add_argument(
            '--end-date',
            dest='end_date',
            default=None,
            help='End date in YYYY-MM-DD format.',
        )

    def handle(self, *args, **options):
        summary = sync_benchmark_index_history(
            index_codes=options.get('index_codes'),
            start_date=options.get('start_date'),
            end_date=options.get('end_date'),
        )
        self.stdout.write(
            self.style.SUCCESS(
                'index_codes={index_codes}; start_date={start_date}; end_date={end_date}; rows_written={rows_written}'.format(
                    index_codes=','.join(summary['index_codes']),
                    start_date=summary['start_date'],
                    end_date=summary['end_date'],
                    rows_written=summary['rows_written'],
                )
            )
        )