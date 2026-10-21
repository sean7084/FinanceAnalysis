"""Walk-forward LightGBM ranker for the ETF momentum-rotation pool.

Two things this deliberately does differently from
:func:`apps.prediction.tasks_lightgbm.train_lightgbm_models`, whose *shape* (feature
frame -> native ``lgb.train`` -> pickle under ``models/`` -> ``ModelVersion`` with an
``is_active`` flip) is reused here:

1. Its reported metric is a holdout metric. The stock pipeline prints
   ``np.mean(y_pred == y_train)`` measured on the matrix it just fitted; a 200-tree booster
   over ~60 correlated features will report ~0.95 that way, which describes memory, not
   skill. Every number registered here comes from a block the booster never saw, produced by
   :mod:`apps.etf_rotation.walk_forward` with the label-window purge applied.
2. Its data layer is ETF-native. ``_create_feature_matrix`` is welded to the stock universe
   -- ``FactorScore``, ``SentimentScore`` and CSI500 point-in-time membership decide which
   rows exist -- so it cannot serve an ETF pool. Here the rows are exactly
   ``ETFFeatureSnapshot`` with ``is_eligible=True`` and the columns are exactly
   ``apps.etf_rotation.features.FEATURE_COLUMNS``.

No early stopping and no ``valid_sets`` reach ``lgb.train`` on purpose: choosing the tree
count against the same block you then report metrics on leaks the holdout into the model
through the hyperparameter selection. ``num_boost_round`` is fixed up front.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import re
from datetime import date

import numpy as np
import pandas as pd
from celery import shared_task
from django.conf import settings
from django.db.models import Max, Min
from django.utils import timezone

from apps.core.date_floor import get_historical_data_floor
from apps.prediction.artifact_store import get_artifact_store, to_store_key
from apps.prediction.models import ModelVersion

from .models import ETFFeatureSnapshot
from .walk_forward import WalkForwardError, audit_folds, expanding_folds, train_dates_for_fold

try:  # Guarded so --dry-run and --report work in an environment without the wheel.
    import lightgbm as lgb
except ImportError:  # pragma: no cover - environment dependent
    lgb = None

logger = logging.getLogger(__name__)

__all__ = [
    'DEFAULT_OBJECTIVE',
    'OBJECTIVES',
    'build_rank_matrix',
    'build_training_frame',
    'evaluate_holdout',
    'load_active_predictor',
    'load_snapshots',
    'plan_walk_forward',
    'register_ensemble_version',
    'registered_versions',
    'train_walk_forward',
]

OBJECTIVES = ('lambdarank', 'binary', 'regression')
DEFAULT_OBJECTIVE = 'lambdarank'

MODELS_RELATIVE_PREFIX = os.path.join('models', 'etf_rotation', 'lightgbm')

#: ``label_rank_pct`` bin edges, ascending -> five integer relevance grades (0..4).
#: Deliberately coarse: LightGBM's default gain curve runs to 32 levels, and a 40-document
#: query cannot populate them, so most of the DCG would sit on positions no ETF can reach.
#: Paired with ``label_gain`` below, which is the same 2^g - 1 curve truncated to 5 terms.
RELEVANCE_BINS = (0.2, 0.4, 0.6, 0.8)
LABEL_GAIN = [0, 1, 3, 7, 15]

#: Native API names only. ``n_estimators``/``subsample``/``colsample_bytree``/
#: ``reg_alpha``/``reg_lambda`` are *silently ignored* by ``lgb.train``, so a misspelling
#: degrades into "the defaults were used" while the run reports metrics as if the override
#: applied. That is why the whitelist is asserted, not just written down.
NATIVE_PARAM_NAMES = frozenset({
    'objective', 'num_leaves', 'learning_rate', 'feature_fraction', 'bagging_fraction',
    'bagging_freq', 'lambda_l1', 'lambda_l2', 'min_data_in_leaf', 'min_sum_hessian_in_leaf',
    'max_depth', 'num_threads', 'seed', 'deterministic', 'verbose', 'verbose_v2',
    'label_gain', 'max_position', 'lambdarank_truncation_level', 'metric', 'metric_freq',
    'first_metric_only', 'force_row_wise', 'boost_from_average', 'num_class',
})

DEFAULT_LGB_PARAMS = {
    'num_leaves': 31,
    'learning_rate': 0.05,
    'feature_fraction': 0.6,
    'bagging_fraction': 0.8,
    'bagging_freq': 5,
    'lambda_l1': 1.0,
    'lambda_l2': 1.0,
    'min_data_in_leaf': 50,
    'max_depth': -1,
    'num_threads': 4,
    'seed': 42,
    'deterministic': True,
    'force_row_wise': True,
    'verbose': -1,
}

#: Identity and look-ahead columns. Either appearing in ``FEATURE_COLUMNS`` puts the answer
#: key into ``X``, so this is a hard failure rather than something filtered quietly.
FORBIDDEN_FEATURE_NAMES = frozenset({
    'ts_code', 'etf', 'etf_id', 'etf__ts_code', 'date',
    'fwd_return', 'label_rank_pct', 'label_top_bucket', 'label_horizon_days',
    'is_eligible', 'metadata',
})

LABEL_COLUMNS = ('fwd_return', 'label_rank_pct', 'label_top_bucket', 'label_horizon_days')

#: A one-document query group carries no ordering information and contributes zero gradient
#: to lambdarank, so such dates are dropped from a ranking fit instead of inflating the
#: reported group count with groups that taught the model nothing.
MIN_LAMBDARANK_GROUP_SIZE = 2

#: Registry label -> the scorer name ``rotation_engine._rescale_ensemble_weights`` keys its
#: ``base_weights`` by. Two different keyspaces describe the same models, and the engine
#: raises "ensemble weights sum to zero" if it is handed the registry spelling, so the
#: ensemble row stores both and the caller picks the one its API expects.
ENGINE_SOURCE_NAMES = {'ETF_LIGHTGBM': 'LIGHTGBM', 'ETF_LSTM': 'LSTM'}


# -----------------------------------------------------------------------------
# Settings-backed tunables
# -----------------------------------------------------------------------------
def _setting_int(name, default: int) -> int:
    """First integer-valued setting among ``name`` (a name or an ordered list of names).

    Aliases exist because the ETF app was written concurrently: ``rotation_engine`` and
    ``train_lstm`` both read ``ETF_MAX_POSITIONS``, so the Top-N metric is keyed on that name
    first and only falls back to the longer alias.
    """
    names = name if isinstance(name, (tuple, list)) else [name]
    for candidate in names:
        value = getattr(settings, candidate, None)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def resolve_horizon_days(horizon_days=None) -> int:
    """Label horizon in the units ``features.py`` wrote ``label_horizon_days`` in.

    ``None`` means "take it from ``settings.ETF_LABEL_HORIZON_DAYS``" (default 20), so the
    documented ``horizon_days=20`` behaviour and a settings-driven override are the same call.
    """
    if horizon_days is None:
        horizon_days = _setting_int('ETF_LABEL_HORIZON_DAYS', 20)
    horizon_days = int(horizon_days)
    if horizon_days < 1:
        raise ValueError(f'horizon_days must be >= 1, got {horizon_days}.')
    return horizon_days


def resolve_params(objective: str, overrides: dict = None) -> dict:
    """Native-only parameter dict: defaults, then ``settings.ETF_LGB_PARAMS``, then ``overrides``."""
    if objective not in OBJECTIVES:
        raise ValueError(f'objective must be one of {OBJECTIVES}, got {objective!r}.')
    params = dict(DEFAULT_LGB_PARAMS)
    params.update(dict(getattr(settings, 'ETF_LGB_PARAMS', None) or {}))
    params.update(dict(overrides or {}))
    params['objective'] = objective
    if objective == 'lambdarank':
        params['label_gain'] = list(LABEL_GAIN)
    unknown = sorted(key for key in params if key not in NATIVE_PARAM_NAMES)
    if unknown:
        raise ValueError(
            f'Non-native LightGBM parameter name(s) {unknown}; lgb.train ignores these in '
            f'silence. Allowed: {sorted(NATIVE_PARAM_NAMES)}.'
        )
    return params


# -----------------------------------------------------------------------------
# Feature whitelist
# -----------------------------------------------------------------------------
def get_feature_columns() -> list:
    """``apps.etf_rotation.features.FEATURE_COLUMNS`` -- the only permitted feature source.

    Membership is never derived from ``ETFFeatureSnapshot._meta.fields``: that table also
    holds ``fwd_return``/``label_*`` (which look ahead) plus identity columns, so an
    introspected "every float column" list would silently train on the label the moment a
    feature was added. The module is being written concurrently, hence the lazy import and a
    failure that names exactly what is missing.
    """
    try:
        from apps.etf_rotation import features
    except ImportError as exc:
        raise RuntimeError(
            'apps.etf_rotation.features is not importable, so the feature whitelist cannot be '
            f'resolved ({exc}). This trainer refuses to guess features from the model schema.'
        ) from exc
    declared = getattr(features, 'FEATURE_COLUMNS', None)
    if not declared:
        raise RuntimeError(
            'apps.etf_rotation.features.FEATURE_COLUMNS is missing or empty; it is the whitelist '
            'this trainer reads and there is no fallback derivation from _meta.fields.'
        )
    columns = [str(name) for name in declared]
    duplicated = sorted({name for name in columns if columns.count(name) > 1})
    if duplicated:
        raise RuntimeError(f'FEATURE_COLUMNS contains duplicate entries: {duplicated}.')
    forbidden = sorted(set(columns) & FORBIDDEN_FEATURE_NAMES)
    if forbidden:
        raise RuntimeError(
            f'FEATURE_COLUMNS must not contain identity or look-ahead columns: {forbidden}.'
        )
    return columns


def feature_schema_version(columns) -> str:
    """Short stable hash of the ordered feature list, so schema drift is visible in metadata.

    Prefers ``features.FEATURE_SCHEMA_VERSION`` when that module publishes one.
    """
    declared = None
    try:
        from apps.etf_rotation import features
        declared = getattr(features, 'FEATURE_SCHEMA_VERSION', None)
    except ImportError:
        declared = None
    if declared:
        return str(declared)
    return hashlib.sha1('|'.join(str(name) for name in columns).encode('utf-8')).hexdigest()[:12]


# -----------------------------------------------------------------------------
# Data loading (DB read) and frame preparation (pure, unit-checkable)
# -----------------------------------------------------------------------------
def resolve_window(start_date=None, end_date=None):
    """Bound the run explicitly or by what is in the table, honouring ``HISTORICAL_DATA_FLOOR``.

    Returns ``(None, None)`` when there is nothing eligible to train on -- an expected
    operational state on a fresh install, not an exception.
    """
    end = (
        date.fromisoformat(str(end_date)) if end_date and not isinstance(end_date, date) else end_date
    )
    start = (
        date.fromisoformat(str(start_date)) if start_date and not isinstance(start_date, date) else start_date
    )
    if start is None or end is None:
        bounds = ETFFeatureSnapshot.objects.filter(is_eligible=True).aggregate(
            min_date=Min('date'), max_date=Max('date'),
        )
        start = start or bounds['min_date']
        end = end or bounds['max_date']
    if start is None or end is None:
        return None, None
    floor = get_historical_data_floor()
    if start < floor:
        logger.info('ETF training start %s raised to HISTORICAL_DATA_FLOOR %s.', start, floor)
        start = floor
    if end < start:
        raise ValueError(f'end_date {end} is before start_date {start}.')
    return start, end


def load_snapshots(start_date, end_date, feature_columns):
    """Read eligible ``ETFFeatureSnapshot`` rows in a window into a tidy frame.

    One ``values()`` query so a row's features and its own label travel together; the
    look-ahead columns are selected by name and never through ``feature_columns``.
    ``label_horizon_days`` is carried because :func:`build_training_frame` will not mix
    horizons in one fit.
    """
    fields = ['etf__ts_code', 'date', *feature_columns, *LABEL_COLUMNS]
    rows = list(
        ETFFeatureSnapshot.objects
        .filter(is_eligible=True, date__gte=start_date, date__lte=end_date)
        .values(*fields)
        .order_by('date', 'etf__ts_code')
    )
    frame = pd.DataFrame.from_records(rows)
    if not frame.empty:
        frame = frame.rename(columns={'etf__ts_code': 'ts_code'})
    return frame


def relevance_grade(rank_pct):
    """``label_rank_pct`` -> integer relevance grades 0..4 for lambdarank.

    Mapping: ``< 0.2 -> 0``, ``[0.2, 0.4) -> 1``, ``[0.4, 0.6) -> 2``, ``[0.6, 0.8) -> 3``,
    ``>= 0.8 -> 4``, consumed with ``label_gain=[0, 1, 3, 7, 15]``. Percentile rather than
    raw ``fwd_return`` because a ranker optimises order inside one day's pool, and the
    percentile is already pool-normalised by ``features.py``; quantising keeps a single
    extreme return in a 40-ETF pool from dominating that query's DCG.

    ``np.digitize`` and not ``pd.cut``: ``pd.cut`` bins are *right*-closed, so a rank of
    exactly 0.2 (which ``rank(pct=True)`` produces whenever the pool size is a multiple of
    five, i.e. constantly) would land in grade 0 while every downstream description of this
    function says 1. The synthetic audit caught 2400 such rows out of 12000.
    """
    values = pd.to_numeric(rank_pct, errors='coerce').to_numpy(dtype=float)
    if np.isnan(values).any():
        raise ValueError('relevance_grade received a null label_rank_pct; filter before grading.')
    return pd.Series(np.digitize(values, RELEVANCE_BINS, right=False),
                     index=getattr(rank_pct, 'index', None), dtype=int)


def build_training_frame(snapshots_df, *, feature_columns, horizon_days: int, objective: str) -> tuple:
    """Raw snapshot rows -> clean, date-sorted, single-horizon frame.

    Pure: no ORM, no files, no LightGBM, so it can be checked against a synthetic panel.
    Returns ``(frame, counts)`` where ``frame`` carries ``_label`` (grade / 0-1 / percentile,
    per objective) and ``counts`` is the row-deduction audit trail that goes into metadata.

    Rows are removed, never imputed, when the *outcome* is missing: a null ``fwd_return``
    means the label window has not closed yet. ``label_horizon_days`` that is not
    ``horizon_days`` means the row answers a different question than the model is being
    asked -- mixed horizons are the quiet failure here, since both labels are percentiles
    and nothing downstream would complain while the model learned a blended 5d/20d target.
    """
    if objective not in OBJECTIVES:
        raise ValueError(f'objective must be one of {OBJECTIVES}, got {objective!r}.')
    columns = list(feature_columns)
    missing = [name for name in columns if name not in snapshots_df.columns]
    if missing:
        raise RuntimeError(f'Snapshot frame lacks whitelisted feature column(s): {missing}.')
    for name in ('ts_code', 'date', *LABEL_COLUMNS):
        if name not in snapshots_df.columns:
            raise RuntimeError(f'Snapshot frame lacks required column {name!r}.')

    counts = {'input_rows': int(len(snapshots_df))}
    frame = snapshots_df.copy()
    frame['date'] = pd.to_datetime(frame['date'])
    for name in [*columns, 'fwd_return', 'label_rank_pct', 'label_horizon_days']:
        frame[name] = pd.to_numeric(frame[name], errors='coerce')

    duplicates = int(frame.duplicated(subset=['ts_code', 'date']).sum())
    if duplicates:
        # unique_together ('etf','date') should make this unreachable. A surviving duplicate
        # would land in the same query group twice and be counted in the group length twice.
        logger.warning('Dropping %s duplicated (ts_code, date) snapshot rows.', duplicates)
        frame = frame.drop_duplicates(subset=['ts_code', 'date'], keep='first')
    counts['duplicate_keys'] = duplicates

    horizon_match = frame['label_horizon_days'] == float(horizon_days)
    counts['wrong_horizon'] = int((~horizon_match).sum())
    frame = frame[horizon_match]

    counts['missing_fwd_return'] = int(frame['fwd_return'].isna().sum())
    frame = frame[frame['fwd_return'].notna()]

    label_source = 'label_top_bucket' if objective == 'binary' else 'label_rank_pct'
    counts['missing_label'] = int(frame[label_source].isna().sum())
    # copy() before adding _label: the masks above leave _is_copy set on the frame, and
    # writing a column through a mask result is exactly the chained-assignment pattern
    # pandas warns about (and is tightening toward an error).
    frame = frame[frame[label_source].notna()].copy()

    if objective == 'binary':
        frame['_label'] = frame[label_source].astype(bool).astype(int)
    elif objective == 'regression':
        frame['_label'] = frame[label_source].astype(float)
    else:
        frame['_label'] = relevance_grade(frame[label_source])

    counts['thin_dates_dropped'] = 0
    if not frame.empty:
        # Sortedness is a precondition of the group array, not a nicety: lgb.Dataset assigns
        # query groups as consecutive runs, so an unsorted frame relabels every row's group.
        frame = frame.sort_values(['date', 'ts_code'], kind='mergesort').reset_index(drop=True)
        if objective == 'lambdarank':
            sizes = frame.groupby('date', sort=False).size()
            keep_dates = set(sizes[sizes >= MIN_LAMBDARANK_GROUP_SIZE].index)
            counts['thin_dates_dropped'] = int((sizes < MIN_LAMBDARANK_GROUP_SIZE).sum())
            frame = frame[frame['date'].isin(keep_dates)].reset_index(drop=True)
    counts['output_rows'] = int(len(frame))
    return frame.assign(date=frame['date'].dt.date), counts


def build_rank_matrix(frame, feature_columns, *, objective: str) -> tuple:
    """``(matrix, labels, group, group_dates)`` ready for ``lgb.Dataset``.

    ``group`` is LightGBM's per-query document count **in row order**: one query per trading
    date. This is the step that either works or is subtly wrong. Loud failure is a sum
    mismatch, which the C++ layer raises on. Silent failure is a group array whose counts
    sum to the right total but were derived from a different row order -- every row then
    belongs to the wrong date, and "ranking" means comparing this month's bank ETF with next
    quarter's semiconductor ETF. Hence the four explicit invariants below rather than a bare
    ``value_counts()``, and the re-check of the expanded date sequence last, which is what
    actually proves the runs line up.
    """
    if objective not in OBJECTIVES:
        raise ValueError(f'objective must be one of {OBJECTIVES}, got {objective!r}.')
    if frame.empty:
        raise ValueError('Cannot build a LightGBM matrix from an empty frame.')
    dates = frame['date'].tolist()
    if any(dates[index] > dates[index + 1] for index in range(len(dates) - 1)):
        raise WalkForwardError('Frame is not sorted by date; query-group runs would be wrong.')

    matrix = frame[list(feature_columns)].reset_index(drop=True)
    labels = frame['_label'].to_numpy()

    date_series = pd.Series(dates)
    sizes = date_series.groupby(by=date_series, sort=False).size()
    group = sizes.to_numpy(dtype=int)
    group_dates = list(sizes.index)

    if int(group.sum()) != len(frame):
        raise WalkForwardError(
            f'Group array sums to {int(group.sum())} but the frame has {len(frame)} rows.'
        )
    if len(group_dates) != len(group):
        raise WalkForwardError('Group date count and group length count differ.')
    if group.min() < 1:
        raise WalkForwardError('A zero-length query group reached the dataset builder.')
    expanded = [day for day, size in zip(group_dates, group) for _ in range(int(size))]
    if expanded != dates:
        raise WalkForwardError('Group boundaries do not reproduce the frame date order.')
    return matrix, labels, group, group_dates


# -----------------------------------------------------------------------------
# Holdout metrics
# -----------------------------------------------------------------------------
def _spearman(predicted, realised):
    """Spearman rank correlation via ``pandas.DataFrame.corr(method='spearman')``.

    Constant input is answered with ``None`` instead of letting pandas divide by zero and
    hand back NaN, which would leak a NaN into the ICIR standard deviation.
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
    return None if value is None or not np.isfinite(float(value)) else float(value)


