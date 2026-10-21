"""Tests for the ETF sector-rotation pipeline (TuShare ingest -> features -> ranking).

Three gates from the implementation plan are pinned here by name and must not be relaxed:

1. ``TuShareDailyIngestUnitTests`` -- verification item 3. ``fund_daily`` reports ``vol`` in
   手 (100 份) and ``amount`` in 千元; ingest converts both to 份 and 元 so a stored row's
   ``amount / volume`` is a per-份 VWAP comparable with ``close``. TuShare is mocked at
   ``tasks.ts.pro_api``, so nothing in this file can reach the network.
2. ``PointInTimeFeatureLeakTests`` -- verification item 7. Both directions are asserted
   separately: a feature row for date D is reproducible from bars ``date <= D`` only (later bars
   are mutated, then deleted), and ``fwd_return``/``label_*`` look strictly forward.
3. ``TaLibRsiSpotCheckTests`` -- verification item 8. ``rsi_14`` equals ``talib.RSI`` run directly
   on the same adjusted close series (TA-Lib 0.6.8).

Conventions follow ``apps/prediction/tests.py``: Django's own runner, ``TestCase``/``SimpleTestCase``
and hand-written private ``_seed_*`` helpers -- no fixtures, no factories. Every row goes to the test
database and each test seeds tens of bars.
"""

import inspect
import math
import os
import pickle
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import numpy as np
import pandas as pd
import talib
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from apps.core import finance_metrics
from apps.etf_rotation import backtest_engine, features, rotation_engine, train_lightgbm
from apps.etf_rotation.etf_universe import (
    classify_etf,
    eligible_universe,
    is_excluded_code,
    is_excluded_vehicle,
    is_index_fund,
    is_point_in_time_eligible,
)
from apps.etf_rotation.features import (
    MIN_AVG_AMOUNT_YUAN_DEFAULT,
    build_eligibility_frame,
    compute_features,
    resolve_pool_parameters,
    sessions_to_calendar_days,
)
from apps.etf_rotation.models import (
    ETFCategory,
    ETFDaily,
    ETFFeatureSnapshot,
    ETFUniverse,
    ETFRotationSignal,
)
from apps.etf_rotation.tasks import (
    TUSHARE_AMOUNT_QIAN_YUAN_TO_YUAN,
    TUSHARE_VOL_HAND_TO_SHARES,
    refresh_etf_first_trade_dates,
    sync_etf_daily_by_date,
)
from apps.markets.models import BenchmarkIndexDaily
from apps.prediction.models import ModelVersion


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

#: ``D`` for the point-in-time tests, and the last seeded bar before the forward tail.
POOL_DATE = date(2024, 6, 28)
#: Sessions in front of D: long enough for every window up to 60 sessions to be fully traded.
HISTORY_SESSIONS = 70
#: Sessions behind D: twice ``LABEL_HORIZON`` so both label outcomes are observable.
FORWARD_SESSIONS = 10
LABEL_HORIZON = 5
#: The production liquidity floor (``settings.ETF_MIN_AVG_AMOUNT_YUAN``), in 元.
LIQUIDITY_FLOOR_YUAN = 50_000_000.0
COMFORTABLE_AMOUNT_YUAN = 200_000_000.0
DEAD_AMOUNT_YUAN = 10_000_000.0

PRICE_QUANT = Decimal('0.0001')


def _session_grid(count, *, last=POOL_DATE):
    """``count`` consecutive calendar days ending on ``last`` (inclusive)."""
    return [last - timedelta(days=offset) for offset in reversed(range(count))]


def _panel_days():
    """The shared leak-test grid: 70 sessions up to D plus a 10-session forward tail."""
    return _session_grid(HISTORY_SESSIONS + FORWARD_SESSIONS, last=POOL_DATE + timedelta(days=FORWARD_SESSIONS))


def _wavy_prices(count, *, base=10.0, drift=0.03, wave=0.35):
    """Deterministic rising-and-oscillating closes: always positive, mixed up/down moves.

    Mixed signs matter -- a monotone series makes ``talib.RSI`` return exactly 100.0, so a wrong
    period or a hand-rolled average could hide behind it.
    """
    return [base + drift * index + wave * math.sin(index / 2.5) for index in range(count)]


def _seed_etf(ts_code, **kwargs):
    kwargs.setdefault('name', ts_code)
    kwargs.setdefault('symbol', ts_code.split('.')[0])
    kwargs.setdefault('category', ETFCategory.SECTOR_TECH)
    kwargs.setdefault('is_rankable', True)
    kwargs.setdefault('list_date', POOL_DATE - timedelta(days=800))
    return ETFUniverse.objects.create(ts_code=ts_code, **kwargs)


def _seed_bars(etf, days, closes, *, amount=COMFORTABLE_AMOUNT_YUAN, adj_factors=None, volume=20_000_000):
    """One ``ETFDaily`` bar per day; ``high`` is ``close * 1.01`` and ``low == open == close``."""
    amounts = [amount] * len(days) if isinstance(amount, (int, float)) else list(amount)
    rows = []
    for index, (day, close) in enumerate(zip(days, closes)):
        price = Decimal(str(round(float(close), 4)))
        factor = Decimal('1.000000') if adj_factors is None else Decimal(str(adj_factors[index]))
        rows.append(
            ETFDaily(
                etf=etf,
                date=day,
                open=price,
                high=(price * Decimal('1.01')).quantize(PRICE_QUANT),
                low=price,
                close=price,
                adj_close=(price * factor).quantize(PRICE_QUANT),
                volume=int(volume),
                amount=Decimal(str(amounts[index])).quantize(PRICE_QUANT),
                adj_factor=factor,
            )
        )
    ETFDaily.objects.bulk_create(rows)
    return rows


def _seed_benchmark(days, index_code='H00905.CSI'):
    """``BenchmarkIndexDaily`` closes on the fund session grid (feeds the rs/corr features)."""
    BenchmarkIndexDaily.objects.bulk_create(
        [
            BenchmarkIndexDaily(
                index_code=index_code,
                index_name='中证500全收益',
                trade_date=day,
                close=Decimal(str(round(5000.0 + 12.0 * index + 40.0 * math.sin(index / 3.5), 4))),
            )
            for index, day in enumerate(days)
        ]
    )


def _stored_adj_close(etf, days):
    """The stored adjusted closes as floats in ``days`` order -- the exact series the features read."""
    by_day = dict(ETFDaily.objects.filter(etf=etf, date__in=days).values_list('date', 'adj_close'))
    return [float(by_day[day]) for day in days]


def _snapshot(etf, day):
    return ETFFeatureSnapshot.objects.get(etf=etf, date=day)


def _feature_values(snapshot):
    """Every ``FEATURE_COLUMNS`` value of a snapshot row, keyed by column name."""
    return {name: getattr(snapshot, name) for name in features.FEATURE_COLUMNS}


def _compute(start, end, **kwargs):
    """``compute_features`` with the pool parameters these tests control explicitly.

    ``min_listing_age_days=1`` and ``label_horizon_days=5`` differ from the signature defaults, so
    :func:`resolve_pool_parameters` cannot substitute a settings value behind them; the turnover
    floor is passed as the literal default and pinned with ``override_settings``.
    """
    kwargs.setdefault('label_horizon_days', LABEL_HORIZON)
    kwargs.setdefault('min_listing_age_days', 1)
    kwargs.setdefault('min_avg_amount_yuan', MIN_AVG_AMOUNT_YUAN_DEFAULT)
    with override_settings(
        ETF_MIN_AVG_AMOUNT_YUAN=int(LIQUIDITY_FLOOR_YUAN),
        ETF_MIN_LISTING_AGE_DAYS=183,
        ETF_LABEL_HORIZON_DAYS=20,
    ):
        return compute_features(start, end, **kwargs)


class _PanelSeedMixin:
    """Seeds two ETFs whose trailing *and* forward momentum order is known: A outruns B.

    The five sessions right after D are the contamination a forward-looking feature would read;
    ``_explode_after_d`` replaces the whole tail with a doubling series.
    """

    def seed(self):
        self.days = _panel_days()
        self.split = HISTORY_SESSIONS - 1
        self.pool_date = self.days[self.split]
        self.etf_a = _seed_etf('512760.SH', name='半导体ETF', category=ETFCategory.SECTOR_TECH)
        self.etf_b = _seed_etf('515980.SH', name='芯片ETF', category=ETFCategory.SECTOR_TECH)
        _seed_bars(self.etf_a, self.days, _wavy_prices(len(self.days)))
        _seed_bars(self.etf_b, self.days, _wavy_prices(len(self.days), base=8.0, drift=0.012, wave=0.30))
        _seed_benchmark(self.days)
        return self.days

    def compute_at_pool_date(self):
        return _compute(self.pool_date, self.pool_date)

    def explode_after_pool_date(self):
        """Replace every post-D bar with a doubling price ladder (16, 32, 64 ... 8192)."""
        for offset, day in enumerate(self.days[self.split + 1:]):
            price = Decimal(16 * 2 ** offset).quantize(PRICE_QUANT)
            ETFDaily.objects.filter(date=day).update(
                open=price,
                high=(price * Decimal('1.01')).quantize(PRICE_QUANT),
                low=price,
                close=price,
                adj_close=price,
            )


