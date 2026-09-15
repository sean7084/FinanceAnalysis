from django.core.management.base import BaseCommand, CommandError

from apps.backtest.inline_execution import run_backtest_inline_chunk_once
from apps.backtest.models import BacktestRun


class Command(BaseCommand):
    help = 'Execute one BacktestRun inline for a single chunk in the current process.'

    def add_arguments(self, parser):
        parser.add_argument('--run-id', required=True, type=int, help='BacktestRun id to execute inline.')

    def handle(self, *args, **options):
        run_id = int(options['run_id'])
        run = BacktestRun.objects.filter(id=run_id).first()
        if run is None:
            raise CommandError(f'BacktestRun not found: {run_id}')

        try:
            result = run_backtest_inline_chunk_once(run_id)
        except Exception as exc:
            raise CommandError(f'Inline backtest execution crashed for run_id={run_id}: {exc}') from exc

        run.refresh_from_db()
        if run.status == BacktestRun.Status.FAILED:
            raise CommandError(f'Inline backtest failed for run_id={run.id}: {run.error_message}')

        self.stdout.write(self.style.SUCCESS(str(result or f'Inline backtest chunk completed for run_id={run.id}')))
