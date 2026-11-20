"""A4 / STK-05 (#193): execution realism — incremental pieces.

Pins the as-of ST limit override, the raw-price detail map reconstruction, and
the execution_model parameter validation. The pending-fill engine surgery
(next_day_open fills, limit-lock retries, suspension deferral) lands on top.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.backtest.models import BacktestRun
from apps.backtest.serializers import BacktestRunSerializer
from apps.backtest.tasks import _build_ohlcv_detail_map, clear_backtest_process_caches
from apps.markets.models import Asset, Market, OHLCV
from apps.markets.price_limits import limit_pct_for


class LimitOverrideTests(TestCase):
    def test_main_board_st_narrows_to_five(self):
        self.assertEqual(limit_pct_for('600001.SH', date(2020, 1, 2), is_st=True), Decimal('0.05'))
        self.assertEqual(limit_pct_for('600001.SH', date(2020, 1, 2), name='ST测试'), Decimal('0.05'))
        self.assertEqual(limit_pct_for('600001.SH', date(2020, 1, 2)), Decimal('0.10'))

    def test_chinext_star_st_keeps_board_limit(self):
        self.assertEqual(limit_pct_for('300001.SZ', date(2021, 1, 4), is_st=True), Decimal('0.20'))
        self.assertEqual(limit_pct_for('688001.SH', date(2021, 1, 4), is_st=True), Decimal('0.20'))
        self.assertEqual(limit_pct_for('300001.SZ', date(2019, 1, 2), is_st=True), Decimal('0.10'))

    def test_new_listing_free_sessions(self):
        self.assertIsNone(limit_pct_for('600001.SH', date(2020, 1, 2), sessions_since_listing=2, is_st=True))


class DetailMapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='SZSE', name='SZSE')
        cls.asset = Asset.objects.create(market=cls.market, symbol='600001', ts_code='600001.SH', name='T')
        cls.d = date(2020, 1, 2)
        OHLCV.objects.create(
            asset=cls.asset, date=cls.d,
            open=Decimal('10.0'), high=Decimal('11.0'), low=Decimal('9.0'), close=Decimal('10.5'),
            adj_close=Decimal('10.5'),
            raw_close=Decimal('21.0'), adj_factor=Decimal('0.5'),
            volume=100, amount=1000,
        )

    def tearDown(self):
        clear_backtest_process_caches()

    def test_raw_open_reconstruction(self):
        detail = _build_ohlcv_detail_map(self.d, self.d)
        row = detail[(self.asset.id, self.d)]
        raw_open = row['open'] * row['raw_close'] / row['close']
        self.assertEqual(raw_open, Decimal('20.0'))

    def test_legacy_null_raw_close_yields_none(self):
        legacy_date = date(2020, 1, 3)
        OHLCV.objects.create(
            asset=self.asset, date=legacy_date,
            open=Decimal('10.0'), high=Decimal('11.0'), low=Decimal('9.0'), close=Decimal('10.5'),
            adj_close=Decimal('10.5'),
            volume=100, amount=1000,
        )
        detail = _build_ohlcv_detail_map(legacy_date, legacy_date)
        row = detail[(self.asset.id, legacy_date)]
        self.assertIsNone(row['raw_close'])
        self.assertIsNone(row['adj_factor'])


class ExecutionModelParamTests(TestCase):
    def _payload(self, **params):
        base = {'top_n': 3, 'horizon_days': 7, 'up_threshold': 0.5, 'prediction_source': 'heuristic'}
        base.update(params)
        return {
            'name': 'exec-test',
            'strategy_type': BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            'start_date': date(2020, 1, 2),
            'end_date': date(2020, 6, 30),
            'parameters': base,
        }

    def test_default_recorded(self):
        serializer = BacktestRunSerializer(data=self._payload())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['parameters']['execution_model'], 'same_day_close')

    def test_next_day_open_accepted(self):
        serializer = BacktestRunSerializer(data=self._payload(execution_model='next_day_open'))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['parameters']['execution_model'], 'next_day_open')

    def test_invalid_rejected(self):
        serializer = BacktestRunSerializer(data=self._payload(execution_model='magic'))
        self.assertFalse(serializer.is_valid())
        self.assertIn('parameters', serializer.errors)