# ---------------------------------------------------------------------------
# Gate 1 -- TuShare unit regression
# ---------------------------------------------------------------------------


class TuShareDailyIngestUnitTests(TestCase):
    """Verification item 3: ``vol`` in 手 and ``amount`` in 千元 become 份 and 元 on ingest."""

    TS_CODE = '512880.SH'
    TRADE_DATE = date(2024, 1, 2)
    #: One real-shaped ``fund_daily`` row: 4.22 元/份, 10577403 手 traded, 4460249.485 千元 turned over.
    TUSHARE_ROW = {
        'ts_code': TS_CODE,
        'trade_date': '20240102',
        'open': 4.20,
        'high': 4.25,
        'low': 4.19,
        'close': 4.22,
        'pre_close': 4.21,
        'change': 0.01,
        'pct_chg': 0.2375,
        'vol': 10577403.0,
        'amount': 4460249.485,
    }

    def _run_sync(self, daily_rows=None, adj_rows=None):
        """Call the sync task with ``tushare`` stubbed at the client boundary -- no network."""
        frame = pd.DataFrame(daily_rows if daily_rows is not None else [self.TUSHARE_ROW])
        adj = pd.DataFrame(
            adj_rows if adj_rows is not None else [{'ts_code': self.TS_CODE, 'adj_factor': 1.0}]
        )

        class StubPro:
            def fund_daily(self, **kwargs):
                return frame

            def fund_adj(self, **kwargs):
                return adj

        with patch('apps.etf_rotation.tasks.ts.pro_api', return_value=StubPro()):
            with override_settings(ETF_SYNC_REQUEST_SLEEP_SECONDS=0, TUSHARE_TOKEN='test-token'):
                return sync_etf_daily_by_date(self.TRADE_DATE)

    def _synced_row(self):
        self._run_sync()
        return ETFDaily.objects.get(etf__ts_code=self.TS_CODE, date=self.TRADE_DATE)

    def test_vol_in_hands_and_amount_in_qian_yuan_are_multiplied_out(self):
        """The scale factors are 100 (手->份) and 1000 (千元->元) and ingest applies both."""
        self.assertEqual(TUSHARE_VOL_HAND_TO_SHARES, Decimal(100))
        self.assertEqual(TUSHARE_AMOUNT_QIAN_YUAN_TO_YUAN, Decimal(1000))

        result = self._run_sync()
        self.assertEqual(result['rows_written'], 1)

        row = ETFDaily.objects.get(etf__ts_code=self.TS_CODE, date=self.TRADE_DATE)
        self.assertEqual(row.volume, 10577403 * 100)
        self.assertEqual(row.amount, Decimal('4460249485.0000'))
        self.assertEqual(row.close, Decimal('4.2200'))
        self.assertEqual(row.adj_factor, Decimal('1.000000'))
        self.assertEqual(row.adj_close, Decimal('4.2200'))

    def test_stored_amount_over_volume_reproduces_the_close_as_a_vwap(self):
        """``amount(元) / volume(份)`` is a per-份 VWAP, so it sits within 2% of ``close``.

        The two factors can only be verified as a pair: dropping either one leaves this ratio at
        1/10 or 100x of ``close``, and the 元-denominated liquidity floor in ``features`` would then
        screen on the wrong scale by 1000x.
        """
        row = self._synced_row()
        vwap = float(row.amount) / float(row.volume)
        self.assertAlmostEqual(vwap, float(row.close), delta=0.02 * float(row.close))
        self.assertGreater(vwap, 1.0)
        self.assertLess(vwap, 10.0)

    def test_model_help_text_declares_the_units_the_ingest_stores(self):
        """``ETFDaily`` help_text says 份 and 元 -- the units gate 1 stores, not TuShare's input units."""
        volume_help = str(ETFDaily._meta.get_field('volume').help_text)
        amount_help = str(ETFDaily._meta.get_field('amount').help_text)
        self.assertIn('份', volume_help)
        self.assertIn('手', volume_help)
        self.assertIn('元', amount_help)
        self.assertIn('千元', amount_help)

        row = self._synced_row()
        self.assertAlmostEqual(float(row.amount) / float(row.volume), float(row.close),
                               delta=0.02 * float(row.close))

    def test_resyncing_a_date_upserts_and_an_unseen_code_becomes_an_unrankable_stub(self):
        """A re-run overwrites the bar instead of duplicating it; a code ``fund_basic`` never returned
        is created with ``is_rankable=False`` so an unknown vehicle cannot enter a momentum pool."""
        self._run_sync()
        second = self._run_sync()
        self.assertEqual(second['rows_written'], 1)
        self.assertEqual(ETFDaily.objects.filter(etf__ts_code=self.TS_CODE).count(), 1)
        self.assertEqual(ETFDaily.objects.get(etf__ts_code=self.TS_CODE).volume, 1057740300)

        stub = ETFUniverse.objects.get(ts_code=self.TS_CODE)
        self.assertFalse(stub.is_rankable)
        self.assertFalse(stub.is_active)
        self.assertEqual(stub.first_trade_date, self.TRADE_DATE)

    def test_sync_refuses_a_date_before_the_historical_data_floor(self):
        """The ``HISTORICAL_DATA_FLOOR`` guard lives in the task, before any TuShare call."""
        class ExplodingPro:
            def fund_daily(self, **kwargs):
                raise AssertionError('no TuShare call may happen for a pre-floor date')

            def fund_adj(self, **kwargs):
                raise AssertionError('no TuShare call may happen for a pre-floor date')

        with patch('apps.etf_rotation.tasks.ts.pro_api', return_value=ExplodingPro()):
            with override_settings(HISTORICAL_DATA_FLOOR='2010-01-01', TUSHARE_TOKEN='test-token'):
                with self.assertRaises(ValueError):
                    sync_etf_daily_by_date(date(2009, 12, 31))


# ---------------------------------------------------------------------------
# Gate 2 -- feature-leak prevention
# ---------------------------------------------------------------------------


