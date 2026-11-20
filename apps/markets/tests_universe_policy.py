"""B1 / UNI-05 / STK-11 (#197): the executable-universe policy at the PIT chokepoints.

The board layer is default-disable (the account cannot trade ChiNext/STAR);
research overrides are explicit; the batch path must equal the single-day path;
the ST layer fails closed until enabled; delisted non-ST names stay in history.
"""
from datetime import date
from unittest.mock import patch

from django.test import TestCase

from apps.markets import universe_policy as up
from apps.markets.benchmarking import (
    PITMembershipCoverageError,
    effective_universe_asset_ids,
    pit_benchmark_code_for,
    point_in_time_union_asset_ids,
    point_in_time_union_asset_ids_by_dates,
    required_pit_index_codes_for_date,
)
from apps.markets.models import Asset, IndexMembership, Market


def _asset(market, symbol, suffix, status='A', delist=None):
    return Asset.objects.create(
        market=market,
        symbol=symbol,
        ts_code=f'{symbol}.{suffix}',
        name=f'Test {symbol}',
        listing_status=status,
        delist_date=delist,
    )


class UniversePolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='SZSE', name='SZSE')
        cls.main1 = _asset(cls.market, '600001', 'SH')
        cls.main2 = _asset(cls.market, '000001', 'SZ')
        cls.chinext = _asset(cls.market, '300001', 'SZ')
        cls.star = _asset(cls.market, '688001', 'SH')
        cls.bse = _asset(cls.market, '920001', 'BJ')
        cls.delisted = _asset(cls.market, '600003', 'SH', status='D', delist=date(2021, 5, 1))
        cls.st_main = _asset(cls.market, '600002', 'SH')
        cls.snap1 = date(2020, 1, 6)
        cls.snap2 = date(2020, 2, 3)
        members = [cls.main1, cls.main2, cls.chinext, cls.star, cls.bse, cls.delisted, cls.st_main]
        for asset in members:
            IndexMembership.objects.create(asset=asset, index_code='000905.SH', trade_date=cls.snap1, weight=1.0)
        for asset in [cls.main1, cls.chinext, cls.st_main]:
            IndexMembership.objects.create(asset=asset, index_code='000905.SH', trade_date=cls.snap2, weight=1.0)

    def test_default_disables_chinext_star_bse(self):
        ids = point_in_time_union_asset_ids(date(2020, 1, 8))
        self.assertEqual(set(ids), {self.main1.id, self.main2.id, self.delisted.id, self.st_main.id})

    def test_research_full_override_includes_boards(self):
        ids = point_in_time_union_asset_ids(date(2020, 1, 8), policy=up.POLICY_RESEARCH_FULL)
        self.assertEqual(len(ids), 7)

    def test_permission_flip_includes_board(self):
        with patch.dict(up.BOARD_TRADING_PERMISSIONS, {up.BOARD_CHINEXT: True}):
            ids = point_in_time_union_asset_ids(date(2020, 1, 8))
        self.assertIn(self.chinext.id, ids)
        self.assertNotIn(self.star.id, ids)

    def test_batch_equals_single_day_across_snapshot_boundary(self):
        dates = [date(2020, 1, 8), date(2020, 2, 5)]
        for policy in (None, up.POLICY_RESEARCH_FULL):
            batch = point_in_time_union_asset_ids_by_dates(dates, policy=policy)
            for d in dates:
                single = point_in_time_union_asset_ids(d, policy=policy)
                self.assertEqual(batch[d], set(single), f'policy={policy} date={d}')

    def test_unknown_policy_raises(self):
        with self.assertRaises(up.UniversePolicyError):
            point_in_time_union_asset_ids(date(2020, 1, 8), policy='nonsense')

    def test_st_request_before_enable_raises(self):
        with self.assertRaises(up.UniversePolicyError):
            point_in_time_union_asset_ids(date(2020, 1, 8), st_layer=True)

    def test_st_layer_excludes_st_and_retains_delisted_non_st(self):
        with patch.object(up, 'ST_LAYER_ENABLED', True):
            ids = point_in_time_union_asset_ids(
                date(2020, 1, 8),
                policy_caches={'st_asset_ids': {self.st_main.id}},
            )
        self.assertNotIn(self.st_main.id, ids)
        self.assertIn(self.delisted.id, ids)  # survivorship discipline: never read listing_status

    def test_cap_request_without_mask_raises(self):
        with self.assertRaises(up.UniversePolicyError):
            point_in_time_union_asset_ids(date(2020, 1, 8), policy=up.POLICY_RESEARCH_CAP)

    def test_cap_mask_excludes_listed_ids(self):
        ids = point_in_time_union_asset_ids(
            date(2020, 1, 8),
            policy=up.POLICY_RESEARCH_CAP,
            policy_caches={'cap_asset_ids': {self.main2.id}},
        )
        self.assertNotIn(self.main2.id, ids)
        self.assertIn(self.main1.id, ids)

    def test_coverage_fail_closed_unchanged(self):
        # >= 2010 with no snapshot at-or-before the date: coverage is required and missing.
        with self.assertRaises(PITMembershipCoverageError):
            effective_universe_asset_ids(date(2010, 1, 4))

    def test_availability_floors_per_index(self):
        self.assertEqual(required_pit_index_codes_for_date(date(2014, 10, 30), index_codes=('000852.SH',)), ())
        self.assertEqual(required_pit_index_codes_for_date(date(2014, 10, 31), index_codes=('000852.SH',)), ('000852.SH',))
        self.assertEqual(required_pit_index_codes_for_date(date(2010, 1, 1)), ('000905.SH',))
        self.assertEqual(required_pit_index_codes_for_date(date(2009, 12, 31)), ())

    def test_benchmark_code_scheme(self):
        self.assertEqual(pit_benchmark_code_for(('000905.SH',), policy=up.POLICY_RESEARCH_FULL), 'CSI500_PIT')
        self.assertEqual(pit_benchmark_code_for(('000905.SH',)), 'CSI500_PIT_EXBOARD')
        self.assertEqual(pit_benchmark_code_for(('000852.SH',)), 'CSI1000_PIT_EXBOARD')
        self.assertEqual(pit_benchmark_code_for(('000905.SH',), policy=up.POLICY_RESEARCH_CAP), 'CSI500_PIT_EXBOARD_CAP')
        with patch.object(up, 'ST_LAYER_ENABLED', True):
            self.assertEqual(pit_benchmark_code_for(('000905.SH',)), 'CSI500_PIT_EXBOARD_ST')
            self.assertEqual(pit_benchmark_code_for(('000852.SH',), st_layer=True), 'CSI1000_PIT_EXBOARD_ST')

    def test_empty_membership_returns_empty(self):
        self.assertEqual(point_in_time_union_asset_ids(date(2020, 1, 8), index_codes=('000852.SH',)), [])