import json
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.factors.models import FundamentalFactorSnapshot
from apps.factors.models import FactorScore
from apps.analytics.indicator_warmup import MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS
from apps.markets.benchmarking import PITMembershipCoverageError
from apps.markets.models import Asset, IndexMembership, Market, OHLCV
from apps.analytics.models import SignalEvent, TechnicalIndicator
from apps.sentiment.models import SentimentScore
from .models import ModelVersion, PredictionResult
from .models_lightgbm import EnsembleWeightSnapshot, LightGBMModelArtifact
from .tasks import generate_predictions_for_date
from .tasks_lightgbm import _create_feature_matrix, _create_labels_for_training
from .tasks_lstm import train_lstm_models


class Phase14PredictionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='phase14_user',
            email='phase14@example.com',
            password='Passw0rd!123',
        )
        self.market = Market.objects.create(code='P14', name='Phase 14 Market')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='605001',
            ts_code='605001.SH',
            name='Prediction Asset',
        )

    def _auth(self):
        self.client.force_authenticate(user=self.user)

    def _seed_features(self):
        d = timezone.now().date()
        self._seed_required_pit_membership(self.asset, d)
        FactorScore.objects.create(
            asset=self.asset,
            date=d,
            mode=FactorScore.FactorMode.COMPOSITE,
            fundamental_score=Decimal('0.62'),
            capital_flow_score=Decimal('0.58'),
            technical_score=Decimal('0.51'),
            composite_score=Decimal('0.57'),
            bottom_probability_score=Decimal('0.57'),
        )
        SentimentScore.objects.create(
            article=None,
            asset=self.asset,
            date=d,
            score_type=SentimentScore.ScoreType.ASSET_7D,
            positive_score=Decimal('0.6'),
            neutral_score=Decimal('0.2'),
            negative_score=Decimal('0.2'),
            sentiment_score=Decimal('0.3'),
            sentiment_label=SentimentScore.Label.POSITIVE,
        )
        for offset in range(30):
            as_of = d - timezone.timedelta(days=offset)
            OHLCV.objects.create(
                asset=self.asset,
                date=as_of,
                open=Decimal('10.0'),
                high=Decimal('10.8') + Decimal(offset) / Decimal('100'),
                low=Decimal('9.6') - Decimal(offset) / Decimal('200'),
                close=Decimal('10.2'),
                adj_close=Decimal('10.2'),
                volume=1000000,
                amount=Decimal('10200000'),
            )
        TechnicalIndicator.objects.create(
            asset=self.asset,
            timestamp=timezone.make_aware(timezone.datetime.combine(d, timezone.datetime.min.time())),
            indicator_type='BBANDS',
            value=Decimal('10.2'),
            parameters={'timeperiod': 20, 'upper': 10.9, 'middle': 10.2, 'lower': 9.7},
        )
        TechnicalIndicator.objects.create(
            asset=self.asset,
            timestamp=timezone.make_aware(timezone.datetime.combine(d, timezone.datetime.min.time())),
            indicator_type='SMA',
            value=Decimal('9.9'),
            parameters={'timeperiod': 60},
        )
        return d

    def _seed_required_pit_membership(self, asset, trade_date, index_codes=('000300.SH', '000510.CSI')):
        for index_code, index_name in (
            ('000300.SH', 'CSI 300'),
            ('000510.CSI', 'CSI A500'),
        ):
            if index_code not in index_codes:
                continue
            IndexMembership.objects.create(
                asset=asset,
                index_code=index_code,
                index_name=index_name,
                trade_date=trade_date,
                weight=Decimal('1.000000'),
            )

    def _seed_features_for_asset(self, asset, index_codes=('000300.SH', '000510.CSI')):
        d = timezone.now().date()
        self._seed_required_pit_membership(asset, d, index_codes=index_codes)
        FactorScore.objects.create(
            asset=asset,
            date=d,
            mode=FactorScore.FactorMode.COMPOSITE,
            fundamental_score=Decimal('0.62'),
            capital_flow_score=Decimal('0.58'),
            technical_score=Decimal('0.51'),
            composite_score=Decimal('0.57'),
            bottom_probability_score=Decimal('0.57'),
        )
        SentimentScore.objects.create(
            article=None,
            asset=asset,
            date=d,
            score_type=SentimentScore.ScoreType.ASSET_7D,
            positive_score=Decimal('0.6'),
            neutral_score=Decimal('0.2'),
            negative_score=Decimal('0.2'),
            sentiment_score=Decimal('0.3'),
            sentiment_label=SentimentScore.Label.POSITIVE,
        )
        for offset in range(30):
            as_of = d - timezone.timedelta(days=offset)
            OHLCV.objects.create(
                asset=asset,
                date=as_of,
                open=Decimal('10.0'),
                high=Decimal('10.8') + Decimal(offset) / Decimal('100'),
                low=Decimal('9.6') - Decimal(offset) / Decimal('200'),
                close=Decimal('10.2'),
                adj_close=Decimal('10.2'),
                volume=1000000,
                amount=Decimal('10200000'),
            )
        TechnicalIndicator.objects.create(
            asset=asset,
            timestamp=timezone.make_aware(timezone.datetime.combine(d, timezone.datetime.min.time())),
            indicator_type='BBANDS',
            value=Decimal('10.2'),
            parameters={'timeperiod': 20, 'upper': 10.9, 'middle': 10.2, 'lower': 9.7},
        )
        TechnicalIndicator.objects.create(
            asset=asset,
            timestamp=timezone.make_aware(timezone.datetime.combine(d, timezone.datetime.min.time())),
            indicator_type='SMA',
            value=Decimal('9.9'),
            parameters={'timeperiod': 60},
        )
        return d

    def test_prediction_endpoints_require_auth(self):
        response = self.client.get(f'/api/v1/prediction/{self.asset.symbol}/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        response = self.client.post('/api/v1/prediction/batch/', {'stock_codes': [self.asset.symbol]}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_prediction_history_list_routes_are_not_exposed(self):
        response = self.client.get('/api/v1/prediction/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        response = self.client.get('/api/v1/lstm-predictions/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_generate_predictions_task_creates_snapshot_rows(self):
        d = self._seed_features()
        generate_predictions_for_date(target_date=str(d), horizons=[3, 7, 30])
        self.assertEqual(PredictionResult.objects.filter(asset=self.asset, date=d).count(), 3)
        prediction = PredictionResult.objects.filter(asset=self.asset, date=d, horizon_days=7).first()
        self.assertIsNotNone(prediction.target_price)
        self.assertIsNotNone(prediction.stop_loss_price)
        self.assertIsNotNone(prediction.risk_reward_ratio)
        self.assertIsNotNone(prediction.trade_score)

    def test_generate_predictions_task_filters_to_point_in_time_effective_universe(self):
        included_asset = self.asset
        excluded_asset = Asset.objects.create(
            market=self.market,
            symbol='605002',
            ts_code='605002.SH',
            name='Excluded Prediction Asset',
        )

        d = self._seed_features_for_asset(included_asset)
        self._seed_features_for_asset(excluded_asset, index_codes=())

        generate_predictions_for_date(target_date=str(d), horizons=[7])

        self.assertTrue(PredictionResult.objects.filter(asset=included_asset, date=d, horizon_days=7).exists())
        self.assertFalse(PredictionResult.objects.filter(asset=excluded_asset, date=d, horizon_days=7).exists())

    def test_generate_predictions_task_raises_when_required_pit_membership_is_missing(self):
        d = timezone.now().date()

        FactorScore.objects.create(
            asset=self.asset,
            date=d,
            mode=FactorScore.FactorMode.COMPOSITE,
            fundamental_score=Decimal('0.62'),
            capital_flow_score=Decimal('0.58'),
            technical_score=Decimal('0.51'),
            composite_score=Decimal('0.57'),
            bottom_probability_score=Decimal('0.57'),
        )

        with self.assertRaisesMessage(
            PITMembershipCoverageError,
            'missing point-in-time membership coverage',
        ):
            generate_predictions_for_date(target_date=str(d), horizons=[7])

    def test_generate_predictions_ignores_future_ohlcv_rows(self):
        d = self._seed_features()
        generate_predictions_for_date(target_date=str(d), horizons=[7])
        baseline = PredictionResult.objects.get(asset=self.asset, date=d, horizon_days=7)
        baseline_payload = dict(baseline.feature_payload)
        baseline_up = baseline.up_probability

        OHLCV.objects.create(
            asset=self.asset,
            date=d + timezone.timedelta(days=1),
            open=Decimal('30.0'),
            high=Decimal('32.0'),
            low=Decimal('29.5'),
            close=Decimal('31.5'),
            adj_close=Decimal('31.5'),
            volume=6000000,
            amount=Decimal('189000000'),
        )

        generate_predictions_for_date(target_date=str(d), horizons=[7])
        refreshed = PredictionResult.objects.get(asset=self.asset, date=d, horizon_days=7)

        self.assertEqual(refreshed.feature_payload, baseline_payload)
        self.assertEqual(refreshed.up_probability, baseline_up)

    def test_single_stock_prediction_endpoint_shape(self):
        self._auth()
        d = self._seed_features()
        generate_predictions_for_date(target_date=str(d), horizons=[3, 7, 30])

        response = self.client.get(f'/api/v1/prediction/{self.asset.symbol}/?date={d}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['stock_code'], self.asset.symbol)
        self.assertEqual(len(response.data['results']), 3)
        first = response.data['results'][0]
        self.assertIn('up', first)
        self.assertIn('flat', first)
        self.assertIn('down', first)
        self.assertIn('confidence', first)
        self.assertIn('target_price', first)
        self.assertIn('stop_loss_price', first)
        self.assertIn('trade_score', first)
        self.assertIn('suggested', first)

    def test_batch_prediction_endpoint(self):
        self._auth()
        d = self._seed_features()
        response = self.client.post(
            '/api/v1/prediction/batch/',
            {
                'date': str(d),
                'stock_codes': [self.asset.symbol],
                'horizons': [3, 7],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(self.asset.symbol, response.data['results'])
        self.assertEqual(len(response.data['results'][self.asset.symbol]), 2)

    @patch('apps.prediction.views.generate_predictions_for_date.delay')
    @patch('apps.prediction.views.train_prediction_models.delay')
    def test_recalculate_endpoint_queues_tasks(self, mock_train_delay, mock_predict_delay):
        self._auth()
        response = self.client.post('/api/v1/prediction/recalculate/', {'target_date': str(timezone.now().date())}, format='json')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_train_delay.assert_called_once()
        mock_predict_delay.assert_called_once()


class BackfillModelDataCommandTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='P14CMD', name='Phase 14 Command Market')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='600001',
            ts_code='600001.SH',
            name='Command Asset',
        )
        self.trading_dates = [
            timezone.datetime(2024, 1, 2).date(),
            timezone.datetime(2024, 1, 3).date(),
            timezone.datetime(2024, 1, 4).date(),
        ]
        for trading_date in self.trading_dates:
            OHLCV.objects.create(
                asset=self.asset,
                date=trading_date,
                open=Decimal('10.0'),
                high=Decimal('10.5'),
                low=Decimal('9.8'),
                close=Decimal('10.2'),
                adj_close=Decimal('10.2'),
                volume=100000,
                amount=Decimal('1020000'),
            )

        FundamentalFactorSnapshot.objects.create(
            asset=self.asset,
            date=self.trading_dates[0],
            pe=Decimal('10.0'),
            pe_ttm=Decimal('9.6'),
            pb=Decimal('1.0'),
            roe=Decimal('0.12'),
            roe_qoq=Decimal('0.01'),
            metadata={'source': 'test'},
        )
        FactorScore.objects.create(
            asset=self.asset,
            date=self.trading_dates[0],
            mode=FactorScore.FactorMode.COMPOSITE,
            technical_reversal_score=Decimal('0.1'),
            sentiment_score=Decimal('0.5'),
            fundamental_score=Decimal('0.5'),
            capital_flow_score=Decimal('0.5'),
            technical_score=Decimal('0.1'),
            financial_weight=Decimal('0.4'),
            flow_weight=Decimal('0.3'),
            technical_weight=Decimal('0.3'),
            sentiment_weight=Decimal('0.0'),
            composite_score=Decimal('0.3'),
            bottom_probability_score=Decimal('0.3'),
            metadata={'source': 'phase11_scoring_with_sentiment'},
        )

    @patch('apps.prediction.management.commands.backfill_model_data.calculate_factor_scores_for_date')
    def test_backfill_model_data_recomputes_factor_scores_for_full_requested_range(self, mock_calculate):
        IndexMembership.objects.create(
            asset=self.asset,
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date='2024-01-02',
            weight=Decimal('4.2'),
        )

        call_command(
            'backfill_model_data',
            start_date='2024-01-02',
            end_date='2024-01-04',
            skip_sentiment=True,
        )

        self.assertEqual(mock_calculate.call_count, 3)
        self.assertEqual(
            [call.kwargs['target_date'] for call in mock_calculate.call_args_list],
            ['2024-01-02', '2024-01-03', '2024-01-04'],
        )

    def test_backfill_model_data_requires_explicit_start_and_end_dates(self):
        with self.assertRaisesMessage(CommandError, 'start-date and end-date are required.'):
            call_command(
                'backfill_model_data',
                skip_sentiment=True,
            )

    def test_backfill_model_data_creates_high_rs_score_signals(self):
        assets = []
        start_date = timezone.datetime(2024, 2, 1).date()
        trade_dates = [start_date + timezone.timedelta(days=offset) for offset in range(21)]

        for asset_index in range(5):
            asset = Asset.objects.create(
                market=self.market,
                symbol=f'6001{asset_index:02d}',
                ts_code=f'6001{asset_index:02d}.SH',
                name=f'RS Asset {asset_index}',
            )
            assets.append(asset)
            for offset, trade_date in enumerate(trade_dates):
                close_value = Decimal('10.0') + (Decimal(str(asset_index + 1)) * Decimal('0.05') * Decimal(offset))
                OHLCV.objects.create(
                    asset=asset,
                    date=trade_date,
                    open=close_value,
                    high=close_value + Decimal('0.1'),
                    low=close_value - Decimal('0.1'),
                    close=close_value,
                    adj_close=close_value,
                    volume=100000,
                    amount=close_value * Decimal('100000'),
                )

        expected_top_asset = assets[-1]

        IndexMembership.objects.bulk_create([
            IndexMembership(
                asset=asset,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date=trade_dates[0],
                weight=Decimal('4.2'),
            )
            for asset in assets
        ])

        call_command(
            'backfill_model_data',
            start_date=trade_dates[0].isoformat(),
            end_date=trade_dates[-1].isoformat(),
            skip_sentiment=True,
        )

        target_timestamp = timezone.make_aware(
            timezone.datetime.combine(trade_dates[-1], timezone.datetime.min.time())
        )
        self.assertEqual(
            TechnicalIndicator.objects.filter(
                timestamp=target_timestamp,
                indicator_type='RS_SCORE',
            ).count(),
            5,
        )
        self.assertEqual(
            SignalEvent.objects.filter(
                timestamp=target_timestamp,
                signal_type='HIGH_RS_SCORE',
            ).count(),
            1,
        )

    @patch('apps.prediction.management.commands.backfill_model_data.calculate_factor_scores_for_date')
    def test_backfill_model_data_prefills_sparse_rs_input_history_beyond_40_calendar_days(self, mock_calculate):
        asset = Asset.objects.create(
            market=self.market,
            symbol='600299',
            ts_code='600299.SH',
            name='Sparse RS Warmup Asset',
        )
        target_date = timezone.datetime(2024, 3, 1).date()
        sparse_dates = [
            target_date - timezone.timedelta(days=100 - (offset * 5))
            for offset in range(20)
        ] + [target_date]

        for offset, trade_date in enumerate(sparse_dates):
            close_value = Decimal('10.0') + (Decimal(offset) / Decimal('10'))
            OHLCV.objects.create(
                asset=asset,
                date=trade_date,
                open=close_value,
                high=close_value + Decimal('0.1'),
                low=close_value - Decimal('0.1'),
                close=close_value,
                adj_close=close_value,
                volume=100000,
                amount=close_value * Decimal('100000'),
            )

        for index_code, index_name, weight in (
            ('000300.SH', 'CSI 300', Decimal('4.2')),
            ('000510.CSI', 'CSI A500', Decimal('2.1')),
        ):
            IndexMembership.objects.create(
                asset=asset,
                index_code=index_code,
                index_name=index_name,
                trade_date=target_date,
                weight=weight,
            )

        output = StringIO()
        call_command(
            'backfill_model_data',
            start_date=target_date.isoformat(),
            end_date=target_date.isoformat(),
            skip_sentiment=True,
            stdout=output,
        )

        target_timestamp = timezone.make_aware(
            timezone.datetime.combine(target_date, timezone.datetime.min.time())
        )
        self.assertTrue(
            TechnicalIndicator.objects.filter(
                asset=asset,
                timestamp=target_timestamp,
                indicator_type='RS_SCORE',
            ).exists()
        )
        self.assertIn(f'calendar_prefill_days={MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS}', output.getvalue())
        self.assertIn('rs_score_prefill_start=', output.getvalue())

    def test_backfill_model_data_filters_rs_scores_to_point_in_time_union_membership(self):
        assets = []
        start_date = timezone.datetime(2024, 2, 1).date()
        trade_dates = [start_date + timezone.timedelta(days=offset) for offset in range(21)]

        for asset_index in range(5):
            asset = Asset.objects.create(
                market=self.market,
                symbol=f'6002{asset_index:02d}',
                ts_code=f'6002{asset_index:02d}.SH',
                name=f'PIT RS Asset {asset_index}',
            )
            assets.append(asset)
            for offset, trade_date in enumerate(trade_dates):
                close_value = Decimal('10.0') + (Decimal(str(asset_index + 1)) * Decimal('0.05') * Decimal(offset))
                OHLCV.objects.create(
                    asset=asset,
                    date=trade_date,
                    open=close_value,
                    high=close_value + Decimal('0.1'),
                    low=close_value - Decimal('0.1'),
                    close=close_value,
                    adj_close=close_value,
                    volume=100000,
                    amount=close_value * Decimal('100000'),
                )

        IndexMembership.objects.create(
            asset=assets[-1],
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=trade_dates[0],
            weight=Decimal('4.2'),
        )
        IndexMembership.objects.create(
            asset=assets[-2],
            index_code='000510.CSI',
            index_name='CSI A500',
            trade_date=trade_dates[0],
            weight=Decimal('2.1'),
        )

        call_command(
            'backfill_model_data',
            start_date=trade_dates[0].isoformat(),
            end_date=trade_dates[-1].isoformat(),
            skip_sentiment=True,
        )

        target_timestamp = timezone.make_aware(
            timezone.datetime.combine(trade_dates[-1], timezone.datetime.min.time())
        )
        self.assertEqual(
            TechnicalIndicator.objects.filter(
                timestamp=target_timestamp,
                indicator_type='RS_SCORE',
            ).count(),
            2,
        )
        self.assertEqual(
            SignalEvent.objects.filter(
                timestamp=target_timestamp,
                signal_type='HIGH_RS_SCORE',
            ).count(),
            1,
        )
        self.assertTrue(
            TechnicalIndicator.objects.filter(
                asset=assets[-1],
                timestamp=target_timestamp,
                indicator_type='RS_SCORE',
            ).exists()
        )
        self.assertFalse(
            TechnicalIndicator.objects.filter(
                asset=assets[0],
                timestamp=target_timestamp,
                indicator_type='RS_SCORE',
            ).exists()
        )
        self.assertTrue(
            SignalEvent.objects.filter(
                asset=assets[-1],
                timestamp=target_timestamp,
                signal_type='HIGH_RS_SCORE',
            ).exists()
        )

        call_command(
            'backfill_model_data',
            start_date=trade_dates[0].isoformat(),
            end_date=trade_dates[-1].isoformat(),
            skip_sentiment=True,
        )

        self.assertEqual(
            SignalEvent.objects.filter(
                timestamp=target_timestamp,
                signal_type='HIGH_RS_SCORE',
            ).count(),
            1,
        )

    @override_settings(HISTORICAL_DATA_FLOOR='2010-01-01')
    def test_backfill_model_data_rejects_dates_before_historical_floor(self):
        with self.assertRaisesMessage(
            CommandError,
            'start-date cannot be earlier than HISTORICAL_DATA_FLOOR=2010-01-01.',
        ):
            call_command(
                'backfill_model_data',
                start_date='2009-12-31',
                end_date='2010-01-04',
                skip_sentiment=True,
            )

    def test_backfill_model_data_rejects_missing_required_pit_membership_coverage(self):
        start_date = timezone.datetime(2024, 9, 23).date()
        end_date = start_date + timezone.timedelta(days=20)

        for offset in range(21):
            trade_date = start_date + timezone.timedelta(days=offset)
            close_value = Decimal('10.0') + Decimal(offset) / Decimal('10')
            OHLCV.objects.create(
                asset=self.asset,
                date=trade_date,
                open=close_value,
                high=close_value + Decimal('0.1'),
                low=close_value - Decimal('0.1'),
                close=close_value,
                adj_close=close_value,
                volume=100000,
                amount=close_value * Decimal('100000'),
            )

        IndexMembership.objects.create(
            asset=self.asset,
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=start_date,
            weight=Decimal('4.2'),
        )

        with self.assertRaisesMessage(
            CommandError,
            'missing point-in-time membership coverage for 000510.CSI on 2024-09-23',
        ):
            call_command(
                'backfill_model_data',
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                skip_sentiment=True,
            )

    @patch('apps.prediction.management.commands.backfill_model_data.calculate_factor_scores_for_date')
    def test_backfill_model_data_fills_pre_news_sentiment_for_historically_listed_delisted_asset(self, mock_calculate):
        start_date = timezone.datetime(2024, 1, 2).date()
        trade_dates = [start_date + timezone.timedelta(days=offset) for offset in range(3)]

        delisted_asset = Asset.objects.create(
            market=self.market,
            symbol='605099',
            ts_code='605099.SH',
            name='Historical Sentiment Asset',
            list_date=trade_dates[0],
            delist_date=trade_dates[2],
            listing_status=Asset.ListingStatus.DELISTED,
        )

        for offset, trade_date in enumerate(trade_dates):
            close_value = Decimal('10.0') + Decimal(offset) / Decimal('10')
            OHLCV.objects.create(
                asset=delisted_asset,
                date=trade_date,
                open=close_value,
                high=close_value + Decimal('0.1'),
                low=close_value - Decimal('0.1'),
                close=close_value,
                adj_close=close_value,
                volume=100000,
                amount=close_value * Decimal('100000'),
            )

        IndexMembership.objects.create(
            asset=delisted_asset,
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=trade_dates[0],
            weight=Decimal('4.2'),
        )

        call_command(
            'backfill_model_data',
            start_date=trade_dates[0].isoformat(),
            end_date=trade_dates[-1].isoformat(),
        )

        self.assertEqual(
            list(
                SentimentScore.objects.filter(
                    asset=delisted_asset,
                    score_type=SentimentScore.ScoreType.ASSET_7D,
                )
                .order_by('date')
                .values_list('date', flat=True)
            ),
            trade_dates[:2],
        )

    @patch('apps.prediction.management.commands.backfill_model_data.persist_ranked_rs_scores')
    @patch('apps.prediction.management.commands.backfill_model_data.calculate_daily_sentiment')
    @patch('apps.prediction.management.commands.backfill_model_data.calculate_factor_scores_for_date')
    def test_backfill_model_data_resume_from_checkpoint_skips_completed_stages(self, mock_calculate, mock_sentiment, mock_rs):
        IndexMembership.objects.create(
            asset=self.asset,
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date='2024-01-02',
            weight=Decimal('4.2'),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_path = Path(temp_dir) / 'backfill_checkpoint.json'
            checkpoint_path.write_text(json.dumps({
                'command': 'backfill_model_data',
                'version': 1,
                'window': {
                    'start_date': '2024-01-02',
                    'end_date': '2024-01-04',
                    'trading_dates_count': 3,
                },
                'stages': {
                    'sentiment': {'status': 'completed'},
                    'rs_score': {'status': 'completed'},
                    'factor_scores': {
                        'status': 'running',
                        'last_completed_date': '2024-01-02',
                    },
                },
            }), encoding='utf-8')

            call_command(
                'backfill_model_data',
                start_date='2024-01-02',
                end_date='2024-01-04',
                checkpoint_file=str(checkpoint_path),
                resume_from_checkpoint=True,
            )

            self.assertFalse(mock_sentiment.called)
            self.assertFalse(mock_rs.called)
            self.assertEqual(
                [call.kwargs['target_date'] for call in mock_calculate.call_args_list],
                ['2024-01-03', '2024-01-04'],
            )

    @patch('apps.prediction.management.commands.backfill_model_data.calculate_factor_scores_for_date')
    def test_backfill_model_data_writes_checkpoint_and_stage_timings(self, mock_calculate):
        IndexMembership.objects.create(
            asset=self.asset,
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date='2024-01-02',
            weight=Decimal('4.2'),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_path = Path(temp_dir) / 'backfill_checkpoint.json'

            call_command(
                'backfill_model_data',
                start_date='2024-01-02',
                end_date='2024-01-04',
                skip_sentiment=True,
                checkpoint_file=str(checkpoint_path),
            )

            checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
            self.assertEqual(checkpoint['stages']['factor_scores']['status'], 'completed')
            self.assertEqual(checkpoint['stages']['factor_scores']['last_completed_date'], '2024-01-04')
            self.assertIn('last_run_seconds', checkpoint['stages']['factor_scores'])


class PointInTimeTrainingDatasetTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='P14TRAIN', name='Phase 14 Training Market')
        self.asset1 = Asset.objects.create(
            market=self.market,
            symbol='605101',
            ts_code='605101.SH',
            name='Training Asset 1',
        )
        self.asset2 = Asset.objects.create(
            market=self.market,
            symbol='605102',
            ts_code='605102.SH',
            name='Training Asset 2',
        )

        self.trade_dates = [timezone.datetime(2024, 1, 2).date() + timezone.timedelta(days=offset) for offset in range(6)]
        for asset_index, asset in enumerate([self.asset1, self.asset2], start=1):
            for offset, trade_date in enumerate(self.trade_dates):
                close_value = Decimal('10.0') + (Decimal(str(asset_index)) * Decimal('0.1') * Decimal(offset))
                OHLCV.objects.create(
                    asset=asset,
                    date=trade_date,
                    open=close_value,
                    high=close_value + Decimal('0.1'),
                    low=close_value - Decimal('0.1'),
                    close=close_value,
                    adj_close=close_value,
                    volume=100000,
                    amount=close_value * Decimal('100000'),
                )

        IndexMembership.objects.create(
            asset=self.asset1,
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=self.trade_dates[0],
            weight=Decimal('4.0'),
        )
        IndexMembership.objects.create(
            asset=self.asset2,
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=self.trade_dates[2],
            weight=Decimal('4.0'),
        )

    def test_create_feature_matrix_filters_rows_to_point_in_time_union(self):
        feature_df = _create_feature_matrix(
            start_date=self.trade_dates[0],
            end_date=self.trade_dates[2],
            asset_ids=[self.asset1.id, self.asset2.id],
        )

        observed_pairs = {(row.date.isoformat(), int(row.asset_id)) for row in feature_df.itertuples()}
        self.assertEqual(
            observed_pairs,
            {
                (self.trade_dates[0].isoformat(), self.asset1.id),
                (self.trade_dates[1].isoformat(), self.asset1.id),
                (self.trade_dates[2].isoformat(), self.asset2.id),
            },
        )

    def test_create_labels_for_training_filters_rows_to_point_in_time_union(self):
        labels = _create_labels_for_training(
            start_date=self.trade_dates[0],
            end_date=self.trade_dates[2],
            horizon_days=3,
        )

        observed_keys = {(target_date.isoformat(), asset_id) for target_date, asset_id, _horizon in labels.keys()}
        self.assertEqual(
            observed_keys,
            {
                (self.trade_dates[0].isoformat(), self.asset1.id),
                (self.trade_dates[1].isoformat(), self.asset1.id),
                (self.trade_dates[2].isoformat(), self.asset2.id),
            },
        )

    def test_create_feature_matrix_raises_when_required_pit_membership_is_missing(self):
        IndexMembership.objects.all().delete()

        with self.assertRaisesMessage(
            PITMembershipCoverageError,
            'missing point-in-time membership coverage for 000300.SH on 2024-01-02',
        ):
            _create_feature_matrix(
                start_date=self.trade_dates[0],
                end_date=self.trade_dates[2],
                asset_ids=[self.asset1.id, self.asset2.id],
            )

    def test_create_labels_for_training_raises_when_required_pit_membership_is_missing(self):
        IndexMembership.objects.all().delete()

        with self.assertRaisesMessage(
            PITMembershipCoverageError,
            'missing point-in-time membership coverage for 000300.SH on 2024-01-02',
        ):
            _create_labels_for_training(
                start_date=self.trade_dates[0],
                end_date=self.trade_dates[2],
                horizon_days=3,
            )


class LstmTrainingRegistryTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='P14LSTM', name='Phase 14 LSTM Market')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='600777',
            ts_code='600777.SH',
            name='LSTM Command Asset',
        )

    @patch('apps.prediction.tasks_lstm._train_single_horizon_lstm')
    @patch('apps.prediction.tasks_lstm._build_sequences')
    @patch('apps.prediction.tasks_lstm._create_feature_matrix')
    @patch('apps.prediction.tasks_lstm._create_labels_for_training')
    def test_train_lstm_models_refreshes_ensemble_metrics(
        self,
        mock_create_labels,
        mock_create_feature_matrix,
        mock_build_sequences,
        mock_train_single_horizon_lstm,
    ):
        training_end = timezone.datetime(2024, 12, 31).date()
        LightGBMModelArtifact.objects.create(
            horizon_days=3,
            version='lgb-3d-2024-12-31',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='/tmp/lightgbm/3d',
            metrics_json={'accuracy': 0.62},
            feature_names=['feature_a'],
            training_window_start=timezone.datetime(2016, 6, 1).date(),
            training_window_end=training_end,
            trained_at=timezone.now(),
            is_active=True,
        )

        feature_df = pd.DataFrame([
            {'date': training_end, 'asset_id': self.asset.id, 'feature_a': 1.0},
        ])
        feature_df.attrs['feature_names'] = ['feature_a']
        mock_create_feature_matrix.return_value = feature_df
        mock_create_labels.return_value = {}
        mock_build_sequences.return_value = (
            np.zeros((2, 20, 1), dtype=np.float32),
            np.asarray([2, 1], dtype=np.int64),
            [training_end, training_end],
        )
        mock_train_single_horizon_lstm.return_value = {
            'status': 'success',
            'accuracy': 0.66,
            'artifact_path': '/tmp/lstm/3d_model.pt',
            'feature_count': 1,
            'sequence_length': 20,
            'training_samples': 2,
            'validation_samples': 1,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch('apps.prediction.tasks_lstm.LSTM_MODELS_DIR', temp_dir):
                result = train_lstm_models(
                    training_start_date='2016-06-01',
                    training_end_date='2024-12-31',
                    horizons=[3],
                    max_samples_per_horizon=2,
                )

        lstm_version = ModelVersion.objects.get(model_type=ModelVersion.ModelType.LSTM, is_active=True)
        ensemble_version = ModelVersion.objects.get(model_type=ModelVersion.ModelType.ENSEMBLE, is_active=True)
        ensemble_snapshot = EnsembleWeightSnapshot.objects.get(date=training_end)

        self.assertEqual(result['status'], 'completed')
        self.assertAlmostEqual(result['aggregate_accuracy'], 0.66)
        self.assertEqual(lstm_version.training_window_start.isoformat(), '2016-06-01')
        self.assertEqual(lstm_version.training_window_end.isoformat(), '2024-12-31')
        self.assertAlmostEqual(lstm_version.metrics['accuracy'], 0.66)
        self.assertAlmostEqual(ensemble_version.metrics['lightgbm_accuracy'], 0.62)
        self.assertAlmostEqual(ensemble_version.metrics['lstm_accuracy'], 0.66)
        self.assertAlmostEqual(ensemble_snapshot.basis_metrics['lightgbm_accuracy'], 0.62)
        self.assertAlmostEqual(ensemble_snapshot.basis_metrics['lstm_accuracy'], 0.66)
