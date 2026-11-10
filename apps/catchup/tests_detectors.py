"""Detector tests. Detectors are pure DB queries, so nothing here mocks TuShare."""

from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone

from django.test import TestCase
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from unittest.mock import patch

from apps.catchup import detectors
from apps.catchup.detectors import CatchUpContext, cross_sectional_gap_dates
from apps.factors.models import CapitalFlowSnapshot
from apps.macro.models import MacroSnapshot
from apps.markets.calendar_utils import open_trading_dates
from apps.markets.models import (
    Asset,
    BenchmarkIndexDaily,
    ExchangeTradingCalendar,
    Market,
    OHLCV,
)

AS_OF = date(2026, 10, 30)
OPEN_DAYS = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28),
             date(2026, 10, 29), date(2026, 10, 30)]
CLOSED_DAY = date(2026, 10, 24)
DAILY_DISPATCHER = 'sync-a-shares-daily-from-tushare'


def watermark_at(as_of, days_ago):
    """A beat watermark anchored to ``as_of``, never to the real clock.

    Tests assert on exact window bounds, so seeding from ``timezone.now()`` would
    make them drift with whatever day the suite happens to run on.
    """
    return datetime.combine(as_of - timedelta(days=days_ago), time(12),
                            tzinfo=dt_timezone.utc)


def seed_periodic_task(name, last_run_at, enabled=True):
    """Set a beat watermark without disturbing the row's existing schedule.

    A migration seeds some entries with a crontab already attached and
    ``PeriodicTask`` rejects a row carrying two schedule types, so only attach one
    when creating.
    """
    task = PeriodicTask.objects.filter(name=name).first()
    if task is None:
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute='10', hour='*', day_of_week='*', day_of_month='*',
            month_of_year='*', timezone='UTC',
        )
        task = PeriodicTask(name=name, task=f'apps.some.tasks.{name}', crontab=schedule)
    task.last_run_at = last_run_at
    task.enabled = enabled
    task.save()
    return task


class CalendarSeededTests(TestCase):
    def setUp(self):
        ExchangeTradingCalendar.objects.bulk_create([
            ExchangeTradingCalendar(exchange_code='SSE', trade_date=day, is_open=True)
            for day in OPEN_DAYS
        ] + [
            ExchangeTradingCalendar(exchange_code='SSE', trade_date=CLOSED_DAY, is_open=False),
        ])
        self.market = Market.objects.create(code='CN', name='China')

    def ctx(self, open_dates=None, window_start=None):
        return CatchUpContext(
            as_of=AS_OF,
            open_dates=tuple(OPEN_DAYS if open_dates is None else open_dates),
            window_start=window_start or (open_dates or OPEN_DAYS)[0],
        )

    def make_asset(self, symbol):
        return Asset.objects.create(
            market=self.market, symbol=symbol, ts_code=f'{symbol}.SZ', name=symbol,
            list_date=date(2015, 1, 1),
        )

    def fill_calendar_range(self, start, end):
        day = start
        while day <= end:
            ExchangeTradingCalendar.objects.get_or_create(
                exchange_code='SSE', trade_date=day,
                defaults={'is_open': day.weekday() < 5})
            day += timedelta(days=1)


class OpenTradingDateTests(CalendarSeededTests):
    def test_open_dates_are_oldest_first(self):
        # Oldest-first is what makes "repair the oldest hole first" work at all.
        self.assertEqual(open_trading_dates(AS_OF, 5), OPEN_DAYS)

    def test_closed_days_are_excluded(self):
        self.assertNotIn(CLOSED_DAY, open_trading_dates(AS_OF, 10))

    def test_zero_lookback_is_empty(self):
        self.assertEqual(open_trading_dates(AS_OF, 0), [])


class CalendarGapTests(CalendarSeededTests):
    def test_calendar_gap_finds_absent_days(self):
        start = date(2026, 10, 20)
        self.fill_calendar_range(start, AS_OF)
        absent = [date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23)]
        ExchangeTradingCalendar.objects.filter(trade_date__in=absent).delete()
        gap = detectors.detect_calendar_gap(self.ctx(window_start=start))
        self.assertEqual(gap.missing_dates, tuple(absent))

    def test_no_gap_when_every_calendar_day_is_present(self):
        self.fill_calendar_range(date(2026, 10, 20), AS_OF)
        gap = detectors.detect_calendar_gap(self.ctx(window_start=date(2026, 10, 20)))
        self.assertTrue(gap.is_empty)

    def test_gap_reports_a_freshly_empty_calendar(self):
        ExchangeTradingCalendar.objects.all().delete()
        gap = detectors.detect_calendar_gap(self.ctx(window_start=date(2026, 10, 28)))
        self.assertEqual(len(gap.missing_dates), 3)