class PointInTimeFeatureLeakTests(_PanelSeedMixin, TestCase):
    """Verification item 7: features read ``date <= D``; labels read the forward window."""

    def test_mutating_every_bar_after_d_leaves_d_features_identical(self):
        """Every later bar forced onto a doubling ladder must not move one feature at D.

        ``mom_5d``/``mom_20d``/``vol_20d``/``rsi_14``/``corr_with_benchmark_60d``/``rank_pct_20d``
        would all shift if any operator looked forward. The control assertion proves the mutated tail
        is live data that the final session's own ``mom_5d`` does react to (+31x), so this is not a
        test that passes because nothing was seeded.
        """
        self.seed()
        self.compute_at_pool_date()
        before = _feature_values(_snapshot(self.etf_a, self.pool_date))
        self.assertIsNotNone(before['mom_5d'], 'seed panel too short to produce mom_5d')

        self.explode_after_pool_date()
        self.compute_at_pool_date()
        after = _feature_values(_snapshot(self.etf_a, self.pool_date))

        for name in features.FEATURE_COLUMNS:
            self.assertEqual(after[name], before[name], f'{name} at D changed when post-D bars were mutated')

        tail_day = self.days[-1]
        _compute(tail_day, tail_day)
        self.assertGreater(_snapshot(self.etf_a, tail_day).mom_5d, 5.0)

    def test_deleting_every_bar_after_d_leaves_d_features_identical_and_nulls_the_label(self):
        """The same row recomputed from bars ``<= D`` only: features equal, label gone."""
        self.seed()
        self.compute_at_pool_date()
        snapshot = _snapshot(self.etf_a, self.pool_date)
        before = _feature_values(snapshot)
        self.assertIsNotNone(snapshot.label_rank_pct, 'the forward window was present, so the label must exist')

        deleted = ETFDaily.objects.filter(date__gt=self.pool_date).delete()[0]
        self.assertEqual(deleted, FORWARD_SESSIONS * 2)
        self.compute_at_pool_date()

        after_row = _snapshot(self.etf_a, self.pool_date)
        after = _feature_values(after_row)
        for name in features.FEATURE_COLUMNS:
            self.assertEqual(after[name], before[name], f'{name} at D changed when post-D bars were deleted')
        self.assertIsNone(after_row.fwd_return)
        self.assertIsNone(after_row.label_rank_pct)
        self.assertIsNone(after_row.label_top_bucket)
        self.assertIsNone(after_row.label_horizon_days)

    def test_labels_are_null_until_the_forward_window_has_traded(self):
        """The last ``label_horizon_days`` sessions of the panel have no label at all.

        Grid: D..D+5 have a realised D+N bar (``FORWARD_SESSIONS=10``), D+6..D+10 do not, so
        ``fwd_return``/``label_rank_pct``/``label_top_bucket``/``label_horizon_days`` must be NULL
        there rather than backfilled or zeroed.
        """
        days = self.seed()
        summary = _compute(days[self.split - 2], days[-1])
        self.assertGreater(summary['rows_written'], 0)

        for offset in range(FORWARD_SESSIONS + 1):
            day = days[self.split + offset]
            snapshot = _snapshot(self.etf_a, day)
            mature = offset + LABEL_HORIZON <= FORWARD_SESSIONS
            with self.subTest(day=day.isoformat(), mature=mature):
                if mature:
                    self.assertIsNotNone(snapshot.fwd_return)
                    self.assertIsNotNone(snapshot.label_rank_pct)
                    self.assertIsNotNone(snapshot.label_top_bucket)
                    self.assertEqual(snapshot.label_horizon_days, LABEL_HORIZON)
                else:
                    self.assertIsNone(snapshot.fwd_return)
                    self.assertIsNone(snapshot.label_rank_pct)
                    self.assertIsNone(snapshot.label_top_bucket)
                    self.assertIsNone(snapshot.label_horizon_days)

    def test_fwd_return_is_the_adjusted_return_over_exactly_n_sessions(self):
        """``fwd_return[D] == adj_close[D+N]/adj_close[D] - 1`` with N = ``label_horizon_days`` = 5.

        Inputs: fund A's seeded adjusted closes, D = the 70th session. The N-1 and N+1 session returns
        are asserted different, which is what an off-by-one in the shift would break.
        """
        days = self.seed()
        self.compute_at_pool_date()
        closes = _stored_adj_close(self.etf_a, days)
        index = self.split
        stored = _snapshot(self.etf_a, self.pool_date).fwd_return

        self.assertEqual(len(closes), len(days))
        self.assertAlmostEqual(stored, closes[index + LABEL_HORIZON] / closes[index] - 1.0, places=10)
        self.assertNotAlmostEqual(stored, closes[index + LABEL_HORIZON - 1] / closes[index] - 1.0, places=6)
        self.assertNotAlmostEqual(stored, closes[index + LABEL_HORIZON + 1] / closes[index] - 1.0, places=6)

    def test_features_are_computed_on_adjusted_close_not_raw_close(self):
        """A 3% distribution that leaves ``adj_close`` flat must read as ~0 momentum, not -3%.

        Fund A keeps ``close == adj_close`` at 10.0000. Fund B drops raw close to 9.7000 on the last
        session while ``adj_factor`` goes 1.000000 -> 1.030928, so its adjusted series ends flat too.
        """
        days = _session_grid(30, last=POOL_DATE)
        etf_a = _seed_etf('512760.SH', name='半导体ETF')
        etf_b = _seed_etf('159915.SZ', name='成长ETF')
        _seed_bars(etf_a, days, [10.0] * len(days))
        _seed_bars(
            etf_b, days,
            [10.0] * (len(days) - 1) + [9.7],
            adj_factors=[Decimal('1.000000')] * (len(days) - 1) + [Decimal('1.030928')],
        )
        _seed_benchmark(days)
        _compute(POOL_DATE, POOL_DATE)

        raw = _snapshot(etf_a, POOL_DATE)
        adjusted = _snapshot(etf_b, POOL_DATE)
        self.assertAlmostEqual(raw.mom_5d, 0.0, places=9)
        self.assertAlmostEqual(adjusted.mom_5d, 0.0, places=6, msg='a distribution was read as a momentum loss')
        self.assertAlmostEqual(adjusted.mom_20d, 0.0, places=6)
        self.assertAlmostEqual(adjusted.vol_20d, 0.0, places=6)

    def test_ranks_are_within_the_point_in_time_eligible_pool(self):
        """``rank_pct_20d``/``label_rank_pct`` rank date D's eligible pool and nobody else's.

        Three funds: A outruns B on both the trailing 20 sessions and the forward 5. C has the best
        forward return in the panel *and* enormous turnover inside the label window, but its
        20-session mean turnover at D is under the floor, so it is not in D's pool: A ranks 1.0 and
        B 0.5 rather than 0.667 and 0.333, and every label field on C is NULL.
        """
        days = self.seed()
        self.etf_c = _seed_etf('518880.SH', name='黄金ETF', category=ETFCategory.COMMODITY)
        _seed_bars(self.etf_c, days, [3.5] * len(days), amount=DEAD_AMOUNT_YUAN)
        ETFDaily.objects.filter(etf=self.etf_c, date__gt=self.pool_date).update(
            amount=Decimal('900000000000.0000'), close=Decimal('6.0000'), adj_close=Decimal('6.0000'),
        )
        summary = self.compute_at_pool_date()
        self.assertGreater(summary['eligible_rows'], 0)

        row_a = _snapshot(self.etf_a, self.pool_date)
        row_b = _snapshot(self.etf_b, self.pool_date)
        row_c = _snapshot(self.etf_c, self.pool_date)

        self.assertTrue(row_a.is_eligible)
        self.assertTrue(row_b.is_eligible)
        self.assertFalse(row_c.is_eligible, "post-D turnover must not buy C a place in D's pool")

        self.assertAlmostEqual(row_a.rank_pct_20d, 1.0, places=9)
        self.assertAlmostEqual(row_b.rank_pct_20d, 0.5, places=9)
        self.assertIsNone(row_c.rank_pct_20d)
        self.assertAlmostEqual(row_a.rank_pct_60d, 1.0, places=9)
        self.assertIsNone(row_c.rank_pct_60d)

        self.assertGreater(row_a.fwd_return, row_b.fwd_return)
        self.assertGreater(row_c.fwd_return, row_a.fwd_return, 'C is the best forward performer by construction')
        self.assertAlmostEqual(row_a.label_rank_pct, 1.0, places=9)
        self.assertAlmostEqual(row_b.label_rank_pct, 0.5, places=9)
        self.assertTrue(row_a.label_top_bucket)
        self.assertFalse(row_b.label_top_bucket)
        self.assertIsNone(row_c.label_rank_pct)
        self.assertIsNone(row_c.label_top_bucket)
        self.assertIsNone(row_c.label_horizon_days)
        self.assertIsNotNone(row_c.fwd_return, 'fwd_return is a price fact and is kept for ineligible rows')

    def test_no_nan_reaches_the_snapshot_table(self):
        """An uncomputable feature is NULL, never NaN, so a training frame cannot read it as zero."""
        self.seed()
        self.compute_at_pool_date()
        row = _snapshot(self.etf_a, self.pool_date)
        for name in features.FEATURE_COLUMNS:
            value = getattr(row, name)
            if value is not None:
                self.assertTrue(math.isfinite(value), f'{name} stored a non-finite value {value}')
        self.assertIsNone(row.mom_250d, 'the seeded panel is far shorter than 250 sessions')
        self.assertIsNone(row.mom_120d)

    def test_dry_run_computes_but_writes_nothing(self):
        """``dry_run=True`` reports the same row counts and persists zero rows."""
        self.seed()
        summary = _compute(self.pool_date, self.pool_date, dry_run=True)
        self.assertGreater(summary['rows_computed'], 0)
        self.assertEqual(summary['rows_written'], 0)
        self.assertEqual(ETFFeatureSnapshot.objects.count(), 0)

    def test_the_ranker_input_frame_carries_no_label_columns(self):
        """``backtest_engine.DatabaseDataSource.snapshots`` selects the feature whitelist only.

        The label columns are stuffed with sentinel values first: if the read path ever widened to
        ``_meta.fields`` the backtest would be scoring with its answer key in the frame.
        """
        self.seed()
        self.compute_at_pool_date()
        ETFFeatureSnapshot.objects.filter(date=self.pool_date).update(
            fwd_return=9.99, label_rank_pct=0.99, label_top_bucket=True, label_horizon_days=LABEL_HORIZON,
        )
        self.assertEqual(_snapshot(self.etf_a, self.pool_date).fwd_return, 9.99)

        frame = backtest_engine.DatabaseDataSource().snapshots(self.pool_date)
        self.assertEqual(set(frame.columns) & set(backtest_engine.LABEL_COLUMNS), set())
        self.assertEqual(
            set(frame.columns),
            {'date', 'ts_code', 'name', 'category', 'is_eligible', *backtest_engine.PIT_SNAPSHOT_FEATURE_COLUMNS},
        )
        self.assertEqual(sorted(frame['ts_code']), sorted([self.etf_a.ts_code, self.etf_b.ts_code]))


# ---------------------------------------------------------------------------
# Gate 3 -- TA-Lib spot check
# ---------------------------------------------------------------------------


