"""The IC join must be date-aligned, and RULE must have a scorer to join.

Two defects are pinned here. ``block_rank_ic`` joined scores to labels on ``ts_code`` alone over
a multi-date block, which is many-to-many: a perfect per-date scorer measured 0.813 instead of
1.0, with the sample inflated by the number of dates. And the RULE arm had no predictor at all,
so its ``per_date_ic`` was empty and the pre-registered arm-minus-baseline paired leg could never
share a date with it.

Every existing ``block_rank_ic`` test used a single-date block, where the bad join is accidentally
correct -- which is why the multi-date case is the one that matters here.
"""
from datetime import date, timedelta

import pandas as pd
from django.test import TestCase

from apps.etf_rotation import rank_ic, rotation_engine, verdict_stats
from apps.etf_rotation.models import ETFFeatureSnapshot, ETFCategory
from apps.etf_rotation.tests import _PanelSeedMixin, _seed_etf


class MultiDateIcAlignmentTests(_PanelSeedMixin, TestCase):

    def setUp(self):
        self.seed()
        self.third = _seed_etf('510300.SH', name='沪深300ETF', category=ETFCategory.BROAD)
        self.fourth = _seed_etf('159934.SZ', name='黄金ETF', category=ETFCategory.COMMODITY)
        self.codes = ['512760.SH', '515980.SH', '510300.SH', '159934.SZ']
        self.etfs = [self.etf_a, self.etf_b, self.third, self.fourth]
        self.days = [self.pool_date + timedelta(days=i) for i in range(3)]
        self.labels = {}
        for i, day in enumerate(self.days):
            for j, (etf, code) in enumerate(zip(self.etfs, self.codes)):
                value = float(i + j)
                self.labels[(day, code)] = value
                ETFFeatureSnapshot.objects.create(
                    etf=etf, date=day, is_eligible=True, fwd_return=value,
                )

    def _perfect(self, frame):
        """A scorer that knows each row's own label -- the strongest possible signal."""
        return pd.DataFrame({
            'ts_code': list(frame['ts_code']),
            'score': [self.labels[(d, c)] for d, c in zip(frame['date'], frame['ts_code'])],
        })

    def test_a_perfect_scorer_over_three_dates_gives_ic_one_on_every_date(self):
        result = rank_ic.block_rank_ic(
            self._perfect, block_start=self.days[0], block_end=self.days[-1],
        )
        self.assertEqual(result['dates'], 3)
        self.assertEqual(len(result['per_date_ic']), 3)
        for day, value in result['per_date_ic'].items():
            self.assertAlmostEqual(value, 1.0, places=9, msg=f'{day} should be a perfect IC')
        self.assertAlmostEqual(result['mean_ic'], 1.0, places=9)

    def test_the_sample_is_not_inflated_by_the_number_of_dates(self):
        seen = {}

        def counting(frame):
            seen['rows'] = len(frame)
            return self._perfect(frame)

        rank_ic.block_rank_ic(counting, block_start=self.days[0], block_end=self.days[-1])
        self.assertEqual(seen['rows'], 12)
        result = rank_ic.block_rank_ic(
            self._perfect, block_start=self.days[0], block_end=self.days[-1],
        )
        self.assertEqual(result['dates'], 3)

    def test_a_scorer_that_drops_rows_is_refused(self):
        def dropper(frame):
            scored = self._perfect(frame)
            return scored.iloc[:-1]

        with self.assertRaisesMessage(ValueError, 'one row per input snapshot'):
            rank_ic.block_rank_ic(dropper, block_start=self.days[0], block_end=self.days[-1])

    def test_a_scorer_that_reorders_rows_is_refused(self):
        def reorder(frame):
            scored = self._perfect(frame)
            return scored.iloc[::-1].reset_index(drop=True)

        with self.assertRaisesMessage(ValueError, 'different order'):
            rank_ic.block_rank_ic(reorder, block_start=self.days[0], block_end=self.days[-1])

    def test_a_single_date_block_still_measures_the_same_thing(self):
        result = rank_ic.block_rank_ic(
            self._perfect, block_start=self.days[0], block_end=self.days[0],
        )
        self.assertEqual(result['dates'], 1)
        self.assertAlmostEqual(result['mean_ic'], 1.0, places=9)


