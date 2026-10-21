"""Point-in-time backtest engine for the ETF momentum-rotation module.

Two decisions are load-bearing, and both are the reason this engine exists separately
from ``apps.backtest.tasks`` rather than as a configuration of it.

**ETF trades carry no stamp duty.** The A-share schedule in
``apps/backtest.tasks._resolve_fee_config`` charges 0.5 permille stamp duty on **sells
only**, plus exchange handling, regulatory and transfer fees. Exchange-traded funds are
exempt from stamp duty entirely, so reusing that schedule would tax every rotation exit
at ~0.05% of notional that is never actually paid. Over a monthly-rebalanced 5-position
portfolio that is a systematic, compounding overstatement of cost which shows up
directly as understated alpha. The model here is therefore:
``commission = max(notional * commission_rate, min_commission)`` on **both** sides and
nothing else, with ``notional`` measured on the slipped fill price (the amount the
broker is paid on). ``min_commission`` is kept because it is what makes small rebalance
deltas economically pointless -- see ``min_trade_notional_fraction``.

**A signal from close D executes at close D+1.** Snapshots are produced by a nightly
batch, so the ranking for session D does not exist until after D's close, and the
earliest honest fill is the next session. Executing at D's own close would be silent
look-ahead. Filling at the *close* of D+1 (rather than its open) is what makes the
ledger exactly value-conserving: the fill is marked at the same price it executed at,
so the only equity change on a fill date is fees plus slippage, and that identity is
asserted in the engine's own test seam instead of being argued in prose.

Everything else in here is bookkeeping around those two facts, plus the three
conventions that decide whether a result is believable:

* **Total return, not price return.** Dividends are reconstructed from the ``fund_adj``
  factor change on the ex-date (``dividend_per_share = close * (factor_t / factor_{t-1} - 1)``)
  and credited to cash. The defensive sleeve is a bond/money ETF that distributes, so a
  price-return-only engine would understate it by roughly its yield, every year.
* **Units.** ``ETFDaily.amount`` is in 元 and ``ETFDaily.volume`` is in 份 (converted at
  ingest by ``apps.etf_rotation.tasks``). Liquidity is tested 元-against-元 and notional
  is ``shares(份) * price(元/份)``. Mixing those is the classic way this module lies.
* **No survivorship bias.** The pool on date D is that date's
  ``ETFFeatureSnapshot(is_eligible=True)`` rows, never today's ``is_active`` list, and the
  frame handed to the ranker excludes every forward-looking label column.

Ranking and selection are delegated to ``apps.etf_rotation.rotation_engine`` via a lazy
import; this module never re-implements them, because a hand-rolled fallback would make
the backtest and the live signal disagree, which is worse than a failed run.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import math
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable

import numpy as np
import pandas as pd

from django.conf import settings

from apps.core import finance_metrics


logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = finance_metrics.TRADING_DAYS_PER_YEAR

MODEL_TYPES = ('RULE', 'LIGHTGBM', 'LSTM', 'ENSEMBLE')
REBALANCE_FREQUENCIES = ('WEEKLY', 'BIWEEKLY', 'MONTHLY', 'TRIGGER')

#: Sessions of turnover history behind the signal date used for the liquidity screen.
LIQUIDITY_LOOKBACK_SESSIONS = 20
#: Calendar days of session calendar pulled before ``start_date``, enough to have
#: ``LIQUIDITY_LOOKBACK_SESSIONS`` bars available on the very first signal date.
CALENDAR_WARMUP_CALENDAR_DAYS = 180
#: Board lot for on-exchange ETF purchases: SSE/SZSE buy orders must be whole multiples
#: of 100 份. Sells are not rounded -- an odd residual is liquidated in one order.
DEFAULT_LOT_SIZE = 100
#: T+1 is satisfied by construction (fills are one full session after the signal and a
#: code is only ever bought on a session it was not sold on), so it is asserted here
#: rather than modelled as a constraint.
MIN_FACTOR_RATIO_DRIFT = 1e-6
#: Price panels are cached per ts_code for the whole run window; the bound keeps peak
#: memory flat on a TRIGGER run that touches every eligible fund.
PRICE_CACHE_MAX_CODES = 512

#: ``ETFFeatureSnapshot`` columns that may be handed to the ranker. Enumerated rather
#: than derived from ``_meta.fields`` because the snapshot table also stores
#: ``fwd_return`` / ``label_rank_pct`` / ``label_top_bucket`` / ``label_horizon_days``,
#: which look forward -- a single leaked label column would silently produce a backtest
#: that cannot be reproduced by the live signal.
PIT_SNAPSHOT_FEATURE_COLUMNS = (
    'mom_5d', 'mom_10d', 'mom_20d', 'mom_60d', 'mom_120d', 'mom_250d', 'mom_accel',
    'vol_20d', 'vol_60d', 'sharpe_20d', 'sharpe_60d',
    'rs_vs_benchmark_20d', 'rs_vs_benchmark_60d', 'corr_with_benchmark_60d',
    'rank_pct_20d', 'rank_pct_60d',
    'rsi_14', 'ma5_ratio', 'ma20_ratio', 'ma60_ratio', 'bollinger_pos',
    'drawdown_from_high_60d', 'avg_amount_20d', 'volume_ratio_5d_20d',
)
#: Mirror of the label block, excluded from every ranker input.
LABEL_COLUMNS = ('fwd_return', 'label_rank_pct', 'label_top_bucket', 'label_horizon_days')


class RotationEngineUnavailable(RuntimeError):
    """``apps.etf_rotation.rotation_engine`` is absent or does not match its contract.

    Raised instead of falling back to a local ranking: a backtest that disagrees with
    the live signal is worse than no backtest.
    """


class BacktestDataUnavailable(RuntimeError):
    """A required point-in-time input (bars or snapshots) is missing for the window."""


class BacktestConfigError(ValueError):
    """The requested parameter set is not runnable."""


def _setting(name: str, default: Any) -> Any:
    """Read a tunable from settings without importing anything that requires a URLconf."""
    try:
        value = getattr(settings, name, default)
    except Exception:  # pragma: no cover - settings backend failure
        return default
    return default if value is None else value


def _to_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == '':
        return default
    try:
        if isinstance(value, Decimal):
            return float(value)
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _as_date(value: Any) -> date | None:
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _iso(value: Any) -> str | None:
    day = _as_date(value)
    return day.isoformat() if day else None


def _normalize_sessions(values: Any) -> list[date]:
    """Coerce whatever a data source handed back into a clean, ordered date list.

    Sources may return ``datetime.date``, ``datetime.datetime`` or ISO strings; the ledger
    indexes price frames by ``date`` objects, so everything is normalised once here. A
    generator expression is deliberately not used inside a comprehension filter -- the
    filter variable would bind to the enclosing scope, not the iterator.
    """
    days: list[date] = []
    for item in values or []:
        day = _as_date(item)
        if day is not None:
            days.append(day)
    return sorted(set(days))


@dataclass
class BacktestConfig:
    """Every knob of one run, mirroring the ``ETFRotationBacktest`` columns.

    Defaults resolve through ``getattr(settings, ...)`` at construction time so a
    deployer's override is picked up without this module knowing which settings exist.
    """

    start_date: date
    end_date: date
    model_type: str = 'RULE'
    rebalance_frequency: str = 'MONTHLY'
    max_positions: int = 5
    momentum_lookback: int = 60
    absolute_momentum_filter: bool = True
    defensive_asset: str = field(default_factory=lambda: str(_setting('ETF_DEFENSIVE_ASSET', '511880.SH')))
    initial_capital: float = field(default_factory=lambda: float(_setting('ETF_INITIAL_CAPITAL', 480000.0)))
    commission_rate: float = field(default_factory=lambda: float(_setting('ETF_COMMISSION_RATE', 0.0003)))
    min_commission: float = field(default_factory=lambda: float(_setting('ETF_MIN_COMMISSION', 5.0)))
    slippage: float = field(default_factory=lambda: float(_setting('ETF_SLIPPAGE', 0.001)))
    liquidity_floor_multiple: float = field(
        default_factory=lambda: float(_setting('ETF_LIQUIDITY_FLOOR_MULTIPLE', 10.0)),
    )
    benchmark_code: str = field(default_factory=lambda: str(_setting('ETF_BENCHMARK_CODE', 'H00905.CSI')))

    # Selection parameters the rotation engine owns; forwarded verbatim into
    # ``RotationConfig`` so a backtest and a live run of the same date agree.
    weighting: str = 'equal'
    rank_change_threshold: int = 2
    max_swaps: int = 2
    min_positions_for_risk_on: int = 3

    lot_size: int = DEFAULT_LOT_SIZE
    liquidity_lookback_sessions: int = LIQUIDITY_LOOKBACK_SESSIONS
    # Rebalance deltas smaller than this share of equity are skipped: paying a 5 yuan
    # minimum commission to move 0.1% of the portfolio is noise that only makes the
    # result look worse than it is.
    min_trade_notional_fraction: float = 0.005
    chunk_trading_days: int = field(
        default_factory=lambda: int(_setting('ETF_ROTATION_BACKTEST_CHUNK_TRADING_DAYS', 120)),
    )
    walk_forward_steps: int = 0
    compute_proxy_benchmark: bool = True
    secondary_benchmark_codes: tuple[str, ...] = ('000905.SH',)
    label_horizon_days: int | None = field(
        default_factory=lambda: (
            None
            if _setting('ETF_LABEL_HORIZON_DAYS', None) is None
            else int(_setting('ETF_LABEL_HORIZON_DAYS', 0))
        ),
    )

    def __post_init__(self) -> None:
        self.start_date = _as_date(self.start_date)  # type: ignore[assignment]
        self.end_date = _as_date(self.end_date)  # type: ignore[assignment]
        self.model_type = str(self.model_type or 'RULE').upper()
        self.rebalance_frequency = str(self.rebalance_frequency or 'MONTHLY').upper()

    def validate(self) -> None:
        if not self.start_date or not self.end_date:
            raise BacktestConfigError('start_date and end_date are required.')
        if self.end_date < self.start_date:
            raise BacktestConfigError('end_date must be on or after start_date.')
        if self.model_type not in MODEL_TYPES:
            raise BacktestConfigError(f'Unsupported model_type {self.model_type!r}; expected one of {MODEL_TYPES}.')
        if self.rebalance_frequency not in REBALANCE_FREQUENCIES:
            raise BacktestConfigError(
                f'Unsupported rebalance_frequency {self.rebalance_frequency!r}; expected one of {REBALANCE_FREQUENCIES}.'
            )
        if self.initial_capital <= 0:
            raise BacktestConfigError('initial_capital must be positive.')
        if self.commission_rate < 0 or self.slippage < 0 or self.min_commission < 0:
            raise BacktestConfigError('commission_rate, min_commission and slippage must be non-negative.')
        if self.liquidity_floor_multiple < 0:
            raise BacktestConfigError('liquidity_floor_multiple must be non-negative.')
        if self.max_positions < 1:
            raise BacktestConfigError('max_positions must be at least 1.')
        if self.momentum_lookback < 1:
            raise BacktestConfigError('momentum_lookback must be at least 1.')
        if self.chunk_trading_days < 1:
            raise BacktestConfigError('chunk_trading_days must be at least 1.')

    def to_parameters(self) -> dict[str, Any]:
        """JSON-safe parameter record persisted on ``ETFRotationBacktest.parameters``."""
        payload = asdict(self)
        payload['start_date'] = self.start_date.isoformat()
        payload['end_date'] = self.end_date.isoformat()
        payload['secondary_benchmark_codes'] = list(self.secondary_benchmark_codes)
        payload['fee_model'] = fee_model_summary(self)
        payload['pit_snapshot_feature_columns'] = list(PIT_SNAPSHOT_FEATURE_COLUMNS)
        payload['excluded_label_columns'] = list(LABEL_COLUMNS)
        return payload

    def fingerprint(self) -> str:
        """Identity of the parameter set, so a resumed run cannot silently mix states."""
        digest = hashlib.sha256(json.dumps(self.to_parameters(), sort_keys=True, default=str).encode())
        return digest.hexdigest()[:16]


def fee_model_summary(config: BacktestConfig) -> dict[str, Any]:
    """Human-readable statement of the fee model, stored with every run.

    Persisted because the single most confusing thing about a cross-module backtest
    comparison is silently different cost assumptions.
    """
    return {
        'mode': 'cn_etf_two_way_commission',
        'commission_rate': float(config.commission_rate),
        'min_commission': float(config.min_commission),
        'applies_to': 'both sides',
        'stamp_duty': 0.0,
        'stamp_duty_note': 'Exchange-traded funds are exempt from CN stamp duty; the A-share sell-side 0.5permille levy is deliberately not applied.',
        'exchange_handling_fee': 0.0,
        'regulatory_fee': 0.0,
        'transfer_fee': 0.0,
        'slippage': float(config.slippage),
        'slippage_applied_to': 'execution price: buys *(1+slippage), sells *(1-slippage)',
        'notional_basis': 'filled notional = shares x slipped execution price',
    }


def etf_commission(notional: float, config: BacktestConfig) -> float:
    """Two-way broker commission on one fill: ``max(notional * rate, min_commission)``.

    Structured after ``apps.backtest.tasks._trade_fee_breakdown`` but with the
    sell-side stamp duty, exchange handling, regulatory and transfer components dropped:
    none of them apply to an on-exchange ETF.
    """
    if notional <= 0:
        return 0.0
    return max(notional * float(config.commission_rate), float(config.min_commission))


def etf_fee_breakdown(notional: float, config: BacktestConfig, *, side: str) -> dict[str, float]:
    """Component breakdown for one fill, mirroring the source engine's payload shape."""
    commission = etf_commission(notional, config)
    return {
        'commission': commission,
        'stamp_duty': 0.0,
        'exchange_fee': 0.0,
        'regulatory_fee': 0.0,
        'transfer_fee': 0.0,
        'total_fee': commission,
        'side': str(side).upper(),
    }


