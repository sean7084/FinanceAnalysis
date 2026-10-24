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
import io
import itertools
import json
import math
import os
import pickle
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import talib
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core import finance_metrics
from apps.etf_rotation import (
    backtest_engine,
    comparison,
    features,
    rotation_engine,
    signals as rotation_signals,
    tasks as etf_tasks,
    train_lightgbm,
    train_lstm,
    views as etf_views,
)
from apps.etf_rotation.etf_universe import (
    TRACK_INDEX_MAX_LENGTH,
    classify_etf,
    eligible_universe,
    is_excluded_code,
    is_excluded_vehicle,
    is_index_fund,
    is_point_in_time_eligible,
    normalize_track_index,
    shares_to_aum_yuan,
)
from apps.etf_rotation.features import (
    MIN_AVG_AMOUNT_YUAN_DEFAULT,
    build_eligibility_frame,
    compute_features,
    resolve_pool_parameters,
    sessions_to_calendar_days,
)
from apps.etf_rotation.management.commands import run_etf_rotation_backtest as backtest_cmd
from apps.etf_rotation.management.commands import sweep_etf_rotation_lstm as sweep_cmd
from apps.etf_rotation.models import (
    ETFCategory,
    ETFDaily,
    ETFFeatureSnapshot,
    ETFUniverse,
    ETFRotationBacktest,
    ETFRotationSignal,
    ETFRotationSignalRun,
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
            # ``track_index_key`` and ``shares_outstanding`` ride along because the selection
            # constraints (one fund per tracked index, biggest-share cluster winner) need them
            # in the same frame as the scores. They are descriptors, not features: neither
            # matches a ``features.FEATURE_PREFIXES`` entry, so neither can reach a model.
            {
                'date', 'ts_code', 'name', 'category', 'is_eligible',
                'track_index_key', 'shares_outstanding',
                *backtest_engine.PIT_SNAPSHOT_FEATURE_COLUMNS,
            },
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
            {'drift_band_fraction': -0.1}, {'drift_band_fraction': 1.5},
            {'liquidity_mode': 'cap'}, {'adv_participation_cap': -1},
        ):
            with self.subTest(**overrides):
                with self.assertRaises(backtest_engine.BacktestConfigError):
                    self.config(**overrides).validate()

    def test_fingerprint_is_stable_and_sensitive_to_momentum_lookback(self):
        left = self.config(momentum_lookback=60)
        self.assertEqual(left.fingerprint(), self.config(momentum_lookback=60).fingerprint())
        self.assertNotEqual(left.fingerprint(), self.config(momentum_lookback=120).fingerprint())
        self.assertNotEqual(left.fingerprint(), self.config(commission_rate=0.0009).fingerprint())
        # A default change to the execution model must invalidate a stored cursor.
        self.assertNotEqual(left.fingerprint(), self.config(drift_band_fraction=0.01).fingerprint())
        self.assertNotEqual(left.fingerprint(), self.config(liquidity_mode='scale').fingerprint())
        self.assertNotEqual(left.fingerprint(), self.config(adv_participation_cap=0.1).fingerprint())
        self.assertEqual(len(left.fingerprint()), 16)

    def test_parameters_record_the_fee_model_and_the_label_block_it_excludes(self):
        payload = self.config().to_parameters()
        self.assertEqual(payload['pit_snapshot_feature_columns'], list(features.FEATURE_COLUMNS))
        self.assertEqual(payload['excluded_label_columns'], list(backtest_engine.LABEL_COLUMNS))
        self.assertEqual(payload['fee_model']['stamp_duty'], 0.0)
        self.assertEqual(payload['start_date'], '2024-01-01')


class _BacktestReportHarness:
    """Shared seam for the report/execution-policy tests: a cached book, no database."""

    DAYS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]

    def config(self, **overrides):
        values = {
            'start_date': self.DAYS[0], 'end_date': self.DAYS[-1],
            'initial_capital': 100_000.0,
            'commission_rate': 0.0003, 'min_commission': 5.0, 'slippage': 0.001,
            'liquidity_floor_multiple': 0.0,
            'secondary_benchmark_codes': (), 'compute_proxy_benchmark': False,
        }
        values.update(overrides)
        return backtest_engine.BacktestConfig(**values)

    def context(self, prices, *, snapshots=None, module=None, amounts=None, **overrides):
        """A context whose price cache is pre-populated, so no data source is consulted."""
        config = self.config(**overrides)
        source = SimpleNamespace(
            benchmark_close=lambda *args, **kwargs: pd.Series(dtype='float64'),
            snapshots=(lambda session: snapshots) if snapshots is not None else None,
        )
        book = backtest_engine.PriceBook(source, self.DAYS[0], self.DAYS[-1], self.DAYS)
        index = pd.Index(self.DAYS)
        for code, closes in prices.items():
            book._cache[code] = {
                'close': pd.Series(closes, index=index, dtype='float64'),
                'adj_close': pd.Series(closes, index=index, dtype='float64'),
                'amount': pd.Series(
                    [(amounts or {}).get(code, 1e9)] * len(self.DAYS), index=index, dtype='float64',
                ),
                'volume': pd.Series([1e7] * len(self.DAYS), index=index, dtype='float64'),
                'adj_factor': pd.Series([1.0] * len(self.DAYS), index=index, dtype='float64'),
            }
        return backtest_engine._Context(
            config, module=module, book=book, source=source, predictors={},
        )

    class _StubRotation:
        """The narrow slice of ``rotation_engine`` that ``_build_plan`` calls."""

        class RotationConfig:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        def __init__(self, turnovers):
            self.turnovers = list(turnovers)
            self.built = 0

        def score_snapshots(self, snapshots, **kwargs):
            return pd.DataFrame({
                'ts_code': snapshots['ts_code'].tolist(),
                'score': [0.5] * len(snapshots),
            })

        def build_rotation_plan(self, scored, snapshots, config, **kwargs):
            turnover = self.turnovers[min(self.built, len(self.turnovers) - 1)]
            self.built += 1
            return SimpleNamespace(
                legs=[SimpleNamespace(ts_code='512760.SH', weight=0.1)],
                defensive_asset='511010.SH',
                defensive_allocation=0.0,
                rankings=[{'ts_code': '512760.SH', 'score': 0.5}],
                metadata={'turnover': turnover},
            )

    def _seeded_context(self, *, min_trade_notional_fraction=0.005, **overrides):
        ctx = self.context(
            {'512760.SH': [1.0] * len(self.DAYS)},
            initial_capital=100_000.0,
            min_trade_notional_fraction=min_trade_notional_fraction,
            **overrides,
        )
        ctx.state.cash = 90_000.0
        ctx.state.positions['512760.SH'] = backtest_engine.PositionLot(
            ts_code='512760.SH', name='半导体ETF', shares=10_000, avg_cost=1.0,
            cost_notional=10_000.0, entry_date=self.DAYS[0],
            last_close=1.0, last_close_date=self.DAYS[0], last_adj_factor=1.0,
        )
        ctx.state.pending_plan = {
            'signal_date': self.DAYS[0],
            'target_weights': {'512760.SH': 0.101},
            'leg_meta': {},
        }
        return ctx


class BacktestReportInstrumentationTests(_BacktestReportHarness, SimpleTestCase):
    """Phase 2: the report explains a run without moving it.

    Every observation is derived from state that already existed (``trade_log``, the
    persisted curve) or is copied from the selection layer's own turnover metadata, so the
    equity curve, fills and realised P&L stay a pure function of prices, commission and
    slippage. The reference rerun's byte-identity against production row 256 is checked
    out of band; the ledger identities below are the CI-sized version of that gate.
    """

    def test_the_report_carries_one_entry_per_rebalance(self):
        turnovers = [
            {
                'mode': 'full', 'swaps_used': 1,
                'entries': [{'ts_code': '512760.SH', 'rank': 1, 'reason': 'vacant_slot'}],
                'exits': [{'ts_code': '515980.SH', 'reason': 'ineligible'}],
                'refills': 1,
                'displacements': [{'ts_code': '515980.SH', 'rank': 4, 'replaced_by': '512760.SH'}],
            },
            {
                'mode': 'kept', 'swaps_used': 0, 'entries': [],
                'exits': [], 'refills': 0, 'displacements': [],
            },
        ]
        module = self._StubRotation(turnovers)
        snapshots = pd.DataFrame({
            'ts_code': ['512760.SH', '515980.SH'],
            'name': ['半导体ETF', '芯片ETF'],
            'category': ['SECTOR_TECH', 'SECTOR_TECH'],
            'mom_60d': [0.3, 0.1],
        })
        ctx = self.context({}, snapshots=snapshots, module=module)
        ctx.state.equity_curve = [
            {'date': day.isoformat(), 'equity': 100_000.0, 'cash': 100_000.0,
             'holdings': 0.0, 'positions': 0, 'drawdown': 0.0}
            for day in self.DAYS[:2]
        ]

        backtest_engine._build_plan(ctx, self.DAYS[0], self.DAYS[1])
        backtest_engine._build_plan(ctx, self.DAYS[1], self.DAYS[2])
        report = backtest_engine._finalize_metrics(
            ctx, ctx.config, ctx.source, self.DAYS[:2],
        )['report']

        self.assertEqual(report['counters']['rebalances'], 2)
        self.assertEqual(report['counters']['signals_generated'], 2)
        self.assertEqual(
            set(report['rebalances'][0]),
            {'signal_date', 'execute_date', 'mode', 'swaps_used', 'entries', 'exits',
             'refills', 'displacements'},
        )
        self.assertEqual(
            [(item['signal_date'], item['execute_date']) for item in report['rebalances']],
            [('2024-01-02', '2024-01-03'), ('2024-01-03', '2024-01-04')],
        )
        self.assertEqual(report['rebalances'][0]['mode'], 'full')
        self.assertEqual(report['rebalances'][0]['swaps_used'], 1)
        self.assertEqual(
            report['rebalances'][0]['entries'],
            [{'ts_code': '512760.SH', 'rank': 1, 'reason': 'vacant_slot'}],
        )
        self.assertEqual(
            report['rebalances'][0]['displacements'],
            [{'ts_code': '515980.SH', 'rank': 4, 'replaced_by': '512760.SH'}],
        )
        self.assertEqual(report['rebalances'][0]['refills'], 1)
        # A plan that changed nothing still leaves a record: no fill row could tell this story.
        self.assertEqual(report['rebalances'][1]['mode'], 'kept')
        self.assertEqual(report['rebalances'][1]['swaps_used'], 0)

    def test_drift_and_discretionary_turnover_are_split_by_fill_reason(self):
        days = self.DAYS
        equity = pd.Series(
            [100.0, 101.0, 102.0, 103.0], index=pd.Index(days), dtype='float64',
        )
        trade_log = [
            {'date': days[0].isoformat(), 'status': 'FILLED', 'reason': 'rebalance_up', 'notional': 10.0},
            {'date': days[1].isoformat(), 'status': 'FILLED', 'reason': 'rebalance_down', 'notional': 20.0},
            {'date': days[2].isoformat(), 'status': 'FILLED', 'reason': 'rotated_out', 'notional': 30.0},
            {'date': days[3].isoformat(), 'status': 'SKIPPED_NO_PRICE', 'reason': 'rotated_out', 'notional': 0.0},
            {'date': days[3].isoformat(), 'status': 'REJECTED_LIQUIDITY', 'reason': 'rotated_out', 'notional': 0.0},
        ]

        facets = backtest_engine._turnover_facets(days, equity, trade_log)

        self.assertEqual(facets['buckets']['rebalance_up'], 'drift')
        self.assertEqual(facets['buckets']['rebalance_down'], 'drift')
        self.assertEqual(facets['buckets']['rotated_out'], 'discretionary')
        self.assertEqual(
            facets['notional'],
            {'drift_yuan': 30.0, 'discretionary_yuan': 30.0, 'other_yuan': 0.0, 'total_yuan': 60.0},
        )
        self.assertEqual(
            facets['by_reason_yuan'],
            {'rebalance_down': 20.0, 'rebalance_up': 10.0, 'rotated_out': 30.0},
        )
        self.assertAlmostEqual(facets['share']['drift_share'], 0.5)
        self.assertAlmostEqual(facets['share']['discretionary_share'], 0.5)
        self.assertAlmostEqual(facets['share']['other_share'], 0.0)
        # The two buckets plus ``other`` must reconcile with the headline column exactly.
        traded = pd.Series([10.0, 20.0, 30.0, 0.0], index=pd.Index(days), dtype='float64')
        self.assertAlmostEqual(
            facets['annualized']['total'],
            finance_metrics.annualized_turnover(traded, equity),
            places=12,
        )
        self.assertAlmostEqual(
            facets['annualized']['drift'],
            finance_metrics.annualized_turnover(
                pd.Series([10.0, 20.0, 0.0, 0.0], index=pd.Index(days), dtype='float64'), equity,
            ),
            places=12,
        )

    def test_a_closed_trade_pays_its_own_slippage(self):
        ctx = self.context({'512760.SH': [10.0, 11.0, 11.0, 11.0]})

        self.assertTrue(backtest_engine._buy(
            ctx, '512760.SH', 1_000, self.DAYS[0], weight=0.1,
            reason='rebalance_up', signal_date=None,
        ))
        self.assertTrue(backtest_engine._sell(
            ctx, '512760.SH', 1_000, self.DAYS[1], weight=0.0,
            reason='rotated_out', signal_date=None,
        ))

        closed = ctx.state.closed_trades[0]
        # 1000 shares, 10 -> 11 raw, 10 bps slippage each way, two 5 yuan floors.
        self.assertAlmostEqual(ctx.state.total_commission, 10.0, places=9)
        self.assertAlmostEqual(ctx.state.total_slippage, 21.0, places=9)
        self.assertAlmostEqual(closed['position_fees_paid'], 31.0, places=9)
        self.assertAlmostEqual(closed['pnl'], 969.0, places=9)
        self.assertAlmostEqual(ctx.state.cash, 100_969.0, places=6)
        self.assertEqual(ctx.state.min_commission_bound_fills, 2)
        # ``pnl`` is untouched by the fee-line change: the gross price move is fully
        # explained by realised P&L plus every cost the leg actually paid.
        self.assertAlmostEqual(closed['pnl'] + closed['position_fees_paid'], 1_000.0, places=9)

    def test_the_default_min_trade_fraction_is_the_hardcode_it_replaces(self):
        default = backtest_engine.BacktestConfig(
            start_date=self.DAYS[0], end_date=self.DAYS[-1],
        )
        self.assertEqual(default.min_trade_notional_fraction, 0.005)
        self.assertEqual(
            ETFRotationBacktest._meta.get_field('min_trade_notional_fraction').default,
            0.005,
        )

        # One board lot (100 yuan) against a 100,000 yuan book: suppressed at 0.5%, filled
        # at 0.05%. The default is the 0.5% that used to be written into ``_execute_plan``.
        suppressed = self._seeded_context()
        backtest_engine._execute_plan(suppressed, self.DAYS[1])
        self.assertEqual(suppressed.state.sub_minimum_deltas, 1)
        self.assertEqual(suppressed.state.trade_log, [])

        executed = self._seeded_context(min_trade_notional_fraction=0.0005)
        backtest_engine._execute_plan(executed, self.DAYS[1])
        self.assertEqual(executed.state.sub_minimum_deltas, 0)
        self.assertEqual([item['status'] for item in executed.state.trade_log], ['FILLED'])
        self.assertEqual(executed.state.trade_log[0]['shares'], 100)
        self.assertAlmostEqual(executed.state.trade_log[0]['weight'], 0.101)

    def test_sub_window_facets_cover_the_window_without_overlap_or_gap(self):
        days = [date(2024, 1, 1) + timedelta(days=offset) for offset in range(9)]
        equity = pd.Series(
            [100.0, 102.0, 101.0, 104.0, 103.0, 106.0, 105.0, 108.0, 107.0],
            index=pd.Index(days), dtype='float64',
        )
        traded = pd.Series(
            [0.0, 10.0, 0.0, 20.0, 0.0, 30.0, 0.0, 40.0, 0.0],
            index=pd.Index(days), dtype='float64',
        )

        facets = backtest_engine._sub_window_facets(equity, traded)

        self.assertEqual([facet['block'] for facet in facets], [1, 2, 3])
        self.assertEqual(facets[0]['base_date'], days[0].isoformat())
        for previous, current in zip(facets, facets[1:]):
            self.assertEqual(current['base_date'], previous['end_date'])
            self.assertEqual(
                days.index(date.fromisoformat(current['start_date'])),
                days.index(date.fromisoformat(previous['end_date'])) + 1,
            )
        self.assertEqual(sum(facet['sessions'] for facet in facets), len(days) - 1)
        self.assertAlmostEqual(
            math.prod(1.0 + facet['total_return'] for facet in facets),
            float(equity.iloc[-1]) / float(equity.iloc[0]),
            places=12,
        )
        for facet in facets:
            self.assertEqual(set(facet), {
                'block', 'start_date', 'end_date', 'base_date', 'sessions',
                'total_return', 'max_drawdown', 'sharpe_ratio', 'turnover_annual',
                'traded_notional_yuan',
            })

    def test_the_resolved_model_version_is_recorded_and_null_for_rule(self):
        rule = pd.DataFrame({'ts_code': ['512760.SH'], 'score': [1.0]})
        rule.attrs['rotation'] = {'model_type': 'RULE'}
        self.assertEqual(
            backtest_engine._model_version_summary(rule, 'RULE'), {'RULE': None},
        )

        ml = pd.DataFrame({'ts_code': ['512760.SH'], 'score': [1.0]})
        ml.attrs['rotation'] = {
            'model_version': {'id': 7, 'version': '20261024', 'model_type': 'LIGHTGBM'},
        }
        self.assertEqual(
            backtest_engine._model_version_summary(ml, 'LIGHTGBM'),
            {'LIGHTGBM': {'id': 7, 'version': '20261024', 'model_type': 'LIGHTGBM'}},
        )

        ensemble = pd.DataFrame({'ts_code': ['512760.SH'], 'score': [1.0]})
        ensemble.attrs['rotation'] = {
            'source_metadata': {
                'RULE': {'model_type': 'RULE'},
                'LIGHTGBM': {'model_version': {'id': 7}},
            },
            'ensemble': {'skipped_sources': ['LSTM']},
        }
        self.assertEqual(
            backtest_engine._model_version_summary(ensemble, 'ENSEMBLE'),
            {'RULE': None, 'LIGHTGBM': {'id': 7}, 'LSTM': None},
        )

        # The identity travels from the loader-attached callable, not a second "active
        # model" query, so the report names the version that actually ranked the run.
        def predictor(frame):
            return pd.DataFrame({
                'ts_code': frame['ts_code'].tolist(),
                'score': [1.0] * len(frame),
            })

        predictor.model_version = {
            'id': 7, 'version': 'v1', 'model_type': 'LIGHTGBM', 'artifact_path': '/tmp/x',
        }
        scored = rotation_engine.score_snapshots(
            pd.DataFrame({'ts_code': ['A', 'B'], 'mom_60d': [0.1, 0.2]}),
            model_type='LIGHTGBM',
            momentum_lookback=60,
            lgb_predictor=predictor,
        )
        self.assertEqual(
            scored.attrs['rotation']['model_version'],
            {'id': 7, 'version': 'v1', 'model_type': 'LIGHTGBM'},
        )

    def test_the_reference_configuration_is_byte_identical_after_instrumentation(self):
        """The ledger identity the reference row's rerun checks at production scale.

        Row 256 is rerun out of band (equity curve digest and all 597 fills); this pins the
        same arithmetic in CI. Instrumentation adds counters and a report-only fee line, but
        cash, marks and realised P&L must remain a pure function of price, commission and
        slippage, and the fee line must reconcile with the gross move.
        """
        ctx = self.context({'512760.SH': [10.0, 10.0, 11.0, 11.0]}, initial_capital=480_000.0)

        backtest_engine._mark_equity(ctx, self.DAYS[0])
        self.assertTrue(backtest_engine._buy(
            ctx, '512760.SH', 1_000, self.DAYS[1], weight=0.02,
            reason='rebalance_up', signal_date=self.DAYS[0],
        ))
        backtest_engine._mark_equity(ctx, self.DAYS[1])
        self.assertTrue(backtest_engine._sell(
            ctx, '512760.SH', 1_000, self.DAYS[2], weight=0.0,
            reason='rotated_out', signal_date=self.DAYS[1],
        ))
        backtest_engine._mark_equity(ctx, self.DAYS[2])

        self.assertEqual(
            [point['equity'] for point in ctx.state.equity_curve],
            [480_000.0, 479_985.0, 480_969.0],
        )
        self.assertEqual(
            [point['cash'] for point in ctx.state.equity_curve],
            [480_000.0, 469_985.0, 480_969.0],
        )
        closed = ctx.state.closed_trades[0]
        self.assertAlmostEqual(closed['pnl'], 969.0, places=9)
        self.assertAlmostEqual(closed['position_fees_paid'], 31.0, places=9)
        self.assertAlmostEqual(ctx.state.total_commission + ctx.state.total_slippage, 31.0, places=9)
        summary = finance_metrics.summarize_performance(
            pd.Series(
                [point['equity'] for point in ctx.state.equity_curve],
                index=pd.Index([date.fromisoformat(point['date']) for point in ctx.state.equity_curve]),
                dtype='float64',
            ),
        )
        self.assertAlmostEqual(summary['total_return'], 480_969.0 / 480_000.0 - 1.0, places=12)


