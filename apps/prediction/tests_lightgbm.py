from decimal import Decimal
from unittest.mock import patch, MagicMock

import numpy as np
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.analytics.models import TechnicalIndicator
from apps.factors.models import FactorScore
from apps.markets.models import Asset, Market, OHLCV
from apps.sentiment.models import SentimentScore
from .models import ModelVersion
from .models_lightgbm import LightGBMModelArtifact, LightGBMPrediction, EnsembleWeightSnapshot, FeatureImportanceSnapshot
from .tasks_lightgbm import _create_feature_matrix, _extract_features_for_asset, train_lightgbm_models


class LightGBMPredictionTests(TestCase):
    """
    Tests for LightGBM parallel prediction API.
    Separate from heuristic baseline to ensure both work independently.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='lightgbm_user',
            email='lightgbm@example.com',
            password='Passw0rd!123',
        )
        self.market = Market.objects.create(code='LGB', name='LightGBM Market')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='606001',
            ts_code='606001.SH',
            name='LightGBM Asset',
        )

    def _auth(self):
        self.client.force_authenticate(user=self.user)

    def _seed_features(self):
        """Create factor and sentiment data for inference."""
        d = timezone.now().date()
        FactorScore.objects.create(
            asset=self.asset,
            date=d,
            mode=FactorScore.FactorMode.COMPOSITE,
            pe_percentile_score=Decimal('0.3'),
            pb_percentile_score=Decimal('0.4'),
            roe_trend_score=Decimal('0.6'),
            northbound_flow_score=Decimal('0.5'),
            main_force_flow_score=Decimal('0.55'),
            margin_flow_score=Decimal('0.45'),
            technical_reversal_score=Decimal('0.7'),
            sentiment_score=Decimal('0.6'),
            fundamental_score=Decimal('0.4'),
            capital_flow_score=Decimal('0.5'),
            technical_score=Decimal('0.65'),
            composite_score=Decimal('0.52'),
            bottom_probability_score=Decimal('0.52'),
        )
        SentimentScore.objects.create(
            article=None,
            asset=self.asset,
            date=d,
            score_type=SentimentScore.ScoreType.ASSET_7D,
            positive_score=Decimal('0.55'),
            neutral_score=Decimal('0.25'),
            negative_score=Decimal('0.2'),
            sentiment_score=Decimal('0.35'),
            sentiment_label=SentimentScore.Label.POSITIVE,
        )
        for offset in range(12):
            as_of = d - timezone.timedelta(days=offset)
            OHLCV.objects.create(
                asset=self.asset,
                date=as_of,
                open=Decimal('10') + Decimal(offset) / Decimal('10'),
                high=Decimal('11') + Decimal(offset) / Decimal('10'),
                low=Decimal('9') + Decimal(offset) / Decimal('10'),
                close=Decimal('10.5') + Decimal(offset) / Decimal('10'),
                adj_close=Decimal('10.5') + Decimal(offset) / Decimal('10'),
                volume=Decimal('100000') + Decimal(offset * 5000),
                amount=Decimal('2500000') + Decimal(offset * 10000),
            )
            TechnicalIndicator.objects.create(
                asset=self.asset,
                indicator_type='RSI',
                value=Decimal('45') + Decimal(offset),
                timestamp=timezone.make_aware(timezone.datetime.combine(as_of, timezone.datetime.min.time())),
            )
            TechnicalIndicator.objects.create(
                asset=self.asset,
                indicator_type='MOM_5D',
                value=Decimal('0.02') + Decimal(offset) / Decimal('1000'),
                timestamp=timezone.make_aware(timezone.datetime.combine(as_of, timezone.datetime.min.time())),
            )
            TechnicalIndicator.objects.create(
                asset=self.asset,
                indicator_type='RS_SCORE',
                value=Decimal('0.50') + Decimal(offset) / Decimal('100'),
                timestamp=timezone.make_aware(timezone.datetime.combine(as_of, timezone.datetime.min.time())),
            )
        return d

    def test_extract_features_for_asset_includes_engineered_features(self):
        d = self._seed_features()

        features = _extract_features_for_asset(self.asset.id, d)

        self.assertIn('rsi_delta_3d', features)
        self.assertIn('return_5d', features)
        self.assertIn('relative_volume_5d', features)
        self.assertIn('sentiment_7d_avg_20d', features)

    def test_create_feature_matrix_adds_interaction_columns(self):
        d = self._seed_features()

        feature_df = _create_feature_matrix(d, d, asset_ids=[self.asset.id])

        self.assertIn('rsi_x_relative_volume_5d', feature_df.columns)
        self.assertIn('factor_composite_x_sentiment', feature_df.columns)

    @patch('apps.prediction.tasks_lightgbm._save_model_artifacts')
    @patch('apps.prediction.tasks_lightgbm.CalibratedClassifierCV')
    @patch('apps.prediction.tasks_lightgbm.lgb.train')
    def test_train_lightgbm_models_records_metadata_and_snapshots(self, mock_train, mock_calibrator_cls, mock_save_artifacts):
        d = self._seed_features()

        for index in range(120):
            asset = Asset.objects.create(
                market=self.market,
                symbol=f'70{index:04d}',
                ts_code=f'70{index:04d}.SH',
                name=f'LightGBM Asset {index}',
            )
            FactorScore.objects.create(
                asset=asset,
                date=d,
                mode=FactorScore.FactorMode.COMPOSITE,
                pe_percentile_score=Decimal('0.3'),
                pb_percentile_score=Decimal('0.4'),
                roe_trend_score=Decimal('0.6'),
                northbound_flow_score=Decimal('0.5'),
                main_force_flow_score=Decimal('0.55'),
                margin_flow_score=Decimal('0.45'),
                technical_reversal_score=Decimal('0.7'),
                sentiment_score=Decimal('0.35'),
                fundamental_score=Decimal('0.4'),
                capital_flow_score=Decimal('0.5'),
                technical_score=Decimal('0.65'),
                composite_score=Decimal('0.52'),
                bottom_probability_score=Decimal('0.52'),
            )
            SentimentScore.objects.create(
                article=None,
                asset=asset,
                date=d,
                score_type=SentimentScore.ScoreType.ASSET_7D,
                positive_score=Decimal('0.55'),
                neutral_score=Decimal('0.25'),
                negative_score=Decimal('0.2'),
                sentiment_score=Decimal('0.35'),
                sentiment_label=SentimentScore.Label.POSITIVE,
            )
            for offset in range(35):
                as_of = d - timezone.timedelta(days=offset)
                OHLCV.objects.create(
                    asset=asset,
                    date=as_of,
                    open=Decimal('10') + Decimal(offset) / Decimal('10'),
                    high=Decimal('11') + Decimal(offset) / Decimal('10'),
                    low=Decimal('9') + Decimal(offset) / Decimal('10'),
                    close=Decimal('10.5') + Decimal(offset) / Decimal('10'),
                    adj_close=Decimal('10.5') + Decimal(offset) / Decimal('10'),
                    volume=Decimal('100000') + Decimal(offset * 5000),
                    amount=Decimal('2500000') + Decimal(offset * 10000),
                )
            for indicator_type, base_value in [('RSI', Decimal('45')), ('MOM_5D', Decimal('0.02')), ('RS_SCORE', Decimal('0.50'))]:
                TechnicalIndicator.objects.create(
                    asset=asset,
                    indicator_type=indicator_type,
                    value=base_value,
                    timestamp=timezone.make_aware(timezone.datetime.combine(d, timezone.datetime.min.time())),
                )

        class StubModel:
            def feature_importance(self, importance_type):
                self.importance_type = importance_type
                return np.array([0.8, 0.4, 0.2, 0.1, 0.05] + [0.01] * 26)

            def predict(self, matrix):
                return np.tile(np.array([[0.2, 0.3, 0.5]]), (matrix.shape[0], 1))

        class StubCalibrator:
            def fit(self, matrix, labels):
                self.matrix = matrix
                self.labels = labels
                return self

            def predict_proba(self, matrix):
                return np.tile(np.array([[0.2, 0.3, 0.5]]), (matrix.shape[0], 1))

        mock_train.return_value = StubModel()
        mock_calibrator_cls.return_value = StubCalibrator()

        results = train_lightgbm_models(training_start_date=str(d - timezone.timedelta(days=10)), training_end_date=str(d))

        self.assertIn(3, results)
        artifact = LightGBMModelArtifact.objects.filter(horizon_days=3).latest('created_at')
        self.assertEqual(artifact.metadata['engineered_feature_version'], 'v2')
        self.assertTrue(any(name.endswith('_x_macro_phase') for name in artifact.feature_names))
        self.assertTrue(FeatureImportanceSnapshot.objects.filter(model_artifact=artifact).exists())
        self.assertTrue(ModelVersion.objects.filter(model_type=ModelVersion.ModelType.LIGHTGBM, version=artifact.version).exists())
        self.assertTrue(EnsembleWeightSnapshot.objects.filter(date=d).exists())

    def test_lightgbm_endpoints_require_auth(self):
        """LightGBM endpoints should require authentication."""
        response = self.client.get(f'/api/v1/lightgbm-predictions/{self.asset.symbol}/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        response = self.client.post('/api/v1/lightgbm-predictions/batch/', {'stock_codes': [self.asset.symbol]}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_lightgbm_model_artifact_storage(self):
        """LightGBM model artifacts should be correctly stored and retrieved."""
        self._auth()
        d = timezone.now().date()

        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=3,
            version='lgb-3d-2026-04-12',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='/models/lightgbm/3d_lgb-3d-2026-04-12/',
            metrics_json={'accuracy': 0.68, 'f1_macro': 0.65},
            feature_names=['rsi', 'pe_percentile', 'sentiment_7d'],
            training_window_start=d.replace(year=d.year - 5),
            training_window_end=d,
            trained_at=timezone.now(),
            is_active=True,
        )

        response = self.client.get('/api/v1/lightgbm-models/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['version'], 'lgb-3d-2026-04-12')

    def test_lightgbm_prediction_creation(self):
        """LightGBM predictions should be created and persisted."""
        d = timezone.now().date()
        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=3,
            version='test-lgb-3d',
            status=LightGBMModelArtifact.Status.READY,
            is_active=True,
        )

        pred = LightGBMPrediction.objects.create(
            asset=self.asset,
            date=d,
            horizon_days=3,
            up_probability=Decimal('0.45'),
            flat_probability=Decimal('0.30'),
            down_probability=Decimal('0.25'),
            predicted_label='UP',
            confidence=Decimal('0.45'),
            target_price=Decimal('12.3000'),
            stop_loss_price=Decimal('9.8000'),
            risk_reward_ratio=Decimal('2.500000'),
            trade_score=Decimal('1.450000'),
            suggested=True,
            model_artifact=artifact,
            feature_snapshot={'rsi': 60, 'pe_percentile': 0.3},
            raw_scores={'down': 0.2, 'flat': 0.35, 'up': 0.45},
            calibrated_scores={'down': 0.24, 'flat': 0.28, 'up': 0.48},
        )

        self.assertTrue(LightGBMPrediction.objects.filter(asset=self.asset, date=d).exists())
        retrieved = LightGBMPrediction.objects.get(id=pred.id)
        self.assertEqual(retrieved.predicted_label, 'UP')
        self.assertEqual(float(retrieved.confidence), 0.45)
        self.assertEqual(float(retrieved.trade_score), 1.45)
        self.assertTrue(retrieved.suggested)

    def test_lightgbm_batch_endpoint(self):
        """Batch endpoint should handle multiple stock_codes."""
        self._auth()
        self._seed_features()

        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=3,
            version='batch-test',
            status=LightGBMModelArtifact.Status.READY,
            is_active=True,
        )

        d = timezone.now().date()
        LightGBMPrediction.objects.create(
            asset=self.asset,
            date=d,
            horizon_days=3,
            up_probability=Decimal('0.50'),
            flat_probability=Decimal('0.30'),
            down_probability=Decimal('0.20'),
            predicted_label='UP',
            confidence=Decimal('0.50'),
            model_artifact=artifact,
        )

        response = self.client.post(
            '/api/v1/lightgbm-predictions/batch/',
            {'date': str(d), 'stock_codes': [self.asset.symbol], 'horizons': [3]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(self.asset.symbol, response.data['results'])

    def test_lightgbm_stock_endpoint_returns_symbol_predictions(self):
        self._auth()
        d = timezone.now().date()
        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=3,
            version='stock-endpoint-test',
            status=LightGBMModelArtifact.Status.READY,
            is_active=True,
        )
        LightGBMPrediction.objects.create(
            asset=self.asset,
            date=d,
            horizon_days=3,
            up_probability=Decimal('0.61'),
            flat_probability=Decimal('0.24'),
            down_probability=Decimal('0.15'),
            predicted_label='UP',
            confidence=Decimal('0.61'),
            target_price=Decimal('12.3000'),
            stop_loss_price=Decimal('10.9000'),
            risk_reward_ratio=Decimal('1.800000'),
            trade_score=Decimal('1.120000'),
            suggested=True,
            model_artifact=artifact,
        )

        response = self.client.get(f'/api/v1/lightgbm-predictions/{self.asset.symbol}/?date={d.isoformat()}')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['stock_code'], self.asset.symbol)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['horizon_days'], 3)
        self.assertEqual(response.data['results'][0]['model_version'], artifact.version)
        self.assertEqual(response.data['results'][0]['target_price'], '12.3000')
        self.assertEqual(response.data['results'][0]['stop_loss_price'], '10.9000')
        self.assertEqual(response.data['results'][0]['risk_reward_ratio'], '1.800000')
        self.assertEqual(response.data['results'][0]['trade_score'], '1.120000')
        self.assertTrue(response.data['results'][0]['suggested'])

    def test_feature_importance_trends_endpoint_groups_recent_history(self):
        self._auth()
        d = timezone.now()
        artifact_latest = LightGBMModelArtifact.objects.create(
            horizon_days=3,
            version='trend-latest',
            status=LightGBMModelArtifact.Status.READY,
            trained_at=d,
            is_active=True,
        )
        artifact_prior = LightGBMModelArtifact.objects.create(
            horizon_days=3,
            version='trend-prior',
            status=LightGBMModelArtifact.Status.READY,
            trained_at=d - timezone.timedelta(days=7),
            is_active=False,
        )
        artifact_other_horizon = LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='trend-7d',
            status=LightGBMModelArtifact.Status.READY,
            trained_at=d - timezone.timedelta(days=3),
            is_active=False,
        )

        FeatureImportanceSnapshot.objects.create(
            model_artifact=artifact_prior,
            horizon_days=3,
            feature_name='rsi_x_relative_volume_5d',
            importance_score=0.5,
            importance_rank=1,
        )
        FeatureImportanceSnapshot.objects.create(
            model_artifact=artifact_latest,
            horizon_days=3,
            feature_name='rsi_x_relative_volume_5d',
            importance_score=0.8,
            importance_rank=1,
        )
        FeatureImportanceSnapshot.objects.create(
            model_artifact=artifact_other_horizon,
            horizon_days=7,
            feature_name='factor_composite_x_sentiment',
            importance_score=0.7,
            importance_rank=1,
        )

        response = self.client.get('/api/v1/lightgbm-models/feature-importance-trends/?horizon_days=3&limit_models=5&top_n=5')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['horizon_days'], 3)
        self.assertEqual(response.data['results'][0]['feature_trends'][0]['feature_name'], 'rsi_x_relative_volume_5d')
        self.assertEqual(len(response.data['results'][0]['feature_trends'][0]['snapshots']), 2)

    @patch('apps.prediction.views_lightgbm.generate_lightgbm_predictions_for_date.delay')
    def test_lightgbm_recalculate_queues_task(self, mock_delay):
        """Recalculate endpoint should queue prediction task."""
        self._auth()
        response = self.client.post(
            '/api/v1/lightgbm-predictions/recalculate/',
            {'target_date': str(timezone.now().date()), 'horizons': [3, 7]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once()

    @patch('apps.prediction.views_lightgbm.train_lightgbm_models.delay')
    def test_lightgbm_train_queues_task(self, mock_delay):
        """Train endpoint should queue training task."""
        self._auth()
        response = self.client.post(
            '/api/v1/lightgbm-predictions/train/',
            {'training_start_date': '2021-04-12', 'training_end_date': '2026-04-11'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once()

    def test_lightgbm_vs_heuristic_coexist(self):
        """
        LightGBM and heuristic predictions should coexist independently.
        This verifies the parallel API pattern.
        """
        from .models import PredictionResult

        d = timezone.now().date()

        # Create LightGBM prediction
        lgb_artifact = LightGBMModelArtifact.objects.create(
            horizon_days=3,
            version='lgb-v1',
            status=LightGBMModelArtifact.Status.READY,
            is_active=True,
        )
        lgb_pred = LightGBMPrediction.objects.create(
            asset=self.asset,
            date=d,
            horizon_days=3,
            up_probability=Decimal('0.60'),
            flat_probability=Decimal('0.25'),
            down_probability=Decimal('0.15'),
            predicted_label='UP',
            confidence=Decimal('0.60'),
            model_artifact=lgb_artifact,
        )

        # Create heuristic prediction
        from .models import ModelVersion
        heuristic_version = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.ENSEMBLE,
            version='heuristic-v1',
            status=ModelVersion.Status.READY,
            is_active=True,
        )
        heuristic_pred = PredictionResult.objects.create(
            asset=self.asset,
            date=d,
            horizon_days=3,
            up_probability=Decimal('0.45'),
            flat_probability=Decimal('0.30'),
            down_probability=Decimal('0.25'),
            predicted_label='UP',
            confidence=Decimal('0.45'),
            model_version=heuristic_version,
        )

        # Both should exist independently
        self.assertTrue(LightGBMPrediction.objects.filter(asset=self.asset, date=d).exists())
        self.assertTrue(PredictionResult.objects.filter(asset=self.asset, date=d).exists())

        # Retrieve both
        lgb = LightGBMPrediction.objects.get(asset=self.asset, date=d)
        heuristic = PredictionResult.objects.get(asset=self.asset, date=d)

        # Verify they're different
        self.assertNotEqual(float(lgb.confidence), float(heuristic.confidence))
        self.assertEqual(float(lgb.up_probability), 0.60)
        self.assertEqual(float(heuristic.up_probability), 0.45)
