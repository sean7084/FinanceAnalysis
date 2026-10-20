from datetime import date
from decimal import Decimal

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.analytics.models import TechnicalIndicator
from apps.factors.models import FactorScore
from apps.markets.benchmarking import effective_universe_tradeable_assets
from apps.markets.models import Asset
from apps.macro.models import MarketContext
from apps.prediction.historical_features import latest_momentum, latest_rs_score, latest_rsi
from apps.prediction.reconciliation import (
    ENSEMBLE,
    RECONCILE_SOURCES,
    gaps_for_date,
    open_trading_dates,
)
from apps.sentiment.models import SentimentScore
from .models import ModelVersion, PredictionResult
from .odds import estimate_trade_decision


def _clamp(value, low=Decimal('0'), high=Decimal('1')):
    return max(low, min(high, value))


def _latest_decimal(queryset, attr='value', default=Decimal('0.5')):
    obj = queryset.first()
    if obj is None:
        return default
    raw = getattr(obj, attr)
    if raw is None:
        return default
    return Decimal(str(raw))


def _resolve_context(explicit_macro_phase=None, explicit_event_tag=None, as_of=None):
    if explicit_macro_phase or explicit_event_tag:
        return explicit_macro_phase or '', explicit_event_tag or ''

    current_queryset = MarketContext.objects.filter(context_key='current', is_active=True)
    if as_of is not None:
        current = (
            current_queryset.filter(starts_at__lte=as_of)
            .filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=as_of))
            .order_by('-starts_at', '-updated_at')
            .first()
        )
        if current is None:
            current = current_queryset.filter(starts_at__lte=as_of).order_by('-starts_at', '-updated_at').first()
    else:
        current = current_queryset.order_by('-starts_at', '-updated_at').first()

    if not current:
        return '', ''
    return current.macro_phase, current.event_tag or ''


def _feature_snapshot(asset_id, as_of, cache=None):
    factor = FactorScore.objects.filter(
        asset_id=asset_id,
        date__lte=as_of,
        mode=FactorScore.FactorMode.COMPOSITE,
    ).order_by('-date').first()

    sentiment = SentimentScore.objects.filter(
        asset_id=asset_id,
        date__lte=as_of,
        score_type=SentimentScore.ScoreType.ASSET_7D,
    ).order_by('-date').first()

    rsi = latest_rsi(asset_id, as_of, default=Decimal('50'), cache=cache)
    mom_5d = latest_momentum(asset_id, as_of, n_days=5, default=Decimal('0'), cache=cache)
    rs_score = latest_rs_score(asset_id, as_of, default=Decimal('0.5'), cache=cache)

    return {
        'factor_composite': Decimal(str(getattr(factor, 'composite_score', Decimal('0.5')))),
        'factor_bottom_prob': Decimal(str(getattr(factor, 'bottom_probability_score', Decimal('0.5')))),
        'sentiment_score': Decimal(str(getattr(sentiment, 'sentiment_score', Decimal('0')))),
        'rsi': rsi,
        'mom_5d': mom_5d,
        'rs_score': rs_score,
    }


def _probabilities_from_features(features, horizon_days, macro_phase):
    base = Decimal('0.33')

    factor_signal = (features['factor_bottom_prob'] - Decimal('0.5'))
    sentiment_signal = features['sentiment_score'] * Decimal('0.25')
    momentum_signal = features['mom_5d'] * Decimal('1.2')
    rs_signal = (features['rs_score'] - Decimal('0.5')) * Decimal('0.4')

    horizon_scale = {3: Decimal('1.10'), 7: Decimal('1.00'), 30: Decimal('0.85')}.get(horizon_days, Decimal('1.00'))

    up = base + (momentum_signal + sentiment_signal + rs_signal - factor_signal) * horizon_scale
    down = base + (factor_signal - sentiment_signal - momentum_signal - rs_signal) * horizon_scale
    flat = Decimal('1') - up - down

    if macro_phase == MarketContext.MacroPhase.RECESSION:
        down += Decimal('0.04')
        up -= Decimal('0.02')
    elif macro_phase == MarketContext.MacroPhase.RECOVERY:
        up += Decimal('0.03')
        down -= Decimal('0.02')

    up = _clamp(up)
    down = _clamp(down)
    flat = _clamp(Decimal('1') - up - down)

    total = up + flat + down
    if total <= 0:
        return Decimal('0.33'), Decimal('0.34'), Decimal('0.33')

    up, flat, down = up / total, flat / total, down / total
    return _clamp(up), _clamp(flat), _clamp(down)