class BacktestExecutionPolicyTests(_BacktestReportHarness, SimpleTestCase):
    """Phase 3: the band and the capacity cap, both off by default.

    ``drift_band_fraction=0`` and ``liquidity_mode='reject'``/``cap=0`` leave the Phase 2
    arithmetic untouched; these tests pin the opt-in branch and the resume contract around
    it.
    """

    def test_a_band_of_zero_suppresses_nothing(self):
        executed = self._seeded_context(min_trade_notional_fraction=0.0005, drift_band_fraction=0.0)
        backtest_engine._execute_plan(executed, self.DAYS[1])
        self.assertEqual(executed.state.band_suppressed_deltas, 0)
        self.assertEqual(executed.state.sub_minimum_deltas, 0)
        self.assertEqual([item['status'] for item in executed.state.trade_log], ['FILLED'])

        # The same 100 yuan delta, below 2% of a 10,100 yuan target, now drifts instead.
        banded = self._seeded_context(min_trade_notional_fraction=0.0005, drift_band_fraction=0.02)
        backtest_engine._execute_plan(banded, self.DAYS[1])
        self.assertEqual(banded.state.band_suppressed_deltas, 1)
        self.assertEqual(banded.state.sub_minimum_deltas, 0)
        self.assertEqual(banded.state.trade_log, [])

    def test_the_band_never_suppresses_a_mandatory_exit(self):
        ctx = self._seeded_context(min_trade_notional_fraction=0.0005, drift_band_fraction=0.5)
        ctx.state.pending_plan = {
            'signal_date': self.DAYS[0],
            'target_weights': {},
            'leg_meta': {},
        }

        backtest_engine._execute_plan(ctx, self.DAYS[1])

        self.assertEqual([item['reason'] for item in ctx.state.trade_log], ['rotated_out'])
        self.assertEqual(ctx.state.trade_log[0]['status'], 'FILLED')
        self.assertEqual(ctx.state.trade_log[0]['shares'], 10_000)
        self.assertEqual(ctx.state.band_suppressed_deltas, 0)
        self.assertNotIn('512760.SH', ctx.state.positions)

    def test_a_paused_banded_run_resumes_to_the_same_curve(self):
        def seeded():
            ctx = self.context(
                {'512760.SH': [1.0] * len(self.DAYS), '515980.SH': [5.0] * len(self.DAYS)},
                initial_capital=100_000.0,
                min_trade_notional_fraction=0.0005,
                drift_band_fraction=0.02,
            )
            ctx.state.cash = 90_000.0
            ctx.state.positions['512760.SH'] = backtest_engine.PositionLot(
                ts_code='512760.SH', name='半导体ETF', shares=10_000, avg_cost=1.0,
                cost_notional=10_000.0, entry_date=self.DAYS[0],
                last_close=1.0, last_close_date=self.DAYS[0], last_adj_factor=1.0,
            )
            ctx.state.equity_curve = [
                {'date': day.isoformat(), 'equity': 100_000.0, 'cash': 90_000.0,
                 'holdings': 10_000.0, 'positions': 1, 'drawdown': 0.0}
                for day in self.DAYS[:2]
            ]
            ctx.state.pending_plan = {
                'signal_date': self.DAYS[0],
                # One banded no-op (512760) and one real buy (515980) in the same plan.
                'target_weights': {'512760.SH': 0.101, '515980.SH': 0.02},
                'leg_meta': {},
            }
            return ctx

        live = seeded()
        run = MagicMock()
        run.report = None
        run.equity_curve = []
        run.trade_log = []
        backtest_engine._persist_progress(run, live, cursor=2, total_sessions=4)

        resumed = seeded()
        resumed.state.cash = 0.0
        resumed.state.positions.clear()
        resumed.state.pending_plan = None
        self.assertEqual(backtest_engine._restore_state(run, resumed), 2)
        self.assertEqual(resumed.state.pending_plan['target_weights'], live.state.pending_plan['target_weights'])

        backtest_engine._execute_plan(live, self.DAYS[1])
        backtest_engine._execute_plan(resumed, self.DAYS[1])

        self.assertEqual(resumed.state.band_suppressed_deltas, 1)
        self.assertEqual(resumed.state.band_suppressed_deltas, live.state.band_suppressed_deltas)
        self.assertEqual(resumed.state.trade_log, live.state.trade_log)
        self.assertAlmostEqual(resumed.state.cash, live.state.cash, places=9)
        self.assertEqual(
            {code: lot.shares for code, lot in resumed.state.positions.items()},
            {code: lot.shares for code, lot in live.state.positions.items()},
        )

        # A checkpoint taken after the suppressed plan must carry the non-zero counter
        # and the filled leg, not just the cash balance.
        after = MagicMock()
        after.report = None
        after.equity_curve = []
        after.trade_log = []
        backtest_engine._persist_progress(after, live, cursor=2, total_sessions=4)
        restored = seeded()
        restored.state.cash = 0.0
        restored.state.positions.clear()
        backtest_engine._restore_state(after, restored)
        self.assertEqual(restored.state.band_suppressed_deltas, 1)
        self.assertEqual(
            restored.state.positions['515980.SH'].shares,
            live.state.positions['515980.SH'].shares,
        )

    def test_capacity_scaling_renormalises_before_the_defensive_fallback(self):
        ctx = self.context(
            {'A': [1.0] * len(self.DAYS), 'B': [1.0] * len(self.DAYS), 'D': [1.0] * len(self.DAYS)},
            amounts={'A': 300_000.0, 'B': 500_000.0, 'D': 1e9},
            initial_capital=100_000.0,
            defensive_asset='D',
            liquidity_floor_multiple=10.0,
            liquidity_lookback_sessions=2,
            liquidity_mode='scale',
            adv_participation_cap=0.1,
        )

        kept, rejected = backtest_engine._apply_liquidity_floor(
            ctx, {'A': 0.5, 'B': 0.4, 'D': 0.1}, self.DAYS[-1],
        )

        # A is clipped 0.5 -> 0.3 (cap 0.1 x 300k / 100k). B absorbs 0.1 of the freed
        # weight up to its own 0.5 cap; only the last 0.1 lands on the defensive sleeve.
        self.assertEqual(rejected, [])
        self.assertAlmostEqual(kept['A'], 0.3, places=12)
        self.assertAlmostEqual(kept['B'], 0.5, places=12)
        self.assertAlmostEqual(kept['D'], 0.2, places=12)
        self.assertAlmostEqual(sum(kept.values()), 1.0, places=12)
        self.assertEqual(len(ctx.state.capacity_scalings), 1)
        scaling = ctx.state.capacity_scalings[0]
        self.assertEqual(scaling['ts_code'], 'A')
        self.assertAlmostEqual(scaling['planned_weight'], 0.5)
        self.assertAlmostEqual(scaling['capacity_weight'], 0.3)
        self.assertAlmostEqual(scaling['capacity_notional_yuan'], 30_000.0)

    def test_reject_mode_is_untouched_by_the_capacity_knobs(self):
        def run(cap):
            ctx = self.context(
                {'A': [1.0] * len(self.DAYS), 'B': [1.0] * len(self.DAYS), 'D': [1.0] * len(self.DAYS)},
                amounts={'A': 300_000.0, 'B': 500_000.0, 'D': 1e9},
                initial_capital=100_000.0,
                defensive_asset='D',
                liquidity_floor_multiple=10.0,
                liquidity_lookback_sessions=2,
                liquidity_mode='reject',
                adv_participation_cap=cap,
            )
            return backtest_engine._apply_liquidity_floor(
                ctx, {'A': 0.5, 'B': 0.4, 'D': 0.1}, self.DAYS[-1],
            )

        with_cap, rejected = run(0.1)
        without_cap, baseline_rejected = run(0.0)

        self.assertEqual(with_cap, without_cap)
        self.assertEqual(rejected, baseline_rejected)
        # A needs 500k of daily turnover for a 50k leg at the 10x floor and only trades
        # 300k, so reject mode drops it into the defensive sleeve -- the cap never runs.
        self.assertEqual([item['ts_code'] for item in rejected], ['A'])
        self.assertAlmostEqual(with_cap['D'], 0.6, places=12)
        self.assertNotIn('A', with_cap)


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


class BacktestLeaderboardOrderingTests(TestCase):
    """``?sort=`` must rank runs that produced a metric above runs that did not.

    PostgreSQL defaults to NULLS FIRST for DESC, so a backtest that failed or was
    abandoned before computing ``alpha_vs_benchmark`` headed the leaderboard and read as
    the best result the module had produced. Every nullable metric sort pins nulls last.
    """

    def _backtest(self, name, **metrics):
        return ETFRotationBacktest.objects.create(
            name=name,
            status=ETFRotationBacktest.Status.COMPLETED,
            start_date=date(2024, 1, 1),
            end_date=date(2026, 10, 20),
            rebalance_frequency=ETFRotationBacktest.RebalanceFrequency.MONTHLY,
            model_type=ETFRotationBacktest.ModelType.RULE,
            **metrics,
        )

    def _names(self, sort):
        response = self.client.get('/api/v1/etf/backtests/', {'sort': sort})
        self.assertEqual(response.status_code, 200)
        return [row['name'] for row in response.data['results']]

    def test_descending_metric_sorts_put_a_run_with_no_metric_last(self):
        self._backtest('aborted')
        self._backtest('best', alpha_vs_benchmark=0.18, sharpe_ratio=0.91, total_return=0.78)
        self._backtest('worst', alpha_vs_benchmark=-0.02, sharpe_ratio=-1.19, total_return=-0.08)
        for sort in ('alpha', 'sharpe', 'return'):
            with self.subTest(sort=sort):
                self.assertEqual(self._names(sort), ['best', 'worst', 'aborted'])

    def test_the_ascending_drawdown_sort_also_puts_a_run_with_no_metric_last(self):
        """Worst drawdown first is the point of this sort, so nulls must not lead it."""
        self._backtest('aborted')
        self._backtest('shallow', max_drawdown=0.18)
        self._backtest('deep', max_drawdown=0.42)
        self.assertEqual(self._names('drawdown'), ['shallow', 'deep', 'aborted'])

    def test_an_unrecognised_sort_falls_back_to_newest_first(self):
        """An unknown key is ignored rather than erroring, and must not silently sort on a metric."""
        self._backtest('older')
        self._backtest('newer', alpha_vs_benchmark=0.18)
        self.assertEqual(self._names('nonsense'), ['newer', 'older'])


class SweepSafeRegistrationTests(TestCase):
    """``activate=False`` registers a candidate without disturbing what is serving today.

    Phase 0 of the follow-on plan. Both trainers used to hardcode ``is_active: True`` and flip every
    prior row of that type, so a 24-trial LSTM sweep would leave whichever trial finished *last*
    loadable by ``rotation_engine.load_active_predictor`` -- the same class of mistake the ``INSAMPLE-``
    relabelling had to clean up. Each trainer is pinned in both directions: suppression must leave the
    active row alone, and the default must still take over (a deploy is a deliberate act).
    """

    def _lgbm_register(self, version, *, activate=None):
        kwargs = {
            'model_type_label': 'ETF_LIGHTGBM',
            'version': version,
            'training_start': date(2019, 1, 1),
            'training_end': date(2023, 12, 31),
            'metrics': {'rank_ic': -0.01},
            'feature_columns': ['mom_60d', 'vol_20d'],
            'metadata': {'sweep': {'grid': version}},
            'artifact_path': f'models/etf_rotation/tests/{version}',
        }
        if activate is not None:
            kwargs['activate'] = activate
        return train_lightgbm._register_model_version(**kwargs)

    def _lstm_config(self, version_tag):
        config = train_lstm._build_config(
            model_type_label='ETF_LSTM', start_date=date(2019, 1, 1), end_date=date(2023, 12, 31),
            n_folds=2, horizon_days=20, sequence_length=5, hidden_size=64, num_layers=1, dropout=0.2,
            epochs=1, batch_size=8, learning_rate=1e-3, patience=1, params=None,
            version_tag=version_tag,
        )
        # ``train_walk_forward`` resolves the feature list from the panel and writes it back onto the
        # config before registering; the registry helper reads it from there, so a test driving the
        # helper directly has to stand in for that step.
        config['base_feature_names'] = ['mom_60d', 'vol_20d']
        return config

    def _lstm_register(self, version, *, activate=None):
        kwargs = {
            'version': version,
            'config': self._lstm_config(version),
            'artifact_relative_path': f'models/etf_rotation/tests/{version}',
            'fold_reports': [],
            'headline': {'rank_ic': 0.03, 'icir': 0.4},
            'aggregate': {'folds_trained': 2, 'folds_with_positive_ic': 1},
            'panel': {'feature_names': ['mom_60d', 'vol_20d']},
            'scaler_stats': {},
            'training_start': date(2019, 1, 1),
            'training_end': date(2023, 12, 31),
            'metadata': {'sweep': {'grid': version}},
        }
        if activate is not None:
            kwargs['activate'] = activate
        return train_lstm._register_model_version(**kwargs)

    def test_a_suppressed_lightgbm_trial_stays_inactive_and_leaves_the_incumbent_active(self):
        incumbent = self._lgbm_register('etf-prod-v1')
        self.assertTrue(incumbent.is_active)

        trial = self._lgbm_register('tune-lstm-hidden128-layer2', activate=False)

        trial.refresh_from_db()
        incumbent.refresh_from_db()
        self.assertFalse(trial.is_active)
        self.assertEqual(trial.status, ModelVersion.Status.READY)
        self.assertTrue(incumbent.is_active)
        self.assertEqual(
            list(ModelVersion.objects.filter(is_active=True).values_list('version', flat=True)),
            ['etf-prod-v1'],
        )

    def test_a_suppressed_lstm_trial_stays_inactive_and_leaves_the_incumbent_active(self):
        incumbent = self._lstm_register('etf-prod-v1')
        self.assertTrue(incumbent.is_active)

        trial = self._lstm_register('tune-lstm-hidden64-dropout035', activate=False)

        trial.refresh_from_db()
        incumbent.refresh_from_db()
        self.assertFalse(trial.is_active)
        self.assertTrue(incumbent.is_active)
        self.assertEqual(
            list(ModelVersion.objects.filter(is_active=True).values_list('version', flat=True)),
            ['etf-prod-v1'],
        )

    def test_the_default_registration_still_takes_over(self):
        """Suppressing is opt-in: a plain training run must keep deploying what it trained."""
        incumbent = self._lgbm_register('etf-prod-v1')
        successor = self._lgbm_register('etf-prod-v2')

        incumbent.refresh_from_db()
        successor.refresh_from_db()
        self.assertFalse(incumbent.is_active)
        self.assertTrue(successor.is_active)

        incumbent = self._lstm_register('lstm-prod-v1')
        successor = self._lstm_register('lstm-prod-v2')

        incumbent.refresh_from_db()
        successor.refresh_from_db()
        self.assertFalse(incumbent.is_active)
        self.assertTrue(successor.is_active)

    def test_an_active_incumbent_of_another_model_type_survives_both_an_flip_and_a_suppression(self):
        """The sweep writes ETF_LSTM; the stock pipeline's active row is not its business."""
        stock = ModelVersion.objects.create(
            model_type=ModelVersion.ModelType.LIGHTGBM, version='stock-v9', is_active=True,
            status=ModelVersion.Status.READY, artifact_path='', feature_schema=[],
        )
        self._lgbm_register('tune-lstm-a', activate=False)
        self._lgbm_register('tune-lstm-b')

        stock.refresh_from_db()
        self.assertTrue(stock.is_active)

    def test_both_trainers_thread_activate_into_their_registry_call(self):
        """A parameter nothing passes is a parameter that does nothing.

        Training itself needs a GPU and a labelled panel, so the threading is pinned from the source
        rather than executed: the public seam declares ``activate`` and hands it to
        ``_register_model_version`` rather than letting the helper fall back to its default.
        """
        for module in (train_lightgbm, train_lstm):
            with self.subTest(module=module.__name__):
                self.assertEqual(
                    inspect.signature(module.train_walk_forward).parameters['activate'].default,
                    True,
                    f'{module.__name__}.train_walk_forward must default to deploying what it trained',
                )
                self.assertIn(
                    'activate=activate',
                    inspect.getsource(module.train_walk_forward),
                    f'{module.__name__}.train_walk_forward never forwards activate to the registry',
                )

    def test_suppressed_sweep_metadata_is_persisted_for_provenance(self):
        """A candidate row is only useful if it records the grid it came from.

        ``metadata['sweep']`` is how Phase C distinguishes a tuning artifact from evidence; the
        suppression path writes the same row shape, so it has to survive the round trip.
        """
        trial = self._lgbm_register('tune-lstm-lr0.05', activate=False)
        trial.refresh_from_db()
        self.assertEqual(trial.metadata['sweep'], {'grid': 'tune-lstm-lr0.05'})

        trial = self._lstm_register('tune-lstm-lr0.1', activate=False)
        trial.refresh_from_db()
        self.assertEqual(trial.metadata['sweep'], {'grid': 'tune-lstm-lr0.1'})


class BacktestComparisonCurveTests(TestCase):
    """``comparison_curve`` rebuilds the series the engine never persisted.

    Phase A of the follow-on plan. ``_finalize_metrics`` reduces each benchmark to scalars and drops the
    levels, so the only way the existing rows become chartable without rerunning them is by recomputing the
    benchmark from ``markets.BenchmarkIndexDaily`` and rescaling it onto the strategy's opening equity. Two
    properties make or break that chart and are pinned here rather than against a live row: every series must
    be measured over the *same* sessions the strategy traded (the ETF bar feed has real holes -- production
    row 82 is missing 34 sessions to ``ETFDaily`` gaps, and an unaligned index series plotted 675 points
    against the strategy's 641), and a benchmark the row never stored must still appear.
    """

    SESSIONS = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
    WINDOW_END = date(2024, 1, 10)

    def _backtest(self, name, equity, *, status=None, **kwargs):
        """A COMPLETED row whose ``equity_curve`` marks one point per leading session.

        Only ``date`` and ``equity`` are written: the payload reads nothing else off a stored point, so a row
        that predates this endpoint carries everything the chart needs in the same minimal shape.
        """
        return ETFRotationBacktest.objects.create(
            name=name,
            status=status or ETFRotationBacktest.Status.COMPLETED,
            start_date=self.SESSIONS[0],
            end_date=self.WINDOW_END,
            rebalance_frequency=ETFRotationBacktest.RebalanceFrequency.MONTHLY,
            model_type=ETFRotationBacktest.ModelType.RULE,
            equity_curve=[
                {'date': day.isoformat(), 'equity': value}
                for day, value in zip(self.SESSIONS, equity)
            ],
            **kwargs,
        )

    def _benchmark(self, code, closes):
        """``BenchmarkIndexDaily`` rows for explicit (session, close) pairs -- the holes are the point."""
        BenchmarkIndexDaily.objects.bulk_create(
            [
                BenchmarkIndexDaily(
                    index_code=code,
                    index_name='中证500全收益',
                    trade_date=day,
                    close=Decimal(str(value)),
                )
                for day, value in closes
            ]
        )

    def _payload(self, run, **params):
        response = self.client.get(f'/api/v1/etf/backtests/{run.id}/comparison_curve/', params)
        self.assertEqual(response.status_code, 200)
        return response.data

    def _spy(self):
        """Count rebuilds: the view binds the builder by name, so that is the seam to wrap."""
        return patch.object(
            etf_views, 'build_etf_comparison_payload', wraps=comparison.build_etf_comparison_payload
        )

    def test_every_series_is_rescaled_onto_the_strategy_opening_equity(self):
        run = self._backtest('strategy', [480000, 460000, 500000, 528000])
        self._benchmark('H00905.CSI', list(zip(self.SESSIONS, [4000, 4000, 4000, 4200])))

        payload = self._payload(run)

        self.assertEqual(payload['available_series_keys'], ['selected_run', 'h00905_csi'])
        self.assertEqual([series['key'] for series in payload['series']], ['selected_run', 'h00905_csi'])
        self.assertIsNone(payload['compare_target'])
        self.assertIsNone(payload['message'])
        self.assertEqual(
            payload['run'],
            {
                'id': run.id,
                'name': 'strategy',
                'status': 'COMPLETED',
                'start_date': '2024-01-02',
                'end_date': '2024-01-10',
                'initial_capital': 480000.0,
                'model_type': 'RULE',
                'benchmark_code': 'H00905.CSI',
            },
        )
        for series in payload['series']:
            self.assertEqual(series['points'][0]['value'], 480000.0)
            self.assertEqual(set(series['points'][0]), {'date', 'value', 'drawdown'})

        strategy, benchmark = payload['series']
        self.assertEqual(strategy['kind'], 'backtest')
        self.assertEqual(strategy['label'], f'#{run.id} strategy')
        self.assertAlmostEqual(strategy['total_return'], 0.10)
        self.assertAlmostEqual(strategy['max_drawdown'], 460000 / 480000 - 1)
        self.assertEqual(benchmark['kind'], 'benchmark')
        self.assertEqual(benchmark['index_code'], 'H00905.CSI')
        self.assertEqual(
            [point['value'] for point in benchmark['points']],
            [480000.0, 480000.0, 480000.0, 504000.0],
        )
        self.assertAlmostEqual(benchmark['total_return'], 0.05)

    def test_a_hole_in_the_benchmark_feed_is_filled_forward_so_both_curves_share_the_session_grid(self):
        """The Phase A acceptance criterion: strategy and benchmark of *equal length*.

        ``backtest_engine._benchmark_returns`` forward-fills the index onto the strategy calendar before
        computing the row's alpha, so a session the index feed lacks is a flat day rather than a dropped
        observation. The payload has to align the same way or the chart plots two different samples and the
        drawn benchmark stops being the one the stored metrics were measured against.
        """
        run = self._backtest('strategy', [480000, 490000, 500000, 510000])
        self._benchmark(
            'H00905.CSI',
            [
                (self.SESSIONS[0], 4000),
                (self.SESSIONS[2], 4400),
                (self.SESSIONS[3], 4400),
                (date(2024, 1, 8), 4840),
            ],
        )

        payload = self._payload(run)

        expected_dates = [day.isoformat() for day in self.SESSIONS]
        for series in payload['series']:
            self.assertEqual([point['date'] for point in series['points']], expected_dates)
        self.assertEqual(
            [point['value'] for point in payload['series'][1]['points']],
            [480000.0, 480000.0, 528000.0, 528000.0],
        )

    def test_a_secondary_code_from_parameters_is_plotted_and_a_missing_one_is_named(self):
        """Chart the benchmarks the row was actually compared to, and name the ones that could not be drawn."""
        run = self._backtest(
            'with-secondary',
            [480000, 490000, 500000, 510000],
            parameters={'secondary_benchmark_codes': ['000905.SH', 'H00905.CSI', '']},
        )
        self._benchmark('000905.SH', list(zip(self.SESSIONS, [5000, 5000, 5000, 5100])))

        payload = self._payload(run)

        self.assertEqual([series['key'] for series in payload['series']], ['selected_run', '000905_sh'])
        self.assertEqual(payload['available_series_keys'], ['selected_run', '000905_sh'])
        self.assertEqual(payload['series'][1]['label'], 'Secondary benchmark 000905.SH')
        self.assertEqual(
            payload['message'],
            'No rows in markets.BenchmarkIndexDaily for: H00905.CSI. Sync the index history to plot it.',
        )

    def test_the_benchmark_labels_match_the_ones_the_report_writes(self):
        """``_finalize_metrics`` spells these strings into ``report``; a chart and a report must not diverge."""
        run = self._backtest(
            'labels',
            [480000, 490000],
            parameters={'secondary_benchmark_codes': ['000905.SH']},
        )
        two_days = self.SESSIONS[:2]
        self._benchmark('H00905.CSI', list(zip(two_days, [4000, 4100])))
        self._benchmark('000905.SH', list(zip(two_days, [5000, 5050])))

        payload = self._payload(run)

        self.assertEqual(
            [series['label'] for series in payload['series']],
            [f'#{run.id} labels', 'Primary benchmark H00905.CSI', 'Secondary benchmark 000905.SH'],
        )

    def test_a_run_that_never_finished_or_never_wrote_a_curve_says_so(self):
        """An empty ``series`` with no explanation is indistinguishable from a broken chart."""
        cases = (
            ('pending', self._backtest('pending', [], status=ETFRotationBacktest.Status.PENDING),
             'Comparison curves are available after the run completes.'),
            ('abandoned', self._backtest('abandoned', []),
             'No equity curve is stored for this run yet.'),
        )
        for name, run, message in cases:
            with self.subTest(name=name):
                payload = self._payload(run)
                self.assertEqual(payload['series'], [])
                self.assertEqual(payload['available_series_keys'], [])
                self.assertIsNone(payload['compare_target'])
                self.assertEqual(payload['message'], message)

    def test_extra_ids_overlay_only_finished_runs_on_the_requested_run_scale(self):
        """An overlay is only comparable once it is drawn from the same opening equity.

        Both spellings are accepted because the stock chart component sends the repeated param and the ETF
        workbench sends the comma list.
        """
        serving = self._backtest('serving', [480000, 490000])
        overlay = self._backtest('overlay', [400000, 440000])
        running = self._backtest('running', [400000, 400000], status=ETFRotationBacktest.Status.RUNNING)
        two_days = self.SESSIONS[:2]
        self._benchmark('H00905.CSI', list(zip(two_days, [4000, 4200])))

        for params in (
            {'extra_ids': f'{serving.id},{overlay.id},{running.id}'},
            {'extra_compare_run_id': [str(serving.id), str(overlay.id), str(running.id)]},
        ):
            with self.subTest(params=params):
                payload = self._payload(serving, **params)

                self.assertEqual(
                    [series['key'] for series in payload['series']],
                    ['selected_run', f'extra_run_{overlay.id}', 'h00905_csi'],
                )
                extra = payload['series'][1]
                self.assertEqual(extra['run_id'], overlay.id)
                self.assertEqual(extra['points'][0]['value'], 480000.0)
                self.assertAlmostEqual(extra['total_return'], 0.10)

    def test_an_unparseable_overlay_id_is_dropped_rather_than_failing_the_chart(self):
        serving = self._backtest('serving', [480000, 490000])
        overlay = self._backtest('overlay', [400000, 440000])

        payload = self._payload(serving, extra_ids=f'nonsense,0,-3,{overlay.id}')

        self.assertEqual(
            [series['key'] for series in payload['series']],
            ['selected_run', f'extra_run_{overlay.id}'],
        )

    def test_an_overlay_request_is_never_served_the_cached_payload_of_a_bare_one(self):
        """The compare ids belong in the cache key or the first overlay wins and the bare curve disappears."""
        serving = self._backtest('serving', [480000, 490000])
        overlay = self._backtest('overlay', [480000, 480000])

        with_overlay = self._payload(serving, extra_ids=str(overlay.id))
        bare = self._payload(serving)

        self.assertEqual(len(with_overlay['series']), 2)
        self.assertEqual([series['key'] for series in bare['series']], ['selected_run'])

    def test_a_finished_run_is_cached_and_a_moving_one_is_rebuilt_every_time(self):
        """Cache what cannot change; never cache what can.

        Mirrors ``apps.backtest.views``: a COMPLETED curve is thousands of points and is finished, so it is
        worth one rebuild per TTL, while a RUNNING row is being appended to and freezing it would pin the
        progress bar. ``updated_at`` rides in the key, so a write to a finished row -- a relabel, a rerun
        that lands -- invalidates instead of serving the old curve for the whole TTL.
        """
        finished = self._backtest('finished', [480000, 490000])
        self._payload(finished)
        self._payload(finished)

        with self._spy() as spy:
            self._payload(finished)
            self.assertEqual(spy.call_count, 0)

            ETFRotationBacktest.objects.filter(pk=finished.id).update(
                equity_curve=[
                    {'date': day.isoformat(), 'equity': value}
                    for day, value in zip(self.SESSIONS[:2], [480000, 600000])
                ],
                updated_at=timezone.now(),
            )
            self.assertEqual(self._payload(finished)['series'][0]['points'][-1]['value'], 600000.0)

        moving = self._backtest('moving', [480000, 490000], status=ETFRotationBacktest.Status.RUNNING)
        with self._spy() as spy:
            self._payload(moving)
            self._payload(moving)
            self.assertEqual(spy.call_count, 2)