class TaLibRsiSpotCheckTests(_PanelSeedMixin, TestCase):
    """Verification item 8: the stored ``rsi_14`` is exactly ``talib.RSI`` on the same series."""

    def adjusted_series(self, etf, sessions=HISTORY_SESSIONS):
        return np.asarray(_stored_adj_close(etf, self.days[:sessions]), dtype='float64')

    def test_rsi_14_matches_talib_rsi_on_the_same_adj_close_series(self):
        """Inputs: fund A's 70 seeded adjusted closes ending at D, ``timeperiod=14``, TA-Lib 0.6.8.

        The series rises and falls, so the expected value is interior (0 < rsi < 100) and a wrong
        period shows up far outside the 1e-6 tolerance -- RSI(7) and RSI(21) on the same series are
        asserted to be more than 1e-3 away.
        """
        self.assertEqual(features.RSI_PERIOD, 14)
        self.seed()
        self.compute_at_pool_date()
        stored = _snapshot(self.etf_a, self.pool_date).rsi_14

        series = self.adjusted_series(self.etf_a)
        expected = float(talib.RSI(series, timeperiod=14)[HISTORY_SESSIONS - 1])
        self.assertGreater(expected, 0.0)
        self.assertLess(expected, 100.0)
        self.assertAlmostEqual(stored, expected, delta=1e-6)
        for other_period in (7, 21):
            other = float(talib.RSI(series, timeperiod=other_period)[HISTORY_SESSIONS - 1])
            self.assertGreater(abs(stored - other), 1e-3, f'rsi_14 is indistinguishable from RSI({other_period})')

    def test_bollinger_position_and_ma_ratios_match_talib_on_the_same_series(self):
        """The same check for the rest of the TA-Lib family: BBANDS position and the SMA ratios at D."""
        self.seed()
        self.compute_at_pool_date()
        row = _snapshot(self.etf_a, self.pool_date)
        series = self.adjusted_series(self.etf_a)
        index = HISTORY_SESSIONS - 1

        upper, _middle, lower = talib.BBANDS(
            series, timeperiod=features.BOLLINGER_PERIOD,
            nbdevup=features.BOLLINGER_STD_DEVIATION, nbdevdn=features.BOLLINGER_STD_DEVIATION,
        )
        self.assertAlmostEqual(
            row.bollinger_pos, float((series[index] - lower[index]) / (upper[index] - lower[index])), places=9,
        )
        for period in features.MA_PERIODS:
            sma = float(talib.SMA(series, timeperiod=period)[index])
            self.assertAlmostEqual(
                getattr(row, f'ma{period}_ratio'), float(series[index] / sma - 1.0), places=9,
                msg=f'ma{period}_ratio is not close/SMA({period}) - 1',
            )

    def test_a_suspended_session_blanks_only_the_windows_that_contain_it(self):
        """TA-Lib runs down the fund's own contiguous sessions, and the full-window mask then NULLs
        exactly the cells whose window has a hole in it.

        Fund B is missing one bar 17 sessions before D: the 20-session windows (``ma20_ratio``,
        ``bollinger_pos``) straddle it and are NULL, while the 14-session RSI window has cleared it
        and equals ``talib.RSI`` over B's 69-value contiguous sequence -- so a suspension cannot
        blank the rest of the panel, and cannot smuggle a partial window into the database either.
        """
        days = self.seed()
        hole = days[self.split - 17]
        ETFDaily.objects.filter(etf=self.etf_b, date=hole).delete()
        self.compute_at_pool_date()

        row = _snapshot(self.etf_b, self.pool_date)
        self.assertIsNone(row.ma20_ratio, 'a 20-session window with a hole must be NULL, not computed')
        self.assertIsNone(row.bollinger_pos)

        remaining = np.asarray(_stored_adj_close(self.etf_b, [day for day in days[:HISTORY_SESSIONS] if day != hole]),
                               dtype='float64')
        self.assertIsNotNone(row.rsi_14)
        self.assertAlmostEqual(row.rsi_14, float(talib.RSI(remaining, timeperiod=14)[-1]), delta=1e-6)

        unaffected = _snapshot(self.etf_a, self.pool_date)
        self.assertAlmostEqual(
            unaffected.rsi_14, float(talib.RSI(self.adjusted_series(self.etf_a), timeperiod=14)[-1]), delta=1e-6,
        )


# ---------------------------------------------------------------------------
# Universe classification, listing-date fallback, eligibility pool
# ---------------------------------------------------------------------------


class EtfVehicleClassificationTests(SimpleTestCase):
    """``fund_basic`` has no index/QDII/commodity field, so classification is assembled in tiers."""

    def test_reits_and_active_managed_funds_are_excluded_from_rankable(self):
        """REITs (``fund_type``), 混合型/灵活配置型/平衡型/成长型 (``invest_type``) and LOF/定开/封闭
        names are vehicles the rotation pool must never hold."""
        for ts_code, name, fund_type, invest_type in (
            ('180401.SZ', '华夏北京保障房中心租赁住房REIT', 'REITs', ''),
            ('160716.SZ', '中欧价值发现混合LOF', '股票型', '混合型'),
            ('163803.SZ', '某灵活配置基金', '混合型', '灵活配置型'),
            ('166005.SZ', '某平衡配置基金', '混合型', '平衡型'),
            ('501001.SH', '某成长基金', '股票型', '成长型'),
            ('161723.SZ', '某定开基金', '股票型', ''),
            ('165509.SZ', '某封闭式基金', '股票型', '封闭'),
        ):
            self.assertTrue(
                is_excluded_vehicle(ts_code, name, fund_type, invest_type),
                f'{name} must be excluded from the rotation pool',
            )
        self.assertFalse(
            is_excluded_vehicle('510300.SH', '华泰柏瑞沪深300ETF', '股票型', '被动指数型'),
        )

    def test_lof_and_structured_shares_are_excluded_by_code_prefix(self):
        """Fund names never carry the tokens "LOF"/"分级", so the exchange prefix is the
        only field that separates a genuine ETF from an on-exchange LOF or 分级 share.

        ``159xxx`` must survive: it shares the ``15`` root with ``150xxx`` and a
        two-character check would have removed 768 real ETFs from the pool.
        """
        for ts_code in ('160218.SZ', '150018.SZ', '151002.SZ', '152000.SZ', '501026.SH', '502000.SH'):
            self.assertTrue(is_excluded_code(ts_code), f'{ts_code} is not an ETF')
        for ts_code in ('159934.SZ', '159915.SZ', '510300.SH', '511880.SH', '561980.SH',
                        '588000.SH', '512760.SH'):
            self.assertFalse(is_excluded_code(ts_code), f'{ts_code} is a genuine ETF')
        # A malformed code cannot be excluded on a prefix it does not have.
        self.assertFalse(is_excluded_code(''))
        self.assertFalse(is_excluded_code('160218'))

    def test_vehicle_exclusion_cannot_be_derived_from_listing_status(self):
        """The exclusion predicate takes no ``status``/``is_active``: a delisted ETF stays rankable."""
        parameters = list(inspect.signature(is_excluded_vehicle).parameters)
        self.assertEqual(parameters, ['ts_code', 'name', 'fund_type', 'invest_type'])
        self.assertTrue(is_excluded_vehicle('180401.SZ', '某REIT', 'REITs', ''))

    def test_money_market_fund_classifies_as_money(self):
        """``invest_type='货币型'`` and the 货币/现金 name keywords both land in MONEY."""
        self.assertEqual(classify_etf(name='银华货币ETF', invest_type='货币型')[0], ETFCategory.MONEY)
        self.assertEqual(classify_etf(name='添富现金ETF', invest_type='')[0], ETFCategory.MONEY)

    def test_gold_spot_contract_classifies_as_commodity(self):
        """``invest_type='黄金现货合约'`` is COMMODITY and the benchmark is kept as sub_category."""
        category, sub_category = classify_etf(
            name='华安黄金ETF', invest_type='黄金现货合约', benchmark='上金所AU9999现货实盘合约收益率',
        )
        self.assertEqual(category, ETFCategory.COMMODITY)
        self.assertEqual(sub_category, '上金所AU9999现货实盘合约收益率')
        self.assertEqual(classify_etf(name='国泰黄金ETF', invest_type='')[0], ETFCategory.COMMODITY)
        # 有色/石化 name equity sectors on purpose -- they are equity sectors, not commodities.
        self.assertEqual(
            classify_etf(name='南方中证有色ETF', invest_type='被动指数型')[0], ETFCategory.SECTOR_MATERIAL,
        )

    def test_blank_invest_type_falls_back_to_benchmark_and_name_keywords(self):
        """A few hundred rows have no ``invest_type`` at all; the name/benchmark tiers carry them."""
        self.assertEqual(classify_etf(name='国泰中证500ETF', benchmark='中证500指数收益率')[0], ETFCategory.BROAD)
        self.assertEqual(classify_etf(name='国泰纳斯达克100ETF', invest_type='')[0], ETFCategory.QDII)
        self.assertEqual(classify_etf(name='国泰上证10年期国债ETF', invest_type='')[0], ETFCategory.BOND)
        self.assertEqual(classify_etf(name='中证红利ETF', invest_type='')[0], ETFCategory.STYLE_DIVIDEND)
        self.assertEqual(classify_etf(name='无关键词基金', invest_type='', benchmark='')[0], ETFCategory.OTHER)

    def test_sector_keyword_beats_the_broad_index_family_it_is_inside(self):
        """Sectors are tested before BROAD: '中证全指半导体' is a sector fund, not the broad market."""
        category, _sub = classify_etf(
            name='国泰中证全指半导体ETF', invest_type='被动指数型', benchmark='中证全指半导体产品与设备指数收益率',
        )
        self.assertEqual(category, ETFCategory.SECTOR_TECH)

    def test_sub_category_is_truncated_to_the_model_column_bound(self):
        """``benchmark`` is free text up to 200 chars; returning it verbatim overflowed varchar(50)."""
        bound = ETFUniverse._meta.get_field('sub_category').max_length
        _category, sub_category = classify_etf(name='某基金', benchmark='A' * 200)
        self.assertEqual(len(sub_category), bound)
        self.assertLess(bound, 200)

    def test_index_fund_detector_needs_an_index_invest_type_or_a_benchmark(self):
        self.assertTrue(is_index_fund('被动指数型', ''))
        self.assertTrue(is_index_fund('增强指数型', ''))
        self.assertTrue(is_index_fund('', '沪深300指数收益率'))
        self.assertFalse(is_index_fund('货币型', ''))


