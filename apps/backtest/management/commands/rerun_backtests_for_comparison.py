# docker exec -i finance_analysis_django python manage.py rerun_backtests_for_comparison --run-ids  --name-suffix --queue
from copy import deepcopy
from unittest.mock import patch

from django.core.management.base import BaseCommand, CommandError

from apps.backtest.models import BacktestRun
from apps.backtest.tasks import run_backtest


def _parse_run_ids(raw_value):
    run_ids = []
    seen_ids = set()

    for raw_token in str(raw_value or '').split(','):
        token = raw_token.strip()
        if not token:
            continue

        if '-' in token:
            start_raw, end_raw = token.split('-', 1)
            try:
                start_id = int(start_raw)
                end_id = int(end_raw)
            except ValueError as exc:
                raise CommandError(f'Invalid run id range: {token}') from exc
            if end_id < start_id:
                raise CommandError(f'Run id range must be ascending: {token}')
            candidate_ids = range(start_id, end_id + 1)
        else:
            try:
                candidate_ids = [int(token)]
            except ValueError as exc:
                raise CommandError(f'Invalid run id: {token}') from exc

        for run_id in candidate_ids:
            if run_id <= 0:
                raise CommandError(f'Run id must be positive: {run_id}')
            if run_id in seen_ids:
                continue
            seen_ids.add(run_id)
            run_ids.append(run_id)

    if not run_ids:
        raise CommandError('run-ids must contain at least one id or range.')
    return run_ids


def _run_backtest_inline(root_run_id):
    pending_run_ids = [root_run_id]

    def _enqueue(run_id):
        pending_run_ids.append(int(run_id))

    with patch('apps.backtest.tasks.run_backtest.delay', side_effect=_enqueue):
        while pending_run_ids:
            current_run_id = pending_run_ids.pop(0)
            run_backtest(current_run_id)


class Command(BaseCommand):
    help = (
        'Clone existing backtests into new comparison runs, setting compare_backtest_run_id to the '
        'original run id and optionally executing the reruns inline.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--run-ids',
            required=True,
            help='Comma-separated ids and/or inclusive ranges, e.g. 525-532 or 525,526,530-532.',
        )
        parser.add_argument(
            '--name-suffix',
            default='comparison-rerun',
            help='Suffix appended to the cloned BacktestRun names.',
        )
        parser.add_argument(
            '--queue',
            action='store_true',
            help='Queue reruns asynchronously instead of executing them inline.',
        )

    def handle(self, *args, **options):
        source_run_ids = _parse_run_ids(options['run_ids'])
        source_runs = list(BacktestRun.objects.filter(id__in=source_run_ids).order_by('id'))
        source_run_map = {run.id: run for run in source_runs}
        missing_ids = [run_id for run_id in source_run_ids if run_id not in source_run_map]
        if missing_ids:
            raise CommandError(f'Backtest runs not found: {missing_ids}')

        name_suffix = str(options['name_suffix'] or '').strip()
        if not name_suffix:
            raise CommandError('name-suffix cannot be blank.')

        execution_mode = 'queued' if options['queue'] else 'executed'
        created_pairs = []

        for source_run_id in source_run_ids:
            source_run = source_run_map[source_run_id]
            cloned_parameters = deepcopy(source_run.parameters or {})
            cloned_parameters['compare_backtest_run_id'] = source_run.id

            cloned_run = BacktestRun.objects.create(
                user=source_run.user,
                name=f'{source_run.name} [{name_suffix}]',
                strategy_type=source_run.strategy_type,
                status=BacktestRun.Status.PENDING,
                start_date=source_run.start_date,
                end_date=source_run.end_date,
                initial_capital=source_run.initial_capital,
                cash=source_run.initial_capital,
                final_value=0,
                total_return=0,
                annualized_return=0,
                max_drawdown=0,
                sharpe_ratio=0,
                win_rate=0,
                total_trades=0,
                winning_trades=0,
                parameters=cloned_parameters,
                report={},
                error_message='',
                started_at=None,
                completed_at=None,
            )
            created_pairs.append((source_run.id, cloned_run.id))

            if options['queue']:
                run_backtest.delay(cloned_run.id)
            else:
                _run_backtest_inline(cloned_run.id)

        self.stdout.write(
            self.style.SUCCESS(
                f'Created {len(created_pairs)} comparison reruns ({execution_mode}).'
            )
        )
        for source_run_id, cloned_run_id in created_pairs:
            self.stdout.write(f'{source_run_id} -> {cloned_run_id}')