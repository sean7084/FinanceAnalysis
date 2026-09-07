import csv
import json
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db.utils import OperationalError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.analytics.models import TechnicalIndicator
from apps.factors.models import FactorScore
from apps.markets.models import Asset, BenchmarkIndexDaily, ExchangeTradingCalendar, IndexMembership, Market, OHLCV
from apps.macro.models import MarketContext
from apps.prediction.odds import estimate_trade_decision
from apps.prediction.models_lightgbm import LightGBMModelArtifact
from apps.prediction.models import ModelVersion, PredictionResult
from apps.prediction.tasks import _confidence, _feature_snapshot, _predicted_label, _probabilities_from_features
from apps.prediction.tasks_lstm import LSTM_MISSING_VALUE_STRATEGY, _predict_with_lstm
from apps.prediction.tasks_lightgbm import IdentityCalibrator, _create_feature_matrix
from apps.sentiment.models import SentimentScore
from . import tasks as backtest_tasks
from .models import BacktestRun, BacktestTrade
from .serializers import BacktestRunSerializer
from .task_health import get_backtest_run_task_owner_state
from .tasks import _pick_candidates, _resolve_macro_context_for_date, run_backtest


class IdentityScaler:
    def transform(self, matrix):
        return matrix


class CapturingScaler:
    def __init__(self):
        self.last_matrix = None

    def transform(self, matrix):
        self.last_matrix = matrix.copy()
        return matrix


class CapturingBooster:
    def __init__(self, feature_count=3, supported_devices=None, probabilities=None):
        self.feature_count = int(feature_count)
        self.supported_devices = set(supported_devices or [])
        self.last_matrix = None
        self.last_kwargs = None
        self.calls = []
        self.probabilities = np.asarray(probabilities or [[0.1, 0.2, 0.7]], dtype=np.float64)

    def num_feature(self):
        return self.feature_count

    def predict(self, matrix, **kwargs):
        device_type = kwargs.get('device_type')
        resolved_matrix = np.asarray(matrix, dtype=np.float64)
        self.last_matrix = resolved_matrix.copy()
        self.last_kwargs = dict(kwargs)
        self.calls.append({'shape': tuple(resolved_matrix.shape), 'kwargs': dict(kwargs)})

        if device_type and device_type not in self.supported_devices:
            raise RuntimeError(f'unsupported device: {device_type}')

        if self.probabilities.shape[0] == 1:
            return np.tile(self.probabilities, (len(resolved_matrix), 1))
        return self.probabilities


class StubCalibrator:
    def predict_proba(self, matrix):
        import numpy as np
        row_count = len(matrix) if hasattr(matrix, '__len__') else 1
        return np.tile(np.array([[0.1, 0.2, 0.7]]), (row_count, 1))


