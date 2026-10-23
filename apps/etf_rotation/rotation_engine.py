"""Portfolio construction for the ETF momentum-rotation module.

Two decisions carry the correctness of everything downstream (the live command and
``backtest_engine.py``, which consumes ``score_snapshots``/``build_rotation_plan``
verbatim):

1. Scoring and selection are split, and both are pure functions of one date's
   cross-section. Selection reads only point-in-time snapshot columns -- never
   ``fwd_return``/``label_rank_pct``/``label_top_bucket``, which live on the same table
   only because building a training frame must be one query. A backtest that reaches for
   a label while ranking is the classic momentum-in-flotation error, so the feature slice
   handed to a predictor is whitelisted instead of "all columns present".

2. The turnover constraint is deterministic: the *worst retained* holding is the only one
   a challenger may displace, and the incumbent's own rank is subtracted out. An absolute
   "rank 6 is outside the window so it is out" rule would churn a name every time some
   unrelated fund moved, which makes the fee bill rather than the alpha the dominant term
   in a monthly backtest. Displacement therefore has to clear ``rank_change_threshold``
   relative to the incumbent it actually evicts.
"""

from dataclasses import asdict, dataclass, field
import logging
import math

from django.conf import settings
import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)

def _setting(name, fallback):
    """Read one ``ETF_*`` setting, so the config defaults resolve per construction.

    Resolved inside a ``default_factory`` rather than at import: the module is imported by
    migrations and by ``manage.py`` commands that may run with a different settings value
    than the worker, and a module-level read would freeze whichever one came first.
    """
    return getattr(settings, name, fallback)


MODEL_TYPES = ('RULE', 'LIGHTGBM', 'LSTM', 'ENSEMBLE')
WEIGHTING_MODES = ('equal', 'score', 'invvol')

DEFAULT_MAX_POSITIONS = 5
# One value, read at construction time by every layer that needs a cash substitute: the
# rotation config, the backtest config, the ``ETFRotationBacktest`` column default and the
# workbench form. Each used to hardcode ``'511880.SH'`` independently, so a deployer that
# overrode the setting got a live signal in the new asset and a backtest in the old one.
DEFAULT_DEFENSIVE_ASSET = '511880.SH'


def default_defensive_asset():
    return str(_setting('ETF_DEFENSIVE_ASSET', DEFAULT_DEFENSIVE_ASSET))


# Selection constraints, both new in the 2026-10 follow-on plan. Four of the five legs of
# the 2026-10-20 signal were bank ETFs and two of them had byte-identical scores, because
# nothing stopped the pool from buying the same index three times under three wrappers.
DEFAULT_MAX_PER_CATEGORY = 2
# Inverse-volatility weights are clamped to this band around equal weight. Without the
# upper bound a 0.10-vol low-volatility ETF takes 1.4x its equal weight while a 0.35-vol
# name starves; the band is the whole point of the mode, not a guard rail.
INVOL_MIN_VOL = 0.01
INVOL_CLAMP_LO = 0.5
INVOL_CLAMP_HI = 1.5

# RULE composite weights. Momentum dominates because it is the thing the module claims to
# harvest; volatility and relative strength are adjustments rather than co-equal legs, so
# a fund that merely does not move cannot outrank one that trends.
MOMENTUM_WEIGHT = 0.6
RELATIVE_STRENGTH_WEIGHT = 0.2
VOLATILITY_PENALTY_WEIGHT = 0.2

VOLATILITY_COLUMN = 'vol_20d'
# The 20-session average turnover already on the snapshot table; it is the within-cluster
# arbiter when two funds tracking one index have no ``fund_share`` row.
TURNOVER_COLUMN = 'avg_amount_20d'
# Cross-section columns the selection constraints read. They are attributes of the fund,
# not factors, so they deliberately stay out of ``features.FEATURE_COLUMNS``.
CONSTRAINT_COLUMNS = ('track_index_key', 'shares_outstanding', TURNOVER_COLUMN, VOLATILITY_COLUMN)
MOMENTUM_PREFIX = 'mom'
RELATIVE_STRENGTH_PREFIX = 'rs_vs_benchmark'
# The RS horizon is chosen to match the momentum horizon: a 20d rotation is judged on 20d
# relative strength, a 60d rotation on 60d. 40 is the midpoint of the two stored horizons,
# i.e. the point past which the longer series stops being the better match.
RELATIVE_STRENGTH_SPLIT_LOOKBACK = 40

# Score-proportional weights on a rank-blend composite are nearly flat (the scores live in
# roughly [0, 1] and the top of the list is bunched), so pure score weighting is equal
# weight with extra steps. 70/30 keeps a sizing tilt while capping concentration.
SCORE_WEIGHT_SCORE_SHARE = 0.7
# Keeps a marginal candidate from being sized to an untradeable lot.
SCORE_WEIGHT_FLOOR = 0.01

WEIGHT_DECIMALS = 6
WEIGHT_TOLERANCE = 1e-9

DEFAULT_ENSEMBLE_WEIGHTS = {'RULE': 0.4, 'LIGHTGBM': 0.3, 'LSTM': 0.3}
# Below this many realised dates a source's rank IC is noise, so its base weight stands.
MIN_IC_OBSERVATION_DATES = 4

PREDICTOR_LOADERS = {
    'LIGHTGBM': 'apps.etf_rotation.train_lightgbm',
    'LSTM': 'apps.etf_rotation.train_lstm',
}

# Fallback feature discovery, used only when ``features.FEATURE_COLUMNS`` cannot be
# imported. These are the non-feature columns of ``ETFFeatureSnapshot``: identity fields
# plus the look-ahead label block, which must never reach a predictor.
NON_FEATURE_COLUMNS = frozenset({
    'ts_code', 'etf_id', 'name', 'category', 'sub_category', 'date', 'is_eligible',
    'track_index_key', 'shares_outstanding', 'share_date',
    'label_horizon_days', 'fwd_return', 'label_rank_pct', 'label_top_bucket', 'metadata',
})
FEATURE_PREFIXES = (
    'mom_', 'vol_', 'sharpe_', 'rs_vs_benchmark_', 'corr_with_benchmark_', 'rank_pct_',
    'rsi_', 'ma5_ratio', 'ma20_ratio', 'ma60_ratio', 'bollinger_pos', 'drawdown_from_high_',
    'avg_amount_', 'volume_ratio_',
)

# Signal JSON is read by humans and shipped to the API; a 2000-name pool would otherwise
# put the whole cross-section in every row.
MAX_LOGGED_CODES = 50


class RotationEngineError(RuntimeError):
    """A rotation input is unusable or a configuration is internally inconsistent.

    One error type on purpose: every caller (the live command, the backtest engine) turns
    it into "this date produced no plan", and the message is the only diagnosis available.
    """


