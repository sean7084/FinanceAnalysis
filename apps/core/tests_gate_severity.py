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


class FactorScoreWatermarkTests(TestCase):
    """A session the factor pipeline has not reached yet is not a defect in the data.

    Measured 2026-11-08: the gate reported 1,497 criticals that were 499 assets x exactly the
    last three sessions -- every asset missing the same tail, because `FactorScore` stopped at
    2026-11-03. The gate compared against the calendar and had no notion of how far the
    pipeline had actually got, which is the same mistake as demanding OHLCV from 2010 for an
    asset listed in 2020. A gate that fires on the ordinary daily state gets ignored.
    """

    def setUp(self):
        from apps.factors.models import FactorScore

        market = Market.objects.create(code='CN', name='China')
        self.asset = Asset.objects.create(
            market=market, symbol='600010', ts_code='600010.SH', name='Watermark',
            list_date=date(2015, 1, 1),
        )
        self.watermark = date(2026, 11, 3)
        FactorScore.objects.create(
            asset=self.asset, date=self.watermark,
            mode=FactorScore.FactorMode.COMPOSITE,
        )
        self.command = Command()

    def _run(self, dates):
        writer = _Writer()
        self.command._validate_cross_tables(
            self.asset, dates, writer,
            defaultdict(int), defaultdict(int), defaultdict(int))
        return [row for _name, row in writer.details]

    def test_a_session_after_the_watermark_is_a_warning_with_a_reason(self):
        rows = self._run([self.watermark, date(2026, 11, 4)])
        lag = [row for row in rows if row['issue_type'] == 'missing_factor_score_pipeline_lag']
        self.assertEqual(len(lag), 1)
        self.assertEqual(lag[0]['severity'], 'warning')
        self.assertIn(str(self.watermark), lag[0]['details'])
        self.assertFalse([row for row in rows if row['issue_type'] == 'missing_factor_score'])

    def test_a_hole_before_the_watermark_stays_critical(self):
        """A gap in the middle of the series is a real defect and must not be excused."""
        rows = self._run([date(2026, 11, 2), self.watermark])
        criticals = [row for row in rows if row['issue_type'] == 'missing_factor_score']
        self.assertEqual([row['date'] for row in criticals], [date(2026, 11, 2)])
        self.assertEqual(criticals[0]['severity'], 'critical')

    def test_the_sessions_with_scores_are_not_reported_at_all(self):
        rows = self._run([self.watermark])
        self.assertEqual([row for row in rows if 'factor_score' in row['issue_type']], [])

    def test_the_watermark_is_computed_once_per_run(self):
        """One aggregate for the whole run, not one per asset."""
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        self.command._validate_cross_tables(
            self.asset, [date(2026, 11, 4)], _Writer(),
            defaultdict(int), defaultdict(int), defaultdict(int))
        with CaptureQueriesContext(connection) as captured:
            # A second asset's call must not re-run the aggregate.
            other = Asset.objects.create(
                market=self.asset.market, symbol='600011', ts_code='600011.SH',
                name='Other', list_date=date(2015, 1, 1),
            )
            self.command._validate_cross_tables(
                other, [date(2026, 11, 4)], _Writer(),
                defaultdict(int), defaultdict(int), defaultdict(int))
        aggregates = [q['sql'] for q in captured.captured_queries if 'MAX(' in q['sql'].upper()]
        self.assertEqual(aggregates, [])