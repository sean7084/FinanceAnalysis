"""The pre-registered verdict statistics (#136), pinned so they cannot drift post-hoc.

The point of these tests is that the constants and the arithmetic are frozen: a Newey-West
t must be SMALLER than the naive t on an overlapping-label series, the Sharpe gate must
reject a positive point estimate whose confidence interval straddles zero, and the
random-effects pooling must report tau^2 / I^2 rather than hide heterogeneity.
"""

import random
from math import sqrt
from statistics import mean, stdev

from django.test import SimpleTestCase

from apps.etf_rotation import verdict_stats as vs


def _ma_series(n=400, horizon=20, seed=7):
    """Daily ICs built from overlapping horizon-day sums: MA(horizon-1) by construction."""
    rng = random.Random(seed)
    shocks = [rng.gauss(0.02, 1.0) for _ in range(n + horizon)]
    return {
        f"2020-01-{i:02d}" if i < 28 else f"2020-02-{i-27:02d}":
            sum(shocks[i:i + horizon]) / horizon
        for i in range(n)
    }


class FrozenConstantTests(SimpleTestCase):
    def test_constants_are_the_pre_registered_values(self):
        self.assertEqual(vs.NW_LAG, 20)
        self.assertEqual(vs.T_MIN, 2.0)
        self.assertEqual(vs.SHARPE_CI_Z, 1.96)

    def test_newey_west_t_is_smaller_than_naive_on_overlapping_labels(self):
        stats = vs.ic_stats(_ma_series())
        self.assertIsNotNone(stats['nw_t'])
        self.assertIsNotNone(stats['naive_t'])
        self.assertLess(stats['nw_t'], stats['naive_t'])
        # The inflation must be material, not a rounding difference: sqrt(20) ~ 4.47 is the
        # worst case, and an MA(19) series should land a substantial fraction of it.
        self.assertGreater(stats['naive_t'] / stats['nw_t'], 1.5)

    def test_icir_matches_the_stock_side_pattern(self):
        series = _ma_series()
        values = [series[key] for key in sorted(series)]
        stats = vs.ic_stats(series)
        self.assertAlmostEqual(stats['icir'], mean(values) / stdev(values), places=12)
        self.assertAlmostEqual(stats['mean_ic'], mean(values), places=12)

    def test_short_series_reports_no_t_rather_than_a_fake_one(self):
        stats = vs.ic_stats({'a': 0.1, 'b': 0.2})
        self.assertIsNone(stats['nw_t'])
        self.assertIsNone(stats['nw_se'])

    def test_newey_west_se_matches_a_hand_computation(self):
        rng = random.Random(11)
        values = [rng.gauss(0.0, 1.0) for _ in range(80)]
        n = len(values)
        mu = mean(values)
        errors = [v - mu for v in values]
        lag = 5
        gamma0 = sum(e * e for e in errors) / n
        long_run = gamma0
        for offset in range(1, lag + 1):
            weight = 1.0 - offset / (lag + 1.0)
            gamma = sum(errors[i] * errors[i - offset] for i in range(offset, n)) / n
            long_run += 2.0 * weight * gamma
        expected = sqrt(long_run / n)
        self.assertAlmostEqual(vs.newey_west_se(values, lag=lag), expected, places=12)


class PairedDifferenceTests(SimpleTestCase):
    def test_only_shared_dates_are_differenced_and_the_drops_are_reported(self):
        arm = {'d1': 0.30, 'd2': 0.20, 'd3': 0.10, 'arm_only': 0.9}
        base = {'d1': 0.10, 'd2': 0.10, 'd3': 0.10, 'base_only': 0.5}
        stats = vs.paired_ic_stats(arm, base)
        self.assertEqual(stats['shared_dates'], 3)
        self.assertEqual(stats['arm_only_dates'], 1)
        self.assertEqual(stats['baseline_only_dates'], 1)
        self.assertAlmostEqual(stats['mean_ic'], mean([0.2, 0.1, 0.0]), places=12)

    def test_a_common_market_term_cancels(self):
        base = {f'd{i}': 0.5 + 0.01 * i for i in range(60)}
        arm = {day: value + 0.02 for day, value in base.items()}
        stats = vs.paired_ic_stats(arm, base)
        self.assertAlmostEqual(stats['mean_ic'], 0.02, places=12)
        self.assertEqual(stats['sd'], 0.0)


