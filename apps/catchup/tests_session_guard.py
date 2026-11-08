"""The in-progress-session repair guard, and the ``catch_up`` command's own path.

Two defects are pinned here. ``--as-of`` was forwarded as a raw string, survived
``as_of = as_of or session_today()`` because a non-empty string is truthy, and then
raised ``TypeError`` at ``as_of - timedelta`` inside ``calendar_scan_start`` --
before a single detector ran. No test went through the command, so the path was
uncovered rather than working.

The second is that ``session_today()`` names the still-trading session from 08:00
Beijing, so every detector reports today as missing and the hourly :25 beat tick
spent TuShare quota repairing a date that cannot have a bar yet, recording a
permanent FAILED / PROVIDER_GAP row each time.
"""

from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.catchup import service
from apps.catchup.detectors import KIND_DATES, KIND_RANGE, CatchUpContext, SeriesGap
from apps.catchup.models import CatchUpRun
from apps.catchup.tests_service import AS_OF, LOC_MEM_CACHE, OPEN_DAYS
from apps.markets.models import ExchangeTradingCalendar

TODAY = OPEN_DAYS[-1]


def _ctx(as_of=TODAY, open_dates=None):
    return CatchUpContext(
        as_of=as_of,
        open_dates=tuple(OPEN_DAYS if open_dates is None else open_dates),
        window_start=OPEN_DAYS[0],
    )


class WithoutUnclosedSessionTests(TestCase):
    """Pure narrowing logic; the clock is pinned so the tests never depend on it."""

    def test_a_closed_session_leaves_the_gap_untouched(self):
        gap = SeriesGap(series='ohlcv', kind=KIND_DATES, missing_dates=(TODAY,))
        with patch('apps.catchup.service.session_has_closed', return_value=True):
            self.assertIs(service._without_unclosed_session(gap, _ctx()), gap)

    def test_a_gap_of_only_the_open_session_becomes_empty(self):
        gap = SeriesGap(series='ohlcv', kind=KIND_DATES, missing_dates=(TODAY,))
        with patch('apps.catchup.service.session_has_closed', return_value=False):
            narrowed = service._without_unclosed_session(gap, _ctx())
        self.assertTrue(narrowed.is_empty)
        self.assertEqual(narrowed.missing_dates, ())

    def test_a_real_hole_behind_the_open_session_survives(self):
        older = OPEN_DAYS[-3]
        gap = SeriesGap(series='ohlcv', kind=KIND_DATES, missing_dates=(older, TODAY))
        with patch('apps.catchup.service.session_has_closed', return_value=False):
            narrowed = service._without_unclosed_session(gap, _ctx())
        self.assertFalse(narrowed.is_empty)
        self.assertEqual(narrowed.missing_dates, (older,))
        self.assertEqual(narrowed.unit_count, 1)

    def test_a_gap_not_touching_the_open_session_is_unchanged(self):
        gap = SeriesGap(series='ohlcv', kind=KIND_DATES,
                        missing_dates=(OPEN_DAYS[-3], OPEN_DAYS[-2]))
        with patch('apps.catchup.service.session_has_closed', return_value=False):
            self.assertIs(service._without_unclosed_session(gap, _ctx()), gap)

    def test_a_range_gap_ending_today_shrinks_to_the_prior_open_date(self):
        gap = SeriesGap(series='suspensions', kind=KIND_RANGE,
                        window_start=OPEN_DAYS[0], window_end=TODAY)
        with patch('apps.catchup.service.session_has_closed', return_value=False):
            narrowed = service._without_unclosed_session(gap, _ctx())
        self.assertEqual(narrowed.window_end, OPEN_DAYS[-2])
        self.assertEqual(narrowed.window_start, OPEN_DAYS[0])
        self.assertFalse(narrowed.is_empty)

    def test_a_range_gap_covering_only_today_becomes_empty(self):
        gap = SeriesGap(series='suspensions', kind=KIND_RANGE,
                        window_start=TODAY, window_end=TODAY)
        with patch('apps.catchup.service.session_has_closed', return_value=False):
            narrowed = service._without_unclosed_session(gap, _ctx(open_dates=[TODAY]))
        self.assertTrue(narrowed.is_empty)


