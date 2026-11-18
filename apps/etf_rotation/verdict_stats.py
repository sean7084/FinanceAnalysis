"""Pre-registered verdict statistics for the ETF rotation instrument (#136).

Why this module exists: the old verdict was a MEDIAN of six block Sharpes. Measured from the
ledger's own ``results.blocks``, the within-start sd of a block Sharpe is ~1.006, so the
standard error of a six-block mean is ~0.411 -- and a median of six is worse still. The
instrument therefore could not resolve the 0.1-0.5 effects the research is chasing, and no
re-layout of the blocks fixes that: ``WALK_FORWARD_MIN_FOLD_REBALANCES = 20`` means 25 blocks
would need ~500 rebalance events while a panel holds ~210.

The fix is the statistic, not the layout. ``rank_ic.block_rank_ic`` already returns
``per_date_ic`` -- a few hundred daily Spearman ICs per block -- and the stock side has the
pattern to port (``apps/prediction/tasks_lightgbm.py`` ``_holdout_metrics``: ICIR and a t-stat
on daily ICs). Daily ICs give orders of magnitude more observations than six block medians.

FROZEN CONSTANTS. These are pre-registered, not tunable at run time. Changing any of them
requires a NEW write-once pre-registration; the old one is void on edit.
"""

from __future__ import annotations

from math import sqrt
from statistics import mean, stdev

#: Newey-West truncation lag. The label horizon is ETF_LABEL_HORIZON_DAYS = 20, so forward
#: returns overlap by 19 sessions and the daily IC series is MA(19)-correlated. A naive
#: t-statistic can be inflated by up to sqrt(20) ~ 4.47x. Newey-West with lag 20 and a
#: Bartlett kernel exploits the actual decay instead of assuming worst-case unit MA
#: coefficients, which keeps power at n ~ 1405. The alternative -- declaring the t uncorrected
#: and raising the threshold by sqrt(20) -- was rejected in the pre-registration.
NW_LAG = 20

#: Primary criterion: Newey-West t on the mean daily IC, per panel start.
T_MIN = 2.0

#: Two-sided 95% normal quantile, used for the secondary Sharpe gate's confidence bound.
SHARPE_CI_Z = 1.96

#: Minimum daily observations before any t-statistic is reported at all. Below this the
#: Newey-West long-run variance is not estimable in any meaningful sense.
MIN_OBSERVATIONS = 40

#: Dispersion below this is numerically degenerate, not "very stable". A constant-growth
#: equity curve produces returns that differ only by float rounding (~1e-16), which would
#: otherwise yield a Sharpe of ~1e13 and an SE of ~1e11 -- a fake number, not a measurement.
MIN_DISPERSION = 1e-10


def _clean(per_date_ic) -> list[float]:
    if not isinstance(per_date_ic, dict):
        return []
    values = []
    for key in sorted(per_date_ic):
        value = per_date_ic[key]
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def newey_west_se(values: list[float], lag: int = NW_LAG) -> float | None:
    """Standard error of the MEAN under MA(lag) correlation, Bartlett kernel.

    Returns None when there are too few observations. The long-run variance can come out
    negative on short or oddly-shaped samples; that is reported as None rather than clamped
    to zero, because a zero SE would manufacture an infinite t-statistic.
    """
    n = len(values)
    if n < MIN_OBSERVATIONS or n < 2:
        return None
    mu = mean(values)
    errors = [value - mu for value in values]
    gamma0 = sum(error * error for error in errors) / n
    long_run = gamma0
    effective_lag = min(int(lag), n - 1)
    for offset in range(1, effective_lag + 1):
        weight = 1.0 - offset / (effective_lag + 1.0)
        gamma = sum(errors[i] * errors[i - offset] for i in range(offset, n)) / n
        long_run += 2.0 * weight * gamma
    if long_run <= 0:
        return None
    return sqrt(long_run / n)


