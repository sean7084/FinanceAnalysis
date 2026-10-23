"""Persist one live ETF rotation signal for one trade date.

The management command is argument parsing and rendering; every correctness claim lives
here so ``tests.py`` can drive the pipeline without ``call_command``. The load-bearing
property is that this path calls the same two things the backtest calls:
``backtest_engine.DatabaseDataSource.snapshots`` for the cross-section and
``rotation_engine.score_snapshots``/``build_rotation_plan`` for the book. A live signal that
ranks differently from the backtest which validated the parameters makes the strategy's
reported history fictional, so nothing here re-derives a pool, a filter or a weight.

Examples of what is deliberately *not* done here:

* No ``eligible_universe`` intersection. ``ETFFeatureSnapshot.is_eligible`` already carries
  the same age/delisting/``is_rankable`` verdict plus the rolling-turnover test that a
  queryset cannot express, so intersecting the two would re-filter an already-filtered
  cross-section for no change in the result. The backtest does not intersect either.
* No ``ic_history`` for ENSEMBLE. The backtest ranks with base weights (no per-date queries,
  no training-window leakage), so the live signal does too.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging

import pandas as pd
from django.conf import settings
from django.db import transaction

from apps.etf_rotation import rotation_engine as engine
from apps.etf_rotation.backtest_engine import DatabaseDataSource
from apps.etf_rotation.models import (
    ETFDaily,
    ETFFeatureSnapshot,
    ETFUniverse,
    ETFRotationPosition,
    ETFRotationSignal,
)
from apps.prediction.models import ModelVersion


logger = logging.getLogger(__name__)

#: Registry label behind each ML scorer. RULE has no artifact, so its ``model_version`` is
#: legitimately blank rather than a fabricated tag.
REGISTRY_LABELS = {'LIGHTGBM': 'ETF_LIGHTGBM', 'LSTM': 'ETF_LSTM'}
ENSEMBLE_REGISTRY_LABEL = 'ETF_ENSEMBLE'

#: Sessions of benchmark history behind ``benchmark_return_20d``, read over a wider calendar
#: window so a holiday week cannot starve the count.
BENCHMARK_LOOKBACK_SESSIONS = 20
BENCHMARK_WINDOW_DAYS = 60

#: Recent eligible sessions ``resolve_trade_date`` will accept before giving up: a snapshot
#: date can exist while every row on it belongs to a non-rankable vehicle, which is an empty
#: cross-section for the ranker even though the table has rows.
DATE_SEARCH_CANDIDATES = 5

MODEL_VERSION_MAX_LENGTH = 50
#: ``selected_etfs`` weights plus the defensive sleeve must be a whole book.
WEIGHT_SUM_TOLERANCE = 1e-6


class SignalGenerationError(RuntimeError):
    """The date, the pool or the model registry cannot produce a usable signal."""


def build_config(max_positions=None, momentum_lookback=None) -> engine.RotationConfig:
    """``RotationConfig`` with settings defaults and the command's overrides applied.

    The turnover and breadth knobs are left at ``RotationConfig``'s defaults on purpose:
    ``BacktestConfig`` forwards those same literals into its ``RotationConfig``, so an
    override here would make the live book differ from the backtested one.
    """
    return engine.rotation_config_from_settings(
        max_positions=max_positions,
        momentum_lookback=momentum_lookback,
    )


def resolve_trade_date(trade_date=None, *, source=None) -> date:
    """Explicit date, else the newest session whose eligible cross-section is non-empty.

    Not ``timezone.localdate()``: on a non-trading day or a day the feature job has not
    reached yet, the engine would answer "fully defensive" for a reason that has nothing to
    do with momentum, and the row would be indistinguishable from a genuine market call.
    """
    if trade_date not in (None, ''):
        day = _as_date(trade_date)
        if day is None:
            raise SignalGenerationError(f'trade_date {trade_date!r} is neither a date nor YYYY-MM-DD.')
        return day

    reader = source or DatabaseDataSource()
    candidates = list(
        ETFFeatureSnapshot.objects.filter(is_eligible=True)
        .order_by('-date').values_list('date', flat=True).distinct()[:DATE_SEARCH_CANDIDATES]
    )
    for day in candidates:
        if len(reader.snapshots(day)):
            return day
    raise SignalGenerationError(
        'ETFFeatureSnapshot has no eligible row with a rankable cross-section'
        f'{f" in its newest {len(candidates)} session(s)" if candidates else " at all"}; '
        'run compute_etf_features for the window, or pass --date for a covered session.'
    )


def load_snapshots(trade_date, *, source=None) -> pd.DataFrame:
    """This session's eligible point-in-time cross-section, labels excluded."""
    frame = (source or DatabaseDataSource()).snapshots(trade_date)
    if frame is None or len(frame) == 0:
        raise SignalGenerationError(
            f'No eligible ETFFeatureSnapshot rows on {trade_date} -- the point-in-time feature '
            'job has not produced a pool for this date, so there is nothing to rank. Run '
            'compute_etf_features for the window before generating a signal.'
        )
    return frame


