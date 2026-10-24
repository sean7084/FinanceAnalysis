"""Mark the live rotation book to the latest close and close displaced legs.

Runs after the 19:45 signal job so a leg the newest signal no longer selects leaves the
book on the session its replacement was decided. A day without a new signal only marks.
"""

from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.etf_rotation.position_ledger import mark_positions


class Command(BaseCommand):
    help = ('Mark OPEN ETF rotation positions to the newest close and close legs displaced '
            'by a newer signal of the same type.')

    def add_arguments(self, parser):
        parser.add_argument('--date', default=None,
                            help='Mark date (YYYY-MM-DD). Default: the newest ETFDaily session.')

    def handle(self, *args, **options):
        mark_date = options['date']
        if mark_date:
            try:
                date.fromisoformat(str(mark_date).strip())
            except ValueError as exc:
                raise CommandError(f'--date must be YYYY-MM-DD, got {mark_date!r}.') from exc
        summary = mark_positions(mark_date)

        if summary['date'] is None:
            self.stdout.write(self.style.WARNING('no ETFDaily bar exists yet; nothing to mark.'))
            return
        self.stdout.write(
            f"ledger {summary['date']}: marked={summary['marked_count']} "
            f"closed={summary['closed_count']} "
            f"skipped_no_price={len(summary['skipped_no_price'])} "
            f"latest_signals={summary['latest_signals']} "
            f"realised_pnl_total={summary['realised_pnl_total']:.2f}"
        )
        for item in summary['closed']:
            self.stdout.write(
                f"closed {item['ts_code']} {item['name']}: "
                f"{item['entry_date']}@{item['entry_price']:.4f} -> "
                f"{item['exit_date']}@{item['exit_price']:.4f} "
                f"shares={item['shares']} pnl={item['pnl']} "
                f"return={item['return_pct']} reason={item['exit_reason']}"
            )
        if summary['skipped_no_price']:
            self.stdout.write(self.style.WARNING(
                'no bar at or before the mark date for: ' + ', '.join(summary['skipped_no_price'])
            ))
