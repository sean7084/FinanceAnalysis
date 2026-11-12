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

    def _scans(self, *assets):
        """The bulk prefetch that the production loop now hands each asset's checks.

        Built through the real method rather than a hand-made dict, so these tests also
        prove the prefetch supplies exactly the sets the per-asset queries used to.
        """
        return self.command._prefetch_asset_scans(
            list(assets) or [self.asset], date(2026, 11, 1), date(2026, 11, 30), [],
        )

    def _run(self, dates):
        writer = _Writer()
        self.command._validate_cross_tables(
            self.asset, dates, writer,
            defaultdict(int), defaultdict(int), defaultdict(int),
            self._scans()[self.asset.id])
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
            defaultdict(int), defaultdict(int), defaultdict(int),
            self._scans()[self.asset.id])
        other = Asset.objects.create(
            market=self.asset.market, symbol='600011', ts_code='600011.SH',
            name='Other', list_date=date(2015, 1, 1),
        )
        other_scans = self._scans(other)[other.id]
        with CaptureQueriesContext(connection) as captured:
            # A second asset's call must not re-run the aggregate.
            self.command._validate_cross_tables(
                other, [date(2026, 11, 4)], _Writer(),
                defaultdict(int), defaultdict(int), defaultdict(int), other_scans)
        aggregates = [q['sql'] for q in captured.captured_queries if 'MAX(' in q['sql'].upper()]
        self.assertEqual(aggregates, [])


class TechnicalWindowAxisTests(TestCase):
    """Re-choosing which axis the window is cut on must not move a row across the edge.

    The per-asset technical window was filtered in Python on the raw instant until
    2026-11-12, when measuring 8,994,707 rows priced that one extra column at 5.4 s -- two
    thirds of the bulk read's whole penalty. The prefetch now carries ``timestamp__date``
    only, so the filter has no instant to work with. That is safe exactly because
    ``session_midnight`` bounds and the ``__date`` lookup both resolve through
    ``TIME_ZONE`` (``apps/core/date_floor.py``); if that ever stops holding, the detail CSVs
    change which rows are in the window while still looking well-formed -- the failure mode
    a release gate is worst at. So the window is computed twice, through SQL and through the
    prefetched dict, on instants sitting at either side of a day boundary.
    """

    def setUp(self):
        from datetime import datetime, time

        from django.utils import timezone

        from apps.analytics.models import TechnicalIndicator

        market = Market.objects.create(code='CN', name='China')
        self.asset = Asset.objects.create(
            market=market, symbol='600012', ts_code='600012.SH', name='Axis',
            list_date=date(2015, 1, 1),
        )
        self.days = [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)]
        # One row late in its own day, one just after the next midnight, one at open.
        offsets = [
            timedelta(seconds=1),
            timedelta(hours=23, minutes=59),
            timedelta(hours=9, minutes=30),
        ]
        for day, offset in zip(self.days, offsets):
            TechnicalIndicator.objects.create(
                asset=self.asset,
                indicator_type='RSI',
                value='50.0',
                parameters={},
                timestamp=timezone.make_aware(datetime.combine(day, time.min) + offset),
            )
        self.command = Command()
        self.indicators = ('RSI',)

    def _sql_window(self, first, last):
        from apps.analytics.models import TechnicalIndicator
        from apps.core.date_floor import session_midnight

        return sorted(
            TechnicalIndicator.objects.filter(
                asset=self.asset,
                timestamp__gte=session_midnight(first),
                timestamp__lt=session_midnight(last + timedelta(days=1)),
                indicator_type__in=self.indicators,
            ).values_list('timestamp__date', flat=True)
        )

    def _prefetched_window(self, first, last):
        scans = self.command._prefetch_asset_scans(
            [self.asset], self.days[0], self.days[-1], list(self.indicators)
        )[self.asset.id]
        return sorted(
            row['timestamp__date']
            for row in scans['technical_rows']
            if first <= row['timestamp__date'] <= last
        )

    def test_every_window_cut_agrees_between_the_two_axes(self):
        windows = [
            (self.days[0], self.days[0]),
            (self.days[0], self.days[1]),
            (self.days[1], self.days[1]),
            (self.days[1], self.days[2]),
            (self.days[0], self.days[2]),
        ]
        for first, last in windows:
            with self.subTest(window=(first, last)):
                self.assertEqual(self._sql_window(first, last), self._prefetched_window(first, last))

    def test_the_window_edges_fall_where_the_instants_sit(self):
        """A row at 23:59 belongs to its own day and a row at 00:00:01 to the next."""
        self.assertEqual(self._prefetched_window(self.days[0], self.days[0]), [self.days[0]])
        self.assertEqual(self._prefetched_window(self.days[1], self.days[1]), [self.days[1]])
        self.assertEqual(
            self._prefetched_window(self.days[0], self.days[1]), [self.days[0], self.days[1]]
        )

    def test_the_prefetch_does_not_carry_the_instant_column(self):
        """The column that cost 5.4 s must not come back because something re-added it.

        Guards the claim rather than the behaviour: no consumer needs the instant, and
        re-carrying it would silently put the penalty back.
        """
        scans = self.command._prefetch_asset_scans(
            [self.asset], self.days[0], self.days[-1], list(self.indicators)
        )[self.asset.id]
        self.assertTrue(scans['technical_rows'])
        self.assertNotIn('timestamp', scans['technical_rows'][0])