def _active_version(registry_label: str):
    """The ``ModelVersion`` row the corresponding loader would actually serve, else ``None``.

    Mirrors both ``train_lightgbm.load_active_predictor`` and ``train_lstm._resolve_active_version``:
    the newest active READY row, then any READY row, because an interrupted retrain can leave
    every row inactive while a servable artifact still exists.
    """
    ready = ModelVersion.objects.filter(
        model_type=getattr(ModelVersion.ModelType, registry_label),
        status=ModelVersion.Status.READY,
    )
    return (
        ready.filter(is_active=True).order_by('-trained_at', '-created_at').first()
        or ready.order_by('-trained_at', '-created_at').first()
    )


def rule_model_version(config) -> str:
    """A stable, config-derived identity for the RULE model.

    RULE has no artifact, so ``model_version`` used to be the empty string and the /etf
    page had nothing to show as "the current model" -- which read like a broken row rather
    than like a hand-written scoring rule. The tag is derived from the knobs that actually
    change the book, so two signals with the same tag are reproducible from the tag alone.
    """
    if config is None:
        return 'rule-v1'
    return (
        f'rule-v1:mom{int(config.momentum_lookback)}-{config.weighting}'
        f'{":dedup" if config.dedup_track_index else ":nodedup"}'
        f':cap{int(config.max_per_category)}'
    )[:50]


def resolve_model_version(model_type: str, config=None) -> tuple[str, dict[str, str]]:
    """``(model_version for the row, every registry version resolved)``.

    Reading the version is also the availability gate. ``rotation_engine`` resolves
    predictors lazily and, for ENSEMBLE, renormalises over whichever sources it can load --
    so without this check an ensemble with nothing trained silently degrades to the rule
    blend while the row claims ``model_type='ENSEMBLE'``. A signal that looks model-driven
    but is not is worse than a failed run.
    """
    if model_type == 'RULE':
        return rule_model_version(config), {}

    members = {
        label: row
        for label in (*REGISTRY_LABELS.values(), ENSEMBLE_REGISTRY_LABEL)
        if (row := _active_version(label)) is not None
    }
    required = REGISTRY_LABELS.get(model_type)
    if not members or (required and required not in members):
        wanted = required or '/'.join(REGISTRY_LABELS.values())
        raise SignalGenerationError(
            f'no READY {wanted} ModelVersion is registered, so {model_type} cannot be scored; '
            'train one with manage.py train_etf_rotation_model before requesting this model type.'
        )

    versions = {label: str(row.version) for label, row in members.items()}
    if required:
        return _clip(versions[required]), versions
    if ENSEMBLE_REGISTRY_LABEL in versions:
        return _clip(versions[ENSEMBLE_REGISTRY_LABEL]), versions
    # Both members are servable but nobody has registered a weighted ensemble row yet, so
    # name the artifacts actually scoring rather than leaving the column blank.
    return _clip('+'.join(versions[label] for label in REGISTRY_LABELS.values() if label in versions)), versions


