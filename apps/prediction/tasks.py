from datetime import date
from decimal import Decimal

from celery import shared_task
from django.utils import timezone

from apps.analytics.models import TechnicalIndicator
from apps.factors.models import FactorScore
from apps.markets.models import Asset
from apps.macro.models import MarketContext
from apps.prediction.historical_features import latest_momentum, latest_rs_score, latest_rsi
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


def _resolve_context(explicit_macro_phase=None, explicit_event_tag=None):
    if explicit_macro_phase or explicit_event_tag:
        return explicit_macro_phase or '', explicit_event_tag or ''

    current = MarketContext.objects.filter(context_key='current', is_active=True).order_by('-starts_at', '-updated_at').first()
    if not current:
        return '', ''
    return current.macro_phase, current.event_tag or ''


def _feature_snapshot(asset_id, as_of):
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

    rsi = latest_rsi(asset_id, as_of, default=Decimal('50'))
    mom_5d = latest_momentum(asset_id, as_of, n_days=5, default=Decimal('0'))
    rs_score = latest_rs_score(asset_id, as_of, default=Decimal('0.5'))

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

    for model_type in [ModelVersion.ModelType.LIGHTGBM, ModelVersion.ModelType.LSTM, ModelVersion.ModelType.ENSEMBLE]:
        ModelVersion.objects.filter(model_type=model_type, is_active=True).update(is_active=False)
        ModelVersion.objects.create(
            model_type=model_type,
            version=f'{model_type.lower()}-{as_of.isoformat()}',
            status=ModelVersion.Status.READY,
            artifact_path=f'models/{model_type.lower()}/{as_of.isoformat()}.bin',
            metrics={'accuracy': 0.5, 'f1_macro': 0.5},
            feature_schema=['phase10', 'phase11', 'phase12', 'phase13'],
            trained_at=timezone.now(),
            training_window_start=as_of.replace(year=max(2000, as_of.year - 5)),
            training_window_end=as_of,
            is_active=True,
            metadata={'source': 'phase14_training_stub'},
        )

    return f'Phase 14 model versions refreshed for {as_of}'


@shared_task
def generate_predictions_for_date(target_date=None, horizons=None, macro_phase=None, event_tag=None):
    if target_date:
        try:
            as_of = date.fromisoformat(str(target_date))
        except ValueError:
            as_of = timezone.now().date()
    else:
        as_of = timezone.now().date()

    horizons = horizons or [3, 7, 30]
    ctx_macro, ctx_event = _resolve_context(macro_phase, event_tag)
    version = _ensure_active_ensemble_version(as_of)

    processed = 0
    for asset in Asset.objects.all():
        features = _feature_snapshot(asset.id, as_of)
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
    ctx_macro, ctx_event = _resolve_context(macro_phase, event_tag)
    version = _ensure_active_ensemble_version(as_of)

    processed = 0
    features = _feature_snapshot(asset.id, as_of)
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