class EtfListingDateFallbackTests(TestCase):
    """Plan C6: ``list_date`` is blank on part of the feed, so age falls back to the first bar."""

    def test_effective_list_date_prefers_list_date_then_first_trade_date(self):
        listed = _seed_etf(
            '510300.SH', name='沪深300ETF', list_date=date(2012, 5, 28), first_trade_date=date(2012, 5, 18),
        )
        self.assertEqual(listed.effective_list_date, date(2012, 5, 28))

        fallback = _seed_etf('159915.SZ', name='创业板ETF', list_date=None, first_trade_date=date(2011, 12, 1))
        self.assertEqual(fallback.effective_list_date, date(2011, 12, 1))

        undated = _seed_etf('588000.SH', name='科创50ETF', list_date=None, first_trade_date=None)
        self.assertIsNone(undated.effective_list_date)

    def test_refresh_first_trade_dates_backfills_from_bars_and_repairs_a_blank_list_date(self):
        """The C6 path in ``tasks.refresh_etf_first_trade_dates``: the bars themselves are the evidence."""
        days = _session_grid(6, last=POOL_DATE)
        etf = _seed_etf('512760.SH', name='半导体ETF', list_date=None, first_trade_date=None)
        _seed_bars(etf, days, _wavy_prices(len(days)))

        self.assertEqual(refresh_etf_first_trade_dates(), 1)

        etf.refresh_from_db()
        self.assertEqual(etf.first_trade_date, days[0])
        self.assertEqual(etf.list_date, days[0], 'a blank list_date must be repaired from the first bar')
        self.assertEqual(etf.effective_list_date, days[0])

    def test_eligible_universe_includes_the_fallback_and_excludes_an_undated_fund(self):
        _seed_etf('510300.SH', name='沪深300ETF', list_date=date(2020, 1, 2))
        _seed_etf('159915.SZ', name='创业板ETF', list_date=None, first_trade_date=date(2020, 1, 2))
        _seed_etf('588000.SH', name='科创50ETF', list_date=None, first_trade_date=None)
        _seed_etf('512100.SH', name='中证1000ETF', list_date=POOL_DATE - timedelta(days=10))
        _seed_etf('501001.SH', name='某LOF', is_rankable=False, list_date=POOL_DATE - timedelta(days=800))

        codes = set(eligible_universe(POOL_DATE, min_listing_age_days=183).values_list('ts_code', flat=True))
        self.assertEqual(
            codes, {'510300.SH', '159915.SZ'},
            'the COALESCE fallback must count as a listing date; an undated, newly listed or '
            'non-rankable fund must not be poolable',
        )

    def test_the_eligibility_queryset_materializes_without_shadowing_the_property(self):
        """The pool annotation must not reuse the ``effective_list_date`` property name.

        ``annotate(effective_list_date=Coalesce(...))`` sets an instance attribute of that name, and
        ``ETFUniverse.effective_list_date`` (models.py:98-106) is a property with no setter, so
        ``list(eligible_universe(d))`` raised ``AttributeError`` even though ``.count()`` and
        ``.values_list()`` worked. The annotation is ``pit_list_date``; this locks both halves: the
        queryset iterates, and the property still answers on a row that came out of it.
        """
        listed = _seed_etf('510300.SH', name='沪深300ETF', list_date=POOL_DATE - timedelta(days=800))
        _seed_etf('510500.SH', name='中证500ETF', list_date=None,
                  first_trade_date=POOL_DATE - timedelta(days=900))

        rows = list(eligible_universe(POOL_DATE, min_listing_age_days=183))

        self.assertEqual([row.ts_code for row in rows], ['510300.SH', '510500.SH'])
        self.assertEqual(rows[0].effective_list_date, listed.list_date)
        self.assertEqual(
            rows[1].effective_list_date, listed.__class__.objects.get(ts_code='510500.SH').first_trade_date,
            'iterating the annotated queryset must not replace the C6 fallback the property computes',
        )


class RotationEligibilityPoolTests(TestCase):
    """The pool filter: minimum listing age and a 20-session average turnover floor, in 元."""

    def test_pool_requires_age_and_a_50m_yuan_average_turnover(self):
        """``build_eligibility_frame`` at the production 50,000,000 元 floor and 183-day age.

        20 sessions x 9 funds. Fund 2 averages exactly the floor (a strict ``>`` rejects it), fund 3
        clears it by 0.95 元, fund 4 sits on the 183-day age boundary (inclusive) and fund 9 is listed
        only through the ``first_trade_date`` fallback; funds 5-8 fail on age, rankability, delisting
        and a missing listing date respectively.
        """
        sessions = _session_grid(20, last=POOL_DATE)

        def master_row(etf_id, **overrides):
            row = {'etf_id': etf_id, 'is_rankable': True, 'list_date': POOL_DATE - timedelta(days=400),
                   'first_trade_date': None, 'delist_date': None}
            row.update(overrides)
            # ``_load_master`` derives this column; ``build_eligibility_frame`` reads only it, so the
            # C6 ``list_date or first_trade_date`` fallback is what the pool actually keys on.
            row['effective_list_date'] = row['list_date'] or row['first_trade_date']
            return row

        master = pd.DataFrame.from_records(
            [
                master_row(1),
                master_row(2),
                master_row(3),
                master_row(4, list_date=POOL_DATE - timedelta(days=183)),
                master_row(5, list_date=POOL_DATE - timedelta(days=182)),
                master_row(6, is_rankable=False),
                master_row(7, delist_date=POOL_DATE),
                master_row(8, list_date=None),
                master_row(9, list_date=None, first_trade_date=POOL_DATE - timedelta(days=400)),
            ]
        ).set_index('etf_id')
        amounts = pd.DataFrame(
            {
                1: [60_000_000.0] * 20,
                2: [50_000_000.0] * 20,
                3: [50_000_000.0] * 19 + [50_000_019.0],
                4: [60_000_000.0] * 20,
                5: [60_000_000.0] * 20,
                6: [60_000_000.0] * 20,
                7: [60_000_000.0] * 20,
                8: [60_000_000.0] * 20,
                9: [60_000_000.0] * 20,
            },
            index=sessions,
        )
        frame = build_eligibility_frame(
            master=master,
            avg_amount_20d=amounts.rolling(features.AMOUNT_SESSIONS).mean(),
            min_listing_age_days=183,
            min_avg_amount_yuan=LIQUIDITY_FLOOR_YUAN,
        )
        passed_on_d = {int(column) for column, ok in zip(frame.columns, frame.iloc[-1]) if bool(ok)}
        self.assertEqual(passed_on_d, {1, 3, 4, 9})
        self.assertFalse(bool(frame.iloc[-1][2]), 'an average exactly at the floor is not above it')
        self.assertFalse(bool(frame.iloc[-1][5]), '182 days of listing age is under the 183-day floor')
        self.assertFalse(bool(frame.iloc[-1][6]), 'a non-rankable vehicle cannot be pooled')
        self.assertFalse(bool(frame.iloc[-1][7]), 'delisted on D is out of D\'s pool')
        self.assertFalse(bool(frame.iloc[-1][8]), 'no listing date at all is out')
        self.assertFalse(bool(frame.iloc[0][1]), 'the first session has no complete 20-session mean')

    def test_liquidity_floor_is_measured_in_yuan_not_tushare_qian_yuan(self):
        """End to end through ``compute_features``: 6e8 元/day passes, 6e5 (a 千元-magnitude number) does not.

        An un-converted 千元 amount is 1000x too small, so a fund really turning over 6 亿元/day would
        be dropped from the pool -- the failure mode this test exists to catch.
        """
        days = _session_grid(30, last=POOL_DATE)
        liquid = _seed_etf('512760.SH', name='半导体ETF', list_date=POOL_DATE - timedelta(days=400))
        miscaled = _seed_etf('515980.SH', name='芯片ETF', list_date=POOL_DATE - timedelta(days=400))
        _seed_bars(liquid, days, _wavy_prices(len(days)), amount=600_000_000.0)
        _seed_bars(miscaled, days, _wavy_prices(len(days), base=8.0), amount=600_000.0)
        _seed_benchmark(days)
        _compute(POOL_DATE, POOL_DATE)

        self.assertTrue(_snapshot(liquid, POOL_DATE).is_eligible)
        self.assertFalse(_snapshot(miscaled, POOL_DATE).is_eligible)
        self.assertAlmostEqual(
            _snapshot(liquid, POOL_DATE).avg_amount_20d, 600_000_000.0, places=4,
            msg='avg_amount_20d is documented in 元 and must not be rescaled again',
        )

    def test_row_level_predicate_mirrors_the_pool_query(self):
        """``is_point_in_time_eligible`` agrees with ``eligible_universe`` on every boundary row."""
        rows = {
            'old': _seed_etf('510300.SH', name='沪深300ETF', list_date=POOL_DATE - timedelta(days=400)),
            'boundary': _seed_etf('510500.SH', name='中证500ETF', list_date=POOL_DATE - timedelta(days=183)),
            'too_new': _seed_etf('512100.SH', name='中证1000ETF', list_date=POOL_DATE - timedelta(days=182)),
            'delisted_on_d': _seed_etf('159001.SZ', name='已退市', list_date=POOL_DATE - timedelta(days=900),
                                       delist_date=POOL_DATE),
            'rankable_off': _seed_etf('501001.SH', name='某LOF', list_date=POOL_DATE - timedelta(days=900),
                                      is_rankable=False),
        }
        query = set(eligible_universe(POOL_DATE, min_listing_age_days=183).values_list('ts_code', flat=True))
        for key, expected in (
            ('old', True), ('boundary', True), ('too_new', False), ('delisted_on_d', False),
            ('rankable_off', False),
        ):
            etf = rows[key]
            self.assertEqual(is_point_in_time_eligible(etf, POOL_DATE, min_listing_age_days=183), expected, key)
            self.assertEqual(etf.ts_code in query, expected, f'{key} disagrees between query and row level')


