"""Report or clear backtest rows whose worker is gone (#6).

``apps/core/task_health`` already decides whether a ``RUNNING`` row is orphaned; it just
never had a caller. This command is that caller, for both the stock ``BacktestRun`` and the
ETF ``ETFRotationBacktest`` row type. It is dry-run unless an explicit verb is given: changing
a shared run's state by default would be a much larger blast radius than the gap it fixes.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.backtest.models import BacktestRun
from apps.core.task_health import get_backtest_run_task_owner_state
from apps.etf_rotation.models import ETFRotationBacktest


class Command(BaseCommand):
    help = 'Report or clear RUNNING backtest rows whose Celery task is gone.'

    def add_arguments(self, parser):
        parser.add_argument('--fail', action='store_true',
                            help='Mark orphaned rows FAILED and clear their task owner.')
        parser.add_argument('--restart', action='store_true',
                            help='Mark orphaned rows PENDING and clear their task owner so the '
                                 'normal dispatcher can start them again.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report only. This is the default when no verb is given.')

    def handle(self, *args, **options):
        if options['fail'] and options['restart']:
            raise ValueError('--fail and --restart are mutually exclusive.')
        verb = 'fail' if options['fail'] else ('restart' if options['restart'] else 'dry-run')
        rows = []
        for model in (BacktestRun, ETFRotationBacktest):
            for run in model.objects.filter(status='RUNNING').order_by('id'):
                state = get_backtest_run_task_owner_state(run)
                if state.get('has_stale_task_owner'):
                    rows.append((model.__name__, run, state))
        if not rows:
            self.stdout.write('no orphaned RUNNING rows')
            return
        self.stdout.write(f'{len(rows)} orphaned RUNNING row(s); verb={verb}')
        for kind, run, state in rows:
            self.stdout.write(
                f'  {kind} id={run.id} task={run.current_task_id or "-"!r} '
                f'state={state.get("task_state") or "-"} '
                f'age={state.get("task_age_seconds")}s'
            )
            if verb == 'fail':
                run.status = 'FAILED'
            elif verb == 'restart':
                run.status = 'PENDING'
            else:
                continue
            run.current_task_id = ''
            run.pending_control_action = 'NONE'
            run.save(update_fields=['status', 'current_task_id', 'pending_control_action',
                                    'updated_at'])
        if verb == 'dry-run':
            self.stdout.write('dry run: nothing changed; pass --fail or --restart to act')
