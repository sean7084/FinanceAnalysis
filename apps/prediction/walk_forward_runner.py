"""Walk-forward cell orchestration for the stock universe campaign (H2 / D7-D11).

One cell = (arm, panel_start, horizons) over the frozen panel. The runner builds the
fold plan, masks the board-level matrix to the arm, fits each fold through the pure
H1 core, scores the fold's OUT-OF-SAMPLE validation block (never the internal
holdout), simulates the top-N net value, and writes one ledger row per horizon with
the preregistration digest. Fold artifacts are persisted is_active=False.

Heavy collaborators (matrix/label/price provider, fit, predict, simulate, ledger)
are injected so the orchestration is unit-testable without the DB matrix build; the
management command wires the real implementations. The actual campaign run is a
research write gated on the data runs (DR1-7) and the signed preregistration (P1).
"""
from dataclasses import dataclass, field

import pandas as pd

from apps.prediction.prereg import PreregistrationError, validate_cell
from apps.prediction.universe_arms import DEFAULT_CAP_FRACTION, arm_row_mask
from apps.prediction.walk_forward import stock_fold_plan, train_dates_for_fold


@dataclass(frozen=True)
class WalkForwardCell:
    arm: str
    panel_start: object
    panel_end: object
    horizons: tuple
    index_codes: tuple
    feature_names: tuple
    execution_model: str
    simulator_params: dict = field(default_factory=dict)
    cap_fraction: object = DEFAULT_CAP_FRACTION
    schema_version: str = 'v3'
    fold_plan: str = 'expanding-7'


def _up_prob(proba):
    """Directional score P(UP) - P(DOWN) from a (n, 3) DOWN/FLAT/UP probability array.

    This is the SAME score the holdout rank IC uses (`_holdout_metrics`), so the portfolio
    selection aligns with the measured IC/acceptance. Ranking by raw P(UP) alone was
    anti-predictive (gross OOS ~ -12%/yr) while P(UP)-P(DOWN) carried the +0.05 IC signal;
    the selection score must match the IC score or the portfolio contradicts the IC.
    """
    frame = pd.DataFrame(proba)
    return (frame.iloc[:, 2] - frame.iloc[:, 0]).to_numpy()


def _simulator_params_for(params, horizon):
    """Resolve per-horizon simulator params (H2b / O4).

    Accepts the keyed form ``{"common": {...}, "3": {...}, "7": {...}, "30": {...}}``
    (common cost block merged with the horizon-specific selection block) or a flat
    dict applied to every horizon (backward compatible). CORE_PROFILES base differs
    by horizon (h3/h7 top_n=5, h30 top_n=3), so the prereg declares the keyed form.
    """
    if not params:
        return {}
    if 'common' in params or str(horizon) in params:
        merged = dict(params.get('common') or {})
        merged.update(params.get(str(horizon)) or {})
        return merged
    return dict(params)


