import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import numpy as np
import pandas as pd
import talib
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.analytics.models import TechnicalIndicator
from apps.core.date_floor import get_historical_data_floor
from apps.markets.models import Asset, OHLCV


DEFAULT_TECHNICAL_INDICATORS = (
    'ADX',
    'BBANDS',
    'EMA',
    'FIB_RET',
    'MACD',
    'MOM_10D',
    'MOM_20D',
    'MOM_5D',
    'OBV',
    'RSI',
    'SMA',
    'STOCH',
)
DECIMAL_QUANTIZER = Decimal('0.00000001')
EMA_PERIODS = (5, 10, 20, 50, 100, 200)
SMA_PERIODS = (5, 10, 20, 50, 100, 200)


def _parse_date(value, label):
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CommandError(f'Invalid {label}: {value}. Expected YYYY-MM-DD.') from exc


def _parse_indicator_types(raw_value):
    indicator_types = tuple(dict.fromkeys(
        item.strip().upper() for item in str(raw_value or '').split(',') if item.strip()
    ))
    if not indicator_types:
        raise CommandError('technical-indicators must include at least one indicator type.')

    unknown = [indicator_type for indicator_type in indicator_types if indicator_type not in DEFAULT_TECHNICAL_INDICATORS and indicator_type != 'RS_SCORE']
    if unknown:
        raise CommandError(
            f'Unsupported technical-indicators value(s): {", ".join(unknown)}. '
            f'Supported values: {", ".join(DEFAULT_TECHNICAL_INDICATORS)}'
        )

    if 'RS_SCORE' in indicator_types:
        raise CommandError('RS_SCORE is handled separately by backfill_model_data and is not supported by this command.')

    return indicator_types


def _make_timestamp(trading_date):
    return timezone.make_aware(datetime.combine(trading_date, datetime.min.time()))


