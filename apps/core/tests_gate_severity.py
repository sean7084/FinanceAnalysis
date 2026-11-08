"""The gate must not call an assumption a critical defect.

``validate_data_quality`` computes each asset's expected date range from ``list_date``.
When that is NULL the range starts at ``HISTORICAL_DATA_FLOOR`` (2010-01-01), so an asset
listed in 2020 produces a decade of "missing" rows that were never supposed to exist. A
bare run returned 5.3M criticals dominated by that, and a gate whose critical count is
mostly an artefact gets skipped -- which is what happened.

Downgrading it is not laxity: for an asset with a KNOWN listing date the window is real,
and an absent row is a genuine hole. The two cases are asserted together so the
distinction cannot quietly collapse in either direction.
"""

from collections import defaultdict
from datetime import date, timedelta

from django.test import TestCase

from apps.core.management.commands.validate_data_quality import Command
from apps.markets.models import Asset, Market


class _Writer:
    """Minimal stand-in: these tests only care about the counters."""

    def __init__(self):
        self.details = []

    def write_detail(self, name, row):
        self.details.append((name, row))

    def fieldnames_for(self, name):
        return []


class ContinuitySeverityTests(TestCase):
    def setUp(self):
        market = Market.objects.create(code='CN', name='China')
        self.unknown = Asset.objects.create(
            market=market, symbol='600001', ts_code='600001.SH', name='Unknown',
            list_date=None,
        )
        self.known = Asset.objects.create(
            market=market, symbol='600002', ts_code='600002.SH', name='Known',
            list_date=date(2015, 1, 1),
        )
        self.command = Command()

    def _run(self, asset):
        expected = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(5)]
        actual = {expected[0], expected[4]}
        counters = defaultdict(int)
        table_counters = defaultdict(int)
        self.command._validate_continuity(
            asset, expected, actual, _Writer(), counters, table_counters)
        return dict(counters), dict(table_counters)

    def test_an_unknown_listing_date_makes_missing_ohlcv_a_warning(self):
        counters, table_counters = self._run(self.unknown)
        self.assertEqual(counters.get(('missing_ohlcv_unknown_list_date', 'warning')), 3)
        self.assertNotIn(('missing_ohlcv', 'critical'), counters)
        self.assertEqual(
            table_counters.get(('ohlcv', 'warning', 'missing_ohlcv_unknown_list_date')), 3)

    def test_a_known_listing_date_still_makes_missing_ohlcv_critical(self):
        counters, table_counters = self._run(self.known)
        self.assertEqual(counters.get(('missing_ohlcv', 'critical')), 3)
        self.assertNotIn(('missing_ohlcv_unknown_list_date', 'warning'), counters)
        self.assertEqual(table_counters.get(('ohlcv', 'critical', 'missing_ohlcv')), 3)

    def test_the_downgrade_does_not_change_what_is_reported_as_missing(self):
        """Only the severity moves; the gap rows and their dates are identical."""
        for asset in (self.unknown, self.known):
            writer = _Writer()
            expected = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(5)]
            self.command._validate_continuity(
                asset, expected, {expected[0], expected[4]}, writer,
                defaultdict(int), defaultdict(int))
            self.assertEqual(len(writer.details), 1)
            name, row = writer.details[0]
            self.assertEqual(name, 'ohlcv_continuity_gaps')
            self.assertEqual(row['expected_count'], 5)