def _predicted_label(up, flat, down):
    if up >= flat and up >= down:
        return PredictionResult.Label.UP
    if down >= up and down >= flat:
        return PredictionResult.Label.DOWN
    return PredictionResult.Label.FLAT


def _confidence(up, flat, down):
    probs = sorted([up, flat, down], reverse=True)
    margin = probs[0] - probs[1]
    return _clamp(Decimal('0.5') + margin)


def _ensure_active_ensemble_version(as_of):
    active = ModelVersion.objects.filter(model_type=ModelVersion.ModelType.ENSEMBLE, is_active=True).order_by('-created_at').first()
    if active:
        return active

    return ModelVersion.objects.create(
        model_type=ModelVersion.ModelType.ENSEMBLE,
        version=f'ensemble-{as_of.isoformat()}',
        status=ModelVersion.Status.READY,
        artifact_path='models/ensemble/latest.json',
        metrics={'note': 'heuristic baseline pending full ML training pipeline'},
        feature_schema=['factor_composite', 'factor_bottom_prob', 'sentiment_score', 'rsi', 'mom_5d', 'rs_score'],
        trained_at=timezone.now(),
        training_window_start=as_of.replace(year=max(2000, as_of.year - 5)),
        training_window_end=as_of,
        is_active=True,
        metadata={'source': 'phase14_baseline'},
    )


@shared_task
def train_prediction_models(target_date=None):
    if target_date:
        try:
            as_of = date.fromisoformat(str(target_date))
        except ValueError:
            as_of = timezone.now().date()
    else:
        as_of = timezone.now().date()

    retired_stub_count = ModelVersion.objects.filter(
        model_type__in=[ModelVersion.ModelType.LIGHTGBM, ModelVersion.ModelType.LSTM],
        is_active=True,
        metadata__source='phase14_training_stub',
    ).update(is_active=False)

    ModelVersion.objects.filter(
        model_type=ModelVersion.ModelType.ENSEMBLE,
        is_active=True,
    ).update(is_active=False)
    refreshed_version = _ensure_active_ensemble_version(as_of)

    return (
        f'Phase 14 ensemble baseline refreshed for {as_of} '
        f'({refreshed_version.version}); retired {retired_stub_count} legacy stub model versions'
    )


# The global soft limit is 60s (config/settings/base.py), which is roughly five
# assets at this loop's cost: the sweep was being killed mid-universe and leaving
# a trading date with a handful of rows. A measured universe day is around two
# hours of CPU, so this budget covers most of one day and
# `reconcile_prediction_coverage` writes whatever is still missing.
@shared_task(soft_time_limit=3600, time_limit=3900)
def generate_predictions_for_date(target_date=None, horizons=None, macro_phase=None, event_tag=None):
    if target_date:
        try:
            as_of = date.fromisoformat(str(target_date))
        except ValueError:
            as_of = timezone.now().date()
    else:
        as_of = timezone.now().date()

    horizons = horizons or [3, 7, 30]
    ctx_macro, ctx_event = _resolve_context(macro_phase, event_tag, as_of=as_of)
    version = _ensure_active_ensemble_version(as_of)

    processed = 0
    for asset in effective_universe_tradeable_assets(as_of, context=f'Daily prediction for {as_of}'):
        runtime_cache = {}
        features = _feature_snapshot(asset.id, as_of, cache=runtime_cache)
        for horizon in horizons:
            up, flat, down = _probabilities_from_features(features, int(horizon), ctx_macro)
            label = _predicted_label(up, flat, down)
            conf = _confidence(up, flat, down)
            trade_decision = estimate_trade_decision(
                asset_id=asset.id,
                as_of=as_of,
                horizon_days=int(horizon),
                up_probability=up,
                predicted_label=label,
                cache=runtime_cache,
            )

            PredictionResult.objects.update_or_create(
                asset=asset,
                date=as_of,
                horizon_days=int(horizon),
                model_version=version,
                defaults={
                    'up_probability': up,
                    'flat_probability': flat,
                    'down_probability': down,
                    'confidence': conf,
                    'predicted_label': label,
                    'target_price': trade_decision['target_price'],
                    'stop_loss_price': trade_decision['stop_loss_price'],
                    'risk_reward_ratio': trade_decision['risk_reward_ratio'],
                    'trade_score': trade_decision['trade_score'],
                    'suggested': trade_decision['suggested'],
                    'macro_phase': ctx_macro,
                    'event_tag': ctx_event,
                    'feature_payload': {
                        'factor_composite': float(features['factor_composite']),
                        'factor_bottom_prob': float(features['factor_bottom_prob']),
                        'sentiment_score': float(features['sentiment_score']),
                        'rsi': float(features['rsi']),
                        'mom_5d': float(features['mom_5d']),
                        'rs_score': float(features['rs_score']),
                    },
                    'metadata': {
                        'source': 'phase14_baseline_prediction',
                        'horizon': int(horizon),
                        'trade_decision_engine': 'v1',
                    },
                },
            )
            processed += 1

    return f'Predictions generated for {processed} asset-horizon rows on {as_of}'