def previous_book(signal_type: str, trade_date) -> dict[str, float]:
    """The book the strategy last asked for, as the turnover constraint's incumbents.

    Read off the previous signal of the same ``signal_type`` rather than from
    ``ETFRotationPosition``: ``apply_turnover_constraint`` only tests *which* codes are held,
    the prior signal is the strategy's own last word, and a re-run of one date cannot be
    biased by legs the first run wrote. The defensive sleeve is included because the backtest
    holds it too -- ``_plan_to_target_weights`` puts it in the target book -- so it is
    displaced on the same terms.
    """
    day = _as_date(trade_date)
    prior = (
        ETFRotationSignal.objects.filter(signal_type=signal_type, date__lt=day)
        .order_by('-date').first()
    )
    if prior is None:
        return {}

    weights: dict[str, float] = {}
    for item in prior.selected_etfs or []:
        code = str(item.get('ts_code') or '')
        weight = float(item.get('weight') or 0.0)
        if code and weight > 0:
            weights[code] = weights.get(code, 0.0) + weight
    if prior.defensive_allocation > 0 and prior.defensive_asset:
        weights.setdefault(prior.defensive_asset, float(prior.defensive_allocation))
    return weights


def verify_book(plan, selected: list[dict]) -> float:
    """Sum the emitted book and refuse one that is not whole.

    ``build_rotation_plan`` asserts on its own legs; this checks the payload that reaches
    ``ETFRotationSignal.selected_etfs``, which is what every downstream reader sizes off.
    """
    total = sum(float(item['weight']) for item in selected) + float(plan.defensive_allocation)
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise SignalGenerationError(
            f'selected_etfs weights plus the {plan.defensive_asset} sleeve sum to {total:.9f}, '
            f'not 1.0 (defensive {plan.defensive_allocation:.6f}).'
        )
    return round(total, 6)


def benchmark_return_20d(trade_date, *, source=None) -> float | None:
    """Trailing 20-session benchmark return, or ``None`` when the index has too little history.

    Read through the backtest's own ``benchmark_close`` seam so the yardstick a live signal
    reports is the one the backtest compared against. Left NULL rather than filled with a
    pool average: a stand-in computed from the very funds being ranked would make an
    underperforming month look ordinary.
    """
    index_code = str(getattr(settings, 'ETF_BENCHMARK_CODE', '') or '')
    if not index_code:
        return None
    closes = (source or DatabaseDataSource()).benchmark_close(
        index_code, trade_date - timedelta(days=BENCHMARK_WINDOW_DAYS), trade_date,
    )
    if closes is None or len(closes) < BENCHMARK_LOOKBACK_SESSIONS + 1:
        return None
    newest = float(closes.iloc[-1])
    oldest = float(closes.iloc[-(BENCHMARK_LOOKBACK_SESSIONS + 1)])
    if oldest <= 0:
        return None
    return round(newest / oldest - 1.0, 6)


@transaction.atomic
def persist_signal(*, trade_date, signal_type: str, model_type: str, config, plan,
                   selected: list[dict], metadata: dict, model_version: str,
                   benchmark_return: float | None) -> dict:
    """Upsert the ``(date, signal_type)`` signal and rebuild the legs hanging off it.

    ``update_or_create`` on the model's unique key is what makes a re-run overwrite instead of
    appending a second row for "the latest signal" to choose between. The day's positions are
    deleted and rewritten from this plan, so a re-run whose ranking changed cannot leave the
    previous ranking's legs open under the same signal.
    """
    signal, created = ETFRotationSignal.objects.update_or_create(
        date=trade_date,
        signal_type=signal_type,
        defaults={
            'model_type': model_type,
            'rankings': plan.rankings,
            'selected_etfs': selected,
            'defensive_allocation': float(plan.defensive_allocation),
            'defensive_asset': plan.defensive_asset,
            'model_version': model_version,
            'benchmark_return_20d': benchmark_return,
            'max_positions': config.max_positions,
            'metadata': metadata,
        },
    )
    replaced = ETFRotationPosition.objects.filter(signal=signal).delete()[0]

    codes = [leg.ts_code for leg in plan.legs]
    prices = _entry_prices(trade_date, codes)
    etf_ids = dict(ETFUniverse.objects.filter(ts_code__in=codes).values_list('ts_code', 'id'))
    legs = []
    unpriced = []
    for leg in plan.legs:
        price = prices.get(leg.ts_code)
        if price is None or leg.ts_code not in etf_ids:
            # ``entry_price`` is non-null, so a leg with no bar on or before the signal date
            # is recorded nowhere rather than booked at a fabricated price. It stays in
            # ``selected_etfs``, and ``legs_without_price`` says which.
            unpriced.append(leg.ts_code)
            continue
        legs.append(ETFRotationPosition(
            signal=signal,
            etf_id=etf_ids[leg.ts_code],
            entry_date=trade_date,
            entry_price=price,
            weight=leg.weight,
            status=ETFRotationPosition.Status.OPEN,
            metadata={
                'rank': leg.rank,
                'score': leg.score,
                'reason': leg.reason,
                'signal_type': signal_type,
                'model_type': model_type,
            },
        ))
    ETFRotationPosition.objects.bulk_create(legs)
    return {
        'signal': signal,
        'created': created,
        'positions_written': len(legs),
        'positions_replaced': replaced,
        'legs_without_price': unpriced,
    }


