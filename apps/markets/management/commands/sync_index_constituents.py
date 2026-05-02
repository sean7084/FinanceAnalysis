from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.markets.tasks import DEFAULT_INDEX_CODES, sync_index_constituent_universe


class Command(BaseCommand):
    help = 'Sync CSI 300 + CSI A500 index constituents, persist membership history, refresh current tags, and dispatch unique asset syncs.'

    def add_arguments(self, parser):
        parser.add_argument('--index-codes', default=','.join(DEFAULT_INDEX_CODES))
        parser.add_argument(
            '--start-date',
            default=(timezone.now().date() - timedelta(days=30)).isoformat(),
            help='Inclusive start date for index_weight snapshots (YYYY-MM-DD).',
        )
        parser.add_argument(
            '--end-date',
            default=timezone.now().date().isoformat(),
            help='Inclusive end date for index_weight snapshots (YYYY-MM-DD).',
        )
        parser.add_argument(
            '--skip-sync-dispatch',
            action='store_true',
            help='Persist membership/tags only and skip dispatching sync_asset_history tasks.',
        )
        parser.add_argument(
            '--force-floor-backfill',
            action='store_true',
            help='Dispatch sync_asset_history with force_floor_backfill=True.',
        )
        parser.add_argument(
            '--dispatch-changed-assets-only',
            action='store_true',
            help='Dispatch only assets whose current CSI300/CSIA500 memberships changed.',
        )

    def _parse_date(self, value, name):
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc

    def handle(self, *args, **options):
        start_date = self._parse_date(options['start_date'], 'start-date')
        end_date = self._parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        summary = sync_index_constituent_universe(
            index_codes=options['index_codes'],
            start_date=start_date,
            end_date=end_date,
            dispatch_assets=not options['skip_sync_dispatch'],
            force_floor_backfill=options['force_floor_backfill'],
            dispatch_changed_assets_only=options['dispatch_changed_assets_only'],
        )

        self.stdout.write(
            f"index_codes={','.join(summary['index_codes'])} "
            f"current_counts={summary['current_constituent_counts']}"
        )
        self.stdout.write(
            f"current_union_count={summary['current_union_count']} overlap_count={summary['overlap_count']} "
            f"new_assets={summary['new_assets']} existing_assets={summary['existing_assets']}"
        )
        self.stdout.write(
            f"historical_membership_rows_seen={summary['historical_membership_rows_seen']} "
            f"membership_rows_created={summary['membership_rows_created']} "
            f"tagged_assets_updated={summary['tagged_assets_updated']} "
            f"dispatched_assets={summary['dispatched_assets']}"
        )
        self.stdout.write(f"latest_trade_dates={summary['latest_trade_dates']}")
        self.stdout.write(self.style.SUCCESS('Index constituent sync complete.'))