def _generate_predictions_for_single_asset(asset, as_of, horizons, macro_phase=None, event_tag=None):
    ctx_macro, ctx_event = _resolve_context(macro_phase, event_tag, as_of=as_of)
    version = _ensure_active_ensemble_version(as_of)

    processed = 0
    runtime_cache = {}
    features = _feature_snapshot(asset.id, as_of, cache=runtime_cache)
    for horizon in horizons:
        up, flat, down = _probabilities_from_features(features, int(horizon), ctx_macro)
        label = _predicted_label(up, flat, down)
        conf = _confidence(up, flat, down)
        trade_decision = estimate_trade_decision(
            asset_id=asset.id,
            as_of=as_of,
            horizon_days=int(horizon),
            up_probability=up,
            predicted_label=label,
            cache=runtime_cache,
        )

        PredictionResult.objects.update_or_create(
            asset=asset,
            date=as_of,
            horizon_days=int(horizon),
            model_version=version,
            defaults={
                'up_probability': up,
                'flat_probability': flat,
                'down_probability': down,
                'confidence': conf,
                'predicted_label': label,
                'target_price': trade_decision['target_price'],
                'stop_loss_price': trade_decision['stop_loss_price'],
                'risk_reward_ratio': trade_decision['risk_reward_ratio'],
                'trade_score': trade_decision['trade_score'],
                'suggested': trade_decision['suggested'],
                'macro_phase': ctx_macro,
                'event_tag': ctx_event,
                'feature_payload': {
                    'factor_composite': float(features['factor_composite']),
                    'factor_bottom_prob': float(features['factor_bottom_prob']),
                    'sentiment_score': float(features['sentiment_score']),
                    'rsi': float(features['rsi']),
                    'mom_5d': float(features['mom_5d']),
                    'rs_score': float(features['rs_score']),
                },
                'metadata': {
                    'source': 'phase14_baseline_prediction',
                    'horizon': int(horizon),
                    'trade_decision_engine': 'v1',
                },
            },
        )
        processed += 1

    return processed


@shared_task
def generate_prediction_for_asset(asset_id, target_date=None, horizons=None, macro_phase=None, event_tag=None):
    if target_date:
        try:
            as_of = date.fromisoformat(str(target_date))
        except ValueError:
            as_of = timezone.now().date()
    else:
        as_of = timezone.now().date()

    horizons = horizons or [3, 7, 30]
    asset = Asset.objects.filter(id=asset_id).first()
    if not asset:
        return f'Asset not found: {asset_id}'

    processed = _generate_predictions_for_single_asset(
        asset=asset,
        as_of=as_of,
        horizons=horizons,
        macro_phase=macro_phase,
        event_tag=event_tag,
    )
    return f'Predictions generated for asset_id={asset_id}, rows={processed}, date={as_of}'


