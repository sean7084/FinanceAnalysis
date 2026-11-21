"""C2 / UNI-03 (#196): coexisting PIT benchmark series and the daily maintenance loop."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.markets import tasks as market_tasks
from apps.markets import universe_policy as up
from apps.markets.benchmarking import (
    ACTIVE_PIT_BENCHMARK_SPECS,
    build_point_in_time_union_benchmark_rows,
    refresh_latest_point_in_time_union_benchmark,
    refresh_point_in_time_union_benchmark,
)
from apps.markets.models import Asset, IndexMembership, Market, OHLCV, PointInTimeBenchmarkDaily
from apps.markets.universe_policy import POLICY_RESEARCH_FULL


class DualPitSeriesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='SZSE', name='SZSE')
        cls.main = Asset.objects.create(market=cls.market, symbol='600001', ts_code='600001.SH', name='主板')
        cls.chinext = Asset.objects.create(market=cls.market, symbol='300001', ts_code='300001.SZ', name='创业')
        cls.d1 = date(2020, 1, 2)
        cls.d2 = date(2020, 1, 3)
        for asset in (cls.main, cls.chinext):
            IndexMembership.objects.create(asset=asset, index_code='000905.SH', trade_date=cls.d1, weight=1.0)
            for d in (cls.d1, cls.d2):
                OHLCV.objects.create(
                    asset=asset, date=d,
                    open=Decimal('10'), high=Decimal('11'), low=Decimal('9'), close=Decimal('10.5'),
                    adj_close=Decimal('10.5'), volume=100, amount=1000,
                )

    def test_full_and_default_series_coexist(self):
        with patch.object(up, 'ST_LAYER_ENABLED', False):
            refresh_point_in_time_union_benchmark(self.d1, self.d2, policy=POLICY_RESEARCH_FULL)
            refresh_point_in_time_union_benchmark(self.d1, self.d2)  # serving default = ex-board
            codes = set(PointInTimeBenchmarkDaily.objects.values_list('benchmark_code', flat=True))
            self.assertEqual(codes, {'CSI500_PIT', 'CSI500_PIT_EXBOARD'})
            full = PointInTimeBenchmarkDaily.objects.filter(benchmark_code='CSI500_PIT')
            exboard = PointInTimeBenchmarkDaily.objects.filter(benchmark_code='CSI500_PIT_EXBOARD')
            self.assertEqual(full.count(), exboard.count())
            self.assertEqual(full.last().constituent_count, 2)
            self.assertEqual(exboard.last().constituent_count, 1)  # ChiNext excluded

    def test_incremental_refresh_seeds_per_series(self):
        with patch.object(up, 'ST_LAYER_ENABLED', False):
            refresh_point_in_time_union_benchmark(self.d1, self.d1, policy=POLICY_RESEARCH_FULL)
            refresh_point_in_time_union_benchmark(self.d1, self.d1)
        d3 = date(2020, 1, 6)
        for asset in (self.main, self.chinext):
            OHLCV.objects.create(
                asset=asset, date=d3,
                open=Decimal('10'), high=Decimal('11'), low=Decimal('9'), close=Decimal('11'),
                adj_close=Decimal('11'), volume=100, amount=1000,
            )
        with patch.object(up, 'ST_LAYER_ENABLED', False):
            full_summary = refresh_latest_point_in_time_union_benchmark(d3, policy=POLICY_RESEARCH_FULL)
            default_summary = refresh_latest_point_in_time_union_benchmark(d3)
        self.assertEqual(full_summary['benchmark_code'], 'CSI500_PIT')
        self.assertEqual(default_summary['benchmark_code'], 'CSI500_PIT_EXBOARD')
        self.assertEqual(full_summary['refresh_mode'], 'incremental')
        self.assertEqual(default_summary['refresh_mode'], 'incremental')
        # The seed row's return must survive the incremental upsert (emit_seed_row=False).
        for code in ('CSI500_PIT', 'CSI500_PIT_EXBOARD'):
            seed = PointInTimeBenchmarkDaily.objects.get(benchmark_code=code, trade_date=self.d1)
            self.assertEqual(seed.daily_return, Decimal('0'))

    def test_daily_loop_refreshes_every_active_spec(self):
        with patch.object(market_tasks, 'refresh_latest_point_in_time_union_benchmark') as refresh, \
             patch.object(market_tasks, 'sync_daily_capital_flow_snapshots', return_value={}), \
             patch.object(market_tasks, 'calculate_factor_scores_for_date', return_value={}), \
             patch.object(market_tasks, 'calculate_indicators_for_all_assets', return_value={}), \
             patch.object(market_tasks, 'calculate_signals_for_all_assets', return_value={}):
            market_tasks.run_post_sync_universal_refresh(target_date=self.d2.isoformat())
        self.assertEqual(refresh.call_count, len(ACTIVE_PIT_BENCHMARK_SPECS))
        called_codes = [call.kwargs.get('benchmark_code') for call in refresh.call_args_list]
        self.assertIn('CSI500_PIT', called_codes)

    def test_official_comparison_specs_include_csi1000(self):
        from apps.backtest.comparison import BENCHMARK_SERIES_SPECS
        codes = {spec['code'] for spec in BENCHMARK_SERIES_SPECS}
        self.assertEqual(codes, {'000905.SH', '000852.SH'})

    def test_build_command_flags_reach_refresh(self):
        from django.core.management import call_command
        with patch('apps.markets.management.commands.build_pit_union_benchmark.refresh_point_in_time_union_benchmark') as refresh:
            refresh.return_value = {'benchmark_code': 'CSI1000_PIT_EXBOARD', 'rows_written': 0, 'start_date': '2020-01-02', 'end_date': '2020-01-03'}
            call_command(
                'build_pit_union_benchmark',
                '--start-date', '2020-01-02', '--end-date', '2020-01-03',
                '--index-codes', '000852.SH', '--policy', 'default',
                '--benchmark-code', 'CSI1000_PIT_EXBOARD',
            )
        kwargs = refresh.call_args.kwargs
        self.assertEqual(kwargs['index_codes'], ['000852.SH'])
        self.assertEqual(kwargs['policy'], 'default')
        self.assertEqual(kwargs['benchmark_code'], 'CSI1000_PIT_EXBOARD')

    def test_build_command_defaults_are_none(self):
        from django.core.management import call_command
        with patch('apps.markets.management.commands.build_pit_union_benchmark.refresh_point_in_time_union_benchmark') as refresh:
            refresh.return_value = {'benchmark_code': 'CSI500_PIT_EXBOARD', 'rows_written': 0, 'start_date': '2020-01-02', 'end_date': '2020-01-03'}
            call_command('build_pit_union_benchmark', '--start-date', '2020-01-02', '--end-date', '2020-01-03')
        kwargs = refresh.call_args.kwargs
        self.assertIsNone(kwargs['index_codes'])
        self.assertIsNone(kwargs['policy'])
        self.assertIsNone(kwargs['benchmark_code'])