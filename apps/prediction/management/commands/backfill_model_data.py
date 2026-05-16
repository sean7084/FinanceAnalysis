from bisect import bisect_right
import json
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import talib
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Min
from django.utils import timezone

from apps.core.date_floor import get_historical_data_floor
from apps.analytics.models import SignalEvent, TechnicalIndicator
from apps.analytics.tasks import persist_ranked_rs_scores
from apps.analytics.technical_staleness import (
    asset_exchange_code,
    exact_trading_window_available,
    latest_official_trade_date,
    ordered_trading_dates_for_exchange,
    trading_date_positions,
)
from apps.analytics.indicator_warmup import (
    MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS,
    minimum_history_prefill_start_date,
)
from apps.factors.models import CapitalFlowSnapshot, FactorScore, FundamentalFactorSnapshot
from apps.factors.tasks import calculate_factor_scores_for_date
from apps.markets.benchmarking import (
    PITMembershipCoverageError,
    ensure_pit_membership_coverage,
    point_in_time_union_asset_ids_by_dates,
)
from apps.markets.models import Asset
from apps.markets.models import OHLCV
from apps.sentiment.models import NewsArticle, SentimentScore
from apps.sentiment.tasks import calculate_daily_sentiment, listed_asset_ids_for_date


class Command(BaseCommand):
    help = 'Backfill model input data over a historical date range for heuristic and LightGBM pipelines.'
    CHECKPOINT_VERSION = 1

    def add_arguments(self, parser):
        parser.add_argument('--start-date', help='Inclusive start date in YYYY-MM-DD format.')
        parser.add_argument('--end-date', help='Inclusive end date in YYYY-MM-DD format.')
        parser.add_argument('--sentiment-weight', type=float, default=0.0)
        parser.add_argument('--skip-sentiment', action='store_true')
        parser.add_argument('--checkpoint-file', default='')
        parser.add_argument('--resume-from-checkpoint', action='store_true')

    def handle(self, *args, **options):
        start_date, end_date, trading_dates, context_label = self._resolve_backfill_dates(options)
        self._configure_checkpoint(options, start_date, end_date, trading_dates)
        self._rs_prefill_start_date = minimum_history_prefill_start_date(start_date)

        self.stdout.write(
            self.style.NOTICE(
                'Applying model-data warm-up prefill: '
                f'calendar_prefill_days={MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS} '
                f'requested_start={start_date} '
                f'rs_score_prefill_start={self._rs_prefill_start_date}'
            )
        )

        try:
            ensure_pit_membership_coverage(
                trading_dates,
                context=context_label,
            )
        except PITMembershipCoverageError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.NOTICE(
                f'Backfilling model inputs for {len(trading_dates)} trading dates from {trading_dates[0]} to {trading_dates[-1]}.'
            )
        )

        historical_asset_windows = list(
            Asset.objects.filter(list_date__isnull=False).values_list('id', 'list_date', 'delist_date')
        )
        earliest_article_date = NewsArticle.objects.aggregate(value=Min('published_at'))['value']
        earliest_article_date = earliest_article_date.date() if earliest_article_date else None

        if not options['skip_sentiment']:
            self._run_stage(
                'sentiment',
                lambda: self._backfill_sentiment(trading_dates, historical_asset_windows, earliest_article_date),
            )

        self._run_stage(
            'rs_score',
            lambda: self._backfill_rs_scores(start_date, end_date, trading_dates),
        )

        self._run_stage(
            'factor_scores',
            lambda: self._backfill_factor_scores(
                trading_dates,
                options['sentiment_weight'],
            ),
        )

        self._write_timing_summary()
        self.stdout.write(self.style.SUCCESS('Historical model data backfill complete.'))

    def _checkpoint_metadata(self, options, start_date, end_date, trading_dates):
        return {
            'version': self.CHECKPOINT_VERSION,
            'command': 'backfill_model_data',
            'window': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'trading_dates_count': len(trading_dates),
            },
            'options': {
                'skip_sentiment': bool(options.get('skip_sentiment')),
                'sentiment_weight': float(options.get('sentiment_weight') or 0.0),
            },
            'stages': {},
        }

    def _configure_checkpoint(self, options, start_date, end_date, trading_dates):
        self._stage_timings = {}
        self._resume_from_checkpoint = bool(options.get('resume_from_checkpoint'))
        checkpoint_file = str(options.get('checkpoint_file') or '').strip()
        self._checkpoint_path = Path(checkpoint_file).expanduser() if checkpoint_file else None
        self._checkpoint = self._checkpoint_metadata(options, start_date, end_date, trading_dates)
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
                expected_window = self._checkpoint['window']
                actual_window = existing.get('window', {})
                if (
                    existing.get('command') != 'backfill_model_data' or
                    actual_window.get('start_date') != expected_window['start_date'] or
                    actual_window.get('end_date') != expected_window['end_date']
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

    def _stage_state(self, stage_name):
        stages = self._checkpoint.setdefault('stages', {})
        return stages.setdefault(stage_name, {'status': 'pending'})

    def _stage_is_complete(self, stage_name):
        return self._resume_from_checkpoint and self._stage_state(stage_name).get('status') == 'completed'

    def _mark_stage_progress(self, stage_name, *, last_completed_date=None, details=None, status=None):
        stage_state = self._stage_state(stage_name)
        if last_completed_date is not None:
            stage_state['last_completed_date'] = last_completed_date.isoformat()
        if details:
            stage_state.setdefault('details', {}).update(details)
        if status is not None:
            stage_state['status'] = status
        self._write_checkpoint()

    def _resume_from_date(self, trading_dates, stage_name):
        if not self._resume_from_checkpoint:
            return list(trading_dates)

        last_completed = self._stage_state(stage_name).get('last_completed_date')
        if not last_completed:
            return list(trading_dates)

        last_completed_date = date.fromisoformat(last_completed)
        remaining_dates = [trading_date for trading_date in trading_dates if trading_date > last_completed_date]
        if remaining_dates:
            self.stdout.write(
                f'  {stage_name}: checkpoint resume skips through {last_completed_date} and restarts at {remaining_dates[0]}'
            )
        else:
            self.stdout.write(
                f'  {stage_name}: checkpoint already covers the requested range through {last_completed_date}'
            )
        return remaining_dates

    def _run_stage(self, stage_name, callback):
        if self._stage_is_complete(stage_name):
            self.stdout.write(f'  {stage_name}: checkpoint already completed, skipping')
            self._stage_timings[stage_name] = {
                'elapsed_seconds': 0.0,
                'status': 'skipped',
            }
            return None

        stage_state = self._stage_state(stage_name)
        stage_state['status'] = 'running'
        stage_state.setdefault('started_at', timezone.now().isoformat())
        self._write_checkpoint()

        started = time.perf_counter()
        try:
            result = callback()
        except Exception:
            elapsed = time.perf_counter() - started
            stage_state['last_run_seconds'] = round(elapsed, 3)
            stage_state['status'] = 'failed'
            self._write_checkpoint()
            raise

        elapsed = time.perf_counter() - started
        stage_state['status'] = 'completed'
        stage_state['completed_at'] = timezone.now().isoformat()
        stage_state['last_run_seconds'] = round(elapsed, 3)
        stage_state['total_elapsed_seconds'] = round(float(stage_state.get('total_elapsed_seconds', 0.0)) + elapsed, 3)
        self._write_checkpoint()
        self._stage_timings[stage_name] = {
            'elapsed_seconds': elapsed,
            'status': 'completed',
        }
        return result

    def _write_timing_summary(self):
        if not self._stage_timings:
            return
        self.stdout.write(self.style.NOTICE('Stage timing summary:'))
        for stage_name, payload in self._stage_timings.items():
            elapsed = float(payload.get('elapsed_seconds', 0.0))
            status = payload.get('status', 'completed')
            self.stdout.write(f'  {stage_name}: {elapsed:.3f}s ({status})')

    def _parse_date(self, value, label):
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid {label}: {value}') from exc

    def _resolve_backfill_dates(self, options):
        start_value = options.get('start_date')
        end_value = options.get('end_date')

        if not start_value or not end_value:
            raise CommandError('start-date and end-date are required.')

        start_date = self._parse_date(start_value, 'start-date')
        end_date = self._parse_date(end_value, 'end-date')
        floor_date = get_historical_data_floor()
        if start_date < floor_date:
            raise CommandError(f'start-date cannot be earlier than HISTORICAL_DATA_FLOOR={floor_date}.')
        if end_date < start_date:
            raise CommandError('end-date must be on or after start-date.')

        trading_dates = list(
            OHLCV.objects.filter(date__gte=start_date, date__lte=end_date)
            .values_list('date', flat=True)
            .distinct()
            .order_by('date')
        )
        if not trading_dates:
            raise CommandError('No OHLCV trading dates found in the requested range.')
        return (
            start_date,
            end_date,
            trading_dates,
            f'Historical model data backfill for {start_date}..{end_date}',
        )

    def _backfill_sentiment(self, trading_dates, historical_asset_windows, earliest_article_date):
        self.stdout.write(self.style.NOTICE('Backfilling daily sentiment coverage...'))
        stage_name = 'sentiment'
        stage_state = self._stage_state(stage_name)
        neutral_dates = [
            trading_date for trading_date in trading_dates
            if earliest_article_date is None or trading_date < earliest_article_date
        ]
        dynamic_dates = [
            trading_date for trading_date in trading_dates
            if earliest_article_date is not None and trading_date >= earliest_article_date
        ]

        neutral_completed = bool(stage_state.get('details', {}).get('neutral_completed'))
        if neutral_dates and not neutral_completed:
            self.stdout.write(
                f'  sentiment: bulk-filling neutral coverage for {len(neutral_dates)} pre-news trading dates'
            )
            SentimentScore.objects.filter(
                date__gte=neutral_dates[0],
                date__lte=neutral_dates[-1],
                score_type__in=[SentimentScore.ScoreType.ASSET_7D, SentimentScore.ScoreType.MARKET_7D],
            ).delete()

            buffer = []
            for trading_date in neutral_dates:
                for asset_id in listed_asset_ids_for_date(trading_date, asset_windows=historical_asset_windows):
                    buffer.append(
                        SentimentScore(
                            article=None,
                            asset_id=asset_id,
                            date=trading_date,
                            score_type=SentimentScore.ScoreType.ASSET_7D,
                            positive_score=Decimal('0.000000'),
                            neutral_score=Decimal('1.000000'),
                            negative_score=Decimal('0.000000'),
                            sentiment_score=Decimal('0.000000'),
                            sentiment_label=SentimentScore.Label.NEUTRAL,
                            metadata={'window_days': 7, 'fallback': 'historical_pre_news_bulk_fill'},
                        )
                    )
                buffer.append(
                    SentimentScore(
                        article=None,
                        asset=None,
                        date=trading_date,
                        score_type=SentimentScore.ScoreType.MARKET_7D,
                        positive_score=Decimal('0.000000'),
                        neutral_score=Decimal('1.000000'),
                        negative_score=Decimal('0.000000'),
                        sentiment_score=Decimal('0.000000'),
                        sentiment_label=SentimentScore.Label.NEUTRAL,
                        metadata={'window_days': 7, 'fallback': 'historical_pre_news_bulk_fill'},
                    )
                )
                if len(buffer) >= 5000:
                    SentimentScore.objects.bulk_create(buffer, batch_size=5000)
                    buffer = []

            if buffer:
                SentimentScore.objects.bulk_create(buffer, batch_size=5000)

            self._mark_stage_progress(stage_name, details={'neutral_completed': True})

        dynamic_dates = self._resume_from_date(dynamic_dates, stage_name)
        if not dynamic_dates:
            return

        stage_started = time.perf_counter()
        total_dynamic_dates = len(dynamic_dates)

        for index, trading_date in enumerate(dynamic_dates, start=1):
            calculate_daily_sentiment(target_date=str(trading_date))
            self._mark_stage_progress(stage_name, last_completed_date=trading_date)
            if index % 25 == 0 or index == len(dynamic_dates):
                elapsed = time.perf_counter() - stage_started
                self.stdout.write(
                    f'  sentiment: {index}/{total_dynamic_dates} news-era dates complete '
                    f'({elapsed:.1f}s elapsed, {elapsed / index:.3f}s/date)'
                )

    def _backfill_rs_scores(self, start_date, end_date, trading_dates):
        self.stdout.write(self.style.NOTICE('Backfilling historical RS_SCORE indicators...'))
        stage_name = 'rs_score'
        trading_dates = self._resume_from_date(trading_dates, stage_name)
        if not trading_dates:
            self.stdout.write('  rs_score: no remaining dates to process after checkpoint resume')
            return

        membership_by_date = point_in_time_union_asset_ids_by_dates(trading_dates)
        ever_union_asset_ids = sorted({
            asset_id
            for asset_ids in membership_by_date.values()
            for asset_id in asset_ids
        })
        if not ever_union_asset_ids:
            self.stdout.write('  rs_score: no PIT-union assets found, skipping')
            return

        query_start = min(start_date - timedelta(days=40), self._rs_prefill_start_date)
        rows = list(
            OHLCV.objects.filter(
                asset_id__in=ever_union_asset_ids,
                date__gte=query_start,
                date__lte=end_date,
            )
            .values('asset_id', 'date', 'close')
            .order_by('date', 'asset_id')
        )
        if not rows:
            self.stdout.write('  rs_score: no OHLCV rows found, skipping')
            return

        actual_dates_by_asset = {}
        for row in rows:
            actual_dates_by_asset.setdefault(row['asset_id'], []).append(row['date'])

        frame = pd.DataFrame.from_records(rows)
        frame['close'] = frame['close'].astype(float)
        pivot = frame.pivot(index='date', columns='asset_id', values='close').sort_index()

        asset_map = {
            asset.id: asset
            for asset in Asset.objects.select_related('market').filter(id__in=ever_union_asset_ids)
        }
        exchange_codes = {asset_exchange_code(asset) for asset in asset_map.values()}
        ordered_trading_dates_by_exchange = {
            exchange_code: ordered_trading_dates_for_exchange(exchange_code, end_date)
            for exchange_code in exchange_codes
        }
        position_maps_by_exchange = {
            exchange_code: trading_date_positions(ordered_dates)
            for exchange_code, ordered_dates in ordered_trading_dates_by_exchange.items()
        }

        delete_start = trading_dates[0]
        delete_end = trading_dates[-1]
        TechnicalIndicator.objects.filter(
            indicator_type='RS_SCORE',
            timestamp__date__gte=delete_start,
            timestamp__date__lte=delete_end,
        ).delete()
        SignalEvent.objects.filter(
            signal_type='HIGH_RS_SCORE',
            timestamp__date__gte=delete_start,
            timestamp__date__lte=delete_end,
        ).delete()

        attempted_indicators = 0
        attempted_signals = 0
        stage_started = time.perf_counter()
        for index, trading_date in enumerate(trading_dates, start=1):
            union_asset_ids = membership_by_date.get(trading_date) or set()
            ranked_scores = []
            for asset_id in union_asset_ids:
                asset = asset_map.get(asset_id)
                if asset is None:
                    continue
                exchange_code = asset_exchange_code(asset)
                ordered_trading_dates = ordered_trading_dates_by_exchange.get(exchange_code, ())
                position_map = position_maps_by_exchange.get(exchange_code, {})
                current_trade_date = latest_official_trade_date(ordered_trading_dates, trading_date)
                if current_trade_date is None:
                    continue
                current_position = position_map.get(current_trade_date)
                if current_position is None or current_position < 20:
                    continue

                actual_dates = actual_dates_by_asset.get(asset_id, [])
                actual_index = bisect_right(actual_dates, current_trade_date)
                window_dates = actual_dates[max(0, actual_index - 21):actual_index]
                if not exact_trading_window_available(window_dates, current_trade_date, position_map, 20):
                    continue

                anchor_date = window_dates[0]
                if current_trade_date not in pivot.index or anchor_date not in pivot.index:
                    continue
                current_close = pivot.at[current_trade_date, asset_id] if asset_id in pivot.columns else None
                anchor_close = pivot.at[anchor_date, asset_id] if asset_id in pivot.columns else None
                if pd.isna(current_close) or pd.isna(anchor_close) or float(anchor_close) <= 0:
                    continue
                momentum_20d = (float(current_close) - float(anchor_close)) / float(anchor_close)
                ranked_scores.append((asset_id, momentum_20d))

            if not ranked_scores:
                self._mark_stage_progress(stage_name, last_completed_date=trading_date)
                continue

            descending = sorted(ranked_scores, key=lambda item: item[1], reverse=True)
            timestamp = timezone.make_aware(datetime.combine(trading_date, datetime.min.time()))
            result = persist_ranked_rs_scores(descending, timestamp)
            attempted_indicators += result['indicator_rows']
            attempted_signals += result['signal_rows']
            self._mark_stage_progress(stage_name, last_completed_date=trading_date)
            if index % 100 == 0 or index == len(trading_dates):
                elapsed = time.perf_counter() - stage_started
                self.stdout.write(
                    f'  rs_score: processed {index}/{len(trading_dates)} dates '
                    f'({elapsed:.1f}s elapsed, {elapsed / index:.3f}s/date)'
                )

        self.stdout.write(
            f'  rs_score: attempted {attempted_indicators} indicator rows and {attempted_signals} HIGH_RS_SCORE rows'
        )

    def _backfill_factor_scores(self, trading_dates, sentiment_weight):
        stage_name = 'factor_scores'
        trading_dates = self._resume_from_date(trading_dates, stage_name)
        if not trading_dates:
            self.stdout.write('  factor_scores: no remaining dates to process after checkpoint resume')
            return

        self._clear_factor_score_window(trading_dates)

        if not FundamentalFactorSnapshot.objects.exists() and not CapitalFlowSnapshot.objects.exists() and float(sentiment_weight) == 0.0:
            return self._backfill_factor_scores_fast(trading_dates)

        self.stdout.write(self.style.NOTICE('Backfilling daily factor scores...'))
        stage_started = time.perf_counter()
        for index, trading_date in enumerate(trading_dates, start=1):
            calculate_factor_scores_for_date(
                target_date=str(trading_date),
                sentiment_weight=sentiment_weight,
            )
            self._mark_stage_progress(stage_name, last_completed_date=trading_date)
            if index % 100 == 0 or index == len(trading_dates):
                elapsed = time.perf_counter() - stage_started
                self.stdout.write(
                    f'  factor_scores: {index}/{len(trading_dates)} dates complete '
                    f'({elapsed:.1f}s elapsed, {elapsed / index:.3f}s/date)'
                )

    def _clear_factor_score_window(self, trading_dates):
        delete_start = trading_dates[0]
        delete_end = trading_dates[-1]
        deleted_count, _ = FactorScore.objects.filter(
            date__gte=delete_start,
            date__lte=delete_end,
            mode=FactorScore.FactorMode.COMPOSITE,
        ).delete()
        self.stdout.write(
            f'  factor_scores: deleted {deleted_count} existing COMPOSITE rows '
            f'from {delete_start} to {delete_end}'
        )

    def _backfill_factor_scores_fast(self, trading_dates):
        self.stdout.write(self.style.NOTICE('Backfilling daily factor scores with OHLCV-only fast path...'))
        start_date = trading_dates[0]
        end_date = trading_dates[-1]

        membership_by_date = point_in_time_union_asset_ids_by_dates(trading_dates)
        ever_union_asset_ids = sorted({
            asset_id
            for asset_ids in membership_by_date.values()
            for asset_id in asset_ids
        })
        if not ever_union_asset_ids:
            self.stdout.write('  factor_scores: no PIT-union assets found, skipping fast path')
            return

        rows = list(
            OHLCV.objects.filter(
                asset_id__in=ever_union_asset_ids,
                date__gte=start_date,
                date__lte=end_date,
            )
            .values('asset_id', 'date', 'close', 'volume')
            .order_by('asset_id', 'date')
        )
        if not rows:
            self.stdout.write('  factor_scores: no OHLCV rows found, skipping')
            return

        frame = pd.DataFrame.from_records(rows)
        frame['close'] = frame['close'].astype(float)
        frame['volume'] = frame['volume'].astype(float)

        pending = []
        created = 0
        for index, (asset_id, asset_frame) in enumerate(frame.groupby('asset_id'), start=1):
            asset_frame = asset_frame.sort_values('date').copy()
            close_series = asset_frame['close']
            volume_series = asset_frame['volume']
            asset_frame['rsi'] = talib.RSI(close_series, timeperiod=14)
            _, _, lower_band = talib.BBANDS(close_series, timeperiod=20, nbdevup=2, nbdevdn=2)
            asset_frame['lower_band'] = lower_band
            asset_frame['avg_volume_20'] = volume_series.shift(1).rolling(20).mean()

            for row in asset_frame.itertuples(index=False):
                technical_score = Decimal('0')
                rsi_value = getattr(row, 'rsi')
                lower_value = getattr(row, 'lower_band')
                close_value = Decimal(str(row.close))

                if pd.notna(rsi_value) and Decimal(str(rsi_value)) <= Decimal('35'):
                    technical_score += Decimal('0.35')

                if pd.notna(lower_value) and close_value <= Decimal(str(lower_value)) * Decimal('1.03'):
                    technical_score += Decimal('0.25')

                avg_volume = getattr(row, 'avg_volume_20')
                if (
                    pd.notna(rsi_value) and
                    pd.notna(lower_value) and
                    pd.notna(avg_volume) and
                    Decimal(str(rsi_value)) < Decimal('30') and
                    close_value <= Decimal(str(lower_value)) * Decimal('1.02') and
                    Decimal(str(row.volume)) < Decimal(str(avg_volume)) * Decimal('0.8')
                ):
                    technical_score += Decimal('0.40')

                technical_score = min(technical_score, Decimal('1'))
                composite_score = Decimal('0.35') + (technical_score * Decimal('0.3'))
                row_date = row.date
                if hasattr(row_date, 'date'):
                    row_date = row_date.date()
                if int(asset_id) not in membership_by_date.get(row_date, set()):
                    continue

                pending.append(
                    FactorScore(
                        asset_id=int(asset_id),
                        date=row_date,
                        mode=FactorScore.FactorMode.COMPOSITE,
                        pe_percentile_score=None,
                        pe_ttm_percentile_score=None,
                        pb_percentile_score=None,
                        roe_trend_score=None,
                        main_force_flow_score=None,
                        margin_flow_score=None,
                        technical_reversal_score=technical_score,
                        sentiment_score=Decimal('0.5'),
                        fundamental_score=Decimal('0.5'),
                        capital_flow_score=Decimal('0.5'),
                        technical_score=technical_score,
                        financial_weight=Decimal('0.4'),
                        flow_weight=Decimal('0.3'),
                        technical_weight=Decimal('0.3'),
                        sentiment_weight=Decimal('0.0'),
                        composite_score=composite_score,
                        bottom_probability_score=composite_score,
                        metadata={'target_date': str(row_date), 'source': 'historical_ohlcv_fast_path'},
                    )
                )

            if len(pending) >= 5000:
                FactorScore.objects.bulk_create(pending, batch_size=5000, ignore_conflicts=True)
                created += len(pending)
                pending = []
            if index % 25 == 0:
                self.stdout.write(f'  factor_scores: processed {index} assets')

        if pending:
            FactorScore.objects.bulk_create(pending, batch_size=5000, ignore_conflicts=True)
            created += len(pending)

        self.stdout.write(f'  factor_scores: created {created} rows via fast path')