@dataclass(frozen=True)
class RotationConfig:
    max_positions: int = 5
    momentum_lookback: int = 60
    absolute_momentum_filter: bool = True
    defensive_asset: str = field(default_factory=default_defensive_asset)
    # ``score`` is the default because that is what the production RULE reference run is
    # weighted with and what the ``ETFRotationBacktest.weighting`` column defaults to; the
    # dataclass used to say ``equal``, which meant an API-created run and an engine-default
    # run of the same name disagreed unless the caller remembered to pass it explicitly.
    weighting: str = 'score'        # 'equal' | 'score' | 'invvol'
    rank_change_threshold: int = 2     # rebalance only if a candidate moved >= this many ranks
    max_swaps: int = 2                 # at most this many names changed in one rebalance
    min_positions_for_risk_on: int = 3 # fewer qualifiers than this -> remainder goes defensive
    all_negative_to_defensive: bool = True
    dedup_track_index: bool = True     # at most one fund per tracked index
    # Caps how many names one ``category`` may contribute. The index dedup alone cannot
    # collapse the bank book: ``中证银行`` and ``中证800银行`` are genuinely different indices.
    max_per_category: int = DEFAULT_MAX_PER_CATEGORY

    def __post_init__(self):
        # frozen dataclass, so normalisation has to go through object.__setattr__.
        for name in ('max_positions', 'momentum_lookback', 'rank_change_threshold',
                     'max_swaps', 'min_positions_for_risk_on', 'max_per_category'):
            object.__setattr__(self, name, int(getattr(self, name)))
        for name in ('absolute_momentum_filter', 'all_negative_to_defensive', 'dedup_track_index'):
            object.__setattr__(self, name, bool(getattr(self, name)))
        object.__setattr__(self, 'weighting', str(self.weighting).strip().lower())
        if self.max_positions < 1:
            raise RotationEngineError('max_positions must be at least 1.')
        if self.momentum_lookback < 1:
            raise RotationEngineError('momentum_lookback must be at least 1.')
        if self.weighting not in WEIGHTING_MODES:
            raise RotationEngineError(f'weighting must be one of {WEIGHTING_MODES}, got {self.weighting!r}.')
        if self.rank_change_threshold < 1:
            raise RotationEngineError('rank_change_threshold must be at least 1.')
        if self.max_swaps < 0:
            raise RotationEngineError('max_swaps cannot be negative.')
        if self.max_per_category < 0:
            raise RotationEngineError('max_per_category cannot be negative; use 0 for no cap.')
        if not 1 <= self.min_positions_for_risk_on <= self.max_positions:
            raise RotationEngineError(
                f'min_positions_for_risk_on must sit between 1 and max_positions '
                f'({self.max_positions}), got {self.min_positions_for_risk_on}.'
            )


@dataclass
class RotationLeg:
    ts_code: str
    name: str
    score: float
    weight: float
    reason: str
    rank: int


@dataclass
class RotationPlan:
    legs: list[RotationLeg] = field(default_factory=list)
    defensive_allocation: float = 0.0
    defensive_asset: str = field(default_factory=default_defensive_asset)
    rankings: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def risk_on_allocation(self) -> float:
        return round(1.0 - self.defensive_allocation, WEIGHT_DECIMALS)

    def selected_etfs(self) -> list[dict]:
        """The ``ETFRotationSignal.selected_etfs`` shape, straight off the legs."""
        return [
            {'ts_code': leg.ts_code, 'name': leg.name, 'weight': leg.weight, 'reason': leg.reason}
            for leg in self.legs
        ]


# --------------------------------------------------------------------------- inputs


def _require_frame(frame, label):
    if isinstance(frame, pd.Series):
        frame = frame.to_frame()
    if not isinstance(frame, pd.DataFrame):
        raise RotationEngineError(f'{label} must be a DataFrame, got {type(frame).__name__}.')
    return frame


def _require_columns(frame, columns, *, label):
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise RotationEngineError(
            f'{label} is missing required column(s) {missing}; it has {list(frame.columns)}.'
        )


def _clean_cross_section(snapshots, label='snapshots'):
    """Cross-section keyed 1:1 on ``ts_code``, with duplicate codes dropped loudly.

    A column-less empty frame is tolerated because ``QuerySet.values()`` on an empty
    result set produces exactly that, and "no snapshots today" must yield a fully
    defensive plan rather than a column error.
    """
    frame = _require_frame(snapshots, label)
    if frame.empty and 'ts_code' not in frame.columns:
        return frame.reindex(columns=['ts_code'])
    _require_columns(frame, ('ts_code',), label=label)
    if frame.empty:
        return frame.copy()
    duplicates = int(frame['ts_code'].duplicated().sum())
    if duplicates:
        logger.warning('%s has %s duplicate ts_code row(s); keeping the first.', label, duplicates)
    return frame.drop_duplicates(subset=['ts_code'], keep='first').reset_index(drop=True)


def _clean_scored(scored):
    frame = _require_frame(scored, 'scored')
    _require_columns(frame, ('ts_code', 'score'), label='scored')
    if frame.empty:
        return frame[['ts_code', 'score']].copy()
    frame = frame.drop_duplicates(subset=['ts_code'], keep='first')[['ts_code', 'score']].copy()
    frame['score'] = pd.to_numeric(frame['score'], errors='coerce')
    return frame.dropna(subset=['score']).reset_index(drop=True)


def _text_map(snapshots, column, default=''):
    if column not in snapshots.columns or snapshots.empty:
        return {}
    return {
        code: (default if _is_missing(value) else str(value))
        for code, value in zip(snapshots['ts_code'], snapshots[column])
    }


def _value_map(snapshots, column):
    """``{ts_code: raw numeric}`` for a column that may be absent from the frame.

    Absent rather than zero: a cross-section produced by an older feature run has no
    ``track_index_key`` column at all, and the constraints must then be no-ops instead of
    collapsing every fund into one keyless cluster.
    """
    if column not in snapshots.columns or snapshots.empty:
        return {}
    return dict(zip(snapshots['ts_code'], snapshots[column]))


def _is_missing(value):
    return value is None or (isinstance(value, float) and math.isnan(value))


def _suffixes(columns, prefix):
    """Day counts available for the ``{prefix}_{N}d`` feature families."""
    found = []
    for column in columns:
        if not column.startswith(f'{prefix}_') or not column.endswith('d'):
            continue
        token = column[len(prefix) + 1:-1]
        if token.isdigit():
            found.append(int(token))
    return tuple(sorted(set(found)))


def resolve_momentum_column(columns, momentum_lookback):
    """``(column, substituted, available)`` for the configured lookback.

    Substitution rather than failure is deliberate: ``mom_45d`` is not stored and a
    rotation must still produce a plan, so the nearest stored horizon wins and the
    substitution is recorded in the plan metadata for whoever reads the signal.
    """
    available = _suffixes(columns, MOMENTUM_PREFIX)
    if not available:
        raise RotationEngineError(
            f'no mom_*d column in the cross-section, so momentum at lookback '
            f'{int(momentum_lookback)} cannot be read; it has {list(columns)}.'
        )
    requested = int(momentum_lookback)
    if requested in available:
        return f'{MOMENTUM_PREFIX}_{requested}d', False, available
    nearest = min(available, key=lambda days: (abs(days - requested), days))
    return f'{MOMENTUM_PREFIX}_{nearest}d', True, available


def resolve_relative_strength_column(columns, momentum_lookback):
    """``(column, substituted)`` for the RS term of the RULE composite."""
    available = _suffixes(columns, RELATIVE_STRENGTH_PREFIX)
    if not available:
        raise RotationEngineError(
            'no rs_vs_benchmark_*d column in the cross-section, so relative strength '
            'cannot adjust the RULE score.'
        )
    target = 60 if int(momentum_lookback) >= RELATIVE_STRENGTH_SPLIT_LOOKBACK else 20
    chosen = target if target in available else min(available, key=lambda days: (abs(days - target), days))
    return f'{RELATIVE_STRENGTH_PREFIX}_{chosen}d', chosen != target


