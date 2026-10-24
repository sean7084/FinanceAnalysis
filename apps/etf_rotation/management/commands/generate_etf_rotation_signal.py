"""Write the ETF rotation signal for one trade date, overwriting any earlier one.

Thin wrapper over :func:`apps.etf_rotation.signals.generate_rotation_signal`, which holds the
pool read, the ranking and the persistence. Two things the command owns, and only those:

* Re-running a date is safe. The signal is upserted on the model's
  ``unique_together = ('date', 'signal_type')`` key, so a second run overwrites the first
  instead of creating a row for "the latest signal" to choose between -- including when the
  second run asks for a different ``--model-type`` or ``--max-positions``. The day's
  ``ETFRotationPosition`` legs are rebuilt from the new plan, so no leg from the old ranking
  stays attached to the signal.
* ``--dry-run`` computes everything, including the weight-sum check, and writes nothing.

The return value is the summary dict, which is what
``tasks.generate_etf_rotation_signal_daily`` forwards to the Celery result backend.

Examples::

    python manage.py generate_etf_rotation_signal --dry-run
    python manage.py generate_etf_rotation_signal --date 2018-12-28 --signal-type MONTHLY
    python manage.py generate_etf_rotation_signal --model-type ENSEMBLE --momentum-lookback 120
"""

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand, CommandError, OutputWrapper
from django.utils import timezone

from apps.etf_rotation import signal_runs, signals
from apps.etf_rotation.models import ETFRotationSignal, ETFRotationSignalRun
from apps.etf_rotation.rotation_engine import MODEL_TYPES

logger = logging.getLogger(__name__)


class SummaryOutput(OutputWrapper):
    """``BaseCommand.execute`` hands ``handle()``'s return value to ``stdout.write``, and
    ``OutputWrapper.write`` only accepts a string. This command returns the summary dict the
    Celery task forwards, so a non-string write is dropped -- the operator-facing rendering
    already happened in ``_render``.
    """

    def write(self, msg='', style_func=None, ending=None):
        if isinstance(msg, str):
            super().write(msg, style_func, ending)