@override_settings(CACHES=LOC_MEM_CACHE, CATCHUP_LOOKBACK_TRADING_DAYS=10)
class OpenSessionRepairTests(TestCase):
    def setUp(self):
        cache.clear()
        open_days = set(OPEN_DAYS)
        day = AS_OF - timedelta(days=120)
        rows = []
        while day <= AS_OF:
            rows.append(ExchangeTradingCalendar(
                exchange_code='SSE', trade_date=day, is_open=day in open_days))
            day += timedelta(days=1)
        ExchangeTradingCalendar.objects.bulk_create(rows)

    def tearDown(self):
        cache.clear()

    def test_the_open_session_is_skipped_without_spending_quota(self):
        from apps.catchup.tests_detectors import DAILY_DISPATCHER, seed_periodic_task, watermark_at
        from apps.markets.models import Asset, Market, OHLCV

        market = Market.objects.create(code='CN', name='China')
        assets = [Asset.objects.create(market=market, symbol=f'60000{i}',
                                       ts_code=f'60000{i}.SH', name=f'A{i}',
                                       list_date=date(2015, 1, 1)) for i in range(4)]
        # Every open day has bars except today, which is still trading.
        OHLCV.objects.bulk_create([
            OHLCV(asset=asset, date=day, open=1, high=1, low=1, close=1,
                  adj_close=1, volume=1, amount=1)
            for asset in assets for day in OPEN_DAYS[:-1]
        ])
        seed_periodic_task(DAILY_DISPATCHER, watermark_at(AS_OF, 0))

        with patch('apps.catchup.service.session_has_closed', return_value=False), \
                patch('apps.markets.tasks.sync_daily_a_shares', return_value='synced') as sync, \
                patch('apps.catchup.repairs.call_command') as command:
            rows = service.run_catch_up(as_of=AS_OF, series=['ohlcv'],
                                        max_calls=500, max_seconds=600)
            first_count = CatchUpRun.objects.filter(series='ohlcv').count()
            # The hourly :25 tick repeats this all session. SKIPPED is deduped, so
            # the day's first row is evidence and every later tick adds nothing --
            # unlike FAILED, which inserts every time and is how 1,797 consecutive
            # news-backfill failures went unnoticed.
            service.run_catch_up(as_of=AS_OF, series=['ohlcv'],
                                 max_calls=500, max_seconds=600)

        row = next(r for r in rows if r.series == 'ohlcv')
        self.assertEqual(row.outcome, CatchUpRun.Outcome.SKIPPED)
        self.assertIn('has not closed trading', row.detail)
        self.assertEqual(sync.call_count, 0)
        self.assertEqual(command.call_count, 0)
        self.assertEqual(first_count, 1)
        self.assertEqual(CatchUpRun.objects.filter(series='ohlcv').count(), 1)

    def test_dry_run_still_reports_the_open_session_as_a_gap(self):
        """Detection is row-presence and stays honest; only repair is guarded."""
        from apps.catchup.tests_detectors import DAILY_DISPATCHER, seed_periodic_task, watermark_at
        from apps.markets.models import Asset, Market, OHLCV

        market = Market.objects.create(code='CN', name='China')
        asset = Asset.objects.create(market=market, symbol='600000',
                                     ts_code='600000.SH', name='A0',
                                     list_date=date(2015, 1, 1))
        OHLCV.objects.bulk_create([
            OHLCV(asset=asset, date=day, open=1, high=1, low=1, close=1,
                  adj_close=1, volume=1, amount=1) for day in OPEN_DAYS[:-1]
        ])
        seed_periodic_task(DAILY_DISPATCHER, watermark_at(AS_OF, 0))

        with patch('apps.catchup.service.session_has_closed', return_value=False), \
                patch('apps.markets.tasks.sync_daily_a_shares') as sync:
            rows = service.run_catch_up(as_of=AS_OF, series=['ohlcv'], dry_run=True,
                                        max_calls=500, max_seconds=600)
        row = next(r for r in rows if r.series == 'ohlcv')
        self.assertEqual(row.outcome, CatchUpRun.Outcome.SKIPPED)
        self.assertIn('dry run', row.detail)
        self.assertEqual(row.gap_end, TODAY)
        self.assertEqual(sync.call_count, 0)


@override_settings(CACHES=LOC_MEM_CACHE)
class CatchUpCommandTests(TestCase):
    """The command path itself -- every other test calls run_catch_up directly."""

    def test_as_of_is_parsed_into_a_date_before_the_detectors_run(self):
        with patch('apps.catchup.management.commands.catch_up.run_catch_up',
                   return_value=[]) as run:
            call_command('catch_up', '--as-of', '2026-10-30', '--dry-run',
                         stdout=StringIO())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs['as_of'], date(2026, 10, 30))

    def test_an_empty_as_of_defers_to_the_service(self):
        with patch('apps.catchup.management.commands.catch_up.run_catch_up',
                   return_value=[]) as run:
            call_command('catch_up', '--dry-run', stdout=StringIO())
        self.assertIsNone(run.call_args.kwargs['as_of'])

    def test_a_malformed_as_of_is_a_command_error_naming_the_flag(self):
        with patch('apps.catchup.management.commands.catch_up.run_catch_up') as run:
            with self.assertRaises(CommandError) as caught:
                call_command('catch_up', '--as-of', '30/10/2026', stdout=StringIO())
        self.assertIn('--as-of', str(caught.exception))
        self.assertIn('YYYY-MM-DD', str(caught.exception))
        self.assertEqual(run.call_count, 0)

    def test_an_impossible_date_is_rejected_not_coerced(self):
        with patch('apps.catchup.management.commands.catch_up.run_catch_up') as run:
            with self.assertRaises(CommandError):
                call_command('catch_up', '--as-of', '2026-02-30', stdout=StringIO())
        self.assertEqual(run.call_count, 0)