class _EtfBacktestLifecycleMixin:
    """Row and config builders shared by the four Phase B classes.

    ``_config`` spells out every knob the way a shell caller would, reading nothing off a
    row. That is deliberate: comparing it against what ``create`` persisted proves the API
    and the engine agree on one run's identity, which is the property the fingerprint dedupe
    rests on -- a grid row adopted by ``_adopt_backtest_row`` and an API row for the same
    computation have to be recognised as the same computation.
    """

    START = date(2024, 1, 2)
    END = date(2024, 6, 28)

    def _config(self, **overrides):
        values = {
            'start_date': self.START,
            'end_date': self.END,
            'model_type': 'RULE',
            'rebalance_frequency': 'MONTHLY',
            'max_positions': 5,
            'momentum_lookback': 60,
            'weighting': 'score',
        }
        values.update(overrides)
        return backtest_engine.BacktestConfig(**values)

    def _row(self, name, **kwargs):
        """A backtest row carrying the configuration ``_payload`` below would POST."""
        state = {
            'name': name,
            'status': ETFRotationBacktest.Status.PENDING,
            'start_date': self.START,
            'end_date': self.END,
            'rebalance_frequency': ETFRotationBacktest.RebalanceFrequency.MONTHLY,
            'model_type': ETFRotationBacktest.ModelType.RULE,
            'weighting': ETFRotationBacktest.Weighting.SCORE,
            'parameters': self._config().to_parameters(),
        }
        state.update(kwargs)
        return ETFRotationBacktest.objects.create(**state)

    def _fresh(self, run):
        return ETFRotationBacktest.objects.get(pk=run.pk)


class EtfBacktestCreateGuardrailTests(_EtfBacktestLifecycleMixin, TestCase):
    """``create`` is the only public write surface in this app, so its bounds are the guards.

    Phase B of the follow-on plan. Authentication is gone repo-wide and the API runs on an
    internal network, so what is bounded here is a retry loop of legitimate requests rather
    than an adversary: RULE-only, dedupe against in-flight rows on the config fingerprint, and
    a global concurrency cap. The *ordering* of the last two is pinned too because it is what a
    client can observe -- a duplicate answers 200 and consumes no slot, so retrying a POST you
    already have running gets your row back instead of a 429 you cannot act on.
    """

    URL = '/api/v1/etf/backtests/'

    def setUp(self):
        # The stock lifecycle tests set this on the instance for the same reason: a JSON
        # POST is what a browser client sends, and the Django test client's form encoding
        # would exercise a different parser.
        self.client = APIClient()

    def _payload(self, **overrides):
        payload = {
            'name': 'api-rule-monthly',
            'start_date': self.START.isoformat(),
            'end_date': self.END.isoformat(),
            'rebalance_frequency': 'MONTHLY',
            'max_positions': 5,
            'momentum_lookback': 60,
            'weighting': 'score',
        }
        payload.update(overrides)
        # ``None`` removes a key rather than sending a null, so a test can ask for the
        # serializer's own default instead of the model's.
        return {key: value for key, value in payload.items() if value is not None}

    def _post(self, **overrides):
        return self.client.post(self.URL, self._payload(**overrides), format='json')

    def _post_and_commit(self, **overrides):
        """POST with the commit callbacks fired and the broker stubbed out.

        ``queue_etf_rotation_run`` only reads ``result.id``, so a stand-in object is enough;
        reaching a real broker would make the suite depend on Redis being up.
        """
        calls = []

        def fake_delay(run_id):
            calls.append(run_id)
            return SimpleNamespace(id='task-1')

        with patch('apps.etf_rotation.tasks.run_etf_rotation_backtest.delay', side_effect=fake_delay):
            with self.captureOnCommitCallbacks(execute=True):
                response = self._post(**overrides)
        return response, calls

    def test_create_answers_202_and_creates_exactly_one_row(self):
        response, calls = self._post_and_commit()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(calls, [response.data['id']])
        self.assertEqual(ETFRotationBacktest.objects.count(), 1)

        run = self._fresh(ETFRotationBacktest.objects.get())
        self.assertEqual(run.name, 'api-rule-monthly')
        self.assertEqual(run.status, ETFRotationBacktest.Status.PENDING)
        self.assertEqual(run.model_type, ETFRotationBacktest.ModelType.RULE)
        self.assertEqual(run.weighting, ETFRotationBacktest.Weighting.SCORE)
        self.assertEqual(run.current_task_id, 'task-1')

    def test_nothing_is_queued_until_the_transaction_commits(self):
        """A row the transaction later rolls back must not have a task pointing at it.

        ``captureOnCommitCallbacks`` without ``execute`` collects the callbacks instead of
        running them, which is the only way to see the deferral from inside a ``TestCase``.
        """
        with patch('apps.etf_rotation.tasks.run_etf_rotation_backtest.delay') as mock_delay:
            with self.captureOnCommitCallbacks() as hooks:
                response = self._post()
            self.assertEqual(response.status_code, 202)
            self.assertEqual(len(hooks), 1)
            mock_delay.assert_not_called()

    def test_the_persisted_parameters_are_the_fingerprint_the_dedupe_reads(self):
        """The blob ``create`` writes must digest to a plain ``BacktestConfig``'s fingerprint.

        Two things could break this silently. ``weighting`` is a fingerprint knob, so the blob
        has to carry the value the column actually holds -- a create path that wrote the
        settings default instead of the saved row would digest differently from the engine's
        own view of the same run. And every knob that is *not* a column has to have the same
        default on both sides, because that is what lets an API row dedupe against a ``--grid``
        row the engine adopted.
        """
        response, _ = self._post_and_commit()

        run = self._fresh(ETFRotationBacktest.objects.get())
        self.assertEqual(
            etf_views._parameters_digest(run.parameters),
            self._config().fingerprint(),
        )
        self.assertEqual(response.data['parameters']['weighting'], 'score')
        self.assertEqual(run.parameters['fee_model']['stamp_duty'], 0.0)

    def test_an_empty_parameters_blob_can_never_match(self):
        """``_parameters_digest`` fails safe: no blob means no identity, so no false dedupe."""
        self.assertEqual(etf_views._parameters_digest({}), '')
        self.assertEqual(etf_views._parameters_digest(None), '')

        legacy = self._row('legacy', parameters={}, status=ETFRotationBacktest.Status.RUNNING)
        response, calls = self._post_and_commit()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(calls), 1)
        self.assertEqual(ETFRotationBacktest.objects.count(), 2)
        self.assertTrue(ETFRotationBacktest.objects.filter(pk=legacy.pk).exists())

    def test_only_rule_can_be_launched_from_the_api(self):
        for model_type in ('LSTM', 'LIGHTGBM', 'ENSEMBLE'):
            with self.subTest(model_type=model_type):
                response = self._post(model_type=model_type)
                self.assertEqual(response.status_code, 400)
                self.assertIn('model_type', response.data)
        self.assertEqual(ETFRotationBacktest.objects.count(), 0)

    def test_an_impossible_window_is_rejected_before_any_row_exists(self):
        cases = {
            'reversed': {'end_date': (self.START - timedelta(days=1)).isoformat()},
            'future': {'end_date': (timezone.localdate() + timedelta(days=30)).isoformat()},
            'missing dates': {'start_date': None, 'end_date': None},
            'empty portfolio': {'max_positions': 0},
            'no lookback': {'momentum_lookback': 0},
            'unfunded': {'initial_capital': 0},
        }
        for label, overrides in cases.items():
            with self.subTest(case=label):
                response = self._post(**overrides)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(ETFRotationBacktest.objects.count(), 0)

    def test_the_cost_model_cannot_be_bought_down_by_a_client(self):
        """The fee knobs are read-only, so a POST cannot manufacture a flattering ledger.

        Silently dropped rather than rejected: these are not fields a launch is expected to
        send, and a 400 over an ignored key would break ordinary clients for no protection.
        """
        response, _ = self._post_and_commit(
            commission_rate=0.0, min_commission=0, slippage=0.0,
            liquidity_floor_multiple=0, min_trade_notional_fraction=0.0,
            benchmark_code='000300.SH', walk_forward_steps=6,
        )

        self.assertEqual(response.status_code, 202)
        run = self._fresh(ETFRotationBacktest.objects.get())
        self.assertEqual(run.commission_rate, 0.0003)
        self.assertEqual(float(run.min_commission), 5.0)
        self.assertEqual(run.slippage, 0.001)
        self.assertEqual(run.liquidity_floor_multiple, 10.0)
        self.assertEqual(run.min_trade_notional_fraction, 0.005)
        self.assertEqual(run.benchmark_code, 'H00905.CSI')
        self.assertEqual(run.walk_forward_steps, 0)

    def test_a_second_identical_launch_is_deduped_and_queues_nothing(self):
        first, first_calls = self._post_and_commit()
        self.assertEqual(first.status_code, 202)

        second, second_calls = self._post_and_commit()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data['id'], first.data['id'])
        self.assertEqual(second_calls, [])
        self.assertEqual(len(first_calls), 1)
        self.assertEqual(ETFRotationBacktest.objects.count(), 1)

    def test_dedupe_ignores_the_name_and_keys_on_the_computation(self):
        """A differently named row for the same window and knobs is the same run."""
        self._row('grid-rule-60', status=ETFRotationBacktest.Status.RUNNING, current_task_id='task-1')

        response, calls = self._post_and_commit(name='something-else-entirely')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [])
        self.assertEqual(ETFRotationBacktest.objects.count(), 1)

    def test_a_run_hanging_its_worker_offers_no_protection(self):
        """Only PENDING/RUNNING are in flight; a COMPLETED row may be re-launched on purpose."""
        for terminal in (
            ETFRotationBacktest.Status.COMPLETED,
            ETFRotationBacktest.Status.FAILED,
            ETFRotationBacktest.Status.PAUSED,
        ):
            with self.subTest(status=terminal):
                ETFRotationBacktest.objects.all().delete()
                self._row('terminal', status=terminal, parameters=self._config().to_parameters())

                response, calls = self._post_and_commit()
                self.assertEqual(response.status_code, 202)
                self.assertEqual(len(calls), 1)

    def test_a_differing_weighting_is_a_different_computation(self):
        """``weighting`` is in the fingerprint, so the grid's other axis is never collapsed."""
        self._post_and_commit()

        response, calls = self._post_and_commit(weighting='equal')

        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(calls), 1)
        self.assertEqual(ETFRotationBacktest.objects.count(), 2)

    @override_settings(ETF_MAX_CONCURRENT_BACKTESTS=2)
    def test_the_cap_answers_429_and_names_the_setting(self):
        self._row('one', status=ETFRotationBacktest.Status.RUNNING, current_task_id='task-1')
        self._row('two', status=ETFRotationBacktest.Status.PENDING, momentum_lookback=90,
                  parameters=self._config(momentum_lookback=90).to_parameters())

        response = self._post(momentum_lookback=120)

        self.assertEqual(response.status_code, 429)
        self.assertIn('ETF_MAX_CONCURRENT_BACKTESTS', str(response.data['detail']))
        self.assertEqual(ETFRotationBacktest.objects.count(), 2)

    @override_settings(ETF_MAX_CONCURRENT_BACKTESTS=1)
    def test_a_duplicate_consumes_no_slot(self):
        """The dedupe runs before the cap, so a retry of a live row is never a 429."""
        self._row('in flight', status=ETFRotationBacktest.Status.RUNNING, current_task_id='task-1')

        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ETFRotationBacktest.objects.count(), 1)

    @override_settings(ETF_MAX_CONCURRENT_BACKTESTS=0)
    def test_the_cap_fails_closed(self):
        response = self._post()
        self.assertEqual(response.status_code, 429)

    def test_a_paused_run_holds_no_slot_and_shields_no_other(self):
        """PAUSED keeps a cursor in ``report``, not a worker, so it is in neither scan."""
        self._row(
            'paused',
            status=ETFRotationBacktest.Status.PAUSED,
            parameters=self._config().to_parameters(),
            report={'runtime_state': {'cursor': 120}},
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self._post()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(ETFRotationBacktest.objects.count(), 2)

    def test_progress_is_detail_only(self):
        """The list payload cannot carry the cursor, which is why the workbench polls retrieve."""
        created, _ = self._post_and_commit()
        run = self._fresh(ETFRotationBacktest.objects.get())
        ETFRotationBacktest.objects.filter(pk=run.pk).update(
            report={'runtime_state': {'cursor': 120}},
            equity_curve=[{'date': self.START.isoformat(), 'equity': 480000}],
            trade_log=[{'id': 1}],
            updated_at=timezone.now(),
        )

        listing = self.client.get(self.URL).data['results'][0]
        self.assertNotIn('report', listing)
        self.assertNotIn('equity_curve', listing)
        self.assertNotIn('trade_log', listing)

        detail = self.client.get(f'{self.URL}{run.id}/').data
        self.assertEqual(detail['report']['runtime_state']['cursor'], 120)
        self.assertEqual(len(detail['equity_curve']), 1)
        self.assertNotIn('equity_curve', created.data)

    def test_a_run_is_immutable_once_it_exists(self):
        """PUT/PATCH are not routes: the fingerprint in the report is what a cursor resumes under."""
        run = self._fresh(self._row('immutable'))

        for verb in ('put', 'patch'):
            with self.subTest(verb=verb):
                response = getattr(self.client, verb)(
                    f'{self.URL}{run.id}/', self._payload(momentum_lookback=120), format='json'
                )
                self.assertEqual(response.status_code, 405)
        self.assertEqual(self._fresh(run).momentum_lookback, 60)

    def test_weighting_is_filterable_in_sql(self):
        """The point of promoting the column: attribute and filter a grid axis without JSON."""
        self._row('scored', weighting=ETFRotationBacktest.Weighting.SCORE)
        self._row('equaled', weighting=ETFRotationBacktest.Weighting.EQUAL)

        for query in ('EQUAL', 'equal'):
            with self.subTest(weighting=query):
                results = self.client.get(self.URL, {'weighting': query}).data['results']
                self.assertEqual([row['name'] for row in results], ['equaled'])


class EtfBacktestLifecycleTransitionTests(_EtfBacktestLifecycleMixin, TestCase):
    """pause/resume/rerun/restart/destroy, pinned to the semantics the plan says to copy.

    Whether a RUNNING row is paused now or at the next chunk boundary is decided entirely by
    ``has_stale_task_owner``, which reads ``AsyncResult`` state from the broker. CI has none, so
    the shared helper is patched at the view seam instead -- the short-circuit for non-RUNNING
    rows means the un-patched paths never reach Celery anyway.
    """

    def setUp(self):
        self.client = APIClient()

    def _owner(self, *, stale):
        return patch.object(
            etf_views,
            'get_backtest_run_task_owner_state',
            return_value={
                'task_state': 'FAILURE' if stale else 'STARTED',
                'task_age_seconds': None,
                'has_stale_task_owner': stale,
            },
        )

    def _running(self, name='running', **kwargs):
        state = {
            'status': ETFRotationBacktest.Status.RUNNING,
            'current_task_id': 'task-1',
            'started_at': timezone.now(),
            'report': {'runtime_state': {'cursor': 120, 'fingerprint': 'deadbeef'}},
            'total_return': 0.11,
        }
        state.update(kwargs)
        return self._row(name, **state)

    def _post(self, run, verb):
        return self.client.post(f'/api/v1/etf/backtests/{run.id}/{verb}/')

    def _queued_post(self, run, verb, *, task_id='task-2'):
        """POST a lifecycle verb and fire the commit callbacks with the broker stubbed.

        The patch wraps the capture, not the other way round: ``captureOnCommitCallbacks``
        runs the callbacks when it *exits*, so a patch nested inside it would already be
        undone by then and the queue helper would publish to a real broker.
        """
        calls = []

        def fake_delay(run_id):
            calls.append(run_id)
            return SimpleNamespace(id=task_id)

        with patch('apps.etf_rotation.tasks.run_etf_rotation_backtest.delay', side_effect=fake_delay):
            with self.captureOnCommitCallbacks(execute=True):
                response = self._post(run, verb)
        return response, calls

    def test_pause_while_pending_parks_the_row_at_once(self):
        run = self._row('pending')

        with patch('apps.etf_rotation.tasks.run_etf_rotation_backtest.delay') as mock_delay:
            response = self._post(run, 'pause')

        self.assertEqual(response.status_code, 202)
        self.assertIn('paused', response.data['message'].lower())
        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.PAUSED)
        self.assertEqual(fresh.pending_control_action, ETFRotationBacktest.ControlAction.NONE)
        mock_delay.assert_not_called()

    def test_pause_while_running_is_a_request_not_an_interruption(self):
        """The engine owns the chunk, so a live run is only told; the boundary check does the rest."""
        run = self._running()

        with self._owner(stale=False):
            response = self._post(run, 'pause')

        self.assertEqual(response.status_code, 202)
        self.assertIn('current chunk', response.data['message'])
        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.RUNNING)
        self.assertEqual(fresh.pending_control_action, ETFRotationBacktest.ControlAction.PAUSE)
        # The resumable state is what pause is for; nothing may touch it here.
        self.assertEqual(fresh.report['runtime_state']['cursor'], 120)

    def test_pause_of_a_run_whose_worker_is_gone_parks_it_and_forgets_the_owner(self):
        run = self._running(error_message='worker died', completed_at=timezone.now())

        with self._owner(stale=True):
            response = self._post(run, 'pause')

        self.assertEqual(response.status_code, 202)
        self.assertIn('dead task', response.data['message'])
        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.PAUSED)
        self.assertEqual(fresh.current_task_id, '')
        self.assertEqual(fresh.error_message, '')
        self.assertIsNone(fresh.completed_at)

    def test_only_pending_or_running_can_be_paused(self):
        for status_value in (
            ETFRotationBacktest.Status.PAUSED,
            ETFRotationBacktest.Status.COMPLETED,
            ETFRotationBacktest.Status.FAILED,
        ):
            with self.subTest(status=status_value):
                run = self._row('unpausable', status=status_value)

                response = self._post(run, 'pause')

                self.assertEqual(response.status_code, 400)
                self.assertIn('Only pending or running', response.data['message'])
                self.assertEqual(self._fresh(run).status, status_value)

    def test_resume_requeues_with_the_cursor_intact(self):
        """Resume must not look like a restart: what it hands the worker is a resumable row."""
        run = self._row(
            'parked',
            status=ETFRotationBacktest.Status.PAUSED,
            current_task_id='',
            error_message='',
            report={'runtime_state': {'cursor': 120}},
            total_return=0.07,
        )

        response, calls = self._queued_post(run, 'resume')

        self.assertEqual(response.status_code, 202)
        self.assertEqual(calls, [run.id])
        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.PENDING)
        self.assertEqual(fresh.current_task_id, 'task-2')
        self.assertEqual(fresh.report['runtime_state']['cursor'], 120)
        self.assertEqual(fresh.total_return, 0.07)

    def test_only_a_paused_run_can_be_resumed(self):
        for status_value in (
            ETFRotationBacktest.Status.PENDING,
            ETFRotationBacktest.Status.RUNNING,
            ETFRotationBacktest.Status.COMPLETED,
            ETFRotationBacktest.Status.FAILED,
        ):
            with self.subTest(status=status_value):
                run = self._row('not paused', status=status_value)

                with patch('apps.etf_rotation.tasks.run_etf_rotation_backtest.delay') as mock_delay:
                    response = self._post(run, 'resume')

                self.assertEqual(response.status_code, 400)
                self.assertIn('Only paused', response.data['message'])
                self.assertEqual(self._fresh(run).status, status_value)
                mock_delay.assert_not_called()

    def test_restart_of_a_finished_row_wipes_the_ledger_and_reruns_from_zero(self):
        run = self._row(
            'done',
            status=ETFRotationBacktest.Status.COMPLETED,
            total_return=0.18,
            equity_curve=[{'date': self.START.isoformat(), 'equity': 480000}],
            trade_log=[{'id': 1}],
            report={'runtime_state': {'cursor': 240}},
        )

        response, calls = self._queued_post(run, 'restart', task_id='task-3')

        self.assertEqual(response.status_code, 202)
        self.assertEqual(calls, [run.id])
        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.PENDING)
        self.assertEqual(fresh.report, {})
        self.assertEqual(fresh.equity_curve, [])
        self.assertEqual(fresh.trade_log, [])
        self.assertIsNone(fresh.total_return)
        self.assertIsNone(fresh.started_at)
        # The configuration survives; only the results are discarded.
        self.assertEqual(fresh.parameters['momentum_lookback'], 60)

    def test_restart_of_a_live_row_is_an_intent_that_protects_the_in_flight_chunk(self):
        """Resetting a RUNNING row would be overwritten by the next progress save, so it is not done."""
        run = self._running()

        with self._owner(stale=False):
            response = self._post(run, 'rerun')

        self.assertEqual(response.status_code, 202)
        self.assertIn('current chunk', response.data['message'])
        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.RUNNING)
        self.assertEqual(fresh.pending_control_action, ETFRotationBacktest.ControlAction.RESTART)
        self.assertEqual(fresh.report['runtime_state']['cursor'], 120)
        self.assertEqual(fresh.total_return, 0.11)

    def test_restart_of_an_orphaned_row_is_applied_at_once(self):
        run = self._running()

        with self._owner(stale=True):
            response, calls = self._queued_post(run, 'restart', task_id='task-4')

        self.assertEqual(response.status_code, 202)
        self.assertIn('dead task', response.data['message'])
        self.assertEqual(calls, [run.id])
        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.PENDING)
        self.assertEqual(fresh.report, {})

    def test_rerun_and_restart_are_the_same_transition(self):
        live = self._running('via-rerun')
        with self._owner(stale=False):
            rerun = self._post(live, 'rerun')
            restart = self._post(self._running('via-restart'), 'restart')

        self.assertEqual(rerun.status_code, restart.status_code)
        self.assertEqual(rerun.data['message'], restart.data['message'])

    def test_destroy_deletes_anything_that_is_not_executing(self):
        for status_value in (
            ETFRotationBacktest.Status.PENDING,
            ETFRotationBacktest.Status.PAUSED,
            ETFRotationBacktest.Status.COMPLETED,
            ETFRotationBacktest.Status.FAILED,
        ):
            with self.subTest(status=status_value):
                run = self._row('deletable', status=status_value)

                response = self.client.delete(f'/api/v1/etf/backtests/{run.id}/')

                self.assertEqual(response.status_code, 204)
                self.assertFalse(ETFRotationBacktest.objects.filter(pk=run.pk).exists())

    def test_destroy_of_a_live_row_is_a_boundary_request(self):
        """Deleting outright would let ``_persist_progress`` re-INSERT a row nobody owns."""
        run = self._running()

        with self._owner(stale=False):
            response = self.client.delete(f'/api/v1/etf/backtests/{run.id}/')

        self.assertEqual(response.status_code, 202)
        self.assertIn('current chunk', response.data['message'])
        fresh = self._fresh(run)
        self.assertEqual(fresh.pending_control_action, ETFRotationBacktest.ControlAction.DELETE)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.RUNNING)

    def test_destroy_of_an_orphaned_row_is_final(self):
        run = self._running()

        with self._owner(stale=True):
            response = self.client.delete(f'/api/v1/etf/backtests/{run.id}/')

        self.assertEqual(response.status_code, 204)
        self.assertFalse(ETFRotationBacktest.objects.filter(pk=run.pk).exists())

    def test_the_detail_payload_reports_whether_the_owner_is_alive(self):
        """``status`` alone cannot tell an advancing run from one whose worker died.

        The seam patched here is the serializer's, not the view's: the fields resolve the helper
        through their own module, so ``patch.object(etf_views, ...)`` would drive the view's
        decisions while the response still reported the un-patched broker state.

        The per-object memo is asserted too: a list page that resolved this once per field per
        row would make two broker round-trips per run.
        """
        run = self._running()

        with patch('apps.etf_rotation.serializers.get_backtest_run_task_owner_state',
                   return_value={'task_state': 'STARTED', 'task_age_seconds': 4,
                                 'has_stale_task_owner': False}) as owner:
            detail = self.client.get(f'/api/v1/etf/backtests/{run.id}/').data

        self.assertEqual(detail['task_state'], 'STARTED')
        self.assertFalse(detail['has_stale_task_owner'])
        self.assertEqual(detail['pending_control_action'], 'NONE')
        self.assertEqual(owner.call_count, 1)

    def test_the_transition_endpoints_refuse_a_run_that_is_not_there(self):
        response = self.client.post('/api/v1/etf/backtests/999999/pause/')
        self.assertEqual(response.status_code, 404)


