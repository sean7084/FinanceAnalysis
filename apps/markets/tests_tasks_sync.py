"""Tests for the OHLCV sync write path.

These cover the contract the previous writer lacked: the unadjusted close and the
adjustment factor are stored alongside the qfq price, so a re-basing can be undone
locally instead of being silently baked in.

The test worth reading first is ``test_legacy_rows_block_the_rebase_entirely``.
A partial re-base is worse than none -- re-scaling only the rows that carry
``raw_close`` adds a second seam rather than removing the first -- and the
intuitive implementation ("fix what you can") is exactly the one that corrupts
more data.
"""

from decimal import Decimal
from unittest.mock import patch

import pandas as pd
from django.test import TestCase

from apps.markets.models import Asset, Market, OHLCV


def bar(trade_date, close, open_=None, high=None, low=None, vol=12.0, amount=345.0):
    return {
        'trade_date': trade_date,
        'open': close if open_ is None else open_,
        'high': close if high is None else high,
        'low': close if low is None else low,
        'close': close,
        'vol': vol,
        'amount': amount,
    }


class SyncAssetHistoryAdjustmentTests(TestCase):
    def setUp(self):
        self.market = Market.objects.create(code='SSE', name='Shanghai Stock Exchange')
        self.asset = Asset.objects.create(
            market=self.market, symbol='600001', ts_code='600001.SH', name='Active Asset',
        )

    def sync(self, daily_rows, factor_rows, **kwargs):
        class StubPro:
            def daily(self, **call_kwargs):
                return pd.DataFrame(daily_rows)

            def adj_factor(self, **call_kwargs):
                return pd.DataFrame(factor_rows)

        with patch('apps.markets.tasks.ts.pro_api', return_value=StubPro()), \
                patch('apps.markets.tasks.settings.TUSHARE_TOKEN', 'test-token'):
            from apps.markets.tasks import sync_asset_history

            return sync_asset_history('600001', 'Active Asset', 'SSE', False, **kwargs)

    def stored(self, on_date):
        return OHLCV.objects.get(asset=self.asset, date=on_date)

    def test_stores_raw_close_and_factor_beside_the_adjusted_price(self):
        result = self.sync(
            [bar('20240103', 10.00)],
            [{'trade_date': '20240103', 'adj_factor': 1.0}],
            repair_start_date='2024-01-02', repair_end_date='2024-01-05',
        )
        self.assertIn('Completed 600001', result)
        row = self.stored('2024-01-03')
        self.assertEqual(row.raw_close, Decimal('10.0000'))
        self.assertEqual(row.adj_factor, Decimal('1.000000'))
        # Newest factor is the basis, so qfq equals the traded price here.
        self.assertEqual(row.close, Decimal('10.0000'))
        self.assertEqual(row.adj_close, row.close)
        self.assertEqual(row.volume, 1200)

    def test_older_sessions_are_scaled_onto_the_newest_basis(self):
        # One session sits before a factor step: its qfq must come out below its raw
        # price, which is the whole point of forward adjustment.
        self.sync(
            [bar('20240103', 10.00), bar('20240104', 11.00)],
            [{'trade_date': '20240103', 'adj_factor': 1.0},
             {'trade_date': '20240104', 'adj_factor': 1.1}],
            repair_start_date='2024-01-02', repair_end_date='2024-01-05',
        )
        before = self.stored('2024-01-03')
        after = self.stored('2024-01-04')
        self.assertEqual(after.close, Decimal('11.0000'))
        self.assertEqual(before.close, Decimal('9.0909'))
        self.assertEqual(before.raw_close, Decimal('10.0000'))
        self.assertEqual(before.adj_factor, Decimal('1.000000'))
        # close = raw * factor / basis must hold for every row written.
        for row in (before, after):
            self.assertAlmostEqual(
                float(row.close),
                float(row.raw_close * row.adj_factor / after.adj_factor),
                places=4,
            )

    def test_a_session_missing_its_factor_inherits_the_previous_one(self):
        # adj_factor only moves on a distribution, so a blank date is not a new basis.
        self.sync(
            [bar('20240103', 10.00), bar('20240104', 10.50)],
            [{'trade_date': '20240103', 'adj_factor': 1.0}],
            repair_start_date='2024-01-02', repair_end_date='2024-01-05',
        )
        self.assertEqual(self.stored('2024-01-04').adj_factor, Decimal('1.000000'))

    def test_a_row_without_a_usable_price_is_dropped(self):
        broken = bar('20240104', 10.0)
        broken['high'] = None
        self.sync(
            [bar('20240103', 10.00), broken],
            [{'trade_date': '20240103', 'adj_factor': 1.0},
             {'trade_date': '20240104', 'adj_factor': 1.0}],
            repair_start_date='2024-01-02', repair_end_date='2024-01-05',
        )
        self.assertFalse(OHLCV.objects.filter(asset=self.asset, date='2024-01-04').exists())
        self.assertTrue(OHLCV.objects.filter(asset=self.asset, date='2024-01-03').exists())

    def test_no_factor_anywhere_refuses_to_write(self):
        result = self.sync([bar('20240103', 10.00)], [],
                           repair_start_date='2024-01-02', repair_end_date='2024-01-05')
        self.assertIn('No adjustment factor', result)
        self.assertEqual(OHLCV.objects.filter(asset=self.asset).count(), 0)

    def test_sessions_before_the_first_known_factor_are_reported_not_silent(self):
        # Carry-forward fills gaps *after* the first known factor. A factor feed that
        # starts late therefore drops the earliest sessions, and without reporting it
        # those holes look exactly like suspensions.
        result = self.sync(
            [bar('20240103', 10.00), bar('20240104', 10.50), bar('20240105', 11.00)],
            [{'trade_date': '20240105', 'adj_factor': 1.0}],
            repair_start_date='2024-01-02', repair_end_date='2024-01-05',
        )
        self.assertIn('dropped_nofactor=2', result)
        self.assertEqual(OHLCV.objects.filter(asset=self.asset).count(), 1)

    def test_incomplete_ohlc_drops_are_counted(self):
        broken = bar('20240104', 10.0)
        broken['high'] = None
        result = self.sync(
            [bar('20240103', 10.00), broken],
            [{'trade_date': '20240103', 'adj_factor': 1.0},
             {'trade_date': '20240104', 'adj_factor': 1.0}],
            repair_start_date='2024-01-02', repair_end_date='2024-01-05',
        )
        self.assertIn('dropped_ohlc=1', result)

    def test_return_states_written_and_inserted_separately(self):
        # A pure-repair sync inserts nothing because every row already exists; saying
        # only "0 new records" makes a full repair read as a no-op.
        self.seed_rebasable_history()
        result = self.sync(
            [bar('20240102', 10.00), bar('20240103', 10.50)],
            [{'trade_date': '20240102', 'adj_factor': 1.0},
             {'trade_date': '20240103', 'adj_factor': 1.0}],
        )
        self.assertIn('2 written', result)
        self.assertIn('0 inserted', result)

    def test_resyncing_a_window_overwrites_instead_of_silently_dropping(self):
        # The old ignore_conflicts=True writer could never repair a row it had already
        # written, which is why stale bases persisted forever.
        OHLCV.objects.create(
            asset=self.asset, date='2024-01-03', open=Decimal('1.0'), high=Decimal('1.0'),
            low=Decimal('1.0'), close=Decimal('1.0'), adj_close=Decimal('1.0'),
            volume=1, amount=Decimal('1'), raw_close=Decimal('1.0'),
            adj_factor=Decimal('1.000000'),
        )
        self.sync([bar('20240103', 10.00)],
                  [{'trade_date': '20240103', 'adj_factor': 1.0}],
                  repair_start_date='2024-01-02', repair_end_date='2024-01-05')
        self.assertEqual(OHLCV.objects.filter(asset=self.asset).count(), 1)
        self.assertEqual(self.stored('2024-01-03').close, Decimal('10.0000'))

    def seed_rebasable_history(self):
        for on_date, close in [('2024-01-02', '10.00'), ('2024-01-03', '10.50')]:
            OHLCV.objects.create(
                asset=self.asset, date=on_date,
                open=Decimal(close), high=Decimal(close), low=Decimal(close),
                close=Decimal(close), adj_close=Decimal(close),
                volume=1000, amount=Decimal('1000'),
                raw_close=Decimal(close), adj_factor=Decimal('1.000000'),
            )

    def test_exdividend_event_rebases_the_older_rows_locally(self):
        self.seed_rebasable_history()
        # Append mode starts the day after the newest row; 2024-01-04 goes ex-dividend.
        self.sync([bar('20240104', 11.00)],
                  [{'trade_date': '20240104', 'adj_factor': 1.1}])
        self.assertEqual(self.stored('2024-01-02').close, Decimal('9.0909'))
        self.assertEqual(self.stored('2024-01-03').close, Decimal('9.5455'))
        # raw_close and adj_factor describe the session and must not move.
        self.assertEqual(self.stored('2024-01-02').raw_close, Decimal('10.0000'))
        self.assertEqual(self.stored('2024-01-02').adj_factor, Decimal('1.000000'))

    def test_legacy_rows_block_the_rebase_entirely(self):
        self.seed_rebasable_history()
        OHLCV.objects.filter(asset=self.asset, date='2024-01-02').update(raw_close=None)

        result = self.sync([bar('20240104', 11.00)],
                           [{'trade_date': '20240104', 'adj_factor': 1.1}])

        self.assertIn('stale_legacy=1', result)
        self.assertIn('rebased=0', result)
        # Untouched, not half-migrated: the asset stays as broken as it was rather
        # than gaining a second seam.
        self.assertEqual(self.stored('2024-01-02').close, Decimal('10.0000'))
        self.assertEqual(self.stored('2024-01-03').close, Decimal('10.5000'))

    def test_no_rebase_when_the_factor_did_not_move(self):
        self.seed_rebasable_history()
        result = self.sync([bar('20240104', 11.00)],
                           [{'trade_date': '20240104', 'adj_factor': 1.0}])
        self.assertIn('rebased=0', result)
        self.assertEqual(self.stored('2024-01-02').close, Decimal('10.0000'))
        self.assertEqual(self.stored('2024-01-03').close, Decimal('10.5000'))

    def test_append_start_skips_sessions_the_asset_already_has(self):
        self.seed_rebasable_history()
        self.sync([bar('20240104', 11.00)],
                  [{'trade_date': '20240104', 'adj_factor': 1.0}])
        self.assertEqual(OHLCV.objects.filter(asset=self.asset).count(), 3)