def _whole_pool_unscorable(snapshots, scored, model_type: str, day) -> str:
    """Return a refusal message when the scorer excluded every row for missing inputs.

    ``_rule_scores`` drops a fund whose scoring inputs are null, which is right for the three
    names that listed last month: excluding a row that cannot be judged is what keeps the
    percentile grid honest. Read across the whole cross-section the same signal means
    something else -- the column is gone, not the funds. On 2026-10-22 that was exactly what
    happened: ``rs_vs_benchmark_60d`` was null for all 488 eligible rows because CSI
    publishes its total-return index after the ETF bars for the day have landed.

    Left alone, the engine answers ``no_scored_candidates`` and puts 100% of the book in the
    money fund -- a risk-off decision the model never made, written as a signal row the page
    presents as current. Refusing leaves the last signal that could actually see the market in
    place, and the freshness endpoint reports it as lagging by a session, which is the truth.
    """
    pool_size = int(len(snapshots))
    if pool_size == 0 or int(len(scored)) > 0:
        return ''
    rotation = dict(getattr(scored, 'attrs', {}).get('rotation') or {})
    if rotation.get('empty_cross_section') or str(rotation.get('model_type') or model_type) != 'RULE':
        return ''
    if int(rotation.get('excluded_missing_features') or 0) < pool_size:
        return ''
    required = (
        str(rotation.get('momentum_column') or ''),
        engine.VOLATILITY_COLUMN,
        str(rotation.get('relative_strength_column') or ''),
    )
    gone = [
        column for column in required
        if column in snapshots.columns and snapshots[column].isna().all()
    ]
    if not gone:
        return ''
    return (
        f'no candidate in the {day.isoformat()} pool could be scored: {", ".join(sorted(gone))} '
        f'is null for all {pool_size} eligible rows, which is a feed gap rather than a market '
        'signal. The RULE scorer needs the benchmark cross-section, so this run is refused '
        'instead of writing an all-defensive book. Check that the benchmark index sync reached '
        'this session before compute_etf_features ran.'
    )


