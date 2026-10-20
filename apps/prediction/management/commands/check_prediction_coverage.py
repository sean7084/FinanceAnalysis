"""Report stored prediction coverage per open trading date. Read-only."""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.prediction.reconciliation import (
    ENSEMBLE,
    LIGHTGBM,
    RECONCILE_SOURCES,
    coverage_gaps,
)


class Command(BaseCommand):
    help = (
        'List which universe members have no stored prediction for each open '
        'trading date in the lookback window. Writes nothing.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--as-of', help='Last date to check (default: today).')
        parser.add_argument(
            '--lookback',
            type=int,
            dest='lookback_trading_days',
            help=f"Trading days to check (default: settings.PREDICTION_RECONCILE_LOOKBACK_TRADING_DAYS = {settings.PREDICTION_RECONCILE_LOOKBACK_TRADING_DAYS}).",
        )
        parser.add_argument(
            '--source',
            action='append',
            choices=[ENSEMBLE, LIGHTGBM],
            dest='sources',
            help='Restrict to one source. Repeatable.',
        )
        parser.add_argument('--json', action='store_true', help='Machine-readable output.')
        parser.add_argument(
            '--fail-on-gaps',
            action='store_true',
            help='Exit non-zero if any date is missing rows, for use as a gate.',
        )

    def handle(self, *args, **options):
        sources = tuple(options['sources']) if options['sources'] else RECONCILE_SOURCES
        try:
            gaps = coverage_gaps(
                as_of=options['as_of'],
                lookback_trading_days=options['lookback_trading_days'],
                sources=sources,
            )
        except (ValueError, TypeError) as err:
            raise CommandError(f'--as-of must be an ISO date: {err}') from err

        if not gaps:
            self.stderr.write(self.style.WARNING('No open trading dates in the window -- is the calendar populated?'))

        if options['json']:
            payload = [
                {
                    'trade_date': gap.trade_date.isoformat(),
                    'source': gap.source,
                    'universe_size': gap.universe_size,
                    'stored_assets': gap.stored_asset_count,
                    'missing_assets': len(gap.missing_asset_ids),
                }
                for gap in gaps
            ]
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            for gap in gaps:
                label = self.style.SUCCESS if gap.is_complete else self.style.ERROR
                self.stdout.write(label(gap.summary()))

        missing = [gap for gap in gaps if not gap.is_complete]
        if options['fail_on_gaps'] and missing:
            raise SystemExit(1)