# ---------------------------------------------------------------------------
# Signal row uniqueness
# ---------------------------------------------------------------------------


class RotationSignalUniquenessTests(TestCase):
    """``unique_together ('date','signal_type')``: a re-run overwrites, it never appends."""

    def _values(self, **overrides):
        values = {
            'date': POOL_DATE,
            'signal_type': ETFRotationSignal.SignalType.MONTHLY,
            'model_type': ETFRotationSignal.ModelType.RULE,
            'rankings': [{'ts_code': '512760.SH', 'rank': 1, 'score': 0.8}],
            'selected_etfs': [{'ts_code': '512760.SH', 'weight': 0.2}],
            'max_positions': 5,
        }
        values.update(overrides)
        return values

    def test_second_insert_for_the_same_date_and_signal_type_raises(self):
        """The constraint is a real database constraint, not a convention."""
        ETFRotationSignal.objects.create(**self._values())
        with self.assertRaises(IntegrityError), transaction.atomic():
            ETFRotationSignal.objects.create(**self._values(model_type='LIGHTGBM'))
        self.assertEqual(ETFRotationSignal.objects.count(), 1)

    def test_regenerating_a_day_overwrites_the_existing_row(self):
        """The command's ``update_or_create`` path: one row per (date, signal_type), latest run wins."""
        ETFRotationSignal.objects.create(**self._values())
        signal, created = ETFRotationSignal.objects.update_or_create(
            date=POOL_DATE,
            signal_type=ETFRotationSignal.SignalType.MONTHLY,
            defaults=self._values(model_type='LIGHTGBM', rankings=[{'ts_code': '515980.SH', 'rank': 1}]),
        )
        self.assertFalse(created)
        self.assertEqual(ETFRotationSignal.objects.count(), 1)
        self.assertEqual(signal.model_type, 'LIGHTGBM')
        self.assertEqual(signal.rankings[0]['ts_code'], '515980.SH')

        # A different signal_type on the same date is a second row, not an overwrite.
        ETFRotationSignal.objects.create(**self._values(signal_type='WEEKLY'))
        self.assertEqual(ETFRotationSignal.objects.count(), 2)


# ---------------------------------------------------------------------------
# Backtest configuration and fee model
# ---------------------------------------------------------------------------


class EtfFeeModelTests(SimpleTestCase):
    """ETF fills are two-way commission only -- the deliberate deviation from the A-share engine."""

    def config(self, **overrides):
        values = {
            'start_date': date(2024, 1, 1), 'end_date': date(2024, 6, 28),
            'commission_rate': 0.0003, 'min_commission': 5.0, 'slippage': 0.001,
        }
        values.update(overrides)
        return backtest_engine.BacktestConfig(**values)

    def test_commission_is_wan_san_with_a_five_yuan_floor(self):
        commission = backtest_engine.etf_commission
        config = self.config()
        self.assertEqual(commission(0.0, config), 0.0)
        self.assertEqual(commission(-1000.0, config), 0.0)
        # 万三 of 10000 is 3.00, under the floor, so the floor governs up to 16,666.67 元.
        self.assertEqual(commission(10_000.0, config), 5.0)
        self.assertEqual(commission(16_666.0, config), 5.0)
        self.assertAlmostEqual(commission(16_667.0, config), 5.0001, places=6)
        self.assertAlmostEqual(commission(200_000.0, config), 60.0, places=6)

    def test_sell_side_reports_zero_stamp_duty(self):
        """Documentation, not a bug: ETFs are stamp-duty exempt, so a sell pays commission only.

        The A-share engine levies 0.5 permille on sells. Someone will "fix" this engine to match;
        this test is the reason not to.
        """
        config = self.config()
        breakdown = backtest_engine.etf_fee_breakdown(200_000.0, config, side='sell')
        self.assertEqual(breakdown['stamp_duty'], 0.0)
        self.assertEqual(breakdown['exchange_fee'], 0.0)
        self.assertEqual(breakdown['regulatory_fee'], 0.0)
        self.assertEqual(breakdown['transfer_fee'], 0.0)
        self.assertAlmostEqual(breakdown['commission'], 60.0, places=9)
        self.assertAlmostEqual(breakdown['total_fee'], 60.0, places=9)
        self.assertEqual(breakdown['side'], 'SELL')
        self.assertAlmostEqual(
            backtest_engine.etf_fee_breakdown(200_000.0, config, side='buy')['total_fee'], 60.0, places=9,
        )

    def test_fee_model_summary_declares_the_etf_exemption_and_resolves_settings(self):
        summary = backtest_engine.fee_model_summary(self.config())
        self.assertEqual(summary['mode'], 'cn_etf_two_way_commission')
        self.assertEqual(summary['stamp_duty'], 0.0)
        self.assertEqual(summary['applies_to'], 'both sides')
        self.assertIn('stamp duty', summary['stamp_duty_note'])
        self.assertEqual(summary['notional_basis'], 'filled notional = shares x slipped execution price')

        with override_settings(ETF_COMMISSION_RATE=0.0005, ETF_MIN_COMMISSION=7, ETF_SLIPPAGE=0.002):
            defaults = backtest_engine.BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 2, 1))
            self.assertAlmostEqual(defaults.commission_rate, 0.0005)
            self.assertEqual(defaults.min_commission, 7.0)
            self.assertAlmostEqual(defaults.slippage, 0.002)
            self.assertAlmostEqual(backtest_engine.etf_commission(10_000.0, defaults), 7.0)


class BacktestConfigValidationTests(SimpleTestCase):
    """``BacktestConfig.validate``/``fingerprint`` guard the run's identity and its parameter space."""

    def config(self, **overrides):
        values = {'start_date': date(2024, 1, 1), 'end_date': date(2024, 6, 28)}
        values.update(overrides)
        return backtest_engine.BacktestConfig(**values)

    def test_validate_rejects_an_inverted_or_missing_date_range(self):
        with self.assertRaises(backtest_engine.BacktestConfigError):
            self.config(start_date=date(2024, 6, 28), end_date=date(2024, 1, 1)).validate()
        with self.assertRaises(backtest_engine.BacktestConfigError):
            self.config(start_date=None).validate()
        self.config(start_date=date(2024, 1, 1), end_date=date(2024, 1, 1)).validate()

    def test_validate_rejects_unknown_model_type_and_frequency(self):
        for model_type in ('MOMENTUM', 'xgboost', 'RULES'):
            with self.subTest(model_type=model_type):
                with self.assertRaises(backtest_engine.BacktestConfigError):
                    self.config(model_type=model_type).validate()
        with self.assertRaises(backtest_engine.BacktestConfigError):
            self.config(rebalance_frequency='DAILY').validate()
        # Case is normalised, so the lowercase form is valid; blank falls back to the default.
        self.config(model_type='rule', rebalance_frequency='weekly').validate()
        self.assertEqual(self.config(model_type='').model_type, 'RULE')

    def test_validate_rejects_non_positive_capital_and_out_of_range_knobs(self):
        for overrides in (
            {'initial_capital': 0}, {'initial_capital': -480000}, {'max_positions': 0},
            {'momentum_lookback': 0}, {'commission_rate': -0.0003}, {'slippage': -0.01},
            {'min_commission': -5}, {'liquidity_floor_multiple': -10}, {'chunk_trading_days': 0},
        ):
            with self.subTest(**overrides):
                with self.assertRaises(backtest_engine.BacktestConfigError):
                    self.config(**overrides).validate()

    def test_fingerprint_is_stable_and_sensitive_to_momentum_lookback(self):
        left = self.config(momentum_lookback=60)
        self.assertEqual(left.fingerprint(), self.config(momentum_lookback=60).fingerprint())
        self.assertNotEqual(left.fingerprint(), self.config(momentum_lookback=120).fingerprint())
        self.assertNotEqual(left.fingerprint(), self.config(commission_rate=0.0009).fingerprint())
        self.assertEqual(len(left.fingerprint()), 16)

    def test_parameters_record_the_fee_model_and_the_label_block_it_excludes(self):
        payload = self.config().to_parameters()
        self.assertEqual(payload['pit_snapshot_feature_columns'], list(features.FEATURE_COLUMNS))
        self.assertEqual(payload['excluded_label_columns'], list(backtest_engine.LABEL_COLUMNS))
        self.assertEqual(payload['fee_model']['stamp_duty'], 0.0)
        self.assertEqual(payload['start_date'], '2024-01-01')


