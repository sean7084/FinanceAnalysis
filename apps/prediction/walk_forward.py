"""Stock walk-forward fold plan (H2 / plan D7, H2 design refinement).

Wraps the ETF rotation ``expanding_folds`` pure function with stock-panel semantics:
training ALWAYS starts at STOCK_TRAINING_START (the contract boundary), evaluation
starts at the declared panel_start, 7 expanding folds over the evaluation region,
a 30-session tail reserve (covers the longest horizon), purge = horizon via
``train_dates_for_fold`` (the single source of truth shared by plan and fit).

The panel-start set is fixed in the preregistration; this module never chooses starts.
"""
from apps.core.date_contract import MIN_TRAIN_SESSIONS, STOCK_TRAINING_START
from apps.etf_rotation.walk_forward import expanding_folds, train_dates_for_fold  # noqa: F401 (re-exported)

N_FOLDS = 7
TAIL_RESERVE = 30


def stock_fold_plan(panel_start, panel_end, trade_dates, *, n_folds=N_FOLDS, horizon_days=TAIL_RESERVE):
    """Build the stock fold plan over actual sessions.

    ``trade_dates`` must be the sorted trading sessions covering
    [STOCK_TRAINING_START, panel_end]. ``min_train_days`` is the session index of
    ``panel_start`` so fold 0 trains on [STOCK_TRAINING_START, panel_start) and
    validation begins at panel_start. Raises if panel_start is not a session or
    leaves fewer than MIN_TRAIN_SESSIONS training sessions.
    """
    sessions = sorted(trade_dates)
    if panel_start not in sessions:
        raise ValueError(f'panel_start {panel_start} is not a trading session in the panel')
    min_train = sessions.index(panel_start)
    if min_train < MIN_TRAIN_SESSIONS:
        raise ValueError(
            f'panel_start {panel_start} leaves {min_train} training sessions '
            f'(< MIN_TRAIN_SESSIONS={MIN_TRAIN_SESSIONS})'
        )
    return expanding_folds(
        STOCK_TRAINING_START,
        panel_end,
        n_folds=n_folds,
        horizon_days=horizon_days,
        min_train_days=min_train,
        trade_dates=sessions,
    )