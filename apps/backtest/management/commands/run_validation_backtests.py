from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.backtest.models import BacktestRun
from apps.backtest.tasks import run_backtest


def _parse_date(value, name):
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc


def _parse_csv_ints(value, name):
    items = []
    for raw in (value or '').split(','):
        token = raw.strip()
        if not token:
            continue
        try:
            items.append(int(token))
        except ValueError as exc:
            raise CommandError(f'Invalid integer in {name}: {token}') from exc
    return items


class Command(BaseCommand):
    help = 'Run systematic backtest validations over rolling windows for heuristic vs LightGBM.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', required=True, help='Validation start date (YYYY-MM-DD).')
        parser.add_argument('--end-date', required=True, help='Validation end date (YYYY-MM-DD).')
        parser.add_argument('--window-days', type=int, default=180, help='Days in each rolling validation window.')
        parser.add_argument('--step-days', type=int, default=30, help='Step size between window starts.')
        parser.add_argument('--sources', default='heuristic,lightgbm', help='Comma-separated sources: heuristic,lightgbm.')
        parser.add_argument('--top-n', type=int, default=3, help='Number of picks per entry cohort.')
        parser.add_argument('--horizon-days', type=int, default=7, help='Prediction horizon in days (3, 7, or 30).')
        parser.add_argument('--entry-weekdays', default='1,3', help='Comma-separated ISO weekdays (1=Mon .. 7=Sun).')
        parser.add_argument('--holding-period-days', type=int, default=7, help='Holding period in calendar days.')
        parser.add_argument('--capital-fraction-per-entry', type=float, default=0.5, help='Capital fraction used for each entry cohort.')
        parser.add_argument('--min-up-probability', type=float, default=0.0, help='Minimum up probability threshold.')
        parser.add_argument('--name-prefix', default='validation', help='Prefix used for BacktestRun names.')
        parser.add_argument('--user-email', default='', help='Optional user email to attribute created runs.')
        parser.add_argument('--queue', action='store_true', help='Queue runs asynchronously instead of running inline.')

    def _resolve_user(self, user_email):
        user_model = get_user_model()
        if user_email:
            user = user_model.objects.filter(email=user_email).first()
            if user:
                return user
            raise CommandError(f'No user found for email: {user_email}')

        user = user_model.objects.filter(is_superuser=True).order_by('id').first()
        if user:
            return user
        return user_model.objects.order_by('id').first()

    def _build_windows(self, start_date, end_date, window_days, step_days):
        windows = []
        current_start = start_date
        while current_start <= end_date:
            current_end = min(end_date, current_start + timedelta(days=window_days - 1))
            if current_end >= current_start:
                windows.append((current_start, current_end))
            current_start += timedelta(days=step_days)
        return windows

    def handle(self, *args, **options):
        start_date = _parse_date(options['start_date'], 'start-date')
        end_date = _parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        window_days = options['window_days']
        step_days = options['step_days']
        if window_days <= 0 or step_days <= 0:
            raise CommandError('window-days and step-days must be positive integers.')

        sources = [token.strip().lower() for token in options['sources'].split(',') if token.strip()]
        allowed_sources = {'heuristic', 'lightgbm'}
        if not sources or any(source not in allowed_sources for source in sources):
            raise CommandError('sources must be a comma-separated subset of: heuristic,lightgbm')

        entry_weekdays = _parse_csv_ints(options['entry_weekdays'], 'entry-weekdays')
        if not entry_weekdays:
            raise CommandError('entry-weekdays must contain at least one ISO weekday integer.')
        if any(day < 1 or day > 7 for day in entry_weekdays):
            raise CommandError('entry-weekdays must use ISO values 1..7.')

        horizon_days = int(options['horizon_days'])
        if horizon_days not in {3, 7, 30}:
            raise CommandError('horizon-days must be one of 3, 7, or 30.')

        user = self._resolve_user(options['user_email'])
        windows = self._build_windows(start_date, end_date, window_days, step_days)
        if not windows:
            raise CommandError('No validation windows produced for the given date range and step settings.')

        self.stdout.write(
            self.style.SUCCESS(
                f'Launching {len(windows)} windows x {len(sources)} sources = {len(windows) * len(sources)} runs.'
            )
        )

        created_run_ids = []
        for source in sources:
            for index, (window_start, window_end) in enumerate(windows, start=1):
                name = f"{options['name_prefix']}-{source}-{window_start.isoformat()}-{window_end.isoformat()}"
                backtest_run = BacktestRun.objects.create(
                    user=user,
                    name=name,
                    strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                    start_date=window_start,
                    end_date=window_end,
                    initial_capital=100000.00,
                    status=BacktestRun.Status.PENDING,
                    parameters={
                        'top_n': options['top_n'],
                        'horizon_days': horizon_days,
                        'up_threshold': options['min_up_probability'],
                        'prediction_source': source,
                        'entry_weekdays': entry_weekdays,
                        'holding_period_days': options['holding_period_days'],
                        'capital_fraction_per_entry': options['capital_fraction_per_entry'],
                    },
                )

                created_run_ids.append(backtest_run.id)
                if options['queue']:
                    run_backtest.delay(backtest_run.id)
                    launch_mode = 'queued'
                else:
                    run_backtest(backtest_run.id)
                    launch_mode = 'executed'

                self.stdout.write(
                    f'[{source}] window {index}/{len(windows)}: run_id={backtest_run.id} ({launch_mode})'
                )

        self.stdout.write(self.style.SUCCESS(f'Created {len(created_run_ids)} validation runs.'))
