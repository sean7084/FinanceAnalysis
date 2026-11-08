"""The stock LightGBM purged holdout and its out-of-sample metrics.

Before this, ``train_lightgbm_models`` fit on the whole aligned window and then computed
``accuracy = mean(pred == y_train)`` -- a pure in-sample number. The 7-day horizon reports
0.4874, below a coin flip, and that figure cannot distinguish sampling noise around 0.5
from a model ranking outcomes in reverse. Those imply opposite responses, so the split and
the t-statistic exist together: one produces out-of-sample data, the other says whether it
is distinguishable from zero.

The purge is the load-bearing part of the split. A sample dated one session before the
holdout has a label built from prices *inside* the holdout, so without dropping the last
``horizon`` training sessions the 30-day model would have seen up to 30 sessions of its own
future.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from apps.prediction.activation import ACCURACY_KEYS, IC_KEYS, activation_violations
from apps.prediction.tasks_lightgbm import (
    HOLDOUT_SESSIONS,
    _holdout_metrics,
    _purged_holdout_split,
    _rankdata,
    _spearman,
)


def _frame(sessions=400, assets=3, start=date(2016, 6, 1)):
    rows = []
    day = start
    made = 0
    while made < sessions:
        for asset in range(assets):
            rows.append({'date': day, 'asset_id': asset})
        day += timedelta(days=1)
        made += 1
    return pd.DataFrame(rows)


class PurgedHoldoutSplitTests(SimpleTestCase):
    def test_the_purge_removes_the_last_horizon_sessions_from_training(self):
        frame = _frame(sessions=400)
        for horizon in (3, 7, 30):
            with self.subTest(horizon=horizon):
                split = _purged_holdout_split(frame, horizon)
                self.assertIsNotNone(split)
                train, holdout, holdout_start = split
                dates = sorted(frame['date'].unique())
                self.assertEqual(holdout_start, dates[len(dates) - HOLDOUT_SESSIONS])
                # No training row may fall within `horizon` sessions of the holdout.
                self.assertLess(train['date'].max(), dates[len(dates) - HOLDOUT_SESSIONS - horizon])
                self.assertGreaterEqual(holdout['date'].min(), holdout_start)

    def test_train_and_holdout_never_overlap(self):
        frame = _frame(sessions=400)
        train, holdout, _ = _purged_holdout_split(frame, 7)
        self.assertEqual(len(set(train['date']) & set(holdout['date'])), 0)

    def test_a_31_day_horizon_purges_more_than_a_7_day_horizon(self):
        frame = _frame(sessions=400)
        train_7, _, _ = _purged_holdout_split(frame, 7)
        train_30, _, _ = _purged_holdout_split(frame, 30)
        self.assertGreater(len(train_7), len(train_30))

    def test_too_short_a_window_returns_none_rather_than_an_empty_split(self):
        frame = _frame(sessions=HOLDOUT_SESSIONS + 10)
        self.assertIsNone(_purged_holdout_split(frame, 30))
        frame = _frame(sessions=100)
        self.assertIsNone(_purged_holdout_split(frame, 7))

    def test_it_is_deterministic(self):
        frame = _frame(sessions=400)
        first = _purged_holdout_split(frame, 7)
        second = _purged_holdout_split(frame, 7)
        self.assertEqual(first[2], second[2])
        self.assertEqual(len(first[0]), len(second[0]))

    def test_the_holdout_is_contiguous_and_the_last_part_of_the_window(self):
        frame = _frame(sessions=400)
        _, holdout, holdout_start = _purged_holdout_split(frame, 7)
        dates = sorted(frame['date'].unique())
        self.assertEqual(sorted(holdout['date'].unique()),
                         dates[len(dates) - HOLDOUT_SESSIONS:])


class RankHelpersTests(SimpleTestCase):
    def test_ranks_are_one_based_and_average_ties(self):
        self.assertEqual(list(_rankdata(np.array([10.0, 20.0, 30.0]))), [1.0, 2.0, 3.0])
        self.assertEqual(list(_rankdata(np.array([5.0, 5.0, 1.0]))), [2.5, 2.5, 1.0])

    def test_perfect_and_inverted_correlation(self):
        ascending = np.arange(10, dtype=float)
        self.assertAlmostEqual(_spearman(ascending, ascending), 1.0, places=12)
        self.assertAlmostEqual(_spearman(ascending, -ascending), -1.0, places=12)

    def test_a_constant_series_has_no_rank_correlation(self):
        self.assertIsNone(_spearman(np.arange(10, dtype=float), np.zeros(10)))

    def test_too_few_points_is_undecided(self):
        self.assertIsNone(_spearman(np.array([1.0, 2.0]), np.array([1.0, 2.0])))


class HoldoutMetricsTests(SimpleTestCase):
    """The decisive test: noise and inversion must be distinguishable."""

    @staticmethod
    def _proba(score):
        """Build a 3-class proba whose P(UP)-P(DOWN) is the normalised score."""
        score = np.asarray(score, dtype=float)
        top = np.abs(score).max() or 1.0
        score = score / top
        proba = np.zeros((len(score), 3))
        proba[:, 2] = np.clip(score, 0, None)
        proba[:, 0] = np.clip(-score, 0, None)
        proba[:, 1] = 1.0 - np.abs(score)
        return proba

    @staticmethod
    def _panel(days, per_day, correlate):
        """Build (dates, returns, score) where ``correlate`` maps return -> score."""
        rng = np.random.default_rng(11)
        dates, returns, score = [], [], []
        for offset in range(days):
            day = date(2026, 1, 1) + timedelta(days=offset)
            day_returns = rng.normal(0, 0.02, per_day)
            dates.extend([day] * per_day)
            returns.extend(day_returns)
            score.extend(correlate(day_returns, rng))
        return dates, np.asarray(returns), np.asarray(score)

    def test_a_perfectly_ordered_score_has_ic_one(self):
        dates = [date(2026, 1, 1)] * 50
        returns = np.linspace(-0.05, 0.05, 50)
        score = np.linspace(-0.9, 0.9, 50)
        metrics = _holdout_metrics(
            np.zeros(50), self._proba(score), dates, list(returns))
        self.assertAlmostEqual(metrics['rank_ic'], 1.0, places=9)

    def test_an_inverted_panel_has_a_significantly_negative_tstat(self):
        """An inverted model is the failure mode a bare 0.487 accuracy cannot name."""
        dates, returns, score = self._panel(
            30, 20, lambda r, rng: -r + rng.normal(0, 0.002, len(r)))
        metrics = _holdout_metrics(
            np.zeros(len(returns)), self._proba(score), dates, list(returns))
        self.assertLess(metrics['rank_ic'], -0.5)
        self.assertLess(metrics['rank_ic_tstat'], -2)
        self.assertLess(metrics['icir'], 0)

    def test_an_independent_panel_is_indistinguishable_from_noise(self):
        dates, returns, score = self._panel(
            30, 20, lambda r, rng: rng.normal(0, 0.02, len(r)))
        metrics = _holdout_metrics(
            np.zeros(len(returns)), self._proba(score), dates, list(returns))
        self.assertLess(abs(metrics['rank_ic']), 0.2)
        self.assertLess(abs(metrics['rank_ic_tstat']), 3)

    def test_ic_is_computed_per_session_not_pooled(self):
        """A shared daily level shift must not be read as cross-sectional skill.

        Day two's returns and scores are both shifted far above day one's. Pooling would
        let that offset dominate; per-date correlation cancels it out.
        """
        first = [date(2026, 1, 5)] * 20
        second = [date(2026, 1, 6)] * 20
        returns = list(np.linspace(-0.02, 0.02, 20)) + list(np.linspace(-0.02, 0.02, 20) + 5.0)
        score = list(np.linspace(-0.9, 0.9, 20)) + list(np.linspace(-0.9, 0.9, 20) + 50.0)
        metrics = _holdout_metrics(
            np.zeros(40), self._proba(score), first + second, returns)
        self.assertGreater(metrics['rank_ic'], 0.9)
        self.assertEqual(metrics['rank_ic_dates'], 2)

    def test_accuracy_is_the_out_of_sample_number(self):
        proba = np.array([[0.1, 0.2, 0.7], [0.7, 0.2, 0.1]])
        y_true = np.array([2, 0])
        metrics = _holdout_metrics(y_true, proba, [date(2026, 1, 5)] * 2, [0.03, -0.03])
        self.assertEqual(metrics['accuracy'], 1.0)

    def test_it_reports_how_many_sessions_the_ic_used(self):
        dates = [date(2026, 1, 5)] * 10 + [date(2026, 1, 6)] * 10
        metrics = _holdout_metrics(
            np.zeros(20), self._proba(np.linspace(0.8, -0.8, 20)),
            dates, list(np.linspace(-0.1, 0.1, 20)))
        self.assertEqual(metrics['holdout_dates'], 2)
        self.assertEqual(metrics['holdout_samples'], 20)
        self.assertEqual(metrics['rank_ic_dates'], 2)


class ActivationInteractionTests(SimpleTestCase):
    def test_the_new_keys_are_bounded(self):
        self.assertIn('test_accuracy', ACCURACY_KEYS)
        self.assertIn('rank_ic', IC_KEYS)
        # train_accuracy is deliberately NOT bounded: the honest in-sample/holdout gap
        # should be visible, not refused.
        self.assertNotIn('train_accuracy', ACCURACY_KEYS)
        self.assertTrue(activation_violations({'test_accuracy': 0.95}))
        self.assertTrue(activation_violations({'rank_ic': 0.41}))
        self.assertFalse(activation_violations({'accuracy': 0.49, 'rank_ic': 0.03}))