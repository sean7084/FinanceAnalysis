import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from django.utils import timezone

from apps.analytics.indicator_warmup import (
    DEFAULT_WARMUP_TECHNICAL_INDICATORS,
    MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS,
    max_technical_indicator_warmup_lookback,
    technical_indicator_warmup_prefill_calendar_days,
    technical_indicator_warmup_prefill_start_date,
)
from apps.core.date_floor import get_historical_data_floor
from apps.markets.benchmarking import point_in_time_union_asset_ids_by_dates
from apps.markets.models import Asset, ExchangeTradingCalendar
from apps.markets.tasks import sync_asset_history


class Command(BaseCommand):
    help = 'Backfill OHLCV history from the configured floor date using TuShare.'
    CONTINUITY_ROW_PREFIX = 'ohlcv,daily_bar,continuity_gap,asset_window,'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=get_historical_data_floor().isoformat())
        parser.add_argument('--end-date', default='')
        parser.add_argument('--csv-file', default='')
        parser.add_argument('--symbols', default='')
        parser.add_argument('--limit-assets', type=int, default=0)
        parser.add_argument('--queue', action='store_true')
        parser.add_argument(
            '--technical-indicator-warmup',
            action='store_true',
            help='Extend the OHLCV repair window earlier by the maximum technical-indicator warm-up lookback and allow that bounded repair to cross the historical floor.',
        )
        parser.add_argument(
            '--effective-universe-entry-warmup',
            action='store_true',
            help='Backfill OHLCV warm-up windows ending on each asset\'s first effective-universe date in the requested range so technical indicators can initialize when the asset first enters PIT validation scope.',
        )

    def _parse_date(self, value, name):
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc

    def _parse_optional_date(self, value, name):
        token = str(value or '').strip()
        if not token:
            return None
        return self._parse_date(token, name)

    def _market_code_from_ts_code(self, ts_code):
        token = str(ts_code or '').strip().upper()
        if '.' not in token:
            raise CommandError(f'Invalid asset_ts_code in CSV: {ts_code}')
        suffix = token.split('.', 1)[1]
        if suffix == 'SH':
            return 'SSE'
        if suffix == 'SZ':
            return 'SZSE'
        if suffix == 'BJ':
            return 'BSE'
        raise CommandError(f'Unsupported asset_ts_code suffix in CSV: {ts_code}')

    def _merge_windows(self, windows):
        merged = []
        for window_start, window_end in sorted(windows):
            if not merged:
                merged.append([window_start, window_end])
                continue
            current_start, current_end = merged[-1]
            if window_start <= current_end + timedelta(days=1):
                merged[-1][1] = max(current_end, window_end)
                continue
            merged.append([window_start, window_end])
        return [(window_start, window_end) for window_start, window_end in merged]

    def _load_csv_repairs(self, csv_file, symbols=None, limit_assets=0):
        csv_path = Path(str(csv_file)).expanduser()
        if not csv_path.exists():
            raise CommandError(f'csv-file does not exist: {csv_file}')

        required_columns = {'asset_symbol', 'asset_ts_code', 'asset_name', 'gap_start', 'gap_end'}
        requested_symbols = {token.strip() for token in (symbols or []) if token.strip()}
        windows_by_ts_code = defaultdict(list)
        asset_rows = {}

        for row in self._iter_continuity_report_rows(csv_path, required_columns):
            asset_symbol = str(row.get('asset_symbol') or '').strip()
            if not asset_symbol:
                continue
            if requested_symbols and asset_symbol not in requested_symbols:
                continue

            asset_ts_code = str(row.get('asset_ts_code') or '').strip().upper()
            if not asset_ts_code:
                raise CommandError('csv-file contains a row with blank asset_ts_code.')

            gap_start = self._parse_date(row.get('gap_start'), 'gap_start')
            gap_end = self._parse_date(row.get('gap_end'), 'gap_end')
            if gap_end < gap_start:
                raise CommandError(
                    f'csv-file contains an invalid gap window for {asset_ts_code}: {gap_start}..{gap_end}'
                )

            asset_rows[asset_ts_code] = {
                'symbol': asset_symbol,
                'ts_code': asset_ts_code,
                'name': str(row.get('asset_name') or asset_symbol).strip() or asset_symbol,
                'market_code': self._market_code_from_ts_code(asset_ts_code),
                'list_date': self._parse_optional_date(row.get('list_date'), 'list_date'),
                'delist_date': self._parse_optional_date(row.get('delist_date'), 'delist_date'),
            }
            asset_rows[asset_ts_code]['listing_status'] = (
                Asset.ListingStatus.DELISTED
                if asset_rows[asset_ts_code]['delist_date'] is not None
                else Asset.ListingStatus.ACTIVE
            )
            windows_by_ts_code[asset_ts_code].append((gap_start, gap_end))

        repairs = []
        sorted_ts_codes = sorted(windows_by_ts_code.keys())
        if limit_assets > 0:
            sorted_ts_codes = sorted_ts_codes[:limit_assets]

        for asset_ts_code in sorted_ts_codes:
            asset_info = asset_rows[asset_ts_code]
            for gap_start, gap_end in self._merge_windows(windows_by_ts_code[asset_ts_code]):
                repairs.append({
                    **asset_info,
                    'gap_start': gap_start,
                    'gap_end': gap_end,
                })
        return repairs

    def _iter_continuity_report_rows(self, csv_path, required_columns):
        raw_lines = csv_path.read_text(encoding='utf-8').splitlines()
        if not raw_lines:
            raise CommandError(f'csv-file is empty: {csv_path}')

        header = raw_lines[0].lstrip('\ufeff').rstrip('\r')
        fieldnames = set(next(csv.reader([header])))
        missing_columns = sorted(required_columns.difference(fieldnames))
        if missing_columns:
            raise CommandError(
                f'csv-file is missing required columns: {", ".join(missing_columns)}'
            )

        body = '\n'.join(line.rstrip('\r') for line in raw_lines[1:] if line.strip())
        parts = body.split(self.CONTINUITY_ROW_PREFIX)
        for part in parts[1:]:
            row_text = f'{self.CONTINUITY_ROW_PREFIX}{part.strip()}'.splitlines()[0].strip()
            reader = csv.DictReader([header, row_text])
            row = next(reader, None)
            if row:
                yield row

    def _dispatch_asset_repair(
        self,
        asset_symbol,
        asset_name,
        market_code,
        gap_start,
        gap_end,
        queue=False,
        list_date=None,
        listing_status=Asset.ListingStatus.ACTIVE,
        delist_date=None,
        allow_pre_floor_repair=False,
    ):
        sync_kwargs = {
            'repair_start_date': gap_start.isoformat(),
            'repair_end_date': gap_end.isoformat(),
            'list_date': list_date.isoformat() if list_date else None,
            'listing_status': listing_status,
            'delist_date': delist_date.isoformat() if delist_date else None,
        }
        if allow_pre_floor_repair:
            sync_kwargs['allow_pre_floor_repair'] = True
        if queue:
            sync_asset_history.delay(asset_symbol, asset_name, market_code, True, **sync_kwargs)
            return 'queued'
        sync_asset_history(asset_symbol, asset_name, market_code, True, **sync_kwargs)
        return 'executed'

    def _handle_csv_repairs(self, options):
        if options.get('technical_indicator_warmup') or options.get('effective_universe_entry_warmup'):
            raise CommandError('--technical-indicator-warmup and --effective-universe-entry-warmup are not supported together with --csv-file.')

        symbols = [token.strip() for token in str(options['symbols'] or '').split(',') if token.strip()]
        repairs = self._load_csv_repairs(
            options['csv_file'],
            symbols=symbols,
            limit_assets=int(options['limit_assets'] or 0),
        )
        total = len(repairs)
        processed = 0

        for repair in repairs:
            result = self._dispatch_asset_repair(
                repair['symbol'],
                repair['name'],
                repair['market_code'],
                repair['gap_start'],
                repair['gap_end'],
                queue=options['queue'],
                list_date=repair['list_date'],
                listing_status=repair['listing_status'],
                delist_date=repair['delist_date'],
            )
            processed += 1
            self.stdout.write(
                f'[{processed}/{total}] {repair["ts_code"]} {repair["gap_start"]}..{repair["gap_end"]}: {result}'
            )

        self.stdout.write(self.style.SUCCESS(
            f'OHLCV backfill dispatched for {processed} repair windows from {options["csv_file"]}. queue={options["queue"]}'
        ))

    def _load_effective_universe_entry_repairs(self, start_date, end_date, symbols=None, limit_assets=0):
        trading_dates = list(
            ExchangeTradingCalendar.objects.filter(
                trade_date__gte=start_date,
                trade_date__lte=end_date,
            )
            .order_by('trade_date')
            .values_list('trade_date', flat=True)
            .distinct()
        )
        if not trading_dates:
            raise CommandError('No ExchangeTradingCalendar rows found in the requested range for --effective-universe-entry-warmup.')

        effective_universe_by_date = point_in_time_union_asset_ids_by_dates(trading_dates)
        first_effective_dates_by_asset_id = {}
        for trading_date in trading_dates:
            for asset_id in effective_universe_by_date.get(trading_date, set()):
                first_effective_dates_by_asset_id.setdefault(asset_id, trading_date)

        if not first_effective_dates_by_asset_id:
            return []

        queryset = Asset.objects.select_related('market').filter(id__in=first_effective_dates_by_asset_id.keys()).order_by('ts_code')
        if symbols:
            queryset = queryset.filter(Q(symbol__in=symbols) | Q(ts_code__in=symbols))

        limit_assets = int(limit_assets or 0)
        if limit_assets > 0:
            queryset = queryset[:limit_assets]

        repairs = []
        for asset in queryset:
            first_effective_date = first_effective_dates_by_asset_id.get(asset.id)
            if first_effective_date is None:
                continue
            repairs.append({
                'asset': asset,
                'first_effective_date': first_effective_date,
                'gap_start': technical_indicator_warmup_prefill_start_date(
                    first_effective_date,
                    DEFAULT_WARMUP_TECHNICAL_INDICATORS,
                ),
                'gap_end': first_effective_date,
            })
        return repairs

    def _handle_effective_universe_entry_warmup(self, options, start_date, end_date):
        symbols = [token.strip() for token in str(options['symbols'] or '').split(',') if token.strip()]
        warmup_lookback = max_technical_indicator_warmup_lookback(DEFAULT_WARMUP_TECHNICAL_INDICATORS)
        prefill_calendar_days = technical_indicator_warmup_prefill_calendar_days(
            DEFAULT_WARMUP_TECHNICAL_INDICATORS,
            minimum_calendar_days=MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS,
        )
        repairs = self._load_effective_universe_entry_repairs(
            start_date,
            end_date,
            symbols=symbols,
            limit_assets=int(options['limit_assets'] or 0),
        )

        total = len(repairs)
        processed = 0
        for repair in repairs:
            asset = repair['asset']
            result = self._dispatch_asset_repair(
                asset.symbol,
                asset.name,
                asset.market.code,
                repair['gap_start'],
                repair['gap_end'],
                queue=options['queue'],
                list_date=asset.list_date,
                listing_status=asset.listing_status,
                delist_date=asset.delist_date,
                allow_pre_floor_repair=True,
            )
            processed += 1
            self.stdout.write(
                f'[{processed}/{total}] {asset.ts_code} '
                f'first_effective_universe_date={repair["first_effective_date"]} '
                f'{repair["gap_start"]}..{repair["gap_end"]}: {result}'
            )

        self.stdout.write(self.style.SUCCESS(
            'OHLCV effective-universe entry warm-up dispatched '
            f'for {processed} assets over {start_date}..{end_date}. '
            f'lookback_trading_days={warmup_lookback} '
            f'calendar_prefill_days={prefill_calendar_days}. queue={options["queue"]}'
        ))

    def handle(self, *args, **options):
        if options.get('csv_file'):
            return self._handle_csv_repairs(options)

        floor_date = get_historical_data_floor()
        requested_start = self._parse_date(options['start_date'], 'start-date')
        allow_pre_floor_repair = False
        effective_start = requested_start
        if options.get('technical_indicator_warmup'):
            warmup_lookback = max_technical_indicator_warmup_lookback(DEFAULT_WARMUP_TECHNICAL_INDICATORS)
            prefill_calendar_days = technical_indicator_warmup_prefill_calendar_days(
                DEFAULT_WARMUP_TECHNICAL_INDICATORS,
                minimum_calendar_days=MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS,
            )
            effective_start = technical_indicator_warmup_prefill_start_date(requested_start, DEFAULT_WARMUP_TECHNICAL_INDICATORS)
            allow_pre_floor_repair = True
            self.stdout.write(
                'Applying technical-indicator warm-up OHLCV prefill: '
                f'lookback_trading_days={warmup_lookback} '
                f'calendar_prefill_days={prefill_calendar_days} '
                f'requested_start={requested_start} '
                f'effective_start={effective_start}'
            )
        elif requested_start < floor_date:
            raise CommandError(f'start-date cannot be earlier than HISTORICAL_DATA_FLOOR={floor_date}.')
        requested_end = None
        if options.get('end_date'):
            requested_end = self._parse_date(options['end_date'], 'end-date')
        else:
            requested_end = timezone.now().date()
        if requested_end < requested_start:
            raise CommandError('end-date must be on or after start-date.')

        if options.get('effective_universe_entry_warmup'):
            return self._handle_effective_universe_entry_warmup(options, requested_start, requested_end)

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
            result = self._dispatch_asset_repair(
                asset.symbol,
                asset.name,
                market_code,
                effective_start,
                requested_end,
                queue=options['queue'],
                list_date=asset.list_date,
                listing_status=asset.listing_status,
                delist_date=asset.delist_date,
                allow_pre_floor_repair=allow_pre_floor_repair,
            )

            processed += 1
            self.stdout.write(f'[{processed}/{total}] {asset.ts_code}: {result}')

        self.stdout.write(self.style.SUCCESS(
            f'OHLCV backfill dispatched for {processed} assets over {requested_start}..{requested_end}. queue={options["queue"]}'
        ))