def run_cell(cell, *, trade_dates, provider, fit_fn, predict_fn, simulate_fn,
             record_fn, holdout_metrics_fn, persist_fn=None, returns_sink=None,
             preregistration='', manifest=None, checkpoint=None):
    """Run one (arm, panel_start) cell across its horizons and folds.

    ``provider`` supplies ``aux_frame()``, ``fold_frames(fold, horizon, mask)`` ->
    (train_frame, valid_frame) aligned with feature columns + label + fwd_return +
    date/asset_id index, and ``prices()`` for the simulator. ``checkpoint`` is an
    optional mutable set of (horizon, fold_index) keys already done (resume).
    Returns a summary dict; one ledger row is recorded per horizon.
    """
    if manifest is not None:
        for horizon in cell.horizons:
            validate_cell(manifest, arm=cell.arm, panel_start=str(cell.panel_start), horizon=horizon)
        if 'index_codes' in manifest and not set(cell.index_codes) <= set(manifest['index_codes']):
            raise PreregistrationError(
                f'index_codes {list(cell.index_codes)} not declared in the preregistration '
                f'(declared: {manifest["index_codes"]})'
            )

    folds = stock_fold_plan(cell.panel_start, cell.panel_end, trade_dates)
    if not folds:
        raise ValueError('fold plan is empty: panel cannot supply the minimum training window')
    aux = provider.aux_frame()
    mask = arm_row_mask(aux, arm=cell.arm, cap_fraction=cell.cap_fraction)
    done = checkpoint if checkpoint is not None else set()
    prices = provider.prices()
    benchmark_returns = provider.benchmark_returns() if hasattr(provider, 'benchmark_returns') else {}

    summary = {'arm': cell.arm, 'panel_start': str(cell.panel_start), 'horizons': {}}
    for horizon in cell.horizons:
        params = _simulator_params_for(cell.simulator_params, horizon)
        fold_results = []
        fold_oos = []
        ic_by_date = {}
        for fold in folds:
            key = (horizon, fold.fold_index)
            if key in done:
                continue
            train_frame, valid_frame = provider.fold_frames(fold, horizon, mask)
            kept, dropped = train_dates_for_fold(fold, trade_dates, horizon)
            # The fit core's purged split reads 'date'/'label'/'fwd_return' as columns and
            # indexes train_frame[selected_feature_names] (expects a LIST, not a tuple — pandas
            # reads a tuple as one key). The provider returns frames indexed by (date, asset_id),
            # so reset before fitting.
            fit = fit_fn(train_frame.reset_index(), horizon, list(cell.feature_names))
            tag = f'wf-{cell.arm}-{cell.panel_start}-f{fold.fold_index}-h{horizon}'
            if persist_fn is not None:
                persist_fn(fit, horizon, tag)
            valid = valid_frame.reset_index()
            proba = predict_fn(fit, valid)
            up_prob = _up_prob(proba)
            metrics = holdout_metrics_fn(
                list(valid['label']), proba,
                list(valid['date']), list(valid['fwd_return']),
            )
            predictions = valid[['date', 'asset_id']].copy()
            predictions['up_prob'] = up_prob
            sim = simulate_fn(predictions, prices, execution_model=cell.execution_model, **params)
            train = train_frame.reset_index()
            train_pred = train[['date', 'asset_id']].copy()
            train_pred['up_prob'] = _up_prob(predict_fn(fit, train))
            train_sim = simulate_fn(train_pred, prices, execution_model=cell.execution_model, **params)
            oos = sim.set_index('date')['portfolio_return'] if len(sim) else pd.Series(dtype='float64')
            fold_oos.append(oos)
            ic_by_date.update(metrics.get('per_date_ic') or {})
            if returns_sink is not None:
                returns_sink(horizon, fold.fold_index, sim)
            fold_results.append({
                'fold_index': fold.fold_index,
                'valid_start': fold.valid_start.isoformat(),
                'valid_end': fold.valid_end.isoformat(),
                'purged_train_dates': len(dropped),
                'train_sessions': len(kept),
                'rank_ic': metrics.get('rank_ic'),
                'icir': metrics.get('icir'),
                'rank_ic_tstat': metrics.get('rank_ic_tstat'),
                'oos_sharpe': _sharpe(sim['portfolio_return']) if len(sim) else None,
                'is_sharpe': _sharpe(train_sim['portfolio_return']) if len(train_sim) else None,
                'oos_cum_return': _cum_return(sim['portfolio_return']),
                'n_selected_mean': float(sim['n_selected'].mean()) if len(sim) else 0.0,
            })
            done.add(key)
        results = _aggregate(horizon, fold_results)
        results.update(_verdict_metrics(fold_oos, ic_by_date, benchmark_returns))
        context = {
            'index_codes': list(cell.index_codes), 'arm': cell.arm,
            'panel_start': str(cell.panel_start), 'panel_end': str(cell.panel_end),
            'fold_plan': cell.fold_plan, 'purge': horizon, 'horizon': horizon,
            'schema_version': cell.schema_version, 'execution_model': cell.execution_model,
        }
        record = record_fn(context=context, results=results, verdict='',
                           preregistration=preregistration)
        summary['horizons'][horizon] = {'results': results, 'measurement_id': record.get('id')}
    return summary


def _sharpe(returns):
    """Annualized Sharpe of a daily return series (sqrt(252)), zero when flat."""
    series = pd.Series(list(returns), dtype='float64')
    if len(series) < 2:
        return None
    std = series.std(ddof=1)
    if std == 0 or pd.isna(std):
        return None
    return float(series.mean() / std * (252 ** 0.5))