def _ndcg_at_k(scores, grades, k: int = 5):
    """NDCG@k over integer relevance grades using the same ``LABEL_GAIN`` curve as the objective."""
    grades = np.asarray(grades, dtype=int)
    order = np.argsort(-np.asarray(scores, dtype=float), kind='mergesort')
    gains = np.asarray(LABEL_GAIN, dtype=float)[grades[order]][:k]
    positions = np.arange(1, len(gains) + 1, dtype=float)
    dcg = float(np.sum(gains / np.log2(positions + 1.0)))
    ideal = np.sort(np.asarray(LABEL_GAIN, dtype=float)[grades])[::-1][:k]
    idcg = float(np.sum(ideal / np.log2(np.arange(1, len(ideal) + 1, dtype=float) + 1.0)))
    return None if idcg <= 0 else dcg / idcg


def _summarise(values):
    if not values:
        return None
    return {
        'mean': float(np.mean(values)),
        'std': float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
        'count': len(values),
    }


def evaluate_holdout(scored_df, *, objective: str, top_n: int = 5) -> dict:
    """Holdout metrics over ``(date, ts_code, score, fwd_return, _label)`` rows.

    All computed **per validation date** and then summarised. Pooling rows across dates would
    rank one day's ETFs against another day's pool, which is not the decision the rotation
    engine makes and reliably produces a flattering IC.

    * ``rank_ic`` -- mean of the daily Spearman(score, realised ``fwd_return``).
    * ``icir`` -- mean/std of those daily ICs (``ddof=1``); needs >= 2 days, and it is the
      figure that survives a change in pool composition better than raw IC.
    * ``top_n_hit_rate`` -- share of the ``top_n`` highest-scored names that landed in that
      date's realised top quartile of ``fwd_return`` (quartile by count, ``ceil(n/4)``).
      This is the metric ``max_positions=5`` actually experiences. Dates with fewer than
      ``top_n`` candidates are skipped: with n=3 the "top 5" is the whole pool and the hit
      rate is 1.0 by construction, which would look like skill and is arithmetic.
    * ``ndcg_at_5`` -- lambdarank only: DCG@5 of the predicted order over IDCG@5 of the
      realised grade order.
    """
    top_n = max(1, int(top_n))
    daily_ics, hit_rates, ndcgs = [], [], []
    skipped_ic = skipped_small = 0
    for _day, day_frame in scored_df.groupby('date', sort=True):
        day_frame = day_frame.sort_values('score', kind='mergesort')
        ic = _spearman(day_frame['score'].to_numpy(), day_frame['fwd_return'].to_numpy())
        if ic is None:
            skipped_ic += 1
        else:
            daily_ics.append(ic)
        sample_count = len(day_frame)
        if sample_count >= top_n:
            quartile_size = max(1, int(np.ceil(sample_count / 4.0)))
            predicted_top = set(day_frame.nlargest(top_n, 'score', keep='first')['ts_code'])
            realised_top = set(day_frame.nlargest(quartile_size, 'fwd_return', keep='first')['ts_code'])
            hit_rates.append(len(predicted_top & realised_top) / float(top_n))
        else:
            skipped_small += 1
        if objective == 'lambdarank' and sample_count >= 2:
            ndcg = _ndcg_at_k(day_frame['score'].to_numpy(), day_frame['_label'].to_numpy(), k=top_n)
            if ndcg is not None:
                ndcgs.append(ndcg)

    ic_summary = _summarise(daily_ics)
    hit_summary = _summarise(hit_rates)
    ndcg_summary = _summarise(ndcgs) if objective == 'lambdarank' else None
    return {
        'rank_ic': ic_summary['mean'] if ic_summary else None,
        'ic_std': ic_summary['std'] if ic_summary else None,
        'ic_dates': ic_summary['count'] if ic_summary else 0,
        'icir': (
            ic_summary['mean'] / ic_summary['std']
            if ic_summary and ic_summary['count'] > 1 and ic_summary['std'] > 0 else None
        ),
        'top_n': top_n,
        'top_n_hit_rate': hit_summary['mean'] if hit_summary else None,
        'top_n_hit_dates': hit_summary['count'] if hit_summary else 0,
        'ndcg_at_5': ndcg_summary['mean'] if ndcg_summary else None,
        'ndcg_dates': ndcg_summary['count'] if ndcg_summary else 0,
        'skipped_dates_ic_undefined': skipped_ic,
        'skipped_dates_pool_below_top_n': skipped_small,
        'valid_rows': int(len(scored_df)),
        'valid_dates': int(scored_df['date'].nunique()) if len(scored_df) else 0,
    }


