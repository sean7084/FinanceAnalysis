"""Feature engineering for the ETF momentum-rotation module.

The load-bearing constraint is the information set. A feature on date D may only read
bars with ``date <= D``; a label on date D may only read the bar at the N-th session
after D. Everything is built on one wide frame (rows = trading sessions, columns =
``etf_id``) so the constraint is enforced by the operators themselves: ``shift(N)`` and
``rolling(N)`` only travel backwards along the session axis, and the one ``shift(-N)`` in
this module is the forward return, isolated in :func:`build_forward_return` so it cannot
leak into a feature. Cross-sectional ranks use ``rank(axis=1)``, which reads a single row
at a time -- a rank on date D is therefore a function of date D's eligible pool and of
nothing else.

Deliberately not reused: ``apps.analytics.tasks`` already computes RSI/Bollinger/SMA, but
every entry point there is keyed on ``asset_id`` against the ``Asset``+``OHLCV`` stock
tables. ETFs do not live in ``Asset`` -- they have their own ``ETFUniverse``/``ETFDaily``
precisely so a fund is not mistaken for a stock -- so those wrappers cannot see a single
``ETFDaily`` row and the TA-Lib calls are made directly here (0.6.8).
"""

import logging
import math
import time
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterator, Sequence
from datetime import date, datetime, timedelta
from typing import NamedTuple

import numpy as np
import pandas as pd
import talib
from django.conf import settings

from apps.markets.models import BenchmarkIndexDaily

from .models import ETFDaily, ETFFeatureSnapshot, ETFUniverse


logger = logging.getLogger(__name__)


# The whitelist ``ETFFeatureSnapshot`` is written from, in model declaration order.
# Nothing may derive this list from ``_meta.fields``: the label columns are model fields
# too, and the whole point of the split is that a training frame can be assembled without
# a feature ever reading its own future.
FEATURE_COLUMNS: tuple[str, ...] = (
    'mom_5d',
    'mom_10d',
    'mom_20d',
    'mom_60d',
    'mom_120d',
    'mom_250d',
    'mom_accel',
    'vol_20d',
    'vol_60d',
    'sharpe_20d',
    'sharpe_60d',
    'rs_vs_benchmark_20d',
    'rs_vs_benchmark_60d',
    'corr_with_benchmark_60d',
    'rank_pct_20d',
    'rank_pct_60d',
    'rsi_14',
    'ma5_ratio',
    'ma20_ratio',
    'ma60_ratio',
    'bollinger_pos',
    'drawdown_from_high_60d',
    'avg_amount_20d',
    'volume_ratio_5d_20d',
    # Creation/redemption flow, joined from ``ETFShareDaily``: 95.8% of eligible rows have it,
    # so unlike the index-valuation columns these belong in the model input.
    'share_growth_5d',
    'share_growth_20d',
    'flow_x_mom',
    'has_share_flow',
)

#: Index-valuation columns: computed and stored, but **not** part of ``FEATURE_COLUMNS``.
#: TuShare's ``index_dailybasic`` only covers six broad indices, which reaches 12% of the
#: eligible pool -- far below the 70% pre-registered floor -- so a model trained on them
#: would learn "this fund has a broad-benchmark mapping" rather than a valuation signal.
#: The plumbing stays (mapping table, valuation table, :func:`attach_index_valuation`) so a
#: source with real coverage can switch them on by moving these names into the tuple above.
INDEX_VALUATION_COLUMNS: tuple[str, ...] = (
    'idx_pe_pct_3y', 'idx_pb_pct_3y', 'idx_turnover_z', 'has_index_valuation',
)

#: Constituent-breadth columns (P30), attached from ``IndexWeightSnapshot`` + ``markets.OHLCV``.
#: Independent of ``FEATURE_COLUMNS`` for the same reason as the valuation family: whether a
#: model may use them is a coverage question measured after the backfill, not an assumption.
INDEX_BREADTH_COLUMNS: tuple[str, ...] = (
    'idx_share_above_ma60', 'idx_ret_dispersion_20d', 'idx_mom_consistency',
    'has_index_breadth',
)

#: Columns produced by an attach function after :func:`assemble_rows`, not by the price
#: builders. They still have to reach the snapshot row, so both the model builder and the
#: upsert's ``update_fields`` read this tuple.
ATTACHED_FEATURE_COLUMNS: tuple[str, ...] = (
    *INDEX_VALUATION_COLUMNS, *INDEX_BREADTH_COLUMNS,
)

#: Share-flow columns. Attached after the price builders from ``ETFShareDaily`` rather than
#: derived from the price panel, and part of ``FEATURE_COLUMNS`` because the backfill covers
#: 95.8% of eligible rows.
SHARE_FLOW_COLUMNS: tuple[str, ...] = (
    'share_growth_5d', 'share_growth_20d', 'flow_x_mom', 'has_share_flow',
)
#: Columns in the tuple above that are attached after the price builders rather than derived
#: from them. Kept separate from ``SHARE_FLOW_COLUMNS`` only to make the exemption lists read
#: clearly; today they are the same four names.
SHARE_FLOW_ATTACHED = SHARE_FLOW_COLUMNS


LABEL_COLUMNS: tuple[str, ...] = (
    'fwd_return', 'label_rank_pct', 'label_top_bucket',
    # Downside labels. The selector arms lost on return while winning on drawdown, so the
    # next hypothesis is that the score belongs in a *filter* that predicts risk rather than
    # in a selector that predicts return.
    'fwd_max_drawdown', 'fwd_downside_deviation', 'label_dd_rank',
)
# ``label_top_bucket`` is derived from ``label_rank_pct`` at assembly time, so these are the
# label columns that exist as frames in the computed feature dictionary.
LABEL_FRAME_COLUMNS: tuple[str, ...] = (
    'fwd_return', 'label_rank_pct', 'fwd_max_drawdown', 'fwd_downside_deviation', 'label_dd_rank',
)

LABEL_HORIZON_DAYS_DEFAULT = 20
MIN_LISTING_AGE_DAYS_DEFAULT = 183
MIN_AVG_AMOUNT_YUAN_DEFAULT = 50_000_000
WRITE_BATCH_SIZE_DEFAULT = 2000

MOMENTUM_SESSIONS = (5, 10, 20, 60, 120, 250)
MOM_ACCELERATION_SHORT_SESSIONS = 20
MOM_ACCELERATION_LONG_SESSIONS = 60
VOLATILITY_SESSIONS = (20, 60)
RELATIVE_STRENGTH_SESSIONS = (20, 60)
RANK_SESSIONS = (20, 60)
CORRELATION_SESSIONS = 60
RSI_PERIOD = 14
BOLLINGER_PERIOD = 20
BOLLINGER_STD_DEVIATION = 2
MA_PERIODS = (5, 20, 60)
MA_RATIO_WINDOWS = {f'ma{period}_ratio': period for period in MA_PERIODS}
DRAWDOWN_SESSIONS = 60
AMOUNT_SESSIONS = 20
VOLUME_RATIO_SHORT_SESSIONS = 5
VOLUME_RATIO_LONG_SESSIONS = 20

TRADING_DAYS_PER_YEAR = 252
SESSIONS_PER_YEAR = 242
TOP_BUCKET_RANK_CUTOFF = 0.70
BENCHMARK_INDEX_CODE = 'H00905.CSI'
FEATURE_VERSION = 1

# Warm-up is sized to the longest lookback so a chunk's first output date sees the same
# history it would have seen mid-chunk. RSI's Wilder recursion is path-dependent, so this
# also has to be long enough for the recursion to converge: 250 sessions decays an initial
# condition to (1 - 1/14)^250, which is below float64 relevance.
WARMUP_SESSIONS = max(MOMENTUM_SESSIONS)
WARMUP_BUFFER_DAYS = 15