class StaticLstmModel:
    def __call__(self, tensor):
        return torch.tensor([[0.15, 0.35, 0.50]], dtype=torch.float32)


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

        ExchangeTradingCalendar.objects.bulk_create([
            ExchangeTradingCalendar(exchange_code='SSE', trade_date=self.d1, is_open=True),
            ExchangeTradingCalendar(exchange_code='SSE', trade_date=self.d2, is_open=True),
        ])

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
        IndexMembership.objects.bulk_create([
            IndexMembership(
                asset=self.asset,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date=self.d1 - timedelta(days=1),
                weight=Decimal('4.200000'),
            ),
            IndexMembership(
                asset=self.asset,
                index_code='000510.CSI',
                index_name='CSI A500',
                trade_date=self.d1 - timedelta(days=1),
                weight=Decimal('2.100000'),
            ),
        ])

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

    def test_celery_queue_split_routes_backtest_and_training_tasks(self):
        queue_names = [queue.name for queue in settings.CELERY_TASK_QUEUES]

        self.assertEqual(settings.CELERY_TASK_DEFAULT_QUEUE, 'ops')
        self.assertCountEqual(queue_names, ['ops', 'backtest', 'train-lightgbm', 'train-lstm'])
        self.assertEqual(settings.CELERY_TASK_ROUTES['apps.backtest.tasks.run_backtest']['queue'], 'backtest')
        self.assertEqual(
            settings.CELERY_TASK_ROUTES['apps.prediction.tasks_lightgbm.train_lightgbm_models']['queue'],
            'train-lightgbm',
        )
        self.assertEqual(
            settings.CELERY_TASK_ROUTES['apps.prediction.tasks_lstm.train_lstm_models']['queue'],
            'train-lstm',
        )

    def _auth(self):
        self.client.force_authenticate(user=self.user)

    def _create_run(self, **overrides):
        payload = {
            'user': self.user,
            'name': 'P15 Lifecycle Test Run',
            'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            'start_date': self.d1,
            'end_date': self.d2,
            'initial_capital': Decimal('100000.00'),
            'parameters': {
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
            },
        }
        payload.update(overrides)
        return BacktestRun.objects.create(**payload)

    def test_backtest_endpoint_requires_auth(self):
        response = self.client.get('/api/v1/backtest/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_backtest_list_returns_up_to_100_runs_for_workbench(self):
        self._auth()

        for index in range(120):
            self._create_run(name=f'P15 Pagination Run {index}')

        response = self.client.get('/api/v1/backtest/?page_size=100')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 120)
        self.assertEqual(len(response.data['results']), 100)

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

    @patch('apps.backtest.views.revoke_backtest_task')
    def test_pause_pending_backtest_marks_run_paused(self, mock_revoke):
        self._auth()
        run = self._create_run(current_task_id='task-pending')

        response = self.client.post(f'/api/v1/backtest/{run.id}/pause/')

        run.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(run.status, BacktestRun.Status.PAUSED)
        self.assertEqual(run.pending_control_action, BacktestRun.ControlAction.NONE)
        mock_revoke.assert_called_once_with(run, terminate=False)

    @patch('apps.backtest.views.revoke_backtest_task')
    def test_pause_running_backtest_marks_pending_pause(self, mock_revoke):
        self._auth()
        run = self._create_run(
            status=BacktestRun.Status.RUNNING,
            current_task_id='task-running',
        )

        response = self.client.post(f'/api/v1/backtest/{run.id}/pause/')

        run.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(run.status, BacktestRun.Status.RUNNING)
        self.assertEqual(run.pending_control_action, BacktestRun.ControlAction.PAUSE)
        mock_revoke.assert_called_once_with(run, terminate=False)

    @patch('apps.backtest.views.queue_backtest_run')
    def test_resume_paused_backtest_requeues_run(self, mock_queue):
        self._auth()
        run = self._create_run(
            status=BacktestRun.Status.PAUSED,
            pending_control_action=BacktestRun.ControlAction.PAUSE,
            error_message='paused on chunk boundary',
            completed_at=timezone.now(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(f'/api/v1/backtest/{run.id}/resume/')

        run.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(run.status, BacktestRun.Status.PENDING)
        self.assertEqual(run.pending_control_action, BacktestRun.ControlAction.NONE)
        self.assertEqual(run.error_message, '')
        self.assertIsNone(run.completed_at)
        mock_queue.assert_called_once_with(run)

    @patch('apps.backtest.views.revoke_backtest_task')
    def test_restart_running_backtest_marks_pending_restart(self, mock_revoke):
        self._auth()
        run = self._create_run(
            status=BacktestRun.Status.RUNNING,
            current_task_id='task-running',
        )

        response = self.client.post(f'/api/v1/backtest/{run.id}/restart/')

        run.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(run.status, BacktestRun.Status.RUNNING)
        self.assertEqual(run.pending_control_action, BacktestRun.ControlAction.RESTART)
        self.assertEqual(run.error_message, '')
        mock_revoke.assert_called_once_with(run, terminate=True)

    @patch('apps.backtest.views.revoke_backtest_task')
    def test_destroy_running_backtest_marks_pending_delete(self, mock_revoke):
        self._auth()
        run = self._create_run(
            status=BacktestRun.Status.RUNNING,
            current_task_id='task-running',
        )

        response = self.client.delete(f'/api/v1/backtest/{run.id}/')

        run.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(run.pending_control_action, BacktestRun.ControlAction.DELETE)
        self.assertTrue(BacktestRun.objects.filter(id=run.id).exists())
        mock_revoke.assert_called_once_with(run, terminate=True)

    @patch('apps.backtest.views.revoke_backtest_task')
    def test_destroy_paused_backtest_deletes_immediately(self, mock_revoke):
        self._auth()
        run = self._create_run(status=BacktestRun.Status.PAUSED)

        response = self.client.delete(f'/api/v1/backtest/{run.id}/')

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BacktestRun.objects.filter(id=run.id).exists())
        mock_revoke.assert_called_once()

    @patch('apps.backtest.serializers.get_backtest_run_task_owner_state', return_value={
        'task_state': 'PENDING',
        'task_age_seconds': 9999,
        'has_stale_task_owner': True,
    })
    def test_backtest_serializer_reports_stale_task_owner(self, _mock_task_owner_state):
        run = self._create_run(
            status=BacktestRun.Status.RUNNING,
            current_task_id='task-stale',
        )

        data = BacktestRunSerializer(run).data

        self.assertEqual(data['task_state'], 'PENDING')
        self.assertTrue(data['has_stale_task_owner'])

    @patch('apps.backtest.task_health.AsyncResult')
    def test_task_owner_state_does_not_mark_pending_continuation_chunk_as_stale(self, mock_async_result):
        mock_async_result.return_value.state = 'PENDING'
        run = self._create_run(
            status=BacktestRun.Status.RUNNING,
            current_task_id='task-queued-next-chunk',
            report={'progress': {'current_index': 30, 'total': 120}},
        )
        stale_dt = timezone.now() - timedelta(hours=2)
        BacktestRun.objects.filter(id=run.id).update(updated_at=stale_dt, started_at=stale_dt)
        run.refresh_from_db()

        state = get_backtest_run_task_owner_state(run)

        self.assertEqual(state['task_state'], 'PENDING')
        self.assertFalse(state['has_stale_task_owner'])

    @patch('apps.backtest.task_health.AsyncResult')
    def test_task_owner_state_marks_old_pending_run_without_progress_as_stale(self, mock_async_result):
        mock_async_result.return_value.state = 'PENDING'
        run = self._create_run(
            status=BacktestRun.Status.RUNNING,
            current_task_id='task-stuck-before-first-progress',
        )
        stale_dt = timezone.now() - timedelta(hours=2)
        BacktestRun.objects.filter(id=run.id).update(updated_at=stale_dt, started_at=stale_dt)
        run.refresh_from_db()

        state = get_backtest_run_task_owner_state(run)

        self.assertEqual(state['task_state'], 'PENDING')
        self.assertTrue(state['has_stale_task_owner'])

    @patch('apps.backtest.views.get_backtest_run_task_owner_state', return_value={
        'task_state': 'PENDING',
        'task_age_seconds': 9999,
        'has_stale_task_owner': True,
    })
    @patch('apps.backtest.views.queue_backtest_run')
    @patch('apps.backtest.views.revoke_backtest_task')
    def test_restart_stale_running_backtest_requeues_immediately(self, mock_revoke, mock_queue, _mock_task_owner_state):
        self._auth()
        run = self._create_run(
            status=BacktestRun.Status.RUNNING,
            current_task_id='task-stale',
            pending_control_action=BacktestRun.ControlAction.DELETE,
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(f'/api/v1/backtest/{run.id}/restart/')

        run.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(run.status, BacktestRun.Status.PENDING)
        self.assertEqual(run.pending_control_action, BacktestRun.ControlAction.NONE)
        self.assertEqual(run.current_task_id, '')
        mock_revoke.assert_called_once_with(run, terminate=True)
        mock_queue.assert_called_once_with(run)

    @patch('apps.backtest.views.get_backtest_run_task_owner_state', return_value={
        'task_state': 'PENDING',
        'task_age_seconds': 9999,
        'has_stale_task_owner': True,
    })
    @patch('apps.backtest.views.revoke_backtest_task')
    def test_destroy_stale_running_backtest_deletes_immediately(self, mock_revoke, _mock_task_owner_state):
        self._auth()
        run = self._create_run(
            status=BacktestRun.Status.RUNNING,
            current_task_id='task-stale',
            pending_control_action=BacktestRun.ControlAction.DELETE,
        )

        response = self.client.delete(f'/api/v1/backtest/{run.id}/')

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BacktestRun.objects.filter(id=run.id).exists())
        mock_revoke.assert_called_once()

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

    def test_run_backtest_does_not_store_runtime_benchmark_payload(self):
        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 No Runtime Benchmark Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('200000.00'),
            parameters={'top_n': 1, 'horizon_days': 7, 'up_threshold': 0.55},
        )

        run_backtest(run.id)
        run.refresh_from_db()

        self.assertNotIn('benchmark', run.report)

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

    def test_run_backtest_fails_when_required_pit_membership_is_missing(self):
        IndexMembership.objects.all().delete()

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Missing PIT Coverage Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={'top_n': 1, 'horizon_days': 7, 'up_threshold': 0.55},
        )

        result = run_backtest(run.id)
        run.refresh_from_db()

        self.assertIn('failed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.FAILED)
        self.assertIn('missing point-in-time membership coverage', run.error_message)

    @patch('apps.backtest.tasks._pick_candidates', side_effect=OperationalError('database connection dropped'))
    def test_run_backtest_persists_failure_after_database_error(self, _mock_pick_candidates):
        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Database Error Failure Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={'top_n': 1, 'horizon_days': 7, 'up_threshold': 0.55},
        )

        result = run_backtest(run.id)
        run.refresh_from_db()

        self.assertIn('Backtest failed', result)
        self.assertEqual(run.status, BacktestRun.Status.FAILED)
        self.assertEqual(run.current_task_id, '')
        self.assertIn('database connection dropped', run.error_message)
        self.assertEqual(BacktestTrade.objects.filter(backtest_run=run).count(), 0)

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

    def test_backtest_heuristic_runtime_matches_prediction_feature_snapshot(self):
        indicator_timestamp = timezone.make_aware(timezone.datetime.combine(self.d1, timezone.datetime.min.time()))
        TechnicalIndicator.objects.create(
            asset=self.asset,
            timestamp=indicator_timestamp,
            indicator_type='RSI',
            value=Decimal('59.25'),
            parameters={'timeperiod': 14},
        )
        TechnicalIndicator.objects.create(
            asset=self.asset,
            timestamp=indicator_timestamp,
            indicator_type='MOM_5D',
            value=Decimal('0.11250000'),
            parameters={'n_days': 5},
        )
        TechnicalIndicator.objects.create(
            asset=self.asset,
            timestamp=indicator_timestamp,
            indicator_type='RS_SCORE',
            value=Decimal('0.72000000'),
        )
        SentimentScore.objects.create(
            article=None,
            asset=self.asset,
            date=self.d1,
            score_type=SentimentScore.ScoreType.ASSET_7D,
            positive_score=Decimal('0.5'),
            neutral_score=Decimal('0.3'),
            negative_score=Decimal('0.2'),
            sentiment_score=Decimal('0.22'),
            sentiment_label=SentimentScore.Label.POSITIVE,
        )

        features = _feature_snapshot(self.asset.id, self.d1, cache={})
        expected_up, expected_flat, expected_down = _probabilities_from_features(features, 7, '')
        payload = backtest_tasks._predict_heuristic_for_asset(self.asset.id, self.d1, 7, cache={})

        self.assertAlmostEqual(float(payload['up_probability']), float(expected_up))
        self.assertAlmostEqual(float(payload['flat_probability']), float(expected_flat))
        self.assertAlmostEqual(float(payload['down_probability']), float(expected_down))
        self.assertEqual(payload['predicted_label'], _predicted_label(expected_up, expected_flat, expected_down))
        self.assertAlmostEqual(float(payload['confidence']), float(_confidence(expected_up, expected_flat, expected_down)))
        self.assertTrue(payload['generated_on_demand'])

    @patch('apps.backtest.tasks._extract_features_for_asset', return_value={'rsi': 50.0, 'mom_5d': 0.1, 'rs_score': 0.9, 'factor_composite': 0.8, 'sentiment_7d': 0.0})
    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_run_backtest_supports_lightgbm_prediction_source(self, mock_load_artifacts, _mock_extract_features):
        compare_run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Compare Target',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.COMPLETED,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            report={'equity_curve': [100000.0, 101000.0], 'prediction_source': 'lightgbm'},
            parameters={'prediction_source': 'lightgbm'},
        )
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
                'compare_backtest_run_id': compare_run.id,
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
        self.assertEqual(run.report['compare_backtest_run_id'], compare_run.id)
        self.assertEqual(run.report['horizon_days'], 7)
        self.assertEqual(run.report['model_reference_count'], 1)
        self.assertEqual(run.report['model_references'][0]['reference_type'], 'LightGBMModelArtifact')
        self.assertEqual(run.report['model_references'][0]['reference_id'], artifact.id)
        self.assertEqual(run.report['model_references'][0]['horizon_days'], 7)
        self.assertEqual(run.report['lightgbm_runtime']['inference_backend'], 'cpu_batched')
        self.assertGreater(run.report['lightgbm_runtime']['prediction_map_calls'], 0)
        self.assertGreater(run.report['lightgbm_runtime']['predicted_asset_count'], 0)
        self.assertGreaterEqual(run.report['lightgbm_runtime']['feature_extraction_seconds'], 0.0)
        self.assertGreaterEqual(run.report['lightgbm_runtime']['probability_inference_seconds'], 0.0)
        self.assertIsNotNone(sell_trade)
        self.assertEqual(sell_trade.metadata['exit_reason'], 'SCHEDULED')

    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_backtest_lightgbm_runtime_features_match_training_matrix_for_stored_lagged_features(self, mock_load_artifacts):
        d = date(2024, 2, 5)
        trade_dates = []
        current_date = d
        while len(trade_dates) < 30:
            if current_date.weekday() < 5:
                trade_dates.append(current_date)
            current_date -= timedelta(days=1)

        ExchangeTradingCalendar.objects.bulk_create([
            ExchangeTradingCalendar(exchange_code='SSE', trade_date=trade_date, is_open=True)
            for trade_date in trade_dates
        ], ignore_conflicts=True)
        IndexMembership.objects.bulk_create([
            IndexMembership(
                asset=self.asset,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date=trade_date,
                weight=Decimal('4.200000'),
            )
            for trade_date in trade_dates
        ])

        FactorScore.objects.create(
            asset=self.asset,
            date=d,
            mode=FactorScore.FactorMode.COMPOSITE,
            pe_ttm_percentile_score=Decimal('0.3'),
            pb_percentile_score=Decimal('0.4'),
            roe_trend_score=Decimal('0.6'),
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
            asset=self.asset,
            date=d,
            score_type=SentimentScore.ScoreType.ASSET_7D,
            positive_score=Decimal('0.55'),
            neutral_score=Decimal('0.25'),
            negative_score=Decimal('0.2'),
            sentiment_score=Decimal('0.35'),
            sentiment_label=SentimentScore.Label.POSITIVE,
        )

        for offset, as_of in enumerate(trade_dates):
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

        feature_names = [
            'rsi',
            'mom_5d',
            'rs_score',
            'rsi_lag_3d',
            'rsi_delta_3d',
            'mom_5d_delta_3d',
            'rs_score_delta_3d',
            'rsi_lag_5d',
            'rsi_delta_5d',
            'mom_5d_delta_5d',
            'rs_score_delta_5d',
            'rsi_lag_10d',
            'rsi_delta_10d',
            'mom_5d_delta_10d',
            'rs_score_delta_10d',
        ]
        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-bt-feature-parity',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/bt-feature-parity',
            feature_names=feature_names,
            is_active=True,
        )
        scaler = CapturingScaler()
        mock_load_artifacts.return_value = {
            'model': object(),
            'scaler': scaler,
            'calibrator': StubCalibrator(),
            'metadata': {'feature_names': feature_names},
        }

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LightGBM Feature Parity Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=d,
            end_date=d,
            initial_capital=Decimal('100000.00'),
            parameters={
                'prediction_source': 'lightgbm',
                'horizon_days': 7,
                'top_n': 1,
                'up_threshold': 0.55,
            },
        )

        payload = backtest_tasks._predict_lightgbm_for_asset(self.asset.id, d, 7, {}, run=run)
        training_row = _create_feature_matrix(d, d, asset_ids=[self.asset.id]).iloc[0]

        self.assertEqual(payload['model_artifact_id'], artifact.id)
        self.assertIsNotNone(scaler.last_matrix)
        for index, feature_name in enumerate(feature_names):
            self.assertAlmostEqual(float(scaler.last_matrix[0][index]), float(training_row[feature_name]))

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

    @patch('apps.backtest.tasks._extract_features_for_asset', return_value={'rsi': 50.0, 'mom_5d': 0.1, 'rs_score': 0.9, 'factor_composite': 0.8, 'sentiment_7d': 0.0})
    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_run_backtest_can_use_selected_inactive_lightgbm_artifact(self, mock_load_artifacts, _mock_extract_features):
        LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-active-artifact',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/active-artifact',
            feature_names=['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d'],
            is_active=True,
        )
        selected_artifact = LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-selected-artifact',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/selected-artifact',
            feature_names=['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d'],
            is_active=False,
        )
        mock_load_artifacts.return_value = {
            'model': object(),
            'scaler': IdentityScaler(),
            'calibrator': StubCalibrator(),
            'metadata': {'feature_names': ['rsi', 'mom_5d', 'rs_score', 'factor_composite', 'sentiment_7d']},
        }

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LightGBM Selected Artifact Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
                'lightgbm_model_artifact_id': selected_artifact.id,
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        buy_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.BUY).first()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertIsNotNone(buy_trade)
        self.assertEqual(buy_trade.signal_payload['model_artifact_id'], selected_artifact.id)
        self.assertEqual(run.report['model_references'][0]['reference_id'], selected_artifact.id)
        mock_load_artifacts.assert_called_with(7, selected_artifact.version)

    @patch('apps.backtest.tasks._pick_candidates')
    def test_scheduled_exit_prioritizes_stop_loss_when_enabled(self, mock_pick_candidates):
        OHLCV.objects.filter(asset=self.asset, date=self.d2).update(
            open=Decimal('9.0000'),
            high=Decimal('9.2000'),
            low=Decimal('8.8000'),
            close=Decimal('9.0000'),
            adj_close=Decimal('9.0000'),
            amount=Decimal('900000.0000'),
        )
        mock_pick_candidates.return_value = [
            {
                'asset_id': self.asset.id,
                'rank_value': Decimal('0.700000'),
                'signal_payload': {
                    'strategy': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                    'prediction_source': 'heuristic',
                    'candidate_mode': 'top_n',
                    'top_n_metric': 'up_prob_7d',
                    'horizon_days': 7,
                    'up_probability': 0.7,
                    'flat_probability': 0.2,
                    'down_probability': 0.1,
                    'confidence': 0.7,
                    'predicted_label': 'UP',
                    'trade_score': 1.2,
                    'target_price': 12.0,
                    'stop_loss_price': 9.5,
                    'suggested': True,
                    'generated_on_demand': True,
                },
            },
        ]

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Stop Loss Priority Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'enable_stop_target_exit': True,
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        sell_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.SELL).first()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertIsNotNone(sell_trade)
        self.assertEqual(sell_trade.metadata['exit_reason'], 'STOP_LOSS')

    @patch('apps.backtest.tasks._pick_candidates')
    def test_run_backtest_applies_capital_fraction_fee_and_slippage(self, mock_pick_candidates):
        mock_pick_candidates.return_value = [
            {
                'asset_id': self.asset.id,
                'rank_value': Decimal('0.700000'),
                'signal_payload': {
                    'strategy': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                    'prediction_source': 'heuristic',
                    'candidate_mode': 'top_n',
                    'top_n_metric': 'up_prob_7d',
                    'horizon_days': 7,
                    'up_probability': 0.7,
                    'flat_probability': 0.2,
                    'down_probability': 0.1,
                    'confidence': 0.7,
                    'predicted_label': 'UP',
                    'trade_score': 1.2,
                    'target_price': 12.0,
                    'stop_loss_price': 9.0,
                    'suggested': True,
                    'generated_on_demand': True,
                },
            },
        ]

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Cost Application Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'capital_fraction_per_entry': 0.25,
                'fee_rate': 0.01,
                'slippage_bps': 100,
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        buy_trade = BacktestTrade.objects.get(backtest_run=run, side=BacktestTrade.Side.BUY)
        sell_trade = BacktestTrade.objects.get(backtest_run=run, side=BacktestTrade.Side.SELL)

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertEqual(buy_trade.price, Decimal('10.1000'))
        self.assertEqual(buy_trade.slippage, Decimal('0.1000'))
        self.assertEqual(sell_trade.price, Decimal('10.8900'))
        self.assertEqual(sell_trade.slippage, Decimal('0.1100'))
        self.assertEqual(buy_trade.signal_payload['candidate_rank'], 1)
        self.assertEqual(buy_trade.signal_payload['candidate_rank_value'], 0.7)
        self.assertEqual(buy_trade.signal_payload['rank_value'], 0.7)
        self.assertTrue(buy_trade.signal_payload['candidate_selected'])
        self.assertEqual(buy_trade.signal_payload['up_threshold'], 0.55)
        self.assertTrue(buy_trade.signal_payload['passed_up_threshold'])
        self.assertGreater(buy_trade.fee, Decimal('0'))
        self.assertGreater(sell_trade.fee, Decimal('0'))
        self.assertAlmostEqual(float(buy_trade.amount + buy_trade.fee), 25000.0, places=2)

    @patch('apps.backtest.tasks._pick_candidates')
    def test_run_backtest_uses_default_cn_a_share_fee_schedule(self, mock_pick_candidates):
        mock_pick_candidates.return_value = [
            {
                'asset_id': self.asset.id,
                'rank_value': Decimal('0.700000'),
                'signal_payload': {
                    'strategy': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                    'prediction_source': 'heuristic',
                    'candidate_mode': 'top_n',
                    'top_n_metric': 'up_prob_7d',
                    'horizon_days': 7,
                    'up_probability': 0.7,
                    'flat_probability': 0.2,
                    'down_probability': 0.1,
                    'confidence': 0.7,
                    'predicted_label': 'UP',
                    'trade_score': 1.2,
                    'target_price': 12.0,
                    'stop_loss_price': 9.0,
                    'suggested': True,
                    'generated_on_demand': True,
                },
            },
        ]

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Default CN A Share Fees Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('10000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'slippage_bps': 0,
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        buy_trade = BacktestTrade.objects.get(backtest_run=run, side=BacktestTrade.Side.BUY)
        sell_trade = BacktestTrade.objects.get(backtest_run=run, side=BacktestTrade.Side.SELL)

        buy_levy_rate = Decimal('0.0000641')
        sell_levy_rate = Decimal('0.0005641')
        commission_min = Decimal('5')
        buy_amount = (Decimal('10000.00') - commission_min) / (Decimal('1') + buy_levy_rate)
        expected_buy_fee = commission_min + (buy_amount * buy_levy_rate)
        quantity = buy_amount / Decimal('10.0000')
        sell_amount = quantity * Decimal('11.0000')
        expected_sell_fee = commission_min + (sell_amount * sell_levy_rate)

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertEqual(run.report['fee_model'], 'cn_a_share_default')
        self.assertAlmostEqual(float(buy_trade.fee), float(expected_buy_fee), places=4)
        self.assertAlmostEqual(float(sell_trade.fee), float(expected_sell_fee), places=4)
        self.assertEqual(buy_trade.metadata['fee_breakdown']['commission'], 5.0)
        self.assertEqual(buy_trade.metadata['fee_breakdown']['stamp_duty'], 0.0)
        self.assertGreater(sell_trade.metadata['fee_breakdown']['stamp_duty'], 0.0)
        self.assertGreater(sell_trade.fee, buy_trade.fee)

    @patch('apps.backtest.tasks._pick_candidates')
    def test_entry_skips_asset_when_buy_close_is_invalid(self, mock_pick_candidates):
        OHLCV.objects.filter(asset=self.asset, date=self.d1).update(
            open=Decimal('0.0000'),
            high=Decimal('0.0000'),
            low=Decimal('0.0000'),
            close=Decimal('0.0000'),
            adj_close=Decimal('0.0000'),
            amount=Decimal('0.0000'),
        )
        mock_pick_candidates.return_value = [
            {
                'asset_id': self.asset.id,
                'rank_value': Decimal('0.700000'),
                'signal_payload': {
                    'strategy': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                    'prediction_source': 'heuristic',
                    'candidate_mode': 'top_n',
                    'top_n_metric': 'up_prob_7d',
                    'horizon_days': 7,
                    'up_probability': 0.7,
                    'flat_probability': 0.2,
                    'down_probability': 0.1,
                    'confidence': 0.7,
                    'predicted_label': 'UP',
                    'trade_score': 1.2,
                    'target_price': 12.0,
                    'stop_loss_price': 9.0,
                    'suggested': True,
                    'generated_on_demand': True,
                },
            },
        ]

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Invalid Entry Price Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertEqual(run.total_trades, 0)
        self.assertEqual(BacktestTrade.objects.filter(backtest_run=run).count(), 0)

    @patch('apps.backtest.tasks._pick_candidates')
    def test_scheduled_exit_retries_on_next_tradeable_price_after_invalid_close(self, mock_pick_candidates):
        d3 = self.d2 + timedelta(days=1)
        ExchangeTradingCalendar.objects.create(exchange_code='SSE', trade_date=d3, is_open=True)
        OHLCV.objects.filter(asset=self.asset, date=self.d2).update(
            open=Decimal('0.0000'),
            high=Decimal('0.0000'),
            low=Decimal('0.0000'),
            close=Decimal('0.0000'),
            adj_close=Decimal('0.0000'),
            amount=Decimal('0.0000'),
        )
        OHLCV.objects.create(
            asset=self.asset,
            date=d3,
            open=Decimal('10.6000'),
            high=Decimal('10.9000'),
            low=Decimal('10.5000'),
            close=Decimal('10.8000'),
            adj_close=Decimal('10.8000'),
            volume=130000,
            amount=Decimal('1404000.0000'),
        )
        mock_pick_candidates.return_value = [
            {
                'asset_id': self.asset.id,
                'rank_value': Decimal('0.700000'),
                'signal_payload': {
                    'strategy': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                    'prediction_source': 'heuristic',
                    'candidate_mode': 'top_n',
                    'top_n_metric': 'up_prob_7d',
                    'horizon_days': 7,
                    'up_probability': 0.7,
                    'flat_probability': 0.2,
                    'down_probability': 0.1,
                    'confidence': 0.7,
                    'predicted_label': 'UP',
                    'trade_score': 1.2,
                    'target_price': 12.0,
                    'stop_loss_price': 9.0,
                    'suggested': True,
                    'generated_on_demand': True,
                },
            },
        ]

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 Deferred Scheduled Exit Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=d3,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'holding_period_days': 1,
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        sell_trade = BacktestTrade.objects.get(backtest_run=run, side=BacktestTrade.Side.SELL)

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertEqual(sell_trade.trade_date, d3)
        self.assertEqual(sell_trade.metadata['exit_reason'], 'SCHEDULED')

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

    @patch('apps.backtest.tasks._predict_with_lstm')
    def test_run_backtest_can_use_selected_inactive_lstm_model_version(self, mock_predict_with_lstm):
        PredictionResult.objects.all().delete()
        ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.LSTM,
            version='lstm-active-runtime-test',
            status=ModelVersion.Status.READY,
            is_active=True,
            artifact_path='models/lstm/active-runtime-test',
        )
        selected_version = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.LSTM,
            version='lstm-selected-runtime-test',
            status=ModelVersion.Status.READY,
            is_active=False,
            artifact_path='models/lstm/selected-runtime-test',
        )
        mock_predict_with_lstm.return_value = {
            'model_version': selected_version,
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
            name='P15 LSTM Selected Version Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lstm',
                'lstm_model_version_id': selected_version.id,
            },
        )

        result = run_backtest(run.id)
        run.refresh_from_db()
        buy_trade = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.BUY).first()

        self.assertIn('completed', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertIsNotNone(buy_trade)
        self.assertEqual(buy_trade.signal_payload['model_version_id'], selected_version.id)
        self.assertEqual(run.report['model_references'][0]['reference_id'], selected_version.id)
        self.assertEqual(mock_predict_with_lstm.call_args.kwargs['model_version'], selected_version)

    @patch('apps.prediction.tasks_lstm._load_lstm_artifact')
    def test_backtest_lstm_runtime_matches_direct_prediction_helper(self, mock_load_lstm_artifact):
        version = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.LSTM,
            version='lstm-backtest-parity',
            status=ModelVersion.Status.READY,
            is_active=True,
            artifact_path='models/lstm/backtest-parity',
        )
        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LSTM Parity Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lstm',
                'lstm_model_version_id': version.id,
            },
        )
        feature_names = [
            'rsi', 'rsi__is_missing',
            'mom_5d', 'mom_5d__is_missing',
            'rs_score', 'rs_score__is_missing',
            'factor_composite', 'factor_composite__is_missing',
            'sentiment_7d', 'sentiment_7d__is_missing',
        ]
        mock_load_lstm_artifact.return_value = {
            'model': StaticLstmModel(),
            'feature_names': feature_names,
            'sequence_length': 5,
            'scaler_mean': np.zeros(len(feature_names), dtype=np.float32),
            'scaler_scale': np.ones(len(feature_names), dtype=np.float32),
            'file_path': 'models/lstm/backtest-parity/7d_model.pt',
            'missing_value_strategy': LSTM_MISSING_VALUE_STRATEGY,
        }

        trade_dates = [self.d2 - timedelta(days=offset) for offset in range(5)]
        for offset, trade_date in enumerate(reversed(trade_dates)):
            ExchangeTradingCalendar.objects.get_or_create(
                exchange_code='SSE',
                trade_date=trade_date,
                defaults={'is_open': True},
            )
            for index_code, index_name in (
                ('000300.SH', 'CSI 300'),
                ('000510.CSI', 'CSI A500'),
            ):
                IndexMembership.objects.get_or_create(
                    asset=self.asset,
                    index_code=index_code,
                    index_name=index_name,
                    trade_date=trade_date,
                    defaults={'weight': Decimal('1.000000')},
                )
            OHLCV.objects.update_or_create(
                asset=self.asset,
                date=trade_date,
                defaults={
                    'open': Decimal('10.0') + Decimal(offset) / Decimal('10'),
                    'high': Decimal('10.5') + Decimal(offset) / Decimal('10'),
                    'low': Decimal('9.8') + Decimal(offset) / Decimal('10'),
                    'close': Decimal('10.2') + Decimal(offset) / Decimal('20'),
                    'adj_close': Decimal('10.2') + Decimal(offset) / Decimal('20'),
                    'volume': 1000000 + offset * 10000,
                    'amount': Decimal('10200000') + Decimal(offset * 100000),
                },
            )
            FactorScore.objects.update_or_create(
                asset=self.asset,
                date=trade_date,
                mode=FactorScore.FactorMode.COMPOSITE,
                defaults={
                    'fundamental_score': Decimal('0.55') + Decimal(offset) / Decimal('100'),
                    'capital_flow_score': Decimal('0.56') + Decimal(offset) / Decimal('100'),
                    'technical_score': Decimal('0.57') + Decimal(offset) / Decimal('100'),
                    'composite_score': Decimal('0.58') + Decimal(offset) / Decimal('100'),
                    'bottom_probability_score': Decimal('0.45') + Decimal(offset) / Decimal('100'),
                },
            )
            SentimentScore.objects.create(
                article=None,
                asset=self.asset,
                date=trade_date,
                score_type=SentimentScore.ScoreType.ASSET_7D,
                positive_score=Decimal('0.5'),
                neutral_score=Decimal('0.3'),
                negative_score=Decimal('0.2'),
                sentiment_score=Decimal('0.10') + Decimal(offset) / Decimal('100'),
                sentiment_label=SentimentScore.Label.POSITIVE,
            )
            indicator_timestamp = timezone.make_aware(timezone.datetime.combine(trade_date, timezone.datetime.min.time()))
            TechnicalIndicator.objects.update_or_create(
                asset=self.asset,
                timestamp=indicator_timestamp,
                indicator_type='RSI',
                defaults={'value': Decimal('55.0') + Decimal(offset), 'parameters': {'timeperiod': 14}},
            )
            TechnicalIndicator.objects.update_or_create(
                asset=self.asset,
                timestamp=indicator_timestamp,
                indicator_type='MOM_5D',
                defaults={'value': Decimal('0.01000000') + Decimal(offset) / Decimal('1000'), 'parameters': {'n_days': 5}},
            )
            TechnicalIndicator.objects.update_or_create(
                asset=self.asset,
                timestamp=indicator_timestamp,
                indicator_type='RS_SCORE',
                defaults={'value': Decimal('0.60000000') + Decimal(offset) / Decimal('100'), 'parameters': {}},
            )

        expected = _predict_with_lstm(
            asset_id=self.asset.id,
            target_date=self.d2,
            horizon_days=7,
            model_version=version,
            cache={},
        )
        payload = backtest_tasks._predict_lstm_for_asset(
            self.asset.id,
            self.d2,
            7,
            cache={},
            run=run,
        )

        self.assertIsNotNone(expected)
        self.assertAlmostEqual(float(payload['up_probability']), float(expected['up_probability']))
        self.assertAlmostEqual(float(payload['flat_probability']), float(expected['flat_probability']))
        self.assertAlmostEqual(float(payload['down_probability']), float(expected['down_probability']))
        self.assertAlmostEqual(float(payload['confidence']), float(expected['confidence']))
        self.assertEqual(payload['predicted_label'], expected['predicted_label'])
        self.assertEqual(payload['model_version_id'], version.id)
        self.assertEqual(payload['model_version'], version.version)
        self.assertTrue(payload['generated_on_demand'])

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

    def test_backtest_serializer_rejects_mixed_legacy_and_structured_fee_params(self):
        self._auth()
        response = self.client.post(
            '/api/v1/backtest/',
            {
                'name': 'P15 Mixed Fee Config',
                'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                'start_date': str(self.d1),
                'end_date': str(self.d2),
                'initial_capital': '100000.00',
                'parameters': {
                    'top_n': 1,
                    'horizon_days': 7,
                    'up_threshold': 0.55,
                    'fee_rate': 0.001,
                    'commission_rate_per_mille': 0.1,
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('fee_rate cannot be combined with structured fee parameters', str(response.data))

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

    @patch('apps.backtest.views.run_backtest.delay')
    def test_backtest_serializer_normalizes_lightgbm_runtime_params(self, mock_delay):
        self._auth()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                '/api/v1/backtest/',
                {
                    'name': 'P15 LightGBM Runtime Params',
                    'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                    'start_date': str(self.d1),
                    'end_date': str(self.d2),
                    'initial_capital': '100000.00',
                    'parameters': {
                        'top_n': 1,
                        'horizon_days': 7,
                        'up_threshold': 0.55,
                        'prediction_source': 'lightgbm',
                        'lightgbm_inference_backend': 'cpu_batched',
                        'lightgbm_batch_size': '128',
                    },
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        run = BacktestRun.objects.latest('id')
        self.assertEqual(run.parameters['lightgbm_inference_backend'], 'cpu_batched')
        self.assertEqual(run.parameters['lightgbm_batch_size'], 128)
        mock_delay.assert_called_once()

    @patch('apps.backtest.views.run_backtest.delay')
    def test_backtest_serializer_accepts_windows_gpu_runtime_params(self, mock_delay):
        self._auth()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                '/api/v1/backtest/',
                {
                    'name': 'P15 LightGBM Windows GPU Params',
                    'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
                    'start_date': str(self.d1),
                    'end_date': str(self.d2),
                    'initial_capital': '100000.00',
                    'parameters': {
                        'top_n': 1,
                        'horizon_days': 7,
                        'up_threshold': 0.55,
                        'prediction_source': 'lightgbm',
                        'lightgbm_inference_backend': 'windows_gpu',
                        'lightgbm_batch_size': '64',
                    },
                },
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        run = BacktestRun.objects.latest('id')
        self.assertEqual(run.parameters['lightgbm_inference_backend'], 'windows_gpu')
        self.assertEqual(run.parameters['lightgbm_batch_size'], 64)
        mock_delay.assert_called_once()

    @patch('apps.backtest.tasks.estimate_trade_decision')
    @patch('apps.backtest.tasks._extract_features_for_asset')
    @patch('apps.backtest.tasks._eligible_backtest_asset_ids')
    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_lightgbm_prediction_map_cpu_batched_matches_cpu_serial(
        self,
        mock_load_artifacts,
        mock_eligible_asset_ids,
        mock_extract_features,
        mock_estimate_trade_decision,
    ):
        second_asset = Asset.objects.create(
            market=self.market,
            symbol='600002',
            ts_code='600002.SH',
            name='Backtest Asset 2',
        )
        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-batch-test',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/batch-test',
            feature_names=['rsi', 'mom_5d', 'rs_score'],
            is_active=True,
        )
        serial_scaler = CapturingScaler()
        batched_scaler = CapturingScaler()
        mock_load_artifacts.side_effect = [
            {
                'model': object(),
                'scaler': serial_scaler,
                'calibrator': StubCalibrator(),
                'metadata': {'feature_names': ['rsi', 'mom_5d', 'rs_score']},
            },
            {
                'model': object(),
                'scaler': batched_scaler,
                'calibrator': StubCalibrator(),
                'metadata': {'feature_names': ['rsi', 'mom_5d', 'rs_score']},
            },
        ]
        mock_eligible_asset_ids.return_value = [self.asset.id, second_asset.id]

        def _feature_side_effect(asset_id, *_args, **_kwargs):
            base = float(asset_id % 10)
            return {
                'rsi': 40.0 + base,
                'mom_5d': 0.1 + (base / 100.0),
                'rs_score': 0.7 + (base / 100.0),
            }

        def _trade_decision_side_effect(*, asset_id, **_kwargs):
            return {
                'trade_score': Decimal(str(asset_id)),
                'target_price': Decimal('12.5'),
                'stop_loss_price': Decimal('9.5'),
                'risk_reward_ratio': Decimal('1.5'),
                'suggested': True,
            }

        mock_extract_features.side_effect = _feature_side_effect
        mock_estimate_trade_decision.side_effect = _trade_decision_side_effect

        serial_run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LightGBM Serial Map',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 2,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
                'lightgbm_inference_backend': 'cpu_serial',
                'lightgbm_model_artifact_id': artifact.id,
            },
        )
        batched_run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LightGBM Batched Map',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 2,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
                'lightgbm_inference_backend': 'cpu_batched',
                'lightgbm_batch_size': 16,
                'lightgbm_model_artifact_id': artifact.id,
            },
        )

        serial_cache = {}
        batched_cache = {}
        serial_map = backtest_tasks._build_lightgbm_prediction_map(self.d1, 7, serial_cache, run=serial_run)
        batched_map = backtest_tasks._build_lightgbm_prediction_map(self.d1, 7, batched_cache, run=batched_run)

        self.assertEqual(serial_map, batched_map)
        self.assertEqual(serial_cache['lightgbm_runtime_metrics']['inference_backend'], 'cpu_serial')
        self.assertEqual(serial_cache['lightgbm_runtime_metrics']['batch_size'], 1)
        self.assertEqual(batched_cache['lightgbm_runtime_metrics']['inference_backend'], 'cpu_batched')
        self.assertEqual(batched_cache['lightgbm_runtime_metrics']['batch_size'], 16)
        self.assertEqual(batched_cache['lightgbm_runtime_metrics']['batch_prediction_calls'], 1)
        self.assertEqual(batched_cache['lightgbm_runtime_metrics']['batch_prediction_rows'], 2)
        self.assertEqual(serial_scaler.last_matrix.shape, (1, 3))
        self.assertEqual(batched_scaler.last_matrix.shape, (2, 3))

    @patch('apps.backtest.tasks.estimate_trade_decision')
    @patch('apps.backtest.tasks._extract_features_for_asset')
    @patch('apps.backtest.tasks._eligible_backtest_asset_ids')
    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_lightgbm_prediction_map_windows_gpu_matches_cpu_serial(
        self,
        mock_load_artifacts,
        mock_eligible_asset_ids,
        mock_extract_features,
        mock_estimate_trade_decision,
    ):
        second_asset = Asset.objects.create(
            market=self.market,
            symbol='600002',
            ts_code='600002.SH',
            name='Backtest Asset 2',
        )
        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-gpu-test',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/gpu-test',
            feature_names=['rsi', 'mom_5d', 'rs_score'],
            is_active=True,
        )
        serial_model = CapturingBooster(feature_count=3)
        gpu_model = CapturingBooster(feature_count=3, supported_devices={'gpu'})
        serial_scaler = CapturingScaler()
        gpu_scaler = CapturingScaler()
        mock_load_artifacts.side_effect = [
            {
                'model': serial_model,
                'scaler': serial_scaler,
                'calibrator': IdentityCalibrator(serial_model),
                'metadata': {'feature_names': ['rsi', 'mom_5d', 'rs_score']},
            },
            {
                'model': gpu_model,
                'scaler': gpu_scaler,
                'calibrator': IdentityCalibrator(gpu_model),
                'metadata': {'feature_names': ['rsi', 'mom_5d', 'rs_score']},
            },
        ]
        mock_eligible_asset_ids.return_value = [self.asset.id, second_asset.id]

        def _feature_side_effect(asset_id, *_args, **_kwargs):
            base = float(asset_id % 10)
            return {
                'rsi': 40.0 + base,
                'mom_5d': 0.1 + (base / 100.0),
                'rs_score': 0.7 + (base / 100.0),
            }

        def _trade_decision_side_effect(*, asset_id, **_kwargs):
            return {
                'trade_score': Decimal(str(asset_id)),
                'target_price': Decimal('12.5'),
                'stop_loss_price': Decimal('9.5'),
                'risk_reward_ratio': Decimal('1.5'),
                'suggested': True,
            }

        mock_extract_features.side_effect = _feature_side_effect
        mock_estimate_trade_decision.side_effect = _trade_decision_side_effect

        serial_run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LightGBM Serial GPU Oracle',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 2,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
                'lightgbm_inference_backend': 'cpu_serial',
                'lightgbm_model_artifact_id': artifact.id,
            },
        )
        gpu_run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LightGBM Windows GPU Map',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 2,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
                'lightgbm_inference_backend': 'windows_gpu',
                'lightgbm_batch_size': 16,
                'lightgbm_model_artifact_id': artifact.id,
            },
        )

        serial_cache = {}
        gpu_cache = {}
        serial_map = backtest_tasks._build_lightgbm_prediction_map(self.d1, 7, serial_cache, run=serial_run)
        gpu_map = backtest_tasks._build_lightgbm_prediction_map(self.d1, 7, gpu_cache, run=gpu_run)

        self.assertEqual(serial_map, gpu_map)
        self.assertEqual(serial_cache['lightgbm_runtime_metrics']['inference_backend'], 'cpu_serial')
        self.assertEqual(gpu_cache['lightgbm_runtime_metrics']['inference_backend'], 'windows_gpu')
        self.assertEqual(gpu_cache['lightgbm_runtime_metrics']['batch_size'], 16)
        self.assertEqual(gpu_cache['lightgbm_runtime_metrics']['batch_prediction_calls'], 1)
        self.assertEqual(gpu_cache['lightgbm_runtime_metrics']['batch_prediction_rows'], 2)
        self.assertEqual(serial_scaler.last_matrix.shape, (1, 3))
        self.assertEqual(gpu_scaler.last_matrix.shape, (2, 3))
        self.assertTrue(any(call['kwargs'].get('device_type') == 'gpu' for call in gpu_model.calls))
        self.assertEqual(gpu_model.last_kwargs.get('device_type'), 'gpu')

    @patch('apps.backtest.tasks.estimate_trade_decision')
    @patch('apps.backtest.tasks._extract_features_for_asset')
    @patch('apps.backtest.tasks._eligible_backtest_asset_ids')
    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_lightgbm_matrix_cache_hit_preserves_windows_gpu_backend(
        self,
        mock_load_artifacts,
        mock_eligible_asset_ids,
        mock_extract_features,
        mock_estimate_trade_decision,
    ):
        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-gpu-cache-test',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/gpu-cache-test',
            feature_names=['rsi', 'mom_5d', 'rs_score'],
            is_active=True,
        )
        gpu_model = CapturingBooster(feature_count=3, supported_devices={'gpu'})
        mock_load_artifacts.return_value = {
            'model': gpu_model,
            'scaler': IdentityScaler(),
            'calibrator': IdentityCalibrator(gpu_model),
            'metadata': {'feature_names': ['rsi', 'mom_5d', 'rs_score']},
        }
        mock_eligible_asset_ids.return_value = [self.asset.id]
        mock_extract_features.return_value = {
            'rsi': 42.0,
            'mom_5d': 0.12,
            'rs_score': 0.74,
        }
        mock_estimate_trade_decision.return_value = {
            'trade_score': Decimal('1.0'),
            'target_price': Decimal('12.5'),
            'stop_loss_price': Decimal('9.5'),
            'risk_reward_ratio': Decimal('1.5'),
            'suggested': True,
        }

        shared_scope = 'gpu-cache-preserve-scope'
        first_run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LightGBM Windows GPU Cache Builder',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
                'lightgbm_inference_backend': 'windows_gpu',
                'lightgbm_batch_size': 16,
                'lightgbm_model_artifact_id': artifact.id,
                'matrix_signal_cache_key': shared_scope,
            },
        )
        second_run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LightGBM Windows GPU Cache Hit',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
                'lightgbm_inference_backend': 'windows_gpu',
                'lightgbm_batch_size': 16,
                'lightgbm_model_artifact_id': artifact.id,
                'matrix_signal_cache_key': shared_scope,
            },
        )

        first_cache = {}
        second_cache = {}
        first_map = backtest_tasks._build_lightgbm_prediction_map(self.d1, 7, first_cache, run=first_run)
        second_map = backtest_tasks._build_lightgbm_prediction_map(self.d1, 7, second_cache, run=second_run)

        self.assertEqual(first_map, second_map)
        self.assertEqual(first_cache['lightgbm_runtime_metrics']['inference_backend'], 'windows_gpu')
        self.assertEqual(second_cache['lightgbm_runtime_metrics']['inference_backend'], 'windows_gpu')
        self.assertEqual(second_cache['lightgbm_runtime_metrics']['matrix_cache_hits'], 1)
        self.assertEqual(second_cache['lightgbm_runtime_metrics']['batch_prediction_calls'], 0)
        self.assertEqual(second_cache['lightgbm_runtime_metrics']['batch_prediction_rows'], 0)

    @patch('apps.backtest.tasks.estimate_trade_decision')
    @patch('apps.backtest.tasks._extract_features_for_asset')
    @patch('apps.backtest.tasks._eligible_backtest_asset_ids')
    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_lightgbm_prediction_map_windows_gpu_falls_back_to_cpu_serial(
        self,
        mock_load_artifacts,
        mock_eligible_asset_ids,
        mock_extract_features,
        mock_estimate_trade_decision,
    ):
        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-gpu-fallback-test',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/gpu-fallback-test',
            feature_names=['rsi', 'mom_5d', 'rs_score'],
            is_active=True,
        )
        fallback_model = CapturingBooster(feature_count=3, supported_devices=set())
        mock_load_artifacts.return_value = {
            'model': fallback_model,
            'scaler': IdentityScaler(),
            'calibrator': IdentityCalibrator(fallback_model),
            'metadata': {'feature_names': ['rsi', 'mom_5d', 'rs_score']},
        }
        mock_eligible_asset_ids.return_value = [self.asset.id]
        mock_extract_features.return_value = {
            'rsi': 42.0,
            'mom_5d': 0.12,
            'rs_score': 0.74,
        }
        mock_estimate_trade_decision.return_value = {
            'trade_score': Decimal('1.0'),
            'target_price': Decimal('12.5'),
            'stop_loss_price': Decimal('9.5'),
            'risk_reward_ratio': Decimal('1.5'),
            'suggested': True,
        }

        run = BacktestRun.objects.create(
            user=self.user,
            name='P15 LightGBM Windows GPU Fallback',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.d1,
            end_date=self.d2,
            initial_capital=Decimal('100000.00'),
            parameters={
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'prediction_source': 'lightgbm',
                'lightgbm_inference_backend': 'windows_gpu',
                'lightgbm_batch_size': 16,
                'lightgbm_model_artifact_id': artifact.id,
            },
        )

        cache = {}
        mapping = backtest_tasks._build_lightgbm_prediction_map(self.d1, 7, cache, run=run)

        self.assertIn(self.asset.id, mapping)
        self.assertEqual(cache['lightgbm_runtime_metrics']['inference_backend'], 'cpu_serial')
        self.assertEqual(cache['lightgbm_runtime_metrics']['batch_size'], 1)
        self.assertTrue(any(call['kwargs'].get('device_type') == 'cuda' for call in fallback_model.calls))
        self.assertTrue(any(call['kwargs'].get('device_type') == 'gpu' for call in fallback_model.calls))
        self.assertEqual(fallback_model.last_kwargs, {})

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

        IndexMembership.objects.bulk_create([
            IndexMembership(
                asset=chunk_asset,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date=trading_dates[0] - timedelta(days=1),
                weight=Decimal('4.200000'),
            ),
            IndexMembership(
                asset=chunk_asset,
                index_code='000510.CSI',
                index_name='CSI A500',
                trade_date=trading_dates[0] - timedelta(days=1),
                weight=Decimal('2.100000'),
            ),
        ])

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

    @patch('apps.backtest.tasks.BACKTEST_CHUNK_TRADING_DAYS', 1)
    def test_run_backtest_pauses_at_chunk_boundary_when_requested(self):
        run = self._create_run(pending_control_action=BacktestRun.ControlAction.PAUSE)

        result = run_backtest(run.id)
        run.refresh_from_db()

        self.assertIn('paused', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.PAUSED)
        self.assertEqual(run.pending_control_action, BacktestRun.ControlAction.NONE)
        self.assertEqual(run.current_task_id, '')
        self.assertIn('runtime_state', run.report)
        self.assertIn('progress', run.report)

    @patch('apps.backtest.tasks.queue_backtest_run')
    @patch('apps.backtest.tasks.BACKTEST_CHUNK_TRADING_DAYS', 1)
    def test_run_backtest_restarts_at_chunk_boundary_when_requested(self, mock_queue):
        run = self._create_run(pending_control_action=BacktestRun.ControlAction.RESTART)

        result = run_backtest(run.id)
        run.refresh_from_db()

        self.assertIn('restart queued', result.lower())
        self.assertEqual(run.status, BacktestRun.Status.PENDING)
        self.assertEqual(run.pending_control_action, BacktestRun.ControlAction.NONE)
        self.assertEqual(run.current_task_id, '')
        self.assertEqual(run.trades.count(), 0)
        self.assertNotIn('runtime_state', run.report)
        self.assertNotIn('progress', run.report)
        mock_queue.assert_called_once()

    @patch('apps.backtest.tasks.BACKTEST_CHUNK_TRADING_DAYS', 1)
    def test_run_backtest_deletes_at_chunk_boundary_when_requested(self):
        run = self._create_run()
        original_save_runtime_state = backtest_tasks._save_runtime_state

        def _mark_delete_after_chunk(*args, **kwargs):
            original_save_runtime_state(*args, **kwargs)
            BacktestRun.objects.filter(id=run.id).update(
                pending_control_action=BacktestRun.ControlAction.DELETE,
            )

        with patch('apps.backtest.tasks._save_runtime_state', side_effect=_mark_delete_after_chunk):
            result = run_backtest(run.id)

        self.assertIn('deleted', result.lower())
        self.assertFalse(BacktestRun.objects.filter(id=run.id).exists())

    def test_capital_fraction_defaults_to_full_allocation_even_with_entry_weekdays(self):
        run = BacktestRun.objects.create(
            user=self.user,
            name='Weekday Legacy Config',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=self.today,
            end_date=self.today,
            initial_capital=Decimal('100000.00'),
            parameters={
                'entry_weekdays': ['TUE', 'THU'],
            },
        )

        self.assertEqual(backtest_tasks._capital_fraction_per_entry(run, [1, 3]), Decimal('1'))

    @patch('apps.backtest.tasks._extract_features_for_asset', return_value={'rsi': 50.0, 'mom_5d': 0.1, 'rs_score': 0.9, 'factor_composite': 0.8, 'sentiment_7d': 0.0})
    @patch('apps.backtest.tasks._load_model_artifacts')
    def test_backtest_ignores_entry_weekdays_and_uses_all_trading_days(self, mock_load_artifacts, _mock_extract_features):
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

        membership_rows = []
        for asset in schedule_assets:
            membership_rows.append(
                IndexMembership(
                    asset=asset,
                    index_code='000300.SH',
                    index_name='CSI 300',
                    trade_date=schedule_start - timedelta(days=1),
                    weight=Decimal('4.200000'),
                )
            )
            membership_rows.append(
                IndexMembership(
                    asset=asset,
                    index_code='000510.CSI',
                    index_name='CSI A500',
                    trade_date=schedule_start - timedelta(days=1),
                    weight=Decimal('2.100000'),
                )
            )
        IndexMembership.objects.bulk_create(membership_rows)

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
        self.assertEqual(buy_dates, ['2026-04-06', '2026-04-07'])
        self.assertEqual(sell_dates, ['2026-04-13', '2026-04-14'])
        self.assertEqual(run.report['entry_weekdays'], [1, 3])
        self.assertEqual(run.report['holding_period_days'], 7)
        self.assertEqual(run.report['prediction_source'], 'lightgbm')
        self.assertEqual(trades[0].signal_payload['model_artifact_id'], artifact.id)
        self.assertTrue(trades[0].signal_payload['generated_on_demand'])
        self.assertNotIn('benchmark', run.report)


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
        self.assertTrue(all('entry_weekdays' not in run.parameters for run in runs))
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
            self.assertNotIn('entry_weekdays', manifest)

            with (output_dir / 'run_config_results.csv').open(newline='', encoding='utf-8') as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), 2)
            self.assertCountEqual(
                [row['prediction_source'] for row in rows],
                ['heuristic', 'lstm'],
            )
            self.assertTrue(all(not row['entry_weekdays'] for row in rows))
            self.assertIn('Reference benchmark suite exported to', output.getvalue())

    @patch('apps.backtest.management.commands.run_core_backtest_matrix.queue_backtest_run')
    def test_run_core_backtest_matrix_exports_compact_bundle(self, mock_queue_backtest_run):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / 'matrix_bundle'
            output = StringIO()

            call_command(
                'run_core_backtest_matrix',
                start_date='2026-01-01',
                end_date='2026-12-31',
                variants='top-n',
                sources='heuristic',
                name_prefix='matrixcmd',
                output_dir=str(output_dir),
                queue=True,
                stdout=output,
            )

            self.assertEqual(mock_queue_backtest_run.call_count, 9)
            self.assertTrue((output_dir / 'run_summary.csv').exists())
            self.assertTrue((output_dir / 'run_config_results.csv').exists())
            self.assertTrue((output_dir / 'model_references.csv').exists())
            self.assertTrue((output_dir / 'matrix_manifest.json').exists())

            manifest = json.loads((output_dir / 'matrix_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['variants'], ['top-n'])
            self.assertEqual(manifest['sources'], ['heuristic'])
            self.assertTrue(manifest['queued'])
            self.assertFalse(manifest['execute_inline'])
            self.assertEqual(manifest['chunk_trading_days'], 60)
            self.assertTrue(manifest['matrix_signal_cache_key'])
            self.assertEqual(len(manifest['run_ids']), 9)

            with (output_dir / 'run_config_results.csv').open(newline='', encoding='utf-8') as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), 9)
            self.assertCountEqual(
                [row['prediction_source'] for row in rows],
                ['heuristic'] * 9,
            )
            self.assertTrue(all(not row['entry_weekdays'] for row in rows))
            self.assertTrue(all(row['chunk_trading_days'] == '60' for row in rows))
            self.assertTrue(all(row['matrix_signal_cache_key'] for row in rows))
            self.assertIn('Core matrix exported to', output.getvalue())

    @patch('apps.backtest.management.commands.run_core_backtest_matrix.queue_backtest_run')
    def test_run_core_backtest_matrix_stamps_lightgbm_runtime_params(self, mock_queue_backtest_run):
        LightGBMModelArtifact.objects.create(
            horizon_days=3,
            version='lgb-matrix-3d',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/matrix-3d',
            feature_names=['rsi'],
            is_active=True,
        )
        LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-matrix-7d',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/matrix-7d',
            feature_names=['rsi'],
            is_active=True,
        )
        LightGBMModelArtifact.objects.create(
            horizon_days=30,
            version='lgb-matrix-30d',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/matrix-30d',
            feature_names=['rsi'],
            is_active=True,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / 'matrix_lightgbm_bundle'
            output = StringIO()

            call_command(
                'run_core_backtest_matrix',
                start_date='2026-01-01',
                end_date='2026-12-31',
                variants='top-n',
                sources='lightgbm',
                name_prefix='matrixlgbm',
                output_dir=str(output_dir),
                queue=True,
                lightgbm_inference_backend='cpu_batched',
                lightgbm_batch_size=128,
                stdout=output,
            )

            manifest = json.loads((output_dir / 'matrix_manifest.json').read_text(encoding='utf-8'))

        runs = list(BacktestRun.objects.order_by('id'))
        self.assertEqual(mock_queue_backtest_run.call_count, 9)
        self.assertEqual(len(runs), 9)
        self.assertTrue(all(run.parameters['prediction_source'] == 'lightgbm' for run in runs))
        self.assertTrue(all(run.parameters['lightgbm_inference_backend'] == 'cpu_batched' for run in runs))
        self.assertTrue(all(run.parameters['lightgbm_batch_size'] == 128 for run in runs))
        self.assertTrue(all(run.parameters['lightgbm_model_artifact_id'] for run in runs))
        self.assertEqual(manifest['lightgbm_inference_backend'], 'cpu_batched')
        self.assertEqual(manifest['lightgbm_batch_size'], 128)

    @patch('apps.backtest.management.commands.run_core_backtest_matrix.queue_backtest_run')
    def test_run_core_backtest_matrix_stamps_windows_gpu_runtime_params(self, mock_queue_backtest_run):
        LightGBMModelArtifact.objects.create(
            horizon_days=3,
            version='lgb-matrix-gpu-3d',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/matrix-gpu-3d',
            feature_names=['rsi'],
            is_active=True,
        )
        LightGBMModelArtifact.objects.create(
            horizon_days=7,
            version='lgb-matrix-gpu-7d',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/matrix-gpu-7d',
            feature_names=['rsi'],
            is_active=True,
        )
        LightGBMModelArtifact.objects.create(
            horizon_days=30,
            version='lgb-matrix-gpu-30d',
            status=LightGBMModelArtifact.Status.READY,
            artifact_path='models/lightgbm/matrix-gpu-30d',
            feature_names=['rsi'],
            is_active=True,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / 'matrix_lightgbm_gpu_bundle'
            output = StringIO()

            call_command(
                'run_core_backtest_matrix',
                start_date='2026-01-01',
                end_date='2026-12-31',
                variants='top-n',
                sources='lightgbm',
                name_prefix='matrixlgbmgpu',
                output_dir=str(output_dir),
                queue=True,
                lightgbm_inference_backend='windows_gpu',
                lightgbm_batch_size=64,
                stdout=output,
            )

            manifest = json.loads((output_dir / 'matrix_manifest.json').read_text(encoding='utf-8'))

        runs = list(BacktestRun.objects.order_by('id'))
        self.assertEqual(mock_queue_backtest_run.call_count, 9)
        self.assertEqual(len(runs), 9)
        self.assertTrue(all(run.parameters['lightgbm_inference_backend'] == 'windows_gpu' for run in runs))
        self.assertTrue(all(run.parameters['lightgbm_batch_size'] == 64 for run in runs))
        self.assertEqual(manifest['lightgbm_inference_backend'], 'windows_gpu')
        self.assertEqual(manifest['lightgbm_batch_size'], 64)

    def test_run_core_backtest_matrix_inline_scheduler_round_robins_continuations(self):
        from apps.backtest.management.commands.run_core_backtest_matrix import Command

        command = Command()
        calls = []
        continuation_counts = {101: 0, 102: 0}

        def _fake_run_backtest(run_id):
            calls.append(run_id)
            if continuation_counts[run_id] == 0:
                continuation_counts[run_id] += 1
                backtest_tasks.run_backtest.delay(run_id)

        with patch('apps.backtest.management.commands.run_core_backtest_matrix.run_backtest', side_effect=_fake_run_backtest):
            command._run_backtests_inline_to_completion([101, 102])

        self.assertEqual(calls, [101, 102, 101, 102])

    @patch('apps.backtest.management.commands.run_core_backtest_matrix.run_backtest')
    def test_run_core_backtest_matrix_execute_inline_creates_all_runs_before_execution(self, mock_run_backtest):
        counts_at_execution = []

        def _fake_run_backtest(_run_id):
            counts_at_execution.append(BacktestRun.objects.count())

        mock_run_backtest.side_effect = _fake_run_backtest

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / 'matrix_inline_bundle'
            output = StringIO()

            call_command(
                'run_core_backtest_matrix',
                start_date='2026-01-01',
                end_date='2026-12-31',
                variants='top-n',
                sources='heuristic',
                name_prefix='matrixinline',
                output_dir=str(output_dir),
                execute_inline=True,
                stdout=output,
            )

            manifest = json.loads((output_dir / 'matrix_manifest.json').read_text(encoding='utf-8'))

        self.assertEqual(mock_run_backtest.call_count, 9)
        self.assertEqual(counts_at_execution, [9] * 9)
        self.assertFalse(manifest['queued'])
        self.assertTrue(manifest['execute_inline'])
        self.assertIn('Executed inline matrix to completion.', output.getvalue())

    def test_export_backtest_runs_includes_compare_backtest_run_id(self):
        compare_run = BacktestRun.objects.create(
            user=self.user,
            name='Export Compare Target',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.COMPLETED,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
            initial_capital=Decimal('100000.00'),
            report={'equity_curve': [100000.0, 101000.0], 'prediction_source': 'lightgbm'},
            parameters={'prediction_source': 'lightgbm'},
        )
        run = BacktestRun.objects.create(
            user=self.user,
            name='Export Subject Run',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            status=BacktestRun.Status.COMPLETED,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
            initial_capital=Decimal('100000.00'),
            report={'equity_curve': [100000.0, 102000.0], 'prediction_source': 'lightgbm'},
            parameters={
                'prediction_source': 'lightgbm',
                'top_n': 1,
                'horizon_days': 7,
                'up_threshold': 0.55,
                'compare_backtest_run_id': compare_run.id,
            },
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / 'export_compare'
            call_command(
                'export_backtest_runs',
                start_id=run.id,
                end_id=run.id,
                output_dir=str(output_dir),
            )

            with (output_dir / 'run_config_results.csv').open(newline='', encoding='utf-8') as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['compare_backtest_run_id'], str(compare_run.id))
