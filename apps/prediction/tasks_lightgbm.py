import json
import os
import pickle
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
from celery import shared_task
from django.conf import settings
from django.utils import timezone

try:
    import lightgbm as lgb
    from sklearn.preprocessing import StandardScaler
    from sklearn.calibration import CalibratedClassifierCV
    LIGHTGBM_AVAILABLE = True
except ImportError:
    LIGHTGBM_AVAILABLE = False

from apps.analytics.models import TechnicalIndicator
from apps.factors.models import FactorScore
from apps.markets.models import Asset, OHLCV
from apps.macro.models import MacroSnapshot, MarketContext
from apps.sentiment.models import SentimentScore
from .models_lightgbm import LightGBMModelArtifact, LightGBMPrediction, EnsembleWeightSnapshot


# ============================================================================
# Model Persistence Utilities
# ============================================================================

MODELS_DIR = os.path.join(settings.BASE_DIR, 'models', 'lightgbm')
os.makedirs(MODELS_DIR, exist_ok=True)


def _get_model_path(horizon_days, version):
    return os.path.join(MODELS_DIR, f'{horizon_days}d_{version}')


def _save_model_artifacts(model_dict, horizon_days, version):
    """Save trained model, scaler, calibrator to disk."""
    path = _get_model_path(horizon_days, version)
    os.makedirs(path, exist_ok=True)

    with open(os.path.join(path, 'model.pkl'), 'wb') as f:
        pickle.dump(model_dict['model'], f)

    with open(os.path.join(path, 'scaler.pkl'), 'wb') as f:
        pickle.dump(model_dict['scaler'], f)

    with open(os.path.join(path, 'calibrator.pkl'), 'wb') as f:
        pickle.dump(model_dict['calibrator'], f)

    with open(os.path.join(path, 'metadata.json'), 'w') as f:
        json.dump({
            'horizon_days': horizon_days,
            'feature_names': model_dict['feature_names'],
            'trained_at': timezone.now().isoformat(),
        }, f, indent=2)


def _load_model_artifacts(horizon_days, version):
    """Load trained model, scaler, calibrator from disk."""
    path = _get_model_path(horizon_days, version)

    if not os.path.exists(path):
        return None

    try:
        with open(os.path.join(path, 'model.pkl'), 'rb') as f:
            model = pickle.load(f)
        with open(os.path.join(path, 'scaler.pkl'), 'rb') as f:
            scaler = pickle.load(f)
        with open(os.path.join(path, 'calibrator.pkl'), 'rb') as f:
            calibrator = pickle.load(f)
        with open(os.path.join(path, 'metadata.json'), 'r') as f:
            metadata = json.load(f)

        return {
            'model': model,
            'scaler': scaler,
            'calibrator': calibrator,
            'metadata': metadata,
        }
    except Exception as e:
        print(f'Error loading model artifacts: {e}')
        return None


# ============================================================================
# Feature Engineering
# ============================================================================

