from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.factors.models import FactorScore
from apps.markets.models import Asset, Market, OHLCV
from apps.prediction.models_lightgbm import LightGBMModelArtifact
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
            bottom_probability_score=Decimal('0.200000'),
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

    def test_run_backtest_uses_on_demand_heuristic_candidates_without_stored_predictions(self):
        PredictionResult.objects.all().delete()

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 On Demand Heuristic Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.20,
                'prediction_source': 'heuristic',
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        buy_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.BUY).first()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertIsNotNone(buy_trade)
        self.assertEqual(buy_trade.signal_payload['prediction_source'], 'heuristic')
        self.assertTrue(buy_trade.signal_payload['generated_on_demand'])

    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_run_backtest_supports_lightgbm_prediction_source(self, mock_load_artifacts):
        class IdentityScaler:
            def transform(self, matrix):
                return matrix

        class StubModel:
            def predict(self, matrix):
                import numpy as np
                return np.array([[0.1, 0.2, 0.7]])

        class StubCalibrator:
            def predict_proba(self, matrix):
                import numpy as np
                return np.array([[0.1, 0.2, 0.7]])

        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-bt-test',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/test',
            feature_names=['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d'],
            is_active=True,
        )
        mock_load_artifacts.return_value = {
            'model': StubModel(),
            'scaler': IdentityScaler(),
            'calibrator': StubCalibrator(),
            'metadata': {'feature_names': ['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d']},
        }

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LightGBM Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        buy_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.BUY).first()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertIsNotNone(buy_trade)
        self.assertEqual(buy_trade.signal_payload['prediction_source'], 'lightgbm')
        self.assertEqual(buy_trade.signal_payload['model_artifact_id'], artifact.id)
        self.assertEqual(buy_trade.signal_payload['model_version'], artifact.version)

    def test_run_backtest_fails_without_active_lightgbm_artifact(self):
        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Missing LightGBM Artifact',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()

        self.assertIn('failed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.FAILED)
        self.assertIn('No active LightGBM artifact available', run.error_message)

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

    def test_backtest_serializer_rejects_invalid_prediction_source(self):
        self._auth()
        response = self.client.post(
            '/api/v1/backtest/',
            {
                'name': 'P15 Invalid Source',
                'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                'start_date': str(self.d1),
                'end_date': str(self.d2),
                'initial_capital': '100000.00',
                'parameters': {
                    'top_n': 1,
                    'horizon_days': 7,
                    'up_threshold': 0.55,
                    'prediction_source': 'foo',
                },
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_backtest_supports_tuesday_thursday_top3_seven_day_hold(self, mock_load_artifacts):
        class IdentityScaler:
            def transform(self, matrix):
                return matrix

        class StubModel:
            def predict(self, matrix):
                import numpy as np
                return np.array([[0.1, 0.2, 0.7]])

        class StubCalibrator:
            def predict_proba(self, matrix):
                import numpy as np
                return np.array([[0.1, 0.2, 0.7]])

        mock_load_artifacts.return_value = {
            'model': StubModel(),
            'scaler': IdentityScaler(),
            'calibrator': StubCalibrator(),
            'metadata': {'feature_names': ['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d']},
        }

        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-schedule-test',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/schedule-test',
            feature_names=['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d'],
            is_active=True,
        )

        market = Market.objects.create(code='P15S', name='Schedule Market')
        schedule_assets = []
        schedule_start = date(2026, 4, 6)
        schedule_end = date(2026, 4, 17)
        for index in range(4):
            asset = Asset.objects.create(
                market=market,
                symbol=f'6010{index}',
                ts_code=f'6010{index}.SH',
                name=f'Schedule Asset {index}',
            )
            schedule_assets.append(asset)
            current_date = schedule_start - timedelta(days=30)
            day_index = 0
            while current_date <= schedule_end:
                if current_date.weekday() < 5:
                    close = Decimal('10.0000') + Decimal(index) + (Decimal(day_index) / Decimal('20'))
                    OHLCV.objects.create(
                        asset=asset,
                        date=current_date,
                        open=close,
                        high=close + Decimal('0.3000'),
                        low=close - Decimal('0.3000'),
                        close=close,
                        adj_close=close,
                        volume=100000 + index * 1000 + day_index * 100,
                        amount=close * Decimal('100000'),
                    )
                    day_index += 1
                current_date += timedelta(days=1)

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Tue Thu Hold Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=schedule_start,
            end_date=schedule_end,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 3,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
                'entry_weekdays': ['TUE', 'THU'],
                'holding_period_days': 7,
                'capital_fraction_per_entry': 0.5,
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        trades = list(BacktestTrade.objects.filter(backtest_run=run).order_by('trade_date', 'id'))
        buy_dates = sorted({trade.trade_date.isoformat() for trade in trades if trade.side == BacktestTrade.Side.BUY})
        sell_dates = sorted({trade.trade_date.isoformat() for trade in trades if trade.side == BacktestTrade.Side.SELL})

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertEqual(run.total_trades, 6)
        self.assertEqual(len(trades), 12)
        self.assertEqual(buy_dates, ['2026-04-07', '2026-04-09'])
        self.assertEqual(sell_dates, ['2026-04-14', '2026-04-16'])
        self.assertEqual(run.report['entry_weekdays'], [1, 3])
        self.assertEqual(run.report['holding_period_days'], 7)
        self.assertEqual(run.report['prediction_source'], 'lightgbm')
        self.assertEqual(trades[0].signal_payload['model_version'], artifact.version)
        self.assertIn('benchmark', run.report)
