"""Tests for the adjustment-drift census command.

Two behaviours carry the whole result and are worth pinning hard, because getting
either one wrong produced a confidently wrong number during development: coverage
must be dense (a thin sample reads as clean), and the call budget must actually cap
the calls (an "advisory" budget spent the daily sync's quota).
"""

import io
from datetime import date

from django.test import SimpleTestCase

from apps.catchup import registry
from apps.markets.management.commands.audit_adjustment_drift import (
    Command,
    downstream_series,
)


class DownstreamSeriesTests(SimpleTestCase):
    def test_reports_the_cascade_in_catch_up_order(self):
        downstream = downstream_series('ohlcv')
        self.assertIn('technical_indicators', downstream)
        self.assertIn('factor_scores', downstream)
        self.assertIn('predictions', downstream)
        self.assertNotIn('ohlcv', downstream)
        self.assertNotIn('trading_calendar', downstream)
        self.assertLess(downstream.index('technical_indicators'), downstream.index('factor_scores'))
        self.assertLess(downstream.index('factor_scores'), downstream.index('predictions'))

    def test_a_terminal_series_has_no_cascade(self):
        self.assertEqual(downstream_series('predictions'), [])

    def test_names_resolve_in_the_registry(self):
        for name in downstream_series('ohlcv'):
            self.assertIn(name, registry.BY_NAME)


class SampleSelectionTests(SimpleTestCase):
    def setUp(self):
        self.command = Command(stdout=io.StringIO(), stderr=io.StringIO())
        self.calendar = [date(2026, 1, day) for day in range(1, 29)]

    def select(self, anchors=(), max_dates=0, max_calls=1000):
        return self.command._select_sample(self.calendar, anchors, max_dates, max_calls)

    def test_coverage_is_contiguous_not_spaced(self):
        # The bug this guards: an evenly-spaced nine-date sample reported 5% on a
        # universe a dense pass put at 40%, because a seam is a step and every date
        # landed on one side of it.
        selected = self.select(max_dates=10)
        self.assertEqual(len(selected), 10)
        self.assertEqual(selected, self.calendar[-10:])
        gaps = [b - a for a, b in zip(selected, selected[1:])]
        self.assertTrue(all(gap.days == 1 for gap in gaps))

    def test_truncation_takes_from_the_front_so_the_recent_window_survives(self):
        selected = self.select(max_dates=5)
        self.assertEqual(selected[-1], self.calendar[-1])
        self.assertNotIn(self.calendar[0], selected)

    def test_the_call_budget_is_a_hard_cap(self):
        # 40 calls affords 20 dates and nothing more; otherwise a "budget" would
        # quietly spend the quota the 16:10 daily sync needs.
        selected = self.select(max_dates=0, max_calls=40)
        self.assertEqual(len(selected), 20)

    def test_a_budget_below_one_date_still_selects_one(self):
        self.assertEqual(len(self.select(max_calls=0)), 1)

    def test_anchors_inside_the_window_add_nothing(self):
        anchors = [self.calendar[-1], self.calendar[-5]]
        self.assertEqual(self.select(anchors=anchors, max_dates=10), self.calendar[-10:])

    def test_anchors_outside_the_covered_range_are_warned_about(self):
        far_away = date(2020, 1, 1)
        stdout = io.StringIO()
        command = Command(stdout=stdout, stderr=io.StringIO())
        command._select_sample(self.calendar, [far_away], 5, 1000)
        self.assertIn('last stored date before', stdout.getvalue())

    def test_a_window_shorter_than_the_request_returns_everything(self):
        selected = self.select(max_dates=100, max_calls=1000)
        self.assertEqual(selected, self.calendar)
