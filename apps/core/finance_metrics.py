"""Shared performance metrics for backtest reporting, free of database and app imports.

WHY THIS MODULE EXISTS: these numbers existed nowhere in the repository as reusable
functions. The only implementations were module-private helpers in
``apps/backtest/tasks.py`` (``_calc_max_drawdown`` ~L2099, ``_calc_sharpe`` ~L2114,
``TRADING_DAYS`` ~L75) which cannot be imported without dragging that module's Celery
task graph and its A-share point-in-time universe coupling along with them, and the
remainder (Calmar, alpha/beta, information ratio, annualised turnover, win rate) were
computed nowhere at all. Every engine that needs a Sharpe ratio should get the same
number from the same series, so the maths lives here once.

The private copies in ``apps/backtest/tasks.py`` are intentionally left in place --
that engine's Decimal-native ledger is out of scope for consolidation, and touching it
would change published A-share backtest results. This module is a port, not a refactor.

Conventions that must not drift:

* 252 trading sessions per year, annualised with ``sqrt(252)`` (``TRADING_DAYS``).
* ``max_drawdown`` is the POSITIVE magnitude of the worst peak-to-trough fall, in
  fraction-of-equity units, exactly like ``_calc_max_drawdown``. Callers that want a
  signed drawdown series negate it themselves.
* Sharpe divides by the POPULATION stdev (``ddof=0``), matching ``_calc_sharpe``'s use
  of ``statistics.pstdev``, and is clamped to +/-10 for the same reason the source
  clamps it: a one-off zero-variance window otherwise produces an absurd magnitude that
  propagates into Calmar.
* Degenerate input never raises. All-NaN, empty, or too-short series yield ``None`` so
  a caller can persist "not measurable" instead of a fabricated 0, which would be
  indistinguishable from "measured, and it was zero".
* No scipy/statsmodels dependency: regression is solved with explicit normal equations
  in numpy, so the numbers do not depend on an optional statistical stack.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

import numpy as np
import pandas as pd


# Mirrors ``apps/backtest/tasks.TRADING_DAYS`` (Decimal) plus its float form for maths.
TRADING_DAYS = Decimal('252')
TRADING_DAYS_PER_YEAR = 252
SHARPE_CLAMP = Decimal('10')

MIN_EQUITY_OBSERVATIONS = 2
MIN_RETURN_OBSERVATIONS = 2
MIN_REGRESSION_OBSERVATIONS = 20


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _finite_series(values: Any) -> pd.Series:
    """Coerce anything returnable-as-numbers into a ``pd.Series`` of finite floats.

    NaN/inf are dropped rather than silently converted to 0: a missing benchmark day or
    a halted ETF must shorten the sample, not inject a fake flat return into a mean.
    """
    if values is None:
        return pd.Series(dtype='float64')
    if isinstance(values, pd.Series):
        series = values
    elif isinstance(values, pd.DataFrame):
        if values.empty:
            return pd.Series(dtype='float64')
        series = values.iloc[:, 0]
    elif isinstance(values, (str, bytes)):
        series = pd.Series([values])
    elif isinstance(values, dict):
        series = pd.Series(list(values.values()))
    elif isinstance(values, (list, tuple, set, frozenset, np.ndarray)) or isinstance(values, Iterable):
        series = pd.Series(list(values))
    else:
        series = pd.Series([values])
    numeric = pd.to_numeric(series, errors='coerce').astype('float64')
    return numeric[np.isfinite(numeric.to_numpy())]


def _aligned_pair(left: Any, right: Any) -> tuple[pd.Series, pd.Series]:
    """Align two return series without inventing observations.

    Labelled indexes are inner-joined (the caller's dates are meaningful). Positional
    input is truncated to the shorter series, which is the only defensible reading of
    two anonymous sequences of different length.
    """
    left_series = _finite_series(left)
    right_series = _finite_series(right)
    if left_series.empty or right_series.empty:
        return pd.Series(dtype='float64'), pd.Series(dtype='float64')

    positional = (
        isinstance(left_series.index, pd.RangeIndex)
        and isinstance(right_series.index, pd.RangeIndex)
    )
    if positional and not left_series.index.equals(right_series.index):
        depth = min(len(left_series), len(right_series))
        return left_series.iloc[:depth].copy(), right_series.iloc[:depth].copy()

    common = left_series.index.intersection(right_series.index)
    if len(common) == 0:
        return pd.Series(dtype='float64'), pd.Series(dtype='float64')
    return left_series.reindex(common).dropna(), right_series.reindex(common).dropna()


def returns_from_equity(equity: Any) -> pd.Series:
    """Session-over-session simple returns of an equity curve (``None``-safe helper).

    Computed as ``v / v.shift(1) - 1`` rather than ``pct_change`` so a gap in the curve
    is not forward-filled into a 0% return, and a zero-valued prior session yields NaN
    (dropped) instead of inf.
    """
    values = _finite_series(equity)
    if len(values) < MIN_EQUITY_OBSERVATIONS:
        return pd.Series(dtype='float64')
    prior = values.shift(1)
    returns = (values / prior.where(prior > 0)) - 1.0
    return returns.dropna()


def max_drawdown(equity: Any) -> float | None:
    """Peak-to-trough magnitude of the worst decline, as a positive fraction.

    Ported from ``apps/backtest.tasks._calc_max_drawdown``.
    """
    values = _finite_series(equity)
    if len(values) < MIN_EQUITY_OBSERVATIONS:
        return None
    running_peak = values.cummax()
    running_peak = running_peak.where(running_peak > 0)
    drawdowns = (1.0 - (values / running_peak)).dropna()
    if drawdowns.empty:
        return None
    worst = _clamp(Decimal(str(float(drawdowns.max()))), Decimal('0'), Decimal('1'))
    return float(worst)


def annualized_return(equity: Any, periods: int = TRADING_DAYS_PER_YEAR) -> float | None:
    """Geometric annualisation of the curve: ``(end/start)**(periods/n) - 1``.

    A curve that ends at or below zero, or that is a single observation, has no
    meaningful annualised return, so it yields ``None`` rather than ``-1`` or ``nan``.
    """
    values = _finite_series(equity)
    if len(values) < MIN_EQUITY_OBSERVATIONS:
        return None
    start = float(values.iloc[0])
    end = float(values.iloc[-1])
    sessions = len(values) - 1
    if start <= 0 or end <= 0 or sessions <= 0:
        return None
    return float((end / start) ** (float(periods) / float(sessions)) - 1.0)


def sharpe_ratio(daily_returns: Any, rf: float = 0.0, periods: int = TRADING_DAYS_PER_YEAR) -> float | None:
    """Annualised Sharpe: mean excess return over population stdev, times ``sqrt(periods)``.

    Ported from ``apps/backtest.tasks._calc_sharpe``, including the population stdev
    (``statistics.pstdev`` there, ``ddof=0`` here) and the +/-10 clamp. Two deliberate
    divergences, both documented because they change published numbers if forgotten:

    * ``rf`` is an ANNUALIZED risk-free rate, converted geometrically to a per-session
      rate. The source had no risk-free term at all (implicitly 0).
    * Fewer than two observations returns ``None``; the source returned ``Decimal(0)``.
      A zero-variance series with >= 2 observations still returns ``0.0`` for parity
      with the source, even though "undefined" would be the more honest answer.
    """
    values = _finite_series(daily_returns)
    if len(values) < MIN_RETURN_OBSERVATIONS:
        return None
    daily_rf = Decimal(str((1.0 + float(rf)) ** (1.0 / float(periods)) - 1.0))
    excess = values.to_numpy(dtype='float64') - float(daily_rf)
    mean_excess = float(np.mean(excess))
    stdev = float(np.std(excess, ddof=0))
    if stdev <= 0.0:
        return 0.0
    ratio = (Decimal(str(mean_excess)) / Decimal(str(stdev))) * Decimal(str(float(periods) ** 0.5))
    return float(_clamp(ratio, -SHARPE_CLAMP, SHARPE_CLAMP))


def calmar_ratio(annual_return: Any, max_drawdown_value: Any) -> float | None:
    """Annualised return divided by the drawdown that produced it.

    ``None`` when the drawdown is zero or unknown: a curve that never fell has an
    unbounded Calmar, and reporting that as ``0.0`` would rank it last instead of first.
    """
    if annual_return is None or max_drawdown_value is None:
        return None
    try:
        numerator = Decimal(str(float(annual_return)))
        denominator = Decimal(str(abs(float(max_drawdown_value))))
    except (TypeError, ValueError):
        return None
    if denominator <= 0:
        return None
    return float(numerator / denominator)


def alpha_beta(
    strategy_returns: Any,
    benchmark_returns: Any,
    periods: int = TRADING_DAYS_PER_YEAR,
) -> dict[str, float | int | None]:
    """OLS factor regression of strategy on benchmark, no scipy/statsmodels required.

    Returns ``{'alpha_daily', 'alpha_annualized', 'beta', 'correlation', 'r_squared',
    'observations'}``, or ``None`` when the aligned sample is shorter than
    ``MIN_REGRESSION_OBSERVATIONS`` or the benchmark has no variance. Alpha is a CAPM
    alpha with a zero risk-free rate (``alpha_daily = mean(s) - beta * mean(b)``),
    annualised linearly by ``periods`` -- the same linear convention the rest of the
    repo uses for per-session to per-year scaling.

    Beta is the slope of the normal equations, ``cov(s,b)/var(b)``, which is what
    ``statsmodels.OLS`` would produce; keeping it in numpy means the metric works in
    whatever minimal environment an engine runs in.
    """
    strategy, benchmark = _aligned_pair(strategy_returns, benchmark_returns)
    observations = len(strategy)
    if observations < MIN_REGRESSION_OBSERVATIONS:
        return None
    strategy_values = strategy.to_numpy(dtype='float64')
    benchmark_values = benchmark.to_numpy(dtype='float64')
    benchmark_centered = benchmark_values - benchmark_values.mean()
    variance = float(np.sum(benchmark_centered ** 2))
    if variance <= 0.0:
        return None

    beta = float(np.sum(benchmark_centered * (strategy_values - strategy_values.mean())) / variance)
    alpha_daily = float(strategy_values.mean() - beta * benchmark_values.mean())
    correlation = float(np.corrcoef(strategy_values, benchmark_values)[0, 1])
    r_squared = float(correlation ** 2) if np.isfinite(correlation) else None
    return {
        'alpha_daily': alpha_daily,
        'alpha_annualized': float(Decimal(str(alpha_daily)) * Decimal(str(periods))),
        'beta': beta,
        'correlation': correlation if np.isfinite(correlation) else None,
        'r_squared': r_squared,
        'observations': observations,
    }


def information_ratio(
    strategy_returns: Any,
    benchmark_returns: Any,
    periods: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """Annualised mean active return over the SAMPLE stdev (``ddof=1``) of that active return.

    Tracking error uses ``ddof=1`` -- the conventional definition, and deliberately not
    the population stdev used in :func:`sharpe_ratio`, because active returns are
    estimated from a finite overlapping window and the source engine's convention only
    applies to its own absolute return series.
    """
    strategy, benchmark = _aligned_pair(strategy_returns, benchmark_returns)
    if len(strategy) < MIN_REGRESSION_OBSERVATIONS:
        return None
    active = (strategy - benchmark).dropna()
    if len(active) < MIN_REGRESSION_OBSERVATIONS:
        return None
    stdev = float(np.std(active.to_numpy(dtype='float64'), ddof=1))
    if stdev <= 0.0:
        return None
    return float(Decimal(str(float(active.mean()))) / Decimal(str(stdev)) * Decimal(str(float(periods) ** 0.5)))


def annualized_turnover(
    daily_traded_notional: Any,
    equity: Any,
    periods: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """One-way traded notional per year divided by mean equity, i.e. portfolio turnover.

    ``daily_traded_notional`` is the per-session sum of BUY and SELL filled notionals
    (both sides count -- a 100% turnover portfolio that rotates out and back in traded
    two times its equity, and a turnover metric that hides that is useless for judging
    whether commission and slippage are affordable at live size).
    """
    traded = _finite_series(daily_traded_notional)
    values = _finite_series(equity)
    if len(values) < MIN_EQUITY_OBSERVATIONS:
        return None
    if traded.empty:
        return 0.0
    if not isinstance(traded.index, pd.RangeIndex) and not isinstance(values.index, pd.RangeIndex):
        # Labelled sessions: a day with no trade is a 0 notional, not a missing sample.
        traded = traded.reindex(values.index).fillna(0.0)
    elif len(traded) != len(values):
        depth = min(len(traded), len(values))
        traded = traded.iloc[:depth]
        values = values.iloc[:depth]
    mean_equity = float(values.mean())
    if mean_equity <= 0.0:
        return None
    sessions = len(values) - 1
    if sessions <= 0:
        return None
    gross = float(traded.clip(lower=0.0).sum())
    return float((Decimal(str(gross)) / Decimal(str(mean_equity))) * (Decimal(str(periods)) / Decimal(str(sessions))))


def win_rate(trade_returns: Any) -> float | None:
    """Fraction of closed trades that made money, on a strictly-positive definition.

    A break-even trade counts as a loss, not as "not a win" that is then dropped from
    the denominator: fees were paid, so it was not a good trade.
    """
    values = _finite_series(trade_returns)
    if values.empty:
        return None
    return float(Decimal(str(int((values > 0).sum()))) / Decimal(str(len(values))))


def payoff_ratio(trade_returns: Any) -> float | None:
    """Average winning trade divided by the absolute average losing trade.

    This is the ``b`` in ``f* = p - (1 - p) / b``, so :func:`win_rate` and this together
    are the two inputs a sizing rule needs; neither alone says anything about whether a
    40%-win-rate strategy is good.

    ``None`` when the sample has no wins or no losses, which is the honest answer: a
    streak of winners has an undefined payoff ratio, and reporting ``inf`` or ``0`` would
    turn a missing measurement into a sizing decision. Break-even trades (exactly 0) are
    dropped from both groups -- they are neither, and counting them as losses would
    understate ``b`` while counting them as wins would overstate the win rate.
    """
    values = _finite_series(trade_returns)
    if values.empty:
        return None
    wins = values[values > 0]
    losses = values[values < 0]
    if wins.empty or losses.empty:
        return None
    average_loss = abs(float(losses.mean()))
    if average_loss <= 0.0:
        return None
    return float(Decimal(str(float(wins.mean()))) / Decimal(str(average_loss)))


def summarize_performance(
    equity: Any,
    daily_traded_notional: Any = None,
    benchmark_returns: Any = None,
    trade_returns: Any = None,
    rf: float = 0.0,
    periods: int = TRADING_DAYS_PER_YEAR,
) -> dict[str, Any]:
    """One-call summary of every metric ``ETFRotationBacktest`` stores a column for.

    Shape (not None) is guaranteed; individual values may be ``None`` when the input
    series were too short to measure. Engines call this instead of re-deriving each
    metric, so the definitions cannot drift between modules.
    """
    values = _finite_series(equity)
    returns = returns_from_equity(values)
    drawdown = max_drawdown(values)
    annualized = annualized_return(values, periods=periods)
    total_return: float | None = None
    if len(values) >= MIN_EQUITY_OBSERVATIONS and float(values.iloc[0]) > 0:
        total_return = float(float(values.iloc[-1]) / float(values.iloc[0]) - 1.0)

    regression: dict[str, Any] | None = None
    active_information: float | None = None
    benchmark_total_return: float | None = None
    if benchmark_returns is not None:
        benchmark_series = _finite_series(benchmark_returns)
        if len(benchmark_series) >= MIN_REGRESSION_OBSERVATIONS:
            regression = alpha_beta(returns, benchmark_series, periods=periods)
            active_information = information_ratio(returns, benchmark_series, periods=periods)
            if float(benchmark_series.iloc[0]) != 0.0:
                benchmark_total_return = float(
                    float((1.0 + benchmark_series).prod()) - 1.0
                )

    return {
        'total_return': total_return,
        'annualized_return': annualized,
        'max_drawdown': drawdown,
        'sharpe_ratio': sharpe_ratio(returns, rf=rf, periods=periods),
        'calmar_ratio': calmar_ratio(annualized, drawdown),
        'win_rate': win_rate(trade_returns),
        'turnover_annual': (
            annualized_turnover(daily_traded_notional, values, periods=periods)
            if daily_traded_notional is not None
            else None
        ),
        'alpha_annualized': None if regression is None else regression['alpha_annualized'],
        'beta': None if regression is None else regression['beta'],
        'r_squared': None if regression is None else regression['r_squared'],
        'correlation': None if regression is None else regression['correlation'],
        'regression_observations': None if regression is None else regression['observations'],
        'information_ratio': active_information,
        'benchmark_return': benchmark_total_return,
        'observations': int(len(returns)),
        'sessions': int(len(values)),
        'rf': float(rf),
        'periods': int(periods),
    }