def feature_columns(columns):
    """Feature columns to hand a trained predictor, plus where the list came from.

    ``features.FEATURE_COLUMNS`` is the whitelist, imported lazily because that module is
    owned concurrently and a module-level import would make the engine unimportable while
    it is in flight. The fallback is a prefix scan that excludes ``NON_FEATURE_COLUMNS``:
    labels sit on the same table as features, so "all columns" would hand a predictor its
    own answer.
    """
    source = 'features.FEATURE_COLUMNS'
    try:
        from apps.etf_rotation import features  # lazy by design, see docstring

        whitelist = list(features.FEATURE_COLUMNS)
    except Exception as exc:  # mid-write by another agent; the fallback is the point
        logger.info('features.FEATURE_COLUMNS unavailable (%s); using the prefix fallback.', exc)
        whitelist = []

    if whitelist:
        selected = [column for column in whitelist if column in columns and column != 'ts_code']
        if selected:
            return selected, source
        source = 'prefix_fallback'
    else:
        source = 'prefix_fallback'

    selected = [
        column for column in columns
        if column != 'ts_code'
        and column not in NON_FEATURE_COLUMNS
        and column.startswith(FEATURE_PREFIXES)
    ]
    if not selected:
        raise RotationEngineError(
            'cannot determine the feature set: features.FEATURE_COLUMNS is unavailable and '
            f'the cross-section holds no recognised feature columns (has {list(columns)}).'
        )
    return selected, source


# ------------------------------------------------------------------------ rule scores


def _pct_rank(series):
    # method='average' shares ranks across ties, which keeps the score reproducible.
    return series.rank(pct=True, method='average')


def _rule_scores(snapshots, momentum_lookback):
    """Rank-blend of momentum and relative strength minus a volatility penalty.

    Percentiles are computed *after* dropping candidates with missing inputs, so an
    unknown fund cannot compress the percentile grid of the funds we can actually judge,
    and the composite lands on a 0-1-ish scale that sits next to the ML score columns.
    """
    momentum_column, substituted, available = resolve_momentum_column(snapshots.columns, momentum_lookback)
    rs_column, rs_substituted = resolve_relative_strength_column(snapshots.columns, momentum_lookback)
    required = (momentum_column, VOLATILITY_COLUMN, rs_column)
    _require_columns(snapshots, required, label='RULE scoring')

    metadata = {
        'model_type': 'RULE',
        'momentum_column': momentum_column,
        'momentum_column_substituted': substituted,
        'available_momentum_lookbacks': list(available),
        'relative_strength_column': rs_column,
        'relative_strength_column_substituted': rs_substituted,
        'rule_weights': {
            'momentum': MOMENTUM_WEIGHT,
            'relative_strength': RELATIVE_STRENGTH_WEIGHT,
            'volatility_penalty': -VOLATILITY_PENALTY_WEIGHT,
        },
    }
    if snapshots.empty:
        empty = snapshots[['ts_code']].copy()
        empty['score'] = pd.Series(dtype='float64')
        metadata.update({'excluded_missing_features': 0, 'excluded_missing_features_ts_codes': []})
        return empty, metadata

    working = snapshots.loc[:, ['ts_code', *required]].copy()
    incomplete = working[list(required)].isna().any(axis=1)
    excluded = working.loc[incomplete, 'ts_code'].tolist()
    complete = working.loc[~incomplete].copy()
    # Missing-feature policy: excluded, counted, and never imputed to zero -- a zero would
    # silently read as "worst available fund" and rank the unknowns just above it.
    complete['score'] = (
        MOMENTUM_WEIGHT * _pct_rank(complete[momentum_column])
        + RELATIVE_STRENGTH_WEIGHT * _pct_rank(complete[rs_column])
        - VOLATILITY_PENALTY_WEIGHT * _pct_rank(complete[VOLATILITY_COLUMN])
    )
    metadata.update({
        'excluded_missing_features': len(excluded),
        'excluded_missing_features_ts_codes': excluded[:MAX_LOGGED_CODES],
    })
    return complete[['ts_code', 'score']].reset_index(drop=True), metadata


def _call_predictor(predictor, snapshots, model_type):
    """Run ``(snapshots_df) -> DataFrame[ts_code, score]`` on the feature slice only."""
    columns, feature_source = feature_columns(snapshots.columns)
    frame = snapshots.loc[:, ['ts_code', *columns]]
    try:
        raw = predictor(frame)
    except Exception as exc:
        raise RotationEngineError(f'{model_type} predictor raised {type(exc).__name__}: {exc}') from exc

    raw = _require_frame(raw, f'{model_type} predictor output')
    _require_columns(raw, ('ts_code', 'score'), label=f'{model_type} predictor output')
    scores = raw.drop_duplicates(subset=['ts_code'], keep='first')[['ts_code', 'score']].copy()
    scores['score'] = pd.to_numeric(scores['score'], errors='coerce')
    dropped = int(scores['score'].isna().sum())
    scores = scores.dropna(subset=['score'])
    outside = set(scores['ts_code']) - set(snapshots['ts_code'])
    if outside:
        logger.warning('%s predictor returned %s code(s) outside the cross-section; dropped.', model_type, len(outside))
    scores = scores[scores['ts_code'].isin(set(snapshots['ts_code']))].reset_index(drop=True)
    return scores, {
        'model_type': model_type,
        'feature_source': feature_source,
        'feature_count': len(columns),
        'excluded_missing_score': dropped,
    }


def _resolve_predictor(model_type, supplied):
    if supplied is not None:
        return supplied
    module_path = PREDICTOR_LOADERS.get(model_type)
    if not module_path:
        raise RotationEngineError(f'no predictor loader for model type {model_type}.')
    try:
        module = __import__(module_path, fromlist=['load_active_predictor'])
    except Exception as exc:
        # Not just ImportError: the training modules touch the Django app registry and the
        # model-artifact tables at import time, so AppRegistryNotReady or a DB error can
        # surface here. ENSEMBLE treats an unresolvable source as a composition detail, so
        # the widest catch is what keeps a partially trained stack scorable.
        raise RotationEngineError(
            f'{module_path} is unavailable, so {model_type} cannot be scored; pass '
            f'{model_type.lower()}_predictor explicitly or train the model first '
            f'({type(exc).__name__}: {exc}).'
        ) from exc
    loader = getattr(module, 'load_active_predictor', None)
    if loader is None:
        raise RotationEngineError(f'{module_path} exposes no load_active_predictor().')
    try:
        predictor = loader()
    except Exception as exc:
        raise RotationEngineError(
            f'{module_path}.load_active_predictor() failed for {model_type} '
            f'({type(exc).__name__}: {exc}).'
        ) from exc
    if predictor is None:
        raise RotationEngineError(f'{module_path}.load_active_predictor() found no active {model_type} model.')
    return predictor


# -------------------------------------------------------------------- ensemble scores


def _rescale_ensemble_weights(base_weights, sources, ic_by_source):
    """Renormalise the base weights over the sources that exist, then tilt by rank IC.

    A source with negative recent IC gets weight 0 rather than an inverted weight: an
    anti-predictive model is worthless here, and shorting its ranking would make the
    ensemble a different strategy than the one being backtested.
    """
    present = [name for name in base_weights if name in sources] or list(sources)
    base = {name: float(base_weights.get(name, 0.0)) for name in present}
    base_total = sum(base.values())
    if base_total <= 0:
        raise RotationEngineError(f'ensemble weights {base_weights} sum to zero.')
    base = {name: value / base_total for name, value in base.items()}

    details = {
        'base_weights': dict(base),
        'unweighted_sources': sorted(set(sources) - set(present)),
        'ic_by_source': {name: ic_by_source.get(name) for name in present},
        'ic_rescaling': 'none',
    }
    ics = {name: ic_by_source.get(name) for name in present}
    if not any(value is not None for value in ics.values()):
        details['final_weights'] = dict(base)
        return base, details

    adjusted = {
        name: value * max(ics[name], 0.0) if ics[name] is not None else value
        for name, value in base.items()
    }
    total = sum(adjusted.values())
    if total <= 0:
        # Every scored source has been anti-predictive lately. The base weights are the
        # honest default rather than a degenerate all-zero blend.
        details['ic_rescaling'] = 'all_nonpositive_ic_fell_back_to_base'
        details['final_weights'] = dict(base)
        return base, details

    details['ic_rescaling'] = 'rank_ic'
    weights = {name: value / total for name, value in adjusted.items()}
    details['final_weights'] = weights
    return weights, details


