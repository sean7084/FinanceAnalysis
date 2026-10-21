"""Expanding-window walk-forward fold plan with a *mandatory* label-window purge.

The stock pipeline in :mod:`apps.prediction.tasks_lightgbm` trains on one contiguous
window and then reports ``np.mean(y_pred == y_train)`` measured on the matrix it just
fitted, so it cannot answer the only question that matters: would this ranking have
worked on data the fit never saw. Nothing resembling a rolling retrain exists anywhere
in the repo (``walk_forward`` -> 0 hits, no ``TimeSeriesSplit``, no purge, no holdout),
so this module supplies the framework: validation blocks that advance through time, a
training window that only ever grows, and a purge that is enforced rather than assumed.

The leak this exists to prevent
------------------------------
``ETFFeatureSnapshot`` keeps the label on the same row as the features: ``fwd_return``
for date ``D`` is the realised outcome of the next ``label_horizon_days`` bars. So a
training date whose horizon reaches into the validation block contributes the very
returns the fold is scored on. With ``horizon_days=20`` and validation opening on
trading-date index 500, training rows at indices 480..499 all carry labels consuming
returns dated inside the validation window. Left un-purged, rank IC on that fold is
measuring the answer key, and the inflation is uninterpretable rather than merely
optimistic. ``Fold.purged_train_dates`` exists so the drop is an assertable number
instead of a footnote.

Purge rule
----------
Drop every training date ``D`` whose label window ``D..D+horizon`` overlaps
``[valid_start, valid_end]``. Because ``D <= train_end < valid_start``, the overlap
test reduces to ``D + horizon >= valid_start``. Both horizon conventions are checked
and the *union* of their hits is dropped (see :func:`purge_train_dates`): the
trading-date-step convention of ``label_horizon_days``, and the calendar-day convention
``apps.prediction`` uses for stock labels. Whichever way ``features.py`` counted its
horizon, no overlap survives -- the conservative choice costs a few extra training
dates and buys the only guarantee worth having here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil

__all__ = [
    'Fold',
    'WalkForwardError',
    'expanding_folds',
    'purge_train_dates',
    'train_dates_for_fold',
    'audit_folds',
]

#: Refuse plans so thin that every fold would be noise; the caller gets
#: ``insufficient_data`` instead of a confident-looking metric on 40 rows.
MIN_DATES_FOR_PLAN = 2


class WalkForwardError(ValueError):
    """A fold plan is malformed, or would let a label window reach its own validation block."""


@dataclass(frozen=True)
class Fold:
    """One train/validation split. Immutable so a plan cannot be edited after it is audited.

    ``train_start`` is always the panel's first date (expanding window), ``train_end`` is
    the step before ``valid_start``, and ``purged_train_dates`` is how many dates inside
    ``[train_start, train_end]`` were then thrown away because their label window reached
    into ``[valid_start, valid_end]``.
    """

    fold_index: int
    train_start: date
    train_end: date
    valid_start: date
    valid_end: date
    purged_train_dates: int

    def as_dict(self) -> dict:
        """JSON-safe form for ``ModelVersion.metadata`` (dates as ISO strings)."""
        return {
            'fold_index': self.fold_index,
            'train_start': self.train_start.isoformat(),
            'train_end': self.train_end.isoformat(),
            'valid_start': self.valid_start.isoformat(),
            'valid_end': self.valid_end.isoformat(),
            'purged_train_dates': self.purged_train_dates,
        }


def _normalize_dates(dates) -> list:
    """Sorted, de-duplicated, timezone-naive ``date`` list."""
    normalized = set()
    for value in dates:
        if hasattr(value, 'date') and not isinstance(value, date):
            value = value.date()
        normalized.add(value)
    return sorted(normalized)


def _calendar_grid(first_date: date, last_date: date) -> list:
    """Every calendar day in ``[first_date, last_date]`` (the no-calendar fallback grid)."""
    if last_date < first_date:
        return []
    return [first_date + timedelta(days=offset) for offset in range((last_date - first_date).days + 1)]


def purge_train_dates(train_dates, valid_start: date, horizon_days: int, *, all_dates=None):
    """Split training dates into ``(kept, dropped)`` under the purge rule.

    A date is dropped when its label window can reach ``valid_start``, tested under both
    conventions and dropped if *either* fires:

    * trading-date steps -- ``index(D) + horizon_days >= index(valid_start)``, correct
      when ``label_horizon_days`` counts sessions (this module's assumption);
    * calendar days -- ``D + timedelta(days=horizon_days) >= valid_start``, correct when
      the horizon counts wall-clock days (``apps.prediction``'s stock convention).

    ``all_dates`` is the panel's full ordered date list, which is what makes the trading
    session index meaningful; without it only the calendar test can run, so the caller
    gets a purge that is *under*-stated rather than over-stated -- pass it whenever you
    have it.
    """
    horizon_days = int(horizon_days)
    if horizon_days < 1:
        raise WalkForwardError(f'horizon_days must be >= 1, got {horizon_days}.')
    if valid_start is None:
        raise WalkForwardError('valid_start is required to purge a training window.')

    positions = None
    valid_position = None
    if all_dates:
        ordered = _normalize_dates(all_dates)
        positions = {value: index for index, value in enumerate(ordered)}
        valid_position = positions.get(valid_start)

    kept, dropped = [], []
    for train_date in _normalize_dates(train_dates):
        session_overlap = (
            positions is not None
            and valid_position is not None
            and train_date in positions
            and positions[train_date] + horizon_days >= valid_position
        )
        calendar_overlap = train_date + timedelta(days=horizon_days) >= valid_start
        if session_overlap or calendar_overlap:
            dropped.append(train_date)
        else:
            kept.append(train_date)
    return kept, dropped


def train_dates_for_fold(fold: Fold, all_dates, horizon_days: int):
    """Re-derive a fold's ``(kept, dropped)`` training dates from the panel.

    The single source of truth for both the plan and the fit: :func:`expanding_folds`
    counts purged dates with this, and the trainer selects training rows with this, so
    the declared ``purged_train_dates`` and the applied split cannot drift apart.
    """
    train_dates = [value for value in _normalize_dates(all_dates)
                   if fold.train_start <= value <= fold.train_end]
    kept, dropped = purge_train_dates(train_dates, fold.valid_start, horizon_days, all_dates=all_dates)
    return kept, dropped


def expanding_folds(first_date, last_date, n_folds: int = 6, *,
                    horizon_days: int = 20, min_train_days: int = 250,
                    trade_dates=None) -> list:
    """Build ``n_folds`` expanding-window folds across a panel.

    Layout, over ``trade_dates`` when supplied (otherwise one synthetic date per calendar
    day from ``first_date`` to ``last_date``), with ``n`` dates indexed ``0..n-1``::

        [0 .. min_train-1]              minimum training window (fold 0 only trains here)
        [min_train .. n-1-horizon]      validation region, cut into n_folds equal steps
        [n-horizon .. n-1]              tail reserve, never used for validation

    Fold ``i`` trains from index ``0`` (always ``first_date``, i.e. expanding not
    sliding) up to the date before its validation block, and validates on its own step::

        valid_i = [min_train + i*step, min_train + (i+1)*step - 1]   step = ceil(V/n_folds)
        train_i = [first_date, valid_i.start - 1 date]

    then ``train_i`` is purged by the rule in :func:`purge_train_dates`, and the number of
    dates it removed is recorded as ``purged_train_dates``.

    Two structural choices, both about honest metrics:

    * The trailing ``horizon_days`` dates are excluded from validation. A validation date
      needs a *realised* label; leaving those dates in would silently shrink the last
      fold's sample (rows with a null ``fwd_return``) instead of being visible here.
    * ``train_end`` is the date immediately before ``valid_start`` before purging, so the
      training window is as long as it can honestly be and the purge trims from there.

    Returns fewer than ``n_folds`` folds when the panel cannot supply full steps (never
    pads), and ``[]`` when even ``min_train_days`` plus one validation date is unavailable
    -- the caller turns that into ``insufficient_data`` rather than training on nothing.
    """
    if n_folds < 1:
        raise WalkForwardError(f'n_folds must be >= 1, got {n_folds}.')
    horizon_days = int(horizon_days)
    min_train_days = int(min_train_days)
    if horizon_days < 1:
        raise WalkForwardError(f'horizon_days must be >= 1, got {horizon_days}.')
    if min_train_days < 1:
        raise WalkForwardError(f'min_train_days must be >= 1, got {min_train_days}.')

    if trade_dates is None:
        panel = _calendar_grid(first_date, last_date)
    else:
        panel = [value for value in _normalize_dates(trade_dates) if first_date <= value <= last_date]
    if not panel:
        return []
    if panel[0] != first_date or panel[-1] != last_date:
        # Boundaries that match no data would make train_start a date the model can
        # never be fitted on, which reads as a silent shrink rather than a failure.
        raise WalkForwardError(
            f'Panel [{panel[0]} .. {panel[-1]}] does not span the requested '
            f'[{first_date} .. {last_date}].'
        )

    # Validation region, after the minimum window and before the label tail reserve.
    if len(panel) - min_train_days - horizon_days < MIN_DATES_FOR_PLAN:
        return []
    step = max(1, ceil((len(panel) - min_train_days - horizon_days) / n_folds))

    folds = []
    for fold_index in range(n_folds):
        valid_start_index = min_train_days + fold_index * step
        if valid_start_index >= len(panel):
            break
        valid_end_index = min(valid_start_index + step - 1, len(panel) - horizon_days - 1)
        train_end_index = valid_start_index - 1
        if train_end_index < 0:
            break
        fold = Fold(
            fold_index=fold_index,
            train_start=panel[0],
            train_end=panel[train_end_index],
            valid_start=panel[valid_start_index],
            valid_end=panel[valid_end_index],
            purged_train_dates=0,
        )
        _kept, dropped = train_dates_for_fold(fold, panel, horizon_days)
        folds.append(Fold(
            fold_index=fold.fold_index,
            train_start=fold.train_start,
            train_end=fold.train_end,
            valid_start=fold.valid_start,
            valid_end=fold.valid_end,
            purged_train_dates=len(dropped),
        ))

    # Structural invariant: two folds must never validate on the same date. Were they to
    # overlap, the pooled holdout would count one date twice and fold i+1 would train up to
    # a date fold i was scored on -- both silently. Asserted here rather than trusted from
    # the index arithmetic above, which is exactly where an off-by-one hides.
    for previous, current in zip(folds, folds[1:]):
        if current.valid_start <= previous.valid_end:
            raise WalkForwardError(
                f'Fold {current.fold_index} validation ({current.valid_start}..{current.valid_end}) '
                f'overlaps fold {previous.fold_index} validation ({previous.valid_start}..{previous.valid_end}).'
            )
        if current.train_end >= current.valid_start:
            raise WalkForwardError(
                f'Fold {current.fold_index}: train_end {current.train_end} is not before '
                f'valid_start {current.valid_start}.'
            )
    return folds


def audit_folds(folds, all_dates, horizon_days: int) -> list:
    """Re-verify a plan against the panel and return a per-fold audit trail.

    Raises :class:`WalkForwardError` on either failure mode that would make the fold
    metrics meaningless: a declared purge count that does not match the recomputed one
    (plan and fit disagree), or a kept training date whose label window still reaches
    ``valid_start`` under either convention (a surviving leak).
    """
    panel = _normalize_dates(all_dates)
    positions = {value: index for index, value in enumerate(panel)}
    trail = []
    for fold in folds:
        kept, dropped = train_dates_for_fold(fold, panel, horizon_days)
        if len(dropped) != fold.purged_train_dates:
            raise WalkForwardError(
                f'Fold {fold.fold_index}: plan declares purged_train_dates='
                f'{fold.purged_train_dates} but the purge rule removes {len(dropped)}.'
            )
        if not kept:
            raise WalkForwardError(
                f'Fold {fold.fold_index}: the purge removed the entire training window '
                f'({len(dropped)} dates, horizon {horizon_days}); need a longer panel.'
            )
        valid_position = positions[fold.valid_start]
        for train_date in kept:
            # Both conventions re-checked here rather than trusted from the helper, so a
            # future edit to purge_train_dates fails this audit instead of passing silently.
            if positions[train_date] + horizon_days >= valid_position:
                raise WalkForwardError(
                    f'Fold {fold.fold_index}: training date {train_date} label window '
                    f'(trading sessions) overlaps validation starting {fold.valid_start}.'
                )
            if train_date + timedelta(days=horizon_days) >= fold.valid_start:
                raise WalkForwardError(
                    f'Fold {fold.fold_index}: training date {train_date} label window '
                    f'(calendar days) overlaps validation starting {fold.valid_start}.'
                )
        trail.append({
            'fold_index': fold.fold_index,
            'train_dates_kept': len(kept),
            'train_dates_dropped': len(dropped),
            'valid_dates': sum(1 for value in panel if fold.valid_start <= value <= fold.valid_end),
            'first_kept_train_date': kept[0].isoformat(),
            'last_kept_train_date': kept[-1].isoformat(),
        })
    return trail
