import json
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import talib
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.analytics.models import SignalEvent
from apps.analytics.technical_staleness import (
    exact_trading_window_available,
    ordered_trading_dates_for_asset,
    technical_indicator_gap_window_points,
    trading_date_positions,
    trailing_indicator_window_is_fresh,
)
from apps.core.date_floor import get_historical_data_floor
from apps.markets.models import Asset, OHLCV


DEFAULT_SIGNAL_TYPES = (
    'GOLDEN_CROSS',
    'DEATH_CROSS',
    'MA_BULL_ALIGN',
    'MA_BEAR_ALIGN',
    'BB_SQUEEZE',
    'BB_BREAKOUT_UP',
    'BB_BREAKOUT_DOWN',
    'BB_RSI_OVERBOUGHT',
    'BB_RSI_OVERSOLD',
    'VOLUME_SPIKE',
    'VOLUME_PRICE_DIVERGENCE',
    'MOMENTUM_UP_5D',
    'MOMENTUM_DOWN_5D',
    'OVERSOLD_COMBINATION',
)


def _parse_date(value, label):
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CommandError(f'Invalid {label}: {value}. Expected YYYY-MM-DD.') from exc


def _parse_signal_types(raw_value):
    signal_types = tuple(dict.fromkeys(
        item.strip().upper() for item in str(raw_value or '').split(',') if item.strip()
    ))
    if not signal_types:
        raise CommandError('signal-types must include at least one signal type.')

    unknown = [signal_type for signal_type in signal_types if signal_type not in DEFAULT_SIGNAL_TYPES]
    if unknown:
        raise CommandError(
            f'Unsupported signal-types value(s): {", ".join(unknown)}. '
            f'Supported values: {", ".join(DEFAULT_SIGNAL_TYPES)}'
        )

    if 'HIGH_RS_SCORE' in signal_types:
        raise CommandError('HIGH_RS_SCORE is handled separately by backfill_model_data and is not supported by this command.')

    return signal_types


def _make_timestamp(trading_date):
    return timezone.make_aware(datetime.combine(trading_date, datetime.min.time()))