def _rank_ic(score_frame, labels):
    """Mean per-date Spearman correlation of a source's score against realised return."""
    if score_frame is None or score_frame.empty or labels is None or labels.empty:
        return None, 0
    joined = score_frame.merge(labels, on=['date', 'ts_code'], how='inner')
    if joined.empty:
        return None, 0
    correlations = []
    for _, group in joined.groupby('date', sort=True):
        # Three points and three distinct scores is the smallest sample where a rank
        # correlation means anything rather than being a coin flip.
        if len(group) < 3 or group['score'].nunique() < 3:
            continue
        matrix = group[['score', 'fwd_return']].corr(method='spearman')
        value = matrix.at['score', 'fwd_return']
        if pd.notna(value):
            correlations.append(float(value))
    if not correlations:
        return None, 0
    return float(sum(correlations) / len(correlations)), len(correlations)


def _clean_history(history):
    """Multi-date snapshot frame keyed on (date, ts_code), labels coerced to numbers."""
    frame = _require_frame(history, 'ic_history')
    _require_columns(frame, ('date', 'ts_code', 'fwd_return'), label='ic_history')
    if frame.empty:
        return frame.copy()
    frame = frame.drop_duplicates(subset=['date', 'ts_code'], keep='first')
    frame = frame.assign(
        date=pd.to_datetime(frame['date'], errors='coerce').dt.date,
        fwd_return=pd.to_numeric(frame['fwd_return'], errors='coerce'),
    )
    return frame.dropna(subset=['date']).reset_index(drop=True)


def _historical_score_frames(history, momentum_lookback, predictors):
    """Per-source ``[date, ts_code, score]`` frames over history, for rank IC only.

    Leakage caveat, recorded rather than hidden: an ML predictor trained *through* these
    dates is scored on data it has seen, so its measured IC is optimistic. The IC vector
    only moves weight between sources and never selects a name, which bounds the damage.
    A walk-forward backtest should retrain per step and pass ``ic_history=None``.
    """
    frames = {}
    for name, predictor in {'RULE': None, **predictors}.items():
        rows = []
        for snapshot_date, group in history.groupby('date', sort=True):
            if predictor is None:
                scored, _ = _rule_scores(group, momentum_lookback)
            else:
                try:
                    scored, _ = _call_predictor(predictor, group, name)
                except RotationEngineError:
                    continue
            if not scored.empty:
                scored = scored.copy()
                scored['date'] = snapshot_date
                rows.append(scored[['date', 'ts_code', 'score']])
        frames[name] = (
            pd.concat(rows, ignore_index=True)
            if rows else pd.DataFrame(columns=['date', 'ts_code', 'score'])
        )
    return frames


def _blend_source_ranks(sources, weights):
    """Blend per-date RANK percentiles, renormalising each candidate over its sources.

    The score scales are not comparable (a RULE composite sits in [0, 1], a LightGBM
    probability is distributed differently, an LSTM regression is in return units), so
    blending raw scores would let the widest-spread source decide the ranking. A
    candidate one source refused to score is handled by renormalising that candidate's
    weights over the sources that did answer -- the same arithmetic as the global
    renormalisation, applied per row.
    """
    names = [name for name in sources if not sources[name].empty]
    if not names:
        return pd.DataFrame(columns=['ts_code', 'score'])

    table = None
    rank_columns = []
    for name in names:
        ranked = sources[name].copy()
        column = f'rank__{name}'
        ranked[column] = _pct_rank(ranked['score'])
        ranked = ranked[['ts_code', column]]
        rank_columns.append(column)
        table = ranked if table is None else table.merge(ranked, on='ts_code', how='outer')

    weight_vector = np.array([float(weights.get(name, 0.0)) for name in names], dtype='float64')
    rank_matrix = table[rank_columns].to_numpy(dtype='float64')
    present = ~np.isnan(rank_matrix)
    totals = (present * weight_vector).sum(axis=1)
    contributions = (np.where(present, rank_matrix, 0.0) * weight_vector).sum(axis=1)
    with np.errstate(invalid='ignore', divide='ignore'):
        blended = np.where(totals > 0, contributions / totals, np.nan)

    result = pd.DataFrame({'ts_code': table['ts_code'].to_numpy(), 'score': blended})
    return result.dropna(subset=['score']).reset_index(drop=True)


def _ensemble_scores(snapshots, momentum_lookback, lgb_predictor, lstm_predictor,
                     ensemble_weights, ic_history):
    rule_frame, rule_metadata = _rule_scores(snapshots, momentum_lookback)
    sources = {'RULE': rule_frame}
    source_metadata = {'RULE': rule_metadata}
    predictors = {}
    unavailable = {}

    for model_type, supplied in (('LIGHTGBM', lgb_predictor), ('LSTM', lstm_predictor)):
        try:
            predictor = _resolve_predictor(model_type, supplied)
            scored, metadata = _call_predictor(predictor, snapshots, model_type)
        except RotationEngineError as exc:
            # A missing source inside an ensemble is a composition detail, not a failure:
            # the blend renormalises over what is available and says so in metadata.
            unavailable[model_type] = str(exc)
            continue
        predictors[model_type] = predictor
        sources[model_type] = scored
        source_metadata[model_type] = metadata

    ic_by_source = {}
    ic_observations = {}
    history_dates = 0
    if ic_history is not None:
        history = _clean_history(ic_history)
        history_dates = int(pd.Series(history['date']).nunique())
        labels = history[['date', 'ts_code', 'fwd_return']].dropna(subset=['fwd_return'])
        for name, frame in _historical_score_frames(history, momentum_lookback, predictors).items():
            value, count = _rank_ic(frame, labels)
            ic_observations[name] = count
            if value is not None and count >= MIN_IC_OBSERVATION_DATES:
                ic_by_source[name] = value

    weights, ensemble_metadata = _rescale_ensemble_weights(
        dict(ensemble_weights or DEFAULT_ENSEMBLE_WEIGHTS), sources, ic_by_source,
    )
    ensemble_metadata.update({
        'ic_history_dates': history_dates,
        'ic_observations': ic_observations,
        'sources_used': sorted(sources),
        'sources_resolved_from_predictor': sorted(predictors),
        # The spec's "skip a source with no predictor and renormalise" is this list; the
        # reasons are kept next to it so a reader knows whether it was an unimportable
        # training module or a module with nothing trained yet.
        'skipped_sources': sorted(set(dict(ensemble_weights or DEFAULT_ENSEMBLE_WEIGHTS)) - set(sources)),
    })
    if unavailable:
        ensemble_metadata['predictors_unavailable'] = unavailable

    blended = _blend_source_ranks(sources, weights)
    return blended, {
        'model_type': 'ENSEMBLE',
        'ensemble': ensemble_metadata,
        'source_metadata': source_metadata,
    }


