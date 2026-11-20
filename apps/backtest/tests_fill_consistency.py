"""STK-07 / #146: backtest and daily inference fill missing features identically.

The backtest paths used to hardcode features_dict.get(name, 0.0) and call the
extractor with no strategy (legacy default), while daily inference resolves the
artifact's native_nan strategy. These tests pin the shared precedence, the
strategy-aware assembly on both backtest paths, and the strict schema-mismatch
raise under native_nan (legacy artifacts keep the zero-fill).
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from django.test import TestCase

from apps.backtest import tasks as bt
from apps.prediction.models_lightgbm import LightGBMModelArtifact
from apps.prediction.tasks_lightgbm import _resolve_missing_value_strategy


class _CapturingScaler:
    def __init__(self):
        self.captured = None

    def transform(self, X):
        self.captured = np.array(X, dtype=float)
        return self.captured


def _artifact(strategy):
    metadata = {'missing_value_strategy': strategy} if strategy else {}
    return LightGBMModelArtifact.objects.create(
        horizon_days=7,
        version=f'fill-test-{strategy or "legacy"}',
        status=LightGBMModelArtifact.Status.READY,
        is_active=True,
        artifact_path='models/lightgbm/7d_fill-test',
        metadata=metadata,
        metrics_json={'accuracy': 0.49},
    )


def _patches(artifact, feature_names, features, scaler):
    loaded = {
        'metadata': {'feature_names': feature_names},
        'scaler': scaler,
        'model': object(),
        'calibrator': object(),
    }
    return [
        patch.object(bt, '_get_selected_lightgbm_artifact', return_value=artifact),
        patch.object(bt, '_load_model_artifacts', return_value=loaded),
        patch.object(bt, '_extract_features_for_asset', return_value=features),
        patch.object(
            bt, '_predict_lightgbm_probabilities_for_scaled_matrix',
            return_value={'calibrated_probabilities': [[0.2, 0.3, 0.5]], 'used_gpu': False},
        ),
        patch.object(bt, '_build_lightgbm_prediction_payload', return_value={}),
    ]


class StrategyPrecedenceTests(TestCase):
    def test_disk_metadata_wins_over_db(self):
        artifact = _artifact('legacy_neutral_fill')
        loaded = {'metadata': {'missing_value_strategy': 'native_nan'}}
        self.assertEqual(_resolve_missing_value_strategy(artifact, loaded), 'native_nan')

    def test_db_metadata_used_when_disk_absent(self):
        artifact = _artifact('native_nan')
        self.assertEqual(_resolve_missing_value_strategy(artifact, {'metadata': {}}), 'native_nan')

    def test_legacy_default_when_both_absent(self):
        artifact = _artifact(None)
        self.assertEqual(_resolve_missing_value_strategy(artifact, {'metadata': {}}), 'legacy_neutral_fill')

    def test_runtime_carries_strategy(self):
        artifact = _artifact('native_nan')
        scaler = _CapturingScaler()
        run = SimpleNamespace(parameters={})
        with patch.object(bt, '_get_selected_lightgbm_artifact', return_value=artifact), \
             patch.object(bt, '_load_model_artifacts', return_value={'metadata': {'feature_names': ['rsi']}, 'scaler': scaler}):
            runtime = bt._get_lightgbm_runtime(run, 7, {})
        self.assertEqual(runtime['missing_value_strategy'], 'native_nan')


class SerialFillTests(TestCase):
    def test_serial_native_nan_preserves_nan(self):
        artifact = _artifact('native_nan')
        scaler = _CapturingScaler()
        run = SimpleNamespace(parameters={})
        patches = _patches(artifact, ['rsi'], {'rsi': float('nan')}, scaler)
        for p in patches:
            p.start()
        try:
            bt._predict_lightgbm_for_asset(1, date(2020, 1, 2), 7, {}, run=run)
        finally:
            for p in patches:
                p.stop()
        self.assertTrue(np.isnan(scaler.captured[0][0]))

    def test_serial_legacy_zero_fills_missing(self):
        artifact = _artifact(None)
        scaler = _CapturingScaler()
        run = SimpleNamespace(parameters={})
        patches = _patches(artifact, ['rsi'], {}, scaler)
        for p in patches:
            p.start()
        try:
            bt._predict_lightgbm_for_asset(1, date(2020, 1, 2), 7, {}, run=run)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(scaler.captured[0][0], 0.0)

    def test_serial_native_nan_missing_column_raises(self):
        artifact = _artifact('native_nan')
        scaler = _CapturingScaler()
        run = SimpleNamespace(parameters={})
        patches = _patches(artifact, ['rsi', 'ghost'], {'rsi': 0.5}, scaler)
        for p in patches:
            p.start()
        try:
            with self.assertRaises(ValueError) as ctx:
                bt._predict_lightgbm_for_asset(1, date(2020, 1, 2), 7, {}, run=run)
            self.assertIn('ghost', str(ctx.exception))
        finally:
            for p in patches:
                p.stop()

    def test_serial_passes_strategy_to_extractor(self):
        artifact = _artifact('native_nan')
        scaler = _CapturingScaler()
        run = SimpleNamespace(parameters={})
        patches = _patches(artifact, ['rsi'], {'rsi': 0.5}, scaler)
        extractor_mock = None
        for index, p in enumerate(patches):
            started = p.start()
            if index == 2:
                extractor_mock = started
        try:
            bt._predict_lightgbm_for_asset(1, date(2020, 1, 2), 7, {}, run=run)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(extractor_mock.call_args.kwargs.get('missing_value_strategy'), 'native_nan')


class BatchedFillTests(TestCase):
    def test_batched_native_nan_preserves_nan(self):
        artifact = _artifact('native_nan')
        scaler = _CapturingScaler()
        run = SimpleNamespace(parameters={})
        patches = _patches(artifact, ['rsi'], {'rsi': float('nan')}, scaler)
        for p in patches:
            p.start()
        try:
            bt._predict_lightgbm_for_assets_batched([1], date(2020, 1, 2), 7, {}, run=run)
        finally:
            for p in patches:
                p.stop()
        self.assertTrue(np.isnan(scaler.captured[0][0]))

    def test_batched_legacy_zero_fills_missing(self):
        artifact = _artifact(None)
        scaler = _CapturingScaler()
        run = SimpleNamespace(parameters={})
        patches = _patches(artifact, ['rsi'], {}, scaler)
        for p in patches:
            p.start()
        try:
            bt._predict_lightgbm_for_assets_batched([1], date(2020, 1, 2), 7, {}, run=run)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(scaler.captured[0][0], 0.0)