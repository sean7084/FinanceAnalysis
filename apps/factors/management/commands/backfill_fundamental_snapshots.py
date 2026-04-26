from datetime import date, timedelta
from decimal import Decimal
import time

import pandas as pd
import tushare as ts
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.factors.models import FundamentalFactorSnapshot
from apps.markets.models import Asset, OHLCV


def _parse_date(value, name):
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc


def _safe_decimal(value):
    if value in (None, '', 'nan'):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _normalize_rate(value):
    parsed = _safe_decimal(value)
    if parsed is None:
        return None
    if abs(parsed) > Decimal('1'):
        return parsed / Decimal('100')
    return parsed


def _iter_date_windows(start_date, end_date, window_days=365 * 5):
    cursor = start_date
    while cursor <= end_date:
        window_end = min(end_date, cursor + timedelta(days=window_days - 1))
        yield cursor, window_end
        cursor = window_end + timedelta(days=1)


class Command(BaseCommand):
    help = 'Backfill FundamentalFactorSnapshot from TuShare daily_basic and fina_indicator onto trading dates.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=getattr(settings, 'HISTORICAL_DATA_FLOOR', '2000-01-01'))
        parser.add_argument('--end-date', default=date.today().isoformat())
        parser.add_argument('--symbols', default='')
        parser.add_argument('--limit-assets', type=int, default=0)

    def handle(self, *args, **options):
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is not configured.')

        floor_date = _parse_date(getattr(settings, 'HISTORICAL_DATA_FLOOR', '2000-01-01'), 'HISTORICAL_DATA_FLOOR')
        start_date = max(_parse_date(options['start_date'], 'start-date'), floor_date)
        end_date = _parse_date(options['end_date'], 'end-date')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        symbols = [token.strip() for token in str(options['symbols'] or '').split(',') if token.strip()]
        assets = Asset.objects.order_by('ts_code')
        if symbols:
            assets = assets.filter(symbol__in=symbols)

        limit_assets = int(options['limit_assets'] or 0)
        if limit_assets > 0:
            assets = assets[:limit_assets]

        pro = ts.pro_api(token)
        self.request_sleep_seconds = float(getattr(settings, 'FUNDAMENTAL_BACKFILL_REQUEST_SLEEP_SECONDS', 0.35))
        self.retry_sleep_seconds = float(getattr(settings, 'FUNDAMENTAL_BACKFILL_RETRY_SLEEP_SECONDS', 65.0))

        processed = 0
        inserted_or_updated = 0
        for asset in assets:
            trading_dates = list(
                OHLCV.objects.filter(asset=asset, date__gte=start_date, date__lte=end_date)
                .values_list('date', flat=True)
                .order_by('date')
            )
            if not trading_dates:
                continue

            existing_count = FundamentalFactorSnapshot.objects.filter(
                asset=asset,
                date__gte=trading_dates[0],
                date__lte=trading_dates[-1],
            ).count()
            has_missing_core_fields = FundamentalFactorSnapshot.objects.filter(
                asset=asset,
                date__gte=trading_dates[0],
                date__lte=trading_dates[-1],
            ).filter(
                Q(pe__isnull=True) |
                Q(pb__isnull=True) |
                Q(roe__isnull=True)
            ).exists()
            if existing_count >= len(trading_dates) and not has_missing_core_fields:
                processed += 1
                self.stdout.write(f'[{processed}] {asset.ts_code}: already complete, skipped')
                continue

            asset_count = self._backfill_asset(pro, asset, trading_dates)
            processed += 1
            inserted_or_updated += asset_count
            self.stdout.write(f'[{processed}] {asset.ts_code}: upserted {asset_count} fundamental snapshots')

        self.stdout.write(
            self.style.SUCCESS(
                f'Fundamental snapshot backfill complete: processed_assets={processed}, upserted_rows={inserted_or_updated}, range={start_date}..{end_date}'
            )
        )

    def _backfill_asset(self, pro, asset, trading_dates):
        trade_start = trading_dates[0]
        trade_end = trading_dates[-1]

        daily_df = self._fetch_daily_basic(pro, asset.ts_code, trade_start, trade_end)
        fina_df = self._fetch_fina_indicator(pro, asset.ts_code, trade_start, trade_end)

        trading_df = pd.DataFrame({'date': pd.to_datetime(trading_dates)})
        merged = trading_df.sort_values('date')

        if not daily_df.empty:
            merged = pd.merge_asof(merged, daily_df, on='date', direction='backward')
        else:
            merged['daily_basic_trade_date'] = pd.NaT
            merged['pe'] = None
            merged['pb'] = None

        if not fina_df.empty:
            merged = pd.merge_asof(
                merged,
                fina_df,
                left_on='date',
                right_on='available_date',
                direction='backward',
            )
        else:
            merged['available_date'] = pd.NaT
            merged['ann_date'] = pd.NaT
            merged['report_end_date'] = pd.NaT
            merged['roe'] = None
            merged['roe_qoq'] = None

        rows = []
        for row in merged.itertuples(index=False):
            snapshot_date = pd.Timestamp(row.date).date()
            daily_trade_date = getattr(row, 'daily_basic_trade_date', None)
            ann_date = getattr(row, 'ann_date', None)
            report_end_date = getattr(row, 'report_end_date', None)
            metadata = {
                'source': 'tushare_daily_basic_fina_indicator',
                'daily_basic_trade_date': daily_trade_date.date().isoformat() if pd.notna(daily_trade_date) else None,
                'fina_indicator_ann_date': ann_date.date().isoformat() if pd.notna(ann_date) else None,
                'fina_indicator_end_date': report_end_date.date().isoformat() if pd.notna(report_end_date) else None,
            }
            rows.append(
                FundamentalFactorSnapshot(
                    asset=asset,
                    date=snapshot_date,
                    pe=_safe_decimal(getattr(row, 'pe', None)),
                    pb=_safe_decimal(getattr(row, 'pb', None)),
                    roe=_safe_decimal(getattr(row, 'roe', None)),
                    roe_qoq=_safe_decimal(getattr(row, 'roe_qoq', None)),
                    metadata=metadata,
                )
            )

        if not rows:
            return 0

        FundamentalFactorSnapshot.objects.bulk_create(
            rows,
            batch_size=2000,
            update_conflicts=True,
            unique_fields=['asset', 'date'],
            update_fields=['pe', 'pb', 'roe', 'roe_qoq', 'metadata'],
        )
        return len(rows)

    def _fetch_daily_basic(self, pro, ts_code, start_date, end_date):
        fields = 'trade_date,pe,pb'
        daily_df = self._call_tushare(
            lambda: pro.daily_basic(
                ts_code=ts_code,
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d'),
                fields=fields,
            ),
            f'daily_basic:{ts_code}:{start_date}:{end_date}',
        )
        if daily_df is None or daily_df.empty:
            return pd.DataFrame(columns=['date', 'daily_basic_trade_date', 'pe', 'pb'])

        normalized = daily_df.copy()
        normalized['date'] = pd.to_datetime(normalized['trade_date'], format='%Y%m%d', errors='coerce')
        normalized['daily_basic_trade_date'] = normalized['date']
        normalized = normalized.dropna(subset=['date']).sort_values('date')
        return normalized[['date', 'daily_basic_trade_date', 'pe', 'pb']]

    def _fetch_fina_indicator(self, pro, ts_code, start_date, end_date):
        frames = []
        for window_start, window_end in _iter_date_windows(start_date, end_date):
            frame = self._call_tushare(
                lambda ws=window_start, we=window_end: pro.fina_indicator(
                    ts_code=ts_code,
                    start_date=ws.strftime('%Y%m%d'),
                    end_date=we.strftime('%Y%m%d'),
                    fields='ann_date,end_date,roe',
                ),
                f'fina_indicator:{ts_code}:{window_start}:{window_end}',
            )
            if frame is not None and not frame.empty:
                frames.append(frame)

        if not frames:
            return pd.DataFrame(columns=['available_date', 'ann_date', 'report_end_date', 'roe', 'roe_qoq'])

        fina_df = pd.concat(frames, ignore_index=True)
        fina_df['ann_date'] = pd.to_datetime(fina_df['ann_date'], format='%Y%m%d', errors='coerce')
        fina_df['report_end_date'] = pd.to_datetime(fina_df['end_date'], format='%Y%m%d', errors='coerce')
        fina_df = fina_df.dropna(subset=['ann_date', 'report_end_date']).sort_values(['report_end_date', 'ann_date'])
        fina_df = fina_df.drop_duplicates(subset=['report_end_date'], keep='last').copy()
        normalized_roe = fina_df['roe'].map(_normalize_rate)
        fina_df['roe'] = normalized_roe
        fina_df['roe_qoq'] = normalized_roe.apply(lambda value: float(value) if value is not None else None).diff()
        fina_df['roe_qoq'] = fina_df['roe_qoq'].map(_safe_decimal)
        fina_df['available_date'] = fina_df['ann_date']
        return fina_df[['available_date', 'ann_date', 'report_end_date', 'roe', 'roe_qoq']].sort_values('available_date')

    def _call_tushare(self, fn, label):
        attempts = 0
        while True:
            try:
                result = fn()
                if self.request_sleep_seconds > 0:
                    time.sleep(self.request_sleep_seconds)
                return result
            except Exception as exc:
                message = str(exc)
                attempts += 1
                if '频率超限' not in message or attempts >= 5:
                    raise
                self.stdout.write(
                    self.style.WARNING(
                        f'{label}: TuShare rate limit encountered, sleeping {self.retry_sleep_seconds:.0f}s before retry {attempts}/5.'
                    )
                )
                time.sleep(self.retry_sleep_seconds)