def generate_rotation_signal(*, signal_type: str = 'MONTHLY', model_type: str = 'RULE',
                             trade_date=None, max_positions=None, momentum_lookback=None,
                             dry_run: bool = False) -> dict:
    """Rank one session's pool, persist the plan, and return a summary dict.

    The one date's pipeline: resolve the date -> read the point-in-time cross-section ->
    score -> build the plan (with the previous signal's book as the turnover constraint's
    incumbents) -> verify the book is whole -> persist ``ETFRotationSignal`` and its
    ``ETFRotationPosition`` rows. ``dry_run`` runs everything up to and including the weight
    check and writes nothing.
    """
    signal_type = str(signal_type or '').strip().upper()
    model_type = str(model_type or '').strip().upper()
    if signal_type not in ETFRotationSignal.SignalType.values:
        raise SignalGenerationError(
            f'signal_type must be one of {ETFRotationSignal.SignalType.values}, got {signal_type!r}.'
        )
    if model_type not in engine.MODEL_TYPES:
        raise SignalGenerationError(f'model_type must be one of {engine.MODEL_TYPES}, got {model_type!r}.')

    source = DatabaseDataSource()
    day = resolve_trade_date(trade_date, source=source)
    snapshots = load_snapshots(day, source=source)
    config = build_config(max_positions=max_positions, momentum_lookback=momentum_lookback)
    model_version, model_versions = resolve_model_version(model_type, config)
    current_weights = previous_book(signal_type, day)

    try:
        scored = engine.score_snapshots(
            snapshots, model_type=model_type, momentum_lookback=config.momentum_lookback,
        )
        blind_reason = _whole_pool_unscorable(snapshots, scored, model_type, day)
        if blind_reason:
            raise SignalGenerationError(blind_reason)
        plan = engine.build_rotation_plan(scored, snapshots, config, current_weights=current_weights)
    except engine.RotationEngineError as exc:
        raise SignalGenerationError(f'rotation engine failed for {day}: {exc}') from exc

    selected = plan.selected_etfs()
    weights_total = verify_book(plan, selected)
    benchmark_return = benchmark_return_20d(day, source=source)
    turnover = plan.metadata.get('turnover') or {}

    metadata = dict(plan.metadata)
    metadata.update({
        'requested_model_type': model_type,
        'signal_type': signal_type,
        'pool_size': int(len(snapshots)),
        'model_versions': model_versions,
        'previous_book': sorted(current_weights),
        'weights_total': weights_total,
        'benchmark_return_20d': benchmark_return,
        'benchmark_code': str(getattr(settings, 'ETF_BENCHMARK_CODE', '') or ''),
    })

    summary = {
        'date': day.isoformat(),
        'signal_type': signal_type,
        'model_type': model_type,
        'model_version': model_version,
        'dry_run': bool(dry_run),
        'legs': selected,
        'defensive_allocation': float(plan.defensive_allocation),
        'defensive_asset': plan.defensive_asset,
        'defensive_reason': plan.metadata.get('defensive_reason'),
        'weights_total': weights_total,
        'max_positions': config.max_positions,
        'momentum_column': plan.metadata.get('momentum_column'),
        'pool_size': int(len(snapshots)),
        'scored_size': int(len(scored)),
        'ranked_size': len(plan.rankings),
        'benchmark_return_20d': benchmark_return,
        'turnover': {
            'mode': turnover.get('mode'),
            'entries': len(turnover.get('entries') or []),
            'exits': len(turnover.get('exits') or []),
            'swaps_used': turnover.get('swaps_used'),
        },
        'signal_pk': None,
        'created': False,
        'positions_written': 0,
        'positions_replaced': 0,
        'legs_without_price': [],
    }
    if dry_run:
        return summary

    written = persist_signal(
        trade_date=day, signal_type=signal_type, model_type=model_type, config=config,
        plan=plan, selected=selected, metadata=metadata, model_version=model_version,
        benchmark_return=benchmark_return,
    )
    summary.update({
        'signal_pk': written['signal'].pk,
        'created': written['created'],
        'positions_written': written['positions_written'],
        'positions_replaced': written['positions_replaced'],
        'legs_without_price': written['legs_without_price'],
    })
    logger.info(
        'ETF rotation signal %s for %s %s: %s leg(s), defensive %.4f, ranked %s of %s.',
        written['signal'].pk, day, signal_type, len(plan.legs), plan.defensive_allocation,
        len(plan.rankings), len(snapshots),
    )
    return summary


def _entry_prices(trade_date, codes) -> dict[str, object]:
    """Close on the signal date, or the most recent bar at or before it, per code.

    A suspended fund's last traded price is the honest mark; refusing the leg would make the
    recorded book smaller than the signal says it is.
    """
    if not codes:
        return {}
    return dict(
        ETFDaily.objects.filter(etf__ts_code__in=list(codes), date__lte=trade_date)
        .order_by('etf__ts_code', '-date').distinct('etf__ts_code')
        .values_list('etf__ts_code', 'close')
    )


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _clip(version: str) -> str:
    return version[:MODEL_VERSION_MAX_LENGTH]
