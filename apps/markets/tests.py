import json
import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
from django.core.management import call_command
from django.test import TestCase

from .admin import AssetAdmin
from .benchmarking import build_point_in_time_union_benchmark_rows, point_in_time_union_asset_ids, refresh_latest_point_in_time_union_benchmark, refresh_point_in_time_union_benchmark, resolve_point_in_time_union_membership
from .models import Asset, BenchmarkIndexDaily, IndexMembership, Market, OHLCV, PointInTimeBenchmarkDaily
from .tasks import run_post_sync_universal_refresh, sync_benchmark_index_history, sync_daily_a_shares, sync_monthly_index_memberships


class MarketAdminAndBackfillTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='MKT', name='Market Test Exchange')
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

    def test_asset_admin_exposes_list_date(self):
        self.assertIn('list_date', AssetAdmin.list_display)
        self.assertIn('list_date', AssetAdmin.list_filter)

    @patch('apps.markets.management.commands.backfill_asset_list_dates.ts.pro_api')
    def test_backfill_asset_list_dates_populates_existing_assets(self, mock_pro_api):
        class StubPro:
            def stock_basic(self, **kwargs):
                list_status = kwargs['list_status']
                if list_status == 'L':
                    return pd.DataFrame([
                        {'ts_code': '600001.SH', 'list_date': '20100105', 'list_status': 'L'},
                    ])
                if list_status == 'D':
                    return pd.DataFrame([
                        {'ts_code': '600002.SH', 'list_date': '20040315', 'list_status': 'D'},
                    ])
                return pd.DataFrame([])

        mock_pro_api.return_value = StubPro()

        output = StringIO()
        with patch('apps.markets.management.commands.backfill_asset_list_dates.settings.TUSHARE_TOKEN', 'test-token'):
            call_command('backfill_asset_list_dates', stdout=output)

        self.active_asset.refresh_from_db()
        self.delisted_asset.refresh_from_db()

        self.assertEqual(self.active_asset.list_date.isoformat(), '2010-01-05')
        self.assertEqual(self.active_asset.listing_status, Asset.ListingStatus.ACTIVE)
        self.assertEqual(self.delisted_asset.list_date.isoformat(), '2004-03-15')
        self.assertEqual(self.delisted_asset.listing_status, Asset.ListingStatus.DELISTED)
        self.assertIn('processed=2', output.getvalue())
        self.assertIn('updated=2', output.getvalue())


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
                if index_code == '000300.SH':
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
    ):
        mock_benchmark_sync.return_value = {
            'latest_trade_dates': {'000300.SH': '20260425', '000510.CSI': '20260425'},
            'rows_written': 3,
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
        mock_benchmark_sync.assert_called_once()
        mock_chord.assert_called_once_with(['sig-a', 'sig-b', 'sig-c'])
        mock_chord_runner.assert_called_once()

    @patch('apps.markets.tasks.sync_benchmark_index_history')
    @patch('apps.markets.tasks.run_post_sync_universal_refresh.delay')
    @patch('apps.markets.tasks.sync_index_constituent_universe')
    def test_sync_daily_a_shares_runs_post_sync_refresh_directly_when_no_signatures(
        self,
        mock_sync_universe,
        mock_refresh_delay,
        mock_benchmark_sync,
    ):
        mock_benchmark_sync.return_value = {
            'latest_trade_dates': {'000300.SH': '20260425', '000510.CSI': '20260425'},
            'rows_written': 2,
        }
        mock_sync_universe.return_value = {
            'current_union_count': 1,
            'overlap_count': 0,
            'current_constituent_counts': {'000300.SH': 1, '000510.CSI': 0},
            'current_union_ts_codes': ['600999.SH'],
        }

        result = sync_daily_a_shares(target_date='2026-04-25')

        self.assertIn('queued post-sync refresh', result)
        mock_benchmark_sync.assert_called_once()
        mock_refresh_delay.assert_called_once_with(target_date='2026-04-25')

    @patch('apps.markets.tasks.calculate_signals_for_all_assets')
    @patch('apps.markets.tasks.calculate_factor_scores_for_date')
    @patch('apps.markets.tasks.sync_daily_capital_flow_snapshots')
    @patch('apps.markets.tasks.refresh_latest_point_in_time_union_benchmark')
    def test_run_post_sync_universal_refresh_executes_metric_refresh_order(
        self,
        mock_pit_benchmark,
        mock_capital_flow,
        mock_factor_scores,
        mock_signals,
    ):
        mock_pit_benchmark.return_value = 'pit-benchmark-ok'
        mock_capital_flow.return_value = 'capital-flow-ok'
        mock_factor_scores.return_value = 'factor-score-ok'
        mock_signals.return_value = 'signals-ok'

        result = run_post_sync_universal_refresh(sync_results=['ok-1', 'ok-2'], target_date='2026-04-25')

        mock_pit_benchmark.assert_called_once_with(target_date='2026-04-25')
        mock_capital_flow.assert_called_once_with(target_date='2026-04-25')
        mock_factor_scores.assert_called_once_with(target_date='2026-04-25')
        mock_signals.assert_called_once_with()
        self.assertIn('synced_assets=2', result)
        self.assertIn('pit_benchmark=pit-benchmark-ok', result)
        self.assertIn('factor_scores=factor-score-ok', result)

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
                'backfill_fundamental_snapshots',
                'backfill_capital_flow_snapshots',
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

        for call_args in mock_call_command.call_args_list[2:5]:
            self.assertEqual(call_args.kwargs['symbols'], '000001')

        pit_benchmark_kwargs = mock_call_command.call_args_list[5].kwargs
        self.assertEqual(pit_benchmark_kwargs['start_date'], '2020-01-01')
        self.assertEqual(pit_benchmark_kwargs['end_date'], '2020-12-31')

        self.assertTrue(mock_call_command.call_args_list[7].kwargs['skip_backfill'])
        self.assertTrue(mock_call_command.call_args_list[8].kwargs['skip_backfill'])

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