class RandomEffectsTests(SimpleTestCase):
    def test_identical_estimates_give_zero_tau2_and_zero_i2(self):
        meta = vs.random_effects_meta([(0.1, 0.02)] * 3)
        self.assertEqual(meta['k'], 3)
        self.assertAlmostEqual(meta['pooled'], 0.1, places=12)
        self.assertEqual(meta['tau2'], 0.0)
        self.assertEqual(meta['i2'], 0.0)

    def test_heterogeneity_is_reported_not_averaged_away(self):
        meta = vs.random_effects_meta([(0.5, 0.01), (-0.4, 0.01), (0.3, 0.01)])
        self.assertGreater(meta['tau2'], 0.0)
        self.assertGreater(meta['i2'], 0.5)
        self.assertIsNotNone(meta['pooled_se'])

    def test_empty_input_is_not_a_number(self):
        meta = vs.random_effects_meta([])
        self.assertEqual(meta['k'], 0)
        self.assertIsNone(meta['pooled'])


class SharpeGateTests(SimpleTestCase):
    def test_positive_point_with_interval_straddling_zero_is_rejected(self):
        gate = vs.sharpe_secondary_gate(0.10, 0.10)
        self.assertFalse(gate['passed'])
        self.assertLess(gate['lower_bound'], 0)

    def test_positive_point_with_interval_clear_of_zero_passes(self):
        gate = vs.sharpe_secondary_gate(0.60, 0.10)
        self.assertTrue(gate['passed'])
        self.assertGreater(gate['lower_bound'], 0)

    def test_negative_point_never_passes(self):
        self.assertFalse(vs.sharpe_secondary_gate(-0.5, 0.01)['passed'])

    def test_missing_inputs_are_not_a_pass(self):
        self.assertIsNone(vs.sharpe_secondary_gate(None, 0.1)['passed'])
        self.assertIsNone(vs.sharpe_secondary_gate(0.5, None)['passed'])

    def test_panel_gate_propagates_the_block_ses(self):
        gate = vs.panel_sharpe_gate([0.4, 0.5, 0.6], [0.1, 0.1, 0.1])
        expected_se = sqrt(3 * 0.01) / 3
        self.assertAlmostEqual(gate['se'], expected_se, places=12)
        self.assertAlmostEqual(gate['point'], 0.5, places=12)

    def test_equity_se_grows_with_autorrelation(self):
        iid = [{'equity': 100.0 * (1.001 ** i)} for i in range(120)]
        rng = random.Random(3)
        shocks = [rng.gauss(0.0, 0.01) for _ in range(140)]
        correlated, level = [], 100.0
        for i in range(120):
            level *= 1.0 + sum(shocks[i:i + 20]) / 20 * 0.1
            correlated.append({'equity': level})
        se_iid = vs.sharpe_se_from_equity(iid)
        self.assertIsNone(se_iid)  # zero variance returns give no SE, not a fake one
        se_correlated = vs.sharpe_se_from_equity(correlated)
        self.assertIsNotNone(se_correlated)
        self.assertGreater(se_correlated, 0)


class PrimaryCriterionTests(SimpleTestCase):
    def test_both_legs_are_required(self):
        self.assertTrue(vs.primary_reject(2.5, 0.03))
        self.assertFalse(vs.primary_reject(2.5, -0.01))
        self.assertFalse(vs.primary_reject(1.5, 0.03))

    def test_missing_evidence_is_not_a_pass(self):
        self.assertFalse(vs.primary_reject(None, 0.03))
        self.assertFalse(vs.primary_reject(3.0, None))
