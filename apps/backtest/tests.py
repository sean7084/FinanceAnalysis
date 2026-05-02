import csv
import json
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.factors.models import FactorScore
from apps.markets.models import Asset, BenchmarkIndexDaily, IndexMembership, Market, OHLCV, PointInTimeBenchmarkDaily
from apps.macro.models import MarketContext
from apps.prediction.odds import estimate_trade_decision
from apps.prediction.models_lightgbm import LightGBMModelArtifact
from apps.prediction.models import ModelVersion, PredictionResult
from .models import BacktestRun, BacktestTrade
from .tasks import _pick_candidates, _resolve_macro_context_for_date, run_backtest


class IdentityScaler:
    def transform(self, matrix):
        return matrix


class StubCalibrator:
    def predict_proba(self, matrix):
        import numpy as np
        return np.array([[0.1, 0.2, 0.7]])


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
        self.d1 = self.today - timedelta(days=2)
        self.d2 = self.today - timedelta(days=1)

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

    def test_macro_context_fallback_ignores_inactive_rows(self):
        MarketContext.objects.create(
            context_key='current',
            macro_phase=MarketContext.MacroPhase.RECOVERY,
            starts_at=date(2024, 1, 1),
            ends_at=date(2024, 1, 31),
            is_active=True,
        )
        MarketContext.objects.create(
            context_key='current',
            macro_phase=MarketContext.MacroPhase.RECESSION,
            starts_at=date(2024, 2, 1),
            ends_at=None,
            is_active=False,
        )

        context = _resolve_macro_context_for_date(date(2024, 2, 15), {})

        self.assertEqual(context['macro_phase'], MarketContext.MacroPhase.RECOVERY)

    def _auth(self):
        self.client.force_authenticate(user=self.user)

    def test_backtest_endpoint_requires_auth(self):
        response = self.client.get('/api/v1/backtest/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('apps.backtest.views.run_backtest.delay')
    def test_create_backtest_queues_task(self, mock_delay):
        self._auth()
        with self.captureOnCommitCallbacks(execute=True):
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

    def test_run_backtest_uses_precomputed_point_in_time_benchmark_when_available(self):
        PointInTimeBenchmarkDaily.objects.bulk_create([
            PointInTimeBenchmarkDaily(
                benchmark_code='CSI300_CSIA500_PIT_UNION',
                benchmark_name='CSI300 + CSI A500 PIT Union',
                trade_date=self.d1,
                daily_return=Decimal('0'),
                nav=Decimal('100000.00000000'),
                constituent_count=1,
                overlap_count=0,
                metadata={'snapshot_dates': {'000300.SH': self.d1.isoformat()}},
            ),
            PointInTimeBenchmarkDaily(
                benchmark_code='CSI300_CSIA500_PIT_UNION',
                benchmark_name='CSI300 + CSI A500 PIT Union',
                trade_date=self.d2,
                daily_return=Decimal('0.02000000'),
                nav=Decimal('102000.00000000'),
                constituent_count=1,
                overlap_count=0,
                metadata={'snapshot_dates': {'000300.SH': self.d2.isoformat()}},
            ),
        ])

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 PIT Benchmark Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('200000.00'),
            parameters={'top_n': 1, 'horizon_days': 7, 'up_threshold': 0.55},
        )

        run_backtest(run.id)
        run.refresh_from_db()

        self.assertEqual(run.report['benchmark']['strategy'], 'point_in_time_union_benchmark')
        self.assertEqual(run.report['benchmark']['benchmark_code'], 'CSI300_CSIA500_PIT_UNION')
        self.assertEqual(run.report['benchmark']['source'], 'precomputed_point_in_time_benchmark')
        self.assertEqual(run.report['benchmark']['equity_curve'], [200000.0, 204000.0])
        self.assertAlmostEqual(run.report['benchmark']['total_return'], 0.02, places=8)

    def test_pick_candidates_filters_bottom_candidate_scores_to_point_in_time_union(self):
        excluded_asset = Asset.objects.create(
            market=self.market,
            symbol='600099',
            ts_code='600099.SH',
            name='Excluded Bottom Candidate Asset',
        )
        OHLCV.objects.create(
            asset=excluded_asset,
            date=self.d1,
            open=Decimal('20.0000'),
            high=Decimal('20.5000'),
            low=Decimal('19.8000'),
            close=Decimal('20.0000'),
            adj_close=Decimal('20.0000'),
            volume=100000,
            amount=Decimal('2000000.0000'),
        )
        FactorScore.objects.create(
            asset=excluded_asset,
            date=self.d1,
            mode=FactorScore.FactorMode.COMPOSITE,
            composite_score=Decimal('0.850000'),
            bottom_probability_score=Decimal('0.950000'),
        )
        IndexMembership.objects.create(
            asset=self.asset,
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=self.d1,
            weight=Decimal('4.200000'),
        )

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 PIT Bottom Candidate Run',
            strategy_type=BacktestRun.StrategyType.BOTTOM_CANDIDATE,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={'top_n': 2, 'bottom_threshold': 0.20},
        )

        rows = _pick_candidates(run, self.d1, {})

        self.assertEqual([row['asset_id'] for row in rows], [self.asset.id])

    @patch('apps.backtest.tasks._predict_lightgbm_for_asset')
    def test_pick_candidates_filters_on_demand_lightgbm_candidates_to_point_in_time_union(self, mock_predict):
        excluded_asset = Asset.objects.create(
            market=self.market,
            symbol='600199',
            ts_code='600199.SH',
            name='Excluded LightGBM Candidate Asset',
        )
        OHLCV.objects.create(
            asset=excluded_asset,
            date=self.d1,
            open=Decimal('30.0000'),
            high=Decimal('31.0000'),
            low=Decimal('29.5000'),
            close=Decimal('30.0000'),
            adj_close=Decimal('30.0000'),
            volume=100000,
            amount=Decimal('3000000.0000'),
        )
        IndexMembership.objects.create(
            asset=self.asset,
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=self.d1,
            weight=Decimal('4.200000'),
        )

        def _prediction_payload(asset_id, dt, horizon, cache, trade_decision_policy=None):
            base_up = Decimal('0.70') if asset_id == self.asset.id else Decimal('0.95')
            return {
                'up_probability': base_up,
                'flat_probability': Decimal('0.20'),
                'down_probability': Decimal('0.10'),
                'confidence': base_up,
                'predicted_label': PredictionResult.Label.UP,
                'trade_score': Decimal('1.50'),
                'target_price': Decimal('12.00'),
                'stop_loss_price': Decimal('9.50'),
                'risk_reward_ratio': Decimal('2.00'),
                'suggested': True,
                'model_artifact_id': 1,
                'model_version': 'lgb-pit-test',
                'generated_on_demand': True,
            }

        mock_predict.side_effect = _prediction_payload

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 PIT LightGBM Candidate Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 2,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
            },
        )

        rows = _pick_candidates(run, self.d1, {})

        self.assertEqual([row['asset_id'] for row in rows], [self.asset.id])
        self.assertEqual(mock_predict.call_count, 1)
        self.assertEqual(mock_predict.call_args.args[0], self.asset.id)

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

    @patch('apps.backtest.tasks._extract_features_for_asset', return_value={'rsi': 50.0, 'mom_5d': 0.1, 'rs_score': 0.9, 'factor_composite': 0.8, 'sentiment_7d': 0.0})
    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_run_backtest_supports_lightgbm_prediction_source(self, mock_load_artifacts, _mock_extract_features):
        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-bt-test',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/test',
            feature_names=['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d'],
            is_active=True,
        )
        mock_load_artifacts.return_value = {
            'model': object(),
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
        sell_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.SELL).first()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertIsNotNone(buy_trade)
        self.assertEqual(buy_trade.signal_payload['prediction_source'], 'lightgbm')
        self.assertEqual(buy_trade.signal_payload['model_artifact_id'], artifact.id)
        self.assertTrue(buy_trade.signal_payload['generated_on_demand'])
        self.assertIsNotNone(buy_trade.signal_payload.get('trade_score'))
        self.assertIsNotNone(buy_trade.signal_payload.get('target_price'))
        self.assertIsNotNone(buy_trade.signal_payload.get('stop_loss_price'))
        self.assertIsNotNone(sell_trade)
        self.assertEqual(sell_trade.metadata['exit_reason'], 'SCHEDULED')

    @patch('apps.backtest.tasks._extract_features_for_asset', return_value={'rsi': 50.0, 'mom_5d': 0.1, 'rs_score': 0.9, 'factor_composite': 0.8, 'sentiment_7d': 0.0})
    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_lightgbm_top_n_stop_target_exit_uses_propagated_levels(self, mock_load_artifacts, _mock_extract_features):
        LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-bt-tpsl-test',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/tpsl-test',
            feature_names=['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d'],
            is_active=True,
        )
        mock_load_artifacts.return_value = {
            'model': object(),
            'scaler': IdentityScaler(),
            'calibrator': StubCalibrator(),
            'metadata': {'feature_names': ['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d']},
        }

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LightGBM TP SL Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
                'enable_stop_target_exit': True,
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        buy_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.BUY).first()
        sell_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.SELL).first()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertIsNotNone(buy_trade)
        self.assertIsNotNone(buy_trade.signal_payload.get('trade_score'))
        self.assertIsNotNone(buy_trade.signal_payload.get('target_price'))
        self.assertIsNotNone(buy_trade.signal_payload.get('stop_loss_price'))
        self.assertIsNotNone(sell_trade)
        self.assertEqual(sell_trade.metadata['exit_reason'], 'TARGET_PRICE')

    def test_trade_decision_policy_adjusts_near_target_and_stop_distance(self):
        policy_asset = Asset.objects.create(
            market=self.market,
            symbol='600091',
            ts_code='600091.SH',
            name='Policy Asset',
        )
        OHLCV.objects.create(
            asset=policy_asset,
            date=self.d1,
            open=Decimal('90.0000'),
            high=Decimal('90.0000'),
            low=Decimal('89.0000'),
            close=Decimal('90.0000'),
            adj_close=Decimal('90.0000'),
            volume=100000,
            amount=Decimal('9000000.0000'),
        )

        baseline = estimate_trade_decision(policy_asset.id, self.d1, 7, Decimal('0.70'), PredictionResult.Label.UP)
        without_near_target = estimate_trade_decision(
            policy_asset.id,
            self.d1,
            7,
            Decimal('0.70'),
            PredictionResult.Label.UP,
            policy_options={'include_near_round_target': False},
        )
        with_target_floor = estimate_trade_decision(
            policy_asset.id,
            self.d1,
            7,
            Decimal('0.70'),
            PredictionResult.Label.UP,
            policy_options={'min_target_return_pct': Decimal('0.05')},
        )
        with_stop_floor = estimate_trade_decision(
            policy_asset.id,
            self.d1,
            7,
            Decimal('0.70'),
            PredictionResult.Label.UP,
            policy_options={'min_stop_distance_pct': Decimal('0.03')},
        )

        self.assertEqual(baseline['target_price'], Decimal('92.0000'))
        self.assertEqual(without_near_target['target_price'], Decimal('95.4000'))
        self.assertEqual(with_target_floor['target_price'], Decimal('94.5000'))
        self.assertEqual(baseline['stop_loss_price'], Decimal('89.0000'))
        self.assertEqual(with_stop_floor['stop_loss_price'], Decimal('87.3000'))

    @patch('apps.backtest.tasks._extract_features_for_asset', return_value={'rsi': 50.0, 'mom_5d': 0.1, 'rs_score': 0.9, 'factor_composite': 0.8, 'sentiment_7d': 0.0})
    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_lightgbm_top_n_applies_trade_decision_policy_to_payload(self, mock_load_artifacts, _mock_extract_features):
        LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-bt-policy-test',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/policy-test',
            feature_names=['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d'],
            is_active=True,
        )
        mock_load_artifacts.return_value = {
            'model': object(),
            'scaler': IdentityScaler(),
            'calibrator': StubCalibrator(),
            'metadata': {'feature_names': ['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d']},
        }
        OHLCV.objects.filter(asset=self.asset, date=self.d1).update(
            open=Decimal('90.0000'),
            high=Decimal('90.0000'),
            low=Decimal('89.0000'),
            close=Decimal('90.0000'),
            adj_close=Decimal('90.0000'),
            amount=Decimal('9000000.0000'),
        )
        OHLCV.objects.filter(asset=self.asset, date=self.d2).update(
            open=Decimal('95.0000'),
            high=Decimal('96.0000'),
            low=Decimal('94.0000'),
            close=Decimal('95.0000'),
            adj_close=Decimal('95.0000'),
            amount=Decimal('9500000.0000'),
        )

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LightGBM Policy Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
                'enable_stop_target_exit': True,
                'trade_decision_policy': {'min_target_return_pct': 0.05},
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        buy_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.BUY).first()
        sell_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.SELL).first()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertIsNotNone(buy_trade)
        self.assertEqual(buy_trade.signal_payload['target_price'], 94.5)
        self.assertEqual(buy_trade.signal_payload['trade_decision_policy'], {'min_target_return_pct': 0.05})
        self.assertIsNotNone(sell_trade)
        self.assertEqual(sell_trade.metadata['exit_reason'], 'TARGET_PRICE')

    @patch('apps.backtest.tasks._pick_candidates')
    def test_open_positions_backfills_missing_prediction_trade_decision_fields(self, mock_pick_candidates):
        mock_pick_candidates.return_value = [
            {
                'asset_id': self.asset.id,
                'rank_value': Decimal('0.700000'),
                'signal_payload': {
                    'strategy': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                    'prediction_source': 'lightgbm',
                    'candidate_mode': 'top_n',
                    'top_n_metric': 'up_prob_7d',
                    'horizon_days': 7,
                    'up_probability': 0.7,
                    'flat_probability': 0.2,
                    'down_probability': 0.1,
                    'confidence': 0.7,
                    'predicted_label': 'UP',
                    'generated_on_demand': True,
                },
            },
        ]

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Missing TP SL Backfill Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
                'enable_stop_target_exit': True,
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        buy_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.BUY).first()
        sell_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.SELL).first()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertIsNotNone(buy_trade)
        self.assertIsNotNone(buy_trade.signal_payload.get('trade_score'))
        self.assertIsNotNone(buy_trade.signal_payload.get('target_price'))
        self.assertIsNotNone(buy_trade.signal_payload.get('stop_loss_price'))
        self.assertIsNotNone(sell_trade)
        self.assertEqual(sell_trade.metadata['exit_reason'], 'TARGET_PRICE')

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
        self.assertEqual(run.total_trades, 0)
        self.assertIn('No active LightGBM artifact available', run.error_message)
        self.assertEqual(BacktestTrade.objects.filter(backtest_run=run).count(), 0)

    @patch('apps.backtest.tasks._predict_with_lstm')
    def test_run_backtest_supports_lstm_prediction_source_without_stored_predictions(self, mock_predict_with_lstm):
        PredictionResult.objects.all().delete()
        version = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.LSTM,
            version='lstm-bt-test',
            status=ModelVersion.Status.READY,
            is_active=True,
            artifact_path='models/lstm/test',
        )
        mock_predict_with_lstm.return_value = {
            'model_version': version,
            'up_probability': 0.72,
            'flat_probability': 0.18,
            'down_probability': 0.10,
            'confidence': 0.72,
            'predicted_label': PredictionResult.Label.UP,
            'trade_decision': {
                'trade_score': Decimal('1.450000'),
                'target_price': Decimal('11.400000'),
                'stop_loss_price': Decimal('9.800000'),
                'suggested': True,
            },
        }

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LSTM Runtime Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lstm',
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        buy_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.BUY).first()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertIsNotNone(buy_trade)
        self.assertEqual(buy_trade.signal_payload['prediction_source'], 'lstm')
        self.assertEqual(buy_trade.signal_payload['model_version_id'], version.id)
        self.assertTrue(buy_trade.signal_payload['generated_on_demand'])

    @patch('apps.backtest.tasks._predict_with_lstm')
    def test_trade_score_mode_supports_runtime_lstm_candidates_without_stored_predictions(self, mock_predict_with_lstm):
        PredictionResult.objects.all().delete()
        version = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.LSTM,
            version='lstm-trade-score-test',
            status=ModelVersion.Status.READY,
            is_active=True,
            artifact_path='models/lstm/trade-score-test',
        )
        mock_predict_with_lstm.return_value = {
            'model_version': version,
            'up_probability': 0.68,
            'flat_probability': 0.20,
            'down_probability': 0.12,
            'confidence': 0.68,
            'predicted_label': PredictionResult.Label.UP,
            'trade_decision': {
                'trade_score': Decimal('1.250000'),
                'target_price': Decimal('11.200000'),
                'stop_loss_price': Decimal('9.900000'),
                'suggested': True,
            },
        }

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LSTM Trade Score Runtime Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'prediction_source': 'lstm',
                'candidate_mode': 'trade_score',
                'horizon_days': 7,
                'up_threshold': 0.55,
                'trade_score_threshold': 1.0,
                'max_positions': 1,
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        buy_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.BUY).first()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertEqual(run.total_trades, 1)
        self.assertIsNotNone(buy_trade)
        self.assertEqual(buy_trade.signal_payload['candidate_mode'], 'trade_score')
        self.assertEqual(buy_trade.signal_payload['trade_score_scope'], 'independent')
        self.assertEqual(buy_trade.signal_payload['model_version_id'], version.id)
        self.assertTrue(buy_trade.signal_payload['generated_on_demand'])

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

    def test_backtest_comparison_curve_returns_selected_and_benchmark_series(self):
        self._auth()
        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Comparison Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.COMPLETED,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            report={
                'equity_curve': [99950.0, 108500.0],
                'prediction_source': 'lightgbm',
            },
            parameters={'prediction_source': 'lightgbm'},
        )
        BenchmarkIndexDaily.objects.create(
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=self.d1,
            close=Decimal('4000.0000'),
        )
        BenchmarkIndexDaily.objects.create(
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=self.d2,
            close=Decimal('4200.0000'),
        )
        BenchmarkIndexDaily.objects.create(
            index_code='000510.CSI',
            index_name='CSI A500',
            trade_date=self.d1,
            close=Decimal('5000.0000'),
        )
        BenchmarkIndexDaily.objects.create(
            index_code='000510.CSI',
            index_name='CSI A500',
            trade_date=self.d2,
            close=Decimal('4900.0000'),
        )

        response = self.client.get(f'/api/v1/backtest/{run.id}/comparison_curve/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['run']['id'], run.id)
        self.assertEqual(response.data['available_series_keys'], ['selected_run', 'csi300', 'csia500'])
        selected_series = next(series for series in response.data['series'] if series['key'] == 'selected_run')
        csi300_series = next(series for series in response.data['series'] if series['key'] == 'csi300')
        self.assertEqual(selected_series['points'][0]['date'], str(self.d1))
        self.assertAlmostEqual(selected_series['points'][0]['value'], 99950.0)
        self.assertAlmostEqual(csi300_series['points'][0]['value'], 99950.0)
        self.assertAlmostEqual(csi300_series['points'][1]['value'], 104947.5)

    def test_backtest_comparison_curve_includes_compare_run_when_explicit_target_exists(self):
        self._auth()
        compare_run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Previous Version Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.COMPLETED,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('120000.00'),
            report={
                'equity_curve': [120000.0, 126000.0],
                'prediction_source': 'lightgbm',
            },
            parameters={'prediction_source': 'lightgbm'},
        )
        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Latest Version Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.COMPLETED,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            report={
                'equity_curve': [100000.0, 108000.0],
                'prediction_source': 'lightgbm',
            },
            parameters={'prediction_source': 'lightgbm', 'compare_backtest_run_id': compare_run.id},
        )
        BenchmarkIndexDaily.objects.create(
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=self.d1,
            close=Decimal('4000.0000'),
        )
        BenchmarkIndexDaily.objects.create(
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=self.d2,
            close=Decimal('4100.0000'),
        )
        BenchmarkIndexDaily.objects.create(
            index_code='000510.CSI',
            index_name='CSI A500',
            trade_date=self.d1,
            close=Decimal('5000.0000'),
        )
        BenchmarkIndexDaily.objects.create(
            index_code='000510.CSI',
            index_name='CSI A500',
            trade_date=self.d2,
            close=Decimal('5100.0000'),
        )

        response = self.client.get(f'/api/v1/backtest/{run.id}/comparison_curve/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        compare_series = next(series for series in response.data['series'] if series['key'] == 'compare_run')
        self.assertEqual(response.data['compare_target']['id'], compare_run.id)
        self.assertAlmostEqual(compare_series['points'][0]['value'], 100000.0)
        self.assertAlmostEqual(compare_series['points'][1]['value'], 105000.0)

    def test_backtest_comparison_curve_includes_multiple_extra_compare_runs_from_query(self):
        self._auth()
        compare_run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Stored Compare Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.COMPLETED,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('120000.00'),
            report={
                'equity_curve': [120000.0, 126000.0],
                'prediction_source': 'lightgbm',
            },
            parameters={'prediction_source': 'lightgbm'},
        )
        extra_run_one = BacktestRun.objects.create(
            user=self.user,
            name='P15 Extra Compare Run One',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.COMPLETED,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('90000.00'),
            report={
                'equity_curve': [90000.0, 99000.0],
                'prediction_source': 'heuristic',
            },
            parameters={'prediction_source': 'heuristic'},
        )
        extra_run_two = BacktestRun.objects.create(
            user=self.user,
            name='P15 Extra Compare Run Two',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.COMPLETED,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('80000.00'),
            report={
                'equity_curve': [80000.0, 88000.0],
                'prediction_source': 'lstm',
            },
            parameters={'prediction_source': 'lstm'},
        )
        BacktestRun.objects.create(
            user=self.user,
            name='P15 Pending Extra Compare Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.PENDING,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('70000.00'),
            report={
                'equity_curve': [70000.0, 71000.0],
                'prediction_source': 'lightgbm',
            },
            parameters={'prediction_source': 'lightgbm'},
        )
        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Latest Version Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.COMPLETED,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            report={
                'equity_curve': [100000.0, 108000.0],
                'prediction_source': 'lightgbm',
            },
            parameters={'prediction_source': 'lightgbm', 'compare_backtest_run_id': compare_run.id},
        )

        response = self.client.get(
            f'/api/v1/backtest/{run.id}/comparison_curve/',
            {
                'extra_compare_run_id': [compare_run.id, extra_run_one.id, extra_run_two.id, run.id, 'invalid'],
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['compare_target']['id'], compare_run.id)
        series_keys = response.data['available_series_keys']
        self.assertIn('compare_run', series_keys)
        self.assertIn(f'extra_run_{extra_run_one.id}', series_keys)
        self.assertIn(f'extra_run_{extra_run_two.id}', series_keys)
        self.assertEqual(series_keys.count('compare_run'), 1)

        extra_series_one = next(series for series in response.data['series'] if series['key'] == f'extra_run_{extra_run_one.id}')
        extra_series_two = next(series for series in response.data['series'] if series['key'] == f'extra_run_{extra_run_two.id}')
        self.assertEqual(extra_series_one['prediction_source'], 'heuristic')
        self.assertEqual(extra_series_two['prediction_source'], 'lstm')
        self.assertAlmostEqual(extra_series_one['points'][0]['value'], 100000.0)
        self.assertAlmostEqual(extra_series_two['points'][1]['value'], 110000.0)

    def test_backtest_serializer_rejects_incompatible_compare_target(self):
        self._auth()
        compare_run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Heuristic Compare Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.COMPLETED,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            report={'prediction_source': 'heuristic'},
            parameters={'prediction_source': 'heuristic'},
        )

        response = self.client.post(
            '/api/v1/backtest/',
            {
                'name': 'P15 Invalid Compare Target',
                'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                'start_date': str(self.d1),
                'end_date': str(self.d2),
                'initial_capital': '100000.00',
                'parameters': {
                    'top_n': 1,
                    'horizon_days': 7,
                    'up_threshold': 0.55,
                    'prediction_source': 'lightgbm',
                    'compare_backtest_run_id': compare_run.id,
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('compare_backtest_run_id', str(response.data))

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

    def test_backtest_serializer_rejects_invalid_trade_decision_policy(self):
        self._auth()
        response = self.client.post(
            '/api/v1/backtest/',
            {
                'name': 'P15 Invalid Trade Policy',
                'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                'start_date': str(self.d1),
                'end_date': str(self.d2),
                'initial_capital': '100000.00',
                'parameters': {
                    'top_n': 1,
                    'horizon_days': 7,
                    'up_threshold': 0.55,
                    'prediction_source': 'lightgbm',
                    'trade_decision_policy': {'min_target_return_pct': 0.75},
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('apps.backtest.views.run_backtest.delay')
    def test_backtest_serializer_aligns_horizon_with_top_n_metric(self, mock_delay):
        self._auth()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                '/api/v1/backtest/',
                {
                    'name': 'P15 Metric Horizon Align',
                    'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                    'start_date': str(self.d1),
                    'end_date': str(self.d2),
                    'initial_capital': '100000.00',
                    'parameters': {
                        'top_n': 1,
                        'horizon_days': 7,
                        'top_n_metric': 'up_prob_30d',
                        'candidate_mode': 'top_n',
                        'up_threshold': 0.55,
                        'prediction_source': 'heuristic',
                    },
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        run = BacktestRun.objects.latest('id')
        self.assertEqual(run.parameters['top_n_metric'], 'up_prob_30d')
        self.assertEqual(run.parameters['horizon_days'], 30)
        mock_delay.assert_called_once()

    @patch('apps.backtest.tasks._build_heuristic_prediction_map')
    def test_top_n_mode_ignores_max_positions_but_trade_score_mode_honors_it(self, mock_heuristic_prediction_map):
        second_asset = Asset.objects.create(
            market=self.market,
            symbol='600002',
            ts_code='600002.SH',
            name='Backtest Asset 2',
        )
        OHLCV.objects.create(
            asset=second_asset,
            date=self.d1,
            open=Decimal('11.0000'),
            high=Decimal('11.5000'),
            low=Decimal('10.8000'),
            close=Decimal('11.0000'),
            adj_close=Decimal('11.0000'),
            volume=90000,
            amount=Decimal('990000.0000'),
        )
        OHLCV.objects.create(
            asset=second_asset,
            date=self.d2,
            open=Decimal('11.3000'),
            high=Decimal('11.6000'),
            low=Decimal('11.1000'),
            close=Decimal('11.4000'),
            adj_close=Decimal('11.4000'),
            volume=91000,
            amount=Decimal('1037400.0000'),
        )

        PredictionResult.objects.all().delete()
        mock_heuristic_prediction_map.return_value = {
            self.asset.id: {
                'up_probability': Decimal('0.700000'),
                'flat_probability': Decimal('0.200000'),
                'down_probability': Decimal('0.100000'),
                'confidence': Decimal('0.700000'),
                'predicted_label': PredictionResult.Label.UP,
                'trade_score': Decimal('1.600000'),
                'target_price': Decimal('11.200000'),
                'stop_loss_price': Decimal('9.800000'),
                'suggested': True,
                'model_version_id': None,
                'model_version': 'heuristic-baseline',
                'generated_on_demand': True,
            },
            second_asset.id: {
                'up_probability': Decimal('0.680000'),
                'flat_probability': Decimal('0.220000'),
                'down_probability': Decimal('0.100000'),
                'confidence': Decimal('0.680000'),
                'predicted_label': PredictionResult.Label.UP,
                'trade_score': Decimal('1.500000'),
                'target_price': Decimal('11.500000'),
                'stop_loss_price': Decimal('10.100000'),
                'suggested': True,
                'model_version_id': None,
                'model_version': 'heuristic-baseline',
                'generated_on_demand': True,
            },
        }

        top_n_run = BacktestRun.objects.create(
            user=self.user,
            name='P15 TopN Ignores Max Positions',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'prediction_source': 'heuristic',
                'candidate_mode': 'top_n',
                'top_n_metric': 'trade_score',
                'top_n': 2,
                'max_positions': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'holding_period_days': 1,
            },
        )
        run_backtest(top_n_run.id)
        top_n_run.refresh_from_db()

        trade_score_run = BacktestRun.objects.create(
            user=self.user,
            name='P15 TradeScore Honors Max Positions',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'prediction_source': 'heuristic',
                'candidate_mode': 'trade_score',
                'top_n': 2,
                'max_positions': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'trade_score_threshold': 1.0,
                'holding_period_days': 1,
            },
        )
        run_backtest(trade_score_run.id)
        trade_score_run.refresh_from_db()

        self.assertEqual(top_n_run.status, BacktestRun.Status.COMPLETED)
        self.assertEqual(trade_score_run.status, BacktestRun.Status.COMPLETED)
        self.assertEqual(top_n_run.total_trades, 2)
        self.assertEqual(trade_score_run.total_trades, 1)

    @patch('apps.backtest.tasks.BACKTEST_CHUNK_TRADING_DAYS', 1)
    def test_chunked_backtest_preserves_open_positions_across_chunks(self):
        chunk_asset = Asset.objects.create(
            market=self.market,
            symbol='600003',
            ts_code='600003.SH',
            name='Chunked Asset',
        )
        start_date = date(2026, 1, 6)
        trading_dates = [start_date + timedelta(days=index) for index in range(4)]
        closes = ['10.0000', '10.3000', '10.7000', '10.9000']

        for trading_date, close in zip(trading_dates, closes):
            OHLCV.objects.create(
                asset=chunk_asset,
                date=trading_date,
                open=Decimal(close),
                high=Decimal(close) + Decimal('0.3000'),
                low=Decimal(close) - Decimal('0.2000'),
                close=Decimal(close),
                adj_close=Decimal(close),
                volume=100000,
                amount=Decimal(close) * Decimal('100000'),
            )

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Chunked Heuristic Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=trading_dates[0],
            end_date=trading_dates[-1],
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.20,
                'prediction_source': 'heuristic',
                'holding_period_days': 2,
            },
        )

        with patch('apps.backtest.tasks.run_backtest.delay', side_effect=lambda run_id: run_backtest(run_id)) as mock_delay:
            run_backtest(run.id)

        run.refresh_from_db()
        trades = list(BacktestTrade.objects.filter(backtest_run=run).order_by('trade_date', 'id'))

        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertGreaterEqual(mock_delay.call_count, 1)
        self.assertEqual(run.total_trades, 1)
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0].trade_date, trading_dates[0])
        self.assertEqual(trades[1].trade_date, trading_dates[2])
        self.assertNotIn('runtime_state', run.report)
        self.assertNotIn('progress', run.report)

    @patch('apps.backtest.tasks._extract_features_for_asset', return_value={'rsi': 50.0, 'mom_5d': 0.1, 'rs_score': 0.9, 'factor_composite': 0.8, 'sentiment_7d': 0.0})
    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_backtest_supports_tuesday_thursday_top3_seven_day_hold(self, mock_load_artifacts, _mock_extract_features):
        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-schedule-test',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/schedule-test',
            feature_names=['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d'],
            is_active=True,
        )
        mock_load_artifacts.return_value = {
            'model': object(),
            'scaler': IdentityScaler(),
            'calibrator': StubCalibrator(),
            'metadata': {'feature_names': ['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d']},
        }

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
                'max_positions': 6,
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
        self.assertEqual(trades[0].signal_payload['model_artifact_id'], artifact.id)
        self.assertTrue(trades[0].signal_payload['generated_on_demand'])
        self.assertIn('benchmark', run.report)


class BacktestManagementCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='backtest_admin',
            email='backtest_admin@example.com',
            password='Passw0rd!123',
        )

    @patch('apps.backtest.management.commands.run_validation_backtests.run_backtest')
    def test_run_validation_backtests_accepts_lstm_source(self, mock_run_backtest):
        output = StringIO()

        call_command(
            'run_validation_backtests',
            start_date='2026-01-01',
            end_date='2026-01-05',
            window_days=5,
            step_days=10,
            sources='heuristic,lstm',
            name_prefix='cmdtest',
            stdout=output,
        )

        runs = list(BacktestRun.objects.order_by('id'))
        self.assertEqual(len(runs), 2)
        self.assertCountEqual(
            [run.parameters.get('prediction_source') for run in runs],
            ['heuristic', 'lstm'],
        )
        self.assertEqual(mock_run_backtest.call_count, 2)
        self.assertIn('Created 2 validation runs.', output.getvalue())

    @patch('apps.backtest.management.commands.run_validation_backtests.run_backtest')
    def test_run_reference_benchmark_suite_exports_csv_bundle(self, mock_run_backtest):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / 'reference_suite'
            output = StringIO()

            call_command(
                'run_reference_benchmark_suite',
                start_date='2026-01-01',
                end_date='2026-01-05',
                window_days=5,
                step_days=10,
                sources='heuristic,lstm',
                name_prefix='suitecmd',
                output_dir=str(output_dir),
                stdout=output,
            )

            self.assertEqual(mock_run_backtest.call_count, 2)
            self.assertTrue((output_dir / 'run_summary.csv').exists())
            self.assertTrue((output_dir / 'run_config_results.csv').exists())
            self.assertTrue((output_dir / 'model_references.csv').exists())
            self.assertTrue((output_dir / 'suite_manifest.json').exists())

            manifest = json.loads((output_dir / 'suite_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(sorted(manifest['run_ids']), sorted(BacktestRun.objects.values_list('id', flat=True)))

            with (output_dir / 'run_config_results.csv').open(newline='', encoding='utf-8') as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), 2)
            self.assertCountEqual(
                [row['prediction_source'] for row in rows],
                ['heuristic', 'lstm'],
            )
            self.assertIn('Reference benchmark suite exported to', output.getvalue())

    @patch('apps.backtest.management.commands.rerun_backtests_for_comparison.run_backtest')
    def test_rerun_backtests_for_comparison_clones_runs_with_compare_target(self, mock_run_backtest):
        source_run_one = BacktestRun.objects.create(
            user=self.user,
            name='Source Run 525',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            initial_capital=Decimal('100000.00'),
            status=BacktestRun.Status.COMPLETED,
            cash=Decimal('101000.00'),
            final_value=Decimal('101000.00'),
            total_return=Decimal('0.010000'),
            annualized_return=Decimal('0.120000'),
            max_drawdown=Decimal('0.030000'),
            sharpe_ratio=Decimal('1.100000'),
            win_rate=Decimal('0.550000'),
            total_trades=4,
            winning_trades=2,
            parameters={'top_n': 1, 'horizon_days': 7, 'up_threshold': 0.55, 'prediction_source': 'lightgbm'},
            report={'equity_curve': [100000.0, 101000.0]},
        )
        source_run_two = BacktestRun.objects.create(
            user=self.user,
            name='Source Run 526',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
            initial_capital=Decimal('120000.00'),
            status=BacktestRun.Status.COMPLETED,
            parameters={'top_n': 2, 'horizon_days': 30, 'up_threshold': 0.60, 'prediction_source': 'lstm'},
            report={'equity_curve': [120000.0, 121500.0]},
        )

        output = StringIO()
        call_command(
            'rerun_backtests_for_comparison',
            run_ids=f'{source_run_one.id}-{source_run_two.id}',
            name_suffix='pit-2016-2024',
            stdout=output,
        )

        cloned_runs = list(BacktestRun.objects.filter(id__gt=source_run_two.id).order_by('id'))
        self.assertEqual(len(cloned_runs), 2)
        self.assertEqual(cloned_runs[0].parameters['compare_backtest_run_id'], source_run_one.id)
        self.assertEqual(cloned_runs[1].parameters['compare_backtest_run_id'], source_run_two.id)
        self.assertEqual(cloned_runs[0].name, 'Source Run 525 [pit-2016-2024]')
        self.assertEqual(cloned_runs[1].name, 'Source Run 526 [pit-2016-2024]')
        self.assertEqual(cloned_runs[0].status, BacktestRun.Status.PENDING)
        self.assertEqual(cloned_runs[0].report, {})
        self.assertEqual(cloned_runs[0].cash, source_run_one.initial_capital)
        self.assertEqual(cloned_runs[1].cash, source_run_two.initial_capital)
        self.assertEqual(mock_run_backtest.call_count, 2)
        self.assertIn(f'{source_run_one.id} -> {cloned_runs[0].id}', output.getvalue())
        self.assertIn(f'{source_run_two.id} -> {cloned_runs[1].id}', output.getvalue())