class RulePredictorContractTests(TestCase):
    """The RULE scorer must be row-aligned and per-date, without touching execution."""

    def _frame(self):
        rows = []
        for i, day in enumerate(['2020-01-01', '2020-01-02']):
            for j, code in enumerate(['A', 'B', 'C', 'D']):
                rows.append({
                    'date': day, 'ts_code': code,
                    'mom_120d': float(j + i),
                    'rs_vs_benchmark_60d': float(4 - j + i),
                    'vol_20d': float(j % 3),
                })
        return pd.DataFrame(rows)

    def test_it_returns_one_row_per_snapshot_in_the_input_order(self):
        frame = self._frame()
        scored = rotation_engine.rule_predictor(120)(frame)
        self.assertEqual(list(scored.columns), ['ts_code', 'score'])
        self.assertEqual(len(scored), len(frame))
        self.assertEqual(list(scored['ts_code']), list(frame['ts_code']))

    def test_it_ranks_each_date_separately_not_the_pooled_frame(self):
        frame = self._frame()
        scored = rotation_engine.rule_predictor(120)(frame)
        for day, group in frame.groupby('date', sort=False):
            expected, _ = rotation_engine._rule_scores(group, 120)
            got = scored.loc[group.index, 'score'].to_numpy()
            want = group[['ts_code']].merge(expected, on='ts_code', how='left')['score'].to_numpy()
            self.assertEqual(list(got), list(want), msg=f'{day} must match a per-date cross-section')
        pooled, _ = rotation_engine._rule_scores(frame, 120)
        self.assertNotEqual(list(scored['score']), list(pooled['score']))

    def test_a_candidate_with_missing_inputs_keeps_its_row_as_nan(self):
        frame = self._frame()
        frame.loc[0, 'mom_120d'] = float('nan')
        scored = rotation_engine.rule_predictor(120)(frame)
        self.assertEqual(len(scored), len(frame))
        self.assertTrue(pd.isna(scored.loc[0, 'score']))
        self.assertFalse(pd.isna(scored.loc[1, 'score']))

    def test_a_frame_without_a_date_column_is_refused(self):
        frame = self._frame().drop(columns=['date'])
        with self.assertRaisesMessage(ValueError, 'date column'):
            rotation_engine.rule_predictor(120)(frame)

    def test_an_empty_frame_scores_empty(self):
        empty = pd.DataFrame({'date': [], 'ts_code': [], 'mom_120d': []})
        scored = rotation_engine.rule_predictor(120)(empty)
        self.assertEqual(len(scored), 0)

    def test_the_rule_scorer_feeds_a_date_aligned_ic(self):
        """End to end on synthetic rows: the scorer's own ranking must correlate with itself."""
        frame = self._frame()
        scorer = rotation_engine.rule_predictor(120)
        scored = scorer(frame)
        labels = frame[['date', 'ts_code']].copy()
        labels['fwd_return'] = scored['score'].to_numpy()
        merged = labels.merge(
            scored.assign(date=frame['date'].to_numpy()), on=['date', 'ts_code'], how='inner',
        )
        for _, group in merged.groupby('date'):
            value = group[['score', 'fwd_return']].corr(method='spearman').at['score', 'fwd_return']
            self.assertAlmostEqual(float(value), 1.0, places=9)


class RuleBaselinePairedLegTests(TestCase):

    def test_a_populated_baseline_shares_dates_with_the_arm(self):
        # MIN_OBSERVATIONS is 40 and a constant difference has no dispersion, so the series has
        # to be long enough and varied enough for a t to exist at all.
        days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(60)]
        arm = {d: 0.10 + 0.01 * (i % 7) for i, d in enumerate(days)}
        base = {d: 0.05 + 0.005 * (i % 5) for i, d in enumerate(days)}
        stats = verdict_stats.paired_ic_stats(arm, base)
        self.assertEqual(stats['shared_dates'], 60)
        self.assertEqual(stats['arm_only_dates'], 0)
        self.assertEqual(stats['baseline_only_dates'], 0)
        self.assertIsNotNone(stats['nw_t'])
        self.assertGreater(stats['mean_ic'], 0)

    def test_an_empty_baseline_is_what_the_old_rule_arm_produced(self):
        arm = {'2020-01-01': 0.10, '2020-01-02': 0.20}
        stats = verdict_stats.paired_ic_stats(arm, {})
        self.assertEqual(stats['shared_dates'], 0)
        self.assertIsNone(stats['nw_t'])