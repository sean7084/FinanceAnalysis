import json
from datetime import datetime
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.backtest.models import BacktestRun


class Command(BaseCommand):
    help = 'Run a rolling benchmark backtest suite and export the resulting report bundle under reports/.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', required=True, help='Validation start date (YYYY-MM-DD).')
        parser.add_argument('--end-date', required=True, help='Validation end date (YYYY-MM-DD).')
        parser.add_argument('--window-days', type=int, default=180, help='Days in each rolling validation window.')
        parser.add_argument('--step-days', type=int, default=30, help='Step size between window starts.')
        parser.add_argument('--sources', default='heuristic,lightgbm,lstm', help='Comma-separated sources: heuristic,lightgbm,lstm.')
        parser.add_argument('--top-n', type=int, default=3, help='Number of picks per entry cohort.')
        parser.add_argument('--horizon-days', type=int, default=7, help='Prediction horizon in days (3, 7, or 30).')
        parser.add_argument('--entry-weekdays', default='1,3', help='Comma-separated ISO weekdays (1=Mon .. 7=Sun).')
        parser.add_argument('--holding-period-days', type=int, default=7, help='Holding period in calendar days.')
        parser.add_argument('--capital-fraction-per-entry', type=float, default=0.5, help='Capital fraction used for each entry cohort.')
        parser.add_argument('--min-up-probability', type=float, default=0.0, help='Minimum up probability threshold.')
        parser.add_argument('--name-prefix', default='validation', help='Prefix used for BacktestRun names.')
        parser.add_argument('--user-email', default='', help='Optional user email to attribute created runs.')
        parser.add_argument('--queue', action='store_true', help='Queue runs asynchronously instead of running inline.')
        parser.add_argument('--output-dir', default='', help='Optional report output directory. Defaults to reports/<suite-name>.')
        parser.add_argument('--suite-name', default='', help='Optional suite name used for the output directory and manifest.')
        parser.add_argument(
            '--include-active-lightgbm-artifacts',
            action='store_true',
            help='Also export active LightGBM artifact metadata.',
        )

    def handle(self, *args, **options):
        suite_name = options['suite_name'] or f"reference_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        output_dir = Path(options['output_dir']) if options['output_dir'] else Path('reports') / suite_name
        output_dir.mkdir(parents=True, exist_ok=True)
        suite_started_at = timezone.now()
        run_name_prefix = f"{options['name_prefix']}-{suite_name}"

        call_command(
            'run_validation_backtests',
            start_date=options['start_date'],
            end_date=options['end_date'],
            window_days=options['window_days'],
            step_days=options['step_days'],
            sources=options['sources'],
            top_n=options['top_n'],
            horizon_days=options['horizon_days'],
            entry_weekdays=options['entry_weekdays'],
            holding_period_days=options['holding_period_days'],
            capital_fraction_per_entry=options['capital_fraction_per_entry'],
            min_up_probability=options['min_up_probability'],
            name_prefix=run_name_prefix,
            user_email=options['user_email'],
            queue=options['queue'],
            stdout=self.stdout,
        )

        created_run_ids = list(
            BacktestRun.objects.filter(
                name__startswith=f'{run_name_prefix}-',
                created_at__gte=suite_started_at,
            )
            .order_by('id')
            .values_list('id', flat=True)
        )

        if not created_run_ids:
            raise CommandError('run_validation_backtests did not create any runs.')

        call_command(
            'export_backtest_runs',
            start_id=min(created_run_ids),
            end_id=max(created_run_ids),
            output_dir=str(output_dir),
            detail_export=False,
            include_active_lightgbm_artifacts=options['include_active_lightgbm_artifacts'],
            stdout=self.stdout,
        )

        manifest = {
            'suite_name': suite_name,
            'output_dir': str(output_dir),
            'run_ids': created_run_ids,
            'sources': [token.strip() for token in str(options['sources']).split(',') if token.strip()],
            'start_date': options['start_date'],
            'end_date': options['end_date'],
            'window_days': options['window_days'],
            'step_days': options['step_days'],
            'top_n': options['top_n'],
            'horizon_days': options['horizon_days'],
            'entry_weekdays': options['entry_weekdays'],
            'holding_period_days': options['holding_period_days'],
            'capital_fraction_per_entry': options['capital_fraction_per_entry'],
            'min_up_probability': options['min_up_probability'],
            'queued': options['queue'],
        }
        (output_dir / 'suite_manifest.json').write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding='utf-8',
        )

        self.stdout.write(self.style.SUCCESS(f'Reference benchmark suite exported to {output_dir}'))