def _extract_features_for_asset(asset_id, as_of):
    """Extract all available features for a single asset on a given date."""
    features = {}

    # Phase 10: Technical Indicators
    rsi = TechnicalIndicator.objects.filter(
        asset_id=asset_id,
        indicator_type='RSI',
        timestamp__date__lte=as_of,
    ).order_by('-timestamp').first()
    features['rsi'] = float(getattr(rsi, 'value', 50)) if rsi else 50.0

    mom_5d = TechnicalIndicator.objects.filter(
        asset_id=asset_id,
        indicator_type='MOM_5D',
        timestamp__date__lte=as_of,
    ).order_by('-timestamp').first()
    features['mom_5d'] = float(getattr(mom_5d, 'value', 0)) if mom_5d else 0.0

    rs_score = TechnicalIndicator.objects.filter(
        asset_id=asset_id,
        indicator_type='RS_SCORE',
        timestamp__date__lte=as_of,
    ).order_by('-timestamp').first()
    features['rs_score'] = float(getattr(rs_score, 'value', 0.5)) if rs_score else 0.5

    # Phase 11: Multi-Factor Scores
    factor = FactorScore.objects.filter(
        asset_id=asset_id,
        date__lte=as_of,
        mode=FactorScore.FactorMode.COMPOSITE,
    ).order_by('-date').first()

    features['pe_percentile'] = float(getattr(factor, 'pe_percentile_score', 0.5)) if factor else 0.5
    features['pb_percentile'] = float(getattr(factor, 'pb_percentile_score', 0.5)) if factor else 0.5
    features['roe_trend'] = float(getattr(factor, 'roe_trend_score', 0.5)) if factor else 0.5
    features['northbound_flow'] = float(getattr(factor, 'northbound_flow_score', 0.5)) if factor else 0.5
    features['main_force_flow'] = float(getattr(factor, 'main_force_flow_score', 0.5)) if factor else 0.5
    features['margin_flow'] = float(getattr(factor, 'margin_flow_score', 0.5)) if factor else 0.5
    features['factor_composite'] = float(getattr(factor, 'composite_score', 0.5)) if factor else 0.5

    # Phase 12: Macro Context
    current_context = MarketContext.objects.filter(
        context_key='current',
        is_active=True,
        starts_at__lte=as_of,
    ).order_by('-starts_at').first()

    phase_to_int = {'RECOVERY': 1, 'OVERHEAT': 2, 'STAGFLATION': 3, 'RECESSION': 0}
    features['macro_phase'] = float(phase_to_int.get(getattr(current_context, 'macro_phase', 'RECOVERY'), 1))

    macro_snap = MacroSnapshot.objects.filter(date__lte=as_of).order_by('-date').first()
    features['pmi_manufacturing'] = float(getattr(macro_snap, 'pmi_manufacturing', 50)) if macro_snap else 50.0
    features['pmi_non_manufacturing'] = float(getattr(macro_snap, 'pmi_non_manufacturing', 50)) if macro_snap else 50.0
    features['yield_curve'] = float(
        (getattr(macro_snap, 'cn10y_yield', 2.0) - getattr(macro_snap, 'cn2y_yield', 2.0)) if macro_snap else 0.0
    )

    # Phase 13: Sentiment
    sentiment = SentimentScore.objects.filter(
        asset_id=asset_id,
        date__lte=as_of,
        score_type=SentimentScore.ScoreType.ASSET_7D,
    ).order_by('-date').first()
    features['sentiment_7d'] = float(getattr(sentiment, 'sentiment_score', 0)) if sentiment else 0.0

    return features


def _create_feature_matrix(start_date, end_date, asset_ids=None):
    """
    Create a feature matrix for all assets over a date range.
    Used for training.
    
    Returns:
        DataFrame with shape (n_samples, n_features)
        Index: (date, asset_id)
    """
    import pandas as pd

    if asset_ids is None:
        asset_ids = list(Asset.objects.values_list('id', flat=True))

    all_dates = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    rows = []

    for target_date in all_dates:
        for asset_id in asset_ids:
            features = _extract_features_for_asset(asset_id, target_date)
            features['date'] = target_date
            features['asset_id'] = asset_id
            rows.append(features)

    df = pd.DataFrame(rows)
    return df


def _create_labels_for_training(start_date, end_date, horizon_days):
    """
    Create direction labels for training based on future returns.
    
    UP:   forward_return >= +2%
    DOWN: forward_return <= -2%
    FLAT: -2% < return < +2%
    """
    import pandas as pd

    labels = {}

    for asset in Asset.objects.all():
        asset_ohlcv = OHLCV.objects.filter(
            asset=asset,
            date__gte=start_date,
            date__lte=end_date + timedelta(days=horizon_days),
        ).order_by('date').values('date', 'close')

        price_dict = {row['date']: float(row['close']) for row in asset_ohlcv}

        for target_date in pd.date_range(start_date, end_date):
            target_date = target_date.date()
            future_date = target_date + timedelta(days=horizon_days)

            if target_date not in price_dict or future_date not in price_dict:
                continue

            start_price = price_dict[target_date]
            end_price = price_dict[future_date]
            forward_return = (end_price - start_price) / start_price

            if forward_return >= 0.02:
                label = 'UP'
            elif forward_return <= -0.02:
                label = 'DOWN'
            else:
                label = 'FLAT'

            key = (target_date, asset.id, horizon_days)
            labels[key] = label

    return labels


# ============================================================================
# LightGBM Training
# ============================================================================