class Command(BaseCommand):
    help = 'Backfill historical non-RS SignalEvent rows from OHLCV history.'
    CHECKPOINT_VERSION = 1

    def add_arguments(self, parser):
        parser.add_argument('--start-date', default=get_historical_data_floor().isoformat())
        parser.add_argument('--end-date', default=date.today().isoformat())
        parser.add_argument('--symbols', default='')
        parser.add_argument('--limit-assets', type=int, default=0)
        parser.add_argument('--signal-types', default=','.join(DEFAULT_SIGNAL_TYPES))
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

        signal_types = _parse_signal_types(options['signal_types'])
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
            signal_types,
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
                rows = self._build_rows(asset, df, chunk_start, chunk_end, signal_types)
                with transaction.atomic():
                    deleted_count, _ = SignalEvent.objects.filter(
                        asset=asset,
                        timestamp__date__gte=chunk_start,
                        timestamp__date__lte=chunk_end,
                        signal_type__in=signal_types,
                    ).delete()
                    if rows:
                        SignalEvent.objects.bulk_create(rows, batch_size=2000)

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
            'Signal event backfill complete: '
            f'processed_assets={processed_assets}, '
            f'resume_skipped_assets={resume_skipped_assets}, '
            f'skipped_assets={skipped_assets}, '
            f'processed_chunks={processed_chunks}, '
            f'deleted_rows={deleted_rows}, '
            f'inserted_rows={inserted_rows}, '
            f'range={start_date}..{end_date}, '
            f'chunk_size_days={chunk_size_days if chunk_size_days > 0 else "all"}, '
            f'signals={",".join(signal_types)}'
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

    def _checkpoint_metadata(self, start_date, end_date, signal_types, chunk_size_days, symbols, limit_assets, assets):
        return {
            'version': self.CHECKPOINT_VERSION,
            'command': 'backfill_signal_events',
            'window': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
            },
            'options': {
                'signal_types': list(signal_types),
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

    def _configure_checkpoint(self, options, start_date, end_date, signal_types, chunk_size_days, symbols, limit_assets, assets):
        self._resume_from_checkpoint = bool(options.get('resume_from_checkpoint'))
        checkpoint_file = str(options.get('checkpoint_file') or '').strip()
        self._checkpoint_path = Path(checkpoint_file).expanduser() if checkpoint_file else None
        self._checkpoint = self._checkpoint_metadata(
            start_date,
            end_date,
            signal_types,
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

    def _build_rows(self, asset, df, start_date, end_date, signal_types):
        actual_dates = list(df.index)
        actual_index_by_date = {trading_date: index for index, trading_date in enumerate(actual_dates)}
        ordered_trading_dates = ordered_trading_dates_for_asset(asset, end_date)
        position_map = trading_date_positions(ordered_trading_dates)
        rows = []
        signal_type_set = set(signal_types)

        if signal_type_set & {'GOLDEN_CROSS', 'DEATH_CROSS', 'MA_BULL_ALIGN', 'MA_BEAR_ALIGN'}:
            rows.extend(self._ma_signal_rows(asset, df, start_date, end_date, actual_dates, actual_index_by_date, position_map))

        if signal_type_set & {'BB_SQUEEZE', 'BB_BREAKOUT_UP', 'BB_BREAKOUT_DOWN', 'BB_RSI_OVERBOUGHT', 'BB_RSI_OVERSOLD'}:
            rows.extend(self._bollinger_signal_rows(asset, df, start_date, end_date, actual_dates, actual_index_by_date, position_map))

        if signal_type_set & {'VOLUME_SPIKE', 'VOLUME_PRICE_DIVERGENCE'}:
            rows.extend(self._volume_signal_rows(asset, df, start_date, end_date, actual_dates, actual_index_by_date, position_map))

        if signal_type_set & {'MOMENTUM_UP_5D', 'MOMENTUM_DOWN_5D'}:
            rows.extend(self._momentum_signal_rows(asset, df, start_date, end_date, actual_dates, actual_index_by_date, position_map))

        if signal_type_set & {'OVERSOLD_COMBINATION'}:
            rows.extend(self._reversal_signal_rows(asset, df, start_date, end_date, actual_dates, actual_index_by_date, position_map))

        return [row for row in rows if row.signal_type in signal_type_set]

    def _iter_chunk_dates(self, actual_dates, start_date, end_date):
        for current_date in actual_dates:
            if start_date <= current_date <= end_date:
                yield current_date

    def _actual_window(self, actual_dates, actual_index, required_points):
        resolved_required_points = max(int(required_points or 0), 0)
        if resolved_required_points <= 0:
            return []
        return actual_dates[max(0, actual_index - resolved_required_points + 1):actual_index + 1]

    def _trailing_fresh_at(self, actual_dates, actual_index, current_date, position_map, *, indicator_type=None, parameters=None, required_points=None, max_gap=None):
        resolved_required_points = int(required_points or technical_indicator_gap_window_points(indicator_type, parameters))
        return trailing_indicator_window_is_fresh(
            self._actual_window(actual_dates, actual_index, resolved_required_points),
            current_date,
            position_map,
            indicator_type=indicator_type,
            parameters=parameters,
            required_points=resolved_required_points,
            max_gap=max_gap,
        )

    def _exact_window_at(self, actual_dates, actual_index, current_date, position_map, periods):
        resolved_periods = max(int(periods or 0), 0)
        return exact_trading_window_available(
            self._actual_window(actual_dates, actual_index, resolved_periods + 1),
            current_date,
            position_map,
            resolved_periods,
        )

    def _signal_row(self, asset, signal_type, trading_date, description, metadata):
        return SignalEvent(
            asset=asset,
            signal_type=signal_type,
            timestamp=_make_timestamp(trading_date),
            description=description,
            metadata=metadata,
        )

    def _ma_signal_rows(self, asset, df, start_date, end_date, actual_dates, actual_index_by_date, position_map):
        rows = []
        ma5 = pd.Series(talib.SMA(df['close'], timeperiod=5), index=df.index)
        ma10 = pd.Series(talib.SMA(df['close'], timeperiod=10), index=df.index)
        ma20 = pd.Series(talib.SMA(df['close'], timeperiod=20), index=df.index)
        ma60 = pd.Series(talib.SMA(df['close'], timeperiod=60), index=df.index)

        for current_date in self._iter_chunk_dates(actual_dates, start_date, end_date):
            actual_index = actual_index_by_date[current_date]
            if actual_index < 1:
                continue
            if not self._trailing_fresh_at(
                actual_dates,
                actual_index,
                current_date,
                position_map,
                indicator_type='SMA',
                parameters={'timeperiod': 60},
            ):
                continue

            if any(pd.isna(value) for value in (ma5.iloc[actual_index], ma20.iloc[actual_index], ma5.iloc[actual_index - 1], ma20.iloc[actual_index - 1])):
                continue

            today_ma5 = float(ma5.iloc[actual_index])
            today_ma10 = float(ma10.iloc[actual_index]) if not pd.isna(ma10.iloc[actual_index]) else None
            today_ma20 = float(ma20.iloc[actual_index])
            today_ma60 = float(ma60.iloc[actual_index]) if not pd.isna(ma60.iloc[actual_index]) else None
            prev_ma5 = float(ma5.iloc[actual_index - 1])
            prev_ma20 = float(ma20.iloc[actual_index - 1])
            close_today = float(df['close'].iloc[actual_index])

            if prev_ma5 <= prev_ma20 and today_ma5 > today_ma20:
                rows.append(self._signal_row(
                    asset,
                    'GOLDEN_CROSS',
                    current_date,
                    f'Golden Cross: MA5={today_ma5:.2f} crossed above MA20={today_ma20:.2f}',
                    {'ma5': today_ma5, 'ma20': today_ma20, 'close': close_today},
                ))

            if prev_ma5 >= prev_ma20 and today_ma5 < today_ma20:
                rows.append(self._signal_row(
                    asset,
                    'DEATH_CROSS',
                    current_date,
                    f'Death Cross: MA5={today_ma5:.2f} crossed below MA20={today_ma20:.2f}',
                    {'ma5': today_ma5, 'ma20': today_ma20, 'close': close_today},
                ))

            if today_ma10 is not None and today_ma60 is not None:
                if today_ma5 > today_ma10 > today_ma20 > today_ma60:
                    rows.append(self._signal_row(
                        asset,
                        'MA_BULL_ALIGN',
                        current_date,
                        (f'Bull MA Alignment: MA5={today_ma5:.2f} > MA10={today_ma10:.2f} '
                         f'> MA20={today_ma20:.2f} > MA60={today_ma60:.2f}'),
                        {'ma5': today_ma5, 'ma10': today_ma10, 'ma20': today_ma20, 'ma60': today_ma60},
                    ))
                elif today_ma5 < today_ma10 < today_ma20 < today_ma60:
                    rows.append(self._signal_row(
                        asset,
                        'MA_BEAR_ALIGN',
                        current_date,
                        (f'Bear MA Alignment: MA5={today_ma5:.2f} < MA10={today_ma10:.2f} '
                         f'< MA20={today_ma20:.2f} < MA60={today_ma60:.2f}'),
                        {'ma5': today_ma5, 'ma10': today_ma10, 'ma20': today_ma20, 'ma60': today_ma60},
                    ))

        return rows

    def _bollinger_signal_rows(self, asset, df, start_date, end_date, actual_dates, actual_index_by_date, position_map):
        rows = []
        upper, middle, lower = talib.BBANDS(df['close'], timeperiod=20, nbdevup=2, nbdevdn=2)
        upper = pd.Series(upper, index=df.index)
        middle = pd.Series(middle, index=df.index)
        lower = pd.Series(lower, index=df.index)
        rsi = pd.Series(talib.RSI(df['close'], timeperiod=14), index=df.index)

        for current_date in self._iter_chunk_dates(actual_dates, start_date, end_date):
            actual_index = actual_index_by_date[current_date]
            if not self._trailing_fresh_at(
                actual_dates,
                actual_index,
                current_date,
                position_map,
                indicator_type='BBANDS',
                parameters={'timeperiod': 20, 'nbdevup': 2, 'nbdevdn': 2},
            ):
                continue
            if any(pd.isna(value) for value in (upper.iloc[actual_index], middle.iloc[actual_index], lower.iloc[actual_index])):
                continue

            close = float(df['close'].iloc[actual_index])
            u = float(upper.iloc[actual_index])
            m = float(middle.iloc[actual_index])
            l = float(lower.iloc[actual_index])
            bandwidth = (u - l) / m if m > 0 else 0.0

            rsi_val = None
            if self._trailing_fresh_at(
                actual_dates,
                actual_index,
                current_date,
                position_map,
                indicator_type='RSI',
                parameters={'timeperiod': 14},
            ) and not pd.isna(rsi.iloc[actual_index]):
                rsi_val = float(rsi.iloc[actual_index])

            if bandwidth < 0.05:
                rows.append(self._signal_row(
                    asset,
                    'BB_SQUEEZE',
                    current_date,
                    f'Bollinger Band Squeeze: bandwidth={bandwidth:.4f} (< 5%)',
                    {'upper': u, 'middle': m, 'lower': l, 'bandwidth': bandwidth, 'close': close},
                ))

            if close > u:
                rows.append(self._signal_row(
                    asset,
                    'BB_BREAKOUT_UP',
                    current_date,
                    f'Price breakout above upper band: close={close:.2f} > upper={u:.2f}',
                    {'upper': u, 'middle': m, 'lower': l, 'close': close},
                ))

            if close < l:
                rows.append(self._signal_row(
                    asset,
                    'BB_BREAKOUT_DOWN',
                    current_date,
                    f'Price breakout below lower band: close={close:.2f} < lower={l:.2f}',
                    {'upper': u, 'middle': m, 'lower': l, 'close': close},
                ))

            if rsi_val is not None and close >= u * 0.98 and rsi_val > 70:
                rows.append(self._signal_row(
                    asset,
                    'BB_RSI_OVERBOUGHT',
                    current_date,
                    f'Overbought: close={close:.2f} near upper={u:.2f}, RSI={rsi_val:.1f}',
                    {'upper': u, 'lower': l, 'close': close, 'rsi': rsi_val},
                ))

            if rsi_val is not None and close <= l * 1.02 and rsi_val < 30:
                rows.append(self._signal_row(
                    asset,
                    'BB_RSI_OVERSOLD',
                    current_date,
                    f'Oversold: close={close:.2f} near lower={l:.2f}, RSI={rsi_val:.1f}',
                    {'upper': u, 'lower': l, 'close': close, 'rsi': rsi_val},
                ))

        return rows

    def _volume_signal_rows(self, asset, df, start_date, end_date, actual_dates, actual_index_by_date, position_map):
        rows = []
        obv = pd.Series(talib.OBV(df['close'], df['volume']), index=df.index)

        for current_date in self._iter_chunk_dates(actual_dates, start_date, end_date):
            actual_index = actual_index_by_date[current_date]
            if actual_index < 20:
                continue
            if not self._trailing_fresh_at(
                actual_dates,
                actual_index,
                current_date,
                position_map,
                required_points=20,
                max_gap=5,
            ):
                continue

            avg_volume = float(df['volume'].iloc[actual_index - 20:actual_index].mean())
            latest_volume = float(df['volume'].iloc[actual_index])
            latest_close = float(df['close'].iloc[actual_index])
            if avg_volume > 0:
                volume_ratio = latest_volume / avg_volume
                if volume_ratio >= 2.0:
                    rows.append(self._signal_row(
                        asset,
                        'VOLUME_SPIKE',
                        current_date,
                        f'Volume spike: {volume_ratio:.1f}x average ({latest_volume:.0f} vs avg {avg_volume:.0f})',
                        {'volume': latest_volume, 'avg_volume': avg_volume, 'ratio': volume_ratio, 'close': latest_close},
                    ))

            if actual_index < 5 or not self._exact_window_at(actual_dates, actual_index, current_date, position_map, 5):
                continue
            if any(pd.isna(value) for value in (obv.iloc[actual_index - 5], obv.iloc[actual_index])):
                continue

            obv_5d_ago = float(obv.iloc[actual_index - 5])
            obv_now = float(obv.iloc[actual_index])
            close_5d_ago = float(df['close'].iloc[actual_index - 5])
            price_5d_return = (latest_close - close_5d_ago) / close_5d_ago if close_5d_ago > 0 else 0.0
            obv_change = obv_now - obv_5d_ago

            if price_5d_return >= 0.03 and obv_change < 0:
                rows.append(self._signal_row(
                    asset,
                    'VOLUME_PRICE_DIVERGENCE',
                    current_date,
                    f'Bearish divergence: price +{price_5d_return:.1%} over 5d but OBV declining',
                    {'price_5d_return': price_5d_return, 'obv_change': obv_change, 'type': 'bearish'},
                ))
            elif price_5d_return <= -0.03 and obv_change > 0:
                rows.append(self._signal_row(
                    asset,
                    'VOLUME_PRICE_DIVERGENCE',
                    current_date,
                    f'Bullish divergence: price {price_5d_return:.1%} over 5d but OBV rising',
                    {'price_5d_return': price_5d_return, 'obv_change': obv_change, 'type': 'bullish'},
                ))

        return rows

    def _momentum_signal_rows(self, asset, df, start_date, end_date, actual_dates, actual_index_by_date, position_map):
        rows = []

        for current_date in self._iter_chunk_dates(actual_dates, start_date, end_date):
            actual_index = actual_index_by_date[current_date]
            if actual_index < 5 or not self._exact_window_at(actual_dates, actual_index, current_date, position_map, 5):
                continue

            latest_close = float(df['close'].iloc[actual_index])
            past_close_5d = float(df['close'].iloc[actual_index - 5])
            if past_close_5d <= 0:
                continue
            momentum_5d = (latest_close - past_close_5d) / past_close_5d

            if momentum_5d >= 0.05:
                rows.append(self._signal_row(
                    asset,
                    'MOMENTUM_UP_5D',
                    current_date,
                    f'Strong upward 5-day momentum: +{momentum_5d:.1%}',
                    {'momentum': momentum_5d, 'close': latest_close, 'close_5d_ago': past_close_5d},
                ))
            elif momentum_5d <= -0.05:
                rows.append(self._signal_row(
                    asset,
                    'MOMENTUM_DOWN_5D',
                    current_date,
                    f'Strong downward 5-day momentum: {momentum_5d:.1%}',
                    {'momentum': momentum_5d, 'close': latest_close, 'close_5d_ago': past_close_5d},
                ))

        return rows

    def _reversal_signal_rows(self, asset, df, start_date, end_date, actual_dates, actual_index_by_date, position_map):
        rows = []
        _upper, _middle, lower = talib.BBANDS(df['close'], timeperiod=20)
        lower = pd.Series(lower, index=df.index)
        rsi = pd.Series(talib.RSI(df['close'], timeperiod=14), index=df.index)

        for current_date in self._iter_chunk_dates(actual_dates, start_date, end_date):
            actual_index = actual_index_by_date[current_date]
            if actual_index < 20:
                continue
            if not self._trailing_fresh_at(
                actual_dates,
                actual_index,
                current_date,
                position_map,
                indicator_type='BBANDS',
                parameters={'timeperiod': 20},
            ):
                continue
            if not self._trailing_fresh_at(
                actual_dates,
                actual_index,
                current_date,
                position_map,
                indicator_type='RSI',
                parameters={'timeperiod': 14},
            ):
                continue
            if not self._trailing_fresh_at(
                actual_dates,
                actual_index,
                current_date,
                position_map,
                required_points=21,
                max_gap=5,
            ):
                continue
            if pd.isna(lower.iloc[actual_index]) or pd.isna(rsi.iloc[actual_index]):
                continue

            close = float(df['close'].iloc[actual_index])
            lower_band = float(lower.iloc[actual_index])
            rsi_val = float(rsi.iloc[actual_index])
            avg_volume = float(df['volume'].iloc[actual_index - 20:actual_index].mean())
            latest_volume = float(df['volume'].iloc[actual_index])
            near_lower = close <= lower_band * 1.02
            volume_contraction = avg_volume > 0 and latest_volume < avg_volume * 0.8

            if rsi_val < 30 and near_lower and volume_contraction:
                rows.append(self._signal_row(
                    asset,
                    'OVERSOLD_COMBINATION',
                    current_date,
                    (f'Oversold combination: RSI={rsi_val:.1f} < 30, '
                     f'close={close:.2f} near lower BB={lower_band:.2f}, '
                     f'volume={latest_volume:.0f} < 80% avg={avg_volume:.0f}'),
                    {'rsi': rsi_val, 'close': close, 'lower_bb': lower_band,
                     'volume': latest_volume, 'avg_volume': avg_volume},
                ))

        return rows