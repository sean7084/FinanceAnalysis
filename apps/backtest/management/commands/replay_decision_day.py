"""Replay one decision day for measurement, with every write rolled back.

The campaign's pre-registration requires the decision-day A/B arm to run "inside a
rolled-back transaction", because the replay path writes ``BacktestRun`` rows and backfills
trade decisions. This command runs ``run_core_backtest_matrix`` for a single session inside
``run_in_rolled_back_transaction`` and then asserts the row counts came back unchanged, so a
measurement cannot silently leave state behind.

The ``--output-dir`` export is written to disk and therefore SURVIVES the rollback -- that is
the measurement artifact.

Both arms (baseline and candidate) must be driven through this command so their conditions
match; pair them A,B,B,A, order swapped, per the pre-registration.
"""
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.backtest.decision_replay import run_in_rolled_back_transaction
from apps.backtest.models import BacktestRun


class Command(BaseCommand):
    help = 'Replay one decision day through the matrix engine and ROLL THE WRITES BACK.'

    def add_arguments(self, parser):
        parser.add_argument('--date', required=True, help='Session date (YYYY-MM-DD).')
        parser.add_argument('--output-dir', required=True,
                            help='Where the run artifacts go. Written to disk; survives the rollback.')
        parser.add_argument('--sources', default='heuristic', help='Comma-separated prediction sources.')

    def handle(self, *args, **options):
        day = options['date']
        output_dir = options['output_dir']
        sources = options['sources']

        from apps.prediction import historical_features as hf

        before = BacktestRun.objects.count()
        stats_before = dict(hf.INDICATOR_PREFETCH_STATS)

        def operation():
            call_command(
                'run_core_backtest_matrix',
                start_date=day,
                end_date=day,
                sources=sources,
                execute_inline=True,
                output_dir=output_dir,
                verbosity=0,
            )
            return True

        started = time.perf_counter()
        run_in_rolled_back_transaction(operation)
        elapsed = time.perf_counter() - started
        stats_after = dict(hf.INDICATOR_PREFETCH_STATS)
        delta = {k: stats_after[k] - stats_before.get(k, 0) for k in stats_after}

        after = BacktestRun.objects.count()
        if after != before:
            # Should be unreachable: the wrapper rolls back. If it ever fires, the rollback
            # contract is broken and the measurement is not admissible.
            raise CommandError(
                f'rollback failed: BacktestRun count moved {before} -> {after}. '
                'The decision-day measurement is NOT valid until this is fixed.'
            )

        self.stdout.write(
            f'replayed {day} inside a rolled-back transaction; BacktestRun count unchanged '
            f'({before}); artifacts in {output_dir}'
        )
        self.stdout.write(f'elapsed_seconds={elapsed:.3f}')
        self.stdout.write(
            'indicator_prefetch_stats=' + ','.join(f'{k}={v}' for k, v in sorted(delta.items()))
        )