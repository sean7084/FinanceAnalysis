"""H1 / D8: _fit_lightgbm_horizon is a pure fit core — no DB or disk writes.

The walk-forward harness (H2) calls it directly and persists fold artifacts itself
(is_active=False). This pins the contract: calling the core writes nothing to the
registry, and returns the full fit structure. Mutation: adding a DB write inside
the core fails the zero-rows assertion.
"""
from datetime import date, timedelta

import pandas as pd
from django.test import TestCase

from apps.prediction.models_lightgbm import LightGBMModelArtifact
from apps.prediction.tasks_lightgbm import _fit_lightgbm_horizon


def _panel(n=300):
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
    return pd.DataFrame([
        {
            'date': d,
            'asset_id': 1,
            'f1': float(i % 7),
            'f2': float(i % 3),
            'label': ['DOWN', 'FLAT', 'UP'][i % 3],
            'fwd_return': 0.01 * (i % 5 - 2),
        }
        for i, d in enumerate(dates)
    ])


class FitCorePurityTests(TestCase):
    def test_fit_core_writes_no_registry_rows(self):
        before = LightGBMModelArtifact.objects.count()
        fit = _fit_lightgbm_horizon(_panel(), 7, ['f1', 'f2'])
        self.assertEqual(LightGBMModelArtifact.objects.count(), before)
        self.assertIsNotNone(fit['model'])
        self.assertIsNotNone(fit['scaler'])
        self.assertIsNotNone(fit['calibrator'])

    def test_fit_core_returns_full_structure(self):
        fit = _fit_lightgbm_horizon(_panel(), 7, ['f1', 'f2'])
        for key in (
            'model', 'scaler', 'calibrator', 'calibration_method',
            'train_accuracy', 'accuracy', 'evaluation_mode',
            'holdout_metrics', 'holdout_start', 'holdout_status',
            'lgb_params', 'training_samples',
        ):
            self.assertIn(key, fit)
        self.assertEqual(fit['evaluation_mode'], 'purged_holdout')
        self.assertGreater(fit['training_samples'], 0)

    def test_short_window_in_sample(self):
        fit = _fit_lightgbm_horizon(_panel(n=50), 30, ['f1', 'f2'])
        self.assertEqual(fit['evaluation_mode'], 'in_sample')
        self.assertEqual(fit['holdout_status'], 'unavailable_window_too_short')