def ic_stats(per_date_ic) -> dict:
    """ICIR, naive t and Newey-West t for one block's (or one panel's) daily IC series."""
    values = _clean(per_date_ic)
    n = len(values)
    if n < 2:
        return {'n': n, 'mean_ic': None, 'sd': None, 'icir': None,
                'naive_t': None, 'nw_se': None, 'nw_t': None}
    mu = mean(values)
    spread = stdev(values)  # ddof=1, matching the stock-side pattern
    degenerate = spread <= MIN_DISPERSION
    icir = (mu / spread) if not degenerate else None
    naive_t = (mu / (spread / sqrt(n))) if not degenerate else None
    nw_se = newey_west_se(values)
    nw_t = (mu / nw_se) if nw_se else None
    return {'n': n, 'mean_ic': mu, 'sd': spread, 'icir': icir,
            'naive_t': naive_t, 'nw_se': nw_se, 'nw_t': nw_t}


def paired_ic_stats(arm_per_date_ic, baseline_per_date_ic) -> dict:
    """Same-day arm-minus-baseline IC differences.

    Legitimate because ``walk_forward.combinatorial_purged_folds`` documents that
    ``panel_tail_days`` overrides the tail WITHOUT changing the purge, so arms measured with
    different purges land on the same blocks. Differencing on the shared dates removes the
    common market term. Dates present in only one arm are dropped, and the count of dropped
    dates is reported so a silent sample shrink cannot pass as a paired test.
    """
    arm = arm_per_date_ic if isinstance(arm_per_date_ic, dict) else {}
    base = baseline_per_date_ic if isinstance(baseline_per_date_ic, dict) else {}
    shared = sorted(set(arm) & set(base))
    differences = {day: float(arm[day]) - float(base[day]) for day in shared
                   if isinstance(arm[day], (int, float)) and isinstance(base[day], (int, float))}
    stats = ic_stats(differences)
    stats['shared_dates'] = len(shared)
    stats['arm_only_dates'] = len(set(arm) - set(base))
    stats['baseline_only_dates'] = len(set(base) - set(arm))
    return stats


def random_effects_meta(estimates) -> dict:
    """DerSimonian-Laird random-effects pooling across panel starts.

    ``estimates`` is an iterable of (mean, se) pairs. Returns the pooled mean, its SE, tau^2
    (between-start variance) and I^2 (share of total variance that is between-start).

    HONESTY CAVEAT, frozen into the pre-registration: when tau^2 dominates, pooling cannot
    manufacture independence. A large I^2 means the starts are measuring different things,
    and the pooled number is a description of that heterogeneity, not a sharper estimate of
    one common effect. Report tau^2 and I^2 alongside the pooled mean, always.
    """
    pairs = [(float(mu), float(se)) for mu, se in estimates
             if mu is not None and se is not None and se > 0]
    k = len(pairs)
    if k == 0:
        return {'k': 0, 'pooled': None, 'pooled_se': None, 'tau2': None, 'i2': None}
    weights = [1.0 / (se * se) for _, se in pairs]
    total_weight = sum(weights)
    fixed = sum(w * mu for w, (mu, _) in zip(weights, pairs)) / total_weight
    q = sum(w * (mu - fixed) ** 2 for w, (mu, _) in zip(weights, pairs))
    df = k - 1
    c = total_weight - sum(w * w for w in weights) / total_weight
    tau2 = max(0.0, (q - df) / c) if (k > 1 and c > 0) else 0.0
    re_weights = [1.0 / (se * se + tau2) for _, se in pairs]
    re_total = sum(re_weights)
    pooled = sum(w * mu for w, (mu, _) in zip(re_weights, pairs)) / re_total
    pooled_se = sqrt(1.0 / re_total)
    i2 = max(0.0, (q - df) / q) if (q > 0 and k > 1) else 0.0
    return {'k': k, 'pooled': pooled, 'pooled_se': pooled_se, 'tau2': tau2, 'i2': i2}


