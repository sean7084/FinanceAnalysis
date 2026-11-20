"""UNI-01/02: CSI1000 acceptance and the full-page truncation defense (issue #196)."""
from datetime import date
from types import SimpleNamespace

import pandas as pd
from django.test import SimpleTestCase

from apps.markets.tasks import (
    INDEX_CODE_SPECS,
    INDEX_WEIGHT_PAGE_LIMIT,
    _fetch_index_weight_frames,
    _membership_continuity_violations,
    _parse_index_codes,
)


def _frame(n):
    return pd.DataFrame({
        'con_code': ['000001.SZ'] * n,
        'trade_date': ['20200106'] * n,
        'weight': [1.0] * n,
    })


class IndexAcceptanceTests(SimpleTestCase):
    def test_csi1000_in_specs(self):
        self.assertEqual(INDEX_CODE_SPECS['000852.SH']['tag'], 'CSI1000')

    def test_parse_accepts_csi1000(self):
        self.assertIn('000852.SH', _parse_index_codes('000852.SH'))


class FullPageBisectTests(SimpleTestCase):
    def test_full_page_bisects_window(self):
        calls = []

        def index_weight(index_code, start_date, end_date):
            calls.append((start_date, end_date))
            if start_date == '20200101' and end_date == '20200110':
                return _frame(INDEX_WEIGHT_PAGE_LIMIT)
            return _frame(3500)

        pro = SimpleNamespace(index_weight=index_weight)
        frames = _fetch_index_weight_frames(pro, '000852.SH', date(2020, 1, 1), date(2020, 1, 10))
        self.assertEqual(len(frames), 2)
        self.assertEqual(sum(len(f) for f in frames), 7000)
        self.assertEqual(len(calls), 3)

    def test_single_day_full_page_raises(self):
        pro = SimpleNamespace(index_weight=lambda **kwargs: _frame(INDEX_WEIGHT_PAGE_LIMIT))
        with self.assertRaises(ValueError) as ctx:
            _fetch_index_weight_frames(pro, '000852.SH', date(2020, 1, 6), date(2020, 1, 6))
        self.assertIn('page cap', str(ctx.exception))

    def test_below_cap_returns_single_frame(self):
        pro = SimpleNamespace(index_weight=lambda **kwargs: _frame(1000))
        frames = _fetch_index_weight_frames(pro, '000852.SH', date(2020, 1, 1), date(2020, 1, 10))
        self.assertEqual(len(frames), 1)
        self.assertEqual(len(frames[0]), 1000)


class ContinuityAnalysisTests(SimpleTestCase):
    def test_missing_month_flagged(self):
        counts = {date(2020, 1, 31): 1000, date(2020, 3, 31): 1000}
        violations = _membership_continuity_violations(
            counts, start_date=date(2020, 1, 1), end_date=date(2020, 3, 31), band=(950, 1050),
        )
        self.assertEqual(len(violations), 1)
        self.assertIn('2020-02', violations[0])

    def test_thin_snapshot_flagged(self):
        counts = {date(2020, 1, 31): 900, date(2020, 2, 29): 1000}
        violations = _membership_continuity_violations(
            counts, start_date=date(2020, 1, 1), end_date=date(2020, 2, 29), band=(950, 1050),
        )
        self.assertEqual(len(violations), 1)
        self.assertIn('900', violations[0])

    def test_clean_history_passes(self):
        counts = {date(2020, 1, 31): 1000, date(2020, 2, 29): 1000}
        self.assertEqual(
            _membership_continuity_violations(
                counts, start_date=date(2020, 1, 1), end_date=date(2020, 2, 29), band=(950, 1050),
            ),
            [],
        )

    def test_multiple_snapshots_per_month_ok(self):
        counts = {date(2020, 1, 10): 1000, date(2020, 1, 31): 1000, date(2020, 2, 29): 1000}
        self.assertEqual(
            _membership_continuity_violations(
                counts, start_date=date(2020, 1, 1), end_date=date(2020, 2, 29), band=(950, 1050),
            ),
            [],
        )