class FeatureWhitelistTests(SimpleTestCase):
    """``FEATURE_COLUMNS`` is the declared feature/label split; nothing may infer it from the model."""

    def test_feature_columns_match_the_snapshot_feature_fields(self):
        model_fields = {
            field.name for field in ETFFeatureSnapshot._meta.get_fields()
            if getattr(field, 'column', None) is not None
        }
        non_features = set(features.LABEL_COLUMNS) | {
            'label_horizon_days', 'is_eligible', 'metadata', 'id', 'etf', 'date',
        }
        self.assertEqual(set(features.FEATURE_COLUMNS), model_fields - non_features)
        self.assertEqual(len(features.FEATURE_COLUMNS), 24)

    def test_label_columns_are_disjoint_from_features_and_from_the_ranker_whitelist(self):
        self.assertEqual(set(features.LABEL_COLUMNS) & set(features.FEATURE_COLUMNS), set())
        self.assertEqual(set(backtest_engine.LABEL_COLUMNS) & set(features.FEATURE_COLUMNS), set())
        self.assertEqual(tuple(backtest_engine.PIT_SNAPSHOT_FEATURE_COLUMNS), tuple(features.FEATURE_COLUMNS))

    def test_feature_membership_is_not_derived_from_model_fields(self):
        """The label columns are model fields too, so ``_meta.fields`` cannot be the feature list."""
        field_names = {field.name for field in ETFFeatureSnapshot._meta.get_fields()}
        self.assertIn('fwd_return', field_names)
        self.assertNotIn('fwd_return', features.FEATURE_COLUMNS)
        self.assertNotIn('label_rank_pct', features.FEATURE_COLUMNS)
        self.assertNotIn('fwd_return', features.build_momentum_features(pd.DataFrame({'a': [1.0, 2.0]})))

    def test_forward_return_is_the_only_forward_looking_builder(self):
        """``shift(-N)`` appears in ``build_forward_return`` alone; every feature travels backwards."""
        for builder in (
            features.build_momentum_features, features.build_risk_features,
            features.build_liquidity_features, features.build_drawdown_feature,
            features.build_cross_sectional_ranks,
        ):
            self.assertNotIn('shift(-', inspect.getsource(builder), f'{builder.__name__} reached forward')
        self.assertIn('shift(-', inspect.getsource(features.build_forward_return))

    def test_warm_up_is_sized_to_the_longest_lookback(self):
        self.assertEqual(features.WARMUP_SESSIONS, max(features.MOMENTUM_SESSIONS))
        self.assertEqual(sessions_to_calendar_days(250), 393)
        self.assertEqual(sessions_to_calendar_days(5), 23)

    def test_pool_parameters_follow_settings_only_when_the_caller_used_the_default(self):
        with override_settings(ETF_LABEL_HORIZON_DAYS=15, ETF_MIN_AVG_AMOUNT_YUAN=1_000_000):
            self.assertEqual(resolve_pool_parameters(20, 183, MIN_AVG_AMOUNT_YUAN_DEFAULT),
                             (15, 183, 1_000_000.0))
            # An explicit non-default argument wins over settings.
            self.assertEqual(resolve_pool_parameters(5, 30, 1000.0), (5, 30, 1000.0))
        with self.assertRaises(ValueError):
            resolve_pool_parameters(0, 183, MIN_AVG_AMOUNT_YUAN_DEFAULT)


def _rule_cross_section():
    """One day's scoring frame: four funds with complete inputs and one with an unknown momentum.

    The three RULE inputs are the only columns the composite reads, and the values are ordered so
    the hand-computed ranking (see :class:`RegistryFallbackScoringTests`) has no ties -- a tie would
    make the ``ts_code`` break in ``_ordered_by_score`` decide the assertion instead of the features.
    """
    return pd.DataFrame([
        {'ts_code': '512760.SH', 'mom_60d': 0.30, 'vol_20d': 0.020, 'rs_vs_benchmark_60d': 0.25},
        {'ts_code': '515980.SH', 'mom_60d': 0.20, 'vol_20d': 0.025, 'rs_vs_benchmark_60d': 0.15},
        {'ts_code': '510300.SH', 'mom_60d': 0.10, 'vol_20d': 0.030, 'rs_vs_benchmark_60d': 0.05},
        {'ts_code': '511880.SH', 'mom_60d': 0.01, 'vol_20d': 0.010, 'rs_vs_benchmark_60d': -0.02},
        {'ts_code': '159934.SZ', 'mom_60d': float('nan'), 'vol_20d': 0.015, 'rs_vs_benchmark_60d': 0.10},
    ])


class _VolumePenaltyBooster:
    """A picklable stand-in for ``lgb.Booster``: ``predict`` returns ``-vol_20d``.

    A real LightGBM model would make this test depend on a training run. All the loader needs is an
    object with a one-argument ``predict`` that returns a number per input row.
    """

    def predict(self, matrix):
        return -matrix['vol_20d']


class RegistryFallbackScoringTests(TestCase):
    """The cold-start contract between ``ModelVersion`` and the rotation engine (verification item 15).

    A production deployment with no trained model must still produce a book: ``load_active_predictor``
    returns ``None``, the engine scores with RULE, and ``ENSEMBLE`` renormalises onto the one source
    that answered. Each of those links is pinned separately here, because the failure mode of any one
    of them is a crash in the 19:45 beat task rather than a wrong number.
    """

    def register(self, version, **kwargs):
        kwargs.setdefault('model_type', 'ETF_LIGHTGBM')
        kwargs.setdefault('status', ModelVersion.Status.READY)
        kwargs.setdefault('is_active', True)
        kwargs.setdefault('artifact_path', '')
        kwargs.setdefault('feature_schema', [])
        return ModelVersion.objects.create(version=version, **kwargs)

    def test_an_empty_registry_yields_no_predictor(self):
        self.assertIsNone(train_lightgbm.load_active_predictor())
        self.assertEqual(ModelVersion.objects.count(), 0)

    def test_a_stock_model_version_is_not_served_as_an_etf_ranker(self):
        """The two pipelines share the table, so the query must be scoped by ``model_type``.

        Without the filter the CSI500 ranker would answer for the ETF rotation and score a
        cross-section of funds on a stock feature schema.
        """
        self.register('stock-1', model_type=ModelVersion.ModelType.LIGHTGBM)
        self.assertIsNone(train_lightgbm.load_active_predictor())

    def test_a_ready_but_inactive_model_is_not_served(self):
        self.register('etf-1', is_active=False)
        self.assertIsNone(train_lightgbm.load_active_predictor())

    def test_an_active_model_that_is_not_ready_is_not_served(self):
        """``TRAINING`` and ``FAILED`` rows are still being written or are known bad."""
        for status in (ModelVersion.Status.TRAINING, ModelVersion.Status.FAILED, ModelVersion.Status.ARCHIVED):
            with self.subTest(status=status):
                ModelVersion.objects.all().delete()
                self.register(f'etf-{status.lower()}', status=status)
                self.assertIsNone(train_lightgbm.load_active_predictor())

    def test_an_active_ready_model_with_no_readable_artifact_is_not_served(self):
        """The registry row is not the artifact: an empty or missing path must not serve a booster.

        This is the state a partly-deleted ``models/etf_rotation/`` directory leaves behind, and the
        honest answer is ``None`` so the engine falls back -- raising here would stop the signal.
        """
        self.register('etf-blank-path', artifact_path='')
        self.assertIsNone(train_lightgbm.load_active_predictor())
        self.register('etf-missing-dir', artifact_path='models/etf_rotation/tests/nope')
        self.assertIsNone(train_lightgbm.load_active_predictor())

    def test_a_registered_artifact_is_served_and_scores_the_features_it_was_trained_on(self):
        """The reversible half of the gate: registry row + readable artifact does return a callable."""
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, 'model.pkl'), 'wb') as handle:
                pickle.dump(_VolumePenaltyBooster(), handle, protocol=pickle.HIGHEST_PROTOCOL)
            with open(os.path.join(directory, 'metadata.json'), 'w', encoding='utf-8') as handle:
                handle.write('{"feature_names": ["mom_60d", "vol_20d", "rs_vs_benchmark_60d"]}')
            # An absolute ``artifact_path`` short-circuits the BASE_DIR join, which is the only reliable
            # way to aim this at a temp directory: the store caches its root once per process, and an
            # existing local directory means it is never consulted even when configured for remote.
            self.register('etf-tempdir', artifact_path=directory)
            predictor = train_lightgbm.load_active_predictor()
            self.assertIsNotNone(predictor)

            scored = predictor(_rule_cross_section())
            self.assertEqual(list(scored.columns), ['ts_code', 'score'])
            # One row per input fund, in input order -- including ``159934.SZ``, whose ``mom_60d`` is
            # unknown. A partially missing feature is LightGBM's missing-value branch, not a reason to
            # drop a candidate; exclusion is the RULE path's job (see the next test).
            self.assertEqual(
                list(scored['ts_code']),
                ['512760.SH', '515980.SH', '510300.SH', '511880.SH', '159934.SZ'],
            )
            self.assertEqual(
                [round(value, 9) for value in scored['score']],
                [-0.02, -0.025, -0.03, -0.01, -0.015],
            )

    def test_the_rule_path_needs_no_registered_model(self):
        """RULE scores a cross-section with an empty registry, and the composite is hand-checkable.

        Percentiles are taken over the four funds with complete inputs (``159934.SZ`` has an unknown
        ``mom_60d`` and is excluded, never zero-filled), so
        ``512760.SH = 0.6*1.00 + 0.2*1.00 - 0.2*0.50 = 0.70``.
        """
        result = rotation_engine.score_snapshots(
            _rule_cross_section(), model_type='RULE', momentum_lookback=60,
        )
        self.assertEqual(ModelVersion.objects.count(), 0)
        self.assertEqual(list(result['ts_code']), ['512760.SH', '515980.SH', '510300.SH', '511880.SH'])
        self.assertEqual([round(value, 10) for value in result['score']], [0.70, 0.45, 0.20, 0.15])

        metadata = result.attrs['rotation']
        self.assertEqual(metadata['model_type'], 'RULE')
        self.assertEqual(metadata['momentum_column'], 'mom_60d')
        self.assertFalse(metadata['momentum_column_substituted'])
        self.assertEqual(metadata['relative_strength_column'], 'rs_vs_benchmark_60d')
        self.assertFalse(metadata['relative_strength_column_substituted'])
        self.assertEqual(metadata['excluded_missing_features'], 1)
        self.assertEqual(metadata['excluded_missing_features_ts_codes'], ['159934.SZ'])

    def test_a_cross_section_with_no_momentum_column_fails_loudly(self):
        frame = _rule_cross_section().drop(columns=['mom_60d'])
        with self.assertRaises(rotation_engine.RotationEngineError) as caught:
            rotation_engine.score_snapshots(frame, model_type='RULE', momentum_lookback=60)
        self.assertIn('no mom_*d column', str(caught.exception))

    def test_lightgbm_scoring_names_the_module_with_nothing_registered(self):
        """The engine cannot silently hand back an empty ranking when the model is missing."""
        with self.assertRaises(rotation_engine.RotationEngineError) as caught:
            rotation_engine.score_snapshots(
                _rule_cross_section(), model_type='LIGHTGBM', momentum_lookback=60,
            )
        self.assertEqual(
            str(caught.exception),
            'apps.etf_rotation.train_lightgbm.load_active_predictor() found no active LIGHTGBM model.',
        )

    def test_the_ensemble_renormalises_onto_rule_when_no_model_is_registered(self):
        """Both ML sources are absent, so ENSEMBLE must rank exactly like RULE instead of raising."""
        result = rotation_engine.score_snapshots(
            _rule_cross_section(), model_type='ENSEMBLE', momentum_lookback=60,
        )
        ensemble = result.attrs['rotation']['ensemble']
        self.assertEqual(ensemble['sources_used'], ['RULE'])
        self.assertEqual(ensemble['sources_resolved_from_predictor'], [])
        self.assertEqual(ensemble['skipped_sources'], ['LIGHTGBM', 'LSTM'])
        self.assertEqual(sorted(ensemble['predictors_unavailable']), ['LIGHTGBM', 'LSTM'])
        self.assertEqual(ensemble['final_weights'], {'RULE': 1.0})
        self.assertEqual(list(result['ts_code']), ['512760.SH', '515980.SH', '510300.SH', '511880.SH'])