def sharpe_secondary_gate(point_estimate, se) -> dict:
    """Secondary gate: net-of-fee Sharpe must be positive AND its 95% CI must not straddle 0.

    Owner's wording was "point estimate > 0 and its SE bound does not cross 0"; the reading
    frozen here is the stricter one -- the lower confidence bound must be > 0, i.e.
    ``point - 1.96 * se > 0``. This is deliberately NOT the block-median statistic: an IC
    signal can be significant while the strategy loses money after fees, so significance
    alone must never approve an arm.
    """
    if point_estimate is None or se is None:
        return {'passed': None, 'point': point_estimate, 'se': se, 'lower_bound': None}
    lower = float(point_estimate) - SHARPE_CI_Z * float(se)
    return {'passed': bool(point_estimate > 0 and lower > 0),
            'point': float(point_estimate), 'se': float(se), 'lower_bound': lower}


def sharpe_se_from_equity(equity_curve, lag: int = NW_LAG) -> float | None:
    """Standard error of a block's Sharpe, from its SESSION-level equity returns.

    Deliberately not derived from block medians: the owner's secondary gate requires an SE
    from return observations, and the equity curve gives one per session -- finer than the
    rebalance-event series and available in the cpcv path (``result['equity_curve']``).

    IID part is Lo (2002): SE(SR) ~ sqrt((1 + 0.5 SR^2) / n) for per-period Sharpe. Because
    overlapping 20-session labels induce MA(19) correlation, the IID SE is inflated by the
    Bartlett-weighted autocorrelation sum, the same kernel and lag as the IC t-statistic.
    Returns None when there are too few sessions or the adjustment is not positive.
    """
    if not isinstance(equity_curve, list) or len(equity_curve) < MIN_OBSERVATIONS:
        return None
    values = []
    for point in equity_curve:
        equity = point.get('equity') if isinstance(point, dict) else None
        if isinstance(equity, (int, float)) and equity > 0:
            values.append(float(equity))
    if len(values) < MIN_OBSERVATIONS:
        return None
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    n = len(returns)
    if n < MIN_OBSERVATIONS:
        return None
    mu = mean(returns)
    spread = stdev(returns)
    if spread <= MIN_DISPERSION:
        return None
    sharpe = mu / spread
    iid_variance = (1.0 + 0.5 * sharpe * sharpe) / n
    errors = [value - mu for value in returns]
    gamma0 = sum(error * error for error in errors) / n
    if gamma0 <= 0:
        return None
    effective_lag = min(int(lag), n - 1)
    inflation = 1.0
    for offset in range(1, effective_lag + 1):
        weight = 1.0 - offset / (effective_lag + 1.0)
        gamma = sum(errors[i] * errors[i - offset] for i in range(offset, n)) / n
        inflation += 2.0 * weight * (gamma / gamma0)
    if inflation <= 0:
        return None
    return sqrt(iid_variance * inflation)


def panel_sharpe_gate(block_sharpes, block_ses) -> dict:
    """Secondary gate on the MEAN of disjoint block Sharpes, with a propagated SE.

    Blocks are disjoint contiguous windows, so their Sharpes are independent estimates and
    the SE of their mean is sqrt(sum(se^2)) / k. This is a mean-with-SE, NOT the median of
    six that the old verdict used, and each block's SE comes from session-level returns.
    """
    pairs = [(float(s), float(se)) for s, se in zip(block_sharpes or [], block_ses or [])
             if s is not None and se is not None and se > 0]
    if not pairs:
        return sharpe_secondary_gate(None, None)
    k = len(pairs)
    point = mean(s for s, _ in pairs)
    se = sqrt(sum(se * se for _, se in pairs)) / k
    return sharpe_secondary_gate(point, se)


def primary_reject(nw_t, mean_ic) -> bool:
    """Primary criterion is REJECT-not-select: an arm must clear BOTH to avoid rejection.

    Failing to clear it does not select anything; it only declines to reject. P35 stays
    no-go and P38 becomes adjudicable rather than permanently closed.
    """
    if nw_t is None or mean_ic is None:
        return False
    return bool(nw_t >= T_MIN and mean_ic > 0)
