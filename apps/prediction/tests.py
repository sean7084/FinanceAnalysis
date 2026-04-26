import tempfile
from decimal import Decimal
from unittest.mock import patch

import numpy as np
import pandas as pd
from django.core.management import call_command
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.factors.models import FundamentalFactorSnapshot
from apps.factors.models import FactorScore
from apps.markets.models import Asset, Market, OHLCV
from apps.analytics.models import TechnicalIndicator
from apps.sentiment.models import SentimentScore
from .models import ModelVersion, PredictionResult
from .models_lightgbm import EnsembleWeightSnapshot, LightGBMModelArtifact
from .tasks import generate_predictions_for_date
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

    def test_prediction_endpoints_require_auth(self):
        response = self.client.get(f'/api/v1/prediction/{self.asset.symbol}/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        response = self.client.post('/api/v1/prediction/batch/', {'stock_codes': [self.asset.symbol]}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_generate_predictions_task_creates_snapshot_rows(self):
        d = self._seed_features()
        generate_predictions_for_date(target_date=str(d), horizons=[3, 7, 30])
        self.assertEqual(PredictionResult.objects.filter(asset=self.asset, date=d).count(), 3)
        prediction = PredictionResult.objects.filter(asset=self.asset, date=d, horizon_days=7).first()
        self.assertIsNotNone(prediction.target_price)
        self.assertIsNotNone(prediction.stop_loss_price)
        self.assertIsNotNone(prediction.risk_reward_ratio)
        self.assertIsNotNone(prediction.trade_score)

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
    def test_backfill_model_data_resume_factor_scores_skips_completed_dates(self, mock_calculate):
        call_command(
            'backfill_model_data',
            start_date='2024-01-02',
            end_date='2024-01-04',
            skip_sentiment=True,
            skip_rs_score=True,
            resume_factor_scores=True,
        )

        self.assertEqual(mock_calculate.call_count, 2)
        self.assertEqual(
            [call.kwargs['target_date'] for call in mock_calculate.call_args_list],
            ['2024-01-03', '2024-01-04'],
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
