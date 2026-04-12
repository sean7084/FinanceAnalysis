from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.factors.models import FactorScore
from apps.markets.models import Asset, Market
from apps.sentiment.models import SentimentScore
from .models_lightgbm import LightGBMModelArtifact, LightGBMPrediction


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
        return d

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
            model_artifact=artifact,
            feature_snapshot={'rsi': 60, 'pe_percentile': 0.3},
            raw_scores={'down': 0.2, 'flat': 0.35, 'up': 0.45},
            calibrated_scores={'down': 0.24, 'flat': 0.28, 'up': 0.48},
        )

        self.assertTrue(LightGBMPrediction.objects.filter(asset=self.asset, date=d).exists())
        retrieved = LightGBMPrediction.objects.get(id=pred.id)
        self.assertEqual(retrieved.predicted_label, 'UP')
        self.assertEqual(float(retrieved.confidence), 0.45)

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
