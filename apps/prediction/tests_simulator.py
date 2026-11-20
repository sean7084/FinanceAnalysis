"""H2 / D10: simulator primitives reuse the engine's limit/price rules."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from apps.prediction.simulator import fill_price, is_limit_locked, raw_open_from_row


class FillPriceTests(SimpleTestCase):
    def test_buy_adds_slippage(self):
        self.assertEqual(fill_price(Decimal('10'), 5, 'buy'), Decimal('10.005'))

    def test_sell_subtracts_slippage(self):
        self.assertEqual(fill_price(Decimal('10'), 5, 'sell'), Decimal('9.995'))


class RawOpenTests(SimpleTestCase):
    def test_reconstruction_factor_cancels(self):
        self.assertEqual(raw_open_from_row(Decimal('10'), Decimal('10.5'), Decimal('21')), Decimal('20'))

    def test_legacy_null_raw_close_yields_none(self):
        self.assertIsNone(raw_open_from_row(Decimal('10'), Decimal('10.5'), None))

    def test_zero_close_yields_none(self):
        self.assertIsNone(raw_open_from_row(Decimal('10'), Decimal('0'), Decimal('21')))


class LimitLockTests(SimpleTestCase):
    def test_main_board_limit_up_locks_buy(self):
        # prev raw 10, +10% => 11.00; raw open 11 locks
        self.assertTrue(is_limit_locked(raw_open=Decimal('11'), prev_raw_close=Decimal('10'),
                                        ts_code='600001.SH', trade_date=date(2020, 1, 2), side='buy'))
        self.assertFalse(is_limit_locked(raw_open=Decimal('10.99'), prev_raw_close=Decimal('10'),
                                         ts_code='600001.SH', trade_date=date(2020, 1, 2), side='buy'))

    def test_limit_down_locks_sell(self):
        # prev raw 10, -10% => 9.00; raw open 9 locks sell
        self.assertTrue(is_limit_locked(raw_open=Decimal('9'), prev_raw_close=Decimal('10'),
                                        ts_code='600001.SH', trade_date=date(2020, 1, 2), side='sell'))
        self.assertFalse(is_limit_locked(raw_open=Decimal('9.01'), prev_raw_close=Decimal('10'),
                                         ts_code='600001.SH', trade_date=date(2020, 1, 2), side='sell'))

    def test_st_main_board_narrows_to_five(self):
        # prev raw 10, ST +5% => 10.50; raw open 10.5 locks
        self.assertTrue(is_limit_locked(raw_open=Decimal('10.5'), prev_raw_close=Decimal('10'),
                                        ts_code='600001.SH', trade_date=date(2020, 1, 2), side='buy', is_st=True))

    def test_chinext_st_keeps_twenty(self):
        # ChiNext 20%: prev raw 10 => 12.00; raw open 11 NOT locked even if ST
        self.assertFalse(is_limit_locked(raw_open=Decimal('11'), prev_raw_close=Decimal('10'),
                                         ts_code='300001.SZ', trade_date=date(2021, 1, 4), side='buy', is_st=True))

    def test_new_listing_free_not_locked(self):
        self.assertFalse(is_limit_locked(raw_open=Decimal('15'), prev_raw_close=Decimal('10'),
                                         ts_code='600001.SH', trade_date=date(2020, 1, 2), side='buy',
                                         sessions_since_listing=2))

    def test_missing_prices_not_locked(self):
        self.assertFalse(is_limit_locked(raw_open=None, prev_raw_close=Decimal('10'),
                                         ts_code='600001.SH', trade_date=date(2020, 1, 2), side='buy'))


class SimulateTopNTests(SimpleTestCase):
    def _panel(self):
        import pandas as pd
        dates = [date(2020, 1, d) for d in (1, 2, 3, 6)]
        prices = pd.DataFrame([
            {'date': d, 'asset_id': 1, 'open': Decimal(10) + Decimal(i) * Decimal('0.5'), 'close': Decimal(10) + Decimal(i) * Decimal('0.5'), 'raw_close': Decimal(10) + Decimal(i) * Decimal('0.5')}
            for i, d in enumerate(dates)
        ] + [
            {'date': d, 'asset_id': 2, 'open': Decimal(20), 'close': Decimal(20), 'raw_close': Decimal(20)}
            for d in dates
        ])
        predictions = pd.DataFrame([
            {'date': dates[0], 'asset_id': 1, 'up_prob': 0.9},
            {'date': dates[0], 'asset_id': 2, 'up_prob': 0.6},
        ])
        return predictions, prices, dates

    def test_next_day_open_top1_return(self):
        from apps.prediction.simulator import simulate_topn_returns
        predictions, prices, dates = self._panel()
        out = simulate_topn_returns(predictions, prices, top_n=1, up_threshold=0.55,
                                    ts_code_map={1: '600001.SH', 2: '600002.SH'})
        row = out[out['date'] == dates[0]].iloc[0]
        self.assertEqual(row['n_selected'], 1)
        gross = 11.0 / 10.5 - 1.0  # entry d2 open 10.5, exit d3 open 11 (< 10% limit)
        cost = 2 * 0.0005 + 2 * 0.00025 + 0.001 + 0.00001  # slip + commission + stamp + transfer (O4)
        self.assertAlmostEqual(row['portfolio_return'], gross - cost, places=6)

    def test_threshold_filters_all(self):
        from apps.prediction.simulator import simulate_topn_returns
        predictions, prices, dates = self._panel()
        out = simulate_topn_returns(predictions, prices, top_n=3, up_threshold=0.95,
                                    ts_code_map={1: '600001.SH', 2: '600002.SH'})
        row = out[out['date'] == dates[0]].iloc[0]
        self.assertEqual(row['n_selected'], 0)
        self.assertEqual(row['portfolio_return'], 0.0)

    def test_suspension_skips_asset(self):
        from apps.prediction.simulator import simulate_topn_returns
        predictions, prices, dates = self._panel()
        out = simulate_topn_returns(predictions, prices, top_n=1, up_threshold=0.55,
                                    ts_code_map={1: '600001.SH'}, suspension_set={(1, dates[1])})
        row = out[out['date'] == dates[0]].iloc[0]
        self.assertEqual(row['n_selected'], 0)  # asset 1 suspended at entry, asset 2 below top_n=1