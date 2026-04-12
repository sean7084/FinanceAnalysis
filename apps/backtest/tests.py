from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.factors.models import FactorScore
from apps.markets.models import Asset, Market, OHLCV
from apps.prediction.models import ModelVersion, PredictionResult
from .models import BacktestRun, BacktestTrade
from .tasks import run_backtest


class Phase15BacktestTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='phase15_user',
            email='phase15@example.com',
            password='Passw0rd!123',
        )
        self.market = Market.objects.create(code='P15', name='Phase 15 Market')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='600001',
            ts_code='600001.SH',
            name='Backtest Asset',
        )
        self.today = timezone.now().date()
        self.d1 = self.today.replace(day=max(1, self.today.day - 2))
        self.d2 = self.today.replace(day=max(1, self.today.day - 1))

        OHLCV.objects.create(
            asset=self.asset,
            date=self.d1,
            open=Decimal('10.0000'),
            high=Decimal('10.5000'),
            low=Decimal('9.9000'),
            close=Decimal('10.0000'),
            adj_close=Decimal('10.0000'),
            volume=100000,
            amount=Decimal('1000000.0000'),
        )
        OHLCV.objects.create(
            asset=self.asset,
            date=self.d2,
            open=Decimal('10.8000'),
            high=Decimal('11.0000'),
            low=Decimal('10.7000'),
            close=Decimal('11.0000'),
            adj_close=Decimal('11.0000'),
            volume=120000,
            amount=Decimal('1200000.0000'),
        )

        mv = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.ENSEMBLE,
            version='ensemble-test',
            status=ModelVersion.Status.READY,
            is_active=True,
        )
        PredictionResult.objects.create(
            asset=self.asset,
            date=self.d1,
            horizon_days=7,
            up_probability=Decimal('0.700000'),
            flat_probability=Decimal('0.200000'),
            down_probability=Decimal('0.100000'),
            confidence=Decimal('0.700000'),
            predicted_label=PredictionResult.Label.UP,
            model_version=mv,
        )
        FactorScore.objects.create(
            asset=self.asset,
            date=self.d1,
            mode=FactorScore.FactorMode.COMPOSITE,
            composite_score=Decimal('0.650000'),
            bottom_probability_score=Decimal('0.800000'),
        )

    def _auth(self):
        self.client.force_authenticate(user=self.user)

    def test_backtest_endpoint_requires_auth(self):
        response = self.client.get('/api/v1/backtest/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('apps.backtest.views.run_backtest.delay')
    def test_create_backtest_queues_task(self, mock_delay):
        self._auth()
        response = self.client.post(
            '/api/v1/backtest/',
            {
                'name': 'P15 Threshold Strategy',
                'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                'start_date': str(self.d1),
                'end_date': str(self.d2),
                'initial_capital': '100000.00',
                'parameters': {
                    'top_n': 1,
                    'horizon_days': 7,
                    'up_threshold': 0.55,
                },
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(BacktestRun.objects.count(), 1)
        mock_delay.assert_called_once()

    def test_run_backtest_task_completes_and_creates_trades(self):
        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Task Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={'top_n': 1, 'horizon_days': 7, 'up_threshold': 0.55},
        )

        result = run_backtest(run.id)
        run.refresh_from_db()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertGreater(run.final_value, Decimal('0'))
        self.assertEqual(run.total_trades, 1)
        self.assertEqual(BacktestTrade.objects.filter(backtest_run=run).count(), 2)

    def test_backtest_trades_action_returns_rows(self):
        self._auth()
        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Trade View Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={'top_n': 1, 'horizon_days': 7, 'up_threshold': 0.55},
        )
        run_backtest(run.id)

        response = self.client.get(f'/api/v1/backtest/{run.id}/trades/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 2)
