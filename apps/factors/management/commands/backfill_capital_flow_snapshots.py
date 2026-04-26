from datetime import date, timedelta
from decimal import Decimal
import time

import pandas as pd
import tushare as ts
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.factors.models import (
    AssetMarginDetailSnapshot,
    AssetMoneyFlowSnapshot,
    CapitalFlowSnapshot,
)
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


def _iter_date_windows(start_date, end_date, window_days=365 * 5):
    cursor = start_date
    while cursor <= end_date:
        window_end = min(end_date, cursor + timedelta(days=window_days - 1))
        yield cursor, window_end
        cursor = window_end + timedelta(days=1)


class Command(BaseCommand):
    help = 'Backfill CapitalFlowSnapshot from TuShare moneyflow and margin_detail onto trading dates.'

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
        self.request_sleep_seconds = float(getattr(settings, 'CAPITAL_FLOW_BACKFILL_REQUEST_SLEEP_SECONDS', 0.35))
        self.retry_sleep_seconds = float(getattr(settings, 'CAPITAL_FLOW_BACKFILL_RETRY_SLEEP_SECONDS', 65.0))

        processed = 0
        moneyflow_rows = 0
        margin_rows = 0
        capital_rows = 0
        for asset in assets:
            trading_dates = list(
                OHLCV.objects.filter(asset=asset, date__gte=start_date, date__lte=end_date)
                .values_list('date', flat=True)
                .order_by('date')
            )
            if not trading_dates:
                continue

            counts = self._backfill_asset(pro, asset, trading_dates)
            processed += 1
            moneyflow_rows += counts['moneyflow_rows']
            margin_rows += counts['margin_rows']
            capital_rows += counts['capital_rows']
            self.stdout.write(
                f'[{processed}] {asset.ts_code}: upserted moneyflow={counts["moneyflow_rows"]}, margin={counts["margin_rows"]}, capital={counts["capital_rows"]}'
            )

        self.stdout.write(
            self.style.SUCCESS(
                'Capital flow snapshot backfill complete: '
                f'processed_assets={processed}, moneyflow_rows={moneyflow_rows}, margin_rows={margin_rows}, capital_rows={capital_rows}, '
                f'range={start_date}..{end_date}'
            )
        )

    def _backfill_asset(self, pro, asset, trading_dates):
        trade_start = trading_dates[0]
        trade_end = trading_dates[-1]

        moneyflow_df = self._fetch_moneyflow(pro, asset.ts_code, trade_start, trade_end)
        margin_df = self._fetch_margin_detail(pro, asset.ts_code, trade_start, trade_end)

        moneyflow_rows = self._upsert_moneyflow_rows(asset, moneyflow_df)
        margin_rows = self._upsert_margin_rows(asset, margin_df)
        capital_rows = self._upsert_capital_flow_rows(asset, trading_dates, moneyflow_df, margin_df)
        return {
            'moneyflow_rows': moneyflow_rows,
            'margin_rows': margin_rows,
            'capital_rows': capital_rows,
        }

    def _fetch_moneyflow(self, pro, ts_code, start_date, end_date):
        frames = []
        for window_start, window_end in _iter_date_windows(start_date, end_date):
            frame = self._call_tushare(
                lambda ws=window_start, we=window_end: pro.moneyflow(
                    ts_code=ts_code,
                    start_date=ws.strftime('%Y%m%d'),
                    end_date=we.strftime('%Y%m%d'),
                    fields='trade_date,buy_sm_amount,sell_sm_amount,buy_md_amount,sell_md_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount',
                ),
                f'moneyflow:{ts_code}:{window_start}:{window_end}',
            )
            if frame is not None and not frame.empty:
                frames.append(frame)

        if not frames:
            return pd.DataFrame(
                columns=[
                    'date', 'buy_sm_amount', 'sell_sm_amount', 'buy_md_amount', 'sell_md_amount',
                    'buy_lg_amount', 'sell_lg_amount', 'buy_elg_amount', 'sell_elg_amount', 'net_mf_amount',
                ]
            )

        moneyflow_df = pd.concat(frames, ignore_index=True)
        moneyflow_df['date'] = pd.to_datetime(moneyflow_df['trade_date'], format='%Y%m%d', errors='coerce')
        moneyflow_df = moneyflow_df.dropna(subset=['date']).sort_values('date')
        moneyflow_df = moneyflow_df.drop_duplicates(subset=['date'], keep='last').copy()
        for column in [
            'buy_sm_amount', 'sell_sm_amount', 'buy_md_amount', 'sell_md_amount',
            'buy_lg_amount', 'sell_lg_amount', 'buy_elg_amount', 'sell_elg_amount', 'net_mf_amount',
        ]:
            if column not in moneyflow_df.columns:
                moneyflow_df[column] = None
        return moneyflow_df[
            [
                'date', 'buy_sm_amount', 'sell_sm_amount', 'buy_md_amount', 'sell_md_amount',
                'buy_lg_amount', 'sell_lg_amount', 'buy_elg_amount', 'sell_elg_amount', 'net_mf_amount',
            ]
        ]

    def _fetch_margin_detail(self, pro, ts_code, start_date, end_date):
        frames = []
        for window_start, window_end in _iter_date_windows(start_date, end_date):
            frame = self._call_tushare(
                lambda ws=window_start, we=window_end: pro.margin_detail(
                    ts_code=ts_code,
                    start_date=ws.strftime('%Y%m%d'),
                    end_date=we.strftime('%Y%m%d'),
                ),
                f'margin_detail:{ts_code}:{window_start}:{window_end}',
            )
            if frame is not None and not frame.empty:
                frames.append(frame)

        if not frames:
            return pd.DataFrame(
                columns=['date', 'rzye', 'rqye', 'rzmre', 'rzche', 'rqyl', 'rqchl', 'rqmcl', 'rzrqye']
            )

        margin_df = pd.concat(frames, ignore_index=True)
        margin_df['date'] = pd.to_datetime(margin_df['trade_date'], format='%Y%m%d', errors='coerce')
        margin_df = margin_df.dropna(subset=['date']).sort_values('date')
        margin_df = margin_df.drop_duplicates(subset=['date'], keep='last').copy()
        for column in ['rzye', 'rqye', 'rzmre', 'rzche', 'rqyl', 'rqchl', 'rqmcl', 'rzrqye']:
            if column not in margin_df.columns:
                margin_df[column] = None
        return margin_df[['date', 'rzye', 'rqye', 'rzmre', 'rzche', 'rqyl', 'rqchl', 'rqmcl', 'rzrqye']]

    def _upsert_moneyflow_rows(self, asset, moneyflow_df):
        rows = []
        for row in moneyflow_df.itertuples(index=False):
            rows.append(
                AssetMoneyFlowSnapshot(
                    asset=asset,
                    date=pd.Timestamp(row.date).date(),
                    buy_sm_amount=_safe_decimal(getattr(row, 'buy_sm_amount', None)),
                    sell_sm_amount=_safe_decimal(getattr(row, 'sell_sm_amount', None)),
                    buy_md_amount=_safe_decimal(getattr(row, 'buy_md_amount', None)),
                    sell_md_amount=_safe_decimal(getattr(row, 'sell_md_amount', None)),
                    buy_lg_amount=_safe_decimal(getattr(row, 'buy_lg_amount', None)),
                    sell_lg_amount=_safe_decimal(getattr(row, 'sell_lg_amount', None)),
                    buy_elg_amount=_safe_decimal(getattr(row, 'buy_elg_amount', None)),
                    sell_elg_amount=_safe_decimal(getattr(row, 'sell_elg_amount', None)),
                    net_mf_amount=_safe_decimal(getattr(row, 'net_mf_amount', None)),
                    metadata={'source': 'tushare_moneyflow'},
                )
            )

        if not rows:
            return 0

        AssetMoneyFlowSnapshot.objects.bulk_create(
            rows,
            batch_size=2000,
            update_conflicts=True,
            unique_fields=['asset', 'date'],
            update_fields=[
                'buy_sm_amount', 'sell_sm_amount', 'buy_md_amount', 'sell_md_amount',
                'buy_lg_amount', 'sell_lg_amount', 'buy_elg_amount', 'sell_elg_amount',
                'net_mf_amount', 'metadata',
            ],
        )
        return len(rows)

    def _upsert_margin_rows(self, asset, margin_df):
        rows = []
        for row in margin_df.itertuples(index=False):
            rows.append(
                AssetMarginDetailSnapshot(
                    asset=asset,
                    date=pd.Timestamp(row.date).date(),
                    rzye=_safe_decimal(getattr(row, 'rzye', None)),
                    rqye=_safe_decimal(getattr(row, 'rqye', None)),
                    rzmre=_safe_decimal(getattr(row, 'rzmre', None)),
                    rzche=_safe_decimal(getattr(row, 'rzche', None)),
                    rqyl=_safe_decimal(getattr(row, 'rqyl', None)),
                    rqchl=_safe_decimal(getattr(row, 'rqchl', None)),
                    rqmcl=_safe_decimal(getattr(row, 'rqmcl', None)),
                    rzrqye=_safe_decimal(getattr(row, 'rzrqye', None)),
                    metadata={'source': 'tushare_margin_detail'},
                )
            )

        if not rows:
            return 0

        AssetMarginDetailSnapshot.objects.bulk_create(
            rows,
            batch_size=2000,
            update_conflicts=True,
            unique_fields=['asset', 'date'],
            update_fields=['rzye', 'rqye', 'rzmre', 'rzche', 'rqyl', 'rqchl', 'rqmcl', 'rzrqye', 'metadata'],
        )
        return len(rows)

    def _upsert_capital_flow_rows(self, asset, trading_dates, moneyflow_df, margin_df):
        merged = pd.DataFrame({'date': pd.to_datetime(trading_dates)}).sort_values('date')

        if not moneyflow_df.empty:
            moneyflow_derived = moneyflow_df.copy()
            moneyflow_derived['main_force_daily'] = (
                moneyflow_derived['buy_lg_amount'].fillna(0) +
                moneyflow_derived['buy_elg_amount'].fillna(0) -
                moneyflow_derived['sell_lg_amount'].fillna(0) -
                moneyflow_derived['sell_elg_amount'].fillna(0)
            )
            moneyflow_derived['main_force_net_5d'] = moneyflow_derived['main_force_daily'].rolling(window=5, min_periods=1).sum()
            merged = merged.merge(
                moneyflow_derived[['date', 'main_force_net_5d']],
                on='date',
                how='left',
            )
        else:
            merged['main_force_net_5d'] = None

        if not margin_df.empty:
            margin_derived = margin_df.copy()
            margin_derived['margin_balance_change_5d'] = margin_derived['rzrqye'].diff(periods=5)
            merged = merged.merge(
                margin_derived[['date', 'margin_balance_change_5d']],
                on='date',
                how='left',
            )
        else:
            merged['margin_balance_change_5d'] = None

        rows = []
        for row in merged.itertuples(index=False):
            rows.append(
                CapitalFlowSnapshot(
                    asset=asset,
                    date=pd.Timestamp(row.date).date(),
                    main_force_net_5d=_safe_decimal(getattr(row, 'main_force_net_5d', None)),
                    margin_balance_change_5d=_safe_decimal(getattr(row, 'margin_balance_change_5d', None)),
                    metadata={
                        'source': 'tushare_moneyflow_margin_detail',
                        'main_force_formula': 'rolling_sum_5d(buy_lg_amount + buy_elg_amount - sell_lg_amount - sell_elg_amount)',
                        'margin_formula': 'diff_5d(rzrqye)',
                    },
                )
            )

        if not rows:
            return 0

        CapitalFlowSnapshot.objects.bulk_create(
            rows,
            batch_size=2000,
            update_conflicts=True,
            unique_fields=['asset', 'date'],
            update_fields=['main_force_net_5d', 'margin_balance_change_5d', 'metadata'],
        )
        return len(rows)

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