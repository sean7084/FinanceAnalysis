import json
import tempfile
import csv
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
from django.core.management import call_command
from django.test import TestCase

from .admin import AssetAdmin
from apps.analytics.indicator_warmup import (
    MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS,
    technical_indicator_warmup_prefill_start_date,
)
from .benchmarking import PITMembershipCoverageError, build_point_in_time_union_benchmark_rows, ensure_pit_membership_coverage, point_in_time_union_asset_ids, refresh_latest_point_in_time_union_benchmark, refresh_point_in_time_union_benchmark, required_pit_index_codes_for_date, resolve_point_in_time_union_membership
from .models import Asset, AssetSuspension, BenchmarkIndexDaily, ExchangeTradingCalendar, IndexMembership, Market, OHLCV, PointInTimeBenchmarkDaily
from .tasks import run_post_sync_universal_refresh, sync_asset_suspensions, sync_benchmark_index_history, sync_daily_a_shares, sync_exchange_trading_calendar, sync_monthly_index_memberships


class MarketAdminAndBackfillTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        self.active_asset = Asset.objects.create(
            market=self.market,
            symbol='600001',
            ts_code='600001.SH',
            name='Active Asset',
        )
        self.delisted_asset = Asset.objects.create(
            market=self.market,
            symbol='600002',
            ts_code='600002.SH',
            name='Delisted Asset',
            listing_status=Asset.ListingStatus.ACTIVE,
        )

    def test_asset_admin_exposes_lifecycle_dates(self):
        self.assertIn('list_date', AssetAdmin.list_display)
        self.assertIn('delist_date', AssetAdmin.list_display)
        self.assertIn('list_date', AssetAdmin.list_filter)
        self.assertIn('delist_date', AssetAdmin.list_filter)

    @patch('apps.markets.management.commands.backfill_asset_list_dates.ts.pro_api')
    def test_backfill_asset_list_dates_populates_existing_assets(self, mock_pro_api):
        class StubPro:
            def stock_basic(self, **kwargs):
                list_status = kwargs['list_status']
                if list_status == 'L':
                    return pd.DataFrame([
                        {'ts_code': '600001.SH', 'list_date': '20100105', 'delist_date': None, 'list_status': 'L'},
                    ])
                if list_status == 'D':
                    return pd.DataFrame([
                        {'ts_code': '600002.SH', 'list_date': '20040315', 'delist_date': '20221230', 'list_status': 'D'},
                    ])
                return pd.DataFrame([])

        mock_pro_api.return_value = StubPro()

        output = StringIO()
        with patch('apps.markets.management.commands.backfill_asset_list_dates.settings.TUSHARE_TOKEN', 'test-token'):
            call_command('backfill_asset_list_dates', stdout=output)

        self.active_asset.refresh_from_db()
        self.delisted_asset.refresh_from_db()

        self.assertEqual(self.active_asset.list_date.isoformat(), '2010-01-05')
        self.assertIsNone(self.active_asset.delist_date)
        self.assertEqual(self.active_asset.listing_status, Asset.ListingStatus.ACTIVE)
        self.assertEqual(self.delisted_asset.list_date.isoformat(), '2004-03-15')
        self.assertEqual(self.delisted_asset.delist_date.isoformat(), '2022-12-30')
        self.assertEqual(self.delisted_asset.listing_status, Asset.ListingStatus.DELISTED)
        self.assertIn('processed=2', output.getvalue())
        self.assertIn('updated=2', output.getvalue())
        self.assertIn('akshare_fallback_hits=0', output.getvalue())

    @patch('apps.markets.management.commands.backfill_asset_list_dates._get_akshare_module')
    @patch('apps.markets.management.commands.backfill_asset_list_dates.ts.pro_api')
    def test_backfill_asset_list_dates_uses_akshare_fallback_when_tushare_missing(self, mock_pro_api, mock_get_akshare_module):
        class StubPro:
            def stock_basic(self, **kwargs):
                return pd.DataFrame([])

        class StubAk:
            @staticmethod
            def stock_info_sh_name_code():
                return pd.DataFrame([
                    {'证券代码': '600001', '上市日期': '2010-01-05'},
                ])

            @staticmethod
            def stock_info_sz_name_code():
                return pd.DataFrame([])

            @staticmethod
            def stock_info_bj_name_code():
                return pd.DataFrame([])

        mock_pro_api.return_value = StubPro()
        mock_get_akshare_module.return_value = StubAk()

        output = StringIO()
        with patch('apps.markets.management.commands.backfill_asset_list_dates.settings.TUSHARE_TOKEN', 'test-token'):
            call_command('backfill_asset_list_dates', symbols='600001', stdout=output)

        self.active_asset.refresh_from_db()

        self.assertEqual(self.active_asset.list_date.isoformat(), '2010-01-05')
        self.assertEqual(self.active_asset.listing_status, Asset.ListingStatus.ACTIVE)
        self.assertIn('processed=1', output.getvalue())
        self.assertIn('updated=1', output.getvalue())
        self.assertIn('missing=0', output.getvalue())
        self.assertIn('akshare_fallback_hits=1', output.getvalue())

    @patch('apps.markets.management.commands.backfill_ohlcv_history.sync_asset_history')
    def test_backfill_ohlcv_history_passes_explicit_repair_window(self, mock_sync_asset_history):
        Asset.objects.filter(pk=self.active_asset.pk).update(list_date=date(2010, 1, 5))
        self.active_asset.refresh_from_db()
        output = StringIO()

        call_command(
            'backfill_ohlcv_history',
            start_date='2024-01-02',
            end_date='2024-01-05',
            symbols='600001',
            stdout=output,
        )

        mock_sync_asset_history.assert_called_once_with(
            '600001',
            'Active Asset',
            'SSE',
            True,
            list_date='2010-01-05',
            listing_status=Asset.ListingStatus.ACTIVE,
            delist_date=None,
            repair_start_date='2024-01-02',
            repair_end_date='2024-01-05',
        )
        self.assertIn('2024-01-02..2024-01-05', output.getvalue())

    @patch('apps.markets.management.commands.backfill_ohlcv_history.sync_asset_history')
    def test_backfill_ohlcv_history_can_extend_repair_window_for_technical_indicator_warmup(self, mock_sync_asset_history):
        output = StringIO()

        call_command(
            'backfill_ohlcv_history',
            start_date='2010-01-04',
            end_date='2010-01-05',
            symbols='600001',
            technical_indicator_warmup=True,
            stdout=output,
        )

        expected_effective_start = technical_indicator_warmup_prefill_start_date(date(2010, 1, 4))
        self.assertEqual((date(2010, 1, 4) - expected_effective_start).days, MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS)
        mock_sync_asset_history.assert_called_once_with(
            '600001',
            'Active Asset',
            'SSE',
            True,
            list_date=None,
            listing_status=Asset.ListingStatus.ACTIVE,
            delist_date=None,
            repair_start_date=expected_effective_start.isoformat(),
            repair_end_date='2010-01-05',
            allow_pre_floor_repair=True,
        )
        self.assertIn('lookback_trading_days=33', output.getvalue())
        self.assertIn(f'calendar_prefill_days={MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS}', output.getvalue())
        self.assertIn(f'effective_start={expected_effective_start}', output.getvalue())

    @patch('apps.markets.management.commands.backfill_ohlcv_history.point_in_time_union_asset_ids_by_dates')
    @patch('apps.markets.management.commands.backfill_ohlcv_history.sync_asset_history')
    def test_backfill_ohlcv_history_can_dispatch_effective_universe_entry_warmup_repairs(self, mock_sync_asset_history, mock_effective_universe_by_dates):
        Asset.objects.filter(pk=self.delisted_asset.pk).update(
            list_date=date(1993, 1, 1),
            listing_status=Asset.ListingStatus.DELISTED,
            delist_date=date(2012, 12, 31),
        )
        self.delisted_asset.refresh_from_db()

        ExchangeTradingCalendar.objects.create(exchange_code='SSE', trade_date='2011-01-04', previous_trade_date='2010-12-31')
        ExchangeTradingCalendar.objects.create(exchange_code='SSE', trade_date='2011-01-05', previous_trade_date='2011-01-04')
        mock_effective_universe_by_dates.return_value = {
            date(2011, 1, 4): {self.active_asset.id, self.delisted_asset.id},
            date(2011, 1, 5): {self.active_asset.id},
        }

        output = StringIO()
        call_command(
            'backfill_ohlcv_history',
            start_date='2011-01-04',
            end_date='2011-01-05',
            effective_universe_entry_warmup=True,
            stdout=output,
        )

        expected_effective_start = technical_indicator_warmup_prefill_start_date(date(2011, 1, 4))
        self.assertEqual((date(2011, 1, 4) - expected_effective_start).days, MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS)
        self.assertEqual(mock_sync_asset_history.call_count, 2)
        mock_sync_asset_history.assert_any_call(
            '600001',
            'Active Asset',
            'SSE',
            True,
            list_date=None,
            listing_status=Asset.ListingStatus.ACTIVE,
            delist_date=None,
            repair_start_date=expected_effective_start.isoformat(),
            repair_end_date='2011-01-04',
            allow_pre_floor_repair=True,
        )
        mock_sync_asset_history.assert_any_call(
            '600002',
            'Delisted Asset',
            'SSE',
            True,
            list_date='1993-01-01',
            listing_status=Asset.ListingStatus.DELISTED,
            delist_date='2012-12-31',
            repair_start_date=expected_effective_start.isoformat(),
            repair_end_date='2011-01-04',
            allow_pre_floor_repair=True,
        )
        self.assertIn('first_effective_universe_date=2011-01-04', output.getvalue())
        self.assertIn('lookback_trading_days=33', output.getvalue())
        self.assertIn(f'calendar_prefill_days={MINIMUM_HISTORY_PREFILL_CALENDAR_DAYS}', output.getvalue())

    @patch('apps.markets.management.commands.backfill_ohlcv_history.sync_asset_history.delay')
    def test_backfill_ohlcv_history_can_dispatch_merged_csv_repairs_for_delisted_assets(self, mock_delay):
        Asset.objects.filter(pk=self.delisted_asset.pk).update(listing_status=Asset.ListingStatus.DELISTED)

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / 'ohlcv_continuity_gaps.csv'
            csv_path.write_text(
                '\n'.join([
                    'metric_family,metric_name,rule_name,report_scope,asset_id,asset_symbol,asset_ts_code,asset_name,list_date,delist_date,expected_start,expected_end,first_observed_date,last_observed_date,expected_count,actual_count,missing_count,missing_pct,gap_start,gap_end,gap_missing_count',
                    'ohlcv,daily_bar,continuity_gap,asset_window,1,600001,600001.SH,Active Asset,1993-01-01,,2024-01-02,2024-01-10,,,7,0,7,1.0,2024-01-02,2024-01-05,4',
                    'ohlcv,daily_bar,continuity_gap,asset_window,1,600001,600001.SH,Active Asset,1993-01-01,,2024-01-02,2024-01-10,,,7,0,7,1.0,2024-01-06,2024-01-08,3',
                    'ohlcv,daily_bar,continuity_gap,asset_window,2,600002,600002.SH,Delisted Asset,1993-01-01,2024-01-31,2024-01-02,2024-01-10,,,7,0,7,1.0,2024-01-03,2024-01-04,2',
                ]),
                encoding='utf-8',
            )

            output = StringIO()
            call_command(
                'backfill_ohlcv_history',
                csv_file=str(csv_path),
                queue=True,
                stdout=output,
            )

        self.assertEqual(mock_delay.call_count, 2)
        first_call = mock_delay.call_args_list[0]
        second_call = mock_delay.call_args_list[1]
        self.assertEqual(first_call.args[:4], ('600001', 'Active Asset', 'SSE', True))
        self.assertEqual(first_call.kwargs, {
            'list_date': '1993-01-01',
            'listing_status': Asset.ListingStatus.ACTIVE,
            'delist_date': None,
            'repair_start_date': '2024-01-02',
            'repair_end_date': '2024-01-08',
        })
        self.assertEqual(second_call.args[:4], ('600002', 'Delisted Asset', 'SSE', True))
        self.assertEqual(second_call.kwargs, {
            'list_date': '1993-01-01',
            'listing_status': Asset.ListingStatus.DELISTED,
            'delist_date': '2024-01-31',
            'repair_start_date': '2024-01-03',
            'repair_end_date': '2024-01-04',
        })
        self.assertIn('600001.SH 2024-01-02..2024-01-08: queued', output.getvalue())
        self.assertIn('600002.SH 2024-01-03..2024-01-04: queued', output.getvalue())

    @patch('apps.markets.tasks.ts.pro_bar')
    @patch('apps.markets.tasks.ts.pro_api')
    def test_sync_asset_history_honors_explicit_repair_window(self, mock_pro_api, mock_pro_bar):
        mock_pro_api.return_value = object()
        OHLCV.objects.create(
            asset=self.active_asset,
            date='2024-01-10',
            open=Decimal('10.0'),
            high=Decimal('11.0'),
            low=Decimal('9.5'),
            close=Decimal('10.5'),
            volume=1000,
            adj_close=Decimal('10.5'),
            amount=Decimal('10000.0'),
        )
        mock_pro_bar.return_value = pd.DataFrame([
            {
                'trade_date': '20240103',
                'open': 10.0,
                'high': 11.0,
                'low': 9.5,
                'close': 10.5,
                'vol': 12.0,
                'amount': 345.0,
            },
        ])

        with patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'):
            from .tasks import sync_asset_history

            result = sync_asset_history(
                '600001',
                'Active Asset',
                'SSE',
                False,
                repair_start_date='2024-01-02',
                repair_end_date='2024-01-05',
            )

        self.assertIn('Completed 600001', result)
        self.assertEqual(mock_pro_bar.call_args.kwargs['start_date'], '20240102')
        self.assertEqual(mock_pro_bar.call_args.kwargs['end_date'], '20240105')
        self.assertTrue(OHLCV.objects.filter(asset=self.active_asset, date='2024-01-03').exists())

    @patch('apps.markets.tasks.ts.pro_bar')
    @patch('apps.markets.tasks.ts.pro_api')
    def test_sync_asset_history_can_honor_pre_floor_repair_window_when_allowed(self, mock_pro_api, mock_pro_bar):
        mock_pro_api.return_value = object()
        mock_pro_bar.return_value = pd.DataFrame([
            {
                'trade_date': '20091102',
                'open': 10.0,
                'high': 11.0,
                'low': 9.5,
                'close': 10.5,
                'vol': 12.0,
                'amount': 345.0,
            },
        ])

        with patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'):
            from .tasks import sync_asset_history

            result = sync_asset_history(
                '600001',
                'Active Asset',
                'SSE',
                False,
                repair_start_date='2009-10-30',
                repair_end_date='2010-01-05',
                allow_pre_floor_repair=True,
            )

        self.assertIn('Completed 600001', result)
        self.assertEqual(mock_pro_bar.call_args.kwargs['start_date'], '20091030')
        self.assertEqual(mock_pro_bar.call_args.kwargs['end_date'], '20100105')