# ---------------------------------------------------------------------------
# Data seam
# ---------------------------------------------------------------------------


@dataclass
class PricePanel:
    """Daily bars for a set of ts_codes, wide-format, indexed by session date."""

    close: pd.DataFrame
    adj_close: pd.DataFrame
    amount: pd.DataFrame
    volume: pd.DataFrame

    @property
    def adj_factor(self) -> pd.DataFrame:
        with pd.option_context('future.no_silent_downcasting', True):
            factor = self.adj_close.div(self.close.where(self.close > 0))
        return factor.replace([float('inf'), -float('inf')], pd.NA).astype('float64')


class DatabaseDataSource:
    """Default market-data seam: read-only point-in-time access to the ETF tables.

    Every method is a pure query with no strategy logic, which is what lets the engine
    be driven end-to-end from hand-built frames in a test with no database at all. The
    interface is documented in :meth:`sessions`, :meth:`snapshots`, :meth:`prices`,
    :meth:`benchmark_close`.
    """

    def __init__(self) -> None:
        self._snapshot_cache: OrderedDict[date, pd.DataFrame] = OrderedDict()

    def sessions(self, start_date: date, end_date: date) -> list[date]:
        """Open sessions, defined as the set of dates that actually have ETF bars."""
        from apps.etf_rotation.models import ETFDaily

        return list(
            ETFDaily.objects.filter(date__gte=start_date, date__lte=end_date)
            .values_list('date', flat=True)
            .distinct()
            .order_by('date')
        )

    def snapshots(self, session: date) -> pd.DataFrame:
        """Point-in-time eligible feature rows for one session, labels excluded.

        ``is_eligible`` is the feature job's point-in-time pool verdict; it is never
        re-derived from ``ETFUniverse.is_active`` here. ``etf__is_rankable`` is the
        vehicle-type exclusion recorded at sync time and is applied as a belt-and-braces
        mirror of ``etf_universe.eligible_universe``.
        """
        if session in self._snapshot_cache:
            self._snapshot_cache.move_to_end(session)
            return self._snapshot_cache[session]

        from apps.etf_rotation.models import ETFFeatureSnapshot

        rows = list(
            ETFFeatureSnapshot.objects.filter(date=session, is_eligible=True, etf__is_rankable=True)
            .select_related('etf')
            .values(
                'date',
                'etf__ts_code',
                'etf__name',
                'etf__category',
                'is_eligible',
                *PIT_SNAPSHOT_FEATURE_COLUMNS,
            )
        )
        frame = pd.DataFrame(
            [
                {
                    'date': row['date'],
                    'ts_code': row['etf__ts_code'],
                    'name': row['etf__name'],
                    'category': row['etf__category'],
                    'is_eligible': row['is_eligible'],
                    **{column: _to_float(row.get(column)) for column in PIT_SNAPSHOT_FEATURE_COLUMNS},
                }
                for row in rows
            ]
        )
        self._snapshot_cache[session] = frame
        while len(self._snapshot_cache) > 32:
            self._snapshot_cache.popitem(last=False)
        return frame

    def prices(self, ts_codes: Iterable[str], start_date: date, end_date: date) -> PricePanel:
        """Wide bars for ``ts_codes`` over ``[start_date, end_date]``.

        ``close``/``adj_close`` are 元 per 份, ``amount`` is 元, ``volume`` is 份 --
        the same units ``apps.etf_rotation.tasks`` normalised on ingest.
        """
        codes = sorted({str(code) for code in ts_codes if code})
        empty = PricePanel(
            close=pd.DataFrame(), adj_close=pd.DataFrame(), amount=pd.DataFrame(), volume=pd.DataFrame()
        )
        if not codes:
            return empty

        from apps.etf_rotation.models import ETFDaily

        records = list(
            ETFDaily.objects.filter(
                etf__ts_code__in=codes, date__gte=start_date, date__lte=end_date
            ).values('date', 'etf__ts_code', 'close', 'adj_close', 'amount', 'volume')
        )
        if not records:
            return empty
        frame = pd.DataFrame(records)
        for column in ('close', 'adj_close', 'amount', 'volume'):
            frame[column] = pd.to_numeric(frame[column], errors='coerce')

        def _pivot(column: str) -> pd.DataFrame:
            return frame.pivot_table(index='date', columns='etf__ts_code', values=column, aggfunc='last').sort_index()

        return PricePanel(
            close=_pivot('close'),
            adj_close=_pivot('adj_close'),
            amount=_pivot('amount'),
            volume=_pivot('volume'),
        )

    def benchmark_close(self, index_code: str, start_date: date, end_date: date) -> pd.Series:
        """Official index closes from ``markets.BenchmarkIndexDaily`` (``trade_date``/``close``)."""
        from apps.markets.models import BenchmarkIndexDaily

        rows = list(
            BenchmarkIndexDaily.objects.filter(
                index_code=index_code, trade_date__gte=start_date, trade_date__lte=end_date
            ).values_list('trade_date', 'close')
        )
        if not rows:
            return pd.Series(dtype='float64')
        series = pd.Series(
            {day: _to_float(value) for day, value in rows}, name=index_code, dtype='float64'
        ).sort_index()
        return series


