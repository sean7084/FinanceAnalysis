"""H2 / plan D7: universe arms as row masks over the board-level matrix.

The aux frame carries ONLY st_flag_asof / circ_mv_asof (never features); arms are
pure masks; the board_st and cap masks must equal a policy rebuild
(apply_universe_policy) on the same date so the harness cannot drift from the
serving filter. ST is resolved as-of, circ_mv as-of is the latest snapshot <= date.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from apps.factors.models import FundamentalFactorSnapshot
from apps.markets import universe_policy as up
from apps.markets.models import Asset, AssetSTInterval, Market
from apps.prediction import universe_arms as ua


def _asset(market, symbol, suffix='SH'):
    return Asset.objects.create(
        market=market, symbol=symbol, ts_code=f'{symbol}.{suffix}', name=f'Test {symbol}',
    )


class UniverseArmsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='SSE', name='SSE')
        cls.a1 = _asset(cls.market, '600001')
        cls.a2 = _asset(cls.market, '600002')
        cls.a3 = _asset(cls.market, '600003')
        cls.a4 = _asset(cls.market, '600004')
        cls.a5 = _asset(cls.market, '600005')
        cls.d1 = date(2021, 1, 4)
        cls.d2 = date(2021, 1, 5)
        # a2 is ST over [d1, d2); a3 becomes ST at d2 (open interval).
        AssetSTInterval.objects.create(asset=cls.a2, start_date=cls.d1, end_date=cls.d2)
        AssetSTInterval.objects.create(asset=cls.a3, start_date=cls.d2, end_date=None)
        # circ_mv on d1: a1=100 a2=20 a3=30 a4=40 a5=50 (bottom 30% of 5 = 1 -> a2).
        for asset, mv in [(cls.a1, 100), (cls.a2, 20), (cls.a3, 30), (cls.a4, 40), (cls.a5, 50)]:
            FundamentalFactorSnapshot.objects.create(asset=asset, date=cls.d1, circ_mv=Decimal(mv))
        cls.index = [(cls.d1, a.id) for a in (cls.a1, cls.a2, cls.a3, cls.a4, cls.a5)]

    def test_aux_frame_has_only_aux_columns(self):
        aux = ua.build_aux_frame(self.index)
        self.assertEqual(tuple(aux.columns), ua.AUX_COLUMNS)
        self.assertNotIn('st_flag', [c for c in aux.columns if c not in ua.AUX_COLUMNS])

    def test_board_only_mask_keeps_all_rows(self):
        aux = ua.build_aux_frame(self.index)
        mask = ua.arm_row_mask(aux, arm=ua.ARM_BOARD_ONLY)
        self.assertTrue(mask.all())
        self.assertEqual(len(mask), 5)

    def test_board_st_mask_drops_asof_st(self):
        aux = ua.build_aux_frame(self.index)
        mask = ua.arm_row_mask(aux, arm=ua.ARM_BOARD_ST)
        kept = {aid for (d, aid), keep in mask.items() if keep}
        # a2 is ST on d1 -> dropped; others kept.
        self.assertEqual(kept, {self.a1.id, self.a3.id, self.a4.id, self.a5.id})

    def test_st_flag_is_asof_across_dates(self):
        index = [(self.d1, self.a3.id), (self.d2, self.a3.id), (self.d2, self.a2.id)]
        aux = ua.build_aux_frame(index)
        self.assertFalse(aux.loc[(self.d1, self.a3.id), 'st_flag_asof'])
        self.assertTrue(aux.loc[(self.d2, self.a3.id), 'st_flag_asof'])
        # a2 ST interval is [d1, d2) -> NOT ST on d2.
        self.assertFalse(aux.loc[(self.d2, self.a2.id), 'st_flag_asof'])

    def test_cap_mask_drops_bottom_30_percent(self):
        aux = ua.build_aux_frame(self.index)
        mask = ua.arm_row_mask(aux, arm=ua.ARM_CAP)
        kept = {aid for (d, aid), keep in mask.items() if keep}
        # 5 assets, int(5*0.30)=1 dropped -> smallest circ_mv (a2=20) removed.
        self.assertEqual(kept, {self.a1.id, self.a3.id, self.a4.id, self.a5.id})

    def test_cap_nan_circ_mv_excluded_fail_closed(self):
        no_mv = _asset(self.market, '600009')
        index = self.index + [(self.d1, no_mv.id)]
        aux = ua.build_aux_frame(index)
        mask = ua.arm_row_mask(aux, arm=ua.ARM_CAP)
        self.assertFalse(mask.loc[(self.d1, no_mv.id)])

    def test_circ_mv_asof_uses_latest_snapshot_le_date(self):
        # A later snapshot must not leak into an earlier date.
        FundamentalFactorSnapshot.objects.create(asset=self.a1, date=self.d2, circ_mv=Decimal('999'))
        aux = ua.build_aux_frame([(self.d1, self.a1.id), (self.d2, self.a1.id)])
        self.assertEqual(aux.loc[(self.d1, self.a1.id), 'circ_mv_asof'], Decimal('100'))
        self.assertEqual(aux.loc[(self.d2, self.a1.id), 'circ_mv_asof'], Decimal('999'))

    def test_unknown_arm_raises(self):
        aux = ua.build_aux_frame(self.index)
        with self.assertRaises(ValueError):
            ua.arm_row_mask(aux, arm='nonsense')

    def test_board_st_mask_equals_policy_rebuild(self):
        aux = ua.build_aux_frame(self.index)
        mask = ua.arm_row_mask(aux, arm=ua.ARM_BOARD_ST)
        kept = {aid for (d, aid), keep in mask.items() if keep}
        board_ids = [aid for _, aid in self.index]
        with patch.object(up, 'ST_LAYER_ENABLED', True):
            policy_ids = set(up.apply_universe_policy(
                board_ids, self.d1, policy=up.POLICY_DEFAULT, st_layer=True,
            ))
        self.assertEqual(kept, policy_ids)

    def test_cap_mask_equals_policy_rebuild(self):
        aux = ua.build_aux_frame(self.index)
        mask = ua.arm_row_mask(aux, arm=ua.ARM_CAP)
        kept = {aid for (d, aid), keep in mask.items() if keep}
        board_ids = [aid for _, aid in self.index]
        cap_set = {self.a2.id}  # bottom 30% of the d1 cross-section
        policy_ids = set(up.apply_universe_policy(
            board_ids, self.d1, policy=up.POLICY_RESEARCH_CAP, cap_asset_ids=cap_set,
        ))
        self.assertEqual(kept, policy_ids)