class TradingCalendarAndSuspensionSyncTests(TestCase):
    def setUp(self):
        self.sse = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        self.szse = Market.objects.create(code='SZSE', name='Shenzhen Stock Exchange')
        self.asset_sse = Asset.objects.create(
            market=self.sse,
            symbol='600001',
            ts_code='600001.SH',
            name='SSE Asset',
        )
        self.asset_szse = Asset.objects.create(
            market=self.szse,
            symbol='000001',
            ts_code='000001.SZ',
            name='SZSE Asset',
        )

    @patch('apps.markets.tasks.ts.pro_api')
    def test_sync_exchange_trading_calendar_persists_open_days(self, mock_pro_api):
        class StubPro:
            def trade_cal(self, **kwargs):
                if kwargs['exchange'] == 'SSE':
                    return pd.DataFrame([
                        {'exchange': 'SSE', 'cal_date': '20260424', 'is_open': '1', 'pretrade_date': '20260423'},
                        {'exchange': 'SSE', 'cal_date': '20260427', 'is_open': '1', 'pretrade_date': '20260424'},
                    ])
                if kwargs['exchange'] == 'SZSE':
                    return pd.DataFrame([
                        {'exchange': 'SZSE', 'cal_date': '20260424', 'is_open': '1', 'pretrade_date': '20260423'},
                    ])
                return pd.DataFrame([])

        mock_pro_api.return_value = StubPro()

        with patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'):
            summary = sync_exchange_trading_calendar(
                exchange_codes=('SSE', 'SZSE'),
                start_date='2026-04-24',
                end_date='2026-04-27',
            )

        self.assertEqual(summary['rows_written'], 3)
        self.assertEqual(summary['latest_trade_dates']['SSE'], '2026-04-27')
        self.assertEqual(summary['latest_trade_dates']['SZSE'], '2026-04-24')
        self.assertTrue(ExchangeTradingCalendar.objects.filter(exchange_code='SSE', trade_date='2026-04-24').exists())
        self.assertTrue(ExchangeTradingCalendar.objects.filter(exchange_code='SSE', trade_date='2026-04-27', previous_trade_date='2026-04-24').exists())
        self.assertTrue(ExchangeTradingCalendar.objects.filter(exchange_code='SZSE', trade_date='2026-04-24').exists())

    @patch('apps.markets.tasks.ts.pro_api')
    def test_sync_exchange_trading_calendar_filters_closed_days_from_mixed_response(self, mock_pro_api):
        class StubPro:
            def trade_cal(self, **kwargs):
                return pd.DataFrame([
                    {'exchange': 'SSE', 'cal_date': '20260311', 'is_open': '1', 'pretrade_date': '20260310'},
                    {'exchange': 'SSE', 'cal_date': '20260312', 'is_open': '1', 'pretrade_date': '20260311'},
                    {'exchange': 'SSE', 'cal_date': '20260313', 'is_open': '1', 'pretrade_date': '20260312'},
                    {'exchange': 'SSE', 'cal_date': '20260314', 'is_open': '0', 'pretrade_date': '20260313'},
                    {'exchange': 'SSE', 'cal_date': '20260315', 'is_open': '0', 'pretrade_date': '20260313'},
                    {'exchange': 'SSE', 'cal_date': '20260316', 'is_open': '1', 'pretrade_date': '20260313'},
                ])

        mock_pro_api.return_value = StubPro()

        with patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'):
            summary = sync_exchange_trading_calendar(
                exchange_codes=('SSE',),
                start_date='2026-03-11',
                end_date='2026-03-16',
            )

        self.assertEqual(summary['rows_written'], 4)
        self.assertFalse(ExchangeTradingCalendar.objects.filter(exchange_code='SSE', trade_date='2026-03-14').exists())
        self.assertFalse(ExchangeTradingCalendar.objects.filter(exchange_code='SSE', trade_date='2026-03-15').exists())
        self.assertTrue(
            ExchangeTradingCalendar.objects.filter(
                exchange_code='SSE',
                trade_date='2026-03-16',
                previous_trade_date='2026-03-13',
            ).exists()
        )

    @patch('apps.markets.tasks.ts.pro_api')
    def test_sync_asset_suspensions_persists_full_day_flags(self, mock_pro_api):
        suspend_kwargs = {}

        class StubPro:
            def suspend_d(self, **kwargs):
                suspend_kwargs.update(kwargs)
                return pd.DataFrame([
                    {'ts_code': '600001.SH', 'trade_date': '20260425', 'suspend_type': 'S', 'suspend_timing': None},
                    {'ts_code': '000001.SZ', 'trade_date': '20260425', 'suspend_type': 'S', 'suspend_timing': '09:30-10:00'},
                ])

        mock_pro_api.return_value = StubPro()

        with patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'):
            summary = sync_asset_suspensions(start_date='2026-04-25', end_date='2026-04-25')

        self.assertEqual(summary['rows_written'], 2)
        self.assertEqual(summary['full_day_rows'], 1)
        self.assertEqual(suspend_kwargs['suspend_type'], '')
        self.assertTrue(AssetSuspension.objects.filter(asset=self.asset_sse, trade_date='2026-04-25', is_full_day=True).exists())
        self.assertTrue(AssetSuspension.objects.filter(asset=self.asset_szse, trade_date='2026-04-25', is_full_day=False).exists())

    @patch('apps.markets.tasks.ts.pro_api')
    def test_sync_asset_suspensions_paginates_results(self, mock_pro_api):
        offsets = []

        class StubPro:
            def suspend_d(self, **kwargs):
                offsets.append(kwargs['offset'])
                if kwargs['offset'] == 0:
                    return pd.DataFrame([
                        {'ts_code': '600001.SH', 'trade_date': '20260425', 'suspend_type': 'S', 'suspend_timing': None},
                    ])
                if kwargs['offset'] == 1:
                    return pd.DataFrame([
                        {'ts_code': '000001.SZ', 'trade_date': '20260425', 'suspend_type': 'S', 'suspend_timing': '09:30-10:00'},
                    ])
                return pd.DataFrame([])

        mock_pro_api.return_value = StubPro()

        with patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'), patch('apps.markets.tasks.SUSPEND_D_PAGE_LIMIT', 1):
            summary = sync_asset_suspensions(start_date='2026-04-25', end_date='2026-04-25')

        self.assertEqual(offsets, [0, 1, 2])
        self.assertEqual(summary['rows_written'], 2)
        self.assertTrue(AssetSuspension.objects.filter(asset=self.asset_sse, trade_date='2026-04-25').exists())
        self.assertTrue(AssetSuspension.objects.filter(asset=self.asset_szse, trade_date='2026-04-25').exists())

    @patch('apps.markets.tasks.ts.pro_api')
    def test_sync_asset_suspensions_deduplicates_same_day_rows(self, mock_pro_api):
        class StubPro:
            def suspend_d(self, **kwargs):
                return pd.DataFrame([
                    {'ts_code': '600001.SH', 'trade_date': '20260425', 'suspend_type': 'S', 'suspend_timing': '09:30-10:00'},
                    {'ts_code': '600001.SH', 'trade_date': '20260425', 'suspend_type': 'S', 'suspend_timing': None},
                    {'ts_code': '000001.SZ', 'trade_date': '20260425', 'suspend_type': 'S', 'suspend_timing': '09:30-10:00'},
                    {'ts_code': '000001.SZ', 'trade_date': '20260425', 'suspend_type': 'S', 'suspend_timing': '13:00-14:00'},
                ])

        mock_pro_api.return_value = StubPro()

        with patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'):
            summary = sync_asset_suspensions(start_date='2026-04-25', end_date='2026-04-25')

        self.assertEqual(summary['rows_written'], 2)
        self.assertEqual(summary['full_day_rows'], 1)

        sse_row = AssetSuspension.objects.get(asset=self.asset_sse, trade_date='2026-04-25')
        self.assertTrue(sse_row.is_full_day)
        self.assertIsNone(sse_row.suspend_timing)

        szse_row = AssetSuspension.objects.get(asset=self.asset_szse, trade_date='2026-04-25')
        self.assertFalse(szse_row.is_full_day)
        self.assertEqual(szse_row.suspend_timing, '09:30-10:00; 13:00-14:00')


class SuspensionOverlapReconciliationCommandTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='600001',
            ts_code='600001.SH',
            name='Suspension Test Asset',
        )
        self.trade_date = date(2026, 4, 30)
        AssetSuspension.objects.create(
            asset=self.asset,
            trade_date=self.trade_date,
            suspend_type='S',
            suspend_timing=None,
            is_full_day=True,
            source='tushare_suspend_d',
        )
        OHLCV.objects.create(
            asset=self.asset,
            date=self.trade_date,
            open=Decimal('10.0'),
            high=Decimal('10.2'),
            low=Decimal('9.8'),
            close=Decimal('10.1'),
            adj_close=Decimal('10.1'),
            volume=100000,
            amount=Decimal('1010000.0'),
        )

    def _write_report(self, temp_dir):
        report_path = Path(temp_dir) / 'asset_lifecycle_issues.csv'
        with report_path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                'metric_family',
                'metric_name',
                'rule_name',
                'report_scope',
                'issue_type',
                'severity',
                'asset_id',
                'asset_symbol',
                'asset_ts_code',
                'asset_name',
                'listing_status',
                'list_date',
                'delist_date',
                'field_name',
                'first_observed_date',
                'last_observed_date',
                'details',
            ])
            writer.writeheader()
            writer.writerow({
                'metric_family': 'asset_lifecycle',
                'metric_name': 'trade_date',
                'rule_name': 'ohlcv_on_full_day_suspension',
                'report_scope': 'asset_lifecycle',
                'issue_type': 'ohlcv_on_full_day_suspension',
                'severity': 'warning',
                'asset_id': '1',
                'asset_symbol': '600001',
                'asset_ts_code': '600001.SH',
                'asset_name': 'Suspension Test Asset',
                'listing_status': 'A',
                'list_date': '',
                'delist_date': '',
                'field_name': 'trade_date',
                'first_observed_date': '2026-04-30',
                'last_observed_date': '2026-04-30',
                'details': 'OHLCV exists on full-day suspension dates: 2026-04-30..2026-04-30 (1 rows).',
            })
        return report_path

    @patch('apps.markets.management.commands.reconcile_suspension_ohlcv_overlaps._get_akshare_module')
    def test_reconcile_suspension_ohlcv_overlaps_reports_verified_rows_without_deleting(self, mock_get_akshare_module):
        class StubAk:
            @staticmethod
            def news_trade_notify_suspend_baidu(date, cookie=None):
                return pd.DataFrame([
                    {
                        '股票代码': '600001',
                        '股票简称': 'Suspension Test Asset',
                        '交易所代码': 'SH',
                        '停牌时间': '2026-04-30',
                        '复牌时间': '2026-05-06',
                        '停牌事项说明': '重要公告',
                        '市值': '100000000',
                        '公告日期': '2026-04-30',
                        '公告时间': '--',
                        '证券类型': 'stock',
                        '市场类型': 'ab',
                        '是否跳过': '1',
                    }
                ])

        mock_get_akshare_module.return_value = StubAk()

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = self._write_report(temp_dir)
            output_path = Path(temp_dir) / 'reconciliation.csv'

            call_command(
                'reconcile_suspension_ohlcv_overlaps',
                csv_file=str(report_path),
                output_file=str(output_path),
            )

            with output_path.open(newline='', encoding='utf-8') as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['trade_date'], '2026-04-30')
        self.assertEqual(rows[0]['akshare_verified'], 'True')
        self.assertEqual(rows[0]['action'], 'would_delete')
        self.assertTrue(OHLCV.objects.filter(asset=self.asset, date=self.trade_date).exists())

    @patch('apps.markets.management.commands.reconcile_suspension_ohlcv_overlaps._get_akshare_module')
    def test_reconcile_suspension_ohlcv_overlaps_execute_deletes_verified_rows(self, mock_get_akshare_module):
        class StubAk:
            @staticmethod
            def news_trade_notify_suspend_baidu(date, cookie=None):
                return pd.DataFrame([
                    {
                        '股票代码': '600001',
                        '股票简称': 'Suspension Test Asset',
                        '交易所代码': 'SH',
                        '停牌时间': '2026-04-30',
                        '复牌时间': '2026-05-06',
                        '停牌事项说明': '重要公告',
                        '市值': '100000000',
                        '公告日期': '2026-04-30',
                        '公告时间': '--',
                        '证券类型': 'stock',
                        '市场类型': 'ab',
                        '是否跳过': '1',
                    }
                ])

        mock_get_akshare_module.return_value = StubAk()

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = self._write_report(temp_dir)
            output_path = Path(temp_dir) / 'reconciliation.csv'

            call_command(
                'reconcile_suspension_ohlcv_overlaps',
                csv_file=str(report_path),
                output_file=str(output_path),
                execute=True,
            )

            with output_path.open(newline='', encoding='utf-8') as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['action'], 'deleted')
        self.assertEqual(rows[0]['deleted_rows'], '1')
        self.assertFalse(OHLCV.objects.filter(asset=self.asset, date=self.trade_date).exists())


