from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

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

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=get_historical_data_floor().isoformat())
        parser.add_argument('--end-date', default=date.today().isoformat())
        parser.add_argument('--symbols', default='')
        parser.add_argument('--limit-assets', type=int, default=0)
        parser.add_argument('--technical-indicators', default=','.join(DEFAULT_TECHNICAL_INDICATORS))

    def handle(self, *args, **options):
        floor_date = get_historical_data_floor()
        start_date = max(_parse_date(options['start_date'], 'start-date'), floor_date)
        end_date = _parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        indicator_types = _parse_indicator_types(options['technical_indicators'])

        symbols = [token.strip() for token in str(options['symbols'] or '').split(',') if token.strip()]
        assets = Asset.objects.order_by('ts_code')
        if symbols:
            assets = assets.filter(Q(symbol__in=symbols) | Q(ts_code__in=symbols))

        limit_assets = int(options['limit_assets'] or 0)
        if limit_assets > 0:
            assets = assets[:limit_assets]

        processed_assets = 0
        skipped_assets = 0
        deleted_rows = 0
        inserted_rows = 0

        for asset in assets:
            df = self._load_ohlcv_df(asset.id, end_date)
            if df.empty or df.index[-1] < start_date:
                skipped_assets += 1
                continue

            rows = self._build_rows(asset, df, start_date, end_date, indicator_types)
            with transaction.atomic():
                deleted_count, _ = TechnicalIndicator.objects.filter(
                    asset=asset,
                    timestamp__date__gte=start_date,
                    timestamp__date__lte=end_date,
                    indicator_type__in=indicator_types,
                ).delete()
                if rows:
                    TechnicalIndicator.objects.bulk_create(rows, batch_size=2000)

            processed_assets += 1
            deleted_rows += deleted_count
            inserted_rows += len(rows)
            self.stdout.write(
                f'[{processed_assets}] {asset.ts_code}: deleted={deleted_count} inserted={len(rows)}'
            )

        self.stdout.write(self.style.SUCCESS(
            'Technical indicator backfill complete: '
            f'processed_assets={processed_assets}, '
            f'skipped_assets={skipped_assets}, '
            f'deleted_rows={deleted_rows}, '
            f'inserted_rows={inserted_rows}, '
            f'range={start_date}..{end_date}, '
            f'indicators={",".join(indicator_types)}'
        ))

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
            _upper, middle, _lower = talib.BBANDS(df['close'], timeperiod=20, nbdevup=2, nbdevdn=2)
            rows.extend(self._series_rows(
                asset,
                pd.Series(middle, index=df.index),
                'BBANDS',
                {'timeperiod': 20, 'nbdevup': 2, 'nbdevdn': 2},
                start_date,
                end_date,
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