class Command(BaseCommand):
    help = 'Rank the point-in-time ETF pool and write the rotation signal for one date.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            help='Trade date (YYYY-MM-DD). Default: newest session with a non-empty eligible cross-section.',
        )
        parser.add_argument(
            '--signal-type', default=ETFRotationSignal.SignalType.MONTHLY,
            choices=list(ETFRotationSignal.SignalType.values),
            help='Rebalance cadence the row is stored under (default MONTHLY).',
        )
        parser.add_argument(
            '--model-type', default=ETFRotationSignal.ModelType.RULE, choices=list(MODEL_TYPES),
            help='Scorer: the rule blend, a trained LIGHTGBM/LSTM, or their ensemble. An ML type '
                 'with no active ModelVersion fails instead of quietly falling back to RULE.',
        )
        parser.add_argument(
            '--max-positions', type=int,
            help='Slots in the book. Default: settings.ETF_MAX_POSITIONS (5).',
        )
        parser.add_argument(
            '--momentum-lookback', type=int,
            help='Momentum horizon in sessions, e.g. 60 -> mom_60d. Default: 60; the nearest stored '
                 'horizon is substituted and recorded in metadata when it is absent.',
        )
        parser.add_argument(
            '--dry-run', action='store_true', help='Score, rank, verify and report; write nothing.',
        )

    def handle(self, *args, **options) -> dict:
        started = time.monotonic()
        try:
            summary = signals.generate_rotation_signal(
                signal_type=options['signal_type'],
                model_type=options['model_type'],
                trade_date=options['date'],
                max_positions=options['max_positions'],
                momentum_lookback=options['momentum_lookback'],
                dry_run=options['dry_run'],
            )
        except signals.SignalGenerationError as error:
            self._record(options, ETFRotationSignalRun.Outcome.REFUSED, started, error=error)
            raise CommandError(str(error)) from error
        except Exception as error:
            self._record(options, ETFRotationSignalRun.Outcome.FAILED, started, error=error)
            raise

        self._record(options, ETFRotationSignalRun.Outcome.OK, started, summary=summary)
        if options['verbosity']:
            self._render(summary)
        # The framework's write of the return value goes through a wrapper that tolerates a
        # dict; see ``SummaryOutput``.
        self.stdout = SummaryOutput(self.stdout)
        return summary

    def _record(self, options, outcome, started, *, error=None, summary=None) -> None:
        """Persist this attempt. ``--dry-run`` keeps its promise of writing nothing.

        A ledger failure must not mask the signal failure that is being reported, so the
        record is best-effort: the message already says what went wrong and the command's
        exit status is unchanged.
        """
        if options['dry_run']:
            return
        day = signals._as_date(options.get('date')) or signal_runs.latest_snapshot_date() \
            or timezone.localdate()
        try:
            diagnostics = signal_runs.build_diagnostics(day)
        except Exception:
            diagnostics = {}
        reason_code = ''
        message = ''
        if error is not None:
            reason_code = str(getattr(error, 'reason_code', '') or 'unexpected_error')
            message = str(error)
            diagnostics.update(getattr(error, 'diagnostics', {}) or {})
        elif summary is not None:
            diagnostics.update({
                'model_version': summary.get('model_version') or '',
                'pool_size': summary.get('pool_size'),
                'scored_size': summary.get('scored_size'),
                'ranked_size': summary.get('ranked_size'),
                'positions_written': summary.get('positions_written'),
                'legs_without_price': summary.get('legs_without_price') or [],
            })
        try:
            signal_runs.record_attempt(
                day=day,
                signal_type=options['signal_type'],
                outcome=outcome,
                reason_code=reason_code,
                message=message,
                diagnostics=diagnostics,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        except Exception:
            logger.exception('Could not persist the ETF signal run for %s (%s).', day, outcome)

    def _render(self, summary: dict) -> None:
        self.stdout.write('')
        self.stdout.write(
            f'{summary["signal_type"]} {summary["model_type"]} rotation plan for {summary["date"]} '
            f'(model_version={summary["model_version"] or "-"}, ranking column {summary["momentum_column"]}, '
            f'max_positions {summary["max_positions"]})'
        )
        if not summary['legs']:
            self.stdout.write(
                f'  no risk legs ({summary["defensive_reason"]}); '
                f'{summary["defensive_allocation"]:.1%} in {summary["defensive_asset"]}'
            )
        else:
            header = f'  {"ts_code":<11} {"weight":>8}  reason'
            self.stdout.write(header)
            self.stdout.write('  ' + '-' * (len(header) - 2))
            for item in summary['legs']:
                self.stdout.write(f'  {item["ts_code"]:<11} {item["weight"]:>8.4f}  {item["reason"]}')
        self.stdout.write(
            f'  book: {len(summary["legs"])} leg(s) + {summary["defensive_allocation"]:.4f} defensive '
            f'({summary["defensive_reason"]}) = {summary["weights_total"]:.6f}'
        )
        turnover = summary['turnover']
        self.stdout.write(
            f'  pool {summary["pool_size"]} -> scored {summary["scored_size"]} -> ranked '
            f'{summary["ranked_size"]}; turnover mode={turnover["mode"]} entries={turnover["entries"]} '
            f'exits={turnover["exits"]} swaps_used={turnover["swaps_used"]}'
        )
        self.stdout.write(f'  benchmark 20d return: {summary["benchmark_return_20d"]}')
        if summary['dry_run']:
            self.stdout.write(self.style.WARNING('  dry run: no signal or position row written'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'  {"created" if summary["created"] else "overwrote"} ETFRotationSignal '
                f'{summary["signal_pk"]}: {summary["positions_written"]} position row(s), '
                f'{summary["positions_replaced"]} replaced'
                + (f', legs with no bar: {summary["legs_without_price"]}' if summary['legs_without_price'] else '')
            ))
        self.stdout.write('')