class BenchmarkGapTests(CalendarSeededTests):
    def test_benchmark_gap_covers_both_index_codes(self):
        # H00905.CSI is the total-return series whose absence is silent: TuShare
        # returns an empty frame, rs_vs_benchmark_* goes null for the whole pool and
        # the RULE scorer reports an all-defensive book as though it were a market
        # call. Only 000905.SH is stored here, so H00905.CSI must still be reported.
        for day in OPEN_DAYS:
            BenchmarkIndexDaily.objects.create(
                index_code='000905.SH', index_name='CSI 500', trade_date=day, close=1)
        gap = detectors.detect_benchmark_gap(self.ctx())
        self.assertEqual(gap.missing_dates, tuple(OPEN_DAYS))
        self.assertIn('H00905.CSI', gap.detail)

    def test_no_gap_when_both_codes_are_complete(self):
        for code in ('000905.SH', 'H00905.CSI'):
            for day in OPEN_DAYS:
                BenchmarkIndexDaily.objects.create(
                    index_code=code, index_name=code, trade_date=day, close=1)
        self.assertTrue(detectors.detect_benchmark_gap(self.ctx()).is_empty)


class EtfDailyGapTests(CalendarSeededTests):
    def test_etf_daily_gap_reuses_missing_etf_daily_dates(self):
        ctx = self.ctx()
        with patch('apps.etf_rotation.tasks.missing_etf_daily_dates',
                   return_value=[OPEN_DAYS[0]]) as mocked:
            gap = detectors.detect_etf_daily_gap(ctx)
        mocked.assert_called_once_with(ctx.window_start, AS_OF, ctx.exchange_code)
        self.assertEqual(gap.missing_dates, (OPEN_DAYS[0],))
        self.assertEqual(gap.estimated_calls, 2)   # fund_daily + fund_adj per date


class MacroGapTests(CalendarSeededTests):
    def test_macro_gap_expects_month_starts(self):
        gap = detectors.detect_macro_gap(self.ctx(window_start=date(2026, 9, 1)))
        self.assertEqual(gap.missing_dates, (date(2026, 9, 1), date(2026, 10, 1)))

    def test_macro_gap_satisfied_by_month_start_row(self):
        MacroSnapshot.objects.create(date=date(2026, 9, 1))
        MacroSnapshot.objects.create(date=date(2026, 10, 1))
        gap = detectors.detect_macro_gap(self.ctx(window_start=date(2026, 9, 1)))
        self.assertTrue(gap.is_empty)

    def test_macro_gap_ignores_a_window_without_a_month_start(self):
        self.assertTrue(detectors.detect_macro_gap(self.ctx()).is_empty)


class CrossSectionalTests(CalendarSeededTests):
    def seed_flow(self, assets, days):
        CapitalFlowSnapshot.objects.bulk_create([
            CapitalFlowSnapshot(asset=asset, date=day)
            for asset in assets for day in days
        ])

    def test_cross_sectional_detects_collapsed_date(self):
        assets = [self.make_asset(f'60000{i}') for i in range(4)]
        self.seed_flow(assets, OPEN_DAYS)
        # A fetch failure removes every asset on one date.
        CapitalFlowSnapshot.objects.filter(date=OPEN_DAYS[2]).delete()
        gap = detectors.detect_capital_flow_gap(self.ctx())
        self.assertEqual(gap.missing_dates, (OPEN_DAYS[2],))

    def test_cross_sectional_ignores_structural_blackout(self):
        assets = [self.make_asset(f'60000{i}') for i in range(4)]
        self.seed_flow(assets, OPEN_DAYS)
        # A structural blackout removes one asset on *all* dates. The cross-section
        # drops 4 -> 3, above the 0.5 ratio, so this must not be reported: retrying
        # it would spend quota forever on rows TuShare does not have.
        CapitalFlowSnapshot.objects.filter(asset=assets[0]).delete()
        self.assertTrue(detectors.detect_capital_flow_gap(self.ctx()).is_empty)

    def test_cross_sectional_reports_everything_when_nothing_was_fetched(self):
        gap = detectors.detect_capital_flow_gap(self.ctx())
        self.assertEqual(gap.missing_dates, tuple(OPEN_DAYS))

    def test_universe_size_step_is_not_reported_as_a_hole(self):
        # The live database narrowed its stored cross-section mid-window from ~1750
        # assets to ~500 and later to ~230. Measured against a single window median,
        # every date after each step reads as collapsed, no repair can fix it, and
        # fundamentals, capital_flow and the whole derived layer block upstream
        # forever. A step is in step with the dates beside it; a failure is not.
        days = tuple(AS_OF - timedelta(days=10 - index) for index in range(8))
        wide = [self.make_asset(f'6001{i}') for i in range(8)]
        self.seed_flow(wide, days[:5])
        self.seed_flow(wide[:2], days[5:])
        ctx = self.ctx(open_dates=days, window_start=days[0])
        self.assertTrue(detectors.detect_capital_flow_gap(ctx).is_empty)

    def test_isolated_dip_inside_a_shrinking_universe_is_still_caught(self):
        days = tuple(AS_OF - timedelta(days=10 - index) for index in range(8))
        wide = [self.make_asset(f'6001{i}') for i in range(8)]
        self.seed_flow(wide, days[:5])
        self.seed_flow(wide[:2], days[5:])
        CapitalFlowSnapshot.objects.filter(date=days[6]).delete()
        ctx = self.ctx(open_dates=days, window_start=days[0])
        gap = detectors.detect_capital_flow_gap(ctx)
        self.assertEqual(gap.missing_dates, (days[6],))

    def test_ohlcv_uses_the_same_primitive(self):
        assets = [self.make_asset(f'60001{i}') for i in range(4)]
        OHLCV.objects.bulk_create([
            OHLCV(asset=asset, date=day, open=1, high=1, low=1, close=1,
                  adj_close=1, volume=1, amount=1)
            for asset in assets for day in OPEN_DAYS
        ])
        OHLCV.objects.filter(date=OPEN_DAYS[-1]).delete()
        gap = detectors.detect_ohlcv_gap(self.ctx())
        self.assertEqual(gap.missing_dates, (OPEN_DAYS[-1],))
        # One dispatcher run repairs a hole of any length, so it is not priced per date.
        self.assertEqual(gap.estimated_calls, 0)

    def test_empty_open_dates_is_no_work(self):
        self.assertEqual(
            cross_sectional_gap_dates(CapitalFlowSnapshot, self.ctx(open_dates=[])), [])