def _json_safe(value):
    """Recursively make a structure JSON-column safe (non-finite floats -> ``None``)."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _mean_of(values):
    clean = [float(value) for value in values if value is not None and np.isfinite(float(value))]
    return float(np.mean(clean)) if clean else None


# -----------------------------------------------------------------------------
# Artifacts
# -----------------------------------------------------------------------------
def _artifact_dir(version: str) -> str:
    return os.path.join(str(settings.BASE_DIR), MODELS_RELATIVE_PREFIX, str(version))


def artifact_relative_path(version: str) -> str:
    """``BASE_DIR``-relative path, which doubles as the artifact-store object key."""
    return os.path.join(MODELS_RELATIVE_PREFIX, str(version))


def _save_artifacts(booster, artifact_metadata: dict, version: str, fold_reports: list) -> str:
    """Pickle the booster (repo convention) and mirror the directory into the artifact store."""
    path = _artifact_dir(version)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, 'model.pkl'), 'wb') as handle:
        pickle.dump(booster, handle, protocol=pickle.HIGHEST_PROTOCOL)
    with open(os.path.join(path, 'metadata.json'), 'w', encoding='utf-8') as handle:
        json.dump(_json_safe(artifact_metadata), handle, indent=2, sort_keys=True)
    with open(os.path.join(path, 'folds.json'), 'w', encoding='utf-8') as handle:
        json.dump(_json_safe(fold_reports), handle, indent=2, sort_keys=True)
    get_artifact_store().upload_dir(path, to_store_key(path))
    return path


def _load_artifacts(relative_path: str):
    """Resolve a registry ``artifact_path`` through the artifact store, then read it."""
    if not relative_path:
        return None
    local_dir = os.path.join(str(settings.BASE_DIR), str(relative_path).replace('/', os.sep))
    store = get_artifact_store()
    # Cold local cache against a remote store: pull the family down before reading it.
    if not os.path.isdir(local_dir) and store.is_remote:
        store.ensure_dir_local(to_store_key(local_dir), local_dir)
    if not os.path.isdir(local_dir):
        logger.warning('ETF LightGBM artifact directory %s does not resolve in the store.', relative_path)
        return None
    try:
        with open(os.path.join(local_dir, 'model.pkl'), 'rb') as handle:
            booster = pickle.load(handle)
        with open(os.path.join(local_dir, 'metadata.json'), 'r', encoding='utf-8') as handle:
            metadata = json.load(handle)
    except (OSError, pickle.UnpicklingError, json.JSONDecodeError, EOFError) as exc:
        logger.error('Failed to load ETF LightGBM artifacts from %s: %s', relative_path, exc)
        return None
    return {'model': booster, 'metadata': metadata}


# -----------------------------------------------------------------------------
# Registry
# -----------------------------------------------------------------------------
def _normalize_version_tag(value) -> str:
    return re.sub(r'[^A-Za-z0-9._-]+', '-', str(value or '').strip()).strip('-._')


#: ``ModelVersion.version`` is ``CharField(max_length=50)``. Postgres rejects an overlong
#: value and SQLite truncates it silently, so a long ``--version-tag`` would otherwise fail
#: (or quietly rename) a run *after* every fold has already been paid for.
MAX_MODEL_VERSION_LENGTH = 50


def _append_tag(base: str, tag: str) -> str:
    """``base[-tag]`` clamped to :data:`MAX_MODEL_VERSION_LENGTH`."""
    if not tag:
        return base[:MAX_MODEL_VERSION_LENGTH]
    room = MAX_MODEL_VERSION_LENGTH - len(base) - 1
    if room <= 0:
        return base[:MAX_MODEL_VERSION_LENGTH]
    return f'{base}-{tag[:room]}'


def build_model_version(horizon_days: int, objective: str, training_end, version_tag: str = '') -> str:
    """``etf-lgb-<horizon>d-<objective>-<training-end>[-<tag>]``.

    The objective is part of the key because the plan lets the backtest decide which label
    form wins: two runs differing only in objective must not overwrite one another.
    """
    end = training_end.date() if hasattr(training_end, 'date') else training_end
    base = f'etf-lgb-{int(horizon_days)}d-{objective}-{end.isoformat()}'
    return _append_tag(base, _normalize_version_tag(version_tag))


def _model_type(label: str):
    choices = ModelVersion.ModelType
    if not hasattr(choices, label):
        raise ValueError(f'{label!r} is not a ModelVersion.ModelType choice.')
    return getattr(choices, label)


def _register_model_version(*, model_type_label: str, version: str, training_start, training_end,
                            metrics: dict, feature_columns, metadata: dict, artifact_path: str):
    """Insert/refresh the registry row and flip prior actives, per ``_register_lightgbm_model_version``.

    The flip is scoped to the same ``model_type`` and excludes this version, so re-running
    the same version tag refreshes it instead of deactivating itself.
    """
    model_type = _model_type(model_type_label)
    ModelVersion.objects.filter(model_type=model_type, is_active=True).exclude(
        version=version,
    ).update(is_active=False)
    row, _created = ModelVersion.objects.update_or_create(
        model_type=model_type,
        version=version,
        defaults={
            'status': ModelVersion.Status.READY,
            'artifact_path': artifact_path,
            'metrics': _json_safe(metrics),
            'feature_schema': list(feature_columns),
            'training_window_start': training_start,
            'training_window_end': training_end,
            'trained_at': timezone.now(),
            'is_active': True,
            'metadata': _json_safe(metadata),
        },
    )
    return row


def registered_versions(model_type_labels=('ETF_LIGHTGBM', 'ETF_LSTM', 'ETF_ENSEMBLE')) -> list:
    """Read-only registry view for ``--report``. No artifact I/O, no writes."""
    rows = []
    for label in model_type_labels:
        model_type = _model_type(label)
        for row in ModelVersion.objects.filter(model_type=model_type).order_by('-is_active', '-trained_at'):
            metadata = dict(row.metadata or {})
            metrics = dict(row.metrics or {})
            rows.append({
                'model_type': row.model_type,
                'version': row.version,
                'status': row.status,
                'is_active': bool(row.is_active),
                'trained_at': row.trained_at.isoformat() if row.trained_at else None,
                'training_window': [
                    row.training_window_start.isoformat() if row.training_window_start else None,
                    row.training_window_end.isoformat() if row.training_window_end else None,
                ],
                'artifact_path': row.artifact_path,
                'objective': metadata.get('objective'),
                'horizon_days': metadata.get('horizon_days'),
                'holdout': {
                    key: metrics.get(key)
                    for key in ('rank_ic', 'icir', 'top_n_hit_rate', 'ndcg_at_5', 'ic_dates')
                    if key in metrics
                },
                'fold_count': len(metadata.get('folds') or []),
                'total_purged_train_dates': metadata.get('total_purged_train_dates'),
                'weights': metadata.get('weights'),
                # Derived when absent so a consumer can hand this straight to
                # rotation_engine._rescale_ensemble_weights without knowing the two keyspaces.
                'engine_weights': (
                    metadata.get('engine_weights')
                    or (engine_weights(metadata['weights']) if metadata.get('weights') else None)
                ),
            })
    return rows


# -----------------------------------------------------------------------------
# Walk-forward driver
# -----------------------------------------------------------------------------
def _prepare(start_date, end_date, n_folds, horizon_days, objective, min_train_days):
    """Shared read + plan path for :func:`plan_walk_forward` and :func:`train_walk_forward`.

    Returns ``(base, frame, counts, folds, panel_dates)``. A ``base['status']`` other than
    ``ok`` short-circuits the caller with a reason instead of an exception, because an empty
    table is an expected state on a fresh install.
    """
    feature_columns = get_feature_columns()
    start, end = resolve_window(start_date, end_date)
    if start is None:
        return ({
            'status': 'no_data',
            'reason': 'ETFFeatureSnapshot holds no is_eligible rows in any window; build the '
                      'feature snapshots before training.',
            'feature_columns': feature_columns,
            'row_counts': {},
        }, None, {}, [], [])

    snapshots_df = load_snapshots(start, end, feature_columns)
    if snapshots_df.empty:
        return ({
            'status': 'no_data',
            'reason': f'No eligible snapshots between {start} and {end}.',
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'feature_columns': feature_columns,
            'row_counts': {},
        }, None, {}, [], [])

    horizon_days = resolve_horizon_days(horizon_days)
    frame, counts = build_training_frame(
        snapshots_df, feature_columns=feature_columns, horizon_days=horizon_days, objective=objective,
    )
    panel_dates = sorted(frame['date'].unique().tolist())
    if not panel_dates:
        return ({
            'status': 'insufficient_data',
            'reason': 'Every snapshot row in the window was dropped for a missing or mismatched label.',
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'feature_columns': feature_columns,
            'row_counts': counts,
        }, frame, counts, [], [])

    folds = expanding_folds(
        panel_dates[0], panel_dates[-1], int(n_folds),
        horizon_days=horizon_days, min_train_days=int(min_train_days), trade_dates=panel_dates,
    )
    base = {
        'status': 'ok',
        'start_date': start.isoformat(),
        'end_date': end.isoformat(),
        'panel_start': panel_dates[0].isoformat(),
        'panel_end': panel_dates[-1].isoformat(),
        'panel_dates': len(panel_dates),
        'horizon_days': int(horizon_days),
        'min_train_days': int(min_train_days),
        'objective': objective,
        'n_folds_requested': int(n_folds),
        'n_folds_built': len(folds),
        'feature_columns': feature_columns,
        'feature_count': len(feature_columns),
        'feature_schema_version': feature_schema_version(feature_columns),
        'row_counts': counts,
        'pool': _pool_stats(frame),
    }
    if not folds:
        return ({
            **base,
            'status': 'insufficient_data',
            'reason': (
                f'Panel spans {len(panel_dates)} trading dates; a single fold needs at least '
                f'min_train_days({min_train_days}) + 2 + horizon_days({horizon_days}). '
                f'Lower --folds / min_train_days, or backfill more history.'
            ),
        }, frame, counts, [], panel_dates)
    return base, frame, counts, folds, panel_dates


def _pool_stats(frame) -> dict:
    if frame is None or frame.empty:
        return {'unique_etfs': 0, 'unique_dates': 0, 'pool_mean_per_date': 0.0}
    sizes = frame.groupby('date').size()
    return {
        'unique_etfs': int(frame['ts_code'].nunique()),
        'unique_dates': int(frame['date'].nunique()),
        'pool_mean_per_date': float(sizes.mean()),
        'pool_min_per_date': int(sizes.min()),
        'pool_max_per_date': int(sizes.max()),
    }


def plan_walk_forward(*, start_date=None, end_date=None, n_folds: int = 6, horizon_days=None,
                      objective: str = DEFAULT_OBJECTIVE, min_train_days: int = None,
                      version_tag: str = '') -> dict:
    """Build the frames and the fold plan, count samples and groups, train nothing, write nothing.

    This is ``--dry-run``: every query is a SELECT, no directory is created and no
    ``ModelVersion`` row is touched. Its point is that the group counts and the purge counts
    are visible *before* a retrain is paid for -- they are the two numbers that say whether
    the panel is worth fitting at all. ``version_tag`` only shapes the previewed version key.
    """
    min_train_days = (
        _setting_int('ETF_MIN_TRAIN_DAYS', 250) if min_train_days is None else int(min_train_days)
    )
    base, frame, _counts, folds, panel_dates = _prepare(
        start_date, end_date, n_folds, horizon_days, objective, min_train_days,
    )
    if base['status'] != 'ok':
        return {**base, 'lightgbm_available': lgb is not None}

    horizon_days = base['horizon_days']
    trail = {item['fold_index']: item for item in audit_folds(folds, panel_dates, horizon_days)}
    fold_plans = []
    for fold in folds:
        kept, dropped = train_dates_for_fold(fold, panel_dates, horizon_days)
        train_frame = frame[frame['date'].isin(kept)]
        valid_frame = frame[(frame['date'] >= fold.valid_start) & (frame['date'] <= fold.valid_end)]
        sizes = [int(size) for size in train_frame.groupby('date', sort=False).size()] if not train_frame.empty else []
        fold_plans.append({
            **fold.as_dict(),
            'train_dates_kept': len(kept),
            'train_dates_dropped': len(dropped),
            'train_rows': int(len(train_frame)),
            'train_groups': len(sizes),
            'train_group_min': min(sizes) if sizes else 0,
            'train_group_max': max(sizes) if sizes else 0,
            'valid_rows': int(len(valid_frame)),
            'valid_dates': int(valid_frame['date'].nunique()),
            'purge_audit': trail.get(fold.fold_index, {}),
        })
    return {
        **base,
        'lightgbm_available': lgb is not None,
        'folds': fold_plans,
        'would_write': {
            'model_type': 'ETF_LIGHTGBM',
            'artifact_dir': os.path.join(MODELS_RELATIVE_PREFIX, '<version>'),
            'version': build_model_version(horizon_days, objective, max(panel_dates), version_tag),
        },
    }


def train_walk_forward(*, model_type_label: str = 'ETF_LIGHTGBM', start_date=None, end_date=None,
                       n_folds: int = 6, horizon_days=None, objective: str = DEFAULT_OBJECTIVE,
                       num_boost_round: int = None, params: dict = None,
                       min_train_days: int = None, version_tag: str = '') -> dict:
    """Expanding-window walk-forward fit with per-fold holdout metrics, then one registry row.

    Each fold trains on its purged window and is scored on the block that follows it. The
    registered ``ModelVersion`` carries the per-fold metrics, purge counts, pool sizes and
    feature schema version in ``metadata``, and the pooled holdout numbers in ``metrics``.

    The shipped artifact is a *refit* over every labelled date rather than one of the fold
    boosters: the folds exist to estimate skill, and the model that scores tomorrow should
    use all the history it has. The registered metrics therefore describe a same-specification
    model, not the exact pickle on disk -- stated here because a metric field that silently
    refers to a different model is how a pipeline stops being believed.
    """
    if lgb is None:
        raise RuntimeError('lightgbm is not installed; ETF walk-forward training cannot run.')
    if model_type_label not in ('ETF_LIGHTGBM', 'ETF_LSTM', 'ETF_ENSEMBLE'):
        raise ValueError(f'model_type_label must be an ETF ModelVersion choice, got {model_type_label!r}.')

    min_train_days = (
        _setting_int('ETF_MIN_TRAIN_DAYS', 250) if min_train_days is None else int(min_train_days)
    )
    num_boost_round = (
        _setting_int('ETF_LGB_NUM_BOOST_ROUND', 200) if num_boost_round is None else int(num_boost_round)
    )
    if num_boost_round < 1:
        raise ValueError(f'num_boost_round must be >= 1, got {num_boost_round}.')

    base, frame, counts, folds, panel_dates = _prepare(
        start_date, end_date, n_folds, horizon_days, objective, min_train_days,
    )
    if base['status'] != 'ok':
        return {**base, 'model_type_label': model_type_label}

    horizon_days = base['horizon_days']
    feature_columns = base['feature_columns']
    params = resolve_params(objective, params)
    top_n = _setting_int('ETF_ROTATION_MAX_POSITIONS', 5)
    min_train_rows = _setting_int('ETF_MIN_TRAIN_ROWS', 200)
    trail = {item['fold_index']: item for item in audit_folds(folds, panel_dates, horizon_days)}

    fold_reports, scored_blocks = [], []
    for fold in folds:
        kept, dropped = train_dates_for_fold(fold, panel_dates, horizon_days)
        if len(dropped) != fold.purged_train_dates:
            raise WalkForwardError(
                f'Fold {fold.fold_index}: the purge removed {len(dropped)} dates but the plan '
                f'declared {fold.purged_train_dates}.'
            )
        train_frame = frame[frame['date'].isin(kept)]
        valid_frame = frame[(frame['date'] >= fold.valid_start) & (frame['date'] <= fold.valid_end)]
        report = {
            **fold.as_dict(),
            'train_dates_kept': len(kept),
            'train_dates_dropped': len(dropped),
            'train_rows': int(len(train_frame)),
            'valid_rows': int(len(valid_frame)),
            'purge_audit': trail.get(fold.fold_index, {}),
            'metrics': {},
        }
        if len(train_frame) < min_train_rows or valid_frame.empty:
            # Skipped, not silently zero-filled: a fold trained on 30 rows and scored on
            # nothing would average a None into the aggregate and look like a weak model.
            fold_reports.append({**report, 'status': 'skipped', 'train_groups': 0})
            continue
        matrix, labels, group, group_dates = build_rank_matrix(train_frame, feature_columns, objective=objective)
        booster = lgb.train(
            params,
            lgb.Dataset(matrix, label=labels, group=group, free_raw_data=False),
            num_boost_round=num_boost_round,
        )
        scored = valid_frame[['date', 'ts_code', 'fwd_return']].copy()
        scored['_label'] = valid_frame['_label'].to_numpy()
        # Validation columns re-ordered by name, not position: LightGBM matches by name, and
        # relying on the frame construction order is how a silent feature shift happens.
        scored['score'] = np.asarray(
            booster.predict(valid_frame.reindex(columns=feature_columns)), dtype=float,
        )
        scored_blocks.append(scored)
        metrics = evaluate_holdout(scored, objective=objective, top_n=top_n)
        fold_reports.append({
            **report,
            'status': 'trained',
            'valid_rows': int(len(scored)),
            'train_groups': len(group_dates),
            'train_group_min': int(group.min()),
            'train_group_max': int(group.max()),
            'metrics': metrics,
            'feature_importance_gain': {
                name: float(value)
                for name, value in zip(booster.feature_name(), booster.feature_importance('gain'))
            },
        })

    trained = [report for report in fold_reports if report.get('status') == 'trained']
    if not trained:
        return {
            **base,
            'status': 'insufficient_data',
            'reason': (
                f'No fold had both >= {min_train_rows} purged training rows and a non-empty '
                f'validation block ({len(fold_reports)} folds planned).'
            ),
            'folds': fold_reports,
            'model_type_label': model_type_label,
        }

    pooled = evaluate_holdout(pd.concat(scored_blocks, ignore_index=True), objective=objective, top_n=top_n)
    aggregate = {
        'mean_rank_ic': _mean_of([report['metrics'].get('rank_ic') for report in trained]),
        'mean_icir': _mean_of([report['metrics'].get('icir') for report in trained]),
        'mean_top_n_hit_rate': _mean_of([report['metrics'].get('top_n_hit_rate') for report in trained]),
        'mean_ndcg_at_5': _mean_of([report['metrics'].get('ndcg_at_5') for report in trained]),
        'folds_trained': len(trained),
        'folds_skipped': len(fold_reports) - len(trained),
        'folds_with_positive_ic': sum(1 for report in trained if (report['metrics'].get('rank_ic') or 0.0) > 0),
        'total_purged_train_dates': int(sum(report['purged_train_dates'] for report in fold_reports)),
    }
    version = build_model_version(horizon_days, objective, max(panel_dates), version_tag)
    headline = {
        'rank_ic': pooled['rank_ic'],
        'icir': pooled['icir'],
        'top_n_hit_rate': pooled['top_n_hit_rate'],
        'ndcg_at_5': pooled['ndcg_at_5'],
        'ic_dates': pooled['ic_dates'],
        'valid_rows': pooled['valid_rows'],
        'num_boost_round': int(num_boost_round),
    }
    metadata = {
        **base,
        'framework': 'expanding_window_walk_forward',
        'model_type_label': model_type_label,
        'version': version,
        'num_boost_round': int(num_boost_round),
        'lgb_params': params,
        'relevance_grade_mapping': {
            'source': 'label_rank_pct',
            'bins': list(RELEVANCE_BINS),
            'grades': list(range(len(RELEVANCE_BINS) + 1)),
            'label_gain': list(LABEL_GAIN),
        },
        'top_n': top_n,
        'purge_rule': (
            'training date D dropped when its label window D..D+horizon overlaps the '
            'validation block, under both the trading-session and calendar-day conventions '
            '(the union of their hits is dropped)'
        ),
        'total_purged_train_dates': aggregate['total_purged_train_dates'],
        'folds': fold_reports,
        'aggregate': aggregate,
        'pooled_holdout': pooled,
        'training_window': [min(panel_dates).isoformat(), max(panel_dates).isoformat()],
        'validation_windows': [[report['valid_start'], report['valid_end']] for report in fold_reports],
        'final_booster_rows': int(len(frame)),
        'holdout_integrity': (
            'No valid_sets and no early stopping: num_boost_round is fixed up front so the '
            'validation blocks never choose model capacity.'
        ),
        'deployment_note': (
            'Metrics come from the fold boosters; the artifact is a refit over every labelled '
            'date, so it is the same specification with more data, not one of the evaluated models.'
        ),
    }

    final_booster = _fit_final_booster(frame, feature_columns, params, objective, num_boost_round)
    local_dir = _save_artifacts(
        final_booster,
        {
            'version': version,
            'model_type': model_type_label,
            'objective': objective,
            'horizon_days': int(horizon_days),
            'feature_names': list(feature_columns),
            'feature_schema_version': base['feature_schema_version'],
            'lgb_params': params,
            'num_boost_round': int(num_boost_round),
            'trained_at': timezone.now().isoformat(),
            'training_window': metadata['training_window'],
            'load_contract': 'callable(snapshots_df) -> DataFrame[ts_code, score] via '
                             'apps.etf_rotation.train_lightgbm.load_active_predictor',
        },
        version,
        fold_reports,
    )
    row = _register_model_version(
        model_type_label=model_type_label,
        version=version,
        training_start=min(panel_dates),
        training_end=max(panel_dates),
        metrics=headline,
        feature_columns=feature_columns,
        metadata=metadata,
        artifact_path=artifact_relative_path(version),
    )
    return {
        'status': 'success',
        'model_type_label': model_type_label,
        'model_version': row.version,
        'model_version_id': row.id,
        'artifact_path': row.artifact_path,
        'artifact_dir': local_dir,
        'objective': objective,
        'horizon_days': horizon_days,
        'start_date': base['start_date'],
        'end_date': base['end_date'],
        'metrics': headline,
        'aggregate': aggregate,
        'pooled_holdout': pooled,
        'folds': fold_reports,
        'n_folds': len(fold_reports),
        'pool': base['pool'],
        'row_counts': counts,
        'feature_schema_version': base['feature_schema_version'],
    }


def _fit_final_booster(frame, feature_columns, params, objective, num_boost_round):
    """Refit on every labelled date. See :func:`train_walk_forward` for why this is not a fold model."""
    matrix, labels, group, _group_dates = build_rank_matrix(frame, feature_columns, objective=objective)
    return lgb.train(
        params,
        lgb.Dataset(matrix, label=labels, group=group, free_raw_data=False),
        num_boost_round=num_boost_round,
    )


# -----------------------------------------------------------------------------
# Inference seam used (lazily) by rotation_engine / backtest_engine
# -----------------------------------------------------------------------------
def load_active_predictor():
    """``callable(snapshots_df) -> DataFrame[ts_code, score]`` for the active ETF ranker, else ``None``.

    Resolution mirrors ``tasks_lightgbm.py:2155`` -- the newest
    ``ModelVersion(model_type=ETF_LIGHTGBM, is_active=True, status=READY)`` -- but the
    authority is the shared ``ModelVersion`` registry, not ``LightGBMModelArtifact``: this
    module owns no table and adds no migration.

    Contract callers depend on:

    * ``None`` when nothing is registered and ready. The rotation engine must then fall back
      to rule-based ranking rather than crash on a cold environment.
    * The returned frame is row-aligned with the input (one row per input snapshot) with
      exactly the columns ``ts_code`` and ``score``. Ranking is only meaningful inside one
      day's pool, so a caller scoring several dates keeps its own ``date`` column and groups
      before selecting.
    * Higher score = better momentum for every objective. ``binary`` emits P(top bucket) and
      ``regression`` the predicted percentile, so a backtest can swap the label form without
      changing the caller.
    * Features are taken by name in the stored training order. A column missing from the
      caller's frame becomes NaN, which LightGBM routes down its native missing-value branch
      instead of zero-filling it to the wrong side of a split.
    """
    model_type = _model_type('ETF_LIGHTGBM')
    version = ModelVersion.objects.filter(
        model_type=model_type,
        is_active=True,
        status=ModelVersion.Status.READY,
    ).order_by('-trained_at').first()
    if version is None:
        return None
    artifacts = _load_artifacts(version.artifact_path)
    if artifacts is None:
        logger.warning(
            'Active ETF ModelVersion %s has no readable artifact at %s.', version.version, version.artifact_path
        )
        return None
    feature_columns = list(artifacts['metadata'].get('feature_names') or version.feature_schema or [])
    if not feature_columns:
        logger.error(
            'Active ETF ModelVersion %s carries no feature schema; refusing to infer one from _meta.fields.',
            version.version,
        )
        return None
    booster = artifacts['model']

    def score(snapshots_df) -> pd.DataFrame:
        if snapshots_df is None or len(snapshots_df) == 0:
            return pd.DataFrame({'ts_code': [], 'score': []})
        absent = [name for name in feature_columns if name not in snapshots_df.columns]
        if len(absent) == len(feature_columns):
            raise ValueError(
                f'Scoring frame has none of the {len(feature_columns)} features model '
                f'{version.version} was trained on (first few: {feature_columns[:5]}).'
            )
        if absent:
            logger.warning(
                '%d of %d training features are absent from the scoring frame (%s); they are '
                'scored as NaN, which shifts the ranking distribution.',
                len(absent), len(feature_columns), ', '.join(absent[:5]),
            )
        matrix = snapshots_df.reindex(columns=feature_columns).apply(pd.to_numeric, errors='coerce')
        if 'ts_code' in snapshots_df.columns:
            codes = snapshots_df['ts_code']
        elif 'etf__ts_code' in snapshots_df.columns:
            codes = snapshots_df['etf__ts_code']
        else:
            codes = pd.Series([''] * len(snapshots_df), index=snapshots_df.index)
        return pd.DataFrame({'ts_code': list(codes), 'score': np.asarray(booster.predict(matrix), dtype=float)})

    return score


# -----------------------------------------------------------------------------
# Ensemble registration
# -----------------------------------------------------------------------------
def engine_weights(weights: dict) -> dict:
    """Same weights keyed by the rotation engine's scorer names (``LIGHTGBM``/``LSTM``).

    ``rotation_engine._rescale_ensemble_weights`` matches ``base_weights`` keys against the
    sources that actually answered, so a registry-keyed dict ({``ETF_LIGHTGBM``: ...}) selects
    nothing and the call raises "ensemble weights sum to zero". ``RULE`` is deliberately
    absent: it is an untrained heuristic with no rank-IC row to weight, and pinning it to 0.0
    here would silently delete the defensive sleeve of the engine's own base weights.
    """
    return {
        ENGINE_SOURCE_NAMES[label]: float(value)
        for label, value in weights.items()
        if label in ENGINE_SOURCE_NAMES
    }


def register_ensemble_version(version_tag: str = '') -> dict:
    """Rank-IC-weighted ``ETF_ENSEMBLE`` row over the active ETF members. Writes no artifact file.

    Weights come from each member's pooled *holdout* rank IC, clamped at zero. Negative IC
    becomes weight 0 rather than a negative weight because a learned sign-flip on one
    quarter of noisy daily ICs is indistinguishable from luck, and the rotation engine can
    express "ignore this member" through a weight but not through a mirrored ranking it never
    validated. Equal weights when no member is positive.
    """
    members = {}
    for label in ('ETF_LIGHTGBM', 'ETF_LSTM'):
        model_type = _model_type(label)
        row = ModelVersion.objects.filter(
            model_type=model_type, is_active=True, status=ModelVersion.Status.READY,
        ).order_by('-trained_at').first()
        if row is None:
            continue
        pooled = dict((row.metadata or {}).get('pooled_holdout') or {})
        metrics = dict(row.metrics or {})
        members[label] = {
            'version': row.version,
            'rank_ic': pooled.get('rank_ic', metrics.get('rank_ic')),
            'icir': pooled.get('icir', metrics.get('icir')),
        }
    if not members:
        return {
            'status': 'no_members',
            'reason': 'No active ETF_LIGHTGBM/ETF_LSTM ModelVersion to weight; train a member first.',
        }

    positive = {
        label: max(0.0, float(info['rank_ic']))
        for label, info in members.items()
        if info.get('rank_ic') is not None and np.isfinite(float(info['rank_ic']))
    }
    total = sum(positive.values())
    if total <= 0:
        basis = 'equal_fallback'
        weights = {label: round(1.0 / len(members), 6) for label in members}
    else:
        basis = 'pooled_holdout_rank_ic'
        weights = {label: round(value / total, 6) for label, value in positive.items()}
    weights = {label: weights.get(label, 0.0) for label in members}

    end_date = timezone.localdate()
    version = _append_tag(f'etf-ensemble-{end_date.isoformat()}', _normalize_version_tag(version_tag))
    alias = engine_weights(weights)
    metadata = {
        'members': members,
        'weights': weights,
        'engine_weights': alias,
        'weight_keyspace': (
            "'weights' is keyed by ModelVersion label (ETF_LIGHTGBM/ETF_LSTM); "
            "'engine_weights' is the same numbers keyed by rotation_engine scorer name "
            "(LIGHTGBM/LSTM). Pass engine_weights to _rescale_ensemble_weights."
        ),
        'weight_basis': basis,
        'composition': 'weighted mean of per-date member scores (higher is better)',
        'artifact_note': (
            'DB-only weights record: no pickle, no artifact_store upload. Inference = score each '
            'member through its own loader and combine with these weights.'
        ),
        'created_at': timezone.now().isoformat(),
    }
    _register_model_version(
        model_type_label='ETF_ENSEMBLE',
        version=version,
        training_start=None,
        training_end=end_date,
        metrics={**weights, 'weight_basis': basis, 'member_count': len(members)},
        feature_columns=[],
        metadata=metadata,
        artifact_path='',
    )
    return {
        'status': 'success',
        'model_version': version,
        'weights': weights,
        'engine_weights': alias,
        'weight_basis': basis,
        'members': members,
    }


# -----------------------------------------------------------------------------
# Celery entry point
# -----------------------------------------------------------------------------
# The *definition* lives in this module because apps.etf_rotation.tasks is owned by another
# agent, but the *name* is deliberately in the ``apps.etf_rotation.tasks`` namespace:
# config/settings/base.py already routes 'apps.etf_rotation.tasks.train_etf_rotation_lightgbm'
# to the ``train-lightgbm`` queue (and train_lstm.py registers its own task the same way), so a
# module-qualified name would not match the route and the retrain would silently run on the
# default queue instead. If tasks.py ever declares this name too, the last import wins and
# Celery says nothing -- both definitions call the same train_walk_forward, so check the
# registry (`app.tasks`) before debugging a "task did not run" report.
# Limits are explicit because settings sets a global 60s soft limit, which would kill a real
# walk-forward retrain partway through its folds.
@shared_task(
    bind=True,
    name='apps.etf_rotation.tasks.train_etf_rotation_lightgbm',
    soft_time_limit=_setting_int('ETF_TRAINING_SOFT_TIME_LIMIT', 3600),
    time_limit=_setting_int('ETF_TRAINING_TIME_LIMIT', 3900),
)
def train_etf_rotation_lightgbm(self, start_date=None, end_date=None, n_folds: int = 6,
                                horizon_days=None, objective: str = DEFAULT_OBJECTIVE,
                                num_boost_round: int = None, params: dict = None) -> dict:
    """Celery wrapper around :func:`train_walk_forward`; ISO-string dates in, JSON-safe dict out."""
    parsed_start = date.fromisoformat(str(start_date)) if start_date else None
    parsed_end = date.fromisoformat(str(end_date)) if end_date else None
    try:
        return _json_safe(train_walk_forward(
            model_type_label='ETF_LIGHTGBM',
            start_date=parsed_start,
            end_date=parsed_end,
            n_folds=n_folds,
            horizon_days=horizon_days,
            objective=objective,
            num_boost_round=num_boost_round,
            params=params,
        ))
    except WalkForwardError as exc:
        logger.error('ETF walk-forward training aborted by the leak audit: %s', exc)
        return {'status': 'failed', 'reason': str(exc)}
