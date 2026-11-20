"""A4 / STK-05 (#193): execution realism — incremental pieces.

Pins the as-of ST limit override, the raw-price detail map reconstruction, and
the execution_model parameter validation. The pending-fill engine surgery
(next_day_open fills, limit-lock retries, suspension deferral) lands on top.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.backtest.models import BacktestRun, BacktestTrade
from apps.backtest.serializers import BacktestRunSerializer
from apps.backtest.tasks import (
    _build_ohlcv_detail_map,
    _resolve_fee_config,
    clear_backtest_process_caches,
    run_backtest,
)
from apps.backtest.tests import Phase15BacktestTests
from apps.markets.models import Asset, IndexMembership, Market, OHLCV
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

    def test_universe_policy_validated(self):
        serializer = BacktestRunSerializer(data=self._payload(universe_policy='research_full'))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['parameters']['universe_policy'], 'research_full')
        bad = BacktestRunSerializer(data=self._payload(universe_policy='nonsense'))
        self.assertFalse(bad.is_valid())

    def test_pit_index_codes_validated(self):
        serializer = BacktestRunSerializer(data=self._payload(pit_index_codes=['000852.SH']))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        bad = BacktestRunSerializer(data=self._payload(pit_index_codes='000852.SH'))
        self.assertFalse(bad.is_valid())


class BacktestUniversePolicyTests(TestCase):
    """Per-run universe policy reaches eligibility via the run cache (B1b)."""

    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='SZSE', name='SZSE')
        cls.main = Asset.objects.create(market=cls.market, symbol='600001', ts_code='600001.SH', name='主板')
        cls.chinext = Asset.objects.create(market=cls.market, symbol='300001', ts_code='300001.SZ', name='创业')
        cls.d = date(2020, 1, 2)
        for asset in (cls.main, cls.chinext):
            IndexMembership.objects.create(asset=asset, index_code='000905.SH', trade_date=cls.d, weight=1.0)
            OHLCV.objects.create(
                asset=asset, date=cls.d,
                open=Decimal('10'), high=Decimal('11'), low=Decimal('9'), close=Decimal('10.5'),
                adj_close=Decimal('10.5'), volume=100, amount=1000,
            )

    def tearDown(self):
        clear_backtest_process_caches()

    def test_default_excludes_chinext(self):
        from apps.backtest.tasks import _eligible_backtest_asset_ids
        cache = {'universe_policy': None, 'pit_index_codes': None}
        ids = _eligible_backtest_asset_ids(self.d, cache)
        self.assertEqual(set(ids), {self.main.id})

    def test_research_full_includes_chinext(self):
        from apps.backtest.tasks import _eligible_backtest_asset_ids
        cache = {'universe_policy': 'research_full', 'pit_index_codes': None}
        ids = _eligible_backtest_asset_ids(self.d, cache)
        self.assertEqual(set(ids), {self.main.id, self.chinext.id})

    def test_cache_key_separates_policies(self):
        from apps.backtest.tasks import _eligible_backtest_asset_ids
        cache = {'universe_policy': None, 'pit_index_codes': None}
        default_ids = _eligible_backtest_asset_ids(self.d, cache)
        cache['universe_policy'] = 'research_full'
        full_ids = _eligible_backtest_asset_ids(self.d, cache)
        self.assertNotEqual(set(default_ids), set(full_ids))


class PendingFillMechanismTests(TestCase):
    """Unit pins for the next_day_open pending-fill mechanism (A4 part 2)."""

    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='SZSE', name='SZSE')
        cls.asset = Asset.objects.create(market=cls.market, symbol='600001', ts_code='600001.SH', name='T')
        cls.d1 = date(2020, 1, 2)
        cls.d2 = date(2020, 1, 3)
        cls.d3 = date(2020, 1, 6)
        cls.trading_dates = [cls.d1, cls.d2, cls.d3]
        cls.backtest_run = BacktestRun.objects.create(
            name='pending-mechanism',
            strategy_type=BacktestRun.StrategyType.PREDICTION_THRESHOLD,
            start_date=cls.d1, end_date=cls.d3,
            parameters={'top_n': 1, 'horizon_days': 7, 'up_threshold': 0.55},
        )
        cls.fee_config = _resolve_fee_config(cls.backtest_run)
        cls.ts_code_map = {cls.asset.id: '600001.SH'}
        cls.first_bar_map = {cls.asset.id: date(2019, 1, 1)}

    def _detail(self, prev_raw):
        return {
            (self.asset.id, self.d1): {'open': Decimal('10'), 'close': Decimal('10.5'), 'raw_close': prev_raw, 'adj_factor': Decimal('0.5')},
            (self.asset.id, self.d2): {'open': Decimal('10'), 'close': Decimal('10.5'), 'raw_close': Decimal('21'), 'adj_factor': Decimal('0.5')},
            (self.asset.id, self.d3): {'open': Decimal('10'), 'close': Decimal('10.5'), 'raw_close': Decimal('21'), 'adj_factor': Decimal('0.5')},
        }

    def _entry(self, retries=0):
        return {
            'asset_id': self.asset.id,
            'entry_date': self.d1.isoformat(),
            'exit_date': self.d3.isoformat(),
            'candidate_rank': 1,
            'retries': retries,
            'signal_payload': {'up_probability': 0.9},
        }

    def _position(self, pending_exit=None):
        position = {
            'asset_id': self.asset.id,
            'quantity': Decimal('100'),
            'buy_amount': Decimal('1000'),
            'buy_fee': Decimal('5'),
            'buy_price': Decimal('10'),
            'signal_payload': {},
            'exit_date': self.d2,
            'target_price': None,
            'stop_loss_price': None,
        }
        if pending_exit is not None:
            position['pending_exit'] = pending_exit
        return position

    def test_buy_fills_at_next_open(self):
        from apps.backtest.tasks import _process_pending_fills
        buffer = []
        cash, positions, deferred = _process_pending_fills(
            self.backtest_run, self.d2, self.trading_dates, 1,
            [self._entry()], [], Decimal('100000'),
            self._detail(Decimal('20')), {}, self.fee_config, Decimal('5'),
            [], 5, set(), self.first_bar_map, self.ts_code_map,
            trade_buffer=buffer,
        )
        self.assertEqual(len(positions), 1)
        self.assertEqual(len(buffer), 1)
        trade = buffer[0]
        self.assertEqual(trade.trade_date, self.d2)
        self.assertEqual(trade.price, Decimal('10.005'))  # open 10 + 5bps
        self.assertEqual(trade.metadata['fill_basis'], 'next_day_open')
        self.assertEqual(trade.metadata['trigger_date'], self.d1.isoformat())
        self.assertLess(cash, Decimal('100000'))
        self.assertEqual(deferred, [])

    def test_limit_up_open_defers_entry(self):
        from apps.backtest.tasks import _process_pending_fills
        buffer = []
        cash, positions, deferred = _process_pending_fills(
            self.backtest_run, self.d2, self.trading_dates, 1,
            [self._entry()], [], Decimal('100000'),
            self._detail(Decimal('18')), {}, self.fee_config, Decimal('5'),  # up limit 19.80, raw_open 20
            [], 5, set(), self.first_bar_map, self.ts_code_map,
            trade_buffer=buffer,
        )
        self.assertEqual(positions, [])
        self.assertEqual(buffer, [])
        self.assertEqual(len(deferred), 1)
        self.assertEqual(deferred[0]['retries'], 1)

    def test_limit_down_open_defers_exit(self):
        from apps.backtest.tasks import _process_pending_fills
        buffer = []
        position = self._position({'reason': 'SCHEDULED', 'trigger_date': self.d1.isoformat(), 'retries': 0})
        cash, positions, deferred = _process_pending_fills(
            self.backtest_run, self.d2, self.trading_dates, 1,
            [], [position], Decimal('0'),
            self._detail(Decimal('23')), {}, self.fee_config, Decimal('5'),  # down limit 20.70, raw_open 20
            [], 5, set(), self.first_bar_map, self.ts_code_map,
            trade_buffer=buffer,
        )
        self.assertEqual(len(positions), 1)  # never force-close
        self.assertEqual(positions[0]['pending_exit']['retries'], 1)
        self.assertEqual(buffer, [])

    def test_suspension_defers_entry(self):
        from apps.backtest.tasks import _process_pending_fills
        cash, positions, deferred = _process_pending_fills(
            self.backtest_run, self.d2, self.trading_dates, 1,
            [self._entry()], [], Decimal('100000'),
            self._detail(Decimal('20')), {}, self.fee_config, Decimal('5'),
            [], 5, {(self.asset.id, self.d2)}, self.first_bar_map, self.ts_code_map,
        )
        self.assertEqual(positions, [])
        self.assertEqual(len(deferred), 1)

    def test_entry_dropped_after_max_retries(self):
        from apps.backtest.tasks import _process_pending_fills
        cash, positions, deferred = _process_pending_fills(
            self.backtest_run, self.d2, self.trading_dates, 1,
            [self._entry(retries=3)], [], Decimal('100000'),
            self._detail(Decimal('18')), {}, self.fee_config, Decimal('5'),
            [], 5, set(), self.first_bar_map, self.ts_code_map,
        )
        self.assertEqual(deferred, [])  # retries 4 > 3 -> dropped

    def test_close_trigger_queues_pending_exit(self):
        from apps.backtest.tasks import _close_positions_for_date
        buffer = []
        position = self._position()
        price_map = {(self.asset.id, self.d2): Decimal('10.5')}
        cash, positions = _close_positions_for_date(
            self.backtest_run, self.d2, [position], Decimal('0'), price_map,
            self.fee_config, Decimal('5'), [], False,
            trade_buffer=buffer, execution_model='next_day_open',
        )
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]['pending_exit']['reason'], 'SCHEDULED')
        self.assertEqual(buffer, [])  # no fill at trigger date
        self.assertEqual(cash, Decimal('0'))

    def test_same_day_close_still_fills_immediately(self):
        from apps.backtest.tasks import _close_positions_for_date
        buffer = []
        position = self._position()
        price_map = {(self.asset.id, self.d2): Decimal('10.5')}
        cash, positions = _close_positions_for_date(
            self.backtest_run, self.d2, [position], Decimal('0'), price_map,
            self.fee_config, Decimal('5'), [], False,
            trade_buffer=buffer,
        )
        self.assertEqual(positions, [])
        self.assertEqual(len(buffer), 1)
        self.assertEqual(buffer[0].side, BacktestTrade.Side.SELL)
        self.assertGreater(cash, Decimal('0'))

    def test_open_queues_pending_entry(self):
        from apps.backtest.tasks import _open_positions_for_date
        pending = []
        positions = []
        price_map = {(self.asset.id, self.d1): Decimal('10.5')}
        candidates = [{'asset_id': self.asset.id, 'signal_payload': {'up_probability': 0.9}, 'rank_value': 0.9}]
        cash = _open_positions_for_date(
            self.backtest_run, self.d1, self.d3, candidates, Decimal('100000'),
            Decimal('100000'), Decimal('0.5'), price_map, self.fee_config, Decimal('5'),
            positions, 5, runtime_cache={}, execution_model='next_day_open', pending_entries=pending,
        )
        self.assertEqual(positions, [])
        self.assertEqual(cash, Decimal('100000'))
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['entry_date'], self.d1.isoformat())

    def test_pending_exit_survives_position_serialization(self):
        from apps.backtest.tasks import _deserialize_open_positions, _serialize_open_positions
        position = self._position({'reason': 'STOP_LOSS', 'trigger_date': self.d1.isoformat(), 'retries': 2})
        restored = _deserialize_open_positions(_serialize_open_positions([position]))
        self.assertEqual(restored[0]['pending_exit'], position['pending_exit'])

    def test_pending_entries_roundtrip_stringifies_decimals(self):
        from apps.backtest.tasks import _deserialize_pending_entries, _serialize_pending_entries
        entry = self._entry()
        entry['signal_payload']['target_price'] = Decimal('12.34')
        rows = _serialize_pending_entries([entry])
        self.assertEqual(rows[0]['signal_payload']['target_price'], '12.34')
        restored = _deserialize_pending_entries(rows)
        self.assertEqual(restored[0]['asset_id'], self.asset.id)
        self.assertEqual(restored[0]['retries'], 0)


class NextDayExecutionIntegrationTests(Phase15BacktestTests):
    """Engine-level next_day_open runs on the shared Phase15 fixture."""

    def test_next_day_open_completes_and_stamps_fill_basis(self):
        run = self._create_run(parameters={
            'top_n': 1, 'horizon_days': 7, 'up_threshold': 0.55,
            'execution_model': 'next_day_open',
        })
        run_backtest(run.id)
        run.refresh_from_db()
        self.assertEqual(run.status, BacktestRun.Status.COMPLETED)
        self.assertEqual(run.report.get('execution_model'), 'next_day_open')
        buys = BacktestTrade.objects.filter(backtest_run=run, side=BacktestTrade.Side.BUY)
        self.assertGreater(buys.count(), 0)
        for trade in buys:
            self.assertEqual(trade.metadata.get('fill_basis'), 'next_day_open')
            self.assertGreater(trade.trade_date, date.fromisoformat(trade.metadata['trigger_date']))

    def test_cross_chunk_pending_fills_are_invariant(self):
        params = {'top_n': 1, 'horizon_days': 7, 'up_threshold': 0.55, 'execution_model': 'next_day_open'}
        single = self._create_run(name='single-chunk', parameters=dict(params))
        run_backtest(single.id)
        single.refresh_from_db()
        chunked = self._create_run(name='one-day-chunks', parameters=dict(params, chunk_trading_days=1))
        for _ in range(100):
            run_backtest(chunked.id)
            chunked.refresh_from_db()
            if chunked.status == BacktestRun.Status.COMPLETED:
                break
        chunked.refresh_from_db()
        self.assertEqual(single.status, BacktestRun.Status.COMPLETED)
        self.assertEqual(chunked.status, BacktestRun.Status.COMPLETED)
        self.assertEqual(single.final_value, chunked.final_value)
        self.assertEqual(single.total_trades, chunked.total_trades)