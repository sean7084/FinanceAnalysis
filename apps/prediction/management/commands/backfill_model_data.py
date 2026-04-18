from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import talib
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Min
from django.utils import timezone

from apps.analytics.models import TechnicalIndicator
from apps.factors.models import CapitalFlowSnapshot, FactorScore, FundamentalFactorSnapshot
from apps.factors.tasks import calculate_factor_scores_for_date
from apps.markets.models import Asset
from apps.markets.models import OHLCV
from apps.prediction.tasks_lightgbm import train_lightgbm_models
from apps.sentiment.models import NewsArticle, SentimentScore
from apps.sentiment.tasks import calculate_daily_sentiment


class Command(BaseCommand):
    help = 'Backfill model input data over a historical date range for heuristic and LightGBM pipelines.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', required=True, help='Inclusive start date in YYYY-MM-DD format.')
        parser.add_argument('--end-date', required=True, help='Inclusive end date in YYYY-MM-DD format.')
        parser.add_argument('--sentiment-weight', type=float, default=0.0)
        parser.add_argument('--skip-sentiment', action='store_true')
        parser.add_argument('--skip-rs-score', action='store_true')
        parser.add_argument('--skip-factor-scores', action='store_true')
        parser.add_argument('--train-lightgbm', action='store_true')

    def handle(self, *args, **options):
        start_date = self._parse_date(options['start_date'], 'start-date')
        end_date = self._parse_date(options['end_date'], 'end-date')
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

        self.stdout.write(
            self.style.NOTICE(
                f'Backfilling model inputs for {len(trading_dates)} trading dates from {trading_dates[0]} to {trading_dates[-1]}.'
            )
        )

        active_asset_ids = list(
            Asset.objects.filter(listing_status=Asset.ListingStatus.ACTIVE).values_list('id', flat=True)
        )
        earliest_article_date = NewsArticle.objects.aggregate(value=Min('published_at'))['value']
        earliest_article_date = earliest_article_date.date() if earliest_article_date else None

        if not options['skip_sentiment']:
            self._backfill_sentiment(trading_dates, active_asset_ids, earliest_article_date)

        if not options['skip_rs_score']:
            self._backfill_rs_scores(start_date, end_date, trading_dates)

        if not options['skip_factor_scores']:
            self._backfill_factor_scores(trading_dates, options['sentiment_weight'])

        if options['train_lightgbm']:
            result = train_lightgbm_models(
                training_start_date=str(start_date),
                training_end_date=str(end_date),
            )
            self.stdout.write(self.style.SUCCESS(f'LightGBM training result: {result}'))

        self.stdout.write(self.style.SUCCESS('Historical model data backfill complete.'))

    def _parse_date(self, value, label):
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise CommandError(f'Invalid {label}: {value}') from exc

    def _backfill_sentiment(self, trading_dates, active_asset_ids, earliest_article_date):
        self.stdout.write(self.style.NOTICE('Backfilling daily sentiment coverage...'))
        neutral_dates = [
            trading_date for trading_date in trading_dates
            if earliest_article_date is None or trading_date < earliest_article_date
        ]
        dynamic_dates = [
            trading_date for trading_date in trading_dates
            if earliest_article_date is not None and trading_date >= earliest_article_date
        ]

        if neutral_dates:
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
                for asset_id in active_asset_ids:
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

        for index, trading_date in enumerate(dynamic_dates, start=1):
            calculate_daily_sentiment(target_date=str(trading_date))
            if index % 25 == 0 or index == len(dynamic_dates):
                self.stdout.write(f'  sentiment: {index}/{len(dynamic_dates)} news-era dates complete')

    def _backfill_rs_scores(self, start_date, end_date, trading_dates):
        self.stdout.write(self.style.NOTICE('Backfilling historical RS_SCORE indicators...'))
        query_start = start_date - timedelta(days=40)
        rows = list(
            OHLCV.objects.filter(date__gte=query_start, date__lte=end_date)
            .values('asset_id', 'date', 'close')
            .order_by('date', 'asset_id')
        )
        if not rows:
            self.stdout.write('  rs_score: no OHLCV rows found, skipping')
            return

        frame = pd.DataFrame.from_records(rows)
        frame['close'] = frame['close'].astype(float)
        pivot = frame.pivot(index='date', columns='asset_id', values='close').sort_index()
        returns_20d = pivot.pct_change(periods=20, fill_method=None)

        indicators = []
        attempted = 0
        for index, trading_date in enumerate(trading_dates, start=1):
            if trading_date not in returns_20d.index:
                continue
            series = returns_20d.loc[trading_date].dropna()
            if series.empty:
                continue

            descending = series.sort_values(ascending=False)
            total = len(descending)
            timestamp = timezone.make_aware(datetime.combine(trading_date, datetime.min.time()))
            for rank, (asset_id, _) in enumerate(descending.items(), start=1):
                score = 1.0 - ((rank - 1) / total)
                indicators.append(
                    TechnicalIndicator(
                        asset_id=int(asset_id),
                        timestamp=timestamp,
                        indicator_type='RS_SCORE',
                        value=score,
                        parameters={},
                    )
                )
            if len(indicators) >= 5000:
                TechnicalIndicator.objects.bulk_create(indicators, batch_size=5000, ignore_conflicts=True)
                attempted += len(indicators)
                indicators = []
            if index % 100 == 0 or index == len(trading_dates):
                self.stdout.write(f'  rs_score: processed {index}/{len(trading_dates)} dates')

        if indicators:
            TechnicalIndicator.objects.bulk_create(indicators, batch_size=5000, ignore_conflicts=True)
            attempted += len(indicators)
        self.stdout.write(f'  rs_score: attempted {attempted} rows')

    def _backfill_factor_scores(self, trading_dates, sentiment_weight):
        if not FundamentalFactorSnapshot.objects.exists() and not CapitalFlowSnapshot.objects.exists() and float(sentiment_weight) == 0.0:
            return self._backfill_factor_scores_fast(trading_dates)

        self.stdout.write(self.style.NOTICE('Backfilling daily factor scores...'))
        for index, trading_date in enumerate(trading_dates, start=1):
            calculate_factor_scores_for_date(
                target_date=str(trading_date),
                sentiment_weight=sentiment_weight,
            )
            if index % 100 == 0 or index == len(trading_dates):
                self.stdout.write(f'  factor_scores: {index}/{len(trading_dates)} dates complete')

    def _backfill_factor_scores_fast(self, trading_dates):
        self.stdout.write(self.style.NOTICE('Backfilling daily factor scores with OHLCV-only fast path...'))
        start_date = trading_dates[0]
        end_date = trading_dates[-1]
        FactorScore.objects.filter(
            date__gte=start_date,
            date__lte=end_date,
            mode=FactorScore.FactorMode.COMPOSITE,
        ).delete()

        rows = list(
            OHLCV.objects.filter(date__gte=start_date, date__lte=end_date)
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

                pending.append(
                    FactorScore(
                        asset_id=int(asset_id),
                        date=row.date,
                        mode=FactorScore.FactorMode.COMPOSITE,
                        pe_percentile_score=None,
                        pb_percentile_score=None,
                        roe_trend_score=None,
                        northbound_flow_score=None,
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
                        metadata={'target_date': str(row.date), 'source': 'historical_ohlcv_fast_path'},
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