def _ordered_by_score(frame):
    """Descending score, with 份额 then ``ts_code`` as the tie-break.

    The returned frame's row order *is* the ranking, so a consumer reading ``iloc[0]``
    gets the best candidate rather than whichever row the queryset happened to return
    first, and two runs on the same data agree.

    ``shares_outstanding`` sits between score and code because two funds tracking the same
    index score within a few ten-millionths of each other on the RULE composite -- on
    2026-10-20 ranks 2 and 3 were byte-identical at 0.71024590 -- and a ``ts_code``
    tie-break decides that coin flip arbitrarily. Fund size is the economically meaningful
    arbiter: it is what the position can actually be exited into. Missing shares sort last
    within an equal score, and the code stays as the final deterministic key.
    """
    if frame.empty:
        return frame
    columns = ['score']
    ascending = [False]
    if 'shares_outstanding' in frame.columns:
        columns.append('shares_outstanding')
        ascending.append(False)
    columns.append('ts_code')
    ascending.append(True)
    return frame.sort_values(columns, ascending=ascending, kind='mergesort').reset_index(drop=True)


def score_snapshots(snapshots: pd.DataFrame, *, model_type: str, momentum_lookback: int,
                    lgb_predictor=None, lstm_predictor=None,
                    ensemble_weights: dict[str, float] | None = None,
                    ic_history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Score one date's cross-section; returns ``DataFrame[ts_code, score]``.

    ``model_type`` is RULE / LIGHTGBM / LSTM / ENSEMBLE. LIGHTGBM and LSTM resolve their
    predictor lazily through ``train_lightgbm`` / ``train_lstm`` unless one is passed in,
    and raise ``RotationEngineError`` if neither is available -- a silently empty ranking
    would look exactly like a defensive market call downstream.

    ``ic_history`` is optional and only read by ENSEMBLE: a multi-date frame with ``date``,
    ``ts_code``, the feature columns and a realised ``fwd_return``. Without it the ensemble
    uses the base weights, which is what a walk-forward backtest wants (no per-date queries,
    no training-window leakage).

    Scoring diagnostics -- which momentum column was used, which candidates were dropped for
    missing features, the ensemble weight vector -- travel on ``result.attrs['rotation']`` so
    :func:`build_rotation_plan` can report them without this contract gaining arguments. They
    are advisory: the plan is still correct if a caller rebuilds the frame and loses attrs.
    """
    normalised = str(model_type or '').strip().upper()
    if normalised not in MODEL_TYPES:
        raise RotationEngineError(f'model_type must be one of {MODEL_TYPES}, got {model_type!r}.')
    cross_section = _clean_cross_section(snapshots)

    if cross_section.empty:
        # No snapshots on the calendar is a data problem for the caller to see in the plan,
        # not a ranking question: an empty score frame makes build_rotation_plan go fully
        # defensive, which is the only honest reading of "we do not know today".
        empty = pd.DataFrame(columns=['ts_code', 'score'])
        empty.attrs['rotation'] = {'model_type': normalised, 'empty_cross_section': True}
        return empty

    if normalised == 'ENSEMBLE':
        frame, metadata = _ensemble_scores(
            cross_section, int(momentum_lookback), lgb_predictor, lstm_predictor,
            ensemble_weights, ic_history,
        )
    elif normalised == 'RULE':
        frame, metadata = _rule_scores(cross_section, int(momentum_lookback))
    else:
        supplied = lgb_predictor if normalised == 'LIGHTGBM' else lstm_predictor
        frame, metadata = _call_predictor(_resolve_predictor(normalised, supplied), cross_section, normalised)

    result = _ordered_by_score(frame[['ts_code', 'score']].copy())
    result.attrs['rotation'] = metadata
    return result


# ------------------------------------------------------------------- selection core


def apply_turnover_constraint(ranked_codes, current_weights, *, max_positions,
                              rank_change_threshold, max_swaps):
    """Turn a ranking plus the current book into the next holding list.

    Pure and DB-free so the turnover rule can be exercised on its own. ``ranked_codes`` is
    the full ordered, already-filtered candidate list (best first); a held name missing
    from it has lost eligibility or gone negative on absolute momentum and must leave.
    Those exits and their refills are mandatory and are not charged to ``max_swaps``: the
    exit is unavoidable, so the paired replacement is not discretionary churn either, and
    a budget that blocked the refill would just park the money in the defensive asset
    while pretending to have limited trading.

    Only a *legacy* holding can be displaced. A name bought earlier in this same rebalance
    is already the ranking's own preference, and evicting it an instant later is precisely
    the churn this rule exists to prevent.

    Returns ``(selected_codes, info)`` with keeps, entries, exits, displacements, the
    mandatory/overflow exit lists, ``refills`` and ``swaps_used``.
    """
    rank_of = {code: index + 1 for index, code in enumerate(ranked_codes)}
    weights = dict(current_weights or {})
    held = [code for code, value in weights.items() if float(value or 0.0) > 0.0]
    max_positions = int(max_positions)
    rank_change_threshold = int(rank_change_threshold)
    max_swaps = int(max_swaps)

    retained = sorted((code for code in held if code in rank_of), key=lambda code: rank_of[code])
    mandatory_exits = [code for code in held if code not in rank_of]
    # Trimming an over-booked portfolio is also mandatory: the config shrank, so the excess
    # names cannot be held even though they still rank. They are not refilled -- the trim
    # exists precisely because the book is already at capacity.
    overflow_exits = []
    if len(retained) > max_positions:
        overflow_exits = retained[max_positions:]
        retained = retained[:max_positions]

    selected = list(retained)
    used = set(selected)
    entries = []
    displaced = []
    swaps_used = 0
    challenger = 0

    def next_candidate():
        nonlocal challenger
        while challenger < len(ranked_codes):
            code = ranked_codes[challenger]
            challenger += 1
            if code not in used:
                return code
        return None

    refills = 0
    for _ in range(min(len(mandatory_exits), max_positions - len(selected))):
        code = next_candidate()
        if code is None:
            break
        selected.append(code)
        used.add(code)
        refills += 1
        entries.append({'ts_code': code, 'rank': rank_of[code], 'reason': 'mandatory_exit_refill'})

    # Vacant slots (a book smaller than max_positions, e.g. straight after a fully
    # defensive period) take the best-ranked newcomers. Nothing is evicted, so
    # rank_change_threshold has no incumbent to compare against and only the budget applies.
    while len(selected) < max_positions:
        code = next_candidate()
        if code is None or swaps_used >= max_swaps:
            break
        selected.append(code)
        used.add(code)
        entries.append({'ts_code': code, 'rank': rank_of[code], 'reason': 'vacant_slot'})
        swaps_used += 1

    # Displacement: challengers arrive in rank order and only the worst legacy incumbent
    # is eligible, so the loop can stop at the first challenger that fails to clear the
    # threshold -- every later challenger ranks worse still.
    incumbents = list(retained)
    while len(selected) >= max_positions and incumbents and swaps_used < max_swaps:
        code = next_candidate()
        if code is None:
            break
        incumbent = max(incumbents, key=lambda held_code: rank_of[held_code])
        improvement = rank_of[incumbent] - rank_of[code]
        if improvement < rank_change_threshold:
            break
        selected.remove(incumbent)
        incumbents.remove(incumbent)
        selected.append(code)
        used.add(code)
        entries.append({
            'ts_code': code, 'rank': rank_of[code], 'reason': 'displacement',
            'displaced': incumbent, 'rank_improvement': improvement,
        })
        displaced.append({'ts_code': incumbent, 'rank': rank_of[incumbent], 'replaced_by': code})
        swaps_used += 1

    selected.sort(key=lambda code: rank_of[code])
    info = {
        'keeps': [code for code in retained if code in selected],
        'entries': entries,
        'exits': (
            [{'ts_code': code, 'reason': 'ineligible'} for code in mandatory_exits]
            + [{'ts_code': code, 'reason': 'over_capacity'} for code in overflow_exits]
            + [{'ts_code': item['ts_code'], 'reason': 'displaced', 'rank': item['rank']} for item in displaced]
        ),
        'mandatory_exits': mandatory_exits,
        'overflow_exits': overflow_exits,
        'refills': refills,
        'displacements': displaced,
        'swaps_used': swaps_used,
        'rank_change_threshold': rank_change_threshold,
        'max_swaps': max_swaps,
    }
    return selected, info


def _empty_turnover(mode, config, entries=None):
    """Turnover report for plans where the constraint never ran, so consumers can read
    ``metadata['turnover']`` unconditionally."""
    return {
        'mode': mode,
        'keeps': [],
        'entries': entries or [],
        'exits': [],
        'mandatory_exits': [],
        'overflow_exits': [],
        'refills': 0,
        'displacements': [],
        'swaps_used': 0,
        'rank_change_threshold': config.rank_change_threshold,
        'max_swaps': config.max_swaps,
    }


def _constraint_sort_value(value):
    """Numeric for descending comparison; missing or non-finite is always the smallest.

    A fund with no ``fund_share`` row must lose the cluster tie rather than win it by
    accident -- ``None`` compared as ``-inf`` is the only reading that does that.
    """
    if value is None:
        return float('-inf')
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float('-inf')
    return number if math.isfinite(number) else float('-inf')


def apply_selection_constraints(ordered_codes, *, score_by_code, category_by_code=None,
                                key_by_code=None, shares_by_code=None, amount_by_code=None,
                                dedup_track_index=True, max_per_category=0):
    """Collapse duplicate index wrappers, then cap how many names one category contributes.

    Two layers, because they solve two different problems and neither subsumes the other.
    Layer 1 is ``track_index_key``: the 2026-10-20 bank book contained 南方中证银行 and
    汇添富中证银行, which track the *same* index and therefore are one bet in two wrappers --
    buying both is 40% of the book on one number. Layer 2 is ``category``: 富国中证800银行
    tracks a genuinely different index, so dedup leaves it standing, and only a per-category
    cap takes the book from four bank funds to two.

    Inside a cluster the winner is chosen ``份额 -> 日均成交额 -> score``, not by score alone.
    The three bank wrappers' scores differ in the eighth decimal, so score cannot arbitrate;
    size and turnover can, and they are the properties that decide whether a position can
    actually be exited. Once a cluster has a representative, the survivors are ordered by
    ``score`` because that is what the rank-tilted weighting is about.

    Pure and DB-free, so the constraint can be exercised without a snapshot table.
    """
    codes = [str(code) for code in ordered_codes]
    info = {
        'dedup_track_index': bool(dedup_track_index),
        'max_per_category': int(max_per_category),
        'input_count': len(codes),
        'dedup_dropped': [],
        'category_cap_dropped': [],
    }
    if not codes:
        info['output_count'] = 0
        info['category_counts'] = {}
        return [], info

    def cluster_key(code):
        return (
            -_constraint_sort_value((shares_by_code or {}).get(code)),
            -_constraint_sort_value((amount_by_code or {}).get(code)),
            -_constraint_sort_value(score_by_code.get(code)),
            code,
        )

    def book_order(code):
        return (
            -_constraint_sort_value(score_by_code.get(code)),
            -_constraint_sort_value((shares_by_code or {}).get(code)),
            -_constraint_sort_value((amount_by_code or {}).get(code)),
            code,
        )

    survivors = codes
    if dedup_track_index:
        clusters: dict[str, list[str]] = {}
        for code in codes:
            # A blank key means the normaliser had nothing to work with, and "unknown" must
            # not become a cluster that collapses every unclassified fund into one leg.
            key = str((key_by_code or {}).get(code) or '').strip() or code
            clusters.setdefault(key, []).append(code)
        winners = []
        for key, members in clusters.items():
            best = sorted(members, key=cluster_key)[0]
            for code in members:
                if code != best:
                    info['dedup_dropped'].append({
                        'ts_code': code,
                        'track_index_key': key,
                        'selected_instead': best,
                        'score': _json_number(score_by_code.get(code)),
                        'reason': 'same_tracked_index',
                    })
            winners.append(best)
        survivors = sorted(winners, key=book_order)
        info['cluster_count'] = len(clusters)

    cap = int(max_per_category)
    if cap > 0:
        counts: dict[str, int] = {}
        capped = []
        for code in survivors:
            category = str((category_by_code or {}).get(code) or 'other')
            if counts.get(category, 0) >= cap:
                info['category_cap_dropped'].append({
                    'ts_code': code,
                    'category': category,
                    'cap': cap,
                    'score': _json_number(score_by_code.get(code)),
                    'reason': 'max_per_category',
                })
                continue
            counts[category] = counts.get(category, 0) + 1
            capped.append(code)
        survivors = capped

    info['output_count'] = len(survivors)
    final_counts: dict[str, int] = {}
    for code in survivors:
        category = str((category_by_code or {}).get(code) or 'other')
        final_counts[category] = final_counts.get(category, 0) + 1
    info['category_counts'] = final_counts
    info['dedup_dropped_count'] = len(info['dedup_dropped'])
    info['category_cap_dropped_count'] = len(info['category_cap_dropped'])
    return survivors, info


def _defensive_allocation(target_count, selected_count, all_momentum_negative, config):
    """``(risk_budget, defensive_allocation, reason)`` for one date's book.

    Slot arithmetic, which is what makes the two defensive rules compose: the target book
    has ``target_count`` slots, each worth ``(1 / target_count)`` of a fully risk-on plan,
    and the breadth rule then shrinks the *investable* share to
    ``target_count / min_positions_for_risk_on`` when too few names qualified. Slots the
    swap budget left unfilled are not invested either -- the money has to sit somewhere and
    the defensive asset is the honest answer, which is also what a live account would do.

    Concentrating the whole book into two names because only two qualified is the opposite
    of what a breadth filter is for; and sizing the two filled names off the *target* slot
    count rather than the filled count is what stops a swap cap from silently doubling
    every position.
    """
    if config.all_negative_to_defensive and all_momentum_negative:
        return 0.0, 1.0, 'all_negative_momentum'
    if target_count <= 0:
        return 0.0, 1.0, 'no_qualified_candidates'

    minimum = int(config.min_positions_for_risk_on)
    investable = 1.0 if target_count >= minimum else target_count / float(minimum)
    slot_share = investable / target_count
    risk_budget = round(slot_share * max(int(selected_count), 0), WEIGHT_DECIMALS)
    defensive = round(1.0 - risk_budget, WEIGHT_DECIMALS)

    if target_count < minimum:
        reason = 'insufficient_qualifiers'
    elif int(selected_count) < target_count:
        reason = 'capacity_unfilled'
    else:
        reason = 'risk_on'
    return risk_budget, defensive, reason


def allocate_weights(ranks, *, weighting, risk_budget, vols=None):
    """Split ``risk_budget`` across the selected names; returns ``(weights, method)``.

    ``ranks`` are 1-based against the full ordered candidate list, which is what score
    weighting tilts on: three names holding ranks 1, 2 and 7 do not deserve the same
    capital, and the linear-decay form keeps the tilt monotone without letting rank 1 take
    more than ``SCORE_WEIGHT_SCORE_SHARE`` of the book.

    ``vols`` is read only by ``invvol`` and must be in the same order as ``ranks``.
    """
    count = len(ranks)
    if count == 0:
        return [], 'none'
    budget = max(float(risk_budget), 0.0)
    equal = 1.0 / count
    if weighting == 'score':
        # The tilt is on rank rather than raw score because the RULE composite can be
        # negative after the volatility penalty, and proportional-to-a-negative-number
        # weights are meaningless.
        raw = [max(0.0, count + 1 - float(rank)) + SCORE_WEIGHT_FLOOR for rank in ranks]
        total = sum(raw)
        tilt = [
            SCORE_WEIGHT_SCORE_SHARE * (value / total) + (1.0 - SCORE_WEIGHT_SCORE_SHARE) * equal
            for value in raw
        ]
        method = 'score_rank_linear'
    elif weighting == 'invvol':
        # ``w_i propto 1/vol_i`` is the inverse-variance weighting of a portfolio whose
        # legs are treated as independent bets. They are not independent -- they are
        # sector ETFs -- so this is a sizing heuristic, not an optimiser, and the clamp is
        # what stops the flattest fund in the pool from dominating it.
        available = list(vols or [])
        usable = (
            len(available) == count
            and all(not _is_missing(value) and float(value) > 0 for value in available)
        )
        if not usable:
            # Same policy as the RULE composite's missing values: degrade visibly. A
            # missing vol treated as 0 would hand that leg the entire book.
            tilt = [equal] * count
            method = 'invvol_unavailable_equal'
        else:
            raw = [1.0 / max(float(value), INVOL_MIN_VOL) for value in available]
            total = sum(raw)
            lo = INVOL_CLAMP_LO * equal
            hi = INVOL_CLAMP_HI * equal
            tilt = [value / total for value in raw]
            # Clamp, then hand the money the clamp released to the legs that still have room,
            # and repeat. A single renormalise-after-clamping pass would push the leg it was
            # meant to cap back over ``hi`` -- on three legs 0.50 became 0.525 -- so the cap
            # would be a suggestion. The loop terminates because every pass either lands on
            # sum == 1 or fills the last unit of room.
            for _ in range(count):
                tilt = [min(hi, max(lo, value)) for value in tilt]
                gap = 1.0 - sum(tilt)
                if abs(gap) <= 1e-12:
                    break
                room = [(hi - value) if gap > 0 else (value - lo) for value in tilt]
                total_room = sum(value for value in room if value > 0.0)
                if total_room <= 0.0:
                    break
                tilt = [
                    value + (gap * share / total_room if share > 0.0 else 0.0)
                    for value, share in zip(tilt, room)
                ]
            method = 'invvol_clamped_0.5x_1.5x'
    else:
        tilt = [equal] * count
        method = 'equal'

    weights = [round(value * budget, WEIGHT_DECIMALS) for value in tilt]
    residual = round(budget - sum(weights), WEIGHT_DECIMALS)
    if abs(residual) > WEIGHT_TOLERANCE:
        # Binary rounding always leaves a sub-micro residual; it lands on the best holding
        # so ``sum(weights) == 1 - defensive_allocation`` is exact rather than approximate.
        receiver = min(range(count), key=lambda index: (ranks[index], index))
        weights[receiver] = round(weights[receiver] + residual, WEIGHT_DECIMALS)
    return weights, method


def build_rotation_plan(scored: pd.DataFrame, snapshots: pd.DataFrame,
                        config: RotationConfig,
                        current_weights: dict[str, float] | None = None,
                        exposure_scale: float = 1.0):
    """Build the target book for one date from its scores and feature cross-section.

    ``scored`` is :func:`score_snapshots` output; ``snapshots`` is the same cross-section,
    which supplies the absolute-momentum column, the display name and the category.
    ``current_weights=None`` yields the unconstrained top-N (what the strategy would buy
    from scratch, and what the backtest grid needs); pass the live book to enforce the
    turnover constraint.

    ``exposure_scale`` shrinks the risk-on budget and hands the remainder to the defensive
    asset. It exists for the fractional-Kelly governor in the backtest, which has to act on
    the *portfolio*, not per leg: the legs of a rotation book are correlated sector bets, so
    a per-leg ``f*`` would size five copies of one bet. ``1.0`` leaves every number
    bit-identical to a run of the same date before the knob existed.

    Invariant, asserted at the end: ``sum(leg.weight) + defensive_allocation == 1.0`` to
    within ``WEIGHT_TOLERANCE``. A plan that quietly left 8% in cash would show up in a
    backtest as unexplained alpha decay.
    """
    if not isinstance(config, RotationConfig):
        raise RotationEngineError('config must be a RotationConfig instance.')
    cross_section = _clean_cross_section(snapshots)
    scores = _clean_scored(scored)
    scoring_metadata = dict(getattr(scored, 'attrs', {}).get('rotation') or {})

    # Resolved only once there is something to rank: an empty snapshot date is a data
    # condition that must produce a fully defensive plan, not a missing-column error.
    momentum_column = scoring_metadata.get('momentum_column')
    substituted = False
    available: tuple[int, ...] = ()
    if not cross_section.empty:
        momentum_column, substituted, available = resolve_momentum_column(
            cross_section.columns, config.momentum_lookback,
        )

    metadata = {
        'model_type': scoring_metadata.get('model_type', 'UNKNOWN'),
        'momentum_column': momentum_column,
        'momentum_column_substituted': substituted,
        'available_momentum_lookbacks': list(available),
        'weighting': config.weighting,
        'config': asdict(config),
        'scoring_metadata': scoring_metadata,
        'scoring_metadata_available': bool(scoring_metadata),
        'scored_count': int(len(scores)),
    }
    if 'date' in cross_section.columns and not cross_section.empty:
        dates = pd.to_datetime(cross_section['date'], errors='coerce').dt.date.dropna()
        if len(dates):
            metadata['date'] = max(dates).isoformat()

    def defensive_plan(reason, **extra):
        metadata.update({
            'defensive_reason': reason,
            'weights_sum': 0.0,
            'risk_budget': 0.0,
            'weight_method': 'none',
            'qualified_count': 0,
            'target_count': 0,
            'selected_count': 0,
            'turnover': _empty_turnover('not_applied', config),
        })
        metadata.update(extra)
        return RotationPlan(
            legs=[], defensive_allocation=1.0, defensive_asset=config.defensive_asset,
            rankings=[], metadata=metadata,
        )

    if scores.empty:
        return defensive_plan('no_scored_candidates')
    if momentum_column is None or momentum_column not in cross_section.columns:
        raise RotationEngineError(
            f'snapshots provides no {momentum_column or "mom_*d"} column, so the absolute momentum '
            f'filter cannot be applied to {len(scores)} scored candidates; it has '
            f'{list(cross_section.columns)}.'
        )

    names = _text_map(cross_section, 'name', default='')
    categories = _text_map(cross_section, 'category', default='other')

    working = scores.merge(
        cross_section[['ts_code', momentum_column]].drop_duplicates(subset=['ts_code'], keep='first'),
        on='ts_code',
        how='inner',
    )
    # The defensive asset is never also a risk leg. The volatility penalty actively rewards
    # the flattest funds in the pool, so a money-market ETF would otherwise be bought twice
    # -- once as a leg and once as the defensive allocation.
    defensive_in_pool = int(working['ts_code'].eq(config.defensive_asset).sum())
    working = working[working['ts_code'] != config.defensive_asset]

    missing_momentum = int(working[momentum_column].isna().sum())
    working = working.dropna(subset=[momentum_column])
    # Measured before the filter, because the filter deletes the evidence it looks for.
    all_negative = bool(len(working)) and bool((working[momentum_column] < 0).all())

    momentum_dropped = []
    if config.absolute_momentum_filter:
        negative = working.loc[working[momentum_column] < 0, ['ts_code', 'score', momentum_column]]
        momentum_dropped = [
            {'ts_code': code, 'score': _json_number(score), 'momentum': _json_number(momentum)}
            for code, score, momentum in negative.itertuples(index=False)
        ]
        working = working[working[momentum_column] >= 0]

    if working.empty:
        return defensive_plan(
            'all_negative_momentum' if config.absolute_momentum_filter and all_negative else 'no_qualified_candidates',
            absolute_momentum_excluded=momentum_dropped[:MAX_LOGGED_CODES],
            absolute_momentum_excluded_count=len(momentum_dropped),
            excluded_missing_momentum=missing_momentum,
            all_negative_momentum=all_negative,
        )

    working = _ordered_by_score(
        working.merge(
            cross_section.drop_duplicates(subset=['ts_code'], keep='first')[
                ['ts_code', *[
                    column for column in CONSTRAINT_COLUMNS
                    if column in cross_section.columns
                ]]
            ],
            on='ts_code',
            how='left',
        )
    )
    pre_constraint_codes = working['ts_code'].tolist()
    score_by_code = dict(zip(working['ts_code'], working['score']))
    momentum_by_code = dict(zip(working['ts_code'], working[momentum_column]))
    shares_by_code = _value_map(working, 'shares_outstanding')
    amount_by_code = _value_map(working, TURNOVER_COLUMN)
    vol_by_code = _value_map(working, VOLATILITY_COLUMN)
    key_by_code = {
        code: str(value or '') for code, value in _value_map(working, 'track_index_key').items()
    }
    # Same index in two wrappers is one bet, and one sector is one factor: both constraints
    # run before ranking so the ranks, the score tilt and the turnover machinery all see
    # the book the strategy is actually allowed to hold.
    ranked_codes, constraint_info = apply_selection_constraints(
        pre_constraint_codes,
        score_by_code=score_by_code,
        category_by_code=categories,
        key_by_code=key_by_code,
        shares_by_code=shares_by_code,
        amount_by_code=amount_by_code,
        dedup_track_index=config.dedup_track_index,
        max_per_category=config.max_per_category,
    )
    rank_of = {code: index + 1 for index, code in enumerate(ranked_codes)}
    # The book the ranking wants, before any trading constraint is applied. Defensive sizing
    # works off this so a swap cap cannot masquerade as a breadth signal.
    target_count = min(len(ranked_codes), config.max_positions)

    if current_weights is None:
        # The unconstrained plan is the strategy's own preference, which is what the
        # backtest needs; the turnover machinery is reported as not applied.
        selected = ranked_codes[:config.max_positions]
        turnover = _empty_turnover(
            'unconstrained', config,
            entries=[{'ts_code': code, 'rank': rank_of[code], 'reason': 'top_rank'} for code in selected],
        )
    else:
        selected, turnover = apply_turnover_constraint(
            ranked_codes, current_weights,
            max_positions=config.max_positions,
            rank_change_threshold=config.rank_change_threshold,
            max_swaps=config.max_swaps,
        )
        turnover['mode'] = 'constrained'

    risk_budget, defensive_allocation, defensive_reason = _defensive_allocation(
        target_count, len(selected), all_negative, config,
    )
    scale = max(0.0, min(float(exposure_scale), 1.0))
    if scale != 1.0:
        # The governor only moves money between the risk legs and the defensive sleeve, so
        # the full-book invariant below still holds exactly.
        risk_budget = round(risk_budget * scale, WEIGHT_DECIMALS)
        defensive_allocation = round(1.0 - risk_budget, WEIGHT_DECIMALS)
        defensive_reason = f'{defensive_reason}+exposure_scaled'
        metadata['exposure_scale'] = scale
    legs = []
    weights, weight_method = allocate_weights(
        [rank_of[code] for code in selected], weighting=config.weighting, risk_budget=risk_budget,
        vols=[vol_by_code.get(code) for code in selected],
    )
    entry_reasons = {item['ts_code']: item['reason'] for item in turnover['entries']}
    for code, weight in zip(selected, weights):
        legs.append(RotationLeg(
            ts_code=code,
            name=names.get(code) or code,
            score=_json_number(score_by_code[code]),
            weight=weight,
            reason=(
                f"{entry_reasons.get(code, 'retained')}; rank {rank_of[code]}; "
                f"score {score_by_code[code]:.4f}; "
                f"{momentum_column} {_format_pct(momentum_by_code.get(code))}"
            ),
            rank=rank_of[code],
        ))

    weights_sum = round(sum(leg.weight for leg in legs), WEIGHT_DECIMALS)
    total = round(weights_sum + defensive_allocation, WEIGHT_DECIMALS)
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise RotationEngineError(
            f'plan does not allocate a full book: weights {weights_sum} + defensive '
            f'{defensive_allocation} = {total}.'
        )

    metadata.update({
        'weight_method': weight_method,
        # Count of names that survived the absolute-momentum screen, before the selection
        # constraints collapsed duplicates -- the number an operator reads as "how many
        # funds looked good", which is not the same as how many we are allowed to buy.
        'qualified_count': len(pre_constraint_codes),
        'candidate_count': len(ranked_codes),
        'selection_constraints': constraint_info,
        'dedup_track_index': config.dedup_track_index,
        'max_per_category': config.max_per_category,
        'target_count': target_count,
        'selected_count': len(selected),
        'risk_budget': risk_budget,
        'min_positions_for_risk_on': config.min_positions_for_risk_on,
        'defensive_reason': defensive_reason,
        'all_negative_momentum': all_negative,
        'excluded_missing_momentum': missing_momentum,
        'absolute_momentum_excluded': momentum_dropped[:MAX_LOGGED_CODES],
        'absolute_momentum_excluded_count': len(momentum_dropped),
        'defensive_asset_excluded_from_ranking': defensive_in_pool,
        'turnover': turnover,
        'weights_sum': weights_sum,
    })

    return RotationPlan(
        legs=legs,
        defensive_allocation=defensive_allocation,
        defensive_asset=config.defensive_asset,
        rankings=[
            {
                'ts_code': code,
                'name': names.get(code) or code,
                'score': _json_number(score_by_code[code]),
                'rank': rank_of[code],
                'category': categories.get(code) or 'other',
            }
            for code in ranked_codes
        ],
        metadata=metadata,
    )


def _json_number(value):
    if value is None:
        return None
    number = float(value)
    # JSON has no NaN token; a null in the signal row is valid and visibly missing, whereas
    # Python's bare NaN would break any strict parser reading the column.
    return None if math.isnan(number) else round(number, 8)


def _format_pct(value):
    return 'n/a' if _is_missing(value) else f'{float(value):+.2%}'


def rotation_config_from_settings(**overrides) -> RotationConfig:
    """``RotationConfig`` with the module tunables read from ``settings``.

    Command-only sugar: the settings defaults live in code so the engine stays usable with
    no ``ETF_*`` settings defined yet.
    """
    values = {
        'max_positions': getattr(settings, 'ETF_MAX_POSITIONS', DEFAULT_MAX_POSITIONS),
        'defensive_asset': getattr(settings, 'ETF_DEFENSIVE_ASSET', DEFAULT_DEFENSIVE_ASSET),
        'weighting': getattr(settings, 'ETF_WEIGHTING', 'score'),
        'dedup_track_index': getattr(settings, 'ETF_DEDUP_TRACK_INDEX', True),
        'max_per_category': getattr(settings, 'ETF_MAX_PER_CATEGORY', DEFAULT_MAX_PER_CATEGORY),
    }
    values.update({key: value for key, value in overrides.items() if value is not None})
    return RotationConfig(**values)