def _first_repairable_gap(as_of, sources):
    """Oldest incomplete (date, source) in the lookback window, plus its universe.

    Date order matters: a week-old hole is what makes a backtest over that window
    wrong, so it is repaired before today's.
    """
    for trade_date in open_trading_dates(
        as_of,
        settings.PREDICTION_RECONCILE_LOOKBACK_TRADING_DAYS,
    ):
        universe = effective_universe_tradeable_assets(
            trade_date,
            context=f'Prediction reconciliation for {trade_date}',
        )
        if not universe:
            continue
        for gap in gaps_for_date(trade_date, sources=sources, universe=universe):
            if gap.missing_asset_ids:
                return gap, universe
    return None, []


@shared_task(soft_time_limit=1500, time_limit=1800)
def reconcile_prediction_coverage(target_date=None, sources=None, max_assets=None, horizons=None):
    """Write the stored predictions the scheduled sweeps did not.

    Covers both ways a trading date ends up short. Beat replays nothing: if the
    scheduler was down at 18:00, that occurrence is gone and the date stays empty
    forever. And a sweep that ran but hit its time budget leaves a date with a few
    assets instead of the universe -- which looks, to every reader of the table,
    like a day on which almost nothing was tradeable.

    One (date, source) per run, capped at `max_assets` assets. The cap is the
    point: at this cost a single job cannot close a universe-sized hole inside any
    time budget worth having, so the beat cadence decides how fast the backlog
    heals and each run reports what it left behind.
    """
    if target_date:
        try:
            as_of = date.fromisoformat(str(target_date))
        except ValueError:
            as_of = timezone.now().date()
    else:
        as_of = timezone.now().date()

    resolved_sources = tuple(sources) if sources else RECONCILE_SOURCES
    resolved_horizons = horizons or [3, 7, 30]
    resolved_max_assets = (
        settings.PREDICTION_RECONCILE_MAX_ASSETS_PER_RUN
        if max_assets is None
        else int(max_assets)
    )

    gap, universe = _first_repairable_gap(as_of, resolved_sources)
    if gap is None:
        return (
            f'Prediction reconciliation: complete through {as_of} '
            f'({settings.PREDICTION_RECONCILE_LOOKBACK_TRADING_DAYS} trading days looked back)'
        )
    if resolved_max_assets <= 0:
        return f'Prediction reconciliation disabled by max_assets=0; {gap.summary()}'

    assets_by_id = {asset.id: asset for asset in universe}

    def repair_asset(asset_id):
        if gap.source == ENSEMBLE:
            return _generate_predictions_for_single_asset(
                asset=assets_by_id[asset_id],
                as_of=gap.trade_date,
                horizons=resolved_horizons,
            )
        # Local import: tasks_lightgbm pulls numpy/pandas/lightgbm, and the ops
        # worker should not pay for that to serve the heuristic path.
        from apps.prediction.tasks_lightgbm import generate_lightgbm_prediction_for_asset

        return generate_lightgbm_prediction_for_asset(
            asset_id,
            gap.trade_date.isoformat(),
            resolved_horizons,
        )

    repaired = 0
    stopped_early = False
    for asset_id in gap.missing_asset_ids[:resolved_max_assets]:
        try:
            repair_asset(asset_id)
        except SoftTimeLimitExceeded:
            stopped_early = True
            break
        repaired += 1

    remaining = len(gap.missing_asset_ids) - repaired
    stopped_note = ', soft time limit reached' if stopped_early else ''
    if remaining <= 0:
        outcome = f'{gap.source} complete for {gap.trade_date}'
    else:
        outcome = f'{remaining} assets still missing for {gap.trade_date}, next run continues'

    if repaired:
        # The dashboard stocks payload is cached for a day under a key that does
        # not change when rows are added for the same factor date, so without
        # this the repaired rows stay invisible until the TTL lapses.
        from apps.analytics.tasks import prewarm_dashboard_stocks_cache

        prewarm_dashboard_stocks_cache.delay()

    return (
        f'Prediction reconciliation wrote {gap.source} rows for {repaired} assets'
        f'{stopped_note}; {outcome}'
    )