class IndexConstituentSyncTests(TestCase):
    def setUp(self):
        self.sse = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        self.szse = Market.objects.create(code='SZSE', name='Shenzhen Stock Exchange')
        self.existing_asset = Asset.objects.create(
            market=self.sse,
            symbol='600001',
            ts_code='600001.SH',
            name='Existing Overlap Asset',
        )

    def _stub_pro(self):
        class StubPro:
            def stock_basic(self, **kwargs):
                if kwargs['list_status'] == 'L':
                    return pd.DataFrame([
                        {'ts_code': '600001.SH', 'symbol': '600001', 'name': 'Existing Overlap Asset', 'list_date': '20100105', 'list_status': 'L'},
                        {'ts_code': '600002.SH', 'symbol': '600002', 'name': 'CSI 300 Only Asset', 'list_date': '20120608', 'list_status': 'L'},
                        {'ts_code': '000001.SZ', 'symbol': '000001', 'name': 'CSI A500 Only Asset', 'list_date': '20150317', 'list_status': 'L'},
                    ])
                return pd.DataFrame([])

            def index_weight(self, **kwargs):
                index_code = kwargs['index_code']
                if index_code in {'000300.SH', '399300.SZ'}:
                    return pd.DataFrame([
                        {'trade_date': '20260325', 'con_code': '600001.SH', 'weight': 4.2},
                        {'trade_date': '20260425', 'con_code': '600001.SH', 'weight': 4.8},
                        {'trade_date': '20260425', 'con_code': '600002.SH', 'weight': 3.9},
                    ])
                if index_code == '000510.CSI':
                    return pd.DataFrame([
                        {'trade_date': '20260325', 'con_code': '600001.SH', 'weight': 1.7},
                        {'trade_date': '20260425', 'con_code': '600001.SH', 'weight': 1.9},
                        {'trade_date': '20260425', 'con_code': '000001.SZ', 'weight': 2.6},
                    ])
                return pd.DataFrame([])

        return StubPro()

    @patch('apps.markets.tasks.sync_asset_history.delay')
    @patch('apps.markets.tasks.ts.pro_api')
    def test_sync_index_constituents_persists_membership_tags_and_dedupes_dispatch(self, mock_pro_api, mock_delay):
        mock_pro_api.return_value = self._stub_pro()

        output = StringIO()
        with patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'):
            call_command(
                'sync_index_constituents',
                start_date='2026-03-25',
                end_date='2026-04-25',
                stdout=output,
            )

        self.existing_asset.refresh_from_db()
        csi300_only_asset = Asset.objects.get(ts_code='600002.SH')
        a500_only_asset = Asset.objects.get(ts_code='000001.SZ')

        self.assertCountEqual(self.existing_asset.membership_tags, ['CSIA500', 'CSI300'])
        self.assertCountEqual(csi300_only_asset.membership_tags, ['CSI300'])
        self.assertCountEqual(a500_only_asset.membership_tags, ['CSIA500'])

        self.assertEqual(IndexMembership.objects.filter(asset=self.existing_asset, trade_date='2026-04-25').count(), 2)
        self.assertEqual(IndexMembership.objects.filter(index_code='000300.SH').count(), 3)
        self.assertEqual(IndexMembership.objects.filter(index_code='000510.CSI').count(), 3)

        self.assertEqual(mock_delay.call_count, 3)
        dispatched_symbols = {call.args[0] for call in mock_delay.call_args_list}
        self.assertEqual(dispatched_symbols, {'600001', '600002', '000001'})

        rendered_output = output.getvalue()
        self.assertIn('current_union_count=3', rendered_output)
        self.assertIn('overlap_count=1', rendered_output)
        self.assertIn('new_assets=2', rendered_output)
        self.assertIn('dispatched_assets=3', rendered_output)

    @patch('apps.markets.tasks.ts.pro_api')
    def test_sync_index_constituent_universe_chunks_large_index_weight_ranges(self, mock_pro_api):
        class StubPro:
            def __init__(self):
                self.index_weight_calls = []

            def stock_basic(self, **kwargs):
                if kwargs['list_status'] == 'L':
                    return pd.DataFrame([
                        {'ts_code': '600001.SH', 'symbol': '600001', 'name': 'Existing Overlap Asset', 'list_date': '20100105', 'list_status': 'L'},
                        {'ts_code': '600002.SH', 'symbol': '600002', 'name': 'CSI 300 Only Asset', 'list_date': '20120608', 'list_status': 'L'},
                    ])
                return pd.DataFrame([])

            def index_weight(self, **kwargs):
                self.index_weight_calls.append((kwargs['index_code'], kwargs['start_date'], kwargs['end_date']))
                if kwargs['start_date'] == '20260101':
                    return pd.DataFrame([
                        {'trade_date': '20260102', 'con_code': '600001.SH', 'weight': 4.2},
                    ])
                if kwargs['start_date'] == '20260103':
                    return pd.DataFrame([
                        {'trade_date': '20260105', 'con_code': '600002.SH', 'weight': 3.9},
                    ])
                return pd.DataFrame([])

        stub_pro = StubPro()
        mock_pro_api.return_value = stub_pro

        with patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'), patch('apps.markets.tasks.INDEX_WEIGHT_SYNC_WINDOW_DAYS', 2):
            from .tasks import sync_index_constituent_universe

            summary = sync_index_constituent_universe(
                index_codes=('000300.SH',),
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 5),
                dispatch_assets=False,
            )

        self.assertEqual(
            stub_pro.index_weight_calls,
            [
                ('399300.SZ', '20260101', '20260102'),
                ('399300.SZ', '20260103', '20260104'),
                ('399300.SZ', '20260105', '20260105'),
            ],
        )
        self.assertEqual(summary['historical_membership_rows_seen'], 2)
        self.assertEqual(summary['membership_rows_created'], 2)
        self.assertEqual(summary['latest_trade_dates']['000300.SH'], '20260105')
        self.assertTrue(IndexMembership.objects.filter(index_code='000300.SH', trade_date='2026-01-02').exists())
        self.assertTrue(IndexMembership.objects.filter(index_code='000300.SH', trade_date='2026-01-05').exists())

    @patch('apps.markets.tasks.time.sleep')
    @patch('apps.markets.tasks.ts.pro_api')
    def test_sync_index_constituent_universe_retries_tushare_rate_limit(self, mock_pro_api, mock_sleep):
        class StubPro:
            def __init__(self):
                self.index_weight_calls = 0

            def stock_basic(self, **kwargs):
                if kwargs['list_status'] == 'L':
                    return pd.DataFrame([
                        {'ts_code': '600001.SH', 'symbol': '600001', 'name': 'Existing Overlap Asset', 'list_date': '20100105', 'list_status': 'L'},
                    ])
                return pd.DataFrame([])

            def index_weight(self, **kwargs):
                self.index_weight_calls += 1
                if self.index_weight_calls == 1:
                    raise Exception('频率超限')
                return pd.DataFrame([
                    {'trade_date': '20260104', 'con_code': '600001.SH', 'weight': 4.2},
                ])

        stub_pro = StubPro()
        mock_pro_api.return_value = stub_pro

        with patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'), patch('apps.markets.tasks.INDEX_WEIGHT_REQUEST_SLEEP_SECONDS', 0), patch('apps.markets.tasks.INDEX_WEIGHT_RETRY_SLEEP_SECONDS', 0):
            from .tasks import sync_index_constituent_universe

            summary = sync_index_constituent_universe(
                index_codes=('000300.SH',),
                start_date=date(2026, 1, 4),
                end_date=date(2026, 1, 4),
                dispatch_assets=False,
            )

        self.assertEqual(stub_pro.index_weight_calls, 2)
        self.assertEqual(mock_sleep.call_count, 1)
        self.assertEqual(summary['membership_rows_created'], 1)
        self.assertEqual(summary['latest_trade_dates']['000300.SH'], '20260104')

    @patch('apps.markets.tasks.sync_asset_suspensions')
    @patch('apps.markets.tasks.sync_exchange_trading_calendar')
    @patch('apps.markets.tasks.sync_benchmark_index_history')
    @patch('apps.markets.tasks.chord')
    @patch('apps.markets.tasks.sync_asset_history.s')
    @patch('apps.markets.tasks.sync_index_constituent_universe')
    def test_sync_daily_a_shares_queues_post_sync_refresh_after_asset_syncs(
        self,
        mock_sync_universe,
        mock_signature,
        mock_chord,
        mock_benchmark_sync,
        mock_calendar_sync,
        mock_suspension_sync,
    ):
        mock_calendar_sync.return_value = {
            'latest_trade_dates': {'SSE': '2026-04-25', 'SZSE': '2026-04-25'},
            'rows_written': 2,
        }
        mock_benchmark_sync.return_value = {
            'latest_trade_dates': {'000300.SH': '20260425', '000510.CSI': '20260425'},
            'rows_written': 3,
        }
        mock_suspension_sync.return_value = {
            'rows_written': 1,
            'full_day_rows': 1,
        }
        mock_sync_universe.return_value = {
            'current_union_count': 3,
            'overlap_count': 1,
            'current_constituent_counts': {'000300.SH': 2, '000510.CSI': 2},
            'current_union_ts_codes': ['000001.SZ', '600001.SH', '600002.SH'],
        }
        mock_signature.side_effect = ['sig-a', 'sig-b', 'sig-c']
        mock_chord_runner = MagicMock()
        mock_chord.return_value = mock_chord_runner

        Asset.objects.create(
            market=self.sse,
            symbol='600002',
            ts_code='600002.SH',
            name='CSI 300 Only Asset',
        )
        Asset.objects.create(
            market=self.szse,
            symbol='000001',
            ts_code='000001.SZ',
            name='CSI A500 Only Asset',
        )

        result = sync_daily_a_shares(target_date='2026-04-25')

        self.assertIn('Dispatched 3 tasks', result)
        self.assertEqual(mock_signature.call_count, 3)
        mock_calendar_sync.assert_called_once_with(exchange_codes=('SSE', 'SZSE'), start_date=date(2026, 4, 25), end_date=date(2026, 4, 25))
        mock_benchmark_sync.assert_called_once()
        mock_suspension_sync.assert_called_once_with(start_date=date(2026, 4, 25), end_date=date(2026, 4, 25), ts_codes=['000001.SZ', '600001.SH', '600002.SH'])
        mock_chord.assert_called_once_with(['sig-a', 'sig-b', 'sig-c'])
        mock_chord_runner.assert_called_once()

    @patch('apps.markets.tasks.sync_asset_suspensions')
    @patch('apps.markets.tasks.sync_exchange_trading_calendar')
    @patch('apps.markets.tasks.sync_benchmark_index_history')
    @patch('apps.markets.tasks.chord')
    @patch('apps.markets.tasks.sync_asset_history.s')
    @patch('apps.markets.tasks.sync_index_constituent_universe')
    def test_sync_daily_a_shares_adds_warmup_repairs_for_new_pre_floor_union_assets(
        self,
        mock_sync_universe,
        mock_signature,
        mock_chord,
        mock_benchmark_sync,
        mock_calendar_sync,
        mock_suspension_sync,
    ):
        mock_calendar_sync.return_value = {
            'latest_trade_dates': {'SSE': '2026-04-25', 'SZSE': '2026-04-25'},
            'rows_written': 2,
        }
        mock_benchmark_sync.return_value = {
            'latest_trade_dates': {'000300.SH': '20260425', '000510.CSI': '20260425'},
            'rows_written': 3,
        }
        mock_suspension_sync.return_value = {
            'rows_written': 0,
            'full_day_rows': 0,
        }
        mock_sync_universe.return_value = {
            'current_union_count': 1,
            'overlap_count': 0,
            'current_constituent_counts': {'000300.SH': 1, '000510.CSI': 0},
            'current_union_ts_codes': ['600001.SH'],
            'new_current_union_ts_codes': ['600001.SH'],
        }
        mock_signature.side_effect = ['sig-standard', 'sig-warmup']
        mock_chord_runner = MagicMock()
        mock_chord.return_value = mock_chord_runner

        Asset.objects.filter(pk=self.existing_asset.pk).update(list_date=date(1993, 1, 1))
        self.existing_asset.refresh_from_db()

        result = sync_daily_a_shares(target_date='2026-04-25')

        self.assertIn('Dispatched 2 tasks', result)
        self.assertEqual(mock_signature.call_count, 2)

        standard_call = mock_signature.call_args_list[0]
        self.assertEqual(standard_call.args[:4], ('600001', 'Existing Overlap Asset', 'SSE', False))

        warmup_call = mock_signature.call_args_list[1]
        self.assertEqual(warmup_call.args[:4], ('600001', 'Existing Overlap Asset', 'SSE', True))
        self.assertEqual(warmup_call.kwargs['repair_end_date'], '2026-04-25')
        self.assertTrue(warmup_call.kwargs['allow_pre_floor_repair'])
        self.assertLess(warmup_call.kwargs['repair_start_date'], '2026-04-25')

        mock_chord.assert_called_once_with(['sig-standard', 'sig-warmup'])
        mock_chord_runner.assert_called_once()

    @patch('apps.markets.tasks.sync_asset_suspensions')
    @patch('apps.markets.tasks.sync_exchange_trading_calendar')
    @patch('apps.markets.tasks.sync_benchmark_index_history')
    @patch('apps.markets.tasks.run_post_sync_universal_refresh.delay')
    @patch('apps.markets.tasks.sync_index_constituent_universe')
    def test_sync_daily_a_shares_runs_post_sync_refresh_directly_when_no_signatures(
        self,
        mock_sync_universe,
        mock_refresh_delay,
        mock_benchmark_sync,
        mock_calendar_sync,
        mock_suspension_sync,
    ):
        mock_calendar_sync.return_value = {
            'latest_trade_dates': {'SSE': '2026-04-25', 'SZSE': '2026-04-25'},
            'rows_written': 2,
        }
        mock_benchmark_sync.return_value = {
            'latest_trade_dates': {'000300.SH': '20260425', '000510.CSI': '20260425'},
            'rows_written': 2,
        }
        mock_suspension_sync.return_value = {
            'rows_written': 0,
            'full_day_rows': 0,
        }
        mock_sync_universe.return_value = {
            'current_union_count': 1,
            'overlap_count': 0,
            'current_constituent_counts': {'000300.SH': 1, '000510.CSI': 0},
            'current_union_ts_codes': ['600999.SH'],
        }

        result = sync_daily_a_shares(target_date='2026-04-25')

        self.assertIn('queued post-sync refresh', result)
        mock_calendar_sync.assert_called_once_with(exchange_codes=('SSE', 'SZSE'), start_date=date(2026, 4, 25), end_date=date(2026, 4, 25))
        mock_benchmark_sync.assert_called_once()
        mock_suspension_sync.assert_called_once_with(start_date=date(2026, 4, 25), end_date=date(2026, 4, 25), ts_codes=['600999.SH'])
        mock_refresh_delay.assert_called_once_with(target_date='2026-04-25')

    @patch('apps.markets.tasks.calculate_signals_for_all_assets')
    @patch('apps.markets.tasks.calculate_indicators_for_all_assets')
    @patch('apps.markets.tasks.calculate_factor_scores_for_date')
    @patch('apps.markets.tasks.sync_daily_capital_flow_snapshots')
    @patch('apps.markets.tasks.refresh_latest_point_in_time_union_benchmark')
    def test_run_post_sync_universal_refresh_executes_metric_refresh_order(
        self,
        mock_pit_benchmark,
        mock_capital_flow,
        mock_factor_scores,
        mock_technical_indicators,
        mock_signals,
    ):
        mock_pit_benchmark.return_value = 'pit-benchmark-ok'
        mock_capital_flow.return_value = 'capital-flow-ok'
        mock_factor_scores.return_value = 'factor-score-ok'
        mock_technical_indicators.return_value = 'technical-indicators-ok'
        mock_signals.return_value = 'signals-ok'

        result = run_post_sync_universal_refresh(sync_results=['ok-1', 'ok-2'], target_date='2026-04-25')

        mock_pit_benchmark.assert_called_once_with(target_date='2026-04-25')
        mock_capital_flow.assert_called_once_with(target_date='2026-04-25')
        mock_factor_scores.assert_called_once_with(target_date='2026-04-25')
        mock_technical_indicators.assert_called_once_with()
        mock_signals.assert_called_once_with()
        self.assertIn('synced_assets=2', result)
        self.assertIn('pit_benchmark=pit-benchmark-ok', result)
        self.assertIn('factor_scores=factor-score-ok', result)
        self.assertIn('technical_indicators=technical-indicators-ok', result)

    @patch('apps.markets.tasks.sync_asset_history.delay')
    @patch('apps.markets.tasks.ts.pro_api')
    def test_sync_index_constituents_can_dispatch_only_current_membership_changes(self, mock_pro_api, mock_delay):
        mock_pro_api.return_value = self._stub_pro()

        Asset.objects.create(
            market=self.sse,
            symbol='600002',
            ts_code='600002.SH',
            name='CSI 300 Only Asset',
            membership_tags=['CSI300'],
        )
        removed_asset = Asset.objects.create(
            market=self.sse,
            symbol='600003',
            ts_code='600003.SH',
            name='Removed Asset',
            membership_tags=['CSIA500'],
        )
        IndexMembership.objects.create(
            asset=removed_asset,
            index_code='000510.CSI',
            index_name='CSI A500',
            trade_date='2026-03-25',
            weight=1.1,
        )
        Asset.objects.filter(pk=self.existing_asset.pk).update(membership_tags=['CSI300'])

        with patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'):
            from .tasks import sync_index_constituent_universe
            summary = sync_index_constituent_universe(
                start_date=date(2026, 3, 25),
                end_date=date(2026, 4, 25),
                dispatch_assets=True,
                dispatch_changed_assets_only=True,
            )

        self.existing_asset.refresh_from_db()
        removed_asset.refresh_from_db()

        self.assertEqual(summary['dispatched_assets'], 2)
        dispatched_symbols = {call.args[0] for call in mock_delay.call_args_list}
        self.assertEqual(dispatched_symbols, {'600001', '000001'})
        self.assertCountEqual(self.existing_asset.membership_tags, ['CSIA500', 'CSI300'])
        self.assertEqual(removed_asset.membership_tags, [])

    @patch('apps.markets.tasks.sync_index_constituent_universe')
    def test_sync_monthly_index_memberships_uses_change_only_dispatch(self, mock_sync):
        mock_sync.return_value = {
            'current_union_count': 3,
            'overlap_count': 1,
            'current_constituent_counts': {'000300.SH': 2, '000510.CSI': 2},
            'dispatched_assets': 2,
        }

        result = sync_monthly_index_memberships()

        self.assertIn('Dispatched 2 membership-change tasks', result)
        self.assertEqual(mock_sync.call_args.kwargs['index_codes'], ('000300.SH', '000510.CSI'))
        self.assertTrue(mock_sync.call_args.kwargs['dispatch_assets'])
        self.assertFalse(mock_sync.call_args.kwargs['force_floor_backfill'])
        self.assertTrue(mock_sync.call_args.kwargs['dispatch_changed_assets_only'])

    @patch('apps.markets.tasks.ts.pro_api')
    def test_sync_benchmark_index_history_persists_rows(self, mock_pro_api):
        class StubPro:
            def index_daily(self, **kwargs):
                index_code = kwargs['ts_code']
                if index_code == '000300.SH':
                    return pd.DataFrame([
                        {'trade_date': '20260425', 'open': 3900.0, 'high': 3950.0, 'low': 3890.0, 'close': 3940.0},
                        {'trade_date': '20260424', 'open': 3880.0, 'high': 3910.0, 'low': 3870.0, 'close': 3900.0},
                    ])
                if index_code == '000510.CSI':
                    return pd.DataFrame([
                        {'trade_date': '20260425', 'open': 5000.0, 'high': 5050.0, 'low': 4980.0, 'close': 5040.0},
                    ])
                return pd.DataFrame([])

        mock_pro_api.return_value = StubPro()

        with patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'):
            summary = sync_benchmark_index_history(start_date='2026-04-24', end_date='2026-04-25')

        self.assertEqual(summary['index_codes'], ['000300.SH', '000510.CSI'])
        self.assertEqual(BenchmarkIndexDaily.objects.filter(index_code='000300.SH').count(), 2)
        self.assertEqual(BenchmarkIndexDaily.objects.filter(index_code='000510.CSI').count(), 1)
        self.assertEqual(
            BenchmarkIndexDaily.objects.get(index_code='000300.SH', trade_date='2026-04-25').close,
            Decimal('3940.0'),
        )


