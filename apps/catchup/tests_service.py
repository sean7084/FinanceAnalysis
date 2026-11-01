"""Tests for the bounded catch-up loop: ordering, budget, lock and dedupe."""

import time
from datetime import date, timedelta
from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catchup import registry, repairs, service
from apps.catchup.detectors import CatchUpContext, SeriesGap, calendar_scan_start
from apps.catchup.models import CatchUpRun
from apps.catchup.repairs import MACRO_YIELDS_DENIED_KEY
from apps.catchup.tests_detectors import DAILY_DISPATCHER, seed_periodic_task, watermark_at
from apps.factors.models import CapitalFlowSnapshot
from apps.markets.models import Asset, ExchangeTradingCalendar, Market, OHLCV

AS_OF = date(2026, 10, 30)
OPEN_DAYS = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28),
             date(2026, 10, 29), date(2026, 10, 30)]

# Tests must not depend on a live Redis, and a leaked lock would silently turn
# every later test in the class into a no-op.
LOC_MEM_CACHE = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'catchup-tests',
    },
}


@override_settings(CACHES=LOC_MEM_CACHE, CATCHUP_LOOKBACK_TRADING_DAYS=10)
class CatchUpRunLoopTests(TestCase):
    def setUp(self):
        cache.clear()
        # Seed every calendar day the pre-open_dates scan looks at, not just the open
        # ones: backfill_trading_calendar writes a row for closed days too, so a
        # sparse fixture would make every test start from a phantom calendar gap.
        open_days = set(OPEN_DAYS)
        scan_start = calendar_scan_start(AS_OF)
        day = scan_start
        rows = []
        while day <= AS_OF:
            rows.append(ExchangeTradingCalendar(
                exchange_code='SSE', trade_date=day, is_open=day in open_days))
            day += timedelta(days=1)
        ExchangeTradingCalendar.objects.bulk_create(rows)
        self.market = Market.objects.create(code='CN', name='China')
        self.assets = [
            Asset.objects.create(market=self.market, symbol=f'60000{i}',
                                 ts_code=f'60000{i}.SH', name=f'A{i}',
                                 list_date=date(2015, 1, 1))
            for i in range(4)
        ]
        self.commands = []

    def tearDown(self):
        cache.clear()

    def record_command(self, name, *args, **kwargs):
        self.commands.append((name, kwargs))
        return ''

    def seed_dispatcher(self, days_ago=0):
        seed_periodic_task(DAILY_DISPATCHER, watermark_at(AS_OF, days_ago))

    def run_catch_up(self, **kwargs):
        kwargs.setdefault('as_of', AS_OF)
        kwargs.setdefault('max_calls', 500)
        kwargs.setdefault('max_seconds', 600)
        with patch('apps.catchup.repairs.call_command', side_effect=self.record_command), \
                patch('apps.markets.tasks.sync_daily_a_shares',
                      return_value='synced') as sync:
            self.sync_mock = sync
            return service.run_catch_up(**kwargs)

    def outcome_for(self, rows, name):
        return next((row for row in rows if row.series == name), None)

    def budget(self, calls=100):
        return service.Budget(max_calls=calls, deadline=time.monotonic() + 60,
                              lock_ttl=600, holder='manual')

    # --- ordering ----------------------------------------------------------

    def test_calendar_repaired_before_dependents(self):
        ExchangeTradingCalendar.objects.filter(trade_date=OPEN_DAYS[-1]).delete()
        self.seed_dispatcher(days_ago=9)
        self.run_catch_up(series=['trading_calendar', 'benchmark_index'])
        self.assertEqual(self.commands[0][0], 'backfill_trading_calendar')

    def test_no_open_dates_stops_before_the_downstream_scan(self):
        # With no calendar rows at all nothing downstream can be scanned, so the run
        # must stop rather than report seventeen meaningless gaps.
        ExchangeTradingCalendar.objects.all().delete()
        rows = self.run_catch_up()
        self.assertEqual([row.series for row in rows], ['trading_calendar'])

    # --- OHLCV is one run, never one per date ------------------------------

    def test_ohlcv_repaired_by_single_call_not_per_date(self):
        OHLCV.objects.bulk_create([
            OHLCV(asset=asset, date=day, open=1, high=1, low=1, close=1,
                  adj_close=1, volume=1, amount=1)
            for asset in self.assets for day in OPEN_DAYS
        ])
        # Collapse three dates. A per-date replay would spend 3x the quota for the
        # same rows, because sync_asset_history resumes from latest.date + 1 day.
        OHLCV.objects.filter(date__in=OPEN_DAYS[-3:]).delete()
        self.seed_dispatcher()
        rows = self.run_catch_up(series=['ohlcv'])
        row = self.outcome_for(rows, 'ohlcv')
        self.assertEqual(row.outcome, CatchUpRun.Outcome.OK)
        self.assertEqual(row.gap_start, OPEN_DAYS[-3])
        self.assertEqual(row.gap_end, OPEN_DAYS[-1])
        self.assertEqual(self.sync_mock.call_count, 1)

    # --- budget ------------------------------------------------------------

    def test_call_budget_stops_run(self):
        ExchangeTradingCalendar.objects.filter(trade_date=OPEN_DAYS[-1]).delete()
        self.seed_dispatcher(days_ago=9)
        rows = self.run_catch_up(series=['trading_calendar', 'benchmark_index'],
                                 max_calls=2)
        partial = [row for row in rows
                   if row.outcome == CatchUpRun.Outcome.PARTIAL
                   and 'budget exhausted' in row.detail]
        self.assertTrue(partial)
        self.assertGreater(partial[0].units_remaining, 0)

    def test_soft_time_limit_marks_partial_and_stops(self):
        ExchangeTradingCalendar.objects.filter(trade_date=OPEN_DAYS[-1]).delete()
        self.seed_dispatcher(days_ago=9)
        with patch('apps.catchup.repairs.call_command',
                   side_effect=SoftTimeLimitExceeded()):
            rows = service.run_catch_up(as_of=AS_OF, max_calls=500, max_seconds=600,
                                        series=['trading_calendar', 'benchmark_index'])
        # The whole run stops: a truncated repair must not be followed by repairs
        # that assume it completed.
        self.assertEqual([row.series for row in rows], ['trading_calendar'])
        self.assertEqual(rows[0].outcome, CatchUpRun.Outcome.PARTIAL)

    def test_throttle_error_stops_whole_run(self):
        ExchangeTradingCalendar.objects.filter(trade_date=OPEN_DAYS[-1]).delete()
        self.seed_dispatcher(days_ago=9)
        error = Exception('抱歉，您每分钟最多访问该接口500次')
        with patch('apps.catchup.repairs.call_command', side_effect=error):
            rows = service.run_catch_up(as_of=AS_OF, max_calls=500, max_seconds=600,
                                        series=['trading_calendar', 'benchmark_index'])
        self.assertEqual([row.series for row in rows], ['trading_calendar'])
        self.assertEqual(rows[0].outcome, CatchUpRun.Outcome.FAILED)
        self.assertEqual(rows[0].cause, CatchUpRun.Cause.THROTTLED)

    # --- lock --------------------------------------------------------------

    def test_lock_prevents_overlap(self):
        cache.set(service.LOCK_KEY, 'startup', 600)
        with patch('apps.catchup.repairs.call_command') as mocked:
            rows = service.run_catch_up(as_of=AS_OF, trigger='beat')
        self.assertEqual(rows, [])
        mocked.assert_not_called()
        self.assertEqual(CatchUpRun.objects.count(), 0)

    def test_lock_is_released_after_a_run(self):
        self.seed_dispatcher()
        service.run_catch_up(as_of=AS_OF, series=['trading_calendar'])
        self.assertIsNone(cache.get(service.LOCK_KEY))

    def test_lock_ttl_outlives_max_seconds(self):
        # A TTL shorter than the run lets the hourly beat entry start a second pass
        # on top of the startup hook, and both then spend the same per-minute quota.
        self.assertGreaterEqual(service.lock_ttl_for(1800), 1800 + 300)
        self.assertGreaterEqual(service.lock_ttl_for(60), 1500)

    # --- dependency gating -------------------------------------------------

    def test_dependent_runs_once_its_upstream_gap_is_filled(self):
        OHLCV.objects.bulk_create([
            OHLCV(asset=asset, date=day, open=1, high=1, low=1, close=1,
                  adj_close=1, volume=1, amount=1)
            for asset in self.assets for day in OPEN_DAYS
        ])
        self.seed_dispatcher()
        rows = self.run_catch_up(series=['ohlcv', 'capital_flow'])
        blocked = self.outcome_for(rows, 'capital_flow')
        self.assertIsNotNone(blocked)
        # capital_flow has no rows at all, so it is either repaired or reported as
        # waiting on ohlcv -- it must never be silently skipped.
        self.assertIn(blocked.outcome, (CatchUpRun.Outcome.BLOCKED_UPSTREAM,
                                        CatchUpRun.Outcome.OK))

    def test_blocked_upstream_is_recorded_when_the_upstream_repair_fails(self):
        ExchangeTradingCalendar.objects.filter(trade_date=OPEN_DAYS[-1]).delete()
        self.seed_dispatcher(days_ago=9)
        with patch('apps.catchup.repairs.call_command', side_effect=Exception('boom')):
            rows = service.run_catch_up(as_of=AS_OF, max_calls=500, max_seconds=600,
                                        series=['trading_calendar', 'benchmark_index'])
        benchmark = self.outcome_for(rows, 'benchmark_index')
        self.assertEqual(benchmark.outcome, CatchUpRun.Outcome.BLOCKED_UPSTREAM)
        self.assertIn('trading_calendar', benchmark.detail)

    # --- record keeping ----------------------------------------------------

    def test_no_gap_dedupe_one_row_per_day(self):
        self.seed_dispatcher()
        for _ in range(3):
            service.run_catch_up(as_of=AS_OF, series=['trading_calendar'])
        self.assertEqual(
            CatchUpRun.objects.filter(series='trading_calendar',
                                      outcome=CatchUpRun.Outcome.NO_GAP).count(), 1)

    def test_failed_outcome_always_inserts(self):
        ExchangeTradingCalendar.objects.filter(trade_date=OPEN_DAYS[-1]).delete()
        with patch('apps.catchup.repairs.call_command', side_effect=Exception('boom')):
            for _ in range(2):
                service.run_catch_up(as_of=AS_OF, series=['trading_calendar'])
        self.assertEqual(
            CatchUpRun.objects.filter(series='trading_calendar',
                                      outcome=CatchUpRun.Outcome.FAILED).count(), 2)

    def test_dry_run_spends_nothing(self):
        ExchangeTradingCalendar.objects.filter(trade_date=OPEN_DAYS[-1]).delete()
        self.seed_dispatcher(days_ago=9)
        rows = service.run_catch_up(as_of=AS_OF, dry_run=True,
                                    series=['trading_calendar', 'ohlcv'],
                                    max_calls=500, max_seconds=600)
        with patch('apps.catchup.repairs.call_command') as mocked:
            service.run_catch_up(as_of=AS_OF, dry_run=True,
                                 series=['trading_calendar'], max_calls=500,
                                 max_seconds=600)
        mocked.assert_not_called()
        self.assertTrue(all(row.outcome == CatchUpRun.Outcome.SKIPPED for row in rows))
        # A dry run reports what it found but writes no history.
        self.assertEqual(CatchUpRun.objects.count(), 0)
        self.assertTrue(any(row.units_remaining for row in rows))

    def test_cause_is_beat_outage_after_an_outage(self):
        ExchangeTradingCalendar.objects.filter(trade_date=OPEN_DAYS[-1]).delete()
        self.seed_dispatcher(days_ago=9)
        rows = self.run_catch_up(series=['trading_calendar'])
        self.assertEqual(rows[0].cause, CatchUpRun.Cause.BEAT_OUTAGE)
        self.assertEqual(rows[0].gap_start, OPEN_DAYS[-1])

    # --- command kwargs ----------------------------------------------------

    def test_membership_repair_passes_skip_sync_dispatch(self):
        self.seed_dispatcher(days_ago=9)
        rows = self.run_catch_up(series=['index_membership'])
        calls = [kwargs for name, kwargs in self.commands
                 if name == 'sync_index_constituents']
        self.assertEqual(len(calls), 1)
        # Without this the command fans out one sync_asset_history task per
        # constituent: redundant, and a quota spike nobody budgeted for.
        self.assertTrue(calls[0]['skip_sync_dispatch'])
        self.assertEqual(rows[0].outcome, CatchUpRun.Outcome.OK)

    def test_membership_repair_never_runs_on_a_fresh_watermark(self):
        self.seed_dispatcher()
        self.run_catch_up(series=['index_membership'])
        self.assertEqual(self.commands, [])

    def test_suspensions_repair_uses_the_watermark_window(self):
        self.seed_dispatcher(days_ago=9)
        self.run_catch_up(series=['suspensions'])
        calls = [kwargs for name, kwargs in self.commands
                 if name == 'backfill_asset_suspensions']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['end_date'], AS_OF.isoformat())
        self.assertEqual(calls[0]['start_date'], (AS_OF - timedelta(days=9)).isoformat())

    def test_capital_flow_batches_by_asset_not_by_date(self):
        # capital_flow depends on ohlcv, so a complete cross-section has to exist or
        # the dependency gate reports BLOCKED_UPSTREAM instead of batching.
        OHLCV.objects.bulk_create([
            OHLCV(asset=asset, date=day, open=1, high=1, low=1, close=1,
                  adj_close=1, volume=1, amount=1)
            for asset in self.assets for day in OPEN_DAYS
        ])
        CapitalFlowSnapshot.objects.bulk_create([
            CapitalFlowSnapshot(asset=asset, date=day)
            for asset in self.assets for day in OPEN_DAYS
        ])
        CapitalFlowSnapshot.objects.filter(date=OPEN_DAYS[-1]).delete()
        self.seed_dispatcher()
        rows = self.run_catch_up(series=['capital_flow'], max_calls=6)
        calls = [kwargs for name, kwargs in self.commands
                 if name == 'backfill_capital_flow_snapshots']
        self.assertEqual(len(calls), 1)
        # 6 calls of budget at 2 calls per asset => 3 assets this run. The hole's
        # length is irrelevant because each asset fetches a 5-year window.
        self.assertEqual(calls[0]['limit_assets'], 3)
        self.assertEqual(rows[0].outcome, CatchUpRun.Outcome.OK)

    def test_etf_daily_repair_widens_the_range_without_touching_the_beat_entry(self):
        self.seed_dispatcher()
        gap = SeriesGap('etf_daily', 'dates', missing_dates=tuple(OPEN_DAYS))
        with patch('apps.catchup.repairs.call_command',
                   side_effect=self.record_command):
            repairs.repair_etf_daily(self.ctx(), gap, self.budget(calls=4))
        calls = [kwargs for name, kwargs in self.commands if name == 'sync_etf_daily']
        # 4 calls at 2 per date => two dates this run, oldest first.
        self.assertEqual(calls[0]['start'], OPEN_DAYS[0].isoformat())
        self.assertEqual(calls[0]['end'], OPEN_DAYS[1].isoformat())

    def ctx(self):
        return CatchUpContext(as_of=AS_OF, open_dates=tuple(OPEN_DAYS),
                              window_start=OPEN_DAYS[0], trigger='manual')