class EtfBacktestChunkBoundaryControlTests(_EtfBacktestLifecycleMixin, TestCase):
    """``_chunk_boundary_control`` is the only place a live ETF run can be interrupted.

    The engine loops every chunk inside one task, so unlike the stock side there is no
    re-queue point to inspect -- the check has to live here, after ``_persist_progress`` made
    ``cursor`` durable. RESTART is deliberately handed back rather than applied: wiping the row
    and re-queueing is task-layer work.
    """

    def _at_boundary(self, run, cursor=120, total=240):
        return backtest_engine._chunk_boundary_control(run, cursor, total)

    def test_no_request_leaves_the_loop_and_the_row_alone(self):
        run = self._fresh(self._running_row())

        self.assertIsNone(self._at_boundary(run))
        self.assertEqual(self._fresh(run).status, ETFRotationBacktest.Status.RUNNING)

    def test_pause_parks_the_row_without_touching_the_resumable_state(self):
        run = self._fresh(self._running_row(pending_control_action=ETFRotationBacktest.ControlAction.PAUSE))

        control = self._at_boundary(run)

        self.assertEqual(control, 'PAUSED')
        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.PAUSED)
        self.assertEqual(fresh.report['runtime_state']['cursor'], 120)
        self.assertIn('120/240', fresh.report['status_note'])
        self.assertEqual(fresh.pending_control_action, ETFRotationBacktest.ControlAction.NONE)
        self.assertEqual(fresh.current_task_id, '')
        self.assertIsNone(fresh.completed_at)

    def test_restart_is_handed_back_unapplied(self):
        run = self._fresh(self._running_row(pending_control_action=ETFRotationBacktest.ControlAction.RESTART))

        self.assertEqual(self._at_boundary(run), 'RESTART')
        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.RUNNING)
        self.assertEqual(fresh.pending_control_action, ETFRotationBacktest.ControlAction.RESTART)
        self.assertEqual(fresh.report['runtime_state']['cursor'], 120)

    def test_delete_removes_the_row_from_inside_the_loop(self):
        run = self._fresh(self._running_row(pending_control_action=ETFRotationBacktest.ControlAction.DELETE))

        self.assertEqual(self._at_boundary(run), 'DELETED')
        self.assertFalse(ETFRotationBacktest.objects.filter(pk=run.pk).exists())

    def test_a_row_deleted_out_from_under_the_loop_stops_it(self):
        run = self._fresh(self._running_row())
        ETFRotationBacktest.objects.filter(pk=run.pk).delete()

        self.assertEqual(self._at_boundary(run), 'DELETED')

    def test_a_halted_run_returns_no_metrics(self):
        """A halt has no finalized numbers; a caller that indexes them unchecked is the bug."""
        result = backtest_engine._halted_result(
            self._fresh(self._running_row()), 'PAUSED', 120, 240, self._config()
        )

        self.assertEqual(result['control'], 'PAUSED')
        self.assertEqual((result['cursor'], result['total_sessions']), (120, 240))
        for absent in ('metrics', 'equity_curve', 'report'):
            self.assertNotIn(absent, result)
        self.assertEqual(result['parameters']['weighting'], 'score')

    def test_a_halted_deleted_run_has_no_row_to_point_at(self):
        result = backtest_engine._halted_result(None, 'DELETED', 0, 240, self._config())

        self.assertIsNone(result['run_id'])
        self.assertEqual(result['control'], 'DELETED')

    def _running_row(self, **kwargs):
        state = {
            'status': ETFRotationBacktest.Status.RUNNING,
            'current_task_id': 'task-1',
            'report': {'runtime_state': {'cursor': 120, 'fingerprint': self._config().fingerprint()}},
        }
        state.update(kwargs)
        return self._row('boundary', **state)


class EtfBacktestTaskLifecycleTests(_EtfBacktestLifecycleMixin, TestCase):
    """The queue helpers and the task body that consumes the lifecycle intents.

    The bound task is called directly, which leaves ``request.id`` empty -- so the
    ownership-skip branch is unreachable this way and the entry guards below are what is
    actually tested. ``apps.etf_rotation.backtest_engine.run_rotation_backtest`` is patched
    because the engine is separately covered and this layer only branches on its return.
    """

    def _call_task(self, run):
        with patch('apps.etf_rotation.backtest_engine.run_rotation_backtest',
                   return_value={'metrics': {'total_return': 0.1}}) as engine:
            result = etf_tasks.run_etf_rotation_backtest(run.pk)
        return result, engine

    def test_queueing_records_the_task_id_and_clears_a_stale_request(self):
        run = self._row('queued', pending_control_action=ETFRotationBacktest.ControlAction.PAUSE,
                        current_task_id='superseded')

        with patch('apps.etf_rotation.tasks.run_etf_rotation_backtest.delay',
                   return_value=SimpleNamespace(id='fresh-task')) as mock_delay:
            etf_tasks.queue_etf_rotation_run(run)

        mock_delay.assert_called_once_with(run.pk)
        fresh = self._fresh(run)
        self.assertEqual(fresh.current_task_id, 'fresh-task')
        # A freshly queued row must not inherit a request meant for the task it replaces.
        self.assertEqual(fresh.pending_control_action, ETFRotationBacktest.ControlAction.NONE)

    def test_reset_returns_a_row_to_never_executed_but_keeps_its_configuration(self):
        run = self._row(
            'reset',
            status=ETFRotationBacktest.Status.COMPLETED,
            total_return=0.18,
            equity_curve=[{'date': self.START.isoformat(), 'equity': 560000}],
            report={'runtime_state': {'cursor': 240}},
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )

        etf_tasks.reset_etf_rotation_run_for_restart(run)

        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.PENDING)
        self.assertIsNone(fresh.total_return)
        self.assertEqual(fresh.equity_curve, [])
        self.assertEqual(fresh.report, {})
        self.assertIsNone(fresh.started_at)
        self.assertEqual(fresh.current_task_id, '')
        self.assertEqual(fresh.start_date, self.START)
        self.assertEqual(fresh.parameters['momentum_lookback'], 60)

    def test_a_missing_row_is_reported_rather_than_raised(self):
        result, engine = self._call_task(SimpleNamespace(pk=987654))

        self.assertIn('987654', result)
        engine.assert_not_called()

    def test_a_delete_request_is_consumed_before_the_engine_runs(self):
        run = self._row('doomed', status=ETFRotationBacktest.Status.RUNNING,
                        pending_control_action=ETFRotationBacktest.ControlAction.DELETE)

        result, engine = self._call_task(run)

        self.assertIn('deleted', result)
        engine.assert_not_called()
        self.assertFalse(ETFRotationBacktest.objects.filter(pk=run.pk).exists())

    def test_a_queued_task_does_not_start_work_a_paused_row_asked_to_stop(self):
        """The re-entry guard: pause is honoured even if the request beat the worker to the row."""
        run = self._row('parked', status=ETFRotationBacktest.Status.PAUSED, current_task_id='task-1',
                        report={'runtime_state': {'cursor': 120}})

        result, engine = self._call_task(run)

        self.assertIn('remains paused', result)
        engine.assert_not_called()
        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.PAUSED)
        self.assertEqual(fresh.current_task_id, '')
        self.assertEqual(fresh.report['runtime_state']['cursor'], 120)

    def test_a_restart_request_is_executed_by_the_wrapper_not_the_engine(self):
        run = self._row('restart-intent', status=ETFRotationBacktest.Status.RUNNING,
                        pending_control_action=ETFRotationBacktest.ControlAction.RESTART,
                        report={'runtime_state': {'cursor': 120}})

        with patch.object(etf_tasks, 'queue_etf_rotation_run') as queue:
            result, engine = self._call_task(run)

        self.assertIn('restart queued', result)
        engine.assert_not_called()
        queue.assert_called_once()
        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.PENDING)
        self.assertEqual(fresh.report, {})

    def test_the_row_columns_are_what_the_engine_is_told_to_run(self):
        """A run launched through the API cannot inherit a different cost model than persisted.

        ``weighting`` reaching the engine from the column rather than from the default is the
        observable half of promoting it to a column. The row is deliberately set to ``'equal'``
        -- the value neither the dataclass, the column nor ``settings`` now defaults to -- so a
        regression that drops the column and falls back to any of those defaults is visible here
        instead of agreeing by accident.

        The two selection columns join it for the same reason. A row created with
        ``dedup_track_index=false`` ran the engine's ``True`` anyway, so the API appeared to
        accept a configuration it never executed -- and the stored ``parameters`` digest, built
        from the row, described the run the caller asked for rather than the one that happened.
        """
        run = self._row(
            'forwarded', initial_capital=Decimal('250000.00'), min_commission=Decimal('7.00'),
            weighting=ETFRotationBacktest.Weighting.EQUAL,
            dedup_track_index=False, max_per_category=5,
        )

        result, engine = self._call_task(run)

        self.assertEqual(result, {'metrics': {'total_return': 0.1}})
        kwargs = engine.call_args.kwargs
        self.assertEqual(kwargs['config_overrides'], {
            'walk_forward_steps': 0, 'weighting': 'equal',
            'dedup_track_index': False, 'max_per_category': 5,
        })
        self.assertEqual(kwargs['initial_capital'], 250000.0)
        self.assertEqual(kwargs['min_commission'], 7.0)
        self.assertIsInstance(kwargs['initial_capital'], float)
        self.assertIsInstance(kwargs['min_commission'], float)
        self.assertEqual(kwargs['momentum_lookback'], 60)
        self.assertEqual(kwargs['benchmark_code'], 'H00905.CSI')
        self.assertEqual(kwargs['persist_to'].pk, run.pk)

    def test_a_halt_from_the_boundary_is_reported_with_its_cursor(self):
        cases = {
            'PAUSED': ('paused', {'control': 'PAUSED', 'cursor': 120, 'total_sessions': 240}),
            'DELETED': ('chunk boundary', {'control': 'DELETED', 'run_id': 7}),
        }
        for control, (needle, halt) in cases.items():
            with self.subTest(control=control):
                run = self._fresh(self._row(f'halt-{control}',
                                            status=ETFRotationBacktest.Status.RUNNING))
                with patch('apps.etf_rotation.backtest_engine.run_rotation_backtest', return_value=halt):
                    result = etf_tasks.run_etf_rotation_backtest(run.pk)

                self.assertIn(needle, result)

    def test_a_restart_handed_back_by_the_engine_is_applied_by_the_wrapper(self):
        run = self._row('engine-restart', status=ETFRotationBacktest.Status.RUNNING,
                        report={'runtime_state': {'cursor': 120}})

        with patch.object(etf_tasks, 'queue_etf_rotation_run'):
            with patch('apps.etf_rotation.backtest_engine.run_rotation_backtest',
                       return_value={'control': 'RESTART', 'run_id': run.pk, 'cursor': 120,
                                     'total_sessions': 240}):
                result = etf_tasks.run_etf_rotation_backtest(run.pk)

        self.assertIn('restart queued', result)
        fresh = self._fresh(run)
        self.assertEqual(fresh.status, ETFRotationBacktest.Status.PENDING)
        self.assertEqual(fresh.report, {})


# ---------------------------------------------------------------------------
# Phase C: the LSTM sweep command
# ---------------------------------------------------------------------------


class _SweepStubTrainer:
    """A torch-free stand-in for ``apps.etf_rotation.train_lstm``.

    ``_run_sweep`` reads three module attributes (``MODEL_TYPE_LABEL``, ``LOSS_BCE``,
    ``LOSS_PAIRWISE``) and makes exactly one call, ``train_walk_forward(**knobs)``, so that is the
    whole seam. The stub reproduces the one gate the contamination discipline rests on --
    ``pooled_holdout`` appears only when ``sweep`` was not passed -- and registers real
    ``ModelVersion`` rows so the provenance stamp can be read back out of the database. No GPU
    work happens here, which is what plan risk 5 asks of CI.
    """

    MODEL_TYPE_LABEL = 'ETF_LSTM'
    LOSS_BCE = 'bce_top_bucket'
    LOSS_PAIRWISE = 'pairwise_rank'

    #: Keeps versions unique across the several stub instances one test may run.
    _sequence = 0

    def __init__(self, *, mean_ic=0.02, mean_ic_by_trial=None, raise_for=None,
                 refuse_for=None, activate_override=None, losses=None):
        self.calls: list[dict] = []
        self.mean_ic = mean_ic
        self.mean_ic_by_trial = dict(mean_ic_by_trial or {})
        self.raise_for = dict(raise_for or {})
        self.refuse_for = dict(refuse_for or {})
        self.activate_override = activate_override
        if losses is not None:
            self.LOSS_BCE, self.LOSS_PAIRWISE = losses

    def mean_for(self, trial: str) -> float:
        return self.mean_ic_by_trial.get(trial, self.mean_ic)

    def train_walk_forward(self, **kwargs):
        self.calls.append(kwargs)
        trial = kwargs['sweep']['trial']
        if trial in self.raise_for:
            raise RuntimeError(self.raise_for[trial])
        if trial in self.refuse_for:
            return {'status': 'insufficient_data', 'reason': self.refuse_for[trial]}

        sweep = kwargs['sweep']
        mean_ic = self.mean_for(trial)
        n_folds = int(kwargs['n_folds'])
        fold_ics = [round(mean_ic + index / 1000.0, 6) for index in range(n_folds)]
        type(self)._sequence += 1
        version = f"{kwargs['model_type_label']}-{kwargs['version_tag']}-{type(self)._sequence}"
        row = ModelVersion.objects.create(
            model_type=kwargs['model_type_label'],
            version=version,
            status=ModelVersion.Status.READY,
            is_active=(kwargs['activate'] if self.activate_override is None
                       else self.activate_override),
            metadata=({'sweep': sweep} if sweep is not None
                      else {'pooled_holdout': {'mean_rank_ic': mean_ic}}),
        )
        return {
            'status': 'success',
            'model_version': row.version,
            'model_version_id': row.pk,
            'artifact_path': f'/tmp/{version}.pt',
            'n_folds': n_folds,
            'folds': [{'fold': index, 'metrics': {'rank_ic': value}}
                      for index, value in enumerate(fold_ics)],
            'aggregate': {
                'mean_rank_ic': mean_ic,
                'mean_icir': round(mean_ic * 10.0, 6),
                'folds_with_positive_ic': sum(1 for value in fold_ics if value > 0),
                'folds_trained': n_folds,
            },
        }


def _sweep_options(**overrides):
    """The options dict ``handle`` receives, for calling the pure helpers directly."""
    options = {
        'start': None, 'end': None, 'folds': 6, 'horizon': None, 'min_train_days': None,
        'sequence_length': 20, 'limit': None, 'top': 10, 'force': False, 'plan': False,
        'dry_run': False, 'allow_holdout_overlap': False,
    }
    options.update(overrides)
    return options