class PointInTimeUniverseResolutionTests(TestCase):
    def setUp(self):
        self.sse = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        self.szse = Market.objects.create(code='SZSE', name='Shenzhen Stock Exchange')
        self.overlap_asset = Asset.objects.create(
            market=self.sse,
            symbol='600001',
            ts_code='600001.SH',
            name='Overlap Asset',
        )
        self.csi300_only_asset = Asset.objects.create(
            market=self.sse,
            symbol='600002',
            ts_code='600002.SH',
            name='CSI 300 Only Asset',
        )
        self.a500_only_asset = Asset.objects.create(
            market=self.szse,
            symbol='000001',
            ts_code='000001.SZ',
            name='CSI A500 Only Asset',
        )

        IndexMembership.objects.bulk_create([
            IndexMembership(
                asset=self.overlap_asset,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date='2026-04-01',
                weight=Decimal('4.20'),
            ),
            IndexMembership(
                asset=self.csi300_only_asset,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date='2026-04-01',
                weight=Decimal('3.10'),
            ),
            IndexMembership(
                asset=self.overlap_asset,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date='2026-04-15',
                weight=Decimal('4.80'),
            ),
            IndexMembership(
                asset=self.csi300_only_asset,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date='2026-04-15',
                weight=Decimal('3.40'),
            ),
            IndexMembership(
                asset=self.a500_only_asset,
                index_code='000510.CSI',
                index_name='CSI A500',
                trade_date='2026-04-10',
                weight=Decimal('2.60'),
            ),
            IndexMembership(
                asset=self.overlap_asset,
                index_code='000510.CSI',
                index_name='CSI A500',
                trade_date='2026-04-10',
                weight=Decimal('1.90'),
            ),
        ])

    def test_resolve_point_in_time_union_membership_uses_latest_snapshot_on_or_before_date(self):
        payload = resolve_point_in_time_union_membership(date(2026, 4, 20))

        self.assertEqual(payload['snapshot_dates']['000300.SH'], '2026-04-15')
        self.assertEqual(payload['snapshot_dates']['000510.CSI'], '2026-04-10')
        self.assertEqual(payload['constituent_count'], 3)
        self.assertEqual(payload['overlap_count'], 1)
        self.assertCountEqual(
            payload['asset_ids'],
            [self.overlap_asset.id, self.csi300_only_asset.id, self.a500_only_asset.id],
        )

    def test_resolve_point_in_time_union_membership_dedupes_overlap_assets(self):
        payload = resolve_point_in_time_union_membership(date(2026, 4, 20))

        overlap_row = next(item for item in payload['constituents'] if item['asset_id'] == self.overlap_asset.id)
        self.assertCountEqual(overlap_row['index_codes'], ['000300.SH', '000510.CSI'])
        self.assertEqual(overlap_row['snapshot_dates']['000300.SH'], '2026-04-15')
        self.assertEqual(overlap_row['snapshot_dates']['000510.CSI'], '2026-04-10')
        self.assertEqual(overlap_row['membership_weights']['000300.SH'], 4.8)
        self.assertEqual(overlap_row['membership_weights']['000510.CSI'], 1.9)

    def test_point_in_time_union_asset_ids_returns_empty_when_no_prior_snapshot_exists(self):
        self.assertEqual(point_in_time_union_asset_ids(date(2026, 3, 1)), [])

    def test_required_pit_index_codes_uses_inclusive_2010_floor_and_csi300_only_before_a500_launch(self):
        self.assertEqual(required_pit_index_codes_for_date(date(2010, 1, 3)), ())
        self.assertEqual(required_pit_index_codes_for_date(date(2010, 1, 4)), ('000300.SH',))
        self.assertEqual(required_pit_index_codes_for_date(date(2024, 9, 22)), ('000300.SH',))
        self.assertEqual(required_pit_index_codes_for_date(date(2024, 9, 23)), ('000300.SH', '000510.CSI'))

    def test_ensure_pit_membership_coverage_allows_pre_launch_csi300_only_but_requires_a500_after_launch(self):
        pre_launch_date = date(2024, 9, 20)
        launch_date = date(2024, 9, 23)

        IndexMembership.objects.create(
            asset=self.overlap_asset,
            index_code='000300.SH',
            index_name='CSI 300',
            trade_date=pre_launch_date,
            weight=Decimal('4.20'),
        )

        ensure_pit_membership_coverage([pre_launch_date], context='PIT test pre-launch')

        with self.assertRaisesMessage(
            PITMembershipCoverageError,
            'missing point-in-time membership coverage for 000510.CSI on 2024-09-23',
        ):
            ensure_pit_membership_coverage([launch_date], context='PIT test launch-day')

    def test_point_in_time_benchmark_daily_persists_internal_benchmark_rows(self):
        row = PointInTimeBenchmarkDaily.objects.create(
            benchmark_code='CSI300_CSIA500_PIT_UNION',
            benchmark_name='CSI300 + CSI A500 PIT Union',
            trade_date='2026-04-20',
            daily_return=Decimal('0.01234567'),
            nav=Decimal('101234.56780000'),
            constituent_count=3,
            overlap_count=1,
            metadata={'snapshot_dates': {'000300.SH': '2026-04-15', '000510.CSI': '2026-04-10'}},
        )

        self.assertEqual(str(row), 'CSI300_CSIA500_PIT_UNION on 2026-04-20')


