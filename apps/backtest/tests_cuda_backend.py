"""#150: the backend rename has no alias, and used_gpu must reach the report.

The probe already returned used_gpu/gpu_device_type and the caller discarded them, so a run
could report 'cuda' while every prediction ran on CPU. These pin the plumbing and the
migration that a no-alias rename requires.
"""

import importlib

from django.apps import apps as django_apps
from django.test import SimpleTestCase, TestCase

from apps.backtest import tasks as backtest_tasks
from apps.backtest.models import BacktestRun
from apps.backtest.serializers import VALID_LIGHTGBM_INFERENCE_BACKENDS

migration = importlib.import_module('apps.backtest.migrations.0005_rename_windows_gpu_backend_to_cuda')


class BackendRenameTests(SimpleTestCase):
    def test_cuda_is_valid_and_windows_gpu_is_not(self):
        self.assertIn('cuda', VALID_LIGHTGBM_INFERENCE_BACKENDS)
        self.assertNotIn('windows_gpu', VALID_LIGHTGBM_INFERENCE_BACKENDS)

    def test_runtime_metrics_default_to_no_gpu(self):
        metrics = backtest_tasks._default_lightgbm_runtime_metrics()
        self.assertIs(metrics['used_gpu'], False)
        self.assertEqual(metrics['gpu_device_type'], '')

    def test_serialize_round_trip_keeps_used_gpu(self):
        metrics = backtest_tasks._default_lightgbm_runtime_metrics()
        metrics['used_gpu'] = True
        metrics['gpu_device_type'] = 'cuda'
        serialized = backtest_tasks._serialize_lightgbm_runtime_metrics(metrics)
        self.assertIs(serialized['used_gpu'], True)
        self.assertEqual(serialized['gpu_device_type'], 'cuda')
        restored = backtest_tasks._deserialize_lightgbm_runtime_metrics(serialized)
        self.assertIs(restored['used_gpu'], True)
        self.assertEqual(restored['gpu_device_type'], 'cuda')

    def test_deserialize_tolerates_legacy_rows_without_the_fields(self):
        restored = backtest_tasks._deserialize_lightgbm_runtime_metrics(
            {'inference_backend': 'cpu_serial', 'batch_size': 1}
        )
        self.assertIs(restored['used_gpu'], False)
        self.assertEqual(restored['gpu_device_type'], '')

    def test_setter_records_what_actually_ran(self):
        cache = {}
        backtest_tasks._set_lightgbm_runtime_gpu(cache, {'used_gpu': False, 'gpu_device_type': None})
        metrics = cache['lightgbm_runtime_metrics']
        self.assertIs(metrics['used_gpu'], False)
        self.assertEqual(metrics['gpu_device_type'], '')
        backtest_tasks._set_lightgbm_runtime_gpu(cache, {'used_gpu': True, 'gpu_device_type': 'cuda'})
        self.assertIs(cache['lightgbm_runtime_metrics']['used_gpu'], True)

    def test_setter_tolerates_a_missing_result(self):
        cache = {}
        backtest_tasks._set_lightgbm_runtime_gpu(cache, None)
        self.assertIs(cache['lightgbm_runtime_metrics']['used_gpu'], False)


class BackendParameterMigrationTests(TestCase):
    def test_persisted_parameters_are_renamed_and_reversible(self):
        run = BacktestRun.objects.create(
            name='gpu-legacy', strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date='2025-01-01', end_date='2025-03-01',
            parameters={'lightgbm_inference_backend': 'windows_gpu', 'horizon_days': 7,
                        'nested': {'lightgbm_inference_backend': 'windows_gpu'}},
        )
        migration.forwards(django_apps, None)
        run.refresh_from_db()
        self.assertEqual(run.parameters['lightgbm_inference_backend'], 'cuda')
        self.assertEqual(run.parameters['nested']['lightgbm_inference_backend'], 'cuda')
        self.assertEqual(run.parameters['horizon_days'], 7)

        migration.backwards(django_apps, None)
        run.refresh_from_db()
        self.assertEqual(run.parameters['lightgbm_inference_backend'], 'windows_gpu')

    def test_rows_without_the_key_are_untouched(self):
        run = BacktestRun.objects.create(
            name='cpu-only', strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date='2025-01-01', end_date='2025-03-01',
            parameters={'lightgbm_inference_backend': 'cpu_serial'},
        )
        migration.forwards(django_apps, None)
        run.refresh_from_db()
        self.assertEqual(run.parameters['lightgbm_inference_backend'], 'cpu_serial')
