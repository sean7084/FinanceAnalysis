"""Walk-forward LSTM ranker for the ETF momentum-rotation pool.

This module is the sequence-model half of `apps/etf_rotation`. It deliberately
borrows the mechanics of ``apps/prediction/tasks_lstm.py`` (LSTM hidden=64 /
2 layers / dropout=0.2 / ``batch_first``, the ``__is_missing`` indicator
convention at ``tasks_lstm.py:110-132``, ``(x - mean) / scale`` normalisation at
``tasks_lstm.py:426``, the registry lookup at ``tasks_lstm.py:260-288``, the
device pick at ``tasks_lstm.py:742``) and rejects its two structural flaws:

1. ``tasks_lstm._build_sequences`` (``tasks_lstm.py:135``) is welded to the
   stock-universe feature matrix built by ``tasks_lightgbm._create_feature_matrix``.
   Here the panel is read straight out of ``ETFFeatureSnapshot`` and the feature
   whitelist comes from ``apps.etf_rotation.features.FEATURE_COLUMNS``.
2. ``tasks_lstm._train_single_horizon_lstm`` (``tasks_lstm.py:522-536``) does one
   80/20 chronological cut and reports accuracy. A single cut cannot say whether
   the model generalised or merely memorised one regime, so this module runs
   ``n_folds`` expanding walk-forward folds (the plan and the purge come from
   ``apps.etf_rotation.walk_forward``) and reports per-fold *holdout* rank IC,
   ICIR and Top-N hit rate. Metric key names and definitions match
   ``apps.etf_rotation.train_lightgbm.evaluate_holdout`` on purpose: the rotation
   and backtest engines call the two predictors interchangeably, so a metric that
   means something different per model would be worse than no metric.

Head and loss
-------------
``tasks_lstm.LSTMClassifier`` ends in a 3-way ``CrossEntropy`` over DOWN/FLAT/UP.
The rotation engine needs a *cross-sectional ordering* per date, not a direction
class, so ``ETFRotationLSTM`` keeps the LSTM body and replaces the head with
``Linear -> GELU -> Dropout -> Linear -> 1``, emitting one scalar per (ETF, date).
Two losses are implemented and selectable through ``params['loss']``:

* ``bce_top_bucket`` (**the default, and what a plain run trains**):
  ``BCEWithLogitsLoss`` against ``label_top_bucket``. Chosen because it needs no
  per-date batch layout, is stable on thin pools, and the sigmoid of its logit is
  directly comparable with the probability a LightGBM ``binary`` model emits --
  which matters because both predictors occupy the same engine slot.
* ``pairwise_rank`` (the documented alternative, also runnable today): a
  within-date pairwise logistic ranking loss on ``label_rank_pct``, built from
  plain torch ops (scipy is not installed). It optimises exactly the quantity the
  engine consumes, but a shuffled loader gives ragged per-date groups, so early
  folds on a thin pool see few ordered pairs per batch; that is why it is not the
  default rather than because it is unimplemented.

Leak discipline
---------------
* Training target dates come from ``walk_forward.train_dates_for_fold`` (the same
  helper that produced the plan's ``purged_train_dates`` count, so plan and fit
  cannot drift), then are hard-filtered to ``< valid_start``.
* Windows are trailing and end at their own target date, so no training window can
  contain a holdout bar. ``_validate_temporal_split`` asserts this per fold and
  refuses the fold otherwise.
* The feature scaler is fit on rows whose *date* is in that fold's purged train set
  and is only ever applied to the holdout.
* Batch shuffling happens only inside the train index set; train and holdout sample
  indices are disjoint by date and are never concatenated or globally shuffled.
* Windows are validated against the pool's eligible-date calendar, so a suspended
  or ineligible stretch breaks the sequence instead of being bridged.
"""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import re
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import torch
from celery import shared_task
from django.conf import settings
from django.db.models import Max, Min
from django.utils import timezone
from torch import nn
from torch.nn.functional import binary_cross_entropy_with_logits, softplus
from torch.nn.utils import clip_grad_norm_
from torch.utils.data import DataLoader, Dataset

from apps.core.date_floor import get_historical_data_floor
from apps.prediction.artifact_store import get_artifact_store, to_store_key
from apps.prediction.models import ModelVersion

from .models import ETFFeatureSnapshot


logger = logging.getLogger(__name__)

__all__ = [
    'MODEL_TYPE_LABEL',
    'ETFRotationLSTMTrainingError',
    'ETFRotationLSTMImportError',
    'ETFRotationLSTM',
    'train_walk_forward',
    'load_active_predictor',
    'train_etf_rotation_lstm',
]


MODEL_TYPE_LABEL = 'ETF_LSTM'

# Relative prefix for ModelVersion.artifact_path, so the registry row stays portable
# across hosts (see apps/prediction/artifact_store.py).
ETF_LSTM_MODELS_RELATIVE_PREFIX = os.path.join('models', 'etf_rotation', 'lstm')

# Same convention as tasks_lstm.LSTM_MISSING_INDICATOR_SUFFIX / LSTM_MISSING_VALUE_STRATEGY.
MISSING_INDICATOR_SUFFIX = '__is_missing'
MISSING_VALUE_STRATEGY = 'mask_and_zero_impute'

LOSS_BCE = 'bce_top_bucket'
LOSS_PAIRWISE = 'pairwise_rank'

SERVING_ARTIFACT_NAME = 'model.pt'
METRICS_ARTIFACT_NAME = 'metrics.json'

LABEL_FIELDS = ('fwd_return', 'label_rank_pct', 'label_top_bucket', 'label_horizon_days')

#: Days of calendar slack used to reach ``sequence_length`` bars back at inference:
#: ``sequence_length`` sessions plus weekends/holiday margin, doubled.
INFERENCE_LOOKBACK_DAYS_PER_BAR = 3
INFERENCE_LOOKBACK_MARGIN_DAYS = 30


class ETFRotationLSTMTrainingError(RuntimeError):
    """Training could not proceed for a reason the operator can act on."""


class ETFRotationLSTMImportError(ETFRotationLSTMTrainingError):
    """A sibling module owned by another workstream is not on disk yet."""


# ---------------------------------------------------------------------------
# Lazy cross-module seams
# ---------------------------------------------------------------------------

def _import_symbol(module_path, symbol, purpose):
    """Import ``module_path.symbol``, distinguishing "absent" from "broken".

    ``features.py`` and ``walk_forward.py`` are written by other agents, so this
    module must stay importable while they are missing and must name the file to go
    write when it is actually called.
    """
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        if exc.name == module_path or str(module_path).startswith(f'{exc.name}.'):
            raise ETFRotationLSTMImportError(
                f'{module_path} is required to {purpose} but is not present in apps/etf_rotation. '
                'Create it (or finish the concurrent workstream) before training the ETF LSTM.'
            ) from exc
        raise
    if not hasattr(module, symbol):
        raise ETFRotationLSTMImportError(
            f'{module_path}.{symbol} is required to {purpose}; the module exists but does not define it.'
        )
    return getattr(module, symbol)


def _feature_columns():
    """The typed snapshot feature columns, taken from the feature whitelist.

    Never derived from ``ETFFeatureSnapshot._meta.fields``: the table also carries
    ``is_eligible``, ``metadata`` and the look-ahead label columns, so reading
    membership off the model would train on the labels as inputs.
    """
    raw = _import_symbol('apps.etf_rotation.features', 'FEATURE_COLUMNS', 'build the LSTM feature panel')
    columns = [str(name) for name in raw]
    if not columns:
        raise ETFRotationLSTMTrainingError('FEATURE_COLUMNS is empty; refusing to train a featureless model.')
    leaked = [name for name in columns if name in ('fwd_return', 'label_rank_pct', 'label_top_bucket',
                                                   'label_horizon_days', 'is_eligible')]
    if leaked:
        raise ETFRotationLSTMTrainingError(
            f'FEATURE_COLUMNS contains look-ahead or filter columns {leaked}; they are labels, not inputs.'
        )
    return columns