class PriceBook:
    """Per-code bar cache over the whole run window, backed by the data seam.

    Loads all not-yet-cached codes in one batched query per call: a rotation touches
    ``max_positions + 1`` codes per rebalance, so per-code queries would be one round
    trip per leg per month for no benefit.
    """

    def __init__(self, source: Any, window_start: date, window_end: date, sessions: list[date]) -> None:
        self.source = source
        self.window_start = window_start
        self.window_end = window_end
        self.sessions = list(sessions)
        self.session_set = {day for day in self.sessions}
        self._cache: OrderedDict[str, dict[str, pd.Series]] = OrderedDict()
        self.load_calls = 0
        self.loaded_rows = 0

    def ensure(self, codes: Iterable[str]) -> None:
        wanted = sorted({str(code) for code in codes if code})
        missing = [code for code in wanted if code not in self._cache]
        if not missing:
            for code in wanted:
                self._cache.move_to_end(code)
            return
        panel = self.source.prices(missing, self.window_start, self.window_end)
        self.load_calls += 1
        self.loaded_rows += int(panel.close.size)
        for code in missing:
            self._cache[code] = {
                'close': self._column(panel.close, code),
                'adj_close': self._column(panel.adj_close, code),
                'amount': self._column(panel.amount, code),
                'volume': self._column(panel.volume, code),
            }
            self._cache[code] = self._align(code, self._cache[code])
        for code in wanted:
            self._cache.move_to_end(code)
        while len(self._cache) > PRICE_CACHE_MAX_CODES:
            self._cache.popitem(last=False)

    @staticmethod
    def _column(frame: pd.DataFrame, code: str) -> pd.Series:
        if frame.empty or code not in frame.columns:
            return pd.Series(dtype='float64', name=code)
        return frame[code].astype('float64')

    def _align(self, code: str, series: dict[str, pd.Series]) -> dict[str, pd.Series]:
        # Object index of ``datetime.date``: the DB hands back date objects, and keeping
        # one label type end-to-end is what makes ``.get(session)`` lookups exact.
        index = pd.Index(list(self.sessions))
        aligned = {}
        for key, values in series.items():
            if values.empty:
                aligned[key] = pd.Series(float('nan'), index=index, name=code, dtype='float64')
                continue
            reindexed = values.reindex(index)
            aligned[key] = pd.Series(reindexed.to_numpy(dtype='float64'), index=index, name=code)
        with pd.option_context('future.no_silent_downcasting', True):
            factor = (aligned['adj_close'] / aligned['close'].where(aligned['close'] > 0)).astype('float64')
        aligned['adj_factor'] = factor.replace([float('inf'), -float('inf')], float('nan'))
        return aligned

    def raw_close(self, code: str, session: date) -> float | None:
        self.ensure([code])
        entry = self._cache.get(code)
        if not entry:
            return None
        return _usable(entry['close'].get(session))

    def adj_factor(self, code: str, session: date) -> float | None:
        self.ensure([code])
        entry = self._cache.get(code)
        if not entry:
            return None
        return _usable(entry['adj_factor'].get(session))

    def last_close_at_or_before(self, code: str, session: date) -> tuple[float | None, date | None]:
        """Most recent close up to and including ``session`` -- the stale-mark fallback."""
        self.ensure([code])
        entry = self._cache.get(code)
        if not entry:
            return None, None
        window = entry['close'].loc[:session].dropna()
        if window.empty:
            return None, None
        return float(window.iloc[-1]), _as_date(window.index[-1])

    def trailing_average_amount_yuan(self, code: str, as_of: date, lookback: int) -> float | None:
        """Mean daily turnover in 元 over the ``lookback`` sessions ending at ``as_of``.

        Point-in-time on purpose: the actual turnover of the *execution* day is not
        knowable when the order is decided, so screening on it would be look-ahead.
        """
        self.ensure([code])
        entry = self._cache.get(code)
        if not entry:
            return None
        window = entry['amount'].loc[:as_of].dropna()
        if window.empty:
            return None
        window = window.iloc[-max(int(lookback), 1):]
        if len(window) < max(2, min(5, int(lookback))):
            # Too little history to characterise the fund at all; treat as untradeable
            # rather than as infinitely liquid.
            return None
        return float(window.mean())


def _usable(value: Any) -> float | None:
    number = _to_float(value)
    if number is None or number <= 0:
        return None
    return number


# ---------------------------------------------------------------------------
# Rotation-engine contract
# ---------------------------------------------------------------------------


def resolve_rotation_module(rotation_module: Any = None) -> Any:
    """Import (lazily) and validate ``apps.etf_rotation.rotation_engine``.

    Kept lazy so this module can be imported -- and the engine driven from a test seam
    -- without the selection layer existing yet.
    """
    module = rotation_module
    if module is None:
        try:
            module = importlib.import_module('apps.etf_rotation.rotation_engine')
        except ImportError as exc:
            raise RotationEngineUnavailable(
                'apps.etf_rotation.rotation_engine is not importable; the rotation '
                'selection layer must exist before a rotation backtest can run. '
                f'Underlying import error: {exc}'
            ) from exc
    for name in ('RotationConfig', 'score_snapshots', 'build_rotation_plan'):
        member = getattr(module, name, None)
        if member is None or (name != 'RotationConfig' and not callable(member)):
            raise RotationEngineUnavailable(
                f'apps.etf_rotation.rotation_engine does not provide a usable {name!r} '
                f'(found {member!r}); the engine contract requires RotationConfig, '
                'score_snapshots and build_rotation_plan. The backtest refuses to fall back to a '
                'hand-rolled ranking, because that would make the backtest and the live signal '
                'disagree.'
            )
    return module


def _leg_field(leg: Any, name: str, default: Any = None) -> Any:
    if isinstance(leg, dict):
        return leg.get(name, default)
    return getattr(leg, name, default)


def _plan_to_target_weights(module: Any, plan: Any, config: BacktestConfig) -> tuple[dict[str, float], dict[str, Any]]:
    """Flatten a ``RotationPlan`` into ``{ts_code: weight}``, defensive sleeve included.

    Shape-checked loudly: a renamed attribute on the concurrently-developed selection
    layer must fail the run, not silently produce an empty portfolio.
    """
    legs = getattr(plan, 'legs', None)
    if legs is None:
        raise RotationEngineUnavailable(
            'rotation_engine.build_rotation_plan() returned an object without a .legs attribute; '
            'the engine contract expects RotationPlan(.legs[.ts_code, .weight], .defensive_allocation, '
            '.defensive_asset, .rankings).'
        )

    weights: dict[str, float] = {}
    for leg in legs:
        code = _leg_field(leg, 'ts_code')
        weight = _to_float(_leg_field(leg, 'weight'))
        if not code or weight is None:
            raise RotationEngineUnavailable(
                'A rotation leg is missing ts_code or weight; cannot size the portfolio.'
            )
        code = str(code)
        weights[code] = weights.get(code, 0.0) + max(weight, 0.0)

    defensive_asset = str(getattr(plan, 'defensive_asset', '') or '') or str(config.defensive_asset)
    defensive_allocation = _to_float(getattr(plan, 'defensive_allocation', 0.0), 0.0) or 0.0
    if defensive_allocation > 0:
        if defensive_asset in weights:
            # A leg may already be the defensive asset (e.g. a bond ETF ranked into the
            # momentum pool). Merge rather than double-count.
            weights[defensive_asset] = max(weights[defensive_asset], defensive_allocation)
        else:
            weights[defensive_asset] = defensive_allocation

    diagnostics = {
        'rankings': getattr(plan, 'rankings', None) or [],
        'defensive_asset': defensive_asset,
        'defensive_allocation': defensive_allocation,
        'legs': len(list(legs)),
    }
    return weights, diagnostics


# ---------------------------------------------------------------------------
# Session-period helpers
# ---------------------------------------------------------------------------


