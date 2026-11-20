"""H2 / D10: simulator-vs-engine NAV reconciliation and the rollback guarantee.

The comparison is pure (alignment, breach threshold, coverage gap); the engine run
is wrapped in a rolled-back transaction so a reconciliation persists nothing. The
rollback test writes a row inside the engine callable and asserts it is discarded.
"""
from datetime import date
from decimal import Decimal

import pandas as pd
from django.test import TestCase

from apps.prediction import engine_reconciliation as rec
from apps.prediction.models_lightgbm import LightGBMModelArtifact


class ReconciliationTests(TestCase):
    def test_nav_from_returns_compounds(self):
        nav = rec.nav_from_returns([0.1, 0.1])
        self.assertAlmostEqual(nav.iloc[0], 1.1)
        self.assertAlmostEqual(nav.iloc[1], 1.21)

    def test_identical_series_within_tolerance(self):
        dates = [date(2021, 1, 4), date(2021, 1, 5)]
        nav = pd.Series([1.0, 1.01], index=dates)
        report = rec.reconcile_nav(nav, nav.copy())
        self.assertTrue(report['within_tolerance'])
        self.assertEqual(report['max_abs_diff'], 0.0)
        self.assertEqual(report['n_compared'], 2)

    def test_breach_above_tolerance_detected(self):
        dates = [date(2021, 1, 4), date(2021, 1, 5)]
        sim = pd.Series([1.0, 1.01], index=dates)
        engine = pd.Series([1.0, 1.0102], index=dates)  # 2bp diff on day 2
        report = rec.reconcile_nav(sim, engine, tolerance=Decimal('0.0001'))
        self.assertFalse(report['within_tolerance'])
        self.assertEqual(len(report['breaches']), 1)
        self.assertEqual(report['breaches'][0]['date'], '2021-01-05')

    def test_aligns_by_date_only_common_compared(self):
        sim = pd.Series([1.0, 1.01, 1.02], index=[date(2021, 1, 4), date(2021, 1, 5), date(2021, 1, 6)])
        engine = pd.Series([1.0, 1.01], index=[date(2021, 1, 4), date(2021, 1, 5)])
        report = rec.reconcile_nav(sim, engine)
        self.assertEqual(report['n_compared'], 2)
        self.assertTrue(report['coverage_gap'])

    def test_empty_common_is_coverage_gap_not_pass(self):
        sim = pd.Series([1.0], index=[date(2021, 1, 4)])
        engine = pd.Series([1.0], index=[date(2021, 1, 5)])
        report = rec.reconcile_nav(sim, engine)
        self.assertFalse(report['within_tolerance'])
        self.assertTrue(report['coverage_gap'])
        self.assertEqual(report['n_compared'], 0)

    def test_engine_run_rolls_back_writes(self):
        def engine_fn():
            LightGBMModelArtifact.objects.create(
                horizon_days=3, version='recon-probe', is_active=False,
                status=LightGBMModelArtifact.Status.READY,
            )
            return pd.Series([1.0, 1.001], index=[date(2021, 1, 4), date(2021, 1, 5)])

        report = rec.run_engine_reconciliation([0.0, 0.001], engine_fn)
        self.assertTrue(report['within_tolerance'])
        # The row written inside the engine run must be discarded by the rollback.
        self.assertFalse(LightGBMModelArtifact.objects.filter(version='recon-probe').exists())

    def test_engine_run_reconciles_to_sim(self):
        def engine_fn():
            return pd.Series(rec.nav_from_returns([0.01, -0.005]).values,
                             index=[date(2021, 1, 4), date(2021, 1, 5)])

        report = rec.run_engine_reconciliation([0.01, -0.005], engine_fn)
        self.assertTrue(report['within_tolerance'])
        self.assertEqual(report['n_compared'], 2)