def _fold_api():
    """The fold plan helpers from ``walk_forward``: plan, purge, audit."""
    return (
        _import_symbol('apps.etf_rotation.walk_forward', 'expanding_folds', 'plan the train/validation folds'),
        _import_symbol('apps.etf_rotation.walk_forward', 'train_dates_for_fold', 'select purged training dates'),
        _import_symbol('apps.etf_rotation.walk_forward', 'audit_folds', 'audit the fold plan against the panel'),
    )


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class ETFRotationLSTM(nn.Module):
    """LSTM encoder over ``sequence_length`` bars -> one scalar rank score.

    Mirrors ``tasks_lstm.LSTMClassifier`` (L67-87) in the body; the head emits a
    scalar rather than 3 class logits because the target is a per-date ordering.
    """

    def __init__(self, input_size, hidden_size=64, num_layers=2, dropout=0.2):
        super().__init__()
        self.input_size = int(input_size)
        self.hidden_size = int(hidden_size)
        self.num_layers = int(num_layers)
        self.dropout = float(dropout)
        self.lstm = nn.LSTM(
            input_size=self.input_size,
            hidden_size=self.hidden_size,
            num_layers=self.num_layers,
            # nn.LSTM ignores `dropout` below the last layer, so a single-layer stack
            # must pass 0.0 explicitly rather than a value it will silently drop.
            dropout=self.dropout if self.num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.head = nn.Sequential(
            nn.Linear(self.hidden_size, self.hidden_size),
            # Exact (erf) GELU, constructed with no kwargs: `F.gelu(..., approximate=...)`
            # is absent from some torch builds and raises TypeError at the first forward,
            # and the bare `nn.GELU()` is the spelling that exists in every build.
            nn.GELU(),
            nn.Dropout(self.dropout),
            nn.Linear(self.hidden_size, 1),
        )

    def forward(self, x):
        encoded, _ = self.lstm(x)
        return self.head(encoded[:, -1, :]).squeeze(-1)


class _WindowDataset(Dataset):
    """Index view over one shared ``(rows, features)`` matrix.

    The panel matrix is never copied per fold: a walk-forward run over a 10-year
    eligible pool would otherwise hold ``n_folds`` float32 cubes of
    ``(samples, sequence_length, features)`` at once. Standardisation is applied per
    item from the fold's train-fitted statistics, which is what makes it impossible
    for a holdout row to be scaled by constants fit on itself.
    """

    def __init__(self, matrix, starts, ends, targets, ranks, date_ids, mean, scale, sequence_length):
        self._matrix = matrix
        self._starts = starts
        self._ends = ends
        self._targets = targets
        self._ranks = ranks
        self._date_ids = date_ids
        self._mean = torch.from_numpy(np.ascontiguousarray(mean, dtype=np.float32))
        self._scale = torch.from_numpy(np.ascontiguousarray(scale, dtype=np.float32))
        self.sequence_length = int(sequence_length)

    def __len__(self):
        return int(len(self._starts))

    def __getitem__(self, index):
        start = int(self._starts[index])
        end = int(self._ends[index])
        window = torch.from_numpy(self._matrix[start:end + 1])
        window = (window - self._mean) / self._scale
        window = torch.nan_to_num(window, nan=0.0, posinf=0.0, neginf=0.0)
        if window.shape[0] != self.sequence_length:
            raise RuntimeError(
                f'window length {window.shape[0]} != sequence_length {self.sequence_length}; '
                'the panel window index arrays are inconsistent.'
            )
        return (
            window,
            torch.tensor(float(self._targets[index]), dtype=torch.float32),
            torch.tensor(float(self._ranks[index]), dtype=torch.float32),
            torch.tensor(int(self._date_ids[index]), dtype=torch.long),
        )


# ---------------------------------------------------------------------------
# Panel loading and sequence assembly
# ---------------------------------------------------------------------------

def _as_date(value):
    """Coerce whatever the fold plan or a caller stored for a date into ``datetime.date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None and hasattr(value, 'date') and not isinstance(value, str):
        try:
            return value.date()
        except (AttributeError, TypeError, ValueError):
            pass
    text = str(value).strip()
    return date.fromisoformat(text[:10])


def _setting_int(name, default):
    return int(getattr(settings, name, default) or default)


def _resolve_window(start_date=None, end_date=None):
    """Explicit bounds, or the eligible span in the table, floored by HISTORICAL_DATA_FLOOR.

    ``(None, None)`` means "nothing eligible to train on", which is the expected state
    of a fresh install and must be an ``insufficient_data`` answer rather than an
    exception from the daily beat.
    """
    start = _as_date(start_date) if start_date else None
    end = _as_date(end_date) if end_date else None
    if start is None or end is None:
        bounds = ETFFeatureSnapshot.objects.filter(is_eligible=True).aggregate(
            min_date=Min('date'), max_date=Max('date'),
        )
        start = start or bounds['min_date']
        end = end or bounds['max_date']
    if start is None or end is None:
        return None, None
    start = _as_date(start)
    end = _as_date(end)
    floor = get_historical_data_floor()
    if start < floor:
        logger.info('ETF LSTM training start %s raised to HISTORICAL_DATA_FLOOR %s.', start, floor)
        start = floor
    if end < start:
        raise ETFRotationLSTMTrainingError(f'end_date {end} is before start_date {start}.')
    return start, end


def _eligible_calendar(start_date, end_date):
    """Distinct eligible snapshot dates -- the contiguity reference for windows.

    Windows are checked against the *pool's* calendar rather than an ETF's own row
    spacing, so a bar the fund did not print (suspension, listing gap, an
    ``is_eligible=False`` stretch) breaks the sequence instead of being quietly
    skipped, which would splice non-contiguous bars into one "20-day" window.
    """
    days = (
        ETFFeatureSnapshot.objects
        .filter(is_eligible=True, date__gte=start_date, date__lte=end_date)
        .values_list('date', flat=True)
        .distinct()
    )
    return sorted({_as_date(day) for day in days})


def _load_snapshot_frame(*, start_date, end_date, feature_columns, ts_codes=None):
    """One query for the (ETF, date) panel, features and their own labels together.

    Rows with an unfinished label are kept: they are legitimate *input* bars for an
    earlier target and are excluded only as *targets*, in ``_assemble_windows``.
    Filtering them out here would delete real bars and make windows jump.
    """
    queryset = ETFFeatureSnapshot.objects.filter(is_eligible=True, date__gte=start_date, date__lte=end_date)
    if ts_codes is not None:
        queryset = queryset.filter(etf__ts_code__in=list(ts_codes))
    rows = list(queryset.values('etf__ts_code', 'date', *feature_columns, *LABEL_FIELDS))
    if not rows:
        return pd.DataFrame(columns=['ts_code', 'date', *feature_columns, *LABEL_FIELDS])
    frame = pd.DataFrame.from_records(rows)
    return frame.rename(columns={'etf__ts_code': 'ts_code'})


def _augment_missingness(frame, base_columns):
    """NaN -> neutral 0 plus a ``{name}__is_missing`` flag (tasks_lstm.py:110-132).

    One deliberate divergence: the stock pipeline flags ``.isna()`` only. A non-finite
    value here (an ``inf`` from a zero-denominator momentum ratio) is equally corrupt,
    and zero-imputing it without a flag would hand the model a neutral input for a
    broken one, so ``~np.isfinite`` is the test.
    """
    working = frame.copy()
    full_names = []
    for column in base_columns:
        if column not in working.columns:
            working[column] = np.nan
        values = pd.to_numeric(working[column], errors='coerce').to_numpy(dtype=np.float64)
        missing = ~np.isfinite(values)
        working[column] = np.where(missing, 0.0, values).astype(np.float32)
        indicator = f'{column}{MISSING_INDICATOR_SUFFIX}'
        working[indicator] = missing.astype(np.float32)
        full_names.extend([column, indicator])
    return working, full_names


def _numeric_column(frame, name):
    """Coerce a possibly object-typed snapshot column to float64, NaN when absent."""
    if name not in frame.columns:
        return np.full(len(frame), np.nan, dtype=np.float64)
    return pd.to_numeric(frame[name], errors='coerce').to_numpy(dtype=np.float64)


def _empty_panel(base_feature_names, calendar_dates, sequence_length):
    return {
        'X': np.zeros((0, 2 * len(base_feature_names)), dtype=np.float32),
        'row_date_pos': np.zeros(0, dtype=np.int64),
        'starts': np.zeros(0, dtype=np.int64), 'ends': np.zeros(0, dtype=np.int64),
        'target_date_pos': np.zeros(0, dtype=np.int64), 'target_code': np.zeros(0, dtype=np.int64),
        'y_top': np.zeros(0, dtype=np.float32), 'fwd': np.zeros(0, dtype=np.float32),
        'rank_pct': np.zeros(0, dtype=np.float32), 'codes': [], 'calendar_dates': list(calendar_dates),
        'feature_names': [f for name in base_feature_names for f in (name, f'{name}{MISSING_INDICATOR_SUFFIX}')],
        'n_rows': 0, 'sequence_length': int(sequence_length),
    }


def _assemble_windows(*, frame, base_feature_names, calendar_dates, sequence_length,
                      require_labels, horizon_days=None):
    """Turn a snapshot frame into a flat matrix plus contiguous trailing windows.

    Pure (no ORM, no files) so the split rules can be checked against a synthetic
    panel. Each guard kills one specific corruption:

    * strict ``(ts_code, date)`` ordering plus a per-ETF calendar-contiguity test:
      a window never bridges a trading gap;
    * target-side label masking only: the input panel stays whole while a target
      whose forward window has not elapsed (or was computed for another horizon) is
      refused;
    * ``row_date_pos`` kept per row and per target so the split checks and the scaler
      audit are provable rather than assumed.
    """
    sequence_length = int(sequence_length)
    if frame is None or frame.empty or sequence_length < 1 or not calendar_dates:
        return _empty_panel(base_feature_names, calendar_dates, sequence_length)

    working, full_names = _augment_missingness(frame, base_feature_names)
    working = working.assign(_date=[_as_date(day) for day in working['date']], _code=working['ts_code'].astype(str))
    working = working.sort_values(['_code', '_date'], kind='mergesort').reset_index(drop=True)

    calendar = np.asarray([np.datetime64(_as_date(day), 'D') for day in calendar_dates], dtype='datetime64[D]')
    row_days = np.asarray(working['_date'].tolist(), dtype='datetime64[D]')
    row_date_pos = np.searchsorted(calendar, row_days)
    matched = calendar[np.clip(row_date_pos, 0, len(calendar) - 1)] == row_days
    if not bool(np.all(matched)):
        raise ETFRotationLSTMTrainingError(
            'the snapshot frame carries dates outside the supplied eligible calendar; the calendar '
            'query and the frame query must cover the same range.'
        )
    row_date_pos = row_date_pos.astype(np.int64)

    codes, code_inverse = np.unique(working['_code'].to_numpy(), return_inverse=True)
    code_of_row = np.asarray(code_inverse, dtype=np.int64)

    new_segment = np.ones(len(working), dtype=bool)
    if len(working) > 1:
        new_segment[1:] = (code_of_row[1:] != code_of_row[:-1]) | (row_date_pos[1:] != row_date_pos[:-1] + 1)
    segment_id = np.cumsum(new_segment) - 1

    n_rows = len(working)
    matrix = np.ascontiguousarray(working[full_names].to_numpy(dtype=np.float32))
    if n_rows < sequence_length:
        return {**_empty_panel(base_feature_names, calendar_dates, sequence_length),
                'X': matrix, 'row_date_pos': row_date_pos, 'codes': list(codes), 'n_rows': n_rows}

    end_rows = np.arange(sequence_length - 1, n_rows, dtype=np.int64)
    start_rows = end_rows - (sequence_length - 1)

    selectable = np.ones(n_rows, dtype=bool)
    if require_labels:
        forward = _numeric_column(working, 'fwd_return')
        rank_pct = _numeric_column(working, 'label_rank_pct')
        top_bucket = _numeric_column(working, 'label_top_bucket')
        selectable = ~np.isnan(forward) & ~np.isnan(rank_pct) & ~np.isnan(top_bucket)
        stored_horizon = _numeric_column(working, 'label_horizon_days')
        selectable &= np.isfinite(stored_horizon) & (stored_horizon == float(horizon_days))

    contiguity_ok = segment_id[start_rows] == segment_id[end_rows]
    # Only the TARGET row needs a usable label: an interior bar whose own forward
    # window was never computable is still a legitimate input bar.
    keep = contiguity_ok & selectable[end_rows]
    ends = end_rows[keep]
    starts = start_rows[keep]
    if ends.size == 0:
        return {**_empty_panel(base_feature_names, calendar_dates, sequence_length),
                'X': matrix, 'row_date_pos': row_date_pos, 'codes': list(codes), 'n_rows': n_rows}

    def target_column(name):
        if not require_labels:
            return np.zeros(ends.size, dtype=np.float32)
        return _numeric_column(working, name)[ends].astype(np.float32)

    return {
        'X': matrix,
        'row_date_pos': row_date_pos,
        'starts': starts,
        'ends': ends,
        'target_date_pos': row_date_pos[ends],
        'target_code': code_of_row[ends],
        'y_top': target_column('label_top_bucket'),
        'fwd': target_column('fwd_return'),
        'rank_pct': target_column('label_rank_pct'),
        'codes': list(codes),
        'calendar_dates': list(calendar_dates),
        'feature_names': full_names,
        'n_rows': n_rows,
        'sequence_length': sequence_length,
    }


def _row_statistics(rows):
    """Column mean/std over rows the caller has already proven to be train rows."""
    if rows.ndim == 3:
        rows = rows.reshape(-1, rows.shape[-1])
    if rows.shape[0] == 0:
        raise ETFRotationLSTMTrainingError('refusing to fit a scaler on zero rows.')
    flat = rows.astype(np.float64, copy=False)
    mean = np.nan_to_num(flat.mean(axis=0, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    scale = flat.std(axis=0, dtype=np.float64)
    # A constant train column has std 0 and would divide by zero; a non-finite std
    # means the train rows were not cleaned. Both collapse to "do not scale".
    scale = np.where(np.isfinite(scale) & (scale > 1e-8), scale, 1.0)
    return mean.astype(np.float32), scale.astype(np.float32)


# ---------------------------------------------------------------------------
# Losses and passes
# ---------------------------------------------------------------------------

def _pairwise_ranking_loss(scores, ranks, date_ids):
    """Alternative (b): within-date pairwise logistic ranking loss on ``label_rank_pct``.

    Plain torch ops only (scipy is not installed). Pairs form between samples of the
    same date whose forward ranks differ, so no cross-date comparison -- and therefore
    no implicit market-timing bet -- enters the objective.
    """
    same_date = date_ids.unsqueeze(1) == date_ids.unsqueeze(0)
    higher_rank = ranks.unsqueeze(1) > ranks.unsqueeze(0)
    mask = same_date & higher_rank
    if not bool(torch.any(mask)):
        # Keep the graph connected so `.backward()` still works on a batch that drew
        # a single date or a set of tied ranks.
        return scores.sum() * 0.0
    margin = scores.unsqueeze(1) - scores.unsqueeze(0)
    return softplus(-margin[mask]).mean()


def _batch_loss(model, batch, device, config):
    windows, targets, ranks, date_ids = (tensor.to(device) for tensor in batch)
    scores = model(windows)
    if config['loss'] == LOSS_PAIRWISE:
        loss = _pairwise_ranking_loss(scores, ranks, date_ids)
    else:
        loss = binary_cross_entropy_with_logits(scores, targets)
    return loss, scores, targets


def _forward_pass(model, loader, device, config, optimizer=None):
    """One pass. With an optimizer it trains; without it evaluates and returns scores.

    Evaluation keeps ``shuffle=False`` so the returned score vector stays aligned with
    the sample index array the loader was built from -- that alignment is what lets a
    holdout score be attributed back to its (ETF, date) for the rank IC.
    """
    training = optimizer is not None
    model.train(training)
    total_loss = 0.0
    seen = 0
    score_chunks = []
    target_chunks = []
    with torch.enable_grad() if training else torch.inference_mode():
        for batch in loader:
            loss, scores, targets = _batch_loss(model, batch, device, config)
            batch_size = int(batch[0].shape[0])
            total_loss += float(loss.detach().cpu()) * batch_size
            seen += batch_size
            if training:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                clip_grad_norm_(model.parameters(), config['max_grad_norm'])
                optimizer.step()
            score_chunks.append(scores.detach().float().cpu())
            target_chunks.append(targets.detach().float().cpu())
    if seen == 0:
        return float('nan'), np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)
    return (
        total_loss / seen,
        torch.cat(score_chunks).numpy(),
        torch.cat(target_chunks).numpy(),
    )


# ---------------------------------------------------------------------------
# Holdout metrics
# ---------------------------------------------------------------------------

def _spearman(predicted, realised):
    """Spearman via ``pandas`` (scipy is not installed).

    A constant input answers ``None`` rather than letting pandas divide by zero and
    hand back NaN, which would poison the ICIR standard deviation.
    """
    frame = pd.DataFrame({
        'p': np.asarray(predicted, dtype=float),
        'r': np.asarray(realised, dtype=float),
    }).dropna()
    if len(frame) < 3:
        return None
    if frame['p'].std(ddof=0) == 0 or frame['r'].std(ddof=0) == 0:
        return None
    value = frame['p'].corr(frame['r'], method='spearman')
    if value is None or not np.isfinite(float(value)):
        return None
    return float(value)


def _mean(values):
    """Mean over a list that may hold ``None`` (a fold whose IC was undefined).

    ``np.mean([None])`` raises, and a fold with no measurable IC must not turn the
    aggregate into a crash or a lie; it is dropped, exactly as
    ``train_lightgbm._mean_of`` drops it.
    """
    clean = [float(value) for value in values
             if value is not None and np.isfinite(float(value))]
    return None if not clean else float(np.mean(clean))


def _holdout_metrics(scored, top_n):
    """Holdout metrics computed **per validation date**, then summarised.

    Definitions are kept identical to ``train_lightgbm.evaluate_holdout`` so an
    LSTM and a LightGBM number can be compared by the backtest engine. Pooling rows
    across dates would rank one day's ETFs against another day's pool, which is not
    the decision the rotation engine makes and reliably flatters the IC.

    Returns ``(metrics, daily_ic)``; the raw daily series is kept out of the registry
    metadata but feeds the pooled holdout.
    """
    top_n = max(1, int(top_n))
    daily_ics, hit_rates, top_bucket_rates, leader_returns, pool_returns = [], [], [], [], []
    skipped_ic = skipped_small = 0
    for _day, day_frame in scored.groupby('date', sort=True):
        ic = _spearman(day_frame['score'].to_numpy(), day_frame['fwd_return'].to_numpy())
        if ic is None:
            skipped_ic += 1
        else:
            daily_ics.append(ic)
        sample_count = len(day_frame)
        if sample_count >= top_n:
            # Realised top quartile by count, intersected with the predicted top N:
            # this is what `max_positions=5` actually experiences. With n=3 the "top 5"
            # is the whole pool and a 1.0 hit rate would be arithmetic, not skill.
            quartile_size = max(1, int(np.ceil(sample_count / 4.0)))
            leaders = day_frame.nlargest(top_n, 'score', keep='first')
            predicted_top = set(leaders['ts_code'])
            realised_top = set(day_frame.nlargest(quartile_size, 'fwd_return', keep='first')['ts_code'])
            hit_rates.append(len(predicted_top & realised_top) / float(top_n))
            top_bucket_rates.append(float(leaders['label_top_bucket'].mean()))
            leader_returns.append(float(leaders['fwd_return'].mean()))
            pool_returns.append(float(day_frame['fwd_return'].mean()))
        else:
            skipped_small += 1

    ic_series = np.asarray(daily_ics, dtype=np.float64)
    ic_std = float(ic_series.std(ddof=1)) if ic_series.size > 1 else 0.0
    metrics = {
        'rank_ic': _mean(daily_ics),
        'ic_std': ic_std if ic_series.size > 1 else None,
        'ic_dates': int(ic_series.size),
        'icir': float(ic_series.mean() / ic_std) if ic_series.size > 1 and ic_std > 0 else None,
        'top_n': top_n,
        'top_n_hit_rate': _mean(hit_rates),
        'top_n_hit_dates': len(hit_rates),
        'top_n_top_bucket_rate': _mean(top_bucket_rates),
        'top_n_mean_fwd_return': _mean(leader_returns),
        'pool_mean_fwd_return': _mean(pool_returns),
        'top_n_excess_fwd_return': (
            float(np.mean(leader_returns) - np.mean(pool_returns)) if leader_returns else None
        ),
        'skipped_dates_ic_undefined': skipped_ic,
        'skipped_dates_pool_below_top_n': skipped_small,
        'valid_rows': int(len(scored)),
        'valid_dates': int(scored['date'].nunique()) if len(scored) else 0,
    }
    return metrics, [float(value) for value in ic_series]


# ---------------------------------------------------------------------------
# Fold split construction and training
# ---------------------------------------------------------------------------

def _fold_valid_dates(fold, calendar_dates):
    valid_start = _as_date(fold.valid_start)
    valid_end = _as_date(fold.valid_end)
    return sorted(day for day in calendar_dates if valid_start <= day <= valid_end), valid_start, valid_end


def _build_fold_dataset(panel, sample_positions, mean, scale):
    return _WindowDataset(
        panel['X'],
        panel['starts'][sample_positions],
        panel['ends'][sample_positions],
        panel['y_top'][sample_positions],
        panel['rank_pct'][sample_positions],
        panel['target_date_pos'][sample_positions],
        mean, scale, panel['sequence_length'],
    )


def _validate_temporal_split(panel, train_positions, valid_positions, train_dates, valid_dates):
    """Prove the split before spending epochs on it; raise instead of training a leak.

    Checks, in order:
    (a) train and holdout share no sample index and no target date;
    (b) no training *window row* falls on a holdout date -- a training sequence that
        reads the future is the leak this whole module exists to avoid;
    (c) every window, train or holdout, spans exactly ``sequence_length``
        calendar-contiguous rows and ends on its own target date, so no sample can
        see a bar after the date it is predicting (a within-sample look-ahead that a
        date-level split check would not catch).
    """
    train_positions = np.asarray(train_positions, dtype=np.int64)
    valid_positions = np.asarray(valid_positions, dtype=np.int64)
    if train_positions.size == 0:
        raise ETFRotationLSTMTrainingError('fold has no training sequences.')
    if np.intersect1d(train_positions, valid_positions).size:
        raise ETFRotationLSTMTrainingError('train and validation sample indices overlap.')
    if set(train_dates) & set(valid_dates):
        raise ETFRotationLSTMTrainingError('train and validation date sets overlap; purge did not take effect.')

    calendar_dates = panel['calendar_dates']
    length = int(panel['sequence_length'])
    first_row = panel['target_date_pos'][train_positions] - (length - 1)
    if int(first_row.min()) < 0:
        raise ETFRotationLSTMTrainingError('a training window starts before the supplied calendar.')
    touched = np.zeros(len(calendar_dates), dtype=bool)
    for offset in range(length):
        touched[first_row + offset] = True

    holdout_dates = {calendar_dates[pos] for pos in panel['target_date_pos'][valid_positions]}
    overlap = sorted(calendar_dates[i] for i in np.flatnonzero(touched) if calendar_dates[i] in holdout_dates)
    if overlap:
        raise ETFRotationLSTMTrainingError(
            f'{len(overlap)} holdout dates appear inside training windows, earliest {overlap[0]}; '
            'refusing to train on a leaked split.'
        )

    for name, index_set in (('train', train_positions), ('validation', valid_positions)):
        if index_set.size == 0:
            continue
        if not bool(np.all(panel['ends'][index_set] - panel['starts'][index_set] + 1 == length)):
            raise ETFRotationLSTMTrainingError(f'a {name} window is not sequence_length rows long.')
        end_pos = panel['row_date_pos'][panel['ends'][index_set]]
        if not bool(np.all(end_pos == panel['target_date_pos'][index_set])):
            raise ETFRotationLSTMTrainingError(f'a {name} window does not end on its own target date.')
        start_pos = panel['row_date_pos'][panel['starts'][index_set]]
        if not bool(np.all(end_pos - start_pos == length - 1)):
            raise ETFRotationLSTMTrainingError(f'a {name} window bridges a trading gap.')

    return {
        'train_window_dates_touched': int(touched.sum()),
        'train_max_date': max(calendar_dates[pos] for pos in panel['target_date_pos'][train_positions]),
        'valid_min_date': (
            min(calendar_dates[pos] for pos in panel['target_date_pos'][valid_positions])
            if valid_positions.size else None
        ),
    }


def _train_fold(*, fold, panel, config, device, purge):
    """Train one walk-forward fold and return ``(report, payload_or_None)``.

    Takes an already-assembled panel, a fold-shaped object and the ``(kept, dropped)``
    training dates from the fold plan, so the split and the scaler rule can be
    exercised on synthetic data without a database. ``payload`` is ``None`` when the
    fold was skipped or failed; the report then carries the reason and no epochs are
    spent.
    """
    calendar_dates = panel['calendar_dates']
    kept, dropped = purge
    train_start, train_end = _as_date(fold.train_start), _as_date(fold.train_end)
    valid_dates, valid_start, valid_end = _fold_valid_dates(fold, calendar_dates)

    # Defence in depth, not a re-implementation of the purge: even a fold plan that
    # handed back a date inside its own validation block cannot train on it.
    train_dates = sorted(
        day for day in kept if train_start <= day <= train_end and day < valid_start
    )
    position_of = {day: index for index, day in enumerate(calendar_dates)}

    report = {
        'fold_index': int(getattr(fold, 'fold_index', 0)),
        'train_start': train_start.isoformat(), 'train_end': train_end.isoformat(),
        'valid_start': valid_start.isoformat(), 'valid_end': valid_end.isoformat(),
        'purged_train_dates': int(getattr(fold, 'purged_train_dates', 0)),
        'train_dates_dropped': len(dropped), 'train_dates_kept': len(train_dates),
        'valid_dates': len(valid_dates),
        # ``train_rows``/``valid_rows``/``train_groups``/``purged_train_dates`` exist under
        # the LightGBM names because ``train_etf_rotation_model --type lstm`` formats those
        # keys directly (``fold['purged_train_dates']``). A KeyError there would be the worst
        # possible way to report the result of a completed multi-fold run.
        'train_rows': 0, 'valid_rows': 0, 'train_groups': len(train_dates),
        'train_group_min': 0, 'train_group_max': 0,
        'train_sequences': 0, 'valid_sequences': 0,
        'metrics': {},
    }
    if not train_dates or not valid_dates:
        return {**report, 'status': 'skipped', 'reason': 'empty fold date range after purge'}, None

    is_train_date = np.zeros(len(calendar_dates), dtype=bool)
    is_train_date[[position_of[day] for day in train_dates]] = True
    is_valid_date = np.zeros(len(calendar_dates), dtype=bool)
    is_valid_date[[position_of[day] for day in valid_dates]] = True

    train_positions = np.flatnonzero(is_train_date[panel['target_date_pos']])
    valid_positions = np.flatnonzero(is_valid_date[panel['target_date_pos']])
    # One "group" is one date's cross-section, the same unit lambdarank groups on; the
    # LSTM sees the same partition, so a per-date group count belongs in this report.
    train_group_sizes = np.bincount(panel['target_date_pos'][train_positions], minlength=len(calendar_dates))
    train_group_sizes = train_group_sizes[train_group_sizes > 0]
    report.update({
        'train_sequences': int(train_positions.size),
        'valid_sequences': int(valid_positions.size),
        'train_rows': int(train_positions.size),
        'valid_rows': int(valid_positions.size),
        'train_group_min': int(train_group_sizes.min()) if train_group_sizes.size else 0,
        'train_group_max': int(train_group_sizes.max()) if train_group_sizes.size else 0,
    })
    if train_positions.size < int(config['min_train_sequences']) or valid_positions.size < 8:
        return {**report, 'status': 'skipped', 'reason': 'too few sequences in fold'}, None

    # Point-in-time scaler: the fitted rows are exactly the rows whose date is a
    # purged train date of this fold, so no holdout bar contributes a mean or a std.
    train_row_mask = is_train_date[panel['row_date_pos']]
    fitted_dates = sorted({calendar_dates[pos] for pos in np.unique(panel['row_date_pos'][train_row_mask])})
    if int(train_row_mask.sum()) < int(config['min_scaler_rows']):
        return {**report, 'status': 'skipped', 'reason': 'too few train rows to fit the scaler'}, None
    mean, scale = _row_statistics(panel['X'][train_row_mask])

    guards = _validate_temporal_split(panel, train_positions, valid_positions, train_dates, valid_dates)
    if not guards['train_max_date'] < valid_start:
        raise ETFRotationLSTMTrainingError('a training target date is not before the holdout start.')

    train_loader = DataLoader(
        _build_fold_dataset(panel, train_positions, mean, scale),
        batch_size=int(config['batch_size']), shuffle=True, num_workers=0, drop_last=False,
        generator=torch.Generator().manual_seed(int(config['seed']) + int(report['fold_index'])),
    )
    valid_loader = DataLoader(
        _build_fold_dataset(panel, valid_positions, mean, scale),
        batch_size=int(config['batch_size']), shuffle=False, num_workers=0, drop_last=False,
    )

    torch.manual_seed(int(config['seed']) + int(report['fold_index']))
    model = ETFRotationLSTM(
        input_size=panel['X'].shape[1], hidden_size=config['hidden_size'],
        num_layers=config['num_layers'], dropout=config['dropout'],
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(config['learning_rate']))

    best_state = None
    best_valid_loss = float('inf')
    best_epoch = None
    stale_epochs = 0
    history = []
    for epoch in range(1, int(config['epochs']) + 1):
        train_loss, train_scores, train_targets = _forward_pass(model, train_loader, device, config, optimizer=optimizer)
        valid_loss, valid_scores, valid_targets = _forward_pass(model, valid_loader, device, config)
        if not math.isfinite(valid_loss):
            return {**report, 'status': 'skipped', 'reason': 'non-finite validation loss', 'epoch': epoch}, None
        history.append({
            'epoch': epoch, 'train_loss': float(train_loss), 'valid_loss': float(valid_loss),
            'in_sample_top_bucket_accuracy': _threshold_accuracy(train_scores, train_targets),
        })
        if valid_loss < best_valid_loss - 1e-6:
            best_valid_loss = float(valid_loss)
            best_epoch = epoch
            stale_epochs = 0
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        else:
            stale_epochs += 1
            if stale_epochs >= int(config['patience']):
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()

    final_valid_loss, scores, _targets = _forward_pass(model, valid_loader, device, config)
    scored = pd.DataFrame({
        'ts_code': [panel['codes'][code] for code in panel['target_code'][valid_positions]],
        'date': [calendar_dates[pos] for pos in panel['target_date_pos'][valid_positions]],
        'score': scores.astype(np.float64),
        'probability': 1.0 / (1.0 + np.exp(-scores.astype(np.float64))),
        'fwd_return': panel['fwd'][valid_positions].astype(np.float64),
        'label_rank_pct': panel['rank_pct'][valid_positions].astype(np.float64),
        'label_top_bucket': panel['y_top'][valid_positions].astype(np.float64) > 0.5,
    })
    metrics, daily_ic = _holdout_metrics(scored, config['top_n'])
    _final_train_loss, in_sample_scores, in_sample_targets = _forward_pass(model, train_loader, device, config)

    report.update({
        'status': 'trained',
        'scaler_fitted_rows': int(train_row_mask.sum()),
        'scaler_min_date': min(fitted_dates).isoformat(),
        'scaler_max_date': max(fitted_dates).isoformat(),
        'scaler_fit_dates': [day.isoformat() for day in fitted_dates],
        'epochs_run': len(history),
        'best_epoch': best_epoch,
        'best_valid_loss': best_valid_loss,
        'final_valid_loss': float(final_valid_loss),
        'in_sample_top_bucket_accuracy_not_evidence': _threshold_accuracy(in_sample_scores, in_sample_targets),
        'history': history,
        'metrics': metrics,
    })
    payload = {
        'model': model, 'mean': mean, 'scale': scale, 'scored': scored, 'daily_ic': daily_ic,
        'train_dates': train_dates, 'valid_dates': valid_dates, 'guards': guards,
        'fitted_dates': fitted_dates, 'fitted_rows': int(train_row_mask.sum()),
    }
    return report, payload


def _threshold_accuracy(scores, targets):
    """In-sample top-bucket accuracy at the logit zero cut. Reported, never relied on."""
    if scores is None or targets is None or len(scores) == 0:
        return None
    predicted = np.asarray(scores) > 0.0
    return float(np.mean(predicted == (np.asarray(targets) > 0.5)))


# ---------------------------------------------------------------------------
# Device, versioning, artifacts
# ---------------------------------------------------------------------------

def _resolve_device(device=None):
    """CUDA when the host has it (WSL2-native venv), CPU otherwise (Docker)."""
    if device:
        return torch.device(str(device))
    return torch.device('cuda' if torch.cuda.is_available() else 'cpu')


def _normalize_version_tag(tag):
    return re.sub(r'[^0-9A-Za-z._-]', '', str(tag or ''))[:12]


def _artifact_dir(version):
    return os.path.join(str(settings.BASE_DIR), ETF_LSTM_MODELS_RELATIVE_PREFIX, version)


def _resolve_stored_path(stored_path):
    if not stored_path:
        return None
    text = str(stored_path)
    return text if os.path.isabs(text) else os.path.join(str(settings.BASE_DIR), text)


def _save_artifact(*, model, mean, scale, panel, config, version, fold_reports, headline, device):
    """``torch.save`` a self-describing payload, then mirror the dir to the store.

    The payload holds only tensors, primitives, lists and dicts: ``torch.load`` in
    modern torch defaults to ``weights_only=True`` and refuses anything else, so a
    ``date`` or a numpy scalar in here would make the artifact unloadable at
    inference time -- exactly when nobody wants to discover it.
    """
    model_dir = _artifact_dir(version)
    os.makedirs(model_dir, exist_ok=True)

    payload = {
        'state_dict': {key: value.detach().cpu() for key, value in model.state_dict().items()},
        'model_class': 'ETFRotationLSTM',
        'model_type': config['model_type_label'],
        'version': version,
        'input_size': int(panel['X'].shape[1]),
        'feature_names': list(panel['feature_names']),
        'base_feature_names': list(config['base_feature_names']),
        'sequence_length': int(panel['sequence_length']),
        'hidden_size': int(config['hidden_size']),
        'num_layers': int(config['num_layers']),
        'dropout': float(config['dropout']),
        'horizon_days': int(config['horizon_days']),
        'loss_mode': str(config['loss']),
        'scaler_mean': [float(value) for value in mean],
        'scaler_scale': [float(value) for value in scale],
        'missing_value_strategy': MISSING_VALUE_STRATEGY,
        'trained_at': timezone.now().isoformat(),
        'train_device': str(device),
        'load_contract': 'callable(snapshots_df) -> DataFrame[ts_code, score] via '
                         'apps.etf_rotation.train_lstm.load_active_predictor',
        'fold_reports': fold_reports,
        'headline_metrics': headline,
    }
    artifact_path = os.path.join(model_dir, SERVING_ARTIFACT_NAME)
    torch.save(payload, artifact_path)
    with open(os.path.join(model_dir, METRICS_ARTIFACT_NAME), 'w', encoding='utf-8') as handle:
        json.dump(fold_reports, handle, indent=2, sort_keys=True, allow_nan=False, default=str)

    store = get_artifact_store()
    store.upload_dir(model_dir, to_store_key(model_dir))
    return artifact_path, os.path.join(ETF_LSTM_MODELS_RELATIVE_PREFIX, version)


def _load_artifact(version_row, device):
    """Read the serving payload for a registry row, pulling from the store on a cold cache."""
    store = get_artifact_store()
    stored = _resolve_stored_path(version_row.artifact_path)
    directories = []
    if stored:
        directories.append(os.path.dirname(stored) if stored.endswith('.pt') else stored)
    fallback_dir = _artifact_dir(version_row.version)
    if fallback_dir not in directories:
        directories.append(fallback_dir)

    file_path = None
    for directory in directories:
        candidate = os.path.join(directory, SERVING_ARTIFACT_NAME)
        if not os.path.isfile(candidate) and store.is_remote:
            try:
                store.ensure_dir_local(to_store_key(directory), directory)
            except Exception as exc:
                logger.warning('ETF LSTM artifact download failed for %s: %s', directory, exc)
        if os.path.isfile(candidate):
            file_path = candidate
            break
    if file_path is None and stored and stored.endswith('.pt') and os.path.isfile(stored):
        file_path = stored
    if file_path is None:
        return None

    payload = torch.load(file_path, map_location=device)
    feature_names = list(payload.get('feature_names') or [])
    base_feature_names = list(payload.get('base_feature_names') or [])
    if not feature_names or not base_feature_names:
        return None

    model = ETFRotationLSTM(
        input_size=len(feature_names),
        hidden_size=int(payload.get('hidden_size') or 64),
        num_layers=int(payload.get('num_layers') or 2),
        dropout=float(payload.get('dropout') or 0.2),
    )
    model.load_state_dict(payload['state_dict'])
    model.to(device)
    model.eval()

    mean = np.asarray(payload.get('scaler_mean') or [0.0] * len(feature_names), dtype=np.float32)
    scale = np.asarray(payload.get('scaler_scale') or [1.0] * len(feature_names), dtype=np.float32)
    scale = np.where(np.isfinite(scale) & (scale > 1e-8), scale, 1.0)
    return {
        'model': model, 'feature_names': feature_names, 'base_feature_names': base_feature_names,
        'sequence_length': int(payload.get('sequence_length') or 20),
        'horizon_days': int(payload.get('horizon_days') or 20),
        'mean': mean, 'scale': scale, 'file_path': file_path, 'payload': payload,
    }


def _json_scalar(value):
    """Registry-metadata-safe copy of a caller-supplied parameter."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _finite(value, digits=6):
    if value is None:
        return None
    number = float(value)
    return None if not math.isfinite(number) else round(number, digits)


def _registry_fold_report(report):
    """JSON-safe per-fold summary for ``ModelVersion.metadata``.

    The raw per-date IC series and the per-day scaler date list are dropped: the
    aggregate holds their mean and std, and hundreds of floats per fold make the
    registry row unreadable in the admin.
    """
    slim = {key: value for key, value in report.items()
            if key not in ('history', 'scaler_fit_dates', 'metrics')}
    slim['scaler_fit_dates_count'] = len(report.get('scaler_fit_dates') or [])
    slim['metrics'] = {
        key: (_finite(value) if isinstance(value, (int, float, type(None))) and not isinstance(value, bool) else value)
        for key, value in (report.get('metrics') or {}).items()
    }
    slim['history'] = [
        {'epoch': entry['epoch'], 'train_loss': _finite(entry['train_loss']), 'valid_loss': _finite(entry['valid_loss'])}
        for entry in (report.get('history') or [])
    ]
    for key in ('best_valid_loss', 'final_valid_loss', 'in_sample_top_bucket_accuracy_not_evidence'):
        slim[key] = _finite(slim.get(key))
    return slim


def _register_model_version(*, version, config, artifact_relative_path, fold_reports, headline,
                            aggregate, panel, scaler_stats, training_start, training_end, metadata):
    """Register ONE active ETF_LSTM row per run, flipping prior rows of this type inactive."""
    ModelVersion.objects.filter(
        model_type=config['model_type_label'], is_active=True,
    ).exclude(version=version).update(is_active=False)

    return ModelVersion.objects.update_or_create(
        model_type=config['model_type_label'],
        version=version,
        defaults={
            'status': ModelVersion.Status.READY,
            'artifact_path': artifact_relative_path,
            'metrics': headline,
            'feature_schema': list(panel['feature_names']),
            'training_window_start': training_start,
            'training_window_end': training_end,
            'trained_at': timezone.now(),
            'is_active': True,
            'metadata': {
                **metadata,
                'framework': 'expanding_window_walk_forward_lstm',
                'model_type_label': config['model_type_label'],
                'version': version,
                'loss_mode': config['loss'],
                'loss_note': (
                    'BCEWithLogitsLoss on label_top_bucket with a scalar-score head (choice a). '
                    "The within-date pairwise ranking loss on label_rank_pct (choice b) is "
                    "implemented and selected with params={'loss': 'pairwise_rank'}."
                ),
                'head': 'LSTM last hidden -> Linear/GELU/Dropout/Linear -> scalar',
                'hyperparameters': {
                    'hidden_size': int(config['hidden_size']), 'num_layers': int(config['num_layers']),
                    'dropout': float(config['dropout']), 'epochs': int(config['epochs']),
                    'batch_size': int(config['batch_size']), 'learning_rate': float(config['learning_rate']),
                    'patience': int(config['patience']), 'max_grad_norm': float(config['max_grad_norm']),
                    'seed': int(config['seed']), 'top_n': int(config['top_n']),
                    'min_train_sequences': int(config['min_train_sequences']),
                    'min_scaler_rows': int(config['min_scaler_rows']),
                },
                'params': {key: _json_scalar(value) for key, value in config['params'].items()},
                'sequence_length': int(config['sequence_length']),
                'horizon_days': int(config['horizon_days']),
                'min_train_days': int(config['min_train_days']),
                'missing_value_strategy': MISSING_VALUE_STRATEGY,
                'missing_indicator_suffix': MISSING_INDICATOR_SUFFIX,
                'feature_columns': list(config['base_feature_names']),
                'augment_feature_names': list(panel['feature_names']),
                'purge_rule': [
                    'training target dates come from walk_forward.train_dates_for_fold, then are '
                    "re-filtered to [train_start, train_end] and < valid_start",
                    'windows are trailing and end on their own target date, so a training window '
                    'cannot contain a holdout bar',
                    'a window is dropped when the eligible-date calendar jumps, so trading gaps '
                    'are never bridged into one sequence',
                ],
                'scaler_rule': (
                    'per fold, fit column mean/std on rows whose date is in that fold purged train '
                    'dates; applied unchanged to the holdout; never fit on the whole panel'
                ),
                'shuffle_rule': (
                    'batches are shuffled inside the train index set only; train and holdout sample '
                    'indices are disjoint by date and never concatenated'
                ),
                'scaler': scaler_stats,
                'folds': fold_reports,
                'aggregate': aggregate,
                'deployment_note': (
                    'The registered artifact is the LAST trained fold (the most history). Fold '
                    'metrics are the out-of-sample evidence for the specification, not a claim '
                    'about the earlier fold weights, which are not shipped.'
                ),
            },
        },
    )[0]


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _build_config(*, model_type_label, start_date, end_date, n_folds, horizon_days, sequence_length,
                  hidden_size, num_layers, dropout, epochs, batch_size, learning_rate, patience, params,
                  objective=None, num_boost_round=None, min_train_days=None, version_tag=''):
    model_type = str(model_type_label or MODEL_TYPE_LABEL)
    if model_type not in ModelVersion.ModelType.values:
        raise ETFRotationLSTMTrainingError(
            f"model_type_label '{model_type}' is not a ModelVersion.ModelType; refusing to write the "
            'registry under a type another pipeline owns.'
        )
    if model_type != MODEL_TYPE_LABEL:
        logger.warning('training the ETF LSTM under model_type=%s; load_active_predictor reads %s',
                       model_type, MODEL_TYPE_LABEL)

    overrides = dict(params or {})
    config = {
        'model_type_label': model_type,
        'start_date': start_date, 'end_date': end_date,
        'n_folds': int(n_folds),
        # ``None`` means "whatever the label builder predicted", i.e. the same
        # settings.ETF_LABEL_HORIZON_DAYS the LightGBM sibling resolves, so one settings
        # change moves both models together. A mismatch against the stored
        # ``label_horizon_days`` then surfaces as insufficient_data, not a silent regrade.
        'horizon_days': _setting_int('ETF_LABEL_HORIZON_DAYS', 20) if horizon_days is None else int(horizon_days),
        'sequence_length': int(sequence_length),
        'hidden_size': int(hidden_size), 'num_layers': int(num_layers), 'dropout': float(dropout),
        'epochs': int(epochs), 'batch_size': int(batch_size), 'learning_rate': float(learning_rate),
        'patience': int(patience),
        'min_train_days': (_setting_int('ETF_MIN_TRAIN_DAYS', 250) if min_train_days is None
                           else int(min_train_days)),
        'min_train_sequences': _setting_int('ETF_MIN_TRAIN_ROWS', 200),
        'min_scaler_rows': 256,
        'max_grad_norm': 5.0, 'seed': 7, 'loss': LOSS_BCE,
        'version_tag': _normalize_version_tag(version_tag or overrides.get('version_tag')),
        # Top-N hit rate has to count the positions the engine actually holds. The
        # sibling reads an unset ETF_ROTATION_MAX_POSITIONS and quietly stays at 5; this
        # uses the knob that exists, and ``params={'top_n': 5}`` pins it if the two models
        # must be compared on an identical cut.
        'top_n': _setting_int('ETF_MAX_POSITIONS', 5),
        # Accepted only because ``train_etf_rotation_model`` passes them to whichever
        # trainer is named. They change nothing here -- the sequence head has no boosting
        # round count and no LightGBM objective -- but they are recorded in the registry
        # metadata so a run is never silently configured differently from what was asked.
        'requested_objective': str(objective) if objective else None,
        'requested_num_boost_round': int(num_boost_round) if num_boost_round else None,
    }
    for key, value in overrides.items():
        if key not in config or value is None:
            continue
        default = config[key]
        if isinstance(default, str):
            config[key] = str(value)
        elif isinstance(default, bool):
            config[key] = bool(value)
        elif isinstance(default, (int, float)):
            config[key] = type(default)(value)
        else:
            config[key] = value
    if config['n_folds'] < 2:
        raise ETFRotationLSTMTrainingError('n_folds must be at least 2 for a walk-forward run.')
    if config['sequence_length'] < 2:
        raise ETFRotationLSTMTrainingError('sequence_length must be at least 2.')
    if config['horizon_days'] < 1:
        raise ETFRotationLSTMTrainingError('horizon_days must be at least 1.')
    if config['top_n'] < 1:
        raise ETFRotationLSTMTrainingError('top_n must be at least 1.')
    if config['loss'] not in (LOSS_BCE, LOSS_PAIRWISE):
        raise ETFRotationLSTMTrainingError(
            f"unknown loss '{config['loss']}'; expected '{LOSS_BCE}' or '{LOSS_PAIRWISE}'."
        )
    config['params'] = overrides
    return config


def _pool_stats(frame):
    sizes = frame.groupby('date').size() if frame is not None and not frame.empty else pd.Series(dtype=int)
    return {
        'unique_etfs': int(frame['ts_code'].nunique()) if frame is not None and not frame.empty else 0,
        'unique_dates': int(frame['date'].nunique()) if frame is not None and not frame.empty else 0,
        'pool_mean_per_date': float(sizes.mean()) if len(sizes) else 0.0,
        'pool_min_per_date': int(sizes.min()) if len(sizes) else 0,
        'pool_max_per_date': int(sizes.max()) if len(sizes) else 0,
    }


def train_walk_forward(*, model_type_label=MODEL_TYPE_LABEL, start_date=None, end_date=None,
                       n_folds=6, horizon_days=20, sequence_length=20, hidden_size=64,
                       num_layers=2, dropout=0.2, epochs=50, batch_size=256, learning_rate=1e-3,
                       patience=5, device=None, params=None, objective=None,
                       num_boost_round=None, min_train_days=None, version_tag=''):
    """Expanding-window walk-forward LSTM training for the ETF rotation pool.

    Returns the same envelope shape as ``apps.etf_rotation.train_lightgbm.train_walk_forward``
    so the management command and the backtest engine can read either: ``status``
    (``'success'`` / ``'insufficient_data'`` / ``'no_data'``), ``model_version``,
    ``model_version_id``, ``artifact_path``, ``metrics`` (pooled holdout headline),
    ``aggregate``, ``pooled_holdout``, ``folds`` and ``n_folds``. Registers exactly one
    active ``ModelVersion(model_type='ETF_LSTM', status=READY)`` per successful run.

    ``params`` carries the knobs that are not in the published signature:
    ``min_train_days`` (fold plan), ``min_train_sequences``, ``min_scaler_rows``,
    ``max_grad_norm``, ``top_n``, ``seed``, ``loss`` ('bce_top_bucket' |
    'pairwise_rank') and ``version_tag``.

    ``objective``, ``num_boost_round``, ``min_train_days`` and ``version_tag`` are accepted
    as named arguments because ``manage.py train_etf_rotation_model --type lstm`` passes the
    LightGBM flag set to whichever trainer is named: rejecting them would make the shared
    command crash on the LSTM branch, while honouring ``objective``/``num_boost_round``
    would be meaningless for an LSTM. They are recorded in the registry metadata and logged,
    never silently dropped; ``params['loss']`` is the only thing that chooses the objective
    here.
    """
    config = _build_config(
        model_type_label=model_type_label, start_date=start_date, end_date=end_date, n_folds=n_folds,
        horizon_days=horizon_days, sequence_length=sequence_length, hidden_size=hidden_size,
        num_layers=num_layers, dropout=dropout, epochs=epochs, batch_size=batch_size,
        learning_rate=learning_rate, patience=patience, params=params,
        objective=objective, num_boost_round=num_boost_round,
        min_train_days=min_train_days, version_tag=version_tag,
    )
    if config['requested_objective'] or config['requested_num_boost_round']:
        logger.info(
            'ETF LSTM ignores the LightGBM knobs objective=%s num_boost_round=%s; its objective is '
            "loss='%s' (params={'loss': ...}) and capacity is epochs=%s.",
            config['requested_objective'], config['requested_num_boost_round'],
            config['loss'], config['epochs'],
        )
    resolved_device = _resolve_device(device)
    base_feature_names = _feature_columns()
    config['base_feature_names'] = base_feature_names
    expanding_folds, train_dates_for_fold, audit_folds = _fold_api()

    start, end = _resolve_window(config['start_date'], config['end_date'])
    base = {
        'status': 'ok', 'model_type_label': config['model_type_label'],
        'start_date': start.isoformat() if start else None,
        'end_date': end.isoformat() if end else None,
        'horizon_days': int(config['horizon_days']), 'sequence_length': int(config['sequence_length']),
        'min_train_days': int(config['min_train_days']), 'n_folds_requested': int(config['n_folds']),
        'feature_columns': base_feature_names, 'feature_count': len(base_feature_names),
        'loss': config['loss'],
        # ``--report`` shows this column for both trainers; for a sequence model the
        # equivalent of an objective is its loss.
        'objective': config['loss'], 'version_tag': config['version_tag'],
        'requested_objective': config['requested_objective'],
        'requested_num_boost_round': config['requested_num_boost_round'],
        'lightgbm_available': False,
    }
    if start is None:
        return {**base, 'status': 'no_data', 'n_folds': 0, 'folds': [], 'metrics': {},
                'reason': 'ETFFeatureSnapshot holds no is_eligible rows in any window; build the '
                          'feature snapshots before training.', 'device': str(resolved_device)}

    calendar = _eligible_calendar(start, end)
    if not calendar:
        return {**base, 'status': 'no_data', 'n_folds': 0, 'folds': [], 'metrics': {},
                'reason': f'No eligible snapshots between {start} and {end}.', 'device': str(resolved_device)}

    frame = _load_snapshot_frame(start_date=calendar[0], end_date=calendar[-1], feature_columns=base_feature_names)
    panel = _assemble_windows(
        frame=frame, base_feature_names=base_feature_names, calendar_dates=calendar,
        sequence_length=config['sequence_length'], require_labels=True, horizon_days=config['horizon_days'],
    )
    del frame
    if panel['starts'].size == 0:
        return {**base, 'status': 'insufficient_data', 'n_folds': 0, 'folds': [], 'metrics': {},
                'reason': 'no label-complete contiguous sequence in the requested range: every target '
                          'date either lacks a forward label for this horizon or is separated from the '
                          'bars before it by a gap.',
                'panel_start': calendar[0].isoformat(), 'panel_end': calendar[-1].isoformat(),
                'device': str(resolved_device)}

    # The fold plan is built over the dates that can actually be *targets*, which is
    # what walk_forward's purge indexes against and what the LightGBm sibling does;
    # planning to the raw panel tail would put the final fold's holdout inside the
    # horizon_days of dates whose forward returns have not happened yet.
    panel_dates = sorted({calendar[pos] for pos in np.unique(panel['target_date_pos'])})
    folds = expanding_folds(
        panel_dates[0], panel_dates[-1], int(config['n_folds']),
        horizon_days=int(config['horizon_days']), min_train_days=int(config['min_train_days']),
        trade_dates=panel_dates,
    )
    base.update({
        'panel_start': panel_dates[0].isoformat(), 'panel_end': panel_dates[-1].isoformat(),
        'panel_dates': len(panel_dates), 'n_folds_built': len(folds),
        'augment_feature_count': int(panel['X'].shape[1]),
        'pool': _pool_stats(pd.DataFrame({
            'date': [calendar[pos] for pos in panel['target_date_pos']],
            'ts_code': [panel['codes'][code] for code in panel['target_code']],
        })),
    })
    if not folds:
        return {**base, 'status': 'insufficient_data', 'n_folds': 0, 'folds': [], 'metrics': {},
                'reason': (
                    f'Panel spans {len(panel_dates)} label-complete trading dates; one fold needs at '
                    f'least min_train_days({config["min_train_days"]}) + 2 + horizon_days'
                    f'({config["horizon_days"]}). Lower n_folds / min_train_days, or backfill history.'
                ),
                'device': str(resolved_device)}

    # Re-audit the plan against this panel: a declared purge count that disagrees with
    # the applied one means plan and fit drifted apart, and every fold metric after
    # that point would be measuring the answer key.
    trail = {item['fold_index']: item for item in audit_folds(folds, panel_dates, int(config['horizon_days']))}

    reports, payloads, scored_blocks = [], [], []
    for fold in folds:
        kept, dropped = train_dates_for_fold(fold, panel_dates, int(config['horizon_days']))
        if len(dropped) != int(fold.purged_train_dates):
            raise ETFRotationLSTMTrainingError(
                f'Fold {fold.fold_index}: the purge removed {len(dropped)} dates but the plan declared '
                f'{fold.purged_train_dates}; refusing to train on an unaudited split.'
            )
        report, payload = _train_fold(
            fold=fold, panel=panel, config=config, device=resolved_device, purge=(kept, dropped),
        )
        report['purge_audit'] = trail.get(report['fold_index'], {})
        reports.append(report)
        if payload is not None:
            payloads.append(payload)
            scored_blocks.append(payload['scored'])

    fold_reports = [_registry_fold_report(report) for report in reports]
    trained = [report for report in reports if report.get('status') == 'trained']
    if not trained:
        return {
            **base, 'status': 'insufficient_data', 'version': None, 'folds': fold_reports, 'metrics': {},
            'n_folds': len(fold_reports),
            'reason': (
                f'No fold reached >= {config["min_train_sequences"]} purged training sequences with a '
                f'non-empty label-complete holdout ({len(reports)} folds planned, '
                f'{len(panel_dates)} panel dates, horizon {config["horizon_days"]}, '
                f'sequence_length {config["sequence_length"]}).'
            ),
            'per_fold_reasons': [report.get('reason') for report in reports],
            'device': str(resolved_device),
        }

    pooled_metrics, _pooled_ic = _holdout_metrics(pd.concat(scored_blocks, ignore_index=True), config['top_n'])
    aggregate = {
        'mean_rank_ic': _mean([report['metrics'].get('rank_ic') for report in trained]),
        'mean_icir': _mean([report['metrics'].get('icir') for report in trained]),
        'mean_top_n_hit_rate': _mean([report['metrics'].get('top_n_hit_rate') for report in trained]),
        'mean_top_n_top_bucket_rate': _mean([report['metrics'].get('top_n_top_bucket_rate') for report in trained]),
        'folds_trained': len(trained),
        'folds_skipped': len(reports) - len(trained),
        'folds_with_positive_ic': sum(1 for report in trained if (report['metrics'].get('rank_ic') or 0.0) > 0),
        'total_purged_train_dates': int(sum(report['train_dates_dropped'] for report in reports)),
        'total_train_sequences': int(sum(report['train_sequences'] for report in trained)),
        'total_valid_sequences': int(sum(report['valid_sequences'] for report in trained)),
    }
    headline = {
        'rank_ic': pooled_metrics['rank_ic'], 'icir': pooled_metrics['icir'],
        'ic_std': pooled_metrics['ic_std'], 'ic_dates': pooled_metrics['ic_dates'],
        'top_n': pooled_metrics['top_n'],
        'top_n_hit_rate': pooled_metrics['top_n_hit_rate'],
        'top_n_hit_dates': pooled_metrics['top_n_hit_dates'],
        'top_n_top_bucket_rate': pooled_metrics['top_n_top_bucket_rate'],
        'top_n_mean_fwd_return': pooled_metrics['top_n_mean_fwd_return'],
        'pool_mean_fwd_return': pooled_metrics['pool_mean_fwd_return'],
        'top_n_excess_fwd_return': pooled_metrics['top_n_excess_fwd_return'],
        'valid_rows': pooled_metrics['valid_rows'], 'valid_dates': pooled_metrics['valid_dates'],
        'sequence_length': int(config['sequence_length']), 'horizon_days': int(config['horizon_days']),
        'loss': config['loss'],
    }
    for key in ('rank_ic', 'icir', 'ic_std', 'top_n_hit_rate', 'top_n_top_bucket_rate',
                'top_n_mean_fwd_return', 'pool_mean_fwd_return', 'top_n_excess_fwd_return'):
        headline[key] = _finite(headline[key])

    version = f'etf-lstm-{panel_dates[-1].isoformat()}'
    if config['version_tag']:
        version = f'{version}-{config["version_tag"]}'
    version = version[:50]

    serving = payloads[-1]
    artifact_path, artifact_relative_path = _save_artifact(
        model=serving['model'], mean=serving['mean'], scale=serving['scale'], panel=panel,
        config=config, version=version, fold_reports=fold_reports, headline=headline, device=resolved_device,
    )
    scaler_stats = {
        'mean': [float(value) for value in serving['mean']],
        'scale': [float(value) for value in serving['scale']],
        'fitted_on': 'rows dated within the last trained fold purged train dates only',
        'fitted_min_date': min(serving['fitted_dates']).isoformat(),
        'fitted_max_date': max(serving['fitted_dates']).isoformat(),
        'first_holdout_date': serving['valid_dates'][0].isoformat(),
        'holdout_dates_used_in_fit': len(set(serving['fitted_dates']) & set(serving['valid_dates'])),
        'train_max_target_date': serving['guards']['train_max_date'].isoformat(),
        'fitted_rows': int(serving['fitted_rows']),
    }
    row = _register_model_version(
        version=version, config=config, artifact_relative_path=artifact_relative_path,
        fold_reports=fold_reports, headline=headline, aggregate=aggregate, panel=panel,
        scaler_stats=scaler_stats, training_start=panel_dates[0], training_end=panel_dates[-1],
        # `pooled_holdout` in metadata is the key train_lightgbm.register_ensemble_version
        # reads to weight this member, so the LSTM must carry it under the same name.
        metadata={**base, 'pooled_holdout': pooled_metrics,
                  'total_purged_train_dates': aggregate['total_purged_train_dates'],
                  'training_window': [panel_dates[0].isoformat(), panel_dates[-1].isoformat()]},
    )
    logger.info('ETF LSTM walk-forward %s: folds=%s pooled rank_ic=%s icir=%s',
                version, aggregate['folds_trained'], headline['rank_ic'], headline['icir'])

    return {
        **base,
        'status': 'success',
        'model_version': row.version,
        'model_version_id': row.id,
        'artifact_path': row.artifact_path,
        'artifact_dir': os.path.dirname(artifact_path),
        'version': version,
        'folds': fold_reports,
        'n_folds': len(fold_reports),
        'folds_trained': aggregate['folds_trained'],
        'metrics': headline,
        'aggregate': aggregate,
        'pooled_holdout': pooled_metrics,
        'scaler': scaler_stats,
        'epochs_run': [report['epochs_run'] for report in trained],
        'device': str(resolved_device),
        'cuda_available': bool(torch.cuda.is_available()),
    }


def load_active_predictor(model_type_label=MODEL_TYPE_LABEL, device=None):
    """``callable(snapshots_df) -> DataFrame[ts_code, score]`` for the active ETF LSTM, else ``None``.

    ``None`` when nothing registered is READY, so the rotation engine falls back to
    rule-based ranking instead of crashing on a cold environment. Callable with no
    arguments, exactly like ``train_lightgbm.load_active_predictor``.

    Contract callers depend on:

    * The result is **row-aligned with the input frame** (one row per input snapshot,
      same order) with exactly the columns ``ts_code`` and ``score``. An ETF whose
      trailing ``sequence_length`` bars are not available contiguously gets ``NaN``
      rather than a fabricated score, so it sorts last and is never selected; a caller
      that wants only the scoreable rows can ``dropna()``.
    * ``snapshots_df`` needs a ``ts_code`` (or ``etf__ts_code``) column and, ideally, a
      ``date`` column. Dates are honoured per row, so a caller passing several dates
      gets each date's own cross-section scored as-of that date. With no ``date``
      column every row is scored as-of the newest eligible snapshot date (or the
      ``as_of=`` argument, which exists because ``rotation_engine._call_predictor``
      hands the predictor ``['ts_code', *features]`` and drops ``date``: fine for
      tonight's signal, wrong for a historical panel, so that case logs a warning).
    * The trailing window is read from ``ETFFeatureSnapshot`` (``is_eligible=True``),
      not from the caller's feature columns, because 20 bars cannot come from one row.
      Higher score = better momentum. ``score`` is ``sigmoid(scalar head)`` in (0, 1),
      rank-identical to the raw logit and comparable with a LightGBM ``binary``
      probability, which is what makes the two slots interchangeable.
    * The scaler and feature order come from the artifact; nothing is re-fit here, and
      a missing feature column is treated as missing (0 + indicator), never as a new
      mean estimated from today's pool.
    * Runs on CPU whenever CUDA is absent (Docker); the GPU is an optimisation, never
      a requirement.
    """
    version_row = _resolve_active_version(str(model_type_label or MODEL_TYPE_LABEL))
    if version_row is None:
        return None
    resolved_device = _resolve_device(device)
    artifact = _load_artifact(version_row, resolved_device)
    if artifact is None:
        logger.warning('Active ETF LSTM ModelVersion %s has no readable artifact at %s.',
                       version_row.version, version_row.artifact_path)
        return None

    model = artifact['model']
    mean, scale = artifact['mean'], artifact['scale']
    sequence_length = int(artifact['sequence_length'])
    base_feature_names = list(artifact['base_feature_names'])
    feature_names = list(artifact['feature_names'])

    def score(snapshots_df, *, as_of=None):
        if snapshots_df is None or len(snapshots_df) == 0:
            return pd.DataFrame({'ts_code': [], 'score': []})
        columns = list(getattr(snapshots_df, 'columns', []))
        code_column = 'ts_code' if 'ts_code' in columns else ('etf__ts_code' if 'etf__ts_code' in columns else None)
        if code_column is None:
            raise ETFRotationLSTMTrainingError(
                'the ETF LSTM scorer needs a ts_code (or etf__ts_code) column to locate each '
                "fund's trailing bars; got columns " + ', '.join(columns) + '.'
            )
        codes = [str(value) for value in snapshots_df[code_column].tolist()]
        if 'date' in columns:
            row_dates = [_as_date(value) if value is not None and str(value) != 'NaT' else None
                         for value in snapshots_df['date'].tolist()]
        elif as_of is not None:
            row_dates = [_as_date(as_of)] * len(codes)
        else:
            row_dates = [None] * len(codes)
        if not any(row_dates):
            fallback = _latest_eligible_date()
            if fallback is None:
                return pd.DataFrame({'ts_code': codes, 'score': [np.nan] * len(codes)})
            # A sequence model cannot be scored from one row: the trailing window comes
            # from the table, so a caller that dropped the date column silently fixes the
            # whole frame to the newest eligible bar. Right for tonight's signal, and a
            # look-ahead if a backtest hands it a historical cross-section -- hence a
            # warning rather than silence.
            logger.warning(
                'ETF LSTM scorer received %d rows with no date column; every row is scored '
                'as-of the newest eligible snapshot %s. Pass a date column (or as_of=) so a '
                'historical cross-section is scored as-of its own date.',
                len(codes), fallback,
            )
            row_dates = [fallback] * len(codes)
        # Rows with an unusable date keep their position and score NaN rather than
        # silently borrowing another row's as-of day.
        known = [day for day in row_dates if day is not None]

        lookback = timedelta(days=sequence_length * INFERENCE_LOOKBACK_DAYS_PER_BAR + INFERENCE_LOOKBACK_MARGIN_DAYS)
        calendar = _eligible_calendar(min(known) - lookback, max(known))
        if len(calendar) < 2:
            return pd.DataFrame({'ts_code': codes, 'score': [np.nan] * len(codes)})

        loaded = _load_snapshot_frame(
            start_date=calendar[0], end_date=max(known),
            feature_columns=base_feature_names, ts_codes=sorted(set(codes)),
        )
        panel = _assemble_windows(
            frame=loaded, base_feature_names=base_feature_names, calendar_dates=calendar,
            sequence_length=sequence_length, require_labels=False,
        )
        if panel['starts'].size == 0:
            return pd.DataFrame({'ts_code': codes, 'score': [np.nan] * len(codes)})
        if panel['feature_names'] != feature_names:
            raise ETFRotationLSTMTrainingError(
                f'the scoring panel has {len(panel["feature_names"])} columns but model '
                f'{version_row.version} was trained on {len(feature_names)}; FEATURE_COLUMNS changed '
                'under a registered model. Retrain before scoring.'
            )

        position_of = {day: index for index, day in enumerate(calendar)}
        by_target = {}
        for target_date in sorted(set(known)):
            index = position_of.get(target_date)
            if index is None:
                continue
            positions = np.flatnonzero(panel['target_date_pos'] == index)
            if positions.size == 0:
                continue
            dataset = _build_fold_dataset(panel, positions, mean, scale)
            loader = DataLoader(dataset, batch_size=max(256, len(dataset)), shuffle=False, num_workers=0)
            chunks = []
            with torch.inference_mode():
                for batch in loader:
                    chunks.append(torch.sigmoid(model(batch[0].to(resolved_device))).float().cpu().numpy())
            values = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
            for offset, sample in enumerate(positions):
                by_target[(panel['codes'][int(panel['target_code'][sample])], target_date)] = float(values[offset])

        # A fund with no bar on its own as-of date scores NaN: the alternative,
        # quietly scoring it off the last window that did close, is a stale ranking.
        return pd.DataFrame({
            'ts_code': codes,
            'score': [by_target.get((code, day), np.nan) for code, day in zip(codes, row_dates)],
        })

    return score


def _resolve_active_version(model_type):
    """Prefer the flagged-active READY row, then any READY row (tasks_lstm.py:260-288).

    The second lookup exists because a half-finished retrain can leave every row
    inactive; serving a READY model beats stalling the daily rotation, while
    TRAINING/FAILED rows stay ineligible.
    """
    ready = ModelVersion.objects.filter(model_type=model_type, status=ModelVersion.Status.READY)
    row = ready.filter(is_active=True).order_by('-trained_at', '-created_at').first()
    if row is None:
        row = ready.order_by('-trained_at', '-created_at').first()
    return row


def _latest_eligible_date():
    day = ETFFeatureSnapshot.objects.filter(is_eligible=True).aggregate(Max('date')).get('date__max')
    return _as_date(day) if day else None


@shared_task(bind=True, name='apps.etf_rotation.tasks.train_etf_rotation_lstm',
             soft_time_limit=5400, time_limit=5700)
def train_etf_rotation_lstm(self, **kwargs):
    """Celery entry point for the walk-forward LSTM.

    The name is the routing key the celery config is expected to carry even though
    the task is defined here. The project-wide 60s soft / 300s hard limit would kill
    a multi-fold training run inside the first fold, so both limits are overridden on
    the task itself.
    """
    return train_walk_forward(**{key: value for key, value in kwargs.items() if value is not None})
