from datetime import date
import time

import pandas as pd
import tushare as ts
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.core.date_floor import get_historical_data_floor
from apps.factors.fundamental_materialization import (
    iter_date_windows,
    materialize_fundamental_snapshot_rows,
    normalize_daily_basic_frame,
    normalize_fina_indicator_frame,
)
from apps.factors.models import FundamentalFactorSnapshot
from apps.markets.models import Asset, OHLCV


def _parse_date(value, name):
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CommandError(f'Invalid {name}: {value}. Expected YYYY-MM-DD.') from exc


class Command(BaseCommand):
    help = 'Backfill FundamentalFactorSnapshot from TuShare daily_basic and fina_indicator onto trading dates.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=get_historical_data_floor().isoformat())
        parser.add_argument('--end-date', default=date.today().isoformat())
        parser.add_argument('--symbols', default='')
        parser.add_argument('--limit-assets', type=int, default=0)

    def handle(self, *args, **options):
        token = getattr(settings, 'TUSHARE_TOKEN', None)
        if not token:
            raise CommandError('TUSHARE_TOKEN is not configured.')

        floor_date = get_historical_data_floor()
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
                Q(roe__isnull=True) |
                Q(free_share__isnull=True) |
                Q(circ_mv__isnull=True)
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

        rows = []
        for payload in materialize_fundamental_snapshot_rows(trading_dates, daily_df, fina_df):
            metadata = {
                'source': 'tushare_daily_basic_fina_indicator',
                'daily_basic_trade_date': payload['daily_basic_trade_date'].isoformat() if payload['daily_basic_trade_date'] else None,
                'fina_indicator_ann_date': payload['fina_indicator_ann_date'].isoformat() if payload['fina_indicator_ann_date'] else None,
                'fina_indicator_end_date': payload['fina_indicator_end_date'].isoformat() if payload['fina_indicator_end_date'] else None,
            }
            rows.append(
                FundamentalFactorSnapshot(
                    asset=asset,
                    date=payload['date'],
                    pe=payload['pe'],
                    pb=payload['pb'],
                    total_share=payload['total_share'],
                    float_share=payload['float_share'],
                    free_share=payload['free_share'],
                    total_mv=payload['total_mv'],
                    circ_mv=payload['circ_mv'],
                    roe=payload['roe'],
                    roe_qoq=payload['roe_qoq'],
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
            update_fields=['pe', 'pb', 'total_share', 'float_share', 'free_share', 'total_mv', 'circ_mv', 'roe', 'roe_qoq', 'metadata'],
        )
        return len(rows)

    def _fetch_daily_basic(self, pro, ts_code, start_date, end_date):
        fields = 'trade_date,pe,pb,total_share,float_share,free_share,total_mv,circ_mv'
        daily_df = self._call_tushare(
            lambda: pro.daily_basic(
                ts_code=ts_code,
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d'),
                fields=fields,
            ),
            f'daily_basic:{ts_code}:{start_date}:{end_date}',
        )
        return normalize_daily_basic_frame(daily_df)

    def _fetch_fina_indicator(self, pro, ts_code, start_date, end_date):
        frames = []
        for window_start, window_end in iter_date_windows(start_date, end_date):
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
            return normalize_fina_indicator_frame(None)

        return normalize_fina_indicator_frame(pd.concat(frames, ignore_index=True))

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