def _safe_decimal(value):
    if value is None:
        return None
    try:
        if pd.isna(value) or np.isinf(value):
            return None
    except TypeError:
        pass
    try:
        return Decimal(str(value)).quantize(DECIMAL_QUANTIZER, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


class Command(BaseCommand):
    help = 'Backfill historical TechnicalIndicator rows from OHLCV history for non-RS indicators.'
    CHECKPOINT_VERSION = 1

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=get_historical_data_floor().isoformat())
        parser.add_argument('--end-date', default=date.today().isoformat())
        parser.add_argument('--symbols', default='')
        parser.add_argument('--limit-assets', type=int, default=0)
        parser.add_argument('--technical-indicators', default=','.join(DEFAULT_TECHNICAL_INDICATORS))
        parser.add_argument(
            '--chunk-size-days',
            type=int,
            default=365,
            help='Maximum inclusive date span per delete/insert transaction. Use 0 to process the full range in one chunk.',
        )
        parser.add_argument('--checkpoint-file', default='')
        parser.add_argument('--resume-from-checkpoint', action='store_true')

    def handle(self, *args, **options):
        floor_date = get_historical_data_floor()
        start_date = max(_parse_date(options['start_date'], 'start-date'), floor_date)
        end_date = _parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        indicator_types = _parse_indicator_types(options['technical_indicators'])
        chunk_size_days = self._parse_chunk_size_days(options.get('chunk_size_days'))

        symbols = [token.strip() for token in str(options['symbols'] or '').split(',') if token.strip()]
        assets = Asset.objects.order_by('ts_code')
        if symbols:
            assets = assets.filter(Q(symbol__in=symbols) | Q(ts_code__in=symbols))

        limit_assets = int(options['limit_assets'] or 0)
        if limit_assets > 0:
            assets = assets[:limit_assets]
        assets = list(assets)

        self._configure_checkpoint(
            options,
            start_date,
            end_date,
            indicator_types,
            chunk_size_days,
            symbols,
            limit_assets,
            assets,
        )

        chunk_windows = self._chunk_windows(start_date, end_date, chunk_size_days)

        processed_assets = 0
        skipped_assets = 0
        resume_skipped_assets = 0
        processed_chunks = 0
        deleted_rows = 0
        inserted_rows = 0

        for asset_index, asset in enumerate(assets, start=1):
            df = self._load_ohlcv_df(asset.id, end_date)
            if df.empty or df.index[-1] < start_date:
                skipped_assets += 1
                self._mark_asset_progress(
                    asset,
                    status='skipped',
                    last_completed_chunk_end=end_date,
                    details={'skip_reason': 'no_ohlcv_in_window'},
                )
                self.stdout.write(
                    f'[{asset_index}/{len(assets)}] {asset.ts_code}: skipped (no OHLCV rows through {end_date})'
                )
                continue

            remaining_chunk_windows = self._remaining_chunk_windows(asset, chunk_windows)
            if not remaining_chunk_windows:
                resume_skipped_assets += 1
                continue

            asset_state = self._asset_state(asset)
            asset_deleted_rows = int(asset_state.get('deleted_rows') or 0)
            asset_inserted_rows = int(asset_state.get('inserted_rows') or 0)
            asset_completed_chunks = int(asset_state.get('completed_chunks') or 0)
            self._mark_asset_progress(asset, status='running')

            total_asset_chunks = len(chunk_windows)
            for chunk_start, chunk_end in remaining_chunk_windows:
                rows = self._build_rows(asset, df, chunk_start, chunk_end, indicator_types)
                with transaction.atomic():
                    deleted_count, _ = TechnicalIndicator.objects.filter(
                        asset=asset,
                        timestamp__date__gte=chunk_start,
                        timestamp__date__lte=chunk_end,
                        indicator_type__in=indicator_types,
                    ).delete()
                    if rows:
                        TechnicalIndicator.objects.bulk_create(rows, batch_size=2000)

                asset_deleted_rows += deleted_count
                asset_inserted_rows += len(rows)
                asset_completed_chunks += 1
                processed_chunks += 1
                deleted_rows += deleted_count
                inserted_rows += len(rows)

                self._mark_asset_progress(
                    asset,
                    status='running',
                    last_completed_chunk_end=chunk_end,
                    details={
                        'completed_chunks': asset_completed_chunks,
                        'deleted_rows': asset_deleted_rows,
                        'inserted_rows': asset_inserted_rows,
                    },
                )
                self.stdout.write(
                    f'[{asset_index}/{len(assets)}] {asset.ts_code} '
                    f'chunk {asset_completed_chunks}/{total_asset_chunks} '
                    f'{chunk_start}..{chunk_end}: deleted={deleted_count} inserted={len(rows)}'
                )

            processed_assets += 1
            self._mark_asset_progress(
                asset,
                status='completed',
                last_completed_chunk_end=end_date,
                details={
                    'completed_chunks': asset_completed_chunks,
                    'deleted_rows': asset_deleted_rows,
                    'inserted_rows': asset_inserted_rows,
                },
            )

        self.stdout.write(self.style.SUCCESS(
            'Technical indicator backfill complete: '
            f'processed_assets={processed_assets}, '
            f'resume_skipped_assets={resume_skipped_assets}, '
            f'skipped_assets={skipped_assets}, '
            f'processed_chunks={processed_chunks}, '
            f'deleted_rows={deleted_rows}, '
            f'inserted_rows={inserted_rows}, '
            f'range={start_date}..{end_date}, '
            f'chunk_size_days={chunk_size_days if chunk_size_days > 0 else "all"}, '
            f'indicators={",".join(indicator_types)}'
        ))

    def _parse_chunk_size_days(self, value):
        try:
            chunk_size_days = int(value or 0)
        except (TypeError, ValueError) as exc:
            raise CommandError(f'Invalid chunk-size-days: {value}. Expected a non-negative integer.') from exc
        if chunk_size_days < 0:
            raise CommandError('chunk-size-days must be a non-negative integer.')
        return chunk_size_days

    def _chunk_windows(self, start_date, end_date, chunk_size_days):
        if chunk_size_days <= 0:
            return [(start_date, end_date)]

        windows = []
        chunk_start = start_date
        while chunk_start <= end_date:
            chunk_end = min(chunk_start + timedelta(days=chunk_size_days - 1), end_date)
            windows.append((chunk_start, chunk_end))
            chunk_start = chunk_end + timedelta(days=1)
        return windows

    def _checkpoint_metadata(self, start_date, end_date, indicator_types, chunk_size_days, symbols, limit_assets, assets):
        return {
            'version': self.CHECKPOINT_VERSION,
            'command': 'backfill_technical_indicators',
            'window': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
            },
            'options': {
                'technical_indicators': list(indicator_types),
                'chunk_size_days': int(chunk_size_days),
                'symbols': sorted(set(symbols)),
                'limit_assets': int(limit_assets or 0),
            },
            'asset_selection': {
                'count': len(assets),
                'ts_codes': [asset.ts_code for asset in assets],
            },
            'assets': {},
        }

    def _configure_checkpoint(self, options, start_date, end_date, indicator_types, chunk_size_days, symbols, limit_assets, assets):
        self._resume_from_checkpoint = bool(options.get('resume_from_checkpoint'))
        checkpoint_file = str(options.get('checkpoint_file') or '').strip()
        self._checkpoint_path = Path(checkpoint_file).expanduser() if checkpoint_file else None
        self._checkpoint = self._checkpoint_metadata(
            start_date,
            end_date,
            indicator_types,
            chunk_size_days,
            symbols,
            limit_assets,
            assets,
        )
        if not self._checkpoint_path:
            return

        if self._checkpoint_path.exists():
            if not self._resume_from_checkpoint:
                self.stdout.write(
                    self.style.WARNING(
                        f'Checkpoint file exists at {self._checkpoint_path}; pass --resume-from-checkpoint to reuse it.'
                    )
                )
            else:
                existing = json.loads(self._checkpoint_path.read_text(encoding='utf-8'))
                if (
                    existing.get('command') != self._checkpoint['command'] or
                    existing.get('window') != self._checkpoint['window'] or
                    existing.get('options') != self._checkpoint['options'] or
                    existing.get('asset_selection') != self._checkpoint['asset_selection']
                ):
                    raise CommandError(
                        f'Checkpoint file {self._checkpoint_path} does not match the requested backfill window.'
                    )
                self._checkpoint = existing

        self._write_checkpoint()

    def _write_checkpoint(self):
        if not self._checkpoint_path:
            return
        self._checkpoint['updated_at'] = timezone.now().isoformat()
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._checkpoint_path.write_text(
            json.dumps(self._checkpoint, ensure_ascii=True, indent=2, sort_keys=True),
            encoding='utf-8',
        )

    def _asset_checkpoint_key(self, asset):
        return str(asset.ts_code or asset.symbol or asset.id)

    def _asset_state(self, asset):
        assets = self._checkpoint.setdefault('assets', {})
        return assets.setdefault(self._asset_checkpoint_key(asset), {'status': 'pending'})

    def _mark_asset_progress(self, asset, *, status=None, last_completed_chunk_end=None, details=None):
        asset_state = self._asset_state(asset)
        if status is not None:
            asset_state['status'] = status
            if status == 'running':
                asset_state.setdefault('started_at', timezone.now().isoformat())
            if status in {'completed', 'skipped'}:
                asset_state['completed_at'] = timezone.now().isoformat()
        if last_completed_chunk_end is not None:
            asset_state['last_completed_chunk_end'] = last_completed_chunk_end.isoformat()
        if details:
            asset_state.update(details)
        self._write_checkpoint()

    def _remaining_chunk_windows(self, asset, chunk_windows):
        if not self._resume_from_checkpoint:
            return list(chunk_windows)

        asset_state = self._asset_state(asset)
        if asset_state.get('status') == 'skipped':
            self.stdout.write(f'  {asset.ts_code}: checkpoint already marked skipped, skipping')
            return []

        last_completed = asset_state.get('last_completed_chunk_end')
        if not last_completed:
            return list(chunk_windows)

        last_completed_date = date.fromisoformat(last_completed)
        remaining_windows = [window for window in chunk_windows if window[0] > last_completed_date]
        if remaining_windows:
            self.stdout.write(
                f'  {asset.ts_code}: checkpoint resume skips through {last_completed_date} '
                f'and restarts at {remaining_windows[0][0]}'
            )
        else:
            self.stdout.write(
                f'  {asset.ts_code}: checkpoint already covers the requested range through {last_completed_date}'
            )
        return remaining_windows

    def _load_ohlcv_df(self, asset_id, end_date):
        rows = list(
            OHLCV.objects.filter(asset_id=asset_id, date__lte=end_date)
            .order_by('date')
            .values('date', 'open', 'high', 'low', 'close', 'volume')
        )
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame.from_records(rows)
        df.set_index('date', inplace=True)
        for column in ('open', 'high', 'low', 'close', 'volume'):
            df[column] = df[column].astype(float)
        return df

    def _build_rows(self, asset, df, start_date, end_date, indicator_types):
        rows = []
        if 'RSI' in indicator_types:
            rows.extend(self._series_rows(
                asset,
                pd.Series(talib.RSI(df['close'], timeperiod=14), index=df.index),
                'RSI',
                {'timeperiod': 14},
                start_date,
                end_date,
            ))

        if 'MACD' in indicator_types:
            macd, _signal, _hist = talib.MACD(df['close'], fastperiod=12, slowperiod=26, signalperiod=9)
            rows.extend(self._series_rows(
                asset,
                pd.Series(macd, index=df.index),
                'MACD',
                {'fastperiod': 12, 'slowperiod': 26, 'signalperiod': 9},
                start_date,
                end_date,
            ))

        if 'BBANDS' in indicator_types:
            upper, middle, lower = talib.BBANDS(df['close'], timeperiod=20, nbdevup=2, nbdevdn=2)
            for trading_date in df.index:
                if trading_date < start_date or trading_date > end_date:
                    continue
                middle_value = _safe_decimal(middle.get(trading_date))
                upper_value = _safe_decimal(upper.get(trading_date))
                lower_value = _safe_decimal(lower.get(trading_date))
                if middle_value is None or upper_value is None or lower_value is None:
                    continue
                rows.append(TechnicalIndicator(
                    asset=asset,
                    timestamp=_make_timestamp(trading_date),
                    indicator_type='BBANDS',
                    value=middle_value,
                    parameters={
                        'timeperiod': 20,
                        'nbdevup': 2,
                        'nbdevdn': 2,
                        'upper': float(upper_value),
                        'middle': float(middle_value),
                        'lower': float(lower_value),
                    },
                ))

        if 'SMA' in indicator_types:
            for period in SMA_PERIODS:
                rows.extend(self._series_rows(
                    asset,
                    pd.Series(talib.SMA(df['close'], timeperiod=period), index=df.index),
                    'SMA',
                    {'timeperiod': period},
                    start_date,
                    end_date,
                ))

        if 'EMA' in indicator_types:
            for period in EMA_PERIODS:
                rows.extend(self._series_rows(
                    asset,
                    pd.Series(talib.EMA(df['close'], timeperiod=period), index=df.index),
                    'EMA',
                    {'timeperiod': period},
                    start_date,
                    end_date,
                ))

        if 'STOCH' in indicator_types:
            slowk, _slowd = talib.STOCH(
                df['high'],
                df['low'],
                df['close'],
                fastk_period=14,
                slowk_period=3,
                slowk_matype=0,
                slowd_period=3,
                slowd_matype=0,
            )
            rows.extend(self._series_rows(
                asset,
                pd.Series(slowk, index=df.index),
                'STOCH',
                {'fastk_period': 14, 'slowk_period': 3, 'slowd_period': 3},
                start_date,
                end_date,
            ))

        if 'ADX' in indicator_types:
            rows.extend(self._series_rows(
                asset,
                pd.Series(talib.ADX(df['high'], df['low'], df['close'], timeperiod=14), index=df.index),
                'ADX',
                {'timeperiod': 14},
                start_date,
                end_date,
            ))

        if 'OBV' in indicator_types:
            obv = pd.Series(talib.OBV(df['close'], df['volume']), index=df.index)
            if not obv.empty:
                obv.iloc[0] = np.nan
            rows.extend(self._series_rows(
                asset,
                obv,
                'OBV',
                {},
                start_date,
                end_date,
            ))

        if 'FIB_RET' in indicator_types:
            rolling_high = df['high'].rolling(window=60, min_periods=2).max()
            rolling_low = df['low'].rolling(window=60, min_periods=2).min()
            fib_mid = ((rolling_high + rolling_low) / 2.0).where((rolling_high - rolling_low) > 0)
            rows.extend(self._series_rows(
                asset,
                fib_mid,
                'FIB_RET',
                {'lookback_days': 60},
                start_date,
                end_date,
            ))

        for indicator_type, periods in (
            ('MOM_5D', 5),
            ('MOM_10D', 10),
            ('MOM_20D', 20),
        ):
            if indicator_type not in indicator_types:
                continue
            rows.extend(self._series_rows(
                asset,
                df['close'].pct_change(periods=periods),
                indicator_type,
                {'n_days': periods},
                start_date,
                end_date,
            ))

        return rows

    def _series_rows(self, asset, series, indicator_type, parameters, start_date, end_date):
        rows = []
        for trading_date, value in series.items():
            if trading_date < start_date or trading_date > end_date:
                continue
            decimal_value = _safe_decimal(value)
            if decimal_value is None:
                continue
            rows.append(TechnicalIndicator(
                asset=asset,
                timestamp=_make_timestamp(trading_date),
                indicator_type=indicator_type,
                value=decimal_value,
                parameters=parameters,
            ))
        return rows