def _period_key(session: date, frequency: str) -> tuple:
    if frequency == 'MONTHLY':
        return (session.year, session.month)
    if frequency == 'WEEKLY':
        iso = session.isocalendar()
        return (iso[0], iso[1])
    if frequency == 'BIWEEKLY':
        iso = session.isocalendar()
        return (iso[0], iso[1] // 2)
    return (session.isoformat(),)


def signal_sessions(run_sessions: list[date], frequency: str) -> list[date]:
    """Sessions on which a ranking is computed: the LAST session of each period.

    Period-end (not period-start) so the ranking uses the freshest completed bar of the
    period it is rotating out of. ``TRIGGER`` evaluates every session and lets the
    rotation engine's ``rank_change_threshold`` / ``max_swaps`` decide whether anything
    actually trades.
    """
    if frequency == 'TRIGGER':
        return list(run_sessions)
    last_of_period: dict[tuple, date] = {}
    for session in run_sessions:
        last_of_period[_period_key(session, frequency)] = session
    return sorted(last_of_period.values())


# ---------------------------------------------------------------------------
# Engine state
# ---------------------------------------------------------------------------


@dataclass
class PositionLot:
    """One open holding. ``shares`` are whole 份, ``avg_cost`` is 元/份 and fee-loaded."""

    ts_code: str
    name: str = ''
    category: str = ''
    shares: int = 0
    avg_cost: float = 0.0
    cost_notional: float = 0.0
    fees_paid: float = 0.0
    dividend_cash: float = 0.0
    entry_date: date | None = None
    entry_price: float | None = None
    signal_date: date | None = None
    target_weight: float = 0.0
    last_close: float | None = None
    last_close_date: date | None = None
    last_adj_factor: float | None = None

    def market_value(self) -> float:
        if self.last_close is None:
            return self.cost_notional
        return float(self.shares) * float(self.last_close)


@dataclass
class EngineState:
    cash: float
    positions: dict[str, PositionLot] = field(default_factory=dict)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    trade_log: list[dict[str, Any]] = field(default_factory=list)
    closed_trades: list[dict[str, Any]] = field(default_factory=list)
    pending_plan: dict[str, Any] | None = None
    total_commission: float = 0.0
    total_slippage: float = 0.0
    total_dividend_cash: float = 0.0
    rejected_fills: int = 0
    stale_marks: int = 0
    skipped_fills: int = 0
    sub_minimum_deltas: int = 0
    cursor: int = 0
    signals_generated: int = 0
    peak_equity: float | None = None
    turnover_by_session: dict[date, float] = field(default_factory=dict)


class _Context:
    """Everything one session needs, so the loop stays a plain function."""

    def __init__(
        self,
        config: BacktestConfig,
        module: Any,
        book: PriceBook,
        source: Any,
        predictors: dict[str, Any],
        calendar: list[date] | None = None,
        signal_sessions: Iterable[date] | None = None,
    ) -> None:
        self.config = config
        self.module = module
        self.book = book
        self.source = source
        self.predictors = predictors
        self.state = EngineState(cash=float(config.initial_capital))
        self.calendar = list(calendar or [])
        self.signal_sessions = set(signal_sessions or [])
        self.errors: list[str] = []

    @property
    def equity(self) -> float:
        if not self.state.equity_curve:
            return float(self.config.initial_capital)
        return float(self.state.equity_curve[-1]['equity'])


def _round_lots(notional: float, price: float, lot_size: int) -> int:
    if price <= 0 or notional <= 0:
        return 0
    lot = max(int(lot_size), 1)
    return int(math.floor(notional / (price * lot))) * lot


# ---------------------------------------------------------------------------
# Signal generation (delegated ranking, local sizing constraints)
# ---------------------------------------------------------------------------


def _current_weights(ctx: _Context) -> dict[str, float]:
    """Live portfolio weights handed to the rotation engine for swap accounting.

    Cash is deliberately absent: ``RotationConfig.max_swaps`` counts ETF-to-ETF swaps,
    and a cash bucket would read as a position and suppress a rotation the engine
    intends to make.
    """
    equity = ctx.equity
    if equity <= 0:
        return {}
    return {code: lot.market_value() / equity for code, lot in ctx.state.positions.items()}


def _validated_scored_frame(scored: Any) -> pd.DataFrame:
    """Contract gate on ``score_snapshots`` output: a frame with ``ts_code`` and ``score``."""
    if isinstance(scored, pd.DataFrame):
        frame = scored
    elif isinstance(scored, (list, tuple)):
        frame = pd.DataFrame(list(scored))
    else:
        raise RotationEngineUnavailable(
            'rotation_engine.score_snapshots() returned '
            f'{type(scored).__name__}; the contract requires a DataFrame with ts_code and score columns.'
        )
    for column in ('ts_code', 'score'):
        if column not in frame.columns:
            raise RotationEngineUnavailable(
                f'rotation_engine.score_snapshots() output is missing the {column!r} column; '
                'the contract requires DataFrame[ts_code, score].'
            )
    return frame


def _snapshot_frame_for_ranking(ctx: _Context, session: date) -> pd.DataFrame:
    """Point-in-time pool for one session, with every forward-looking column stripped.

    The exclusion is enforced here rather than only in :class:`DatabaseDataSource` so a
    custom seam cannot leak ``fwd_return``/``label_*`` into the ranker: those columns are
    computed from bars AFTER ``date``, so a model that sees them backtests a strategy that
    could not exist live.
    """
    frame = ctx.source.snapshots(session)
    if frame is None or len(frame) == 0:
        raise BacktestDataUnavailable(
            f'No eligible ETFFeatureSnapshot rows on {session.isoformat()} -- the point-in-time '
            'feature job has not produced a pool for this date, so there is nothing to rank. '
            'Run the ETF feature build for the window before backtesting it.'
        )
    leaked = [column for column in LABEL_COLUMNS if column in frame.columns]
    if leaked:
        frame = frame.drop(columns=leaked)
    return frame


def _apply_liquidity_floor(ctx: _Context, weights: dict[str, float], session: date) -> tuple[dict[str, float], list[dict]]:
    """Drop legs whose planned notional the fund's own turnover cannot absorb.

    Screened with the trailing mean ``ETFDaily.amount`` (元) as of the SIGNAL date: the
    execution day's turnover is unknowable when the order is decided, so testing it
    would be look-ahead. A rejected leg's weight is handed to the defensive asset; if
    the defensive asset itself breaches the floor the weight becomes cash.
    """
    config = ctx.config
    if config.liquidity_floor_multiple <= 0 or not weights:
        return dict(weights), []

    equity = ctx.equity
    defensive = str(config.defensive_asset)
    ctx.book.ensure(weights.keys())
    ordered = sorted(
        (item for item in weights.items() if item[0] != defensive and item[1] > 0),
        key=lambda item: (-item[1], item[0]),
    )
    kept: dict[str, float] = {defensive: weights.get(defensive, 0.0)} if defensive in weights else {}
    rejected: list[dict] = []

    for code, weight in ordered:
        notional = weight * equity
        required = notional * float(config.liquidity_floor_multiple)
        average_amount = ctx.book.trailing_average_amount_yuan(code, session, config.liquidity_lookback_sessions)
        if average_amount is None or average_amount < required:
            rejected.append(
                _rejection_record(
                    code, weight, notional, required, average_amount, session, defensive,
                    config.liquidity_floor_multiple,
                )
            )
            ctx.state.rejected_fills += 1
            kept[defensive] = kept.get(defensive, 0.0) + weight
            continue
        kept[code] = weight

    defensive_weight = kept.get(defensive, 0.0)
    if defensive_weight > 0:
        required = defensive_weight * equity * float(config.liquidity_floor_multiple)
        average_amount = ctx.book.trailing_average_amount_yuan(defensive, session, config.liquidity_lookback_sessions)
        if average_amount is None or average_amount < required:
            rejected.append(
                _rejection_record(
                    defensive, defensive_weight, defensive_weight * equity, required, average_amount, session,
                    None, config.liquidity_floor_multiple,
                )
            )
            ctx.state.rejected_fills += 1
            kept.pop(defensive, None)

    return kept, rejected


def _rejection_record(code, weight, notional, required, average_amount, session, redistributed_to, floor_multiple) -> dict[str, Any]:
    return {
        'ts_code': code,
        'weight': float(weight),
        'planned_notional_yuan': float(notional),
        'required_average_amount_yuan': float(required),
        'observed_average_amount_yuan': None if average_amount is None else float(average_amount),
        'signal_date': session.isoformat(),
        'rejected_reason': 'liquidity_floor',
        'liquidity_floor_multiple': float(floor_multiple),
        'redistributed_to': redistributed_to,
    }


def _build_plan(ctx: _Context, session: date, execute_session: date) -> None:
    """Rank, select, liquidity-screen, and queue one plan for execution next session."""
    config = ctx.config
    snapshots = _snapshot_frame_for_ranking(ctx, session)
    scored = _validated_scored_frame(
        ctx.module.score_snapshots(
            snapshots,
            model_type=config.model_type,
            momentum_lookback=config.momentum_lookback,
            lgb_predictor=ctx.predictors.get('lgb'),
            lstm_predictor=ctx.predictors.get('lstm'),
        )
    )
    rotation_config = ctx.module.RotationConfig(
        max_positions=int(config.max_positions),
        momentum_lookback=int(config.momentum_lookback),
        absolute_momentum_filter=bool(config.absolute_momentum_filter),
        defensive_asset=str(config.defensive_asset),
        weighting=str(config.weighting),
        rank_change_threshold=int(config.rank_change_threshold),
        max_swaps=int(config.max_swaps),
        # The published default is 3, but ``rotation_engine.RotationConfig`` requires
        # ``1 <= min_positions_for_risk_on <= max_positions``; a 1- or 2-slot book would
        # otherwise fail validation on every session. Clamping keeps small books fully
        # invested (with 2 slots and a floor of 3 nothing could ever be risk-on) instead of
        # silently swallowing a config error.
        min_positions_for_risk_on=min(int(config.min_positions_for_risk_on), int(config.max_positions)),
    )
    plan = ctx.module.build_rotation_plan(
        scored,
        snapshots,
        rotation_config,
        current_weights=_current_weights(ctx),
    )
    weights, diagnostics = _plan_to_target_weights(ctx.module, plan, config)
    weights, rejected = _apply_liquidity_floor(ctx, weights, session)

    total_weight = float(sum(max(value, 0.0) for value in weights.values()))
    scaled = False
    if total_weight > 1.0 + 1e-9:
        # Never lever: the plan asked for more than the portfolio has, so every leg is
        # trimmed pro-rata rather than dropping the tail by hand.
        weights = {code: weight / total_weight for code, weight in weights.items()}
        scaled = True

    metadata = snapshots.set_index('ts_code')[['name', 'category']] if 'name' in snapshots.columns else None
    leg_meta = {}
    for code in weights:
        name, category = '', ''
        if metadata is not None and code in metadata.index:
            row = metadata.loc[code]
            name = str(row['name']) if isinstance(row, pd.Series) else ''
            category = str(row['category']) if isinstance(row, pd.Series) else ''
        leg_meta[code] = {'name': name, 'category': category}

    ctx.state.pending_plan = {
        'signal_date': session,
        'execute_date': execute_session,
        'target_weights': weights,
        'rejected': rejected,
        'leg_meta': leg_meta,
        'model_type': config.model_type,
        'cash_weight': max(0.0, 1.0 - min(total_weight, 1.0)),
        'weights_scaled': scaled,
        'rankings': _serialize_rankings(diagnostics.get('rankings'), scored),
        'defensive_asset': diagnostics.get('defensive_asset') or str(config.defensive_asset),
        'defensive_allocation': float(diagnostics.get('defensive_allocation') or 0.0),
    }
    for record in rejected:
        ctx.state.trade_log.append(
            {
                **record,
                'date': session.isoformat(),
                'execute_date': execute_session.isoformat(),
                'side': 'BUY',
                'shares': 0,
                'raw_price': None,
                'exec_price': None,
                'notional': 0.0,
                'commission': 0.0,
                'slippage_cost': 0.0,
                'status': 'REJECTED_LIQUIDITY',
                'liquidity_floor_multiple': float(config.liquidity_floor_multiple),
                'fee_model': fee_model_summary(config)['mode'],
            }
        )
    ctx.state.signals_generated += 1


def _serialize_rankings(rankings: Any, scored: pd.DataFrame) -> list[dict[str, Any]]:
    """Top-of-book ranking snapshot, for auditing why a leg was chosen."""
    rows: list[dict[str, Any]] = []
    source = rankings
    if not source:
        source = scored.head(20).to_dict('records')
    if hasattr(source, 'to_dict'):
        source = source.to_dict('records')
    for rank, item in enumerate(list(source)[:20], start=1):
        if not isinstance(item, dict):
            item = {'ts_code': _leg_field(item, 'ts_code'), 'score': _leg_field(item, 'score')}
        entry = {'rank': rank}
        for key in ('ts_code', 'name', 'score', 'rank', 'category'):
            value = item.get(key)
            if value is not None:
                entry[key] = float(value) if key == 'score' and isinstance(value, (int, float, Decimal)) else value
        rows.append(entry)
    return rows


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _fill_price(raw_price: float, side: str, config: BacktestConfig) -> float:
    """Slipped execution price: buys pay up, sells give back."""
    if str(side).upper() == 'BUY':
        return raw_price * (1.0 + float(config.slippage))
    return raw_price * (1.0 - float(config.slippage))


def _trade_log_entry(
    ctx: _Context,
    *,
    session: date,
    code: str,
    side: str,
    shares: int,
    raw_price: float,
    exec_price: float,
    commission: float,
    weight: float,
    signal_date: date | None,
    reason: str,
    realized_pnl: float | None = None,
    return_pct: float | None = None,
    status: str = 'FILLED',
) -> dict[str, Any]:
    config = ctx.config
    lot = ctx.state.positions.get(code)
    meta = (ctx.state.pending_plan or {}).get('leg_meta', {}).get(code, {})
    notional = shares * exec_price
    return {
        'date': session.isoformat(),
        'signal_date': signal_date.isoformat() if signal_date else None,
        'ts_code': code,
        'name': meta.get('name') or (lot.name if lot else ''),
        'category': meta.get('category') or (lot.category if lot else ''),
        'side': str(side).upper(),
        'shares': int(shares),
        'raw_price': float(raw_price),
        'exec_price': float(exec_price),
        'notional': float(notional),
        'gross_notional': float(shares * raw_price),
        'commission': float(commission),
        'slippage_cost': float(abs(shares * raw_price * float(config.slippage))),
        'weight': float(weight),
        'reason': reason,
        'status': status,
        'realized_pnl': None if realized_pnl is None else float(realized_pnl),
        'return_pct': None if return_pct is None else float(return_pct),
        'fee_model': 'cn_etf_two_way_commission',
        'stamp_duty': 0.0,
    }


def _sell(ctx: _Context, code: str, shares: int, session: date, *, weight: float, reason: str, signal_date: date | None) -> bool:
    """Liquidate ``shares`` of ``code`` at this session's close. Returns whether it filled."""
    lot = ctx.state.positions.get(code)
    if lot is None or shares <= 0:
        return False
    raw_price = ctx.book.raw_close(code, session)
    if raw_price is None:
        # Never force-close at a fabricated price (same rule as the A-share engine): a
        # suspension means the sell simply did not happen and the leg stays open.
        ctx.state.skipped_fills += 1
        ctx.state.trade_log.append(
            _trade_log_entry(
                ctx, session=session, code=code, side='SELL', shares=0, raw_price=0.0,
                exec_price=0.0, commission=0.0, weight=weight, signal_date=signal_date,
                reason=reason, status='SKIPPED_NO_PRICE',
            )
        )
        return False

    shares = int(min(shares, lot.shares))
    exec_price = _fill_price(raw_price, 'SELL', ctx.config)
    notional = shares * exec_price
    commission = etf_commission(notional, ctx.config)
    slippage_cost = shares * raw_price * float(ctx.config.slippage)

    proceeds = notional - commission
    ctx.state.cash += proceeds
    ctx.state.total_commission += commission
    ctx.state.total_slippage += slippage_cost
    lot.shares -= shares
    lot.fees_paid += commission

    cost_basis = lot.avg_cost * shares
    realized = proceeds - cost_basis
    return_pct = (realized / cost_basis) if cost_basis > 0 else None
    ctx.state.turnover_by_session[session] = ctx.state.turnover_by_session.get(session, 0.0) + notional

    ctx.state.trade_log.append(
        _trade_log_entry(
            ctx, session=session, code=code, side='SELL', shares=shares, raw_price=raw_price,
            exec_price=exec_price, commission=commission, weight=weight, signal_date=signal_date,
            reason=reason, realized_pnl=realized, return_pct=return_pct,
        )
    )

    if lot.shares <= 0:
        holding_sessions = _session_count(ctx, lot.entry_date, session)
        ctx.state.closed_trades.append(
            {
                # ``pnl``/``return_pct`` describe this tranche and are already net: the
                # buy commission and every distribution are embedded in ``avg_cost``,
                # and the sell commission is embedded in ``proceeds``. ``position_*``
                # fields describe the whole lifetime of the lot, which for a single
                # tranche position is the same thing.
                'ts_code': code,
                'name': lot.name,
                'category': lot.category,
                'signal_date': _iso(lot.signal_date or lot.entry_date),
                'entry_date': _iso(lot.entry_date),
                'exit_date': _iso(session),
                'entry_price': lot.entry_price,
                'exit_price': exec_price,
                'weight': lot.target_weight,
                'shares': shares,
                'pnl': float(realized),
                'return_pct': float(return_pct) if return_pct is not None else None,
                'holding_sessions': holding_sessions,
                'position_fees_paid': float(lot.fees_paid),
                'position_dividend_cash': float(lot.dividend_cash),
                'position_cost_notional': float(lot.cost_notional),
                'status': 'CLOSED',
            }
        )
        ctx.state.positions.pop(code, None)
    return True


def _buy(ctx: _Context, code: str, shares: int, session: date, *, weight: float, reason: str, signal_date: date | None) -> bool:
    """Establish or add to a position at this session's close, within available cash."""
    if shares <= 0:
        return False
    raw_price = ctx.book.raw_close(code, session)
    if raw_price is None:
        ctx.state.skipped_fills += 1
        ctx.state.trade_log.append(
            _trade_log_entry(
                ctx, session=session, code=code, side='BUY', shares=0, raw_price=0.0, exec_price=0.0,
                commission=0.0, weight=weight, signal_date=signal_date, reason=reason, status='SKIPPED_NO_PRICE',
            )
        )
        return False

    lot = ctx.state.positions.get(code)
    name, category = '', ''
    meta = (ctx.state.pending_plan or {}).get('leg_meta', {}).get(code, {})
    if lot is not None:
        name, category = lot.name, lot.category
    else:
        name, category = str(meta.get('name') or ''), str(meta.get('category') or '')

    # Affordability loop: a min-commission leg or a rounding remainder must not overdraw
    # the ledger, so the order shrinks by one board lot until cash covers it.
    while shares > 0:
        exec_price = _fill_price(raw_price, 'BUY', ctx.config)
        notional = shares * exec_price
        commission = etf_commission(notional, ctx.config)
        if notional + commission <= ctx.state.cash + 1e-6:
            break
        shares -= max(int(ctx.config.lot_size), 1)
    if shares <= 0:
        ctx.state.skipped_fills += 1
        return False

    ctx.state.cash -= notional + commission
    ctx.state.total_commission += commission
    ctx.state.total_slippage += shares * raw_price * float(ctx.config.slippage)
    ctx.state.turnover_by_session[session] = ctx.state.turnover_by_session.get(session, 0.0) + notional

    if lot is None:
        ctx.state.positions[code] = PositionLot(
            ts_code=code,
            name=name,
            category=category,
            shares=int(shares),
            avg_cost=(notional + commission) / shares,
            cost_notional=notional,
            fees_paid=commission,
            entry_date=session,
            entry_price=exec_price,
            signal_date=signal_date,
            target_weight=weight,
            last_close=raw_price,
            last_close_date=session,
            last_adj_factor=ctx.book.adj_factor(code, session),
        )
    else:
        total_shares = lot.shares + shares
        total_cost = lot.avg_cost * lot.shares + notional + commission
        lot.shares = int(total_shares)
        lot.avg_cost = total_cost / total_shares
        lot.cost_notional += notional
        lot.fees_paid += commission
        lot.target_weight = weight
        if not lot.name:
            lot.name = name
        if lot.signal_date is None:
            lot.signal_date = signal_date

    ctx.state.trade_log.append(
        _trade_log_entry(
            ctx, session=session, code=code, side='BUY', shares=shares, raw_price=raw_price,
            exec_price=exec_price, commission=commission, weight=weight, signal_date=signal_date, reason=reason,
        )
    )
    return True


def _execute_plan(ctx: _Context, session: date) -> None:
    """Rebalance to the queued plan at this session's close: sells first, then buys.

    Leg notionals are sized off the PRE-TRADE equity marked at this session's close, so
    a plan built yesterday is funded by what the book is actually worth today.
    """
    plan = ctx.state.pending_plan
    if not plan:
        return
    config = ctx.config
    targets = {code: float(weight) for code, weight in plan['target_weights'].items() if float(weight) > 0}
    signal_date = plan['signal_date']
    ctx.book.ensure(set(ctx.state.positions) | set(targets))

    for code, lot in ctx.state.positions.items():
        price = ctx.book.raw_close(code, session)
        if price is not None:
            lot.last_close = price
            lot.last_close_date = session
    pre_trade_equity = ctx.state.cash + sum(lot.market_value() for lot in ctx.state.positions.values())
    if pre_trade_equity <= 0:
        ctx.state.pending_plan = None
        return

    minimum_notional = float(config.min_trade_notional_fraction) * pre_trade_equity

    for code in sorted(set(ctx.state.positions) - set(targets)):
        lot = ctx.state.positions[code]
        _sell(ctx, code, lot.shares, session, weight=0.0, reason='rotated_out', signal_date=signal_date)

    for code in sorted(targets):
        weight = targets[code]
        target_notional = weight * pre_trade_equity
        price = ctx.book.raw_close(code, session)
        if price is None:
            ctx.state.skipped_fills += 1
            ctx.state.trade_log.append(
                _trade_log_entry(
                    ctx, session=session, code=code, side='BUY', shares=0, raw_price=0.0, exec_price=0.0,
                    commission=0.0, weight=weight, signal_date=signal_date, reason='no_price_on_execution_session',
                    status='SKIPPED_NO_PRICE',
                )
            )
            continue
        target_shares = _round_lots(target_notional, price, config.lot_size)
        current_shares = int(ctx.state.positions[code].shares) if code in ctx.state.positions else 0
        delta = target_shares - current_shares
        if abs(delta) * price < minimum_notional:
            ctx.state.sub_minimum_deltas += 1
            continue
        if delta < 0:
            # Sells are not lot-rounded: the residual odd lot must be liquidable in one order.
            _sell(ctx, code, -delta, session, weight=weight, reason='rebalance_down', signal_date=signal_date)
        else:
            _buy(ctx, code, delta, session, weight=weight, reason='rebalance_up', signal_date=signal_date)

    ctx.state.pending_plan = None


def _session_count(ctx: _Context, start: date | None, end: date) -> int:
    if start is None:
        return 0
    return sum(1 for day in ctx.calendar if start < day <= end)


def _accrue_dividends(ctx: _Context, session: date) -> float:
    """Credit the implied cash distribution from the ``fund_adj`` factor change.

    TuShare's factor is cumulative, so ``close * (factor_t / factor_{t-1} - 1)`` is the
    ex-date distribution per share. Crediting it to cash -- instead of silently
    compounding it inside a total-return price -- is what lets the ledger be marked and
    realised in the raw prices a human can check against market data.
    """
    credited = 0.0
    for code, lot in ctx.state.positions.items():
        factor = ctx.book.adj_factor(code, session)
        if factor is None:
            continue
        prior_factor = lot.last_adj_factor
        lot.last_adj_factor = factor
        if prior_factor is None or prior_factor <= 0:
            continue
        ratio = factor / prior_factor
        if abs(ratio - 1.0) <= MIN_FACTOR_RATIO_DRIFT:
            continue
        price = ctx.book.raw_close(code, session) or lot.last_close
        if not price:
            continue
        dividend_per_share = price * (ratio - 1.0)
        if dividend_per_share <= 0:
            continue
        cash = float(lot.shares) * dividend_per_share
        ctx.state.cash += cash
        ctx.state.total_dividend_cash += cash
        lot.dividend_cash += cash
        lot.avg_cost = max(0.0, lot.avg_cost - dividend_per_share)
        credited += cash
    return credited


def _mark_equity(ctx: _Context, session: date) -> dict[str, Any]:
    """Cash plus holdings marked at this session's raw close; a stale bar reuses the last close."""
    holdings = 0.0
    for code, lot in ctx.state.positions.items():
        price = ctx.book.raw_close(code, session)
        if price is not None:
            lot.last_close = price
            lot.last_close_date = session
        else:
            ctx.state.stale_marks += 1
        holdings += lot.market_value()
    equity = ctx.state.cash + holdings
    peak = ctx.state.peak_equity
    if peak is None or equity > peak:
        peak = equity
        ctx.state.peak_equity = peak
    point = {
        'date': session.isoformat(),
        'equity': float(equity),
        'cash': float(ctx.state.cash),
        'holdings': float(holdings),
        'positions': int(len(ctx.state.positions)),
        'drawdown': float(1.0 - equity / peak) if peak > 0 else 0.0,
    }
    ctx.state.equity_curve.append(point)
    return point


# ---------------------------------------------------------------------------
# Session loop
# ---------------------------------------------------------------------------


def _run_session(ctx: _Context, run_sessions: list[date], index: int) -> dict[str, Any]:
    """One session, in the order that makes the ledger provable.

    1. Distributions are credited on the shares held ENTERING the session (a holder who
       sells on the ex-date still receives it), before any order can reduce the count.
    2. The plan queued on the previous session executes at this session's close.
    3. Equity is marked at this session's close, so a fill and its mark use one price.
    4. A new ranking is built from this session's snapshots, queued for the NEXT session.
    """
    session = run_sessions[index]
    dividends = _accrue_dividends(ctx, session)
    plan = ctx.state.pending_plan
    executed = False
    if plan is not None and plan['execute_date'] == session:
        _execute_plan(ctx, session)
        executed = True
    point = _mark_equity(ctx, session)
    point['dividend_cash'] = float(dividends)
    point['rebalanced'] = bool(executed)
    if session in ctx.signal_sessions and index + 1 < len(run_sessions):
        _build_plan(ctx, session, run_sessions[index + 1])
    elif session in ctx.signal_sessions:
        # A signal with no session left to execute in is dropped rather than filled at
        # the same close it was computed from.
        ctx.errors.append(f'signal on {session.isoformat()} had no following session; dropped.')
    return point


# ---------------------------------------------------------------------------
# Benchmark series and metrics
# ---------------------------------------------------------------------------


def _benchmark_returns(closes: pd.Series, sessions: list[date]) -> pd.Series | None:
    """Benchmark session returns aligned to the strategy's calendar.

    The index feed can legitimately lack a row the ETF feed has (or vice versa), so the
    closes are forward-filled onto the strategy calendar: an index row that is merely
    missing must not become a 0% return day AND a real gap in the strategy calendar must
    not shorten the sample.
    """
    if closes is None or len(closes) == 0 or not sessions:
        return None
    calendar = pd.Index(sessions)
    combined = closes.index.union(calendar)
    aligned = closes.reindex(combined).ffill().reindex(calendar).astype('float64')
    aligned = aligned[aligned > 0]
    if len(aligned) < 2:
        return None
    returns = (aligned / aligned.shift(1)) - 1.0
    return returns.dropna()


def build_equal_weight_proxy_returns(
    source: Any,
    run_sessions: list[date],
    batch_codes: int = 200,
) -> pd.Series | None:
    """Frictionless equal-weight return series over every point-in-time eligible ETF.

    The "could I have just bought the whole pool" reference. Holdings are set on the
    MONTHLY signal session and carried from that session's execution close to the next
    period's execution close, i.e. the proxy sees the same one-session latency the
    strategy does, so the comparison is not an artefact of timing.

    Deliberately expensive: it reads bars for every eligible fund each month. It is the
    only benchmark that cannot be wrong about survivorship (an index adds a constituent
    only after it has grown), which is why it is worth the rows. Disable with
    ``compute_proxy_benchmark=False`` when the run is exploratory.
    """
    if len(run_sessions) < 3:
        return None
    month_signals = signal_sessions(run_sessions, 'MONTHLY')
    if len(month_signals) < 2:
        return None

    returns = pd.Series(float('nan'), index=pd.Index(run_sessions), dtype='float64')
    position_of = {day: i for i, day in enumerate(run_sessions)}
    blocks = []
    for order, signal_day in enumerate(month_signals):
        execute_index = position_of.get(signal_day, -1) + 1
        if execute_index >= len(run_sessions):
            continue
        next_execute_index = len(run_sessions)
        if order + 1 < len(month_signals):
            candidate = position_of.get(month_signals[order + 1], -1) + 1
            if candidate > execute_index and candidate <= len(run_sessions):
                next_execute_index = candidate
        blocks.append((execute_index, next_execute_index, signal_day))

    for execute_index, end_index, signal_day in blocks:
        if end_index <= execute_index + 1:
            continue
        window = run_sessions[execute_index:end_index + 1]
        snapshots = source.snapshots(signal_day)
        if snapshots is None or len(snapshots) == 0 or 'ts_code' not in snapshots.columns:
            continue
        codes = sorted({str(code) for code in snapshots['ts_code'].tolist() if code})
        if not codes:
            continue
        sums = pd.Series(0.0, index=pd.Index(window), dtype='float64')
        counts = pd.Series(0.0, index=pd.Index(window), dtype='float64')
        for offset in range(0, len(codes), max(int(batch_codes), 1)):
            chunk = codes[offset:offset + max(int(batch_codes), 1)]
            panel = source.prices(chunk, window[0], window[-1])
            adjusted = panel.adj_close
            if adjusted.empty:
                continue
            adjusted = adjusted.reindex(pd.Index(window))
            step = (adjusted / adjusted.shift(1)) - 1.0
            valid = step.notna() & adjusted.notna() & adjusted.shift(1).notna()
            sums = sums.add(step.where(valid).fillna(0.0).sum(axis=1, min_count=0), fill_value=0.0)
            counts = counts.add(valid.sum(axis=1).astype('float64'), fill_value=0.0)
        with np.errstate(divide='ignore', invalid='ignore'):
            block_returns = sums / counts.where(counts > 0)
        returns.loc[run_sessions[execute_index + 1:end_index + 1]] = block_returns.iloc[1:].to_numpy()

    if returns.dropna().empty:
        return None
    return returns


def _comparison_bundle(
    label: str,
    code: str,
    closes: pd.Series | None,
    returns: pd.Series | None,
    strategy_returns: pd.Series,
    strategy_annualized: float | None,
) -> dict[str, Any]:
    """One secondary benchmark column for ``report``: level stats plus relative stats.

    Relative numbers are computed on the ALIGNED sample (both series present), so a
    benchmark with holes cannot inflate the strategy's apparent alpha.
    """
    bundle: dict[str, Any] = {
        'label': label,
        'code': code,
        'available': bool(returns is not None and len(returns) > 0),
    }
    if not bundle['available']:
        bundle['note'] = 'No rows in markets.BenchmarkIndexDaily (or no eligible pool) for this code in the window.'
        return bundle

    aligned_strategy, aligned_benchmark = finance_metrics._aligned_pair(strategy_returns, returns)
    benchmark_equity = (1.0 + finance_metrics._finite_series(returns).fillna(0.0)).cumprod()
    bundle['benchmark_total_return'] = float((1.0 + aligned_benchmark).prod() - 1.0) if len(aligned_benchmark) else None
    bundle['benchmark_annualized_return'] = finance_metrics.annualized_return(benchmark_equity)
    bundle['benchmark_max_drawdown'] = finance_metrics.max_drawdown(benchmark_equity)
    bundle['benchmark_sharpe_ratio'] = finance_metrics.sharpe_ratio(returns)
    bundle['aligned_observations'] = int(len(aligned_strategy))
    if closes is not None:
        bundle['benchmark_close_observations'] = int(len(finance_metrics._finite_series(closes)))

    regression = finance_metrics.alpha_beta(aligned_strategy, aligned_benchmark)
    bundle['alpha_annualized'] = None if regression is None else regression['alpha_annualized']
    bundle['beta'] = None if regression is None else regression['beta']
    bundle['r_squared'] = None if regression is None else regression['r_squared']
    bundle['information_ratio'] = finance_metrics.information_ratio(aligned_strategy, aligned_benchmark)
    benchmark_annualized = bundle['benchmark_annualized_return']
    bundle['excess_annualized_return'] = (
        None
        if strategy_annualized is None or benchmark_annualized is None
        else float(strategy_annualized - benchmark_annualized)
    )
    return bundle


def _finalize_metrics(ctx: _Context, config: BacktestConfig, source: Any, run_sessions: list[date]) -> dict[str, Any]:
    """Everything that needs the completed curve: strategy metrics, comparisons, report."""
    state = ctx.state
    dates = [date.fromisoformat(point['date']) for point in state.equity_curve]
    equity = pd.Series([point['equity'] for point in state.equity_curve], index=pd.Index(dates), dtype='float64')
    traded = pd.Series(
        [state.turnover_by_session.get(day, 0.0) for day in dates], index=pd.Index(dates), dtype='float64'
    )
    strategy_returns = finance_metrics.returns_from_equity(equity)

    benchmark_closes = source.benchmark_close(config.benchmark_code, config.start_date, config.end_date)
    benchmark_returns = _benchmark_returns(benchmark_closes, dates)
    trade_pnls = [item.get('pnl') for item in state.closed_trades if item.get('pnl') is not None]

    summary = finance_metrics.summarize_performance(
        equity,
        daily_traded_notional=traded,
        benchmark_returns=benchmark_returns,
        trade_returns=trade_pnls,
    )

    metrics = {
        'total_return': summary['total_return'],
        'annualized_return': summary['annualized_return'],
        'max_drawdown': summary['max_drawdown'],
        'sharpe_ratio': summary['sharpe_ratio'],
        'calmar_ratio': summary['calmar_ratio'],
        'win_rate': summary['win_rate'],
        'turnover_annual': summary['turnover_annual'],
        'alpha_vs_benchmark': summary['alpha_annualized'],
        'beta_vs_benchmark': summary['beta'],
        'information_ratio': summary['information_ratio'],
        'benchmark_return': summary['benchmark_return'],
        'total_fees': float(state.total_commission),
        'total_slippage_cost': float(state.total_slippage),
        'total_cost': float(state.total_commission + state.total_slippage),
        'final_equity': float(equity.iloc[-1]) if len(equity) else None,
    }

    strategy_annualized = summary['annualized_return']
    comparisons = {
        config.benchmark_code: _comparison_bundle(
            f'Primary benchmark {config.benchmark_code}', config.benchmark_code, benchmark_closes,
            benchmark_returns, strategy_returns, strategy_annualized,
        )
    }
    for code in config.secondary_benchmark_codes:
        closes = source.benchmark_close(code, config.start_date, config.end_date)
        comparisons[code] = _comparison_bundle(
            f'Secondary benchmark {code}', code, closes, _benchmark_returns(closes, dates),
            strategy_returns, strategy_annualized,
        )
    if config.compute_proxy_benchmark:
        try:
            proxy = build_equal_weight_proxy_returns(source, run_sessions)
        except Exception as exc:  # the proxy is context, never a reason to fail a run
            proxy = None
            ctx.errors.append(f'equal-weight proxy benchmark unavailable: {exc}')
        comparisons['EQUAL_WEIGHT_ELIGIBLE_POOL'] = _comparison_bundle(
            'Equal-weight all-eligible-ETF proxy (frictionless, monthly)', 'EQUAL_WEIGHT_ELIGIBLE_POOL',
            None, proxy, strategy_returns, strategy_annualized,
        )

    report = {
        'execution_model': {
            'signal_date': 'close of session D, from D point-in-time snapshots',
            'execution_date': 'close of session D+1 (next ETFDaily session)',
            'look_ahead': 'none: no order is filled at a price at or before its signal close',
            'price_basis': 'ETFDaily.close (raw) x (1 +/- slippage); adj_factor changes credited as cash dividends',
        },
        'fee_model': fee_model_summary(config),
        'benchmark': comparisons,
        'counters': {
            'sessions': int(len(state.equity_curve)),
            'signals_generated': int(state.signals_generated),
            'trades': int(len(state.trade_log)),
            'filled_trades': int(sum(1 for item in state.trade_log if item.get('status') == 'FILLED')),
            'rejected_liquidity_fills': int(state.rejected_fills),
            'skipped_no_price_fills': int(state.skipped_fills),
            'stale_marks': int(state.stale_marks),
            'sub_minimum_deltas': int(state.sub_minimum_deltas),
            'closed_trades': int(len(state.closed_trades)),
            'dividend_cash_yuan': float(state.total_dividend_cash),
            'traded_notional_yuan': float(traded.sum()),
            'price_book_loads': int(ctx.book.load_calls),
            'price_book_rows': int(ctx.book.loaded_rows),
        },
        'metrics_detail': {
            key: summary[key]
            for key in (
                'observations', 'sessions', 'rf', 'periods', 'r_squared', 'correlation',
                'regression_observations', 'alpha_annualized',
            )
        },
        'closed_trades': state.closed_trades,
        'liquidity_rejections': [
            item for item in state.trade_log if item.get('status') == 'REJECTED_LIQUIDITY'
        ][:50],
        'errors': ctx.errors,
    }
    return {'metrics': metrics, 'report': report, 'summary': summary}


# ---------------------------------------------------------------------------
# Persistence (only touched when a run row was supplied)
# ---------------------------------------------------------------------------

#: ``ETFRotationBacktest`` columns stamped when a run is adopted, so a FAILED row is
#: still self-describing.
_RUN_PARAMETER_FIELDS = (
    'start_date', 'end_date', 'rebalance_frequency', 'max_positions', 'momentum_lookback',
    'defensive_asset', 'initial_capital', 'commission_rate', 'min_commission', 'slippage',
    'liquidity_floor_multiple', 'model_type', 'benchmark_code', 'walk_forward_steps',
    'absolute_momentum_filter',
)


def _adopt_backtest_row(persist_to: Any, name: str | None, config: BacktestConfig) -> Any:
    """Return the ``ETFRotationBacktest`` this run writes to, or ``None`` for no writes."""
    from apps.etf_rotation.models import ETFRotationBacktest

    if persist_to is not None:
        if isinstance(persist_to, ETFRotationBacktest):
            run = persist_to
        elif isinstance(persist_to, int):
            run = ETFRotationBacktest.objects.get(pk=persist_to)
        else:
            raise BacktestConfigError(
                f'persist_to must be an ETFRotationBacktest instance or pk, got {type(persist_to).__name__}.'
            )
    elif name:
        run = ETFRotationBacktest(name=str(name))
    else:
        return None

    for field_name in _RUN_PARAMETER_FIELDS:
        value = getattr(config, field_name)
        if field_name == 'initial_capital':
            value = Decimal(str(value)).quantize(Decimal('0.01'))
        elif field_name in ('commission_rate', 'slippage', 'liquidity_floor_multiple'):
            value = float(value)
        elif field_name == 'min_commission':
            value = Decimal(str(value)).quantize(Decimal('0.01'))
        setattr(run, field_name, value)
    run.parameters = config.to_parameters()
    run.status = ETFRotationBacktest.Status.PENDING
    run.error_message = ''
    run.save()
    run.status = ETFRotationBacktest.Status.RUNNING
    run.started_at = _now()
    run.save(update_fields=['status', 'started_at', 'updated_at'])
    return run


def _now():
    from django.utils import timezone

    return timezone.now()


def _position_payload(lot: PositionLot) -> dict[str, Any]:
    payload = asdict(lot)
    for key in ('entry_date', 'signal_date', 'last_close_date'):
        payload[key] = _iso(payload.get(key))
    return payload


def _plan_payload(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    payload = dict(plan)
    payload['signal_date'] = _iso(payload.get('signal_date'))
    payload['execute_date'] = _iso(payload.get('execute_date'))
    return payload


def _runtime_payload(ctx: _Context, cursor: int) -> dict[str, Any]:
    state = ctx.state
    return {
        'fingerprint': ctx.config.fingerprint(),
        'cursor': int(cursor),
        'cash': float(state.cash),
        'total_commission': float(state.total_commission),
        'total_slippage': float(state.total_slippage),
        'total_dividend_cash': float(state.total_dividend_cash),
        'rejected_fills': int(state.rejected_fills),
        'skipped_fills': int(state.skipped_fills),
        'stale_marks': int(state.stale_marks),
        'sub_minimum_deltas': int(state.sub_minimum_deltas),
        'signals_generated': int(state.signals_generated),
        'turnover_by_session': {day.isoformat(): float(value) for day, value in state.turnover_by_session.items()},
        'positions': {code: _position_payload(lot) for code, lot in state.positions.items()},
        'pending_plan': _plan_payload(state.pending_plan),
        # ``equity_points`` is one per completed session, so it must equal ``cursor``; the
        # row may legitimately hold MORE rows than the cursor (a later chunk appended into
        # the same list before a crash), which is why the restore truncates instead of
        # rejecting. Anything FEWER means the columns and the scratch state disagree.
        'equity_points': int(len(state.equity_curve)),
        'trade_rows': int(len(state.trade_log)),
        'closed_trade_rows': int(len(state.closed_trades)),
    }


def _persist_progress(run: Any, ctx: _Context, cursor: int, total_sessions: int) -> None:
    """Durably stage a chunk's output so a worker restart resumes instead of restarting.

    The curve, the ledger and the closed-trade list live in their own columns;
    ``runtime_state`` holds only what cannot be re-derived from them (cash, lots, the
    queued plan, the accumulators). Mirrors ``apps.backtest.tasks``' resumable design.

    The JSON columns are stored as *snapshots* (copies), never as aliases of the live
    lists: an alias would keep growing inside the row after the cursor was written, and a
    later resume could no longer tell which rows belonged to the completed chunks.
    """
    from apps.etf_rotation.models import ETFRotationBacktest

    report = dict(run.report or {})
    report['runtime_state'] = _runtime_payload(ctx, cursor)
    report['closed_trades'] = [dict(item) for item in ctx.state.closed_trades]
    report['status_note'] = f'chunked run in progress: {cursor}/{total_sessions} sessions'
    run.report = report
    run.equity_curve = [dict(point) for point in ctx.state.equity_curve]
    run.trade_log = [dict(entry) for entry in ctx.state.trade_log]
    # Same convention as the finished run: ``total_fees`` is the brokerage commission ledger
    # only. Slippage is a market-impact cost, not a fee, and is reported separately.
    run.total_fees = float(ctx.state.total_commission)
    run.status = ETFRotationBacktest.Status.RUNNING
    run.save()


def _restore_state(run: Any, ctx: _Context) -> int:
    """Rehydrate engine state from a persisted chunk. Returns the session index to resume at."""
    from apps.etf_rotation.models import ETFRotationBacktest

    if run is None:
        return 0
    raw = (run.report or {}).get('runtime_state')
    if not raw:
        return 0
    if raw.get('fingerprint') != ctx.config.fingerprint():
        ctx.errors.append('stored runtime_state fingerprint mismatch; restarting the run from scratch.')
        return 0

    state = ctx.state
    cursor = int(raw.get('cursor', -1))
    curve = list(run.equity_curve or [])
    trade_log = list(run.trade_log or [])
    closed_trades = list((run.report or {}).get('closed_trades') or [])
    trade_rows = int(raw.get('trade_rows', -1))
    closed_rows = int(raw.get('closed_trade_rows', len(closed_trades)))
    # The cursor says how many sessions are finished; the persisted columns may carry a
    # little more (a chunk that was cut off mid-way), so extra rows are dropped rather
    # than trusted -- and a shortfall means the scratch state cannot be trusted at all.
    if cursor < 0 or cursor > len(curve) or trade_rows < 0 or trade_rows > len(trade_log):
        ctx.errors.append(
            f'stored runtime_state (cursor={raw.get("cursor")}, equity_points={raw.get("equity_points")}, '
            f'trade_rows={raw.get("trade_rows")}) is ahead of the persisted columns '
            f'({len(curve)} curve points, {len(trade_log)} trades); restarting from scratch.'
        )
        return 0
    state.cursor = cursor
    state.cash = float(raw.get('cash', ctx.config.initial_capital))
    state.total_commission = float(raw.get('total_commission', 0.0))
    state.total_slippage = float(raw.get('total_slippage', 0.0))
    state.total_dividend_cash = float(raw.get('total_dividend_cash', 0.0))
    state.rejected_fills = int(raw.get('rejected_fills', 0))
    state.skipped_fills = int(raw.get('skipped_fills', 0))
    state.stale_marks = int(raw.get('stale_marks', 0))
    state.sub_minimum_deltas = int(raw.get('sub_minimum_deltas', 0))
    state.signals_generated = int(raw.get('signals_generated', 0))
    state.turnover_by_session = {
        _as_date(key): float(value) for key, value in (raw.get('turnover_by_session') or {}).items() if _as_date(key)
    }
    state.equity_curve = curve[:cursor]
    state.trade_log = trade_log[:trade_rows]
    state.closed_trades = closed_trades[:closed_rows]
    state.peak_equity = max((point['equity'] for point in state.equity_curve), default=None)
    for code, payload in (raw.get('positions') or {}).items():
        lot = PositionLot(**{**payload, 'ts_code': code})
        for key in ('entry_date', 'signal_date', 'last_close_date'):
            setattr(lot, key, _as_date(getattr(lot, key)))
        state.positions[code] = lot
    pending = raw.get('pending_plan')
    if pending:
        state.pending_plan = {
            **pending,
            'signal_date': _as_date(pending.get('signal_date')),
            'execute_date': _as_date(pending.get('execute_date')),
        }
    run.status = ETFRotationBacktest.Status.RUNNING
    return state.cursor


def clear_runtime_state(report: dict[str, Any] | None) -> dict[str, Any]:
    """Drop the resumable scratch state from a finished run's report."""
    cleaned = dict(report or {})
    cleaned.pop('runtime_state', None)
    cleaned.pop('status_note', None)
    return cleaned


def _persist_completion(run: Any, ctx: _Context, result: dict[str, Any]) -> None:
    from apps.etf_rotation.models import ETFRotationBacktest

    if run is None:
        return
    metrics = result['metrics']
    for column in (
        'total_return', 'annualized_return', 'max_drawdown', 'sharpe_ratio', 'calmar_ratio',
        'win_rate', 'turnover_annual', 'alpha_vs_benchmark', 'beta_vs_benchmark',
        'information_ratio', 'benchmark_return', 'total_fees',
    ):
        setattr(run, column, metrics.get(column))
    run.equity_curve = result['equity_curve']
    run.trade_log = result['trade_log']
    run.parameters = result['parameters']
    run.report = clear_runtime_state(result['report'])
    run.status = ETFRotationBacktest.Status.COMPLETED
    run.completed_at = _now()
    run.error_message = ''
    run.save()


def _persist_failure(run: Any, error: Exception) -> None:
    from apps.etf_rotation.models import ETFRotationBacktest

    if run is None:
        return
    report = dict(run.report or {})
    report['failure'] = {'type': type(error).__name__, 'message': str(error)}
    run.report = report
    run.status = ETFRotationBacktest.Status.FAILED
    run.error_message = f'{type(error).__name__}: {error}'[:4000]
    run.completed_at = _now()
    run.save()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_rotation_backtest(
    *,
    start_date: date,
    end_date: date,
    model_type: str | None = None,
    rebalance_frequency: str | None = None,
    max_positions: int | None = None,
    momentum_lookback: int | None = None,
    absolute_momentum_filter: bool | None = None,
    defensive_asset: str | None = None,
    initial_capital: float | None = None,
    commission_rate: float | None = None,
    min_commission: float | None = None,
    slippage: float | None = None,
    liquidity_floor_multiple: float | None = None,
    benchmark_code: str | None = None,
    lgb_predictor: Any = None,
    lstm_predictor: Any = None,
    persist_to: Any = None,
    name: str | None = None,
    data_source: Any = None,
    rotation_module: Any = None,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Backtest one rotation configuration and return metrics, curve, ledger and report.

    ``None`` for any cost/parameter knob means "take the ``BacktestConfig`` default",
    which resolves through ``settings`` (``ETF_COMMISSION_RATE`` etc.) -- the literal
    values in the signature are documented on the dataclass, not repeated here, so there
    is exactly one place a default can change.

    Test seams: ``data_source`` replaces :class:`DatabaseDataSource` (any object with
    ``sessions``/``snapshots``/``prices``/``benchmark_close``) and ``rotation_module``
    replaces the lazy ``apps.etf_rotation.rotation_engine`` import. With both supplied the
    engine runs with no database and no selection layer installed.

    Persistence happens only when ``persist_to`` (an ``ETFRotationBacktest`` row or pk) or
    ``name`` (creates the row) is given: PENDING -> RUNNING -> COMPLETED/FAILED, with the
    metric columns, the JSON columns, and a resumable ``report.runtime_state`` written
    between chunks.
    """
    config = BacktestConfig(start_date=start_date, end_date=end_date)
    explicit = {
        'model_type': model_type,
        'rebalance_frequency': rebalance_frequency,
        'max_positions': max_positions,
        'momentum_lookback': momentum_lookback,
        'absolute_momentum_filter': absolute_momentum_filter,
        'defensive_asset': defensive_asset,
        'initial_capital': initial_capital,
        'commission_rate': commission_rate,
        'min_commission': min_commission,
        'slippage': slippage,
        'liquidity_floor_multiple': liquidity_floor_multiple,
        'benchmark_code': benchmark_code,
    }
    for key, override in explicit.items():
        if override is not None:
            setattr(config, key, override)
    for key, override in (config_overrides or {}).items():
        if override is not None:
            setattr(config, key, override)
    config.validate()

    module = resolve_rotation_module(rotation_module)
    source = data_source if data_source is not None else DatabaseDataSource()

    run = _adopt_backtest_row(persist_to, name, config)
    try:
        calendar = _normalize_sessions(
            source.sessions(
                config.start_date - timedelta(days=CALENDAR_WARMUP_CALENDAR_DAYS), config.end_date
            )
        )
        run_sessions = [day for day in calendar if day >= config.start_date]
        if len(run_sessions) < 2:
            raise BacktestDataUnavailable(
                f'ETFDaily has fewer than two sessions in {config.start_date}..{config.end_date}; '
                'the ETF bars must be synced for the window first.'
            )
        warm_sessions = [day for day in calendar if day < config.start_date]
        if len(warm_sessions) < config.liquidity_lookback_sessions:
            # Not fatal: the trailing turnover mean just uses whatever history exists,
            # but a short window means the very first rebalances are screened weakly.
            short_history_note = (
                f'only {len(warm_sessions)} session(s) of ETFDaily history before {config.start_date}; '
                f'the {config.liquidity_lookback_sessions}-session liquidity screen is short on the first rebalance.'
            )
        else:
            short_history_note = ''

        book = PriceBook(source, calendar[0], config.end_date, calendar)
        ctx = _Context(
            config=config,
            module=module,
            book=book,
            source=source,
            predictors={'lgb': lgb_predictor, 'lstm': lstm_predictor},
            calendar=calendar,
            signal_sessions=signal_sessions(run_sessions, config.rebalance_frequency),
        )
        if short_history_note:
            ctx.errors.append(short_history_note)

        cursor = _restore_state(run, ctx)
        for chunk_start in range(cursor, len(run_sessions), config.chunk_trading_days):
            chunk_end = min(chunk_start + config.chunk_trading_days, len(run_sessions))
            # Load everything this chunk can touch in one query: the held legs (marked
            # every session), the queued plan, and the defensive sleeve. Codes first
            # selected inside the chunk are batch-loaded by ``_build_plan`` instead.
            needed = set(ctx.state.positions) | {config.defensive_asset}
            if ctx.state.pending_plan:
                needed |= set(ctx.state.pending_plan['target_weights'])
            book.ensure(needed)
            for index in range(chunk_start, chunk_end):
                if ctx.state.pending_plan:
                    book.ensure(ctx.state.pending_plan['target_weights'])
                _run_session(ctx, run_sessions, index)
            ctx.state.cursor = chunk_end
            if run is not None:
                _persist_progress(run, ctx, chunk_end, len(run_sessions))

        finalized = _finalize_metrics(ctx, config, source, run_sessions)
        positions = [
            *ctx.state.closed_trades,
            *[
                {
                    'ts_code': lot.ts_code,
                    'name': lot.name,
                    'category': lot.category,
                    'signal_date': _iso(lot.signal_date),
                    'entry_date': _iso(lot.entry_date),
                    'exit_date': None,
                    'entry_price': lot.entry_price,
                    'exit_price': None,
                    'weight': lot.target_weight,
                    'shares': lot.shares,
                    'pnl': None,
                    'return_pct': None,
                    'market_value': lot.market_value(),
                    'position_fees_paid': lot.fees_paid,
                    'position_dividend_cash': lot.dividend_cash,
                    'status': 'OPEN',
                }
                for lot in ctx.state.positions.values()
            ],
        ]
        report = finalized['report']
        report['resumed_from_chunk'] = int(cursor)
        result = {
            'metrics': finalized['metrics'],
            'equity_curve': ctx.state.equity_curve,
            'trade_log': ctx.state.trade_log,
            'positions': positions,
            'parameters': config.to_parameters(),
            'report': report,
            'summary': finalized['summary'],
        }
        _persist_completion(run, ctx, result)
        return result
    except Exception as error:
        if run is not None:
            _persist_failure(run, error)
        raise