# Longest session run kept in memory and converted to model instances at one time.
ROWS_PER_WRITE_BLOCK = 100_000

# Feature -> the number of sessions its window must be completely traded in. ``rolling``
# with the default ``min_periods=window`` already does this for the pandas maths; TA-Lib
# needs the same mask applied afterwards or the two families disagree after a suspension.
TALIB_FEATURE_WINDOWS = {
    'rsi_14': RSI_PERIOD,
    'bollinger_pos': BOLLINGER_PERIOD,
    **MA_RATIO_WINDOWS,
}

_UNKNOWN_LIST_ORDINAL = 0
_NO_DELIST_ORDINAL = np.iinfo(np.int64).max


class Panel(NamedTuple):
    """One chunk's bars as wide frames. Columns are ``etf_id``, rows are trading sessions.

    ``prices``/``highs`` are dividend-adjusted (``adj_close`` and ``high * adj_factor``):
    an unadjusted close reads a distribution as a -3% momentum spike, and adjusted close is
    the only series here that is the return a holder could actually have earned.
    """

    prices: pd.DataFrame
    highs: pd.DataFrame
    volumes: pd.DataFrame
    amounts: pd.DataFrame


def sessions_to_calendar_days(sessions: int, buffer_days: int = WARMUP_BUFFER_DAYS) -> int:
    """Calendar span covering ``sessions`` trading sessions, rounded up with a buffer.

    Load windows are filtered on calendar days because that is what the ORM understands;
    the buffer keeps a holiday run (Spring Festival is ~9 sessions) from truncating the
    warm-up and quietly NULLing the first feature dates of a chunk.
    """
    return int(math.ceil(int(sessions) * 365.25 / SESSIONS_PER_YEAR)) + buffer_days


def iter_calendar_year_chunks(start_date: date, end_date: date) -> Iterator[tuple[date, date]]:
    """Split ``[start_date, end_date]`` by calendar year.

    One year of output is ~243 sessions x ~2100 funds, which keeps the wide frame and every
    intermediate rolling copy inside a few hundred MB; the whole panel in one frame would
    not fit, and per-date recomputation would repeat the same rolling maths ~250 times.
    """
    current = start_date
    while current <= end_date:
        chunk_end = min(date(current.year, 12, 31), end_date)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def _divide(numerator: pd.DataFrame, denominator: pd.DataFrame) -> pd.DataFrame:
    """Element-wise ratio that treats a zero denominator as missing rather than infinite.

    A flat 20-day series gives a zero Bollinger width and a zero-volume fund gives a zero
    mean volume; either would otherwise store ``inf``, which is neither a number nor NULL
    and is rejected on the way into a ``FloatField``.
    """
    with np.errstate(divide='ignore', invalid='ignore'):
        result = numerator / denominator
    return result.replace([np.inf, -np.inf], np.nan)


def build_forward_return(prices: pd.DataFrame, label_horizon_days: int) -> pd.DataFrame:
    """``fwd_return`` at D = ``adj_close[D+N] / adj_close[D] - 1`` for the N-th session after D.

    THE ONLY FORWARD-LOOKING EXPRESSION IN THIS MODULE. Kept separate from
    :func:`build_feature_frame`'s feature maths so a reviewer has one line to audit. Where
    the D+N bar does not exist -- the fund delisted, or D sits within N sessions of the end
    of the panel -- the result stays NaN and is written as NULL: never backfilled from a
    later session, never zeroed.
    """
    return prices.shift(-int(label_horizon_days)) / prices - 1.0


def build_forward_drawdown(prices: pd.DataFrame, label_horizon_days: int) -> pd.DataFrame:
    """Largest peak-to-trough fall over the ``N`` sessions *after* each date, as a magnitude.

    ``md(D) = max_k (peak(D..D+k) - P(D+k)) / peak(D..D+k)`` for ``k`` in ``1..N``, with the
    peak starting at ``D`` itself. Positive numbers: 0.05 means the fund fell 5 percent from
    its running high somewhere inside the horizon. FORWARD-LOOKING, like
    :func:`build_forward_return`, and NaN where the window is incomplete.
    """
    horizon = int(label_horizon_days)
    peak = prices.copy()
    drawdown = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for offset in range(1, horizon + 1):
        path = prices.shift(-offset)
        peak = peak.where(peak >= path, path)
        fall = (peak - path) / peak
        drawdown = drawdown.where(drawdown >= fall, fall)
    return drawdown.where(prices.shift(-horizon).notna())


def build_forward_downside_deviation(prices: pd.DataFrame, label_horizon_days: int) -> pd.DataFrame:
    """Root-mean-square of the *negative* forward daily returns over the horizon.

    Upside days contribute zero, so this measures how violently the fund lost, not how much
    it moved. Same forward-window rule and NaN policy as :func:`build_forward_drawdown`.
    """
    horizon = int(label_horizon_days)
    # ``fill_method=None``: padding a missing bar into a 0% return would invent an
    # observation, and a downside label built from invented mild days understates the risk.
    returns = prices.pct_change(fill_method=None)
    total = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for offset in range(1, horizon + 1):
        falling = returns.shift(-offset).clip(upper=0.0)
        total = total.add(falling.fillna(0.0) ** 2)
    deviation = np.sqrt(total / float(horizon))
    return deviation.where(prices.shift(-horizon).notna())