def _cum_return(returns):
    """Cumulative return of a daily return series: prod(1+r) - 1."""
    series = pd.Series(list(returns), dtype='float64')
    if series.empty:
        return 0.0
    return float((1.0 + series).prod() - 1.0)


def _alpha_beta(strategy_returns, benchmark_returns):
    """OLS of strategy daily returns on benchmark daily returns (H2b / O5).

    Returns (annualized alpha, beta) over the dates present in both series. The
    strategy return is indexed by signal date T (realized T+1->T+2); the benchmark
    return is indexed by T — the alignment declared in the prereg, validated by the
    full-engine reconciliation. None when fewer than 2 common dates or zero variance.
    """
    if strategy_returns is None or benchmark_returns is None:
        return None, None
    strat = pd.Series(strategy_returns, dtype='float64')
    bench = pd.Series(benchmark_returns, dtype='float64')
    # Holding-period returns can extend past a fold's validation block into the next fold's
    # dates, so the concatenated panel series can carry duplicate dates — dedupe before the
    # date-intersection regression (keep first).
    strat = strat[~strat.index.duplicated(keep='first')]
    bench = bench[~bench.index.duplicated(keep='first')]
    common = strat.index.intersection(bench.index)
    if len(common) < 2:
        return None, None
    y = strat.loc[common].to_numpy()
    x = bench.loc[common].to_numpy()
    x_mean = x.mean()
    denom = float(((x - x_mean) ** 2).sum())
    if denom == 0:
        return None, None
    beta = float(((x - x_mean) * (y - y.mean())).sum() / denom)
    alpha_daily = float(y.mean() - beta * x_mean)
    return alpha_daily * 252.0, beta


def _aggregate(horizon, fold_results):
    ics = [f['rank_ic'] for f in fold_results if f['rank_ic'] is not None]
    icirs = [f['icir'] for f in fold_results if f['icir'] is not None]
    sharpes = [f['oos_sharpe'] for f in fold_results if f['oos_sharpe'] is not None]
    is_sharpes = [f['is_sharpe'] for f in fold_results if f.get('is_sharpe') is not None]
    mean = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    return {
        'horizon': horizon,
        'per_fold': fold_results,
        'mean_rank_ic': mean(ics),
        'mean_icir': mean(icirs),
        'mean_oos_sharpe': mean(sharpes),
        'mean_is_sharpe': mean(is_sharpes),
        'n_folds': len(fold_results),
    }


def _verdict_metrics(fold_oos, ic_by_date, benchmark_returns):
    """Panel-level verdict metrics (H2b / O3-O5): alpha/beta vs official + executable
    benchmarks, Newey-West t on the per-date IC series, folds beating the executable
    benchmark, and size beta. Benchmark series are date-indexed daily returns supplied
    by the provider; absent series yield None (never a fabricated zero)."""
    from apps.etf_rotation.verdict_stats import ic_stats

    nonempty = [s for s in fold_oos if len(s)]
    strategy = pd.concat(nonempty).sort_index() if nonempty else pd.Series(dtype='float64')
    if len(strategy):
        strategy = strategy[~strategy.index.duplicated(keep='first')]
    alpha_off, beta_off = _alpha_beta(strategy, benchmark_returns.get('official'))
    alpha_exe, beta_exe = _alpha_beta(strategy, benchmark_returns.get('executable'))
    _, beta_size = _alpha_beta(strategy, benchmark_returns.get('size'))
    nw_t = ic_stats(ic_by_date).get('nw_t') if ic_by_date else None
    folds_beating = 0
    executable = benchmark_returns.get('executable')
    if executable is not None:
        exe = pd.Series(executable, dtype='float64')
        for series in nonempty:
            bench = exe.reindex(series.index).dropna()
            if _cum_return(series) > _cum_return(bench):
                folds_beating += 1
    return {
        'nw_t': nw_t,
        'alpha_official': alpha_off, 'beta_official': beta_off,
        'alpha_executable': alpha_exe, 'beta_executable': beta_exe,
        'beta_size': beta_size,
        'folds_beating': folds_beating,
        'oos_cum_return': _cum_return(strategy),
    }