class LstmSweepGridTests(SimpleTestCase):
    """The grid, the names and the fingerprints -- no database and no torch needed."""

    def test_the_grid_is_the_product_of_its_five_axes(self):
        combos = sweep_cmd.sweep_combinations()

        self.assertEqual(len(combos), sweep_cmd.SWEEP_EXPECTED_COMBOS)
        self.assertEqual(
            len(combos),
            (len(sweep_cmd.SWEEP_HIDDEN_SIZES) * len(sweep_cmd.SWEEP_NUM_LAYERS)
             * len(sweep_cmd.SWEEP_DROPOUTS) * len(sweep_cmd.SWEEP_LOSSES)
             * len(sweep_cmd.SWEEP_LR_EPOCHS)),
        )
        self.assertEqual(
            {(combo['hidden_size'], combo['num_layers'], combo['dropout'], combo['loss'],
              combo['learning_rate'], combo['epochs']) for combo in combos},
            {(*axes, lr_epochs[0], lr_epochs[1]) for *axes, lr_epochs in itertools.product(
                sweep_cmd.SWEEP_HIDDEN_SIZES, sweep_cmd.SWEEP_NUM_LAYERS,
                sweep_cmd.SWEEP_DROPOUTS, sweep_cmd.SWEEP_LOSSES, sweep_cmd.SWEEP_LR_EPOCHS)},
        )
        self.assertEqual(
            len({json.dumps(combo, sort_keys=True) for combo in combos}), len(combos),
            'two identical configurations would collide on name and fingerprint')
        self.assertEqual(len({sweep_cmd.trial_name(combo) for combo in combos}), len(combos))

    def test_the_loss_axis_is_what_train_lstm_actually_accepts(self):
        """The plan's ``objective {binary, lambdarank}`` axis names LightGBM knobs.

        Substituted by the LSTM's ``params['loss']`` values; if ``train_lstm`` changes what it
        validates, this fails and the grid constants have to move with it.
        """
        self.assertEqual({train_lstm.LOSS_BCE, train_lstm.LOSS_PAIRWISE},
                         set(sweep_cmd.SWEEP_LOSSES))

    def test_the_tuning_window_ends_the_day_before_the_locked_holdout(self):
        self.assertEqual(sweep_cmd.TUNING_WINDOW_END, date(2023, 12, 31))
        self.assertEqual(sweep_cmd.LOCKED_HOLDOUT_START, date(2024, 1, 1))
        self.assertLess(sweep_cmd.TUNING_WINDOW_END, sweep_cmd.LOCKED_HOLDOUT_START)

    def test_trial_names_show_the_whole_configuration_and_carry_the_prefix(self):
        combos = sweep_cmd.sweep_combinations()

        self.assertEqual(sweep_cmd.trial_name(combos[0]),
                         'tune-lstm-h64-l1-d020-bce-lr1e-3-e50')
        self.assertEqual(sweep_cmd.trial_name(combos[-1]),
                         'tune-lstm-h128-l2-d035-pair-lr3e-4-e80')
        for combo in combos:
            with self.subTest(loss=combo['loss'], lr=combo['learning_rate']):
                self.assertTrue(combo['loss'] in sweep_cmd.SWEEP_LOSSES)
                self.assertTrue(sweep_cmd.trial_name(combo)
                                .startswith(sweep_cmd.TUNE_NAME_PREFIX))

    def test_the_fingerprint_is_deterministic_hex_and_covers_the_protocol(self):
        combos = sweep_cmd.sweep_combinations()
        window = {'tuning_start': '2016-01-29', 'tuning_end': '2023-12-31'}
        knobs = sweep_cmd.protocol_knobs(_sweep_options())

        self.assertEqual(sweep_cmd.trial_fingerprint(combos[0], window, knobs),
                         sweep_cmd.trial_fingerprint(combos[0], window, knobs))
        self.assertRegex(sweep_cmd.trial_fingerprint(combos[0], window, knobs), r'^[0-9a-f]{16}$')
        self.assertNotEqual(sweep_cmd.trial_fingerprint(combos[0], window, knobs),
                            sweep_cmd.trial_fingerprint(combos[1], window, knobs))
        # Default knobs are the settings-respecting ones; None is a real value here, not a gap.
        self.assertEqual(knobs, {'folds': 6, 'horizon_days': None, 'min_train_days': None,
                                 'sequence_length': 20})

        variations = {
            'window start': (combos[0], {**window, 'tuning_start': '2017-01-03'}, knobs),
            'window end': (combos[0], {**window, 'tuning_end': '2022-12-30'}, knobs),
            'folds': (combos[0], window, {**knobs, 'folds': 8}),
            'sequence length': (combos[0], window, {**knobs, 'sequence_length': 30}),
            'horizon': (combos[0], window, {**knobs, 'horizon_days': 10}),
            'min train days': (combos[0], window, {**knobs, 'min_train_days': 300}),
        }
        base = sweep_cmd.trial_fingerprint(combos[0], window, knobs)
        for label, (combo, varied_window, varied_knobs) in variations.items():
            with self.subTest(variation=label):
                self.assertNotEqual(
                    sweep_cmd.trial_fingerprint(combo, varied_window, varied_knobs), base,
                    f'{label} must change the fingerprint, or a re-run reuses incomparable rows')

    def test_trial_parameters_record_everything_needed_to_reproduce_the_trial(self):
        combo = sweep_cmd.sweep_combinations()[0]
        window = {'tuning_start': '2016-01-29', 'tuning_end': '2023-12-31'}
        knobs = sweep_cmd.protocol_knobs(_sweep_options(folds=3))
        fingerprint = sweep_cmd.trial_fingerprint(combo, window, knobs)

        parameters = sweep_cmd.trial_parameters(combo, 'tune-lstm-x', fingerprint, window, knobs)

        self.assertEqual(parameters['sweep'], 'lstm')
        self.assertEqual(parameters['trial'], 'tune-lstm-x')
        self.assertEqual(parameters['fingerprint'], fingerprint)
        self.assertEqual(parameters['config'], combo)
        self.assertEqual(parameters['window'], window)
        self.assertEqual(parameters['protocol'], knobs)
        self.assertEqual(sorted(parameters['grid_axes']),
                         ['dropout', 'hidden_size', 'learning_rate_epochs', 'loss', 'num_layers'])

    def test_tuning_only_report_carries_the_fold_ics_and_says_what_it_is_not(self):
        window = {'tuning_start': '2016-01-29', 'tuning_end': '2023-12-31'}
        result = {
            'n_folds': 3,
            'folds': [{'metrics': {'rank_ic': 0.01}}, {'metrics': {}}, {}],
            'aggregate': {'mean_rank_ic': 0.01, 'mean_icir': 0.1,
                          'folds_with_positive_ic': 1, 'folds_trained': 3},
        }

        report = sweep_cmd.tuning_only_report(window, result)

        self.assertEqual(sorted(report), sorted([
            'window', 'fold_ics', 'mean_rank_ic', 'mean_icir', 'folds_with_positive_ic',
            'folds_trained', 'folds_planned', 'selection_rule', 'note']))
        self.assertEqual(report['window'], window)
        self.assertEqual(report['fold_ics'], [0.01, None, None])
        self.assertEqual(report['folds_planned'], 3)
        self.assertEqual(report['selection_rule'],
                         'aggregate.mean_rank_ic, then aggregate.folds_with_positive_ic')
        self.assertIn('Not evidence about live performance', report['note'])

    def test_resolve_window_defaults_to_the_locked_tuning_end_and_refuses_the_holdout(self):
        cases = {
            'default end': (_sweep_options(start='2016-01-29'),
                            {'tuning_start': '2016-01-29', 'tuning_end': '2023-12-31'}),
            'explicit end before the holdout': (
                _sweep_options(start='2016-01-29', end='2022-12-30'),
                {'tuning_start': '2016-01-29', 'tuning_end': '2022-12-30'}),
            'overlap authorised': (
                _sweep_options(start='2016-01-29', end='2024-06-28', allow_holdout_overlap=True),
                {'tuning_start': '2016-01-29', 'tuning_end': '2024-06-28'}),
        }
        for label, (options, expected) in cases.items():
            with self.subTest(case=label):
                self.assertEqual(sweep_cmd.resolve_window(options), expected)

        refusals = {
            'end deep inside the holdout': (_sweep_options(start='2016-01-29', end='2026-10-20'),
                                            'locked holdout'),
            'end exactly at the holdout start': (
                _sweep_options(start='2016-01-29', end='2024-01-01'), 'locked holdout'),
            'end before start': (_sweep_options(start='2020-01-01', end='2019-01-01'),
                                 'is before the panel start'),
            'malformed start': (_sweep_options(start='29/01/2016'), 'ISO format'),
            'malformed end': (_sweep_options(start='2016-01-29', end='31-12-2023'), 'ISO format'),
        }
        for label, (options, needle) in refusals.items():
            with self.subTest(case=label):
                with self.assertRaises(CommandError) as caught:
                    sweep_cmd.resolve_window(options)
                self.assertIn(needle, str(caught.exception))

    def test_the_trainer_gate_that_keeps_pooled_holdout_off_a_sweep_row_is_still_there(self):
        """``train_lstm`` must keep writing ``metadata['sweep']`` instead of the holdout key.

        The whole no-``pooled_holdout`` discipline is one ``if`` in the trainer, and the sweep
        cannot enforce it from the outside -- so the seam is pinned here rather than trusted.
        """
        source = inspect.getsource(train_lstm.train_walk_forward)

        self.assertIn('if sweep is None:', source)
        self.assertIn("run_metadata['pooled_holdout'] = pooled_metrics", source)
        self.assertIn("run_metadata['sweep'] = sweep", source)
        self.assertLess(source.index('if sweep is None:'),
                        source.index("run_metadata['pooled_holdout'] = pooled_metrics"),
                        'pooled_holdout must only be written on the sweep-is-None branch')
        self.assertEqual(source.count("run_metadata['pooled_holdout']"), 1)

    def test_the_sweep_calls_the_trainer_with_activation_suppressed_and_a_sweep_dict(self):
        source = inspect.getsource(sweep_cmd.Command._run_sweep)

        self.assertIn('activate=False', source)
        self.assertIn('sweep={', source)
        self.assertIn("params={'loss': combo['loss']}", source)