@override_settings(CACHES=LOC_MEM_CACHE)
class MacroYieldEntitlementTests(TestCase):
    def setUp(self):
        cache.clear()
        self.ctx = CatchUpContext(as_of=AS_OF, open_dates=tuple(OPEN_DAYS),
                                  window_start=OPEN_DAYS[0], trigger='manual')
        self.gap = SeriesGap('macro_yields', 'dates',
                             missing_dates=(date(2026, 10, 1),))
        self.budget = service.Budget(max_calls=100, deadline=time.monotonic() + 60,
                                     lock_ttl=600, holder='manual')

    def tearDown(self):
        cache.clear()

    def test_denied_entitlement_short_circuits_without_calling_the_provider(self):
        cache.set(MACRO_YIELDS_DENIED_KEY, True, timeout=None)
        with patch('apps.catchup.repairs.call_command') as mocked:
            result = repairs.repair_macro_yields(self.ctx, self.gap, self.budget)
        # yc_cb is refused on this token tier and the yield phase sleeps 31s between
        # calls, so retrying would burn the run's wall clock to learn nothing.
        mocked.assert_not_called()
        self.assertEqual(result.outcome, CatchUpRun.Outcome.SKIPPED)
        self.assertEqual(result.cause, CatchUpRun.Cause.ENTITLEMENT)

    def test_first_refusal_is_cached_permanently(self):
        with patch('apps.catchup.repairs.call_command',
                   side_effect=Exception('抱歉，您没有权限访问该接口')):
            result = repairs.repair_macro_yields(self.ctx, self.gap, self.budget)
        self.assertEqual(result.outcome, CatchUpRun.Outcome.SKIPPED)
        self.assertTrue(cache.get(MACRO_YIELDS_DENIED_KEY))

    def test_a_non_permission_error_still_propagates(self):
        with patch('apps.catchup.repairs.call_command',
                   side_effect=Exception('connection reset')):
            with self.assertRaises(Exception):
                repairs.repair_macro_yields(self.ctx, self.gap, self.budget)
        self.assertIsNone(cache.get(MACRO_YIELDS_DENIED_KEY))


class RegistryIntegrityTests(TestCase):
    def test_every_descriptor_has_a_callable_detector_and_repair(self):
        for descriptor in registry.SERIES:
            self.assertTrue(callable(descriptor.detector), descriptor.name)
            self.assertTrue(callable(descriptor.repair), descriptor.name)

    def test_series_filter_preserves_priority_order(self):
        selected = registry.ordered(['predictions', 'ohlcv'])
        self.assertEqual([d.name for d in selected], ['ohlcv', 'predictions'])
