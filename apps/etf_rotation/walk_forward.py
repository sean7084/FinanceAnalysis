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
from itertools import combinations
from math import ceil

__all__ = [
    'CombinatorialFold',
    'Fold',
    'WalkForwardError',
    'audit_combinatorial_folds',
    'audit_folds',
    'combinatorial_purged_folds',
    'expanding_folds',
    'purge_train_dates',
    'train_dates_for_fold',
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


@dataclass(frozen=True)
class CombinatorialFold:
    """One combinatorial purged CV split.

    Unlike :class:`Fold` this is not an expanding window: the panel is cut into
    ``n_blocks`` contiguous blocks, ``test_blocks`` of them are held out, and the training
    set is every remaining date *after* purging label-window overlaps and embargoing the
    sessions immediately after each held-out block. That shape is what lets a fitted model
    (LightGBM/LSTM) be trained on a block set it cannot leak into, while the held-out
    blocks give several disjoint out-of-sample readings instead of one.

    ``train_dates`` is materialised because a combinatorial split is not a single range;
    counting it here means the plan and the fit cannot disagree about what was trained on.
    """

    fold_index: int
    train_dates: tuple
    validation_blocks: tuple
    purged_train_dates: int
    embargoed_train_dates: int

    def as_dict(self) -> dict:
        """JSON-safe form for reports and ``ModelVersion.metadata``."""
        return {
            'fold_index': self.fold_index,
            'validation_blocks': [
                [start.isoformat(), end.isoformat()] for start, end in self.validation_blocks
            ],
            'train_dates_kept': len(self.train_dates),
            'purged_train_dates': self.purged_train_dates,
            'embargoed_train_dates': self.embargoed_train_dates,
            'first_train_date': self.train_dates[0].isoformat() if self.train_dates else None,
            'last_train_date': self.train_dates[-1].isoformat() if self.train_dates else None,
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


def _panel_for(first_date, last_date, trade_dates):
    """The panel a plan is built over, with the same boundary check for every shape."""
    if trade_dates is None:
        panel = _calendar_grid(first_date, last_date)
    else:
        panel = [value for value in _normalize_dates(trade_dates) if first_date <= value <= last_date]
    if not panel:
        return []
    if panel[0] != first_date or panel[-1] != last_date:
        raise WalkForwardError(
            f'Panel [{panel[0]} .. {panel[-1]}] does not span the requested '
            f'[{first_date} .. {last_date}].'
        )
    return panel


def _block_sizes(total: int, n_blocks: int) -> list:
    """``total`` dates split into ``n_blocks`` contiguous groups, as even as they go.

    ``ceil(total / n_blocks)`` looks equivalent and is not: it silently produces fewer
    than ``n_blocks`` blocks whenever the division leaves a remainder, and a plan with
    fewer test blocks than requested changes what the distribution means. The remainder
    is distributed one date at a time instead.
    """
    base, extra = divmod(total, n_blocks)
    return [base + (1 if index < extra else 0) for index in range(n_blocks)]


def _trim_train_dates(panel, positions, validation_blocks, horizon_days, embargo_days):
    """Split candidate training dates into ``(kept, purged, embargoed)``.

    A date inside a validation block is not a training candidate at all. A date before a
    block is *purged* when its label window can reach that block (both conventions, as in
    :func:`purge_train_dates`). A date after a block is *embargoed* when it sits within
    ``embargo_days`` sessions of the block's last date. Purge wins over embargo when both
    fire, which is the conservative reading: the date is dropped either way.
    """
    validation_dates = set()
    for block_start, block_end in validation_blocks:
        validation_dates.update(
            value for value in panel if block_start <= value <= block_end
        )
    kept, purged, embargoed = [], [], []
    for value in panel:
        if value in validation_dates:
            continue
        position = positions[value]
        reason = None
        for block_start, block_end in validation_blocks:
            if value < block_start:
                session_overlap = position + horizon_days >= positions[block_start]
                calendar_overlap = value + timedelta(days=horizon_days) >= block_start
                if session_overlap or calendar_overlap:
                    reason = 'purge'
                    break
            elif value > block_end:
                if position - positions[block_end] <= embargo_days:
                    reason = 'embargo'
                    break
        if reason == 'purge':
            purged.append(value)
        elif reason == 'embargo':
            embargoed.append(value)
        else:
            kept.append(value)
    return kept, purged, embargoed


def combinatorial_purged_folds(first_date, last_date, n_blocks: int = 6,
                               test_blocks: int = 2, *, horizon_days: int = 20,
                               embargo_days: int = 0, trade_dates=None) -> list:
    """Build combinatorial purged CV splits over a panel.

    The usable panel is everything except the trailing ``horizon_days`` dates (a
    validation date needs a *realised* label, the same reserve :func:`expanding_folds`
    keeps). It is cut into ``n_blocks`` contiguous blocks and every combination of
    ``test_blocks`` blocks becomes one fold's validation set; the rest of the panel, after
    purging label-window overlaps and embargoing the sessions after each block, is its
    training set.

    ``C(n_blocks, test_blocks)`` folds are returned in stable combination order. Each
    held-out block is a disjoint contiguous window, so the validation readings are
    independent samples rather than one aggregate. This is a *measurement* plan: it says
    nothing about which configuration is best, and the caller must not rank configurations
    by it (that is what turns the tuning window into a second holdout).
    """
    n_blocks = int(n_blocks)
    test_blocks = int(test_blocks)
    horizon_days = int(horizon_days)
    embargo_days = int(embargo_days)
    if n_blocks < 2:
        raise WalkForwardError(f'n_blocks must be >= 2, got {n_blocks}.')
    if not 1 <= test_blocks < n_blocks:
        raise WalkForwardError(
            f'test_blocks must satisfy 1 <= test_blocks < n_blocks ({n_blocks}), got {test_blocks}.',
        )
    if horizon_days < 1:
        raise WalkForwardError(f'horizon_days must be >= 1, got {horizon_days}.')
    if embargo_days < 0:
        raise WalkForwardError(f'embargo_days must be >= 0, got {embargo_days}.')

    panel = _panel_for(first_date, last_date, trade_dates)
    if not panel:
        return []
    usable = len(panel) - horizon_days
    if usable < n_blocks:
        raise WalkForwardError(
            f'Panel has {len(panel)} dates; {n_blocks} blocks plus a {horizon_days}-date '
            'label tail need at least that many more. Shorten the block count or widen the window.'
        )

    positions = {value: index for index, value in enumerate(panel)}
    blocks = []
    cursor = 0
    for size in _block_sizes(usable, n_blocks):
        blocks.append((panel[cursor], panel[cursor + size - 1]))
        cursor += size

    folds = []
    for fold_index, combo in enumerate(combinations(range(n_blocks), test_blocks)):
        validation_blocks = tuple(blocks[index] for index in combo)
        kept, purged, embargoed = _trim_train_dates(
            panel[:usable], positions, validation_blocks, horizon_days, embargo_days,
        )
        if not kept:
            raise WalkForwardError(
                f'Fold {fold_index}: purge and embargo removed every training date '
                f'({len(purged)} purged, {len(embargoed)} embargoed); need a longer panel '
                'or fewer test blocks.'
            )
        folds.append(CombinatorialFold(
            fold_index=fold_index,
            train_dates=tuple(kept),
            validation_blocks=validation_blocks,
            purged_train_dates=len(purged),
            embargoed_train_dates=len(embargoed),
        ))
    return folds


def audit_combinatorial_folds(folds, all_dates, horizon_days: int, embargo_days: int = 0) -> list:
    """Re-verify a combinatorial plan against the panel and return a per-fold trail.

    Recomputes the training set from the declared blocks rather than trusting the stored
    dates, then re-checks the two leaks this shape can suffer: a training date whose label
    window reaches a later block, and a training date inside the embargo after an earlier
    block. A count mismatch means the plan and the recomputation disagree, which is a bug
    in the plan even when both look plausible.
    """
    panel = _normalize_dates(all_dates)
    positions = {value: index for index, value in enumerate(panel)}
    horizon_days = int(horizon_days)
    embargo_days = int(embargo_days)
    usable = len(panel) - horizon_days
    trail = []
    for fold in folds:
        for block_start, block_end in fold.validation_blocks:
            if block_start not in positions or block_end not in positions:
                raise WalkForwardError(
                    f'Fold {fold.fold_index}: validation block {block_start}..{block_end} '
                    'is not on the panel.'
                )
            if positions[block_end] >= usable:
                raise WalkForwardError(
                    f'Fold {fold.fold_index}: validation block ends {block_end}, inside the '
                    f'{horizon_days}-date label tail; its labels are not realised.'
                )
        kept, purged, embargoed = _trim_train_dates(
            panel[:usable], positions, fold.validation_blocks, horizon_days, embargo_days,
        )
        if (len(kept), len(purged), len(embargoed)) != (
            len(fold.train_dates), fold.purged_train_dates, fold.embargoed_train_dates,
        ):
            raise WalkForwardError(
                f'Fold {fold.fold_index}: plan declares '
                f'({len(fold.train_dates)} kept, {fold.purged_train_dates} purged, '
                f'{fold.embargoed_train_dates} embargoed) but the rule recomputes '
                f'({len(kept)}, {len(purged)}, {len(embargoed)}).'
            )
        if tuple(kept) != tuple(fold.train_dates):
            raise WalkForwardError(
                f'Fold {fold.fold_index}: stored training dates differ from the recomputed set.'
            )
        trail.append({
            'fold_index': fold.fold_index,
            'validation_blocks': [[s.isoformat(), e.isoformat()] for s, e in fold.validation_blocks],
            'train_dates_kept': len(kept),
            'purged_train_dates': len(purged),
            'embargoed_train_dates': len(embargoed),
        })
    return trail
