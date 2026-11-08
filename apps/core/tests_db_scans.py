"""The windowed-scan helper and the gate's no-cursor invariant.

``validate_data_quality`` is the release gate and used to take ~58 minutes, because
every big scan streamed through ``.iterator(chunk_size=...)``. A chunked iterator makes
Django open a named server-side cursor and PostgreSQL will not use a parallel plan
through one: the same 5.7M-row query measured 86.1 s bulk against still unfinished at
432 s through a cursor. A gate that takes an hour gets skipped.

The source-scan and filter-pin tests below are the CI-enforceable guards. A wall-clock
budget is deliberately NOT asserted: CI's database has no realistic volume, so a
seconds budget would measure nothing. The measured evidence lives in
``apps/core/db_scans.py``'s docstring and in the PR.
"""

import inspect
from datetime import date, timedelta

from django.test import SimpleTestCase, TestCase

from apps.core.db_scans import DEFAULT_WINDOW_DAYS, date_windows, iter_windowed, windowed_rows
from apps.core.management.commands import validate_data_quality as vdq


class DateWindowsTests(SimpleTestCase):
    def test_windows_cover_the_range_exactly_once(self):
        start, end = date(2026, 1, 1), date(2026, 12, 31)
        windows = list(date_windows(start, end, window_days=90))
        self.assertEqual(windows[0][0], start)
        self.assertEqual(windows[-1][1], end)
        covered = []
        for lo, hi in windows:
            self.assertLessEqual(lo, hi)
            day = lo
            while day <= hi:
                covered.append(day)
                day += timedelta(days=1)
        self.assertEqual(len(covered), (end - start).days + 1)
        self.assertEqual(len(set(covered)), len(covered), 'a date was covered twice')
        self.assertEqual(covered, sorted(covered))

    def test_adjacent_windows_do_not_overlap(self):
        windows = list(date_windows(date(2026, 1, 1), date(2026, 12, 31), 30))
        for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
            self.assertEqual(next_start, prev_end + timedelta(days=1))

    def test_a_range_shorter_than_a_window_is_one_window(self):
        start, end = date(2026, 3, 1), date(2026, 3, 10)
        self.assertEqual(list(date_windows(start, end, 90)), [(start, end)])

    def test_a_single_day_range(self):
        day = date(2026, 3, 1)
        self.assertEqual(list(date_windows(day, day, 90)), [(day, day)])

    def test_an_inverted_range_yields_nothing(self):
        self.assertEqual(list(date_windows(date(2026, 3, 10), date(2026, 3, 1))), [])

    def test_a_zero_window_is_refused_rather_than_looping_forever(self):
        with self.assertRaises(ValueError):
            list(date_windows(date(2026, 1, 1), date(2026, 12, 31), 0))

    def test_the_default_window_is_a_quarter(self):
        self.assertEqual(DEFAULT_WINDOW_DAYS, 90)


class WindowedRowsTests(SimpleTestCase):
    def test_every_row_is_yielded_once_across_windows(self):
        start, end = date(2026, 1, 1), date(2026, 6, 30)
        expected = []
        day = start
        while day <= end:
            expected.append(day)
            day += timedelta(days=1)

        calls = []

        def fetch(lo, hi):
            calls.append((lo, hi))
            return [d for d in expected if lo <= d <= hi]

        got = [row for rows in windowed_rows(fetch, start, end, 30) for row in rows]
        self.assertEqual(got, expected)
        self.assertGreater(len(calls), 1, 'the range should have needed several windows')

    def test_only_one_window_list_is_live_at_a_time(self):
        """The memory bound: the previous list must be released before the next fetch."""
        import weakref

        class Sentinel:
            pass

        start, end = date(2026, 1, 1), date(2026, 12, 31)
        refs = []
        fetch_count = []

        def fetch(lo, hi):
            # A list cannot be weak-referenced, and windowed_rows copies it with list()
            # anyway, so track a sentinel carried inside it: when the window list dies,
            # the sentinel's refcount hits zero and the weakref clears.
            if fetch_count:
                self.assertIsNone(refs[-1](), 'the previous window list was still alive')
            fetch_count.append(1)
            sentinel = Sentinel()
            refs.append(weakref.ref(sentinel))
            return [sentinel, lo, hi]

        for rows in windowed_rows(fetch, start, end, 90):
            self.assertEqual(len(rows), 3)
            del rows
        self.assertGreater(len(fetch_count), 1)

    def test_iter_windowed_flattens_without_changing_order(self):
        start, end = date(2026, 1, 1), date(2026, 5, 5)
        fetch = lambda lo, hi: [lo, hi]  # noqa: E731
        flat = list(iter_windowed(fetch, start, end, 30))
        nested = [r for rows in windowed_rows(fetch, start, end, 30) for r in rows]
        self.assertEqual(flat, nested)

    def test_an_empty_range_fetches_nothing(self):
        calls = []

        def fetch(lo, hi):
            calls.append((lo, hi))
            return []

        self.assertEqual(list(windowed_rows(fetch, date(2026, 5, 5), date(2026, 1, 1))), [])
        self.assertEqual(calls, [])