@shared_task
def train_lightgbm_models(training_start_date=None, training_end_date=None):
    """
    Train LightGBM models for 3, 7, 30-day horizons.
    Runs weekly via Celery Beat.
    """
    if not LIGHTGBM_AVAILABLE:
        return 'LightGBM not installed. Skipping training.'

    if training_end_date:
        try:
            training_end = date.fromisoformat(str(training_end_date))
        except ValueError:
            training_end = timezone.now().date() - timedelta(days=1)
    else:
        training_end = timezone.now().date() - timedelta(days=1)

    if training_start_date:
        try:
            training_start = date.fromisoformat(str(training_start_date))
        except ValueError:
            training_start = training_end - timedelta(days=365 * 5)
    else:
        training_start = training_end - timedelta(days=365 * 5)

    print(f'Training LightGBM models from {training_start} to {training_end}')

    # Create feature and label matrices
    import pandas as pd

    X_df = _create_feature_matrix(training_start, training_end)
    feature_names = [col for col in X_df.columns if col not in ['date', 'asset_id']]

    results = {}

    for horizon in [3, 7, 30]:
        print(f'Training model for {horizon}-day horizon...')

        # Create labels
        labels_dict = _create_labels_for_training(training_start, training_end, horizon)
        y_series = pd.Series(labels_dict)

        # Align data
        X_train_aligned = X_df.copy()
        X_train_aligned['label'] = X_train_aligned.apply(
            lambda row: labels_dict.get((row['date'], row['asset_id'], horizon), None),
            axis=1
        )
        X_train_aligned = X_train_aligned.dropna(subset=['label'])

        if len(X_train_aligned) < 100:
            print(f'Insufficient training data for {horizon}-day horizon (n={len(X_train_aligned)})')
            continue

        X_train = X_train_aligned[feature_names].values
        y_train = X_train_aligned['label'].map({'DOWN': 0, 'FLAT': 1, 'UP': 2}).values

        # Standardize features
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)

        # Train LightGBM
        lgb_params = {
            'objective': 'multiclass',
            'num_class': 3,
            'num_leaves': 31,
            'learning_rate': 0.05,
            'feature_fraction': 0.8,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'lambda_l1': 0.1,
            'lambda_l2': 0.1,
            'min_data_in_leaf': 20,
            'random_state': 42,
            'verbose': -1,
        }

        train_data = lgb.Dataset(X_train_scaled, label=y_train)
        model = lgb.train(lgb_params, train_data, num_boost_round=200)

        # Calibrate probabilities
        calibrator = CalibratedClassifierCV(model, method='sigmoid', cv=5)
        calibrator.fit(X_train_scaled, y_train)

        # Calculate metrics
        y_pred_proba = calibrator.predict_proba(X_train_scaled)
        y_pred = np.argmax(y_pred_proba, axis=1)
        accuracy = np.mean(y_pred == y_train)

        # Get feature importance
        importance = model.feature_importance('gain')
        top_features = sorted(
            [(feature_names[i], float(importance[i])) for i in range(len(feature_names))],
            key=lambda x: x[1],
            reverse=True
        )[:10]

        # Save artifacts
        model_version = f'lgb-{horizon}d-{training_end.isoformat()}'
        artifacts = {
            'model': model,
            'scaler': scaler,
            'calibrator': calibrator,
            'feature_names': feature_names,
        }
        _save_model_artifacts(artifacts, horizon, model_version)

        # Register in database
        if LightGBMModelArtifact.objects.filter(horizon_days=horizon, is_active=True).exists():
            LightGBMModelArtifact.objects.filter(horizon_days=horizon, is_active=True).update(is_active=False)

        artifact = LightGBMModelArtifact.objects.create(
            horizon_days=horizon,
            version=model_version,
            status=LightGBMModelArtifact.Status.READY,
            artifact_path=_get_model_path(horizon, model_version),
            metrics_json={
                'accuracy': float(accuracy),
                'training_samples': len(X_train),
                'feature_count': len(feature_names),
            },
            feature_names=feature_names,
            training_window_start=training_start,
            training_window_end=training_end,
            trained_at=timezone.now(),
            is_active=True,
            feature_importance=dict(top_features),
        )

        results[horizon] = {'status': 'success', 'accuracy': accuracy, 'artifact_id': artifact.id}
        print(f'Trained {horizon}-day model: accuracy={accuracy:.4f}')

    return results


# ============================================================================
# LightGBM Inference
# ============================================================================

