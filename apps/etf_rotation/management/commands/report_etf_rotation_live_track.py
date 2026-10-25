"""Render the live ETF book's forward record. Read-only unless ``--alert`` is passed.

The holdout is spent (plan §7.7), so the live ledger is the only source of genuinely
out-of-sample evidence left. This command is how it is read: realised pnl, win rate, open
marks, and -- only when a reference backtest is declared -- a drift verdict against that
row's own closed-trade distribution.

The default is a read. ``--alert`` is what writes a ``LIVE_DRIFT`` alert event, and the
monthly beat task is the only caller that passes it; an operator running this by hand cannot
accidentally page themselves.

Examples::

    python manage.py report_etf_rotation_live_track
    python manage.py report_etf_rotation_live_track --reference-backtest-id 256
    python manage.py report_etf_rotation_live_track --reference-backtest-id 256 --alert
"""

from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand

from apps.etf_rotation import live_track


def _pct(value) -> str:
    return 'n/a' if value is None else f'{float(value):+.2%}'


def _yuan(value) -> str:
    return 'n/a' if value is None else f'{float(value):,.2f}'


#: Start of the sealed forward window (``docs/plans/etf-rotation-next-phases-2026-10-24.md`` §8,
#: 2026-10-25). A recorded decision, not a knob: the ledger accrues from here and nothing
#: earlier can be called out-of-sample again.
FORWARD_WINDOW_START = date(2026, 10, 25)


class Command(BaseCommand):
    help = 'Report the live ETF rotation book and its drift against a declared reference backtest.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reference-backtest-id', type=int, default=None,
            help='ETFRotationBacktest id the live book is judged against. Default: '
                 'settings.ETF_LIVE_REFERENCE_BACKTEST_ID (0 = descriptive report only).',
        )
        parser.add_argument(
            '--win-rate-tolerance', type=float, default=None,
            help='Absolute win-rate deviation that counts as drift. '
                 'Default: settings.ETF_LIVE_DRIFT_WIN_RATE_TOLERANCE (0.20).',
        )
        parser.add_argument(
            '--mean-return-tolerance', type=float, default=None,
            help='Absolute mean-trade-return deviation that counts as drift. '
                 'Default: settings.ETF_LIVE_DRIFT_MEAN_RETURN_TOLERANCE (0.03).',
        )
        parser.add_argument(
            '--min-closed-trades', type=int, default=None,
            help='Closed trades required before a drift verdict is made. '
                 'Default: settings.ETF_LIVE_DRIFT_MIN_CLOSED_TRADES (10).',
        )
        parser.add_argument(
            '--alert', action='store_true',
            help='Write a LIVE_DRIFT alert event when a book breaches its tolerance.',
        )

    def handle(self, *args, **options):
        # Display only: the sealed window never enters a calculation. It is here so a reader
        # knows which dates are live evidence and which belong to the released holdout.
        self.stdout.write(
            f'forward window: sealed from {FORWARD_WINDOW_START.isoformat()} -- readings before '
            'it are the released holdout, not live evidence.'
        )
        summary = live_track.build_live_track(
            reference_backtest_id=options['reference_backtest_id'],
            win_rate_tolerance=options['win_rate_tolerance'],
            mean_return_tolerance=options['mean_return_tolerance'],
            min_closed_trades=options['min_closed_trades'],
        )
        reference = summary['reference']
        if reference is None:
            self.stdout.write(self.style.WARNING(
                'no reference backtest configured: this is a descriptive report, not a '
                'drift verdict. Set ETF_LIVE_REFERENCE_BACKTEST_ID or pass '
                '--reference-backtest-id to judge the book against a row.'
            ))
        else:
            self.stdout.write(
                f"reference: ETFRotationBacktest {reference['backtest_id']} "
                f"\"{reference['name']}\" -- closed={reference['closed_count']} "
                f"win_rate={_pct(reference.get('win_rate'))} "
                f"mean_return={_pct(reference.get('mean_return'))}"
            )
        self.stdout.write('book     closed  open  win_rate  mean_return  realised_pnl  open_mv  open_pnl  drift')
        for signal_type, book in summary['books'].items():
            drift = book['drift']
            if not drift.get('measured'):
                verdict = 'n/a'
            elif drift['breached']:
                verdict = 'BREACH'
            else:
                verdict = 'ok'
            self.stdout.write(
                f'{signal_type:<8} {book["closed_count"]:>6} {book["open_count"]:>5}  '
                f'{_pct(book["win_rate"]):>8}  {_pct(book["mean_return"]):>11}  '
                f'{_yuan(book["realised_pnl"]):>12}  {_yuan(book["open_market_value"]):>7}  '
                f'{_yuan(book["open_unrealised_pnl"]):>8}  {verdict}'
            )
            if drift.get('measured') and drift['breached']:
                for reason in drift['reasons']:
                    self.stdout.write(f'  {signal_type}: {reason}')
            elif not drift.get('measured'):
                self.stdout.write(f'  {signal_type}: {drift.get("reason")}')
        if options['alert']:
            event_id = live_track.dispatch_drift_alert(summary)
            if event_id is None:
                self.stdout.write('alert: nothing breached; no LIVE_DRIFT event written.')
            else:
                self.stdout.write(self.style.SUCCESS(
                    f'alert: LIVE_DRIFT event {event_id} written and queued for dispatch.'
                ))
        else:
            self.stdout.write('alert: none (pass --alert to write a LIVE_DRIFT event on a breach).')