class WatermarkDetectorTests(CalendarSeededTests):
    """Class-B series: absence of rows is NOT evidence of a gap."""

    def test_suspensions_absence_is_not_a_gap(self):
        # No AssetSuspension rows at all and a fresh watermark: a day with zero
        # suspensions legitimately has zero rows, so row-presence would be a
        # permanent false positive here.
        seed_periodic_task(DAILY_DISPATCHER, watermark_at(AS_OF, 0))
        detector = detectors.watermark_detector(
            'suspensions', DAILY_DISPATCHER, grace_days=1)
        self.assertTrue(detector(self.ctx()).is_empty)

    def test_index_membership_absence_is_not_a_gap(self):
        seed_periodic_task(DAILY_DISPATCHER, watermark_at(AS_OF, 0))
        detector = detectors.watermark_detector(
            'index_membership', DAILY_DISPATCHER, grace_days=1)
        self.assertTrue(detector(self.ctx()).is_empty)

    def test_stale_watermark_yields_a_range_not_dates(self):
        seed_periodic_task(DAILY_DISPATCHER, watermark_at(AS_OF, 9))
        detector = detectors.watermark_detector(
            'suspensions', DAILY_DISPATCHER, grace_days=1)
        gap = detector(self.ctx())
        self.assertFalse(gap.is_empty)
        self.assertEqual(gap.kind, detectors.KIND_RANGE)
        self.assertEqual(gap.window_start, AS_OF - timedelta(days=9))
        self.assertEqual(gap.window_end, AS_OF)

    def test_replay_kind_converts_the_window_to_open_dates(self):
        seed_periodic_task('compute-etf-features-daily', watermark_at(AS_OF, 9))
        detector = detectors.watermark_detector(
            'etf_features', 'compute-etf-features-daily',
            grace_days=1, kind=detectors.KIND_REPLAY)
        gap = detector(self.ctx())
        self.assertEqual(gap.kind, detectors.KIND_REPLAY)
        self.assertEqual(gap.missing_dates, tuple(OPEN_DAYS))


class DetectorPurityTests(CalendarSeededTests):
    def test_detectors_never_call_tushare(self):
        # --dry-run is only zero-quota if detection is purely local.
        self.assertFalse(hasattr(detectors, 'ts'))
        MacroSnapshot.objects.create(date=date(2026, 10, 1))
        ctx = self.ctx(window_start=date(2026, 10, 1))
        for detector in (detectors.detect_calendar_gap, detectors.detect_benchmark_gap,
                         detectors.detect_etf_daily_gap, detectors.detect_macro_gap,
                         detectors.detect_capital_flow_gap,
                         detectors.detect_fundamentals_gap, detectors.detect_ohlcv_gap):
            detector(ctx)