class LstmSweepCommandTests(TestCase):
    """``sweep_etf_rotation_lstm`` end to end against a stubbed trainer.

    ``--limit 3`` keeps the ledger small; the grid itself is pinned in ``LstmSweepGridTests``.
    Every test here is really about the three things the plan asks of the command: it is bounded,
    it is resumable, and it cannot leave a trace that reads like out-of-sample evidence.
    """

    START = '2016-01-29'
    END = '2023-12-31'
    LIMIT = 3

    #: A tuning row simulates nothing, so none of these may be filled in.
    PORTFOLIO_METRIC_FIELDS = (
        'total_return', 'annualized_return', 'max_drawdown', 'sharpe_ratio', 'calmar_ratio',
        'win_rate', 'turnover_annual', 'alpha_vs_benchmark', 'beta_vs_benchmark',
        'information_ratio', 'benchmark_return', 'total_fees')

    def _options(self, **overrides):
        merged = {'start': self.START, 'end': self.END, 'folds': 2, 'limit': self.LIMIT}
        merged.update(overrides)
        # ``call_command`` renders values into argv, so an explicit None has to drop out and
        # take the parser default rather than arrive as the string "None".
        return {key: value for key, value in merged.items() if value is not None}

    def _run(self, stub, **overrides) -> str:
        stdout = io.StringIO()
        with patch.object(sweep_cmd, '_trainer', return_value=stub):
            call_command('sweep_etf_rotation_lstm', stdout=stdout, **self._options(**overrides))
        return stdout.getvalue()

    def _run_expect_error(self, stub, **overrides) -> tuple[str, str]:
        stdout = io.StringIO()
        with patch.object(sweep_cmd, '_trainer', return_value=stub):
            with self.assertRaises(CommandError) as caught:
                call_command('sweep_etf_rotation_lstm', stdout=stdout,
                             **self._options(**overrides))
        return str(caught.exception), stdout.getvalue()

    def _rows(self):
        return list(ETFRotationBacktest.objects
                    .filter(name__startswith=sweep_cmd.TUNE_NAME_PREFIX)
                    .order_by('name'))

    def _combos(self):
        return sweep_cmd.sweep_combinations()[:self.LIMIT]

    def test_one_run_writes_one_prefixed_ledger_row_per_configuration(self):
        output = self._run(_SweepStubTrainer())
        rows = self._rows()

        self.assertEqual(len(rows), self.LIMIT)
        self.assertEqual(ETFRotationBacktest.objects.count(), self.LIMIT,
                         'the sweep must not write any row outside its own prefix, and needs no '
                         'campaign table to keep score')
        for row, combo in zip(rows, self._combos()):
            with self.subTest(trial=row.name):
                self.assertEqual(row.name, sweep_cmd.trial_name(combo))
                self.assertTrue(row.name.startswith(sweep_cmd.TUNE_NAME_PREFIX))
                self.assertEqual(row.model_type, ETFRotationBacktest.ModelType.LSTM)
                self.assertEqual(row.status, ETFRotationBacktest.Status.COMPLETED)
                self.assertEqual(row.start_date, date.fromisoformat(self.START))
                self.assertEqual(row.end_date, date.fromisoformat(self.END))
                self.assertEqual(row.parameters['sweep'], 'lstm')
                self.assertEqual(row.parameters['config'], combo)
                self.assertEqual(row.parameters['window'],
                                 {'tuning_start': self.START, 'tuning_end': self.END})
                self.assertEqual(row.parameters['grid_axes']['loss'],
                                 list(sweep_cmd.SWEEP_LOSSES))
                self.assertEqual(row.report['tuning_only']['window'],
                                 {'tuning_start': self.START, 'tuning_end': self.END})
                self.assertEqual(row.error_message, '')
                self.assertIsNotNone(row.completed_at)
        self.assertIn('activation guard: OK', output)

    def test_a_tuning_row_is_empty_of_portfolio_evidence_by_construction(self):
        self._run(_SweepStubTrainer())

        for row in self._rows():
            with self.subTest(trial=row.name):
                self.assertEqual(
                    [getattr(row, field) for field in self.PORTFOLIO_METRIC_FIELDS],
                    [None] * len(self.PORTFOLIO_METRIC_FIELDS),
                    'a rank IC must not be readable as alpha: the tuning row simulates no portfolio')
                self.assertEqual(row.equity_curve, [])
                self.assertEqual(row.trade_log, [])
                tuning = row.report['tuning_only']
                self.assertEqual(len(tuning['fold_ics']), 2, 'one IC per planned fold')
                self.assertEqual(tuning['folds_planned'], 2)
                self.assertIn('Not evidence about live performance', tuning['note'])

    def test_every_trial_is_registered_inactive_and_no_row_gets_a_pooled_holdout(self):
        stub = _SweepStubTrainer()
        self._run(stub)
        window = {'tuning_start': self.START, 'tuning_end': self.END}

        self.assertEqual(len(stub.calls), self.LIMIT)
        for call, combo in zip(stub.calls, self._combos()):
            with self.subTest(trial=call['sweep']['trial']):
                self.assertIs(call['activate'], False)
                self.assertEqual(call['params'], {'loss': combo['loss']})
                self.assertEqual(call['model_type_label'], 'ETF_LSTM')
                self.assertEqual(call['hidden_size'], combo['hidden_size'])
                self.assertEqual(call['num_layers'], combo['num_layers'])
                self.assertEqual(call['dropout'], combo['dropout'])
                self.assertEqual(call['epochs'], combo['epochs'])
                self.assertEqual(call['learning_rate'], combo['learning_rate'])
                self.assertEqual(call['n_folds'], 2)
                self.assertEqual(call['sequence_length'], 20)
                self.assertEqual(call['start_date'], date.fromisoformat(self.START))
                self.assertEqual(call['end_date'], date.fromisoformat(self.END))
                self.assertEqual(call['sweep']['campaign'], 'sweep_etf_rotation_lstm')
                self.assertIs(call['sweep']['activated'], False)
                self.assertEqual(call['sweep']['trials_in_grid'], self.LIMIT)
                self.assertEqual(call['sweep']['grid'], combo)
                self.assertEqual(call['sweep']['tuning_window'], window)
                self.assertEqual(call['version_tag'],
                                 f"t-{call['sweep']['fingerprint'][:8]}")

        rows = list(ModelVersion.objects.filter(model_type='ETF_LSTM'))
        self.assertEqual(len(rows), self.LIMIT)
        for row in rows:
            with self.subTest(version=row.version):
                self.assertFalse(row.is_active)
                self.assertNotIn('pooled_holdout', row.metadata,
                                 'pooled_holdout is the key the ensemble weights this member by')
                self.assertIn('sweep', row.metadata)

    def test_the_sweep_leaves_the_active_incumbent_alone(self):
        incumbent = ModelVersion.objects.create(
            model_type='ETF_LSTM', version='etf-lstm-prod-v1',
            status=ModelVersion.Status.READY, is_active=True, metadata={})

        output = self._run(_SweepStubTrainer())

        incumbent.refresh_from_db()
        self.assertTrue(incumbent.is_active)
        self.assertEqual(
            list(ModelVersion.objects.filter(model_type='ETF_LSTM', is_active=True)
                 .values_list('version', flat=True)), ['etf-lstm-prod-v1'])
        self.assertIn('activation guard: OK', output)

    def test_a_trainer_that_activates_fails_the_command_loudly(self):
        """The guard is the Phase C acceptance test, and it runs even when it has nothing to fix."""
        stub = _SweepStubTrainer(activate_override=True)

        message, _ = self._run_expect_error(stub)

        self.assertIn('activation guard FAILED', message)
        self.assertIn('A sweep must never activate a candidate', message)
        self.assertNotEqual(
            list(ModelVersion.objects.filter(model_type='ETF_LSTM', is_active=True)
                 .values_list('version', flat=True)), [],
            'the guard reports a real breach; it does not merely narrate one')

    def test_a_sweep_that_ends_mid_failure_still_leaves_the_active_set_alone(self):
        """The guard runs after the table, so a partially-failed sweep is still checked."""
        combos = self._combos()
        stub = _SweepStubTrainer(raise_for={sweep_cmd.trial_name(combos[0]): 'CUDA out of memory'})

        output = self._run(stub)

        self.assertIn('activation guard: OK', output)
        self.assertFalse(ModelVersion.objects.filter(is_active=True).exists())

    def test_a_second_identical_run_trains_nothing_and_reuses_the_ledger(self):
        stub = _SweepStubTrainer()
        self._run(stub)
        resumed = _SweepStubTrainer()
        output = self._run(resumed)

        self.assertEqual(resumed.calls, [], 'a resumed sweep must not re-train a COMPLETED trial')
        self.assertEqual(len(stub.calls), self.LIMIT)
        self.assertIn('already COMPLETED', output)
        self.assertIn(f'{self.LIMIT} reused from persisted rows', output)
        self.assertEqual(self._rows()[0].report['tuning_only']['folds_trained'], 2)

    def test_a_changed_window_invalidates_the_stored_fingerprint_and_re_runs(self):
        stub = _SweepStubTrainer()
        self._run(stub)
        before = {row.name: dict(row.parameters) for row in self._rows()}

        second = _SweepStubTrainer()
        output = self._run(second, end='2022-12-30')

        self.assertEqual(len(second.calls), self.LIMIT,
                         'the name does not encode the window, so a stale row cannot be reused')
        self.assertIn('does not match', output)
        for row in self._rows():
            with self.subTest(trial=row.name):
                stored = before[row.name]
                self.assertNotEqual(row.parameters['fingerprint'], stored['fingerprint'])
                self.assertEqual(row.end_date, date(2022, 12, 30))
                self.assertEqual(row.parameters['window']['tuning_end'], '2022-12-30')
                self.assertEqual(row.report['tuning_only']['window']['tuning_end'], '2022-12-30')

    def test_a_changed_fold_plan_is_a_different_experiment(self):
        self._run(_SweepStubTrainer())
        fingerprints = {row.name: row.parameters['fingerprint'] for row in self._rows()}

        stub = _SweepStubTrainer()
        self._run(stub, folds=4)

        self.assertEqual(len(stub.calls), self.LIMIT)
        for row in self._rows():
            self.assertNotEqual(row.parameters['fingerprint'], fingerprints[row.name])
            self.assertEqual(row.parameters['protocol']['folds'], 4)
            self.assertEqual(len(row.report['tuning_only']['fold_ics']), 4)

    def test_force_re_trains_a_completed_trial(self):
        self._run(_SweepStubTrainer())

        stub = _SweepStubTrainer()
        output = self._run(stub, force=True)

        self.assertEqual(len(stub.calls), self.LIMIT)
        self.assertIn('0 reused from persisted rows', output)

    def test_one_trial_running_out_of_vram_does_not_cost_the_others(self):
        combos = self._combos()
        broken = sweep_cmd.trial_name(combos[1])
        stub = _SweepStubTrainer(raise_for={broken: 'CUDA out of memory'})

        output = self._run(stub)

        by_name = {row.name: row for row in self._rows()}
        self.assertEqual(by_name[broken].status, ETFRotationBacktest.Status.FAILED)
        self.assertIn('RuntimeError: CUDA out of memory', by_name[broken].error_message)
        self.assertEqual(by_name[sweep_cmd.trial_name(combos[0])].status,
                         ETFRotationBacktest.Status.COMPLETED)
        self.assertEqual(by_name[sweep_cmd.trial_name(combos[2])].status,
                         ETFRotationBacktest.Status.COMPLETED,
                         'the trial after a failure must still run')
        self.assertIn('1 trial(s) failed', output)

    def test_a_trial_the_panel_cannot_train_is_recorded_as_a_refusal_not_a_crash(self):
        combos = self._combos()
        name = sweep_cmd.trial_name(combos[0])
        stub = _SweepStubTrainer(refuse_for={name: 'only 3 label-complete dates'})

        output = self._run(stub)

        row = ETFRotationBacktest.objects.get(name=name)
        self.assertEqual(row.status, ETFRotationBacktest.Status.FAILED)
        self.assertEqual(row.error_message, 'insufficient_data: only 3 label-complete dates')
        self.assertIn('1 trial(s) failed', output)

    def test_the_winner_is_the_highest_mean_rank_ic_and_gets_the_whole_campaign_stamped_on_it(self):
        combos = self._combos()
        ranking = {sweep_cmd.trial_name(combo): 0.01 + index * 0.04 for index, combo in
                   enumerate(combos)}
        best = sweep_cmd.trial_name(combos[2])
        stub = _SweepStubTrainer(mean_ic_by_trial=ranking)

        output = self._run(stub, top=2)

        self.assertIn(f'mean_rank_ic={0.09:.4f}', output)
        self.assertIn('runner-up', output)
        winner = ModelVersion.objects.get(pk=ETFRotationBacktest.objects.get(
            name=best).parameters['model_version_id'])
        sweep = winner.metadata['sweep']

        self.assertFalse(winner.is_active)
        self.assertNotIn('pooled_holdout', winner.metadata)
        self.assertTrue(sweep['selected'])
        self.assertEqual(sweep['selected_config'], combos[2])
        self.assertEqual(sweep['trials_in_grid'], self.LIMIT)
        self.assertEqual(sweep['tuning_window'], {'tuning_start': self.START,
                                                  'tuning_end': self.END})
        self.assertEqual(len(sweep['per_trial_ic']), self.LIMIT)
        self.assertEqual({entry['trial'] for entry in sweep['per_trial_ic']}, set(ranking))
        self.assertIn('registered inactive', sweep['deployment_note'])
        self.assertIn('--- selected candidate ---', output)
        self.assertIn('stamped sweep provenance', output)

    def test_the_phase_d_recipe_it_prints_cannot_train_through_the_holdout(self):
        """The paste block is how a human spends the holdout, so its window is part of the guard."""
        output = self._run(_SweepStubTrainer())

        self.assertIn('Phase D single-shot retrain', output)
        self.assertIn('date(2023, 12, 31)', output)
        self.assertNotIn('date(2024, ', output,
                         'a retrain whose end_date is inside the locked holdout is the +513.76% '
                         'mistake this protocol exists to prevent')
        self.assertIn("version_tag='oos-lstm-v2', activate=False", output)
        self.assertIn('+513.76%', output)

    def test_an_all_negative_sweep_still_names_a_candidate_instead_of_inventing_a_win(self):
        stub = _SweepStubTrainer(mean_ic=-0.02)

        output = self._run(stub)

        self.assertIn('--- selected candidate ---', output)
        self.assertIn('mean_rank_ic=-0.0200', output)
        self.assertIn('positive_folds=0/2', output)

    def test_a_sweep_where_everything_fails_produces_no_candidate_and_no_error(self):
        combos = self._combos()
        stub = _SweepStubTrainer(raise_for={sweep_cmd.trial_name(combo): 'boom'
                                           for combo in combos})

        output = self._run(stub)

        self.assertIn('no trial produced metrics; nothing to rank', output)
        self.assertIn('no candidate to promote', output)
        self.assertNotIn('--- selected candidate ---', output)
        self.assertTrue(all(row.status == ETFRotationBacktest.Status.FAILED for row in self._rows()))

    def test_a_sweep_row_carrying_pooled_holdout_is_treated_as_a_breach(self):
        contaminated = ModelVersion.objects.create(
            model_type='ETF_LSTM', version='etf-lstm-contaminated',
            status=ModelVersion.Status.READY,
            metadata={'pooled_holdout': {'mean_rank_ic': 0.5}})
        command = sweep_cmd.Command()

        with self.assertRaises(CommandError) as caught:
            command._stamp_winner(
                {'model_version_id': contaminated.pk, 'name': 'tune-lstm-x',
                 'combo': sweep_cmd.sweep_combinations()[0]},
                [], {'tuning_start': self.START, 'tuning_end': self.END},
                sweep_cmd.protocol_knobs(_sweep_options()), 1)

        self.assertIn('contamination discipline has been broken', str(caught.exception))

    def test_limit_runs_a_subset_and_says_so(self):
        stub = _SweepStubTrainer()
        output = self._run(stub, limit=1)

        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(len(self._rows()), 1)
        self.assertIn('running a subset, not the full 32-trial grid', output)

    def test_a_trainer_whose_loss_constants_moved_stops_the_sweep_before_it_trains(self):
        stub = _SweepStubTrainer(losses=('binary', 'lambdarank'))

        message, _ = self._run_expect_error(stub)

        self.assertIn('drifted apart', message)
        self.assertEqual(stub.calls, [])
        self.assertEqual(ETFRotationBacktest.objects.count(), 0)

    def test_an_end_inside_the_locked_holdout_is_refused_before_anything_is_trained(self):
        stub = _SweepStubTrainer()

        message, _ = self._run_expect_error(stub, end='2024-01-01')

        self.assertIn('locked holdout', message)
        self.assertEqual(stub.calls, [])
        self.assertEqual(ETFRotationBacktest.objects.count(), 0)

        authorised = _SweepStubTrainer()
        self._run(authorised, end='2024-01-01', allow_holdout_overlap=True)

        self.assertEqual(len(authorised.calls), self.LIMIT,
                         'the flag is the documented escape hatch once the holdout is spent')

    def test_the_default_start_is_the_earliest_eligible_snapshot(self):
        etf = _seed_etf('512880.SH')
        ETFFeatureSnapshot.objects.create(etf=etf, date=date(2018, 5, 2), is_eligible=True)
        ETFFeatureSnapshot.objects.create(etf=etf, date=date(2017, 1, 3), is_eligible=False)

        stub = _SweepStubTrainer()
        self._run(stub, start=None)

        self.assertEqual(stub.calls[0]['start_date'], date(2018, 5, 2))
        self.assertEqual(self._rows()[0].start_date, date(2018, 5, 2))

    def test_an_empty_panel_stops_a_sweep_but_only_warns_a_dry_run(self):
        stdout = io.StringIO()
        with patch.object(sweep_cmd, '_trainer',
                          side_effect=AssertionError('a dry run must not import torch')):
            with self.assertRaises(CommandError) as caught:
                call_command('sweep_etf_rotation_lstm', stdout=stdout,
                             **self._options(start=None))
        self.assertIn('no is_eligible rows', str(caught.exception))

        with patch.object(sweep_cmd, '_trainer',
                          side_effect=AssertionError('a dry run must not import torch')) as never:
            stdout = io.StringIO()
            call_command('sweep_etf_rotation_lstm', stdout=stdout,
                         **self._options(start=None, dry_run=True))

        self.assertFalse(never.called)
        self.assertIn('dry run: ETFFeatureSnapshot has no is_eligible rows', stdout.getvalue())
        self.assertEqual(ETFRotationBacktest.objects.count(), 0)

    def test_plan_and_dry_run_read_the_grid_without_writing_or_training(self):
        stdout = io.StringIO()
        with patch.object(sweep_cmd, '_trainer',
                          side_effect=AssertionError('--plan must not import torch')) as never:
            call_command('sweep_etf_rotation_lstm', stdout=stdout,
                         **self._options(plan=True, limit=None, folds=None))
        plan = stdout.getvalue()

        self.assertFalse(never.called)
        self.assertIn('planned LSTM trials: 32 = 2 hidden_size x 2 num_layers x 2 dropout '
                      'x 2 loss x 2 (learning_rate, epochs)', plan)
        self.assertIn('32 configurations listed', plan)
        self.assertIn('cfg-fp', plan)
        self.assertIn('belongs to LightGBM', plan)
        self.assertEqual(ETFRotationBacktest.objects.count(), 0)

        stdout = io.StringIO()
        with patch.object(sweep_cmd, '_trainer',
                          side_effect=AssertionError('--dry-run must not import torch')):
            call_command('sweep_etf_rotation_lstm', stdout=stdout,
                         **self._options(plan=True, dry_run=True, limit=None, folds=None))
        dry = stdout.getvalue()

        self.assertIn('resolved tuning window: 2016-01-29..2023-12-31 (32 trials x 6 folds)', dry)
        self.assertIn('dry run trains nothing and writes nothing', dry)
        self.assertIn('ledger rows by status: PENDING=0, RUNNING=0, PAUSED=0, COMPLETED=0, '
                      'FAILED=0 (of 0 total)', dry)
        self.assertEqual(ETFRotationBacktest.objects.count(), 0)

    def test_the_workbench_endpoint_answers_for_a_sweep_row_without_inventing_a_curve(self):
        self._run(_SweepStubTrainer())
        row = self._rows()[0]

        response = self.client.get(f'/api/v1/etf/backtests/{row.id}/comparison_curve/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'No equity curve is stored for this run yet.')
        self.assertEqual(response.json()['available_series_keys'], [])

    def test_trials_run_in_this_process_rather_than_on_the_train_queue(self):
        """Documented deviation from the plan's "sequentially through the ``train-lstm`` queue".

        That queue's worker comes up with ``--concurrency 2``, so two dispatched trials can hold
        VRAM at once; one in-process loop holds exactly one. Same constraint, stricter reading.
        """
        source = inspect.getsource(sweep_cmd.Command._run_sweep)

        self.assertIn('trainer.train_walk_forward(', source)
        for dispatched in ('apply_async', 'delay(', 'queue_etf', 'send_task', 'chord'):
            self.assertNotIn(dispatched, source)


# ---------------------------------------------------------------------------
# Selection constraints: tracked-index dedup, category cap, share tie-break
# ---------------------------------------------------------------------------


class TrackIndexNormalisationTests(SimpleTestCase):
    """The benchmark decorations that actually repeat collapse; the index itself does not.

    Every string below is a shape measured in ``ETFUniverse.benchmark`` over the rankable
    rows. Asserting them as a group is the point: a normaliser tuned on the one example that
    was in front of the author passes this file and then merges 中证银行 onto 中证800银行 in
    production, which quietly deletes a bet from the book.
    """

    def _key(self, benchmark, name=''):
        return normalize_track_index(name=name, benchmark=benchmark)

    def test_repeated_decorations_of_one_index_share_one_key(self):
        shapes = (
            '中证银行指数收益率×100%',
            '中证银行指数×100%',
            '中证银行指数收益率×100%（人民币）',
            '中证银行指数收益率 × 100% (使用估值汇率折算)',
            '中证银行指数(人民币)收益率×100%',
        )
        keys = {self._key(shape, name='中证银行ETF') for shape in shapes}
        self.assertEqual(keys, {'中证银行'})

    def test_a_different_index_keeps_its_own_key(self):
        bank = self._key('中证银行指数收益率×100%')
        c800_bank = self._key('中证800银行指数收益率×100%')
        self.assertNotEqual(bank, c800_bank)

    def test_a_two_leg_blend_is_not_collapsed_onto_its_dominant_leg(self):
        blend = self._key('沪深300指数收益率×95%+中债-沪深300全价指数收益率×5%')
        self.assertIn('+', blend)
        self.assertNotEqual(blend, self._key('沪深300指数收益率×100%'))

    def test_a_blank_benchmark_falls_back_to_the_name(self):
        self.assertEqual(self._key('', name='恒生医疗ETF'), '恒生医疗etf')
        self.assertEqual(normalize_track_index(name='', benchmark=''), '')

    def test_the_key_never_exceeds_the_column(self):
        self.assertEqual(
            len(normalize_track_index(name='', benchmark='上证' + '指' * 400 + '指数收益率×100%')),
            TRACK_INDEX_MAX_LENGTH,
        )


class FundSizeEstimateTests(SimpleTestCase):
    """``shares x close`` is an estimate of AUM, and it is honest about missing inputs."""

    def test_a_stamp_and_a_price_make_a_number(self):
        self.assertAlmostEqual(shares_to_aum_yuan(Decimal('44084.53'), 1.234), 44084.53 * 1.234)

    def test_a_missing_input_makes_none_rather_than_zero(self):
        for shares, close in ((None, 1.0), (1000, None), (0, 1.0), (1000, 0.0), ('x', 1.0)):
            with self.subTest(shares=shares, close=close):
                self.assertIsNone(shares_to_aum_yuan(shares, close))


class MoneyTierClassificationTests(SimpleTestCase):
    """``现金流`` is a substring of both a cash vehicle and an equity screen; only one is money."""

    def test_free_cash_flow_equity_is_not_a_cash_vehicle(self):
        for name in ('招商中证800自由现金流ETF', '国泰上证主板现金流ETF', '景顺长城中证现金流ETF'):
            with self.subTest(name=name):
                self.assertNotEqual(classify_etf(name=name, invest_type='被动指数型')[0], ETFCategory.MONEY)

    def test_the_real_cash_vehicles_still_classify_as_money(self):
        self.assertEqual(classify_etf(name='添富现金ETF', invest_type='')[0], ETFCategory.MONEY)
        self.assertEqual(classify_etf(name='华夏现金宝货币ETF', invest_type='')[0], ETFCategory.MONEY)
        self.assertEqual(
            classify_etf(name='任意名称', invest_type='货币型')[0], ETFCategory.MONEY,
            'invest_type alone must be enough',
        )


def _bank_book():
    """The 2026-10-20 top of the ranking, with the two properties that made it a problem.

    512700.SH and 512820.SH score byte-identically (0.71024590), the first two names track the
    same index, and all five sit in one category. So: score alone cannot arbitrate the tie, and
    rank alone buys the same bet twice. Shares are set so the cluster winner is NOT the
    higher-scoring wrapper -- 512700 (1e9 份) beats 515020 (4e8 份) on size while losing on
    score -- which is the whole point of the constraint.
    """
    return {
        'ordered': ['515020.SH', '512700.SH', '512820.SH', '159887.SH', '515300.SH'],
        'score_by_code': {
            '515020.SH': 0.71270492, '512700.SH': 0.71024590, '512820.SH': 0.71024590,
            '159887.SH': 0.70901639, '515300.SH': 0.69959016,
        },
        'key_by_code': {
            '515020.SH': '中证银行', '512700.SH': '中证银行', '512820.SH': '中证800银行',
            '159887.SH': '中证800银行', '515300.SH': '沪深300银行',
        },
        'category_by_code': {code: 'bank' for code in ('515020.SH', '512700.SH', '512820.SH',
                                                       '159887.SH', '515300.SH')},
        'shares_by_code': {
            '515020.SH': 400_000_000, '512700.SH': 1_000_000_000, '512820.SH': 900_000_000,
            '159887.SH': 50_000_000, '515300.SH': 2_000_000_000,
        },
    }


class SelectionConstraintTests(SimpleTestCase):
    """One fund per tracked index, then at most ``max_per_category`` per category."""

    def test_the_largest_fund_represents_its_cluster(self):
        book = _bank_book()
        survivors, info = rotation_engine.apply_selection_constraints(
            book['ordered'], score_by_code=book['score_by_code'],
            key_by_code=book['key_by_code'], shares_by_code=book['shares_by_code'],
            max_per_category=0,
        )
        self.assertEqual(survivors, ['512700.SH', '512820.SH', '515300.SH'])
        self.assertEqual(
            {entry['ts_code'] for entry in info['dedup_dropped']}, {'515020.SH', '159887.SH'},
        )
        self.assertEqual({entry['reason'] for entry in info['dedup_dropped']},
                         {'same_tracked_index'})
        self.assertEqual(info['cluster_count'], 3)

    def test_turnover_arbitrates_when_shares_are_missing(self):
        book = _bank_book()
        survivors, info = rotation_engine.apply_selection_constraints(
            book['ordered'], score_by_code=book['score_by_code'],
            key_by_code=book['key_by_code'],
            amount_by_code={'515020.SH': 300_000_000.0, '512700.SH': 100_000_000.0},
            max_per_category=0,
        )
        self.assertEqual(survivors[0], '515020.SH')
        self.assertEqual(
            {entry['ts_code'] for entry in info['dedup_dropped']}, {'512700.SH', '159887.SH'},
        )

    def test_score_is_the_last_resort(self):
        book = _bank_book()
        survivors, _info = rotation_engine.apply_selection_constraints(
            book['ordered'], score_by_code=book['score_by_code'],
            key_by_code=book['key_by_code'], max_per_category=0,
        )
        self.assertIn('515020.SH', survivors)
        self.assertNotIn('512700.SH', survivors)

    def test_the_category_cap_takes_what_dedup_left(self):
        book = _bank_book()
        survivors, info = rotation_engine.apply_selection_constraints(
            book['ordered'], score_by_code=book['score_by_code'],
            category_by_code=book['category_by_code'], key_by_code=book['key_by_code'],
            shares_by_code=book['shares_by_code'], max_per_category=2,
        )
        self.assertEqual(survivors, ['512700.SH', '512820.SH'])
        self.assertEqual(info['category_cap_dropped_count'], 1)
        self.assertEqual(info['category_cap_dropped'][0]['reason'], 'max_per_category')
        self.assertEqual(info['category_counts'], {'bank': 2})

    def test_dedup_off_and_cap_zero_leave_the_ranking_alone(self):
        book = _bank_book()
        survivors, info = rotation_engine.apply_selection_constraints(
            book['ordered'], score_by_code=book['score_by_code'],
            category_by_code=book['category_by_code'], key_by_code=book['key_by_code'],
            shares_by_code=book['shares_by_code'], dedup_track_index=False, max_per_category=0,
        )
        self.assertEqual(survivors, book['ordered'])
        self.assertEqual(info['dedup_dropped'], [])
        self.assertEqual(info['category_cap_dropped'], [])

    def test_an_unknown_key_never_becomes_a_cluster(self):
        """Blank keys must not merge: every unclassified fund is its own leg."""
        codes = ['512100.SH', '515880.SH', '512690.SH']
        survivors, info = rotation_engine.apply_selection_constraints(
            codes, score_by_code={code: 0.5 - index / 10 for index, code in enumerate(codes)},
            key_by_code={code: '' for code in codes}, max_per_category=0,
        )
        self.assertEqual(survivors, codes)
        self.assertEqual(info['cluster_count'], 3)

    def test_constraints_are_inert_without_the_columns(self):
        """A fixture or an old snapshot frame with no descriptors cannot fail on them."""
        codes = ['510300.SH', '511880.SH']
        survivors, info = rotation_engine.apply_selection_constraints(
            codes, score_by_code={'510300.SH': 0.2, '511880.SH': 0.1}, max_per_category=2,
        )
        self.assertEqual(survivors, codes)
        self.assertEqual(info['output_count'], 2)


class ScoreOrderingTieBreakTests(SimpleTestCase):
    """份额, not ``ts_code``, breaks an exact score tie."""

    def test_equal_scores_sort_by_shares_then_code(self):
        frame = pd.DataFrame([
            {'ts_code': '512820.SH', 'score': 0.71024590, 'shares_outstanding': 900_000_000},
            {'ts_code': '512700.SH', 'score': 0.71024590, 'shares_outstanding': 400_000_000},
            {'ts_code': '159934.SZ', 'score': 0.71024590, 'shares_outstanding': None},
            {'ts_code': '510100.SH', 'score': 0.80},
        ])
        self.assertEqual(
            list(rotation_engine._ordered_by_score(frame)['ts_code']),
            ['510100.SH', '512820.SH', '512700.SH', '159934.SZ'],
        )

    def test_a_frame_without_shares_orders_on_score_and_code(self):
        frame = pd.DataFrame([
            {'ts_code': '512700.SH', 'score': 0.5},
            {'ts_code': '512820.SH', 'score': 0.5},
        ])
        self.assertEqual(
            list(rotation_engine._ordered_by_score(frame)['ts_code']), ['512700.SH', '512820.SH'],
        )


# ---------------------------------------------------------------------------
# Sizing: inverse-volatility legs and the exposure governor
# ---------------------------------------------------------------------------


class InverseVolatilityWeightingTests(SimpleTestCase):
    """``invvol`` is a clamped heuristic, not an optimiser, and says so when it cannot run."""

    def test_flatter_legs_take_more_but_never_more_than_half_the_book(self):
        weights, method = rotation_engine.allocate_weights(
            [1, 2, 3], weighting='invvol', risk_budget=1.0, vols=[0.05, 0.10, 0.20],
        )
        self.assertEqual(method, 'invvol_clamped_0.5x_1.5x')
        self.assertAlmostEqual(sum(weights), 1.0, places=6)
        self.assertEqual(weights, sorted(weights, reverse=True))
        # The bound is on the final weight, not on the pre-renormalisation tilt: a single
        # pass that clamped and then rescaled put the capped leg back over 0.5.
        for weight in weights:
            self.assertLessEqual(weight, 1.5 / 3.0 + 1e-5)
            self.assertGreaterEqual(weight, 0.5 / 3.0 - 1e-5)

    def test_a_flat_fund_cannot_take_the_whole_book(self):
        weights, _method = rotation_engine.allocate_weights(
            [1, 2], weighting='invvol', risk_budget=1.0, vols=[0.0001, 0.30],
        )
        self.assertLess(weights[0], 0.8, 'the clamp, not 1/vol, is what caps this leg')

    def test_a_missing_vol_degrades_to_equal_and_records_it(self):
        for vols in ([0.05, None, 0.20], [0.05, 0.0, 0.20], None, []):
            with self.subTest(vols=vols):
                weights, method = rotation_engine.allocate_weights(
                    [1, 2, 3], weighting='invvol', risk_budget=0.9, vols=vols,
                )
                self.assertEqual(method, 'invvol_unavailable_equal')
                self.assertEqual(weights, [0.3, 0.3, 0.3])

    def test_the_budget_is_split_not_rescaled(self):
        weights, _method = rotation_engine.allocate_weights(
            [1, 2], weighting='invvol', risk_budget=0.6, vols=[0.10, 0.10],
        )
        self.assertEqual(weights, [0.3, 0.3])


    def test_every_weighting_mode_spends_exactly_the_budget_it_is_given(self):
        """The three modes disagree about *who* gets what, never about *how much*.

        ``allocate_weights`` is the only place the risk-on sleeve is divided, and the leg
        weights are written straight into the order size, so a mode that renormalised against
        1.0 instead of ``risk_budget`` would silently over-invest the cash the defensive sleeve
        was supposed to hold. It is checked at a fractional budget and against ranks that are
        not consecutive, because the score tilt is rank-decaying.
        """
        for weighting in backtest_engine.WEIGHTING_MODES:
            with self.subTest(weighting=weighting):
                weights, _method = rotation_engine.allocate_weights(
                    [1, 2, 7], weighting=weighting, risk_budget=0.7, vols=[0.05, 0.10, 0.40],
                )
                self.assertAlmostEqual(sum(weights), 0.7, places=6)
                self.assertTrue(all(weight >= 0.0 for weight in weights), weights)

    def test_the_three_modes_really_do_split_it_differently(self):
        """A parity test alone cannot tell "three modes" from "one mode reached three times"."""
        splits = {
            weighting: rotation_engine.allocate_weights(
                [1, 2, 7], weighting=weighting, risk_budget=1.0, vols=[0.05, 0.10, 0.40],
            )[0]
            for weighting in backtest_engine.WEIGHTING_MODES
        }
        self.assertEqual(len({tuple(value) for value in splits.values()}), 3, splits)
        # The flattest leg is the one both tilts agree deserves more than an equal share, and
        # for opposite reasons: score because it ranks first, invvol because it moves least.
        for weighting in ('score', 'invvol'):
            self.assertGreater(splits[weighting][0], splits['equal'][0], weighting)


def _kelly_config(**overrides):
    values = {'start_date': date(2024, 1, 2), 'end_date': date(2024, 6, 28)}
    values.update(overrides)
    return backtest_engine.BacktestConfig(**values)


class ExposureGovernorTests(SimpleTestCase):
    """Fractional Kelly sizes the portfolio from realised trades only, and off means off."""

    def test_the_default_fraction_disables_it_without_touching_the_scale(self):
        governor = backtest_engine.kelly_exposure_scale(_kelly_config(), [0.1] * 50)
        self.assertEqual(governor, {'scale': 1.0, 'enabled': False})

    def test_a_short_sample_declines_to_guess(self):
        governor = backtest_engine.kelly_exposure_scale(
            _kelly_config(kelly_fraction=0.25), [0.05] * (backtest_engine.MIN_KELLY_TRADES - 1),
        )
        self.assertEqual(governor['scale'], 1.0)
        self.assertEqual(governor['reason'], 'insufficient_closed_trades')

    def test_a_book_without_losses_has_no_measurable_payoff(self):
        governor = backtest_engine.kelly_exposure_scale(
            _kelly_config(kelly_fraction=0.25), [0.05] * backtest_engine.MIN_KELLY_TRADES,
        )
        self.assertEqual(governor['scale'], 1.0)
        self.assertEqual(governor['reason'], 'payoff_not_measurable')

    def test_the_scale_is_the_hand_computed_fractional_kelly(self):
        # p = 15/20 = 0.75, b = mean win 0.10 / mean loss 0.05 = 2.0, so
        # f* = 0.75 - 0.25/2 = 0.625 and a half-Kelly book is 0.3125.
        returns = [0.10] * 15 + [-0.05] * 5
        governor = backtest_engine.kelly_exposure_scale(
            _kelly_config(kelly_fraction=0.5, kelly_cap=1.0, kelly_floor=0.0), returns,
        )
        self.assertAlmostEqual(governor['scale'], 0.3125, places=6)
        self.assertEqual(governor['payoff_ratio'], 2.0)
        self.assertEqual(governor['win_rate'], 0.75)
        self.assertFalse(governor['clamped'])

    def test_the_clamp_binds_both_ways(self):
        losing = [0.10] * 2 + [-0.05] * 18           # a negative edge floors at kelly_floor
        floored = backtest_engine.kelly_exposure_scale(
            _kelly_config(kelly_fraction=0.5, kelly_floor=0.2), losing,
        )
        self.assertEqual(floored['scale'], 0.2)
        self.assertTrue(floored['clamped'])
        capped = backtest_engine.kelly_exposure_scale(
            _kelly_config(kelly_fraction=1.0, kelly_cap=0.3), [0.10] * 18 + [-0.01] * 2,
        )
        self.assertEqual(capped['scale'], 0.3)

    def test_only_trades_closed_before_the_session_are_visible(self):
        """The estimate is walk-forward: a trade that closes later cannot inform today."""
        state = SimpleNamespace(closed_trades=[
            {'return_pct': 0.10, 'exit_date': date(2024, 3, 1)},
            {'return_pct': -0.05, 'exit_date': date(2024, 3, 15)},
            {'return_pct': 0.20, 'exit_date': date(2024, 6, 1)},
            {'return_pct': 0.30},                   # undated: never counts
        ])
        self.assertEqual(
            backtest_engine._closed_returns_before(state, date(2024, 3, 1)), [0.10],
        )
        self.assertEqual(
            backtest_engine._closed_returns_before(state, date(2024, 3, 15)), [0.10, -0.05],
        )
        self.assertEqual(
            backtest_engine._closed_returns_before(state, date(2024, 6, 1)), [0.10, -0.05, 0.20],
        )

    def test_an_exposure_scale_moves_the_residual_into_the_defensive_asset(self):
        scored = pd.DataFrame([
            {'ts_code': '515020.SH', 'score': 0.71},
            {'ts_code': '512820.SH', 'score': 0.70},
            {'ts_code': '512700.SH', 'score': 0.69},
        ])
        snapshots = scored.copy()
        snapshots['name'] = ['A', 'B', 'C']
        snapshots['category'] = ['bank'] * 3
        snapshots['mom_60d'] = [0.3, 0.2, 0.1]
        config = rotation_engine.RotationConfig(
            max_positions=3, momentum_lookback=60, min_positions_for_risk_on=3,
            absolute_momentum_filter=False, weighting='equal',
            # Neutral, so this test isolates the governor. Left at their defaults the two
            # selection constraints would collapse the three same-category names to two, and
            # the breadth rule would then put a third of the book in the sleeve for a reason
            # that has nothing to do with ``exposure_scale``.
            dedup_track_index=False, max_per_category=0,
        )
        full = rotation_engine.build_rotation_plan(scored, snapshots, config)
        half = rotation_engine.build_rotation_plan(scored, snapshots, config, exposure_scale=0.5)

        self.assertAlmostEqual(full.defensive_allocation, 0.0, places=6)
        self.assertAlmostEqual(sum(leg.weight for leg in full.legs) + full.defensive_allocation, 1.0, places=6)
        self.assertAlmostEqual(sum(leg.weight for leg in half.legs) + half.defensive_allocation, 1.0, places=6)
        self.assertAlmostEqual(half.defensive_allocation, 0.5, places=6)
        self.assertEqual(half.metadata['exposure_scale'], 0.5)
        self.assertIn('exposure_scaled', half.metadata['defensive_reason'])
        self.assertNotIn('exposure_scale', full.metadata)


    def test_the_constraints_and_the_breadth_rule_compose(self):
        """Capping a three-name pool to two does not leave 2/3 invested -- the breadth rule agrees.

        This is the arithmetic that made the first draft of the exposure test above fail, and it
        is the behaviour an operator needs to see: ``max_per_category`` shrinks the eligible
        book, ``min_positions_for_risk_on`` then reads the smaller book as weak breadth, and the
        difference sits in the defensive sleeve instead of being doubled onto two names.
        """
        scored = pd.DataFrame([
            {'ts_code': '515020.SH', 'score': 0.71},
            {'ts_code': '512820.SH', 'score': 0.70},
            {'ts_code': '512700.SH', 'score': 0.69},
        ])
        snapshots = scored.copy()
        snapshots['name'] = ['A', 'B', 'C']
        snapshots['category'] = ['bank'] * 3
        snapshots['mom_60d'] = [0.3, 0.2, 0.1]
        config = rotation_engine.RotationConfig(
            max_positions=3, momentum_lookback=60, min_positions_for_risk_on=3,
            absolute_momentum_filter=False, weighting='equal', max_per_category=2,
        )
        plan = rotation_engine.build_rotation_plan(scored, snapshots, config)

        self.assertEqual([leg.ts_code for leg in plan.legs], ['515020.SH', '512820.SH'])
        self.assertEqual(plan.metadata['candidate_count'], 2)
        self.assertEqual(plan.metadata['qualified_count'], 3)
        self.assertEqual(plan.metadata['selection_constraints']['category_cap_dropped_count'], 1)
        self.assertAlmostEqual(plan.defensive_allocation, 1.0 / 3.0, places=6)
        self.assertEqual(plan.metadata['defensive_reason'], 'insufficient_qualifiers')


class PayoffRatioTests(SimpleTestCase):
    """``payoff_ratio`` is the b in Kelly's ``f* = p - (1-p)/b``; a one-sided sample has no b."""

    def test_average_win_over_absolute_average_loss(self):
        self.assertAlmostEqual(finance_metrics.payoff_ratio([0.10, 0.20, -0.05]), 3.0)

    def test_a_sample_without_both_sides_is_not_measurable(self):
        self.assertIsNone(finance_metrics.payoff_ratio([0.10, 0.20]))
        self.assertIsNone(finance_metrics.payoff_ratio([-0.10, -0.20]))
        self.assertIsNone(finance_metrics.payoff_ratio([]))
        self.assertIsNone(finance_metrics.payoff_ratio(None))

    def test_break_even_trades_count_as_neither(self):
        self.assertAlmostEqual(finance_metrics.payoff_ratio([0.10, 0.0, -0.05]), 2.0)


# ---------------------------------------------------------------------------
# Defensive sleeve: it must exist, and it must be told when it cannot be filled
# ---------------------------------------------------------------------------


class CashYieldAccrualTests(SimpleTestCase):
    """A sleeve the liquidity floor could not fill is idle cash, and idle cash now says so.

    ``_accrue_cash_yield`` is called with a hand-built context because the arithmetic is the
    whole contract: one session's interest at the run's annual rate on the notional the floor
    released, credited to cash and to the counter a report reads.
    """

    def _ctx(self, *, rate, principal, cash=100_000.0):
        return SimpleNamespace(
            config=SimpleNamespace(cash_yield_annual=rate),
            state=SimpleNamespace(
                cash=cash, uninvested_defensive_notional_yuan=principal, total_cash_yield_yuan=0.0,
            ),
        )

    def test_the_default_rate_earns_exactly_nothing(self):
        ctx = self._ctx(rate=0.0, principal=1_000_000.0)
        self.assertEqual(backtest_engine._accrue_cash_yield(ctx, POOL_DATE), 0.0)
        self.assertEqual(ctx.state.cash, 100_000.0)
        self.assertEqual(ctx.state.total_cash_yield_yuan, 0.0)

    def test_a_session_of_interest_is_the_annual_rate_over_252(self):
        ctx = self._ctx(rate=0.02, principal=1_000_000.0)
        interest = backtest_engine._accrue_cash_yield(ctx, POOL_DATE)
        self.assertAlmostEqual(interest, 1_000_000.0 * 0.02 / 252.0, places=9)
        self.assertAlmostEqual(ctx.state.cash, 100_000.0 + interest, places=9)
        self.assertAlmostEqual(ctx.state.total_cash_yield_yuan, interest, places=9)

    def test_no_uninvested_sleeve_means_no_interest(self):
        ctx = self._ctx(rate=0.02, principal=0.0)
        self.assertEqual(backtest_engine._accrue_cash_yield(ctx, POOL_DATE), 0.0)

    def test_a_negative_rate_is_refused_by_validate(self):
        for overrides in ({'cash_yield_annual': -0.01}, {'kelly_fraction': 1.5},
                          {'kelly_floor': 0.8, 'kelly_cap': 0.5}, {'max_per_category': -1},
                          {'weighting': 'inverse_volatility'}):
            with self.subTest(**overrides):
                with self.assertRaises(backtest_engine.BacktestConfigError):
                    _kelly_config(**overrides).validate()


class WeightingChoiceSyncTests(SimpleTestCase):
    """Three modules name the weighting modes; a fourth reading must not invent a fourth mode."""

    def test_the_engine_and_the_selector_agree(self):
        self.assertEqual(backtest_engine.WEIGHTING_MODES, rotation_engine.WEIGHTING_MODES)

    def test_the_model_column_offers_every_mode_the_engine_accepts(self):
        self.assertEqual(
            {choice[0] for choice in ETFRotationBacktest.Weighting.choices},
            set(backtest_engine.WEIGHTING_MODES),
        )


class GridResumeKeyTests(SimpleTestCase):
    """A grid row's name is its resume key, so the naming function decides what re-running means."""

    COMBO = {
        'rebalance_frequency': 'MONTHLY', 'max_positions': 3,
        'absolute_momentum_filter': True, 'momentum_lookback': 20, 'weighting': 'score',
    }

    def test_the_default_name_is_stable_across_processes(self):
        """The 140 rows already in the table were named by this function; a re-run must find them.

        Renaming the stem would not break anything loudly -- it would make every resumed grid
        re-execute all 144 configurations against the same window and persist a second
        indistinguishable generation.
        """
        self.assertEqual(backtest_cmd.grid_name(self.COMBO), 'grid-rule-monthly-p3-mom20-abs-score')
        self.assertEqual(backtest_cmd.grid_name(self.COMBO), backtest_cmd.grid_name(self.COMBO))

    def test_a_second_generation_names_differently_for_the_same_configuration(self):
        """A re-run after the selection rules change must not collide with the old rows.

        Under one prefix the second sweep either skips every combination (they are already
        COMPLETED) or, with ``--grid-force``, overwrites the evidence the before/after
        comparison is built from. A distinct stem keeps both.
        """
        old = backtest_cmd.grid_name(self.COMBO)
        new = backtest_cmd.grid_name(self.COMBO, 'grid-dedup')
        self.assertNotEqual(old, new)
        self.assertTrue(new.endswith(old[len('grid-'):]), new)

    def test_a_blank_or_whitespace_prefix_falls_back_to_the_default_stem(self):
        """A typo in the flag must not produce a leading-dash name the resume key can't match."""
        for prefix in ('', '   ', None):
            self.assertEqual(
                backtest_cmd.grid_name(self.COMBO, prefix), backtest_cmd.grid_name(self.COMBO)
            )

    def test_every_combination_gets_its_own_row_under_any_prefix(self):
        combos = backtest_cmd.grid_combinations()
        for prefix in ('grid', 'grid-dedup'):
            names = [backtest_cmd.grid_name(combo, prefix) for combo in combos]
            self.assertEqual(len(names), len(set(names)), f'{prefix} produced duplicate row names')


class WalkForwardRuleTests(SimpleTestCase):
    """Phase 4: the RULE selection process is scored only on data each fold never trained on."""

    FIRST = date(2016, 1, 4)

    def _sessions(self, count):
        return [self.FIRST + timedelta(days=offset) for offset in range(count)]

    def _combo(self, frequency, positions, lookback, absolute, weighting):
        return {
            'rebalance_frequency': frequency, 'max_positions': positions,
            'absolute_momentum_filter': absolute, 'momentum_lookback': lookback,
            'weighting': weighting,
        }

    def test_fold_plan_refuses_to_pad_when_the_panel_is_short(self):
        """A short panel loses folds; it must never gain a boundary no session sits on.

        ``expanding_folds`` clamps a fold's validation end, not its start, so an
        over-requested plan can emit a fold whose window is empty. The wrapper drops the
        requested count until every fold scores at least one session -- 255 dates seat
        four one-session folds, not six, and 250 dates seat none.
        """
        self.assertEqual(backtest_cmd.walk_forward_folds(self._sessions(250), 6), [])
        narrow = self._sessions(255)
        folds = backtest_cmd.walk_forward_folds(narrow, 6)
        self.assertEqual(len(folds), 4)
        self.assertTrue(all(fold.valid_start <= fold.valid_end for fold in folds))
        for fold in folds:
            self.assertIn(fold.valid_start, narrow)
            self.assertIn(fold.valid_end, narrow)
            self.assertEqual(fold.train_start, narrow[0])
            self.assertLess(fold.train_end, fold.valid_start)

    def test_a_fold_never_sees_data_after_its_training_window(self):
        """The selection grid's window ends at ``train_end``; validation starts after it."""
        sessions = self._sessions(600)
        folds = backtest_cmd.walk_forward_folds(sessions, 2)
        self.assertEqual(len(folds), 2)
        combos = [
            self._combo('MONTHLY', 3, 20, True, 'score'),
            self._combo('WEEKLY', 5, 60, False, 'equal'),
        ]
        calls = []

        def fake_run(**kwargs):
            calls.append(kwargs)
            return {
                'metrics': {'sharpe_ratio': 0.5, 'turnover_annual': 2.0, 'total_return': 0.1,
                            'annualized_return': 0.05, 'max_drawdown': 0.02,
                            'alpha_vs_benchmark': 0.01},
                'report': {'counters': {'rebalances': 8}},
            }

        inner = MagicMock()
        inner.sessions.return_value = sessions
        with patch.object(backtest_cmd, 'DatabaseDataSource', return_value=inner), \
                patch.object(backtest_cmd, 'run_rotation_backtest', side_effect=fake_run), \
                patch.object(backtest_cmd, 'grid_combinations', return_value=combos):
            call_command(
                'run_etf_rotation_backtest', '--grid', '--walk-forward-steps', '2',
                '--start', sessions[0].isoformat(), '--end', sessions[-1].isoformat(),
                stdout=io.StringIO(),
            )

        self.assertEqual(len(calls), len(folds) * (len(combos) + 1))
        for fold in folds:
            train_calls = [
                call for call in calls
                if (call['start_date'], call['end_date']) == (fold.train_start, fold.train_end)
            ]
            self.assertEqual(len(train_calls), len(combos))
            valid_calls = [
                call for call in calls
                if (call['start_date'], call['end_date']) == (fold.valid_start, fold.valid_end)
            ]
            self.assertEqual(len(valid_calls), 1)
            # The declared invariant behind the window choice: no fold's selection run
            # can reach a bar its own validation block is scored on.
            self.assertLess(fold.train_end, fold.valid_start)

    def test_the_winner_is_chosen_by_the_declared_criterion(self):
        """Highest Sharpe wins; a dead heat goes to the lower turnover, never to row order."""
        def row(name, sharpe, turnover, frequency='MONTHLY'):
            return {
                **self._combo(frequency, 3, 20, True, 'score'), 'name': name,
                'metrics': {'sharpe_ratio': sharpe, 'turnover_annual': turnover},
            }

        rows = [
            row('rule-a-high-turnover', 0.9, 30.0),
            row('rule-b-low-turnover', 0.9, 10.0),
            row('rule-c-lower-sharpe', 0.8, 1.0, 'WEEKLY'),
        ]
        self.assertEqual(
            backtest_cmd.select_walk_forward_winner(rows)['name'], 'rule-b-low-turnover'
        )
        rows.append(row('rule-d-unmeasured', float('nan'), 0.1, 'BIWEEKLY'))
        self.assertEqual(
            backtest_cmd.select_walk_forward_winner(rows)['name'], 'rule-b-low-turnover'
        )
        with self.assertRaises(ValueError):
            backtest_cmd.select_walk_forward_winner([row('rule-e', None, 1.0)])

    def test_the_fold_table_reports_its_own_rebalance_counts(self):
        """Each fold's line carries its own trade count -- the reading floor the plan sets."""
        def outcome(index, rebalances):
            return {
                'fold': {
                    'fold_index': index, 'train_start': '2016-01-29', 'train_end': '2018-01-02',
                    'valid_start': '2018-01-03', 'valid_end': '2019-01-02',
                },
                'winner': backtest_cmd.REFERENCE_RULE_COMBO,
                'winner_name': backtest_cmd.grid_stem(backtest_cmd.REFERENCE_RULE_COMBO),
                'train_metrics': {'sharpe_ratio': 0.5}, 'train_rebalances': rebalances + 100,
                'valid_metrics': {
                    'total_return': 0.1, 'annualized_return': 0.05, 'max_drawdown': 0.02,
                    'sharpe_ratio': 0.4, 'alpha_vs_benchmark': 0.01, 'turnover_annual': 2.0,
                },
                'valid_rebalances': rebalances, 'reference': True,
            }

        lines = backtest_cmd.walk_forward_table([outcome(0, 7), outcome(1, 21)])
        self.assertEqual(len(lines), 3)
        self.assertIn('reb', lines[0].split())
        for line, expected in zip(lines[1:], (7, 21)):
            self.assertEqual(int(line.split()[-2]), expected)
            self.assertEqual(line.split()[-1], 'yes')


class SingleRunKwargForwardingTests(SimpleTestCase):
    """Every flag the single-run path accepts must arrive in a form the engine accepts.

    ``--no-dedup-track-index`` was handed to ``run_rotation_backtest`` as a top-level argument,
    and that function has no such parameter: the command raised ``TypeError`` before it read a
    single bar, so the flag had never worked -- and no test had ever invoked the command with
    the Phase-2 flags, which is why the grid passing them through ``config_overrides`` masked it.
    ``_call`` and ``_report`` are patched because the claim is about the arguments, not about a
    decade of bars; ``defensive_asset_problem`` is patched because it is a DB check.
    """

    def _captured(self, **options):
        captured = {}

        def fake_call(self, run_kwargs, predictors, data_source=None):
            captured.update(run_kwargs)
            return {'metrics': {}, 'parameters': {}, 'report': {}, 'equity_curve': []}

        call_options = {'start': '2016-01-29', 'end': '2023-12-31', 'persist': False}
        call_options.update(options)
        with patch.object(backtest_cmd.Command, '_call', fake_call), \
                patch.object(backtest_cmd.Command, '_report', lambda *a, **k: None), \
                patch.object(backtest_cmd, 'defensive_asset_problem', lambda *a, **k: ''):
            call_command('run_etf_rotation_backtest', **call_options)
        return captured

    def test_the_selection_and_sizing_knobs_reach_config_overrides(self):
        captured = self._captured(
            frequency='BIWEEKLY', max_positions=3, momentum_lookback=120,
            absolute_momentum_filter=False, weighting='invvol', kelly_fraction=0.25,
            dedup_track_index=False, max_per_category=0,
        )
        self.assertEqual(captured['config_overrides'], {
            'weighting': 'invvol', 'kelly_fraction': 0.25,
            'dedup_track_index': False, 'max_per_category': 0,
        })

    def test_no_argument_is_hand_to_the_engine_that_it_cannot_accept(self):
        """The general form of the bug: a knob added without an engine parameter is a crash."""
        captured = self._captured(dedup_track_index=False)
        accepted = set(inspect.signature(backtest_cmd.run_rotation_backtest).parameters)
        self.assertEqual(sorted(set(captured) - accepted), [])

    def test_an_unset_selection_flag_leaves_the_config_default_alone(self):
        """``bool(None)`` is False, and False is not "not asked for".

        The tri-state flags default to ``None`` so that omitting them means "run the production
        configuration". Coercing unconditionally made every plain CLI run a dedup-off run, which
        silently selected a different book than the grid rows of the same name and made CLI and
        grid numbers incomparable. Absent from ``config_overrides`` is the correct encoding.
        """
        captured = self._captured()
        self.assertNotIn('dedup_track_index', captured['config_overrides'])
        self.assertNotIn('max_per_category', captured['config_overrides'])

        asked_off = self._captured(dedup_track_index=False, max_per_category=0)
        self.assertEqual(
            (asked_off['config_overrides']['dedup_track_index'],
             asked_off['config_overrides']['max_per_category']), (False, 0))


class BlindCrossSectionGuardTests(_PanelSeedMixin, TestCase):
    """A scorer that cannot see *any* fund is a feed gap, and must not be written as a signal.

    Reproduces 2026-10-22: the CSI total-return benchmark was a session behind, so
    ``rs_vs_benchmark_60d`` was null across all 488 eligible rows. ``_rule_scores`` excluded
    every one of them -- correct per fund, meaningless as a cross-section -- and
    ``build_rotation_plan`` answered ``no_scored_candidates`` with 100% in the money fund. The
    page would have shown that as today's view of the market.
    """

    def _snapshots(self, **overrides):
        """A RULE-scored frame shaped like ``load_snapshots`` output."""
        frame = pd.DataFrame({
            'ts_code': ['512760.SH', '515980.SH'],
            'mom_60d': [0.11, 0.08],
            'vol_20d': [0.02, 0.018],
            'rs_vs_benchmark_60d': [0.03, 0.01],
        })
        for column, values in overrides.items():
            frame[column] = values
        return frame

    def _scored(self, *, rows=0, excluded=2, **attrs):
        frame = pd.DataFrame({'ts_code': [], 'score': pd.Series(dtype='float64')})
        metadata = {'model_type': 'RULE', 'momentum_column': 'mom_60d',
                    'relative_strength_column': 'rs_vs_benchmark_60d',
                    'excluded_missing_features': excluded}
        metadata.update(attrs)
        frame.attrs['rotation'] = metadata
        return frame if rows == 0 else pd.DataFrame(
            {'ts_code': ['512760.SH'] * rows, 'score': [0.7] * rows},
        )

    def _refusal(self, snapshots, scored, model_type='RULE'):
        return rotation_signals._whole_pool_unscorable(
            snapshots, scored, model_type, POOL_DATE,
        )

    def test_a_column_null_across_the_whole_pool_is_named_in_the_refusal(self):
        snapshots = self._snapshots(rs_vs_benchmark_60d=[None, None])
        message = self._refusal(snapshots, self._scored())
        self.assertIn('rs_vs_benchmark_60d', message)
        self.assertIn('2 eligible rows', message)
        self.assertIn(POOL_DATE.isoformat(), message)

    def test_a_partially_missing_column_is_not_a_refusal(self):
        """One fund with no history is excluded, exactly as before -- the pool is still judged."""
        snapshots = self._snapshots(rs_vs_benchmark_60d=[0.03, None])
        self.assertEqual(self._refusal(snapshots, self._scored(excluded=1)), '')

    def test_the_guard_stays_out_of_the_way_of_every_other_empty_reason(self):
        cases = {
            'nothing in the pool': (self._snapshots().iloc[:0], self._scored()),
            'the scorer produced rows': (self._snapshots(), self._scored(rows=1, excluded=1)),
            'an ML scorer came back empty': (
                self._snapshots(mom_60d=[None, None]),
                self._scored(model_type='LIGHTGBM'),
            ),
            'the cross-section itself was empty': (
                self._snapshots(mom_60d=[None, None]),
                self._scored(empty_cross_section=True),
            ),
            'rows dropped for some other reason': (
                self._snapshots(mom_60d=[None, None]),
                self._scored(excluded=0),
            ),
        }
        for label, (snapshots, scored) in cases.items():
            with self.subTest(label):
                self.assertEqual(self._refusal(snapshots, scored), '')

    def test_a_momentum_column_missing_for_every_fund_is_also_refused(self):
        """The guard names whichever input went dark, not just the benchmark one."""
        snapshots = self._snapshots(mom_60d=[None, None])
        message = self._refusal(snapshots, self._scored())
        self.assertIn('mom_60d', message)

    def test_the_real_pipeline_refuses_and_writes_nothing(self):
        """End to end over seeded snapshots: null the rs column, run the signal, expect a refusal."""
        self.seed()
        self.compute_at_pool_date()
        ETFFeatureSnapshot.objects.filter(date=self.pool_date).update(rs_vs_benchmark_60d=None)
        pool_size = ETFFeatureSnapshot.objects.filter(date=self.pool_date, is_eligible=True).count()
        self.assertGreater(pool_size, 0, 'the seeded pool must be eligible for this to mean anything')

        with self.assertRaises(rotation_signals.SignalGenerationError) as caught:
            rotation_signals.generate_rotation_signal(
                trade_date=self.pool_date, model_type='RULE', dry_run=True,
            )

        self.assertIn('rs_vs_benchmark_60d', str(caught.exception))
        self.assertEqual(ETFFeatureSnapshot.objects.filter(date=self.pool_date).count(), pool_size)
        self.assertEqual(ETFRotationSignal.objects.filter(date=self.pool_date).count(), 0)

    def test_the_same_pool_scores_normally_once_the_benchmark_is_back(self):
        """The refusal is a property of the missing column, not of the seeded funds."""
        self.seed()
        self.compute_at_pool_date()
        summary = rotation_signals.generate_rotation_signal(
            trade_date=self.pool_date, model_type='RULE', dry_run=True,
        )
        self.assertGreater(summary['scored_size'], 0)


class SignalRunLedgerTests(_PanelSeedMixin, TestCase):
    """Every attempt leaves a row, and a refusal still fails the task.

    Before this table existed a refused signal was only a worker log line: the page could not
    distinguish "the model said hold" from "the job never produced an answer". The row is
    written by the command so manual runs and beat runs are equally visible.
    """

    def _refuse_through_the_task(self):
        self.seed()
        self.compute_at_pool_date()
        ETFFeatureSnapshot.objects.filter(date=self.pool_date).update(rs_vs_benchmark_60d=None)
        # One session of benchmark history is missing, the 2026-10-22 shape.
        BenchmarkIndexDaily.objects.filter(trade_date=self.days[-1]).delete()
        with self.assertRaises(CommandError):
            etf_tasks.generate_etf_rotation_signal_daily.run()
        return ETFRotationSignalRun.objects.get()

    def test_a_refused_signal_writes_one_run_row_and_still_fails_the_task(self):
        run = self._refuse_through_the_task()
        self.assertEqual(run.outcome, ETFRotationSignalRun.Outcome.REFUSED)
        self.assertEqual(run.reason_code, 'blind_cross_section')
        self.assertEqual(run.signal_type, ETFRotationSignalRun.SignalType.MONTHLY)
        self.assertEqual(run.date, self.pool_date)
        self.assertEqual(ETFRotationSignal.objects.filter(date=self.pool_date).count(), 0)

    def test_the_run_row_diagnostics_name_the_null_column_and_the_lagging_benchmark(self):
        run = self._refuse_through_the_task()
        eligible = ETFFeatureSnapshot.objects.filter(
            date=self.pool_date, is_eligible=True,
        ).count()
        self.assertEqual(run.diagnostics['null_counts']['rs_vs_benchmark_60d'], eligible)
        self.assertEqual(run.diagnostics['benchmark_code'], 'H00905.CSI')
        self.assertEqual(run.diagnostics['benchmark_lag_sessions'], 1)
        self.assertEqual(str(run.diagnostics['latest_benchmark_date']), str(self.days[-2]))

    def test_a_successful_signal_writes_an_ok_run_row(self):
        self.seed()
        self.compute_at_pool_date()
        call_command(
            'generate_etf_rotation_signal',
            date=self.pool_date.isoformat(),
            verbosity=0,
        )
        run = ETFRotationSignalRun.objects.get()
        self.assertEqual(run.outcome, ETFRotationSignalRun.Outcome.OK)
        self.assertEqual(run.reason_code, '')
        self.assertIsNotNone(run.duration_ms)
        self.assertTrue(str(run.diagnostics['model_version']).startswith('rule-v1'))
        self.assertTrue(ETFRotationSignal.objects.filter(date=self.pool_date).exists())

    def test_a_dry_run_still_writes_nothing(self):
        self.seed()
        self.compute_at_pool_date()
        call_command(
            'generate_etf_rotation_signal',
            date=self.pool_date.isoformat(),
            dry_run=True,
            verbosity=0,
        )
        self.assertEqual(ETFRotationSignalRun.objects.count(), 0)


class FeedHealthTests(TestCase):
    """A lagging benchmark or feature run becomes a FEED_STALE row within one beat cycle."""

    def setUp(self):
        self.days = _session_grid(5, last=POOL_DATE)
        self.fund = _seed_etf('512760.SH', name='半导体ETF')
        _seed_bars(self.fund, self.days, [10.0] * len(self.days))
        _seed_benchmark(self.days[:-1])

    def test_a_benchmark_lag_produces_a_feed_stale_row_within_one_beat_cycle(self):
        ETFFeatureSnapshot.objects.create(
            etf=self.fund, date=self.days[-1], is_eligible=True,
        )
        result = etf_tasks.check_etf_rotation_feed_health.run()
        self.assertEqual(result['outcome'], 'FEED_STALE')
        run = ETFRotationSignalRun.objects.get()
        self.assertEqual(run.date, self.days[-1])
        self.assertEqual(run.signal_type, ETFRotationSignalRun.SignalType.HEALTH)
        self.assertEqual(run.outcome, ETFRotationSignalRun.Outcome.FEED_STALE)
        self.assertEqual(run.reason_code, 'benchmark_lag')
        self.assertIn('H00905.CSI', run.message)
        self.assertEqual(run.diagnostics['benchmark_lag_sessions'], 1)

        # The same beat on the same day must not append a second identical row.
        again = etf_tasks.check_etf_rotation_feed_health.run()
        self.assertFalse(again['created'])
        self.assertEqual(ETFRotationSignalRun.objects.count(), 1)

    def test_a_feature_lag_is_named_when_the_benchmark_is_current(self):
        _seed_benchmark([self.days[-1]])
        ETFFeatureSnapshot.objects.create(
            etf=self.fund, date=self.days[-2], is_eligible=True,
        )
        etf_tasks.check_etf_rotation_feed_health.run()
        run = ETFRotationSignalRun.objects.get()
        self.assertEqual(run.reason_code, 'feature_lag')
        self.assertEqual(run.diagnostics['feature_lag_sessions'], 1)

    def test_a_current_feed_writes_nothing(self):
        _seed_benchmark([self.days[-1]])
        ETFFeatureSnapshot.objects.create(
            etf=self.fund, date=self.days[-1], is_eligible=True,
        )
        result = etf_tasks.check_etf_rotation_feed_health.run()
        self.assertEqual(result['outcome'], 'OK')
        self.assertEqual(ETFRotationSignalRun.objects.count(), 0)

    def test_an_absent_benchmark_is_stale_not_ok(self):
        # No rows is not lag zero: a feed that never synced must not pass the check.
        BenchmarkIndexDaily.objects.all().delete()
        result = etf_tasks.check_etf_rotation_feed_health.run()
        self.assertEqual(result['outcome'], 'FEED_STALE')
        run = ETFRotationSignalRun.objects.get()
        self.assertEqual(run.reason_code, 'benchmark_missing')
        self.assertTrue(run.diagnostics['benchmark_missing'])

    def test_an_absent_feature_snapshot_is_stale_not_ok(self):
        _seed_benchmark([self.days[-1]])
        result = etf_tasks.check_etf_rotation_feed_health.run()
        self.assertEqual(result['outcome'], 'FEED_STALE')
        run = ETFRotationSignalRun.objects.get()
        self.assertEqual(run.reason_code, 'feature_missing')
        self.assertTrue(run.diagnostics['feature_missing'])


# ---------------------------------------------------------------------------
# Defensive sleeve and signal transparency: API surface
# ---------------------------------------------------------------------------


class DefensiveSleeveValidationTests(TestCase):
    """The sleeve is a position like any other, so it is checked against real market data.

    ``BacktestConfig.validate()`` stays DB-free on purpose -- it runs inside unit tests and
    against injected fake data sources where ``ETFUniverse`` is empty by construction, and a
    config method that needed the database would make every parameter-hygiene test depend on
    a money-market row. These are the two boundaries that can start a run against real data:
    the public create endpoint and the management command.
    """

    URL = '/api/v1/etf/backtests/'
    BARS = _session_grid(40, last=POOL_DATE)

    def setUp(self):
        self.api = APIClient()

    def _payload(self, **overrides):
        payload = {
            'name': 'sleeve-test',
            'start_date': self.BARS[20].isoformat(),
            'end_date': POOL_DATE.isoformat(),
            'rebalance_frequency': 'MONTHLY',
            'max_positions': 3,
            'momentum_lookback': 20,
            'weighting': 'score',
        }
        payload.update(overrides)
        return {key: value for key, value in payload.items() if value is not None}

    def _sleeve_with_bars(self, ts_code='511880.SH'):
        fund = _seed_etf(ts_code, name='货币ETF', category=ETFCategory.MONEY)
        _seed_bars(fund, self.BARS, [1.0] * len(self.BARS))
        return fund

    def test_create_rejects_a_code_that_is_not_a_fund_we_can_trade(self):
        response = self.api.post(
            self.URL, self._payload(defensive_asset='999999.SH'), format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('999999.SH', str(response.data['defensive_asset']))
        self.assertEqual(ETFRotationBacktest.objects.count(), 0)

    def test_create_rejects_a_fund_that_stopped_trading_before_the_window(self):
        fund = _seed_etf('511999.SH', name='已退市货币ETF', category=ETFCategory.MONEY)
        _seed_bars(fund, _session_grid(10, last=POOL_DATE - timedelta(days=400)), [1.0] * 10)
        response = self.api.post(
            self.URL, self._payload(defensive_asset=fund.ts_code), format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('stopped trading', str(response.data['defensive_asset']))

    def test_create_accepts_a_sleeve_that_trades_inside_the_window(self):
        fund = self._sleeve_with_bars()
        with patch('apps.etf_rotation.tasks.run_etf_rotation_backtest.delay',
                   return_value=SimpleNamespace(id='task-1')):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.api.post(
                    self.URL, self._payload(defensive_asset=fund.ts_code), format='json',
                )
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(ETFRotationBacktest.objects.get().defensive_asset, fund.ts_code)

    def test_the_command_refuses_a_dead_sleeve_before_it_creates_a_row(self):
        with self.assertRaises(CommandError) as caught:
            call_command(
                'run_etf_rotation_backtest',
                start=self.BARS[20].isoformat(),
                end=POOL_DATE.isoformat(),
                defensive_asset='999999.SH',
            )
        self.assertIn('999999.SH', str(caught.exception))
        self.assertEqual(ETFRotationBacktest.objects.count(), 0)


class DefensiveAssetListEndpointTests(TestCase):
    """The workbench dropdown reads its liquidity numbers off the ranker's own cross-section."""

    def setUp(self):
        self.api = APIClient()

    def _money_with_turnover(self, ts_code, name, turnover):
        fund = _seed_etf(ts_code, name=name, category=ETFCategory.MONEY)
        ETFFeatureSnapshot.objects.create(
            etf=fund, date=POOL_DATE, is_eligible=True, avg_amount_20d=turnover,
        )
        return fund

    def test_money_funds_are_listed_richest_first_beside_the_floor(self):
        rich = self._money_with_turnover('511880.SH', '银华货币ETF', 27_583_000_000.0)
        thin = self._money_with_turnover('511670.SH', '华泰紫金货币ETF', 1_000_000.0)
        unmeasured = _seed_etf('511830.SH', name='无快照货币ETF', category=ETFCategory.MONEY)
        _seed_etf('511900.SH', name='不可排名货币ETF', category=ETFCategory.MONEY, is_rankable=False)
        equity = _seed_etf('510300.SH', name='沪深300ETF')
        ETFFeatureSnapshot.objects.create(
            etf=equity, date=POOL_DATE, is_eligible=True, avg_amount_20d=9_000_000_000.0,
        )

        response = self.api.get('/api/v1/etf/defensive_assets/')
        self.assertEqual(response.status_code, 200)
        rows = response.data['results']
        self.assertEqual(
            [row['ts_code'] for row in rows], [rich.ts_code, thin.ts_code, unmeasured.ts_code],
        )
        self.assertEqual(set(rows[0]), {'ts_code', 'name', 'avg_amount_20d', 'eligible',
                                        'min_avg_amount_yuan'})
        self.assertTrue(rows[0]['eligible'])
        self.assertEqual(rows[0]['avg_amount_20d'], 27_583_000_000.0)
        self.assertFalse(rows[2]['eligible'])
        self.assertIsNone(rows[2]['avg_amount_20d'])
        self.assertEqual(rows[0]['min_avg_amount_yuan'], float(MIN_AVG_AMOUNT_YUAN_DEFAULT))

    def test_the_route_is_the_short_one_the_client_contract_promises(self):
        """``etf/universe/defensive_assets/`` is a router action path; the page asks for the short one."""
        self.assertEqual(
            reverse('etf-defensive-assets'), '/api/v1/etf/defensive_assets/',
        )


class RuleModelVersionTests(SimpleTestCase):
    """RULE has no artifact, so its version is a digest of the knobs that change the book."""

    def test_the_tag_names_lookback_weighting_and_both_selection_constraints(self):
        tight = rotation_engine.RotationConfig(
            momentum_lookback=60, weighting='score', dedup_track_index=True, max_per_category=2,
        )
        self.assertEqual(rotation_signals.rule_model_version(tight), 'rule-v1:mom60-score:dedup:cap2')
        loose = rotation_engine.RotationConfig(
            momentum_lookback=20, weighting='invvol', dedup_track_index=False, max_per_category=0,
        )
        self.assertEqual(rotation_signals.rule_model_version(loose), 'rule-v1:mom20-invvol:nodedup:cap0')
        self.assertEqual(rotation_signals.rule_model_version(None), 'rule-v1')

    def test_a_rule_signal_resolves_its_version_without_touching_the_registry(self):
        config = rotation_engine.RotationConfig(momentum_lookback=60, weighting='equal')
        self.assertEqual(
            rotation_signals.resolve_model_version('RULE', config),
            ('rule-v1:mom60-equal:dedup:cap2', {}),
        )


class SignalFreshnessEndpointTests(TestCase):
    """The page labels ``date`` an as-of date; this is the one GET that proves or refutes it."""

    def setUp(self):
        self.api = APIClient()
        self.days = _session_grid(6, last=POOL_DATE)
        self.fund = _seed_etf('512760.SH', name='半导体ETF')
        _seed_bars(self.fund, self.days, [10.0] * len(self.days))
        ETFFeatureSnapshot.objects.create(etf=self.fund, date=self.days[-1], is_eligible=True)

    def _signal(self, day):
        ETFRotationSignal.objects.create(
            date=day,
            signal_type=ETFRotationSignal.SignalType.MONTHLY,
            model_type=ETFRotationSignal.ModelType.RULE,
            model_version='rule-v1:mom60-score:dedup:cap2',
            rankings=[], selected_etfs=[], metadata={},
        )

    def _get(self):
        response = self.api.get('/api/v1/etf/signals/freshness/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.data),
            {'signal_date', 'latest_snapshot_date', 'latest_bar_date', 'lag_sessions',
             'is_stale', 'model_version', 'last_attempt', 'feed_stale'},
        )
        return response.data

    def test_a_signal_two_sessions_behind_the_newest_snapshot_is_reported_stale(self):
        """The 2026-10-22 case: a current cross-section, a signal nobody regenerated."""
        self._signal(self.days[-3])
        data = self._get()
        self.assertEqual(str(data['signal_date']), str(self.days[-3]))
        self.assertEqual(str(data['latest_snapshot_date']), str(self.days[-1]))
        self.assertEqual(str(data['latest_bar_date']), str(self.days[-1]))
        self.assertEqual(data['lag_sessions'], 2)
        self.assertTrue(data['is_stale'])
        self.assertEqual(data['model_version'], 'rule-v1:mom60-score:dedup:cap2')

    def test_a_signal_on_the_newest_snapshot_is_current(self):
        self._signal(self.days[-1])
        data = self._get()
        self.assertEqual(data['lag_sessions'], 0)
        self.assertFalse(data['is_stale'])

    def test_no_signal_row_answers_instead_of_failing(self):
        """An empty table is the first-run state, not an error the page cannot render."""
        data = self._get()
        self.assertIsNone(data['signal_date'])
        self.assertFalse(data['is_stale'])
        self.assertIsNone(data['last_attempt'])
        self.assertIsNone(data['feed_stale'])

    def test_the_last_attempt_is_reported_even_when_it_refused(self):
        self._signal(self.days[-1])
        ETFRotationSignalRun.objects.create(
            date=self.days[-1],
            signal_type=ETFRotationSignalRun.SignalType.MONTHLY,
            outcome=ETFRotationSignalRun.Outcome.REFUSED,
            reason_code='blind_cross_section',
            message='nothing could be scored',
        )
        data = self._get()
        self.assertEqual(data['last_attempt']['outcome'], 'REFUSED')
        self.assertEqual(data['last_attempt']['reason_code'], 'blind_cross_section')
        self.assertEqual(str(data['last_attempt']['date']), str(self.days[-1]))

    def test_a_feed_defect_on_the_newest_bar_is_reported_separately_from_signal_lag(self):
        self._signal(self.days[-1])
        ETFRotationSignalRun.objects.create(
            date=self.days[-1],
            signal_type=ETFRotationSignalRun.SignalType.HEALTH,
            outcome=ETFRotationSignalRun.Outcome.FEED_STALE,
            reason_code='benchmark_lag',
            message='benchmark is one session behind',
        )
        data = self._get()
        self.assertEqual(data['lag_sessions'], 0)
        self.assertFalse(data['is_stale'])
        self.assertEqual(data['feed_stale']['reason_code'], 'benchmark_lag')
        # The watchdog is not a signal attempt, so it never becomes ``last_attempt``.
        self.assertIsNone(data['last_attempt'])

    def test_a_stale_row_for_an_older_bar_clears_once_bars_advance(self):
        self._signal(self.days[-1])
        ETFRotationSignalRun.objects.create(
            date=self.days[-2],
            signal_type=ETFRotationSignalRun.SignalType.HEALTH,
            outcome=ETFRotationSignalRun.Outcome.FEED_STALE,
            reason_code='benchmark_lag',
            message='yesterday was stale',
        )
        self.assertIsNone(self._get()['feed_stale'])