class PointInTimeBenchmarkPrecomputeTests(TestCase):
    def setUp(self):
        self.sse = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        self.szse = Market.objects.create(code='SZSE', name='Shenzhen Stock Exchange')
        self.overlap_asset = Asset.objects.create(
            market=self.sse,
            symbol='600001',
            ts_code='600001.SH',
            name='Overlap Asset',
        )
        self.csi300_only_asset = Asset.objects.create(
            market=self.sse,
            symbol='600002',
            ts_code='600002.SH',
            name='CSI 300 Only Asset',
        )

        IndexMembership.objects.bulk_create([
            IndexMembership(
                asset=self.overlap_asset,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date='2026-04-20',
                weight=Decimal('4.80'),
            ),
            IndexMembership(
                asset=self.csi300_only_asset,
                index_code='000300.SH',
                index_name='CSI 300',
                trade_date='2026-04-20',
                weight=Decimal('3.40'),
            ),
            IndexMembership(
                asset=self.overlap_asset,
                index_code='000510.CSI',
                index_name='CSI A500',
                trade_date='2026-04-20',
                weight=Decimal('1.90'),
            ),
        ])

        for trade_date, overlap_close, csi300_close in [
            ('2026-04-20', '10.0', '20.0'),
            ('2026-04-21', '12.0', '18.0'),
        ]:
            OHLCV.objects.create(
                asset=self.overlap_asset,
                date=trade_date,
                open=Decimal(overlap_close),
                high=Decimal(overlap_close),
                low=Decimal(overlap_close),
                close=Decimal(overlap_close),
                adj_close=Decimal(overlap_close),
                volume=1000000,
                amount=Decimal(overlap_close) * Decimal('1000000'),
            )
            OHLCV.objects.create(
                asset=self.csi300_only_asset,
                date=trade_date,
                open=Decimal(csi300_close),
                high=Decimal(csi300_close),
                low=Decimal(csi300_close),
                close=Decimal(csi300_close),
                adj_close=Decimal(csi300_close),
                volume=1000000,
                amount=Decimal(csi300_close) * Decimal('1000000'),
            )

        from apps.factors.models import FundamentalFactorSnapshot

        FundamentalFactorSnapshot.objects.bulk_create([
            FundamentalFactorSnapshot(
                asset=self.overlap_asset,
                date='2026-04-20',
                pe=Decimal('8.0'),
                pb=Decimal('1.1'),
                free_share=Decimal('60.0'),
                circ_mv=Decimal('600.0'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.01'),
            ),
            FundamentalFactorSnapshot(
                asset=self.csi300_only_asset,
                date='2026-04-20',
                pe=Decimal('9.0'),
                pb=Decimal('1.2'),
                free_share=Decimal('40.0'),
                circ_mv=Decimal('800.0'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.01'),
            ),
            FundamentalFactorSnapshot(
                asset=self.overlap_asset,
                date='2026-04-21',
                pe=Decimal('8.2'),
                pb=Decimal('1.1'),
                free_share=Decimal('60.0'),
                circ_mv=Decimal('720.0'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.01'),
            ),
            FundamentalFactorSnapshot(
                asset=self.csi300_only_asset,
                date='2026-04-21',
                pe=Decimal('8.8'),
                pb=Decimal('1.2'),
                free_share=Decimal('40.0'),
                circ_mv=Decimal('720.0'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.01'),
            ),
        ])

    def test_build_point_in_time_union_benchmark_rows_uses_free_float_market_cap_weights(self):
        rows = build_point_in_time_union_benchmark_rows(
            start_date=date(2026, 4, 20),
            end_date=date(2026, 4, 21),
            initial_nav=Decimal('100000'),
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].trade_date.isoformat(), '2026-04-20')
        self.assertEqual(rows[0].nav, Decimal('100000'))
        self.assertEqual(rows[1].trade_date.isoformat(), '2026-04-21')
        self.assertAlmostEqual(float(rows[1].daily_return), 0.05, places=8)
        self.assertAlmostEqual(float(rows[1].nav), 105000.0, places=6)
        self.assertEqual(rows[1].constituent_count, 2)
        self.assertEqual(rows[1].overlap_count, 1)
        self.assertEqual(rows[1].metadata['weighted_constituent_count'], 2)

    def test_build_pit_union_benchmark_command_persists_rows(self):
        output = StringIO()

        call_command(
            'build_pit_union_benchmark',
            start_date='2026-04-20',
            end_date='2026-04-21',
            initial_nav='100000',
            stdout=output,
        )

        rows = list(PointInTimeBenchmarkDaily.objects.order_by('trade_date'))
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(float(rows[1].daily_return), 0.05, places=8)
        self.assertIn('rows_written=2', output.getvalue())

    def test_refresh_point_in_time_union_benchmark_updates_existing_rows(self):
        refresh_point_in_time_union_benchmark(
            start_date='2026-04-20',
            end_date='2026-04-21',
            initial_nav=Decimal('100000'),
        )

        OHLCV.objects.filter(asset=self.csi300_only_asset, date='2026-04-21').update(close=Decimal('20.0'))
        summary = refresh_point_in_time_union_benchmark(
            start_date='2026-04-20',
            end_date='2026-04-21',
            initial_nav=Decimal('100000'),
        )

        refreshed = PointInTimeBenchmarkDaily.objects.get(trade_date='2026-04-21')
        self.assertEqual(summary['rows_written'], 2)
        self.assertAlmostEqual(float(refreshed.daily_return), 0.09473684, places=8)

    def test_refresh_latest_point_in_time_union_benchmark_continues_from_previous_nav(self):
        refresh_point_in_time_union_benchmark(
            start_date='2026-04-20',
            end_date='2026-04-21',
            initial_nav=Decimal('100000'),
        )

        OHLCV.objects.create(
            asset=self.overlap_asset,
            date='2026-04-22',
            open=Decimal('13.2'),
            high=Decimal('13.2'),
            low=Decimal('13.2'),
            close=Decimal('13.2'),
            adj_close=Decimal('13.2'),
            volume=1000000,
            amount=Decimal('13200000'),
        )
        OHLCV.objects.create(
            asset=self.csi300_only_asset,
            date='2026-04-22',
            open=Decimal('19.8'),
            high=Decimal('19.8'),
            low=Decimal('19.8'),
            close=Decimal('19.8'),
            adj_close=Decimal('19.8'),
            volume=1000000,
            amount=Decimal('19800000'),
        )

        from apps.factors.models import FundamentalFactorSnapshot

        FundamentalFactorSnapshot.objects.bulk_create([
            FundamentalFactorSnapshot(
                asset=self.overlap_asset,
                date='2026-04-22',
                pe=Decimal('8.1'),
                pb=Decimal('1.1'),
                free_share=Decimal('60.0'),
                circ_mv=Decimal('792.0'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.01'),
            ),
            FundamentalFactorSnapshot(
                asset=self.csi300_only_asset,
                date='2026-04-22',
                pe=Decimal('8.7'),
                pb=Decimal('1.2'),
                free_share=Decimal('40.0'),
                circ_mv=Decimal('792.0'),
                roe=Decimal('0.1'),
                roe_qoq=Decimal('0.01'),
            ),
        ])

        summary = refresh_latest_point_in_time_union_benchmark(target_date='2026-04-22')

        latest_row = PointInTimeBenchmarkDaily.objects.get(trade_date='2026-04-22')
        self.assertEqual(summary['refresh_mode'], 'incremental')
        self.assertEqual(summary['seed_trade_date'], '2026-04-21')
        self.assertEqual(summary['rows_written'], 2)
        self.assertAlmostEqual(float(latest_row.daily_return), 0.1, places=8)
        self.assertAlmostEqual(float(latest_row.nav), 115500.0, places=6)


class UniverseOnboardingCommandTests(TestCase):
    def setUp(self):
        self.sse = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        self.szse = Market.objects.create(code='SZSE', name='Shenzhen Stock Exchange')
        Asset.objects.create(
            market=self.sse,
            symbol='600001',
            ts_code='600001.SH',
            name='Overlap Asset',
            membership_tags=['CSI300', 'CSIA500'],
        )
        Asset.objects.create(
            market=self.szse,
            symbol='000001',
            ts_code='000001.SZ',
            name='A500 Only Asset',
            membership_tags=['CSIA500'],
        )
        Asset.objects.create(
            market=self.sse,
            symbol='600002',
            ts_code='600002.SH',
            name='CSI300 Only Asset',
            membership_tags=['CSI300'],
        )

    @patch('apps.markets.management.commands.onboard_csi_a500_universe.call_command')
    def test_onboard_csi_a500_universe_runs_expected_subcommands(self, mock_call_command):
        output = StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'onboard_csi_a500_universe',
                start_date='2020-01-01',
                end_date='2020-12-31',
                benchmark_start_date='2020-07-01',
                benchmark_end_date='2020-12-31',
                report_label='a500_rollout_test',
                report_root_dir=temp_dir,
                stdout=output,
            )

            manifest_path = Path(temp_dir) / 'a500_rollout_test' / 'rollout_manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))

        command_names = [call.args[0] for call in mock_call_command.call_args_list]
        self.assertEqual(
            command_names,
            [
                'run_reference_benchmark_suite',
                'sync_index_constituents',
                'backfill_ohlcv_history',
                'backfill_ohlcv_history',
                'backfill_fundamental_snapshots',
                'backfill_capital_flow_snapshots',
                'backfill_technical_indicators',
                'build_pit_union_benchmark',
                'backfill_model_data',
                'rebuild_lightgbm_pipeline',
                'rebuild_lstm_pipeline',
                'run_reference_benchmark_suite',
            ],
        )

        sync_kwargs = mock_call_command.call_args_list[1].kwargs
        self.assertTrue(sync_kwargs['skip_sync_dispatch'])
        self.assertEqual(sync_kwargs['index_codes'], '000300.SH,000510.CSI')

        targeted_ohlcv_kwargs = mock_call_command.call_args_list[2].kwargs
        self.assertEqual(targeted_ohlcv_kwargs['symbols'], '000001')
        self.assertTrue(targeted_ohlcv_kwargs['technical_indicator_warmup'])

        entry_warmup_kwargs = mock_call_command.call_args_list[3].kwargs
        self.assertTrue(entry_warmup_kwargs['effective_universe_entry_warmup'])
        self.assertEqual(entry_warmup_kwargs['start_date'], '2020-01-01')
        self.assertEqual(entry_warmup_kwargs['end_date'], '2020-12-31')

        for call_args in mock_call_command.call_args_list[4:6]:
            self.assertEqual(call_args.kwargs['symbols'], '000001')

        technical_backfill_kwargs = mock_call_command.call_args_list[6].kwargs
        self.assertEqual(technical_backfill_kwargs['start_date'], '2020-01-01')
        self.assertEqual(technical_backfill_kwargs['end_date'], '2020-12-31')

        pit_benchmark_kwargs = mock_call_command.call_args_list[7].kwargs
        self.assertEqual(pit_benchmark_kwargs['start_date'], '2020-01-01')
        self.assertEqual(pit_benchmark_kwargs['end_date'], '2020-12-31')

        self.assertTrue(mock_call_command.call_args_list[9].kwargs['skip_backfill'])
        self.assertTrue(mock_call_command.call_args_list[10].kwargs['skip_backfill'])

        self.assertEqual(manifest['a500_only_symbols'], ['000001'])
        self.assertEqual(manifest['pit_benchmark_window']['start_date'], '2020-01-01')
        self.assertEqual(manifest['pit_benchmark_window']['end_date'], '2020-12-31')
        self.assertTrue(manifest['pre_benchmark_output_dir'].endswith('pre_expansion'))
        self.assertTrue(manifest['post_benchmark_output_dir'].endswith('post_expansion'))
        self.assertIn('CSI A500 onboarding workflow complete.', output.getvalue())


class SafeUniverseRolloutCommandTests(TestCase):
    @patch('apps.markets.management.commands.rollout_csi_a500_universe.call_command')
    def test_rollout_csi_a500_universe_runs_safe_split_subcommands(self, mock_call_command):
        output = StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                'rollout_csi_a500_universe',
                start_date='2010-01-01',
                end_date='2026-04-27',
                retrain_start_date='2016-06-01',
                retrain_end_date='2024-12-31',
                report_label='safe_rollout_test',
                report_root_dir=temp_dir,
                stdout=output,
            )

            manifest_path = Path(temp_dir) / 'safe_rollout_test' / 'rollout_manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))

        command_names = [call.args[0] for call in mock_call_command.call_args_list]
        self.assertEqual(
            command_names,
            [
                'onboard_csi_a500_universe',
                'rebuild_lightgbm_pipeline',
                'rebuild_lstm_pipeline',
                'run_reference_benchmark_suite',
                'run_reference_benchmark_suite',
                'run_reference_benchmark_suite',
                'run_reference_benchmark_suite',
                'run_reference_benchmark_suite',
                'run_reference_benchmark_suite',
            ],
        )

        onboarding_kwargs = mock_call_command.call_args_list[0].kwargs
        self.assertEqual(onboarding_kwargs['start_date'], '2010-01-01')
        self.assertEqual(onboarding_kwargs['end_date'], '2026-04-27')
        self.assertTrue(onboarding_kwargs['skip_pre_benchmarks'])
        self.assertTrue(onboarding_kwargs['skip_retrain'])
        self.assertTrue(onboarding_kwargs['skip_post_benchmarks'])
        self.assertEqual(onboarding_kwargs['report_label'], 'onboarding')

        lightgbm_kwargs = mock_call_command.call_args_list[1].kwargs
        self.assertEqual(lightgbm_kwargs['start_date'], '2016-06-01')
        self.assertEqual(lightgbm_kwargs['end_date'], '2024-12-31')
        self.assertEqual(lightgbm_kwargs['horizons'], '3,7,30')
        self.assertTrue(lightgbm_kwargs['skip_backfill'])

        lstm_kwargs = mock_call_command.call_args_list[2].kwargs
        self.assertEqual(lstm_kwargs['start_date'], '2016-06-01')
        self.assertEqual(lstm_kwargs['end_date'], '2024-12-31')
        self.assertEqual(lstm_kwargs['horizons'], '3,7,30')
        self.assertTrue(lstm_kwargs['skip_backfill'])

        suite_calls = mock_call_command.call_args_list[3:]
        suite_shapes = [
            (
                call_args.kwargs['start_date'],
                call_args.kwargs['end_date'],
                call_args.kwargs['horizon_days'],
                call_args.kwargs['window_days'],
                call_args.kwargs['step_days'],
                call_args.kwargs['queue'],
            )
            for call_args in suite_calls
        ]
        self.assertEqual(
            suite_shapes,
            [
                ('2023-01-01', '2024-12-31', 3, 731, 731, True),
                ('2023-01-01', '2024-12-31', 7, 731, 731, True),
                ('2023-01-01', '2024-12-31', 30, 731, 731, True),
                ('2025-01-01', '2025-12-31', 3, 365, 365, True),
                ('2025-01-01', '2025-12-31', 7, 365, 365, True),
                ('2025-01-01', '2025-12-31', 30, 365, 365, True),
            ],
        )

        self.assertEqual(
            [suite['label'] for suite in manifest['benchmark_suites']],
            ['train_h3', 'train_h7', 'train_h30', 'test_h3', 'test_h7', 'test_h30'],
        )
        self.assertEqual(manifest['raw_backfill_window']['start_date'], '2010-01-01')
        self.assertEqual(manifest['raw_backfill_window']['end_date'], '2026-04-27')
        self.assertEqual(manifest['retrain_window']['start_date'], '2016-06-01')
        self.assertEqual(manifest['retrain_window']['end_date'], '2024-12-31')
        self.assertEqual(manifest['benchmark_launch_mode'], 'queue')
        self.assertIn('CSI A500 safe rollout workflow complete.', output.getvalue())