def _code_of(func):
    """Source with comment lines stripped.

    The comments explain what the code used to do and why, so they necessarily contain
    the very strings these tests forbid. Scanning raw source would fail on the
    explanation rather than on a regression.
    """
    source = inspect.getsource(func)
    return '\n'.join(
        line for line in source.splitlines() if not line.lstrip().startswith('#')
    )


class GateHasNoServerSideCursorTests(SimpleTestCase):
    def test_the_coverage_scan_uses_no_chunked_iterator(self):
        source = _code_of(vdq.Command._write_effective_universe_daily_coverage)
        self.assertNotIn('.iterator(', source)
        self.assertIn('windowed_rows', source)
        self.assertIn('iter_windowed', source)

    def test_the_cross_section_audit_uses_no_chunked_iterator(self):
        self.assertNotIn('.iterator(', _code_of(vdq.Command._write_cross_section_audit))

    def test_the_capital_flow_scan_uses_no_chunked_iterator(self):
        self.assertNotIn('.iterator(', _code_of(vdq.Command._validate_capital_flow_null_fields))

    def test_the_rs_score_scan_is_narrowed_in_sql_not_in_python(self):
        """The single biggest win: ~140M matched rows reduced to the RS_SCORE subset.

        The old filter was indicator_type__in=technical_indicators and the loop then
        discarded everything that was not RS_SCORE -- about 96% of the largest scan in
        the command transferred for nothing.
        """
        source = _code_of(vdq.Command._write_effective_universe_daily_coverage)
        self.assertIn("indicator_type='RS_SCORE'", source)
        self.assertNotIn('indicator_type__in=technical_indicators', source)
        # The scan must still be conditional: a caller asking for other indicators only
        # should not get RS_SCORE bits it did not ask for.
        self.assertIn("if 'RS_SCORE' in technical_indicators:", source)

    def test_the_scans_are_not_wrapped_in_a_long_read_transaction(self):
        """The atomic() existed only to avoid a WITH HOLD cursor; it now pins xmin."""
        self.assertNotIn('with transaction.atomic():', _code_of(vdq.Command.handle))


class GateCoverageEquivalenceTests(TestCase):
    """The rewrite must not change what the gate reports, only how it fetches it."""

    def test_window_boundaries_do_not_duplicate_or_drop_a_date(self):
        from apps.markets.models import Asset, Market, OHLCV

        market = Market.objects.create(code='CN', name='China')
        asset = Asset.objects.create(market=market, symbol='600000', ts_code='600000.SH',
                                     name='A0', list_date=date(2015, 1, 1))
        start, end = date(2026, 1, 1), date(2026, 4, 15)
        days = []
        day = start
        while day <= end:
            days.append(day)
            day += timedelta(days=1)
        OHLCV.objects.bulk_create([
            OHLCV(asset=asset, date=d, open=1, high=1, low=1, close=1,
                  adj_close=1, volume=1, amount=1) for d in days
        ])

        # A window edge lands inside the range; every date must appear exactly once.
        fetched = [
            row_date
            for rows in windowed_rows(
                lambda lo, hi: OHLCV.objects.filter(
                    asset_id=asset.pk, date__gte=lo, date__lte=hi,
                ).order_by().values_list('date', 'asset_id'),
                start, end, window_days=30,
            )
            for row_date, _ in rows
        ]
        self.assertEqual(sorted(fetched), days)
        self.assertEqual(len(fetched), len(set(fetched)))