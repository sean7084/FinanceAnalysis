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

from apps.prediction.prereg import validate_cell
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
    """UP-class probability column from a (n, 3) DOWN/FLAT/UP probability array."""
    frame = pd.DataFrame(proba)
    return frame.iloc[:, 2].to_numpy()


def run_cell(cell, *, trade_dates, provider, fit_fn, predict_fn, simulate_fn,
             record_fn, holdout_metrics_fn, persist_fn=None, preregistration='',
             manifest=None, checkpoint=None):
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

    folds = stock_fold_plan(cell.panel_start, cell.panel_end, trade_dates)
    if not folds:
        raise ValueError('fold plan is empty: panel cannot supply the minimum training window')
    aux = provider.aux_frame()
    mask = arm_row_mask(aux, arm=cell.arm, cap_fraction=cell.cap_fraction)
    done = checkpoint if checkpoint is not None else set()
    prices = provider.prices()

    summary = {'arm': cell.arm, 'panel_start': str(cell.panel_start), 'horizons': {}}
    for horizon in cell.horizons:
        fold_results = []
        for fold in folds:
            key = (horizon, fold.fold_index)
            if key in done:
                continue
            train_frame, valid_frame = provider.fold_frames(fold, horizon, mask)
            kept, dropped = train_dates_for_fold(fold, trade_dates, horizon)
            fit = fit_fn(train_frame, horizon, cell.feature_names)
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
            sim = simulate_fn(predictions, prices, execution_model=cell.execution_model,
                              **cell.simulator_params)
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
                'n_selected_mean': float(sim['n_selected'].mean()) if len(sim) else 0.0,
            })
            done.add(key)
        results = _aggregate(horizon, fold_results)
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


def _aggregate(horizon, fold_results):
    ics = [f['rank_ic'] for f in fold_results if f['rank_ic'] is not None]
    icirs = [f['icir'] for f in fold_results if f['icir'] is not None]
    sharpes = [f['oos_sharpe'] for f in fold_results if f['oos_sharpe'] is not None]
    mean = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    return {
        'horizon': horizon,
        'per_fold': fold_results,
        'mean_rank_ic': mean(ics),
        'mean_icir': mean(icirs),
        'mean_oos_sharpe': mean(sharpes),
        'n_folds': len(fold_results),
    }