"""Simulator-vs-engine reconciliation for the stock walk-forward harness (H2 / D10).

The vectorized top-N simulator is an approximation of the full backtest engine; on
sampled fold windows the two must agree to within a declared tolerance (1bp of NAV)
or the difference must be attributable. The engine run is executed inside
``decision_replay.run_in_rolled_back_transaction`` so the reconciliation persists
nothing — it reads the engine's daily NAV and discards every row the run wrote.

The engine collaborator is injected (a callable returning a date-indexed NAV
Series); the production wiring runs ``run_backtest`` over the fold window. The
comparison itself is pure so it is unit-testable without the engine or data.

(Named engine_reconciliation to avoid the prediction-coverage ``reconciliation``
module, which answers a different question: which stored predictions are missing.)
"""
from decimal import Decimal

import pandas as pd

from apps.backtest.decision_replay import run_in_rolled_back_transaction

DEFAULT_NAV_TOLERANCE = Decimal('0.0001')  # 1bp of NAV


def nav_from_returns(returns):
    """Cumulative NAV (start 1.0) from a daily return series."""
    nav = 1.0
    out = []
    for value in returns:
        nav *= 1.0 + float(value)
        out.append(nav)
    return pd.Series(out, dtype='float64')


def reconcile_nav(sim_nav, engine_nav, *, tolerance=DEFAULT_NAV_TOLERANCE):
    """Align two date-indexed NAV series and report their absolute difference.

    Only dates present in both are compared (a missing engine session is a coverage
    gap, not a silent zero). ``breaches`` lists dates whose |diff| exceeds tolerance.
    """
    sim = pd.Series(sim_nav, dtype='float64')
    engine = pd.Series(engine_nav, dtype='float64')
    common = sim.index.intersection(engine.index)
    if len(common) == 0:
        return {'n_compared': 0, 'max_abs_diff': None, 'mean_abs_diff': None,
                'within_tolerance': False, 'breaches': [], 'coverage_gap': True}
    diff = (sim.loc[common] - engine.loc[common]).abs()
    tol = float(tolerance)
    breaches = [
        {'date': str(idx), 'abs_diff': float(value)}
        for idx, value in diff.items() if value > tol
    ]
    return {
        'n_compared': int(len(common)),
        'max_abs_diff': float(diff.max()),
        'mean_abs_diff': float(diff.mean()),
        'within_tolerance': not breaches,
        'breaches': breaches,
        'coverage_gap': len(common) < len(sim.index),
    }


def run_engine_reconciliation(sim_returns, engine_nav_fn, *, tolerance=DEFAULT_NAV_TOLERANCE):
    """Run the engine in a rolled-back transaction and reconcile its NAV to the sim.

    ``engine_nav_fn`` returns a date-indexed NAV Series; every row it writes is
    discarded by the rollback. The simulator NAV is compounded from its daily
    returns. Returns the reconciliation report (nothing persisted).
    """
    engine_nav = run_in_rolled_back_transaction(engine_nav_fn)
    sim_nav = nav_from_returns(sim_returns)
    if isinstance(engine_nav, pd.Series) and engine_nav.index.name is None and len(engine_nav) == len(sim_nav):
        engine_nav.index = sim_nav.index
    return reconcile_nav(sim_nav, engine_nav, tolerance=tolerance)