def _predict_with_lightgbm(asset_id, target_date, horizon_days):
    """Generate a single LightGBM prediction for an asset on a date."""
    # Load active model
    model_artifact = LightGBMModelArtifact.objects.filter(
        horizon_days=horizon_days,
        is_active=True,
        status=LightGBMModelArtifact.Status.READY,
    ).order_by('-trained_at').first()

    if not model_artifact:
        return None

    # Load artifacts from disk
    artifacts = _load_model_artifacts(horizon_days, model_artifact.version)
    if not artifacts:
        return None

    # Extract features
    features_dict = _extract_features_for_asset(asset_id, target_date)
    feature_names = artifacts['metadata']['feature_names']
    X = np.array([features_dict.get(name, 0.0) for name in feature_names]).reshape(1, -1)

    # Scale
    X_scaled = artifacts['scaler'].transform(X)

    # Predict
    raw_probs = artifacts['model'].predict(X_scaled)[0]
    calibrated_probs = artifacts['calibrator'].predict_proba(X_scaled)[0]

    down_prob, flat_prob, up_prob = calibrated_probs
    confidence = max(calibrated_probs)
    label_idx = np.argmax(calibrated_probs)
    predicted_label = ['DOWN', 'FLAT', 'UP'][label_idx]

    return {
        'up_probability': Decimal(str(up_prob)),
        'flat_probability': Decimal(str(flat_prob)),
        'down_probability': Decimal(str(down_prob)),
        'confidence': Decimal(str(confidence)),
        'predicted_label': predicted_label,
        'feature_snapshot': features_dict,
        'raw_scores': {'down': float(raw_probs[0]), 'flat': float(raw_probs[1]), 'up': float(raw_probs[2])},
        'calibrated_scores': {'down': float(down_prob), 'flat': float(flat_prob), 'up': float(up_prob)},
        'model_artifact': model_artifact,
    }


@shared_task
def generate_lightgbm_predictions_for_date(target_date=None, horizons=None):
    """Generate LightGBM predictions for all assets on a given date."""
    if target_date:
        try:
            as_of = date.fromisoformat(str(target_date))
        except ValueError:
            as_of = timezone.now().date()
    else:
        as_of = timezone.now().date()

    horizons = horizons or [3, 7, 30]
    processed = 0

    for asset in Asset.objects.all():
        for horizon in horizons:
            pred = _predict_with_lightgbm(asset.id, as_of, horizon)

            if pred is None:
                continue

            LightGBMPrediction.objects.update_or_create(
                asset=asset,
                date=as_of,
                horizon_days=horizon,
                model_artifact=pred['model_artifact'],
                defaults={
                    'up_probability': pred['up_probability'],
                    'flat_probability': pred['flat_probability'],
                    'down_probability': pred['down_probability'],
                    'confidence': pred['confidence'],
                    'predicted_label': pred['predicted_label'],
                    'feature_snapshot': pred['feature_snapshot'],
                    'raw_scores': pred['raw_scores'],
                    'calibrated_scores': pred['calibrated_scores'],
                },
            )
            processed += 1

    return f'LightGBM predictions generated for {processed} asset-horizon pairs on {as_of}'


@shared_task
def generate_lightgbm_prediction_for_asset(asset_id, target_date=None, horizons=None):
    """Generate LightGBM predictions for a single asset."""
    if target_date:
        try:
            as_of = date.fromisoformat(str(target_date))
        except ValueError:
            as_of = timezone.now().date()
    else:
        as_of = timezone.now().date()

    horizons = horizons or [3, 7, 30]
    processed = 0

    try:
        asset = Asset.objects.get(id=asset_id)
    except Asset.DoesNotExist:
        return f'Asset not found: {asset_id}'

    for horizon in horizons:
        pred = _predict_with_lightgbm(asset_id, as_of, horizon)

        if pred is None:
            continue

        LightGBMPrediction.objects.update_or_create(
            asset=asset,
            date=as_of,
            horizon_days=horizon,
            model_artifact=pred['model_artifact'],
            defaults={
                'up_probability': pred['up_probability'],
                'flat_probability': pred['flat_probability'],
                'down_probability': pred['down_probability'],
                'confidence': pred['confidence'],
                'predicted_label': pred['predicted_label'],
                'feature_snapshot': pred['feature_snapshot'],
                'raw_scores': pred['raw_scores'],
                'calibrated_scores': pred['calibrated_scores'],
            },
        )
        processed += 1

    return f'LightGBM predictions generated for asset {asset.symbol}, {processed} horizons, date {as_of}'
