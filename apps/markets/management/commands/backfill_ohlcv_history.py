from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.date_floor import get_historical_data_floor
from apps.markets.models import Asset
from apps.markets.tasks import sync_asset_history


class Command(BaseCommand):
    help = 'Backfill OHLCV history from the configured floor date using TuShare.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=get_historical_data_floor().isoformat())
        parser.add_argument('--symbols', default='')
        parser.add_argument('--limit-assets', type=int, default=0)
        parser.add_argument('--queue', action='store_true')

    def _parse_date(self, value, name):
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc

    def handle(self, *args, **options):
        floor_date = get_historical_data_floor()
        requested_start = self._parse_date(options['start_date'], 'start-date')
        if requested_start < floor_date:
            raise CommandError(f'start-date cannot be earlier than HISTORICAL_DATA_FLOOR={floor_date}.')

        symbols = [token.strip() for token in str(options['symbols'] or '').split(',') if token.strip()]
        queryset = Asset.objects.select_related('market').filter(listing_status=Asset.ListingStatus.ACTIVE).order_by('ts_code')
        if symbols:
            queryset = queryset.filter(symbol__in=symbols)

        limit_assets = int(options['limit_assets'] or 0)
        if limit_assets > 0:
            queryset = queryset[:limit_assets]

        total = queryset.count() if hasattr(queryset, 'count') else len(list(queryset))
        processed = 0

        for asset in queryset:
            market_code = asset.market.code
            if options['queue']:
                sync_asset_history.delay(asset.symbol, asset.name, market_code, True)
                result = 'queued'
            else:
                sync_asset_history(asset.symbol, asset.name, market_code, True)
                result = 'executed'

            processed += 1
            self.stdout.write(f'[{processed}/{total}] {asset.ts_code}: {result}')

        self.stdout.write(self.style.SUCCESS(
            f'OHLCV backfill dispatched for {processed} assets from floor {floor_date}. queue={options["queue"]}'
        ))
