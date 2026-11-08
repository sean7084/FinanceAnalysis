"""Per-asset-class boundaries for training and walk-forward evaluation.

Three different histories are deliberately kept apart:

* **Raw data retained** -- what the database holds. Descriptive only; nothing
  here enforces it, and no purge derives from it.
* **Training start** -- the earliest session a production model may fit.
* **Backtest start** -- the earliest session a walk-forward fold may validate.

Why the boundaries are policy rather than the earliest row in the database: a
cross-sectional ranking model learns the shape of its own universe. The median
count of eligible ETFs per session was 24 in 2016, 27 in 2018, 61 in 2020 and
492 in 2026, so selecting the top 30% over roughly seven funds is not the same
task as selecting it over several hundred. Training across that discontinuity
teaches universe development rather than repeatable alpha.

``STOCK_TRAINING_START`` excludes the 2014-2015 leverage bubble, the
OTC-financing cleanup and the January 2016 circuit-breaker episode, while
keeping the 2018, 2020 and 2022 regimes as adverse-regime evidence.
``ETF_BACKTEST_START`` is the 251st eligible session after
``ETF_TRAINING_START``: :func:`apps.etf_rotation.walk_forward.expanding_folds`
reserves indices ``0..min_train_days-1`` for training and validates from
``min_train_days`` onward, so a boundary 250 sessions deep would name a date on
which no fold can exist.

These constants are intentionally not env-overridable. Moving one re-baselines
every headline metric, so it must be a committed change to this file.
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import CommandError

ASSET_STOCKS = 'stocks'
ASSET_ETF = 'etf'

STAGE_TRAINING = 'training'
STAGE_BACKTEST = 'backtest'

#: Earliest session retained in the database. Display only -- no code path
#: enforces these, and they are observations about stored data, not limits.
#: The stock bound is the *observed* first bar, which predates the
#: ``HISTORICAL_DATA_FLOOR`` of 2010-01-01: 365,224 rows across 1,077 assets are
#: older than the floor because ``purge_pre_floor_historical_data`` has not been
#: run over them. Retention policy and retained data are genuinely different here.
STOCK_RAW_DATA_FROM = date(2008, 7, 2)
ETF_RAW_DATA_FROM = date(2016, 1, 4)

STOCK_TRAINING_START = date(2016, 6, 1)
STOCK_BACKTEST_START = date(2018, 1, 2)
ETF_TRAINING_START = date(2020, 1, 2)
ETF_BACKTEST_START = date(2021, 1, 13)

#: Minimum training sessions before a walk-forward fold may validate. Single source
#: for the trainers, ``expanding_folds``, the backtest command and ``/data-limits``:
#: it used to be seven duplicated literals, and ``ETF_BACKTEST_START`` is derived
#: from it (the 251st eligible session after ``ETF_TRAINING_START``), so moving one
#: copy without the others silently broke the contract's coherence.
#:
#: Deliberately not env-overridable, for the same reason as the dates above: an
#: ``ETF_MIN_TRAIN_DAYS`` environment variable could move the fold arithmetic away
#: from the committed boundary without any change to this file being reviewed.
MIN_TRAIN_SESSIONS = 250

TRAINING_FLOORS = {ASSET_STOCKS: STOCK_TRAINING_START, ASSET_ETF: ETF_TRAINING_START}
BACKTEST_FLOORS = {ASSET_STOCKS: STOCK_BACKTEST_START, ASSET_ETF: ETF_BACKTEST_START}

_FLOORS = {STAGE_TRAINING: TRAINING_FLOORS, STAGE_BACKTEST: BACKTEST_FLOORS}

#: Opt-in override so the legacy pre-contract panels stay usable for research
#: and long-history stress testing without weakening the production default.
PRE_CONTRACT_FLAG = 'allow-pre-contract-history'


class ContractBoundaryError(CommandError):
    """Raised when a requested start precedes the contract without an override."""


def contract_floor(asset_class, stage):
    """The contract boundary for ``asset_class`` at ``stage``."""
    try:
        return _FLOORS[stage][asset_class]
    except KeyError as exc:
        raise ContractBoundaryError(
            f'unknown date contract key {stage!s}/{asset_class!s}; '
            f'stages {sorted(_FLOORS)} assets {sorted(TRAINING_FLOORS)}.'
        ) from exc


def enforce_date_contract(*, asset_class, stage, requested_start,
                          allow_pre_contract=False, flag_name=PRE_CONTRACT_FLAG):
    """Return the effective start, refusing a pre-contract one without an override.

    ``None`` resolves to the boundary, which is what lets a command inherit the
    contract simply by leaving its ``--start`` default empty.
    """
    boundary = contract_floor(asset_class, stage)
    if requested_start is None:
        return boundary
    if requested_start >= boundary:
        return requested_start
    if allow_pre_contract:
        return requested_start
    raise ContractBoundaryError(
        f'{stage} start {requested_start.isoformat()} precedes the {asset_class} date '
        f'contract boundary {boundary.isoformat()} (apps/core/date_contract.py). '
        f'Pass --{flag_name} to train or evaluate on pre-contract history, which is '
        f'research-only and must not be reported alongside production metrics.'
    )
