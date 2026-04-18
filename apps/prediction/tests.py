from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.factors.models import FactorScore
from apps.markets.models import Asset, Market, OHLCV
from apps.analytics.models import TechnicalIndicator
from apps.sentiment.models import SentimentScore
from .models import PredictionResult
from .tasks import generate_predictions_for_date


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