class FinanceMetricsTests(SimpleTestCase):
    """``apps.core.finance_metrics`` -- the conventions the rotation backtest reports through."""

    def test_max_drawdown_is_the_positive_peak_to_trough_magnitude(self):
        self.assertEqual(finance_metrics.max_drawdown([100, 120, 60, 90, 80]), 0.5)
        self.assertAlmostEqual(finance_metrics.max_drawdown([100, 80, 120]), 0.2, places=12)
        self.assertEqual(finance_metrics.max_drawdown([100, 110, 120]), 0.0)

    def test_max_drawdown_of_a_too_short_curve_is_none(self):
        """None, not 0.0: an unmeasurable drawdown must not read as a perfect one."""
        self.assertIsNone(finance_metrics.max_drawdown([100]))
        self.assertIsNone(finance_metrics.max_drawdown([]))
        self.assertIsNone(finance_metrics.max_drawdown([float('nan')]))

    def test_sharpe_uses_the_population_stdev_and_clamps_at_ten(self):
        # ``periods=1`` keeps the annualisation factor at 1 so the number is hand-checkable:
        # mean 0.05 over population stdev 0.1118034 = 0.4472136 (a sample stdev would give 0.3952847).
        self.assertAlmostEqual(finance_metrics.sharpe_ratio([0.1, -0.1, 0.2, 0.0], periods=1),
                               0.4472135955, places=9)
        self.assertAlmostEqual(finance_metrics.sharpe_ratio([0.01, 0.02, 0.03]), 10.0, places=9)
        self.assertAlmostEqual(finance_metrics.sharpe_ratio([-0.01, -0.02, -0.03]), -10.0, places=9)

    def test_sharpe_of_a_zero_variance_series_is_zero_and_too_short_is_none(self):
        self.assertEqual(finance_metrics.sharpe_ratio([0.01] * 5), 0.0)
        self.assertIsNone(finance_metrics.sharpe_ratio([0.01]))
        self.assertIsNone(finance_metrics.sharpe_ratio([]))

    def test_calmar_divides_annualised_return_by_drawdown_magnitude(self):
        self.assertAlmostEqual(finance_metrics.calmar_ratio(0.2, 0.5), 0.4, places=12)
        self.assertAlmostEqual(finance_metrics.calmar_ratio(0.2, -0.5), 0.4, places=12)
        self.assertIsNone(finance_metrics.calmar_ratio(0.2, 0.0))
        self.assertIsNone(finance_metrics.calmar_ratio(None, 0.5))

    def test_information_ratio_uses_the_sample_stdev(self):
        """``ddof=1`` here where Sharpe uses ``ddof=0``: 2.9368350, not the 3.0 the population form gives."""
        strategy = [0.01, 0.02] * 12
        benchmark = [0.0] * 24
        self.assertAlmostEqual(
            finance_metrics.information_ratio(strategy, benchmark, periods=1), 2.9368350311, places=9,
        )
        self.assertIsNone(finance_metrics.information_ratio([0.01] * 24, [0.0] * 24, periods=1))
        self.assertIsNone(finance_metrics.information_ratio(strategy[:19], benchmark[:19], periods=1))

    def test_alpha_beta_recovers_a_known_beta(self):
        benchmark = [math.sin(index / 3.0) * 0.01 for index in range(20)]
        regression = finance_metrics.alpha_beta([2.0 * value for value in benchmark], benchmark)
        self.assertAlmostEqual(regression['beta'], 2.0, places=9)
        self.assertAlmostEqual(regression['alpha_daily'], 0.0, places=9)
        self.assertAlmostEqual(regression['correlation'], 1.0, places=9)
        self.assertEqual(regression['observations'], 20)
        self.assertIsNone(finance_metrics.alpha_beta(benchmark[:19], benchmark[:19]))
        self.assertIsNone(finance_metrics.alpha_beta(benchmark, [0.0] * 20))

    def test_annualized_turnover_divides_two_way_notional_by_mean_equity(self):
        sessions = pd.date_range('2024-01-01', periods=13)
        equity = pd.Series([100_000.0] * 13, index=sessions)
        traded = pd.Series([1000.0] * 13, index=sessions)
        # (13000 / 100000) * (252 / 12) = 2.73; a session with no trade is a zero, not a gap.
        self.assertAlmostEqual(finance_metrics.annualized_turnover(traded, equity), 2.73, places=9)
        self.assertAlmostEqual(finance_metrics.annualized_turnover(traded.iloc[:12], equity), 2.52, places=9)
        self.assertEqual(finance_metrics.annualized_turnover(pd.Series(dtype='float64'), equity), 0.0)
        self.assertIsNone(finance_metrics.annualized_turnover(traded, [100.0]))

    def test_win_rate_counts_a_break_even_trade_as_a_loss(self):
        self.assertAlmostEqual(finance_metrics.win_rate([0.1, -0.1, 0.0]), 1.0 / 3.0, places=12)
        self.assertIsNone(finance_metrics.win_rate([]))

    def test_summarize_performance_returns_every_persisted_key(self):
        equity = [100.0, 105.0, 98.0, 112.0, 120.0]
        summary = finance_metrics.summarize_performance(equity, daily_traded_notional=[5000.0] * 5)
        for key in ('total_return', 'annualized_return', 'max_drawdown', 'sharpe_ratio', 'calmar_ratio',
                    'win_rate', 'turnover_annual', 'information_ratio', 'benchmark_return'):
            self.assertIn(key, summary)
        self.assertAlmostEqual(summary['total_return'], 0.2, places=12)
        self.assertEqual(summary['observations'], 4)
        self.assertEqual(summary['sessions'], 5)
        self.assertIsNone(summary['benchmark_return'])