def build_momentum_features(prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """``mom_Nd`` = ``adj_close[D] / adj_close[D-N] - 1`` for each session count in
    :data:`MOMENTUM_SESSIONS`, on the dividend-adjusted series.

    ``mom_accel`` is short-horizon momentum minus long-horizon momentum
    (``mom_20d - mom_60d``): positive when the most recent 20 sessions have outrun the
    trailing 60, i.e. the trend is steepening rather than merely intact.
    """
    features = {f'mom_{sessions}d': prices / prices.shift(sessions) - 1.0 for sessions in MOMENTUM_SESSIONS}
    features['mom_accel'] = (
        features[f'mom_{MOM_ACCELERATION_SHORT_SESSIONS}d']
        - features[f'mom_{MOM_ACCELERATION_LONG_SESSIONS}d']
    )
    return features


def build_risk_features(log_returns: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Annualised volatility and Sharpe ratio from daily *log* returns.

    Log rather than simple returns, consistently with :func:`build_benchmark_features`,
    because the correlation is over the same series. Annualisation is ``sqrt(252)`` and the
    Sharpe ratio uses a zero risk-free rate -- a rotation model ranks on the risk-adjusted
    *relative* score, and a borrowed rate constant would only add a term that cancels in
    the cross-section.
    """
    features = {}
    for sessions in VOLATILITY_SESSIONS:
        deviation = log_returns.rolling(sessions).std()
        annualisation = math.sqrt(TRADING_DAYS_PER_YEAR)
        features[f'vol_{sessions}d'] = deviation * annualisation
        features[f'sharpe_{sessions}d'] = (
            _divide(log_returns.rolling(sessions).mean(), deviation) * annualisation
        )
    return features


def build_benchmark_features(
    momentum_features: dict[str, pd.DataFrame],
    log_returns: pd.DataFrame,
    benchmark_close: pd.Series,
) -> dict[str, pd.DataFrame]:
    """Relative strength and correlation against ``H00905.CSI`` (CSI 500 total return).

    ``rs_vs_benchmark_Nd`` is the fund's N-session return minus the index's N-session
    return, so it is an apples-to-apples difference of the same two endpoints.
    ``corr_with_benchmark_60d`` is the rolling 60-session correlation of daily log returns.

    ``benchmark_close`` is aligned to the fund session grid with NaN where the index feed
    has no bar, which makes the affected cells NULL instead of raising: the index is synced
    by ``apps.markets`` and its coverage is not guaranteed to match the fund feed.
    """
    features = {}
    for sessions in RELATIVE_STRENGTH_SESSIONS:
        benchmark_momentum = benchmark_close / benchmark_close.shift(sessions) - 1.0
        # ``axis=0`` is not optional: ``DataFrame - Series`` aligns on columns by default,
        # which here would silently broadcast the index series across the wrong axis.
        features[f'rs_vs_benchmark_{sessions}d'] = momentum_features[
            f'mom_{sessions}d'
        ].subtract(benchmark_momentum, axis=0)

    benchmark_log_returns = np.log(benchmark_close).diff()
    features['corr_with_benchmark_60d'] = log_returns.rolling(CORRELATION_SESSIONS).corr(benchmark_log_returns)
    return features


def build_liquidity_features(amounts: pd.DataFrame, volumes: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """``avg_amount_20d`` in 元 and the 5/20 session mean-volume ratio.

    ``ETFDaily.amount`` is already converted to 元 at ingest (TuShare reports 千元), so the
    20-session mean needs no scale factor here -- and it is the same series the eligibility
    liquidity floor tests against, so the two can never drift apart.
    """
    return {
        'avg_amount_20d': amounts.rolling(AMOUNT_SESSIONS).mean(),
        'volume_ratio_5d_20d': _divide(
            volumes.rolling(VOLUME_RATIO_SHORT_SESSIONS).mean(),
            volumes.rolling(VOLUME_RATIO_LONG_SESSIONS).mean(),
        ),
    }


def build_drawdown_feature(prices: pd.DataFrame, highs: pd.DataFrame) -> pd.DataFrame:
    """``close / rolling_max(high, 60) - 1`` in adjusted space, so always ``<= 0``.

    The window contains the current session, and ``high >= close`` on every bar, so the
    ratio can only be 0 at a 60-session high. The clip is float round-off insurance, not a
    change of meaning.
    """
    return (prices / highs.rolling(DRAWDOWN_SESSIONS).max() - 1.0).clip(upper=0.0)


def _talib_column_values(prices: pd.DataFrame) -> dict[str, np.ndarray]:
    """RSI / Bollinger / SMA from TA-Lib, run down one fund column at a time.

    TA-Lib is a 1-D library, and 0.6.8 stops emitting values after the first NaN in its
    input (verified against the installed build). A fund that did not trade some session of
    the grid is therefore handed its *own* contiguous session sequence and the results are
    written back onto the grid rows it came from, so a suspension early in the loaded window
    cannot blank the rest of the panel for that fund.
    """
    matrix = prices.to_numpy(dtype='float64')
    columns = matrix.shape[1]
    produced = {name: np.full(matrix.shape, np.nan) for name in TALIB_FEATURE_WINDOWS}
    for position in range(columns):
        column = matrix[:, position]
        traded = ~np.isnan(column)
        if not traded.any():
            continue
        series = column[traded]
        upper, _middle, lower = talib.BBANDS(
            series, timeperiod=BOLLINGER_PERIOD, nbdevup=BOLLINGER_STD_DEVIATION, nbdevdn=BOLLINGER_STD_DEVIATION,
        )
        width = upper - lower
        with np.errstate(divide='ignore', invalid='ignore'):
            band_position = np.where(width > 0, (series - lower) / width, np.nan)
        produced['rsi_14'][traded, position] = talib.RSI(series, timeperiod=RSI_PERIOD)
        produced['bollinger_pos'][traded, position] = band_position
        for period in MA_PERIODS:
            produced[f'ma{period}_ratio'][traded, position] = talib.SMA(series, timeperiod=period)
    return produced


def build_talib_features(prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """TA-Lib moving-average *ratios*, RSI and Bollinger position, masked to full windows.

    The ``ma_N_ratio`` columns are ``close / SMA(close, N) - 1`` -- the raw SMA is not a
    feature and the ratio is scale-free, which is what makes it comparable across funds
    priced 0.7 and 6.0. The mask re-applies the ``min_periods=window`` rule the pandas
    rolling features get for free, so a fund that missed a session inside the window is
    NULL here too rather than quietly computing over a hole.
    """
    coverage = _window_coverage(prices)
    features = {}
    for name, values in _talib_column_values(prices).items():
        frame = pd.DataFrame(values, index=prices.index, columns=prices.columns)
        if name in MA_RATIO_WINDOWS:
            frame = _divide(prices, frame) - 1.0
        features[name] = frame.where(coverage[TALIB_FEATURE_WINDOWS[name]])
    return features


def _window_coverage(prices: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """Per-window mask: True only where every one of the last N sessions was traded.

    Derived from the bar grid rather than from each feature's own NaN output, because TA-Lib
    consumes the fund's contiguous sequence and so cannot report "this window had a hole"
    itself.
    """
    traded = prices.notna().astype('int64')
    return {window: traded.rolling(window).sum() >= window for window in sorted(set(TALIB_FEATURE_WINDOWS.values()))}


def build_feature_frame(
    *,
    prices: pd.DataFrame,
    highs: pd.DataFrame,
    volumes: pd.DataFrame,
    amounts: pd.DataFrame,
    benchmark_close: pd.Series,
    label_horizon_days: int,
) -> dict[str, pd.DataFrame]:
    """Every feature a fund's own history determines, plus the raw forward return.

    Excludes the cross-sectional ranks and ``is_eligible``, which need the whole date's pool
    -- see :func:`build_cross_sectional_ranks` and :func:`build_eligibility_frame`.

    ``prices`` is coerced to ">0 or missing" here so the function is safe on hand-built
    frames as well as on loaded ones: a zero or negative price would otherwise produce
    ``log(0) = -inf`` and an infinite Sharpe that survives straight into the database.
    """
    prices = prices.where(prices > 0)
    log_returns = np.log(prices).diff()
    features = build_momentum_features(prices)
    features.update(build_risk_features(log_returns))
    features.update(build_benchmark_features(features, log_returns, benchmark_close))
    features.update(build_talib_features(prices))
    features.update(build_liquidity_features(amounts, volumes))
    features['drawdown_from_high_60d'] = build_drawdown_feature(prices, highs)
    features['fwd_return'] = build_forward_return(prices, label_horizon_days)
    features['fwd_max_drawdown'] = build_forward_drawdown(prices, label_horizon_days)
    features['fwd_downside_deviation'] = build_forward_downside_deviation(
        prices, label_horizon_days,
    )
    return features


def build_eligibility_frame(
    *,
    master: pd.DataFrame,
    avg_amount_20d: pd.DataFrame,
    min_listing_age_days: int,
    min_avg_amount_yuan: float,
) -> pd.DataFrame:
    """The pool filter evaluated on every (session, fund) cell, as a boolean frame.

    Point-in-time on purpose: listed at least ``min_listing_age_days`` *calendar* days
    before D (the same ``timedelta`` units and ``effective_list_date`` = ``list_date`` or
    ``first_trade_date`` fallback as ``is_point_in_time_eligible``), not yet delisted at
    D, ``is_rankable``, and rolling-20-session mean ``amount`` strictly above
    ``min_avg_amount_yuan``. Two things it is *not*: it is not ``is_active`` (today's
    survivors would score momentum only on the funds that worked out), and the age bound
    is a minimum, not a band -- a fund never leaves the pool for being too old. The pool
    is small in 2016-2018 and ~500 today; that is the market, not a bug.

    Written as integer/date ordinal comparisons over the grid because the alternative is
    one ``eligible_universe`` query per session, ~2600 of them for a full rebuild.
    """
    sessions = np.fromiter(
        (session.toordinal() for session in avg_amount_20d.index), dtype='int64', count=len(avg_amount_20d.index),
    )
    aligned = master.reindex(avg_amount_20d.columns)
    listed_on = _day_ordinals(list(aligned['effective_list_date']), _UNKNOWN_LIST_ORDINAL)
    delisted_on = _day_ordinals(list(aligned['delist_date']), _NO_DELIST_ORDINAL)
    rankable = aligned['is_rankable'].fillna(False).to_numpy(dtype=bool)

    with np.errstate(invalid='ignore'):
        liquid = avg_amount_20d.to_numpy() > float(min_avg_amount_yuan)
    listed_known = listed_on != _UNKNOWN_LIST_ORDINAL
    old_enough = (sessions[:, None] - listed_on[None, :]) >= int(min_listing_age_days)
    still_trading = delisted_on[None, :] > sessions[:, None]

    return pd.DataFrame(
        listed_known[None, :] & old_enough & still_trading & rankable[None, :] & liquid,
        index=avg_amount_20d.index,
        columns=avg_amount_20d.columns,
    )


def _day_ordinals(values: Sequence[date | None], sentinel: int) -> np.ndarray:
    """Day ordinals with ``sentinel`` standing in for a missing date.

    The sentinel keeps the eligibility test one vectorised integer comparison: a fund with
    no listing date can never pass ``old_enough``, a fund with no delisting date can never
    fail ``still_trading``. Anything that is not a real date counts as missing -- including
    ``NaN``, which is what ``reindex`` leaves for an unknown ``etf_id``, and ``NaT``, which
    passes ``isinstance(value, date)`` but refuses ``toordinal()``.
    """
    ordinals = np.full(len(values), sentinel, dtype='int64')
    for position, value in enumerate(values):
        if isinstance(value, date) and value == value:
            ordinals[position] = value.toordinal()
    return ordinals


def build_cross_sectional_ranks(
    features: dict[str, pd.DataFrame],
    eligibility: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """``rank_pct_20d``/``rank_pct_60d``/``label_rank_pct`` as per-date percentiles.

    ``rank(axis=1)`` compares one session row at a time, so the ranking universe is the
    funds eligible on *that* date -- never the panel. Funds outside the pool are masked to
    NaN first, so they neither occupy a rank nor shift anybody else's. Percentiles are
    ascending ``rank / count`, so the best momentum/forward return is 1.0 and a pool of one
    ranks 1.0.
    """
    ranks = {
        f'rank_pct_{sessions}d': features[f'mom_{sessions}d'].where(eligibility).rank(axis=1, pct=True)
        for sessions in RANK_SESSIONS
    }
    ranks['label_rank_pct'] = features['fwd_return'].where(eligibility).rank(axis=1, pct=True)
    # Safety rank: the *smallest* drawdown ranks 1.0, so every label keeps the engine's
    # convention that a higher score is better and a veto can keep dropping the bottom share.
    ranks['label_dd_rank'] = (-features['fwd_max_drawdown']).where(eligibility).rank(axis=1, pct=True)
    return ranks


def _require_complete_feature_set(features: dict[str, pd.DataFrame]) -> None:
    """Fail loudly if :data:`FEATURE_COLUMNS` and the builders have drifted apart.

    A silently missing column would be written as NULL, which in a training frame looks
    like "this fund was flat that month" rather than "nobody implemented the feature".
    """
    built = (
        set(features) - set(LABEL_FRAME_COLUMNS)
        - set(INDEX_VALUATION_COLUMNS) - set(SHARE_FLOW_COLUMNS)
    )
    missing = [
        name for name in FEATURE_COLUMNS
        if name not in built
        and name not in INDEX_VALUATION_COLUMNS
        and name not in SHARE_FLOW_COLUMNS
    ]
    unexpected = sorted(built - set(FEATURE_COLUMNS))
    if missing or unexpected:
        raise ValueError(f'feature frame does not match FEATURE_COLUMNS: missing={missing} unexpected={unexpected}')


def assemble_rows(
    *,
    features: dict[str, pd.DataFrame],
    eligibility: pd.DataFrame,
    traded: pd.DataFrame,
    label_horizon_days: int,
) -> pd.DataFrame:
    """Long form of a slice: ``etf_id``, ``date``, the features, ``is_eligible``, the labels.

    A row exists only where the fund actually traded that session -- a (fund, session) cell
    with no bar has no feature vector and would be pure NULL padding in the snapshot table.
    A *label* exists only where the forward bar was found and the fund was in that date's
    eligible pool, which is exactly where ``label_rank_pct`` is non-NULL;
    ``label_top_bucket`` and ``label_horizon_days`` are NULL wherever the rank is, so no row
    can carry a top-bucket flag or a horizon with nothing measured. ``fwd_return`` itself is
    kept for every traded row, eligible or not: it is a price fact, and it is what makes a
    dropped-out-of-the-pool fund auditable after the fact.
    """
    _require_complete_feature_set(features)
    session_count = len(traded.index)
    keep = np.flatnonzero(traded.to_numpy().ravel(order='F'))
    if len(keep):
        # A Fortran-order flat index into (sessions, funds) is ``row + column * sessions``,
        # so the quotient is the fund and the remainder is the session.
        column_positions, row_positions = np.divmod(keep, session_count)
    else:
        row_positions = column_positions = np.empty(0, dtype='int64')

    rows = pd.DataFrame(index=range(len(keep)))
    # ``order='F'`` walks each fund's whole session run before the next fund, so rows arrive
    # in (etf, date) order -- the order the ``(etf, date)`` unique index wants to insert in.
    rows.insert(0, 'etf_id', traded.columns.to_numpy()[column_positions])
    rows.insert(1, 'date', np.asarray(traded.index, dtype=object)[row_positions])

    # Index-valuation columns are attached per block from `IndexValuationDaily`; they are
    # not in the price-derived `features` dict, so the assembly skips them here.
    for name in (*FEATURE_COLUMNS, *LABEL_FRAME_COLUMNS):
        if name in INDEX_VALUATION_COLUMNS or name in SHARE_FLOW_COLUMNS:
            continue
        rows[name] = features[name].to_numpy(dtype='float64').ravel(order='F')[keep]
    rows['is_eligible'] = eligibility.to_numpy().ravel(order='F')[keep]

    ranks = rows['label_rank_pct'].to_numpy(dtype='float64')
    labeled = (~np.isnan(ranks)).tolist()
    with np.errstate(invalid='ignore'):
        buckets = (ranks >= TOP_BUCKET_RANK_CUTOFF).tolist()
    # Explicit ``object`` dtype and native ``bool``/``int``/``None``: pandas would otherwise
    # infer float64 from an [int, None] list and turn "no label" into NaN, which
    # ``PositiveIntegerField`` cannot render and ``BooleanField`` cannot answer.
    rows['label_top_bucket'] = pd.Series(
        [bucket if is_labeled else None for bucket, is_labeled in zip(buckets, labeled)],
        index=rows.index, dtype=object,
    )
    rows['label_horizon_days'] = pd.Series(
        [int(label_horizon_days) if is_labeled else None for is_labeled in labeled],
        index=rows.index, dtype=object,
    )
    return rows


def _none_if_na(values: np.ndarray) -> np.ndarray:
    """NaN/NaT -> ``None`` for a column of model values.

    SQLite and PostgreSQL both reject NaN in a ``FloatField`` on the direct-copy paths, and
    where they accept it, ``NaN`` reads as a number while this module means "unknown".
    """
    return np.where(pd.isna(values), None, values)


def build_snapshot_models(rows: pd.DataFrame, *, metadata: dict) -> list[ETFFeatureSnapshot]:
    """Convert an assembled block into unsaved ``ETFFeatureSnapshot`` instances."""
    feature_cells = list(zip(*[_none_if_na(rows[name].to_numpy()) for name in FEATURE_COLUMNS]))
    attached_cells = list(
        zip(*[_none_if_na(rows[name].to_numpy()) for name in ATTACHED_FEATURE_COLUMNS])
    )
    label_cells = list(zip(*[_none_if_na(rows[name].to_numpy()) for name in LABEL_COLUMNS]))
    etf_ids = rows['etf_id'].to_numpy()
    sessions = rows['date'].to_numpy()
    # ``_none_if_na`` again, so a column that reached here as float64 NaN is still NULL and
    # never ``int(nan)`` -- the horizon is only ever a session count or nothing.
    horizons = _none_if_na(rows['label_horizon_days'].to_numpy())
    eligible = rows['is_eligible'].to_numpy()

    snapshots = []
    for position in range(len(rows)):
        snapshots.append(
            ETFFeatureSnapshot(
                etf_id=int(etf_ids[position]),
                date=sessions[position],
                # Keyword expansion straight off FEATURE_COLUMNS is the only path from the
                # frame into the model, so feature membership is declared, never inferred.
                **dict(zip(FEATURE_COLUMNS, feature_cells[position])),
                **dict(zip(ATTACHED_FEATURE_COLUMNS, attached_cells[position])),
                is_eligible=bool(eligible[position]),
                label_horizon_days=(int(horizons[position]) if horizons[position] is not None else None),
                **dict(zip(LABEL_COLUMNS, label_cells[position])),
                metadata=metadata,
            )
        )
    return snapshots


def _upsert(snapshots: Sequence[ETFFeatureSnapshot], *, batch_size: int, update_fields: Sequence[str]) -> None:
    """Idiom copied from ``apps.markets.tasks``: a re-run of a window overwrites.

    ``update_conflicts`` against the ``(etf, date)`` unique constraint is what makes the
    command restartable; without it the second pass raises, and ``ignore_conflicts`` would
    silently keep stale features instead of refreshing them.
    """
    ETFFeatureSnapshot.objects.bulk_create(
        list(snapshots),
        batch_size=int(batch_size),
        update_conflicts=True,
        update_fields=list(update_fields),
        unique_fields=['etf', 'date'],
    )


def _full_update_fields() -> list[str]:
    return [
        *FEATURE_COLUMNS, *ATTACHED_FEATURE_COLUMNS,
        'is_eligible', 'label_horizon_days', *LABEL_COLUMNS, 'metadata',
    ]


def _labels_update_fields() -> list[str]:
    # Feature columns are deliberately untouched: a labels-only pass must not be able to
    # NULL a feature that the original run computed.
    return ['label_horizon_days', *LABEL_COLUMNS]


def _plain_date(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def attach_share_flow(block: pd.DataFrame) -> pd.DataFrame:
    """Creation/redemption flow features from ``ETFShareDaily``, as of the previous session.

    ETF shares change when authorised participants create or redeem, so the change *is* the
    flow -- unlike a stock's share count, which only moves on issuance. ``fund_share`` is
    published with a one-session lag, so every row is shifted to the next session before it is
    joined.

    Two traps this function exists to avoid, both silent on a small sample:

    * The block's session list is sparse, so many history dates map onto one session. The join
      therefore keeps only the **latest** row per (fund, session) -- otherwise it is a
      many-to-many merge and the block comes back with thousands of duplicated rows.
    * A fund with no history keeps NaN and ``has_share_flow=0``; zeroing it would read as
      "no creations or redemptions", which is a different statement.
    """
    from apps.etf_rotation.models import ETFShareDaily

    block = block.copy()
    etf_ids = sorted(set(int(value) for value in block['etf_id'].tolist()))
    dates = sorted(set(block['date'].tolist()))
    frames = ETFShareDaily.objects.filter(
        etf_id__in=etf_ids, date__lte=dates[-1],
    ).values('etf_id', 'date', 'shares')
    history = pd.DataFrame(list(frames))
    if history.empty:
        block['share_growth_5d'] = np.nan
        block['share_growth_20d'] = np.nan
        block['flow_x_mom'] = np.nan
        block['has_share_flow'] = False
        return block

    history['date'] = pd.to_datetime(history['date']).dt.date
    history = history.sort_values(['etf_id', 'date'])
    grouped = history.groupby('etf_id', sort=False)['shares']
    history['share_growth_5d'] = grouped.pct_change(5)
    history['share_growth_20d'] = grouped.pct_change(20)

    session_index = block['date'].drop_duplicates().sort_values().reset_index(drop=True)
    sessions = session_index.to_numpy()
    positions = np.searchsorted(sessions, history['date'].to_numpy(), side='right')
    usable = positions < len(sessions)
    history = history.loc[usable].copy()
    history['session'] = sessions[positions[usable]]
    # One row per (fund, session): the last publication strictly before that session.
    history = history.drop_duplicates(subset=['etf_id', 'session'], keep='last')

    history['date_key'] = pd.to_datetime(history['session']).dt.strftime('%Y-%m-%d')
    block['date_key'] = pd.to_datetime(block['date']).dt.strftime('%Y-%m-%d')
    merged = block.merge(
        history[['etf_id', 'date_key', 'share_growth_5d', 'share_growth_20d']],
        on=['etf_id', 'date_key'], how='left', validate='many_to_one',
    ).drop(columns=['date_key'])
    if len(merged) != len(block):  # pragma: no cover - the right side is deduped above
        raise ValueError('share-flow join multiplied the block; the right side was not unique')
    momentum = pd.to_numeric(merged.get('mom_20d'), errors='coerce')
    merged['flow_x_mom'] = merged['share_growth_20d'] * momentum
    merged['has_share_flow'] = merged['share_growth_20d'].notna()
    return merged


def attach_index_valuation(block: pd.DataFrame) -> pd.DataFrame:
    """Add the index-valuation columns to an assembled block, as of the previous session.

    ``index_dailybasic`` is published after the close, so every value is shifted one session
    before it is joined: a signal generated on D must not use D's own valuation. Percentiles
    run over each index's trailing 750 rows and the turnover z-score over 250, all computed
    on that index's own history rather than across indices -- an ETF tracking a 40x-PE
    sector is not expensive relative to a 10x bank index.

    A fund with no mapping, or a date before its index's valuation history starts, keeps NaN
    and ``has_index_valuation=0``. That flag exists so the coverage bias can be measured
    later instead of being silently mixed into the feature.
    """
    from apps.etf_rotation.models import ETFIndexMapping, IndexValuationDaily

    mapping = dict(ETFIndexMapping.objects.values_list('etf_id', 'index_code'))
    block = block.copy()
    if not mapping:
        block['idx_pe_pct_3y'] = np.nan
        block['idx_pb_pct_3y'] = np.nan
        block['idx_turnover_z'] = np.nan
        block['has_index_valuation'] = False
        return block

    dates = sorted(set(block['date'].tolist()))
    session_index = block['date'].drop_duplicates().sort_values().reset_index(drop=True)
    frames = IndexValuationDaily.objects.filter(
        index_code__in=set(mapping.values()), date__lte=dates[-1],
    ).values('index_code', 'date', 'pe_ttm', 'pb', 'turnover_rate')
    valuation = pd.DataFrame(list(frames))
    if valuation.empty:
        block['idx_pe_pct_3y'] = np.nan
        block['idx_pb_pct_3y'] = np.nan
        block['idx_turnover_z'] = np.nan
        block['has_index_valuation'] = False
        return block

    valuation['date'] = pd.to_datetime(valuation['date']).dt.date
    valuation = valuation.sort_values(['index_code', 'date'])
    grouped = valuation.groupby('index_code', sort=False)
    # ``rolling.rank(pct=True)`` is the C path; a Python lambda over 750-row windows would
    # take minutes on this panel.
    valuation['idx_pe_pct_3y'] = grouped['pe_ttm'].transform(
        lambda series: series.rolling(750, min_periods=250).rank(pct=True)
    )
    valuation['idx_pb_pct_3y'] = grouped['pb'].transform(
        lambda series: series.rolling(750, min_periods=250).rank(pct=True)
    )
    turnover = grouped['turnover_rate']
    mean = turnover.transform(lambda series: series.rolling(250, min_periods=120).mean())
    std = turnover.transform(lambda series: series.rolling(250, min_periods=120).std())
    valuation['idx_turnover_z'] = (valuation['turnover_rate'] - mean) / std.replace(0.0, np.nan)
    # PIT: the row published on D is only usable from D+1. ``searchsorted`` keeps this O(n).
    sessions = session_index.to_numpy()
    positions = np.searchsorted(sessions, valuation['date'].to_numpy(), side='right')
    usable = positions < len(sessions)
    valuation = valuation.loc[usable].copy()
    valuation['session'] = sessions[positions[usable]]
    # One row per (index, session): the block's session list is sparse, so many valuation
    # dates map onto one session. Without this the merge is many-to-many and the block comes
    # back with duplicated (etf, date) keys.
    valuation = valuation.drop_duplicates(subset=['index_code', 'session'], keep='last')
    valuation['date_key'] = pd.to_datetime(valuation['session']).dt.strftime('%Y-%m-%d')
    # Join on the *index*, not on a reversed etf map: 170 ETFs can track one index, and a
    # ``{index_code: etf}`` dict keeps only one of them.
    merged = block.assign(
        index_code=block['etf_id'].map(mapping),
        date_key=pd.to_datetime(block['date']).dt.strftime('%Y-%m-%d'),
    ).merge(
        valuation[['index_code', 'date_key', 'idx_pe_pct_3y', 'idx_pb_pct_3y', 'idx_turnover_z']],
        on=['index_code', 'date_key'], how='left', validate='many_to_one',
    ).drop(columns=['date_key', 'index_code'])
    if len(merged) != len(block):  # pragma: no cover - guarded by the dedupe above
        raise ValueError('index-valuation join multiplied the block; right side not unique')
    merged['has_index_valuation'] = merged['idx_pe_pct_3y'].notna()
    return merged


def attach_index_breadth(block: pd.DataFrame) -> pd.DataFrame:
    """Constituent breadth of the tracked index, computed as of the previous session.

    Three facts no fund-level feature can see: how much of the index sits above its own
    60-session mean, how dispersed constituent returns are, and whether the constituents
    agree with the index's direction. Weights come from the latest ``index_weight``
    publication at or before **D-1** (monthly, published after the close), and every price
    series is shifted one session for the same reason as the valuation family.

    A fund whose index has no weights, or a session where less than half the index weight has
    usable prices, keeps NaN with ``has_index_breadth=0``: a breadth number computed from a
    fifth of an index is not the index's breadth, and quietly mixing it in is how a coverage
    artefact starts looking like a signal.
    """
    from apps.etf_rotation.models import ETFIndexMapping, IndexWeightSnapshot
    from apps.markets.models import Asset, OHLCV

    block = block.copy()
    empty = {
        'idx_share_above_ma60': np.nan, 'idx_ret_dispersion_20d': np.nan,
        'idx_mom_consistency': np.nan, 'has_index_breadth': False,
    }
    mapping = dict(ETFIndexMapping.objects.values_list('etf_id', 'index_code'))
    if not mapping:
        return block.assign(**empty)
    block['index_code'] = block['etf_id'].map(mapping)
    wanted = sorted({code for code in block['index_code'].dropna().unique() if code})
    if not wanted:
        return block.assign(**empty).drop(columns=['index_code'])

    sessions = pd.Index(sorted(block['date'].unique()))
    start, end = sessions.min(), sessions.max()
    # Only the snapshots that can be the "latest publication at or before D" are loaded: one
    # year back covers the monthly cadence with room for a missed publication, and the whole
    # history (millions of rows) would be re-read once per chunk for no additional answer.
    weights = pd.DataFrame(list(
        IndexWeightSnapshot.objects.filter(
            index_code__in=wanted,
            trade_date__gte=start - timedelta(days=400),
            trade_date__lte=end,
        ).values('index_code', 'con_code', 'trade_date', 'weight')
    ))
    if weights.empty:
        return block.assign(**empty).drop(columns=['index_code'])
    weights = weights.dropna(subset=['weight'])
    weights['trade_date'] = pd.to_datetime(weights['trade_date']).dt.date

    warm_start = start - timedelta(days=sessions_to_calendar_days(120))
    asset_ids = dict(
        Asset.objects.filter(ts_code__in=set(weights['con_code'])).values_list('ts_code', 'id')
    )
    bars = pd.DataFrame(list(
        OHLCV.objects.filter(
            asset_id__in=set(asset_ids.values()), date__gte=warm_start, date__lte=end,
        ).values('asset_id', 'date', 'adj_close')
    ))
    if bars.empty:
        return block.assign(**empty).drop(columns=['index_code'])
    bars['adj_close'] = pd.to_numeric(bars['adj_close'], errors='coerce').astype('float64')
    prices = (
        bars.pivot_table(index='date', columns='asset_id', values='adj_close', aggfunc='last')
        .sort_index()
    )
    prices = prices.where(prices > 0)
    # PIT: every input a session D uses is D-1's bar, so the price itself is shifted too --
    # comparing D's close against D-1's mean would leak the session being priced.
    asof = prices.shift(1)
    ma60 = asof.rolling(60, min_periods=40).mean()
    ret20 = asof / asof.shift(20) - 1.0

    panels = []
    weights['asset_id'] = weights['con_code'].map(asset_ids)
    weights = weights.dropna(subset=['asset_id'])
    weights['asset_id'] = weights['asset_id'].astype('int64')
    for code in wanted:
        subset = weights[weights['index_code'] == code]
        if subset.empty:
            continue
        weight_frame = subset.pivot_table(
            index='trade_date', columns='asset_id', values='weight', aggfunc='last',
        ).sort_index()
        # A monthly snapshot published on T is usable from T+1; ffill then shift one session.
        weight_frame = weight_frame.reindex(index=prices.index, method='ffill').shift(1)
        columns = [column for column in weight_frame.columns if column in prices.columns]
        if not columns:
            continue
        weight_frame = weight_frame[columns]
        price_frame, mean_frame, return_frame = asof[columns], ma60[columns], ret20[columns]
        mask = weight_frame.notna() & price_frame.notna() & mean_frame.notna() & return_frame.notna()
        weight_sum = weight_frame.where(mask).sum(axis=1)
        # Less than half the index weight priced is not the index's breadth.
        usable = weight_sum > 0.5
        share = ((price_frame > mean_frame) & mask).mul(weight_frame).sum(axis=1) / weight_sum
        mean_return = (return_frame.where(mask) * weight_frame).sum(axis=1) / weight_sum
        variance = (
            (return_frame.sub(mean_return, axis=0) ** 2).where(mask) * weight_frame
        ).sum(axis=1) / weight_sum
        agrees = (return_frame.gt(0) == mean_return.gt(0).to_numpy()[:, None]) & mask
        consistency = agrees.mul(weight_frame).sum(axis=1) / weight_sum
        frame = pd.DataFrame({
            'idx_share_above_ma60': share.where(usable),
            'idx_ret_dispersion_20d': np.sqrt(variance.where(usable)),
            'idx_mom_consistency': consistency.where(usable),
        }).reindex(sessions).rename_axis('date').reset_index()
        frame['index_code'] = code
        panels.append(frame)

    if not panels:
        return block.assign(**empty).drop(columns=['index_code'])
    panel = pd.concat(panels, ignore_index=True)
    merged = block.merge(panel, on=['index_code', 'date'], how='left', validate='many_to_one')
    if len(merged) != len(block):  # pragma: no cover - guarded by the per-index uniqueness
        raise ValueError('index-breadth join multiplied the block; right side not unique')
    merged['has_index_breadth'] = merged['idx_share_above_ma60'].notna()
    return merged.drop(columns=['index_code'])


def _load_master() -> pd.DataFrame:
    """Pool-filter master data for every fund, indexed by ``etf_id``.

    ``effective_list_date`` is replicated from :attr:`ETFUniverse.effective_list_date`
    because the filter runs as an array comparison; the property would need 2100 object
    loads per chunk to say the same thing.
    """
    records = list(
        ETFUniverse.objects.values_list('id', 'is_rankable', 'list_date', 'first_trade_date', 'delist_date'),
    )
    frame = pd.DataFrame.from_records(
        records, columns=['etf_id', 'is_rankable', 'list_date', 'first_trade_date', 'delist_date'],
    ).set_index('etf_id')
    frame['effective_list_date'] = frame['list_date'].where(frame['list_date'].notna(), frame['first_trade_date'])
    return frame


def _load_panel(load_start: date, load_end: date) -> Panel:
    """Bars for one load window, pivoted into wide frames by a single ``unstack``.

    The panel is ~5.5M rows over 2600 sessions and ~2100 funds, so the shape is fixed here
    and the maths downstream is vectorised: the per-fund loop that this replaces is the one
    thing the module may not afford.
    """
    records = list(
        ETFDaily.objects.filter(date__gte=load_start, date__lte=load_end)
        .values_list('etf_id', 'date', 'high', 'adj_close', 'adj_factor', 'volume', 'amount')
        .iterator(chunk_size=100_000),
    )
    empty = pd.DataFrame(index=pd.Index([], name='date'), columns=pd.Index([], dtype='int64'))
    if not records:
        return Panel(empty.copy(), empty.copy(), empty.copy(), empty.copy())

    frame = pd.DataFrame.from_records(
        records, columns=['etf_id', 'date', 'high', 'adj_close', 'adj_factor', 'volume', 'amount'],
    )
    del records
    for column in ('high', 'adj_close', 'adj_factor', 'amount'):
        frame[column] = frame[column].astype('float64')
    frame['adj_high'] = frame['high'] * frame['adj_factor']
    wide = (
        frame.set_index(['date', 'etf_id'])[['adj_close', 'adj_high', 'volume', 'amount']]
        .sort_index()
        .unstack('etf_id')
    )
    # ``sort_index`` returned ``datetime.date`` labels; keep them plain dates so the slice
    # positions, the ordinal eligibility maths and the model field all agree on one type.
    wide.index = pd.Index([_plain_date(session) for session in wide.index], name='date')

    prices = wide['adj_close']
    non_positive = int((prices <= 0).sum().sum())
    if non_positive:
        logger.warning('Dropped %s non-positive adj_close cells in %s..%s.', non_positive, load_start, load_end)
    return Panel(
        prices=prices.where(prices > 0),
        highs=wide['adj_high'],
        volumes=wide['volume'],
        amounts=wide['amount'],
    )


def _load_benchmark(load_start: date, load_end: date, sessions: pd.Index) -> pd.Series:
    """``H00905.CSI`` closes reindexed onto the fund session grid.

    Missing index sessions stay NaN, which NULLs the relative-strength and correlation
    features for exactly those sessions; the count is logged by the caller rather than
    silently tolerated.
    """
    records = list(
        BenchmarkIndexDaily.objects.filter(
            index_code=BENCHMARK_INDEX_CODE,
            trade_date__gte=load_start,
            trade_date__lte=load_end,
        ).values_list('trade_date', 'close'),
    )
    if not records:
        return pd.Series(dtype='float64').reindex(sessions)
    frame = pd.DataFrame.from_records(records, columns=['trade_date', 'close'])
    frame['close'] = frame['close'].astype('float64')
    series = frame.set_index('trade_date')['close'].sort_index()
    return series.reindex(sessions)


def _load_window(chunk_start: date, chunk_end: date, *, label_horizon_days: int) -> tuple[date, date]:
    """Read window for a chunk: warm-up history in front, ``N`` label sessions behind.

    Only the labels read past ``chunk_end``, and only to find the D+N bar; features for the
    rows that get written are still computed from ``date <= D`` alone.
    """
    return (
        chunk_start - timedelta(days=sessions_to_calendar_days(WARMUP_SESSIONS)),
        chunk_end + timedelta(days=sessions_to_calendar_days(label_horizon_days)),
    )


def _iter_chunk_blocks(
    *,
    chunk_start: date,
    chunk_end: date,
    master: pd.DataFrame,
    label_horizon_days: int,
    min_listing_age_days: int,
    min_avg_amount_yuan: float,
    stats: dict,
) -> Iterator[pd.DataFrame]:
    """Yield write-sized long-form blocks for one calendar-year chunk.

    The wide frames are built once for the whole load window and every block is a row slice
    of them, so block-level writing costs a view per block rather than a re-read per block.
    """
    load_start, load_end = _load_window(chunk_start, chunk_end, label_horizon_days=label_horizon_days)
    panel = _load_panel(load_start, load_end)
    prices = panel.prices
    stats['benchmark_missing_sessions'] = 0
    if prices.empty:
        logger.warning('No ETFDaily bars found for %s..%s; chunk skipped.', load_start, load_end)
        return

    benchmark_close = _load_benchmark(load_start, load_end, prices.index)
    # The load window includes the warm-up and label tails, so this counts the grid sessions
    # the index feed missed over the whole window, not the rows written.
    stats['benchmark_missing_sessions'] = int(benchmark_close.isna().sum())
    if stats['benchmark_missing_sessions']:
        logger.warning(
            '%s of %s sessions in %s..%s have no %s bar; rs/corr features are NULL there.',
            stats['benchmark_missing_sessions'], len(prices.index), load_start, load_end, BENCHMARK_INDEX_CODE,
        )

    features = build_feature_frame(
        prices=prices,
        highs=panel.highs,
        volumes=panel.volumes,
        amounts=panel.amounts,
        benchmark_close=benchmark_close,
        label_horizon_days=label_horizon_days,
    )
    eligibility = build_eligibility_frame(
        master=master,
        avg_amount_20d=features['avg_amount_20d'],
        min_listing_age_days=min_listing_age_days,
        min_avg_amount_yuan=min_avg_amount_yuan,
    )
    features.update(build_cross_sectional_ranks(features, eligibility))
    traded = prices.notna()

    grid = list(prices.index)
    first = bisect_left(grid, chunk_start)
    last = bisect_right(grid, chunk_end)
    stats['sessions'] += last - first
    stats['funds'] += len(prices.columns)

    dates_per_block = max(1, ROWS_PER_WRITE_BLOCK // max(1, len(prices.columns)))
    for start in range(first, last, dates_per_block):
        stop = min(start + dates_per_block, last)
        block = assemble_rows(
            features={name: frame.iloc[start:stop] for name, frame in features.items()},
            eligibility=eligibility.iloc[start:stop],
            traded=traded.iloc[start:stop],
            label_horizon_days=label_horizon_days,
        )
        if not block.empty:
            yield block


def _filter_existing_snapshots(block: pd.DataFrame) -> pd.DataFrame:
    """Keep only rows that already have a snapshot, for ``labels_only``.

    ``update_conflicts`` inserts when there is nothing to conflict with, so an unfiltered
    labels-only pass would create feature-NULL snapshot rows.
    """
    existing = set(
        ETFFeatureSnapshot.objects.filter(
            date__gte=block['date'].min(),
            date__lte=block['date'].max(),
        ).values_list('etf_id', 'date'),
    )
    keep = np.fromiter(
        ((fund, session) in existing for fund, session in zip(block['etf_id'], block['date'])),
        dtype=bool,
        count=len(block),
    )
    return block[keep]


def resolve_pool_parameters(label_horizon_days, min_listing_age_days, min_avg_amount_yuan):
    """Settings win over the signature defaults; an explicit argument wins over both.

    Other modules call :func:`compute_features` with the literal defaults declared in its
    signature, so "the caller did not choose" is only detectable by comparing against that
    same literal -- which is why ``getattr(settings, ...)`` is applied conditionally here
    instead of as an argument default (a default is evaluated at import time, before the
    app registry and any ``override_settings`` are in effect).
    """
    if label_horizon_days == LABEL_HORIZON_DAYS_DEFAULT:
        label_horizon_days = getattr(settings, 'ETF_LABEL_HORIZON_DAYS', LABEL_HORIZON_DAYS_DEFAULT)
    if min_listing_age_days == MIN_LISTING_AGE_DAYS_DEFAULT:
        min_listing_age_days = getattr(settings, 'ETF_MIN_LISTING_AGE_DAYS', MIN_LISTING_AGE_DAYS_DEFAULT)
    if min_avg_amount_yuan == MIN_AVG_AMOUNT_YUAN_DEFAULT:
        min_avg_amount_yuan = getattr(settings, 'ETF_MIN_AVG_AMOUNT_YUAN', MIN_AVG_AMOUNT_YUAN_DEFAULT)

    label_horizon_days = int(label_horizon_days)
    if label_horizon_days < 1:
        raise ValueError('label_horizon_days must be at least 1 trading session.')
    return label_horizon_days, int(min_listing_age_days), float(min_avg_amount_yuan)


def compute_features(
    start_date: date,
    end_date: date,
    *,
    label_horizon_days: int = LABEL_HORIZON_DAYS_DEFAULT,
    min_listing_age_days: int = MIN_LISTING_AGE_DAYS_DEFAULT,
    min_avg_amount_yuan: float = MIN_AVG_AMOUNT_YUAN_DEFAULT,
    batch_size: int = WRITE_BATCH_SIZE_DEFAULT,
    dry_run: bool = False,
    labels_only: bool = False,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """Compute the point-in-time feature frame and upsert ``ETFFeatureSnapshot`` rows.

    The only DB-writing entry point in this module. Everything upstream of it is pure
    pandas/TA-Lib maths on a wide frame, chunked by calendar year: read one year's bars
    plus the warm-up history and the ``N`` label sessions in front and behind, compute every
    feature and label for that window, then write it as ``(etf, date)``-conflicting upserts.

    ``labels_only`` still computes the whole frame -- the label rank is defined over the
    point-in-time eligible pool, so re-labelling cannot skip eligibility or the momentum
    features it ranks -- but restricts the writes to the label columns of rows that already
    exist. ``dry_run`` computes and reports, writing nothing.
    """
    label_horizon_days, min_listing_age_days, min_avg_amount_yuan = resolve_pool_parameters(
        label_horizon_days, min_listing_age_days, min_avg_amount_yuan,
    )
    if end_date < start_date:
        raise ValueError(f'end_date {end_date} precedes start_date {start_date}.')

    chunks = list(iter_calendar_year_chunks(start_date, end_date))
    master = _load_master()
    metadata = {'feature_version': FEATURE_VERSION}
    update_fields = _labels_update_fields() if labels_only else _full_update_fields()

    summary = {
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'label_horizon_days': label_horizon_days,
        'min_listing_age_days': min_listing_age_days,
        'min_avg_amount_yuan': min_avg_amount_yuan,
        'dry_run': bool(dry_run),
        'labels_only': bool(labels_only),
        'chunks': len(chunks),
        'benchmark_index_code': BENCHMARK_INDEX_CODE,
    }
    started = time.monotonic()
    rows_computed = rows_written = 0
    eligible_rows = labeled_rows = skipped_rows = benchmark_missing_sessions = 0
    dates_seen: set[date] = set()
    funds_seen: set[int] = set()

    for index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        stats = {'sessions': 0, 'funds': 0, 'benchmark_missing_sessions': 0}
        chunk_rows = chunk_written = 0
        for block in _iter_chunk_blocks(
            chunk_start=chunk_start,
            chunk_end=chunk_end,
            master=master,
            label_horizon_days=label_horizon_days,
            min_listing_age_days=min_listing_age_days,
            min_avg_amount_yuan=min_avg_amount_yuan,
            stats=stats,
        ):
            chunk_rows += len(block)
            if labels_only:
                found = len(block)
                block = _filter_existing_snapshots(block)
                skipped_rows += found - len(block)
            if block.empty:
                continue
            eligible_rows += int(block['is_eligible'].sum())
            labeled_rows += int(block['label_horizon_days'].notna().sum())
            dates_seen.update(block['date'].unique().tolist())
            funds_seen.update(int(fund) for fund in block['etf_id'].unique().tolist())
            chunk_written += len(block)
            # Both feature families live in their own tables and are joined per block.
            block = attach_index_valuation(block)
            block = attach_share_flow(block)
            block = attach_index_breadth(block)
            if not dry_run:
                _upsert(
                    build_snapshot_models(block, metadata=metadata),
                    batch_size=batch_size,
                    update_fields=update_fields,
                )
        rows_computed += chunk_rows
        rows_written += chunk_written
        benchmark_missing_sessions += stats['benchmark_missing_sessions']
        if progress is not None:
            progress({
                'chunk_index': index,
                'chunk_count': len(chunks),
                'start': chunk_start,
                'end': chunk_end,
                'rows': chunk_written,
                'rows_computed': chunk_rows,
                'dates': stats['sessions'],
                'funds': stats['funds'],
                'benchmark_missing_sessions': stats['benchmark_missing_sessions'],
                'dry_run': bool(dry_run),
            })

    if dry_run:
        rows_written = 0
    summary.update({
        'rows_computed': rows_computed,
        'rows_written': rows_written,
        'rows_skipped_missing_snapshot': skipped_rows,
        'eligible_rows': eligible_rows,
        'labeled_rows': labeled_rows,
        'dates': len(dates_seen),
        'etfs': len(funds_seen),
        'benchmark_missing_sessions': benchmark_missing_sessions,
        'elapsed_seconds': round(time.monotonic() - started, 3),
    })
    logger.info('ETF feature computation finished: %s', summary)
    return summary
