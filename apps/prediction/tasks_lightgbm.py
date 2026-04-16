import json
import os
import pickle
from datetime import date, timedelta
from decimal import Decimal
from statistics import pstdev

import numpy as np
from celery import shared_task
from django.conf import settings
from django.db.models import Avg
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
from .models import ModelVersion
from .models_lightgbm import LightGBMModelArtifact, LightGBMPrediction, EnsembleWeightSnapshot, FeatureImportanceSnapshot
from .odds import estimate_trade_decision


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

LAG_WINDOWS = (3, 5, 10)


class IdentityCalibrator:
    """Fallback calibrator for LightGBM boosters that already emit class probabilities."""

    def __init__(self, model):
        self.model = model

    def fit(self, matrix, labels=None):
        return self

    def predict_proba(self, matrix):
        return np.asarray(self.model.predict(matrix))


def _safe_float(value, default=0.0):
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _indicator_value(asset_id, indicator_type, as_of, days_ago=0, default=0.0):
    target_date = as_of - timedelta(days=days_ago)
    indicator = TechnicalIndicator.objects.filter(
        asset_id=asset_id,
        indicator_type=indicator_type,
        timestamp__date__lte=target_date,
    ).order_by('-timestamp').first()
    return _safe_float(getattr(indicator, 'value', default), default)


def _get_recent_ohlcv_rows(asset_id, as_of, limit=30):
    return list(
        OHLCV.objects.filter(asset_id=asset_id, date__lte=as_of)
        .order_by('-date')
        .values('date', 'close', 'volume')[:limit]
    )


def _row_value(rows, index, key, default):
    if index >= len(rows):
        return float(default)
    return _safe_float(rows[index].get(key), default)


def _compute_return(rows, periods, default=0.0):
    if len(rows) <= periods:
        return float(default)
    current_close = _row_value(rows, 0, 'close', 0.0)
    historical_close = _row_value(rows, periods, 'close', 0.0)
    if not historical_close:
        return float(default)
    return (current_close - historical_close) / historical_close


def _compute_realized_volatility(rows, window=5):
    if len(rows) <= window:
        return 0.0
    closes = [_row_value(rows, idx, 'close', 0.0) for idx in range(window + 1)]
    closes = list(reversed([close for close in closes if close]))
    if len(closes) <= 1:
        return 0.0
    returns = []
    for previous_close, current_close in zip(closes, closes[1:]):
        if previous_close:
            returns.append((current_close - previous_close) / previous_close)
    if len(returns) <= 1:
        return 0.0
    return float(pstdev(returns))


def _build_interaction_features(df, feature_names):
    interaction_specs = [
        ('rsi_x_relative_volume_5d', 'rsi', 'relative_volume_5d'),
        ('rsi_x_macro_phase', 'rsi', 'macro_phase'),
        ('factor_composite_x_sentiment', 'factor_composite', 'sentiment_7d'),
        ('northbound_flow_x_mom_5d', 'northbound_flow', 'mom_5d'),
        ('pe_percentile_x_macro_phase', 'pe_percentile', 'macro_phase'),
    ]
    created = []
    for output_name, left_name, right_name in interaction_specs:
        if left_name in df.columns and right_name in df.columns:
            df[output_name] = df[left_name] * df[right_name]
            created.append(output_name)
    return df, feature_names + created


def _get_recent_feature_artifacts(horizon_days, lookback_runs=3):
    artifacts = list(
        LightGBMModelArtifact.objects.filter(status=LightGBMModelArtifact.Status.READY, horizon_days=horizon_days)
        .order_by('-trained_at')[:lookback_runs]
    )
    if len(artifacts) < 2:
        return []
    return artifacts


def _get_pruned_feature_names(horizon_days, feature_names):
    artifacts = _get_recent_feature_artifacts(horizon_days)
    if not artifacts:
        return []

    snapshots = FeatureImportanceSnapshot.objects.filter(model_artifact__in=artifacts)
    if not snapshots.exists():
        return []

    grouped = {}
    for snapshot in snapshots:
        grouped.setdefault(snapshot.feature_name, []).append(snapshot.importance_score)

    pruned = []
    required_history = len(artifacts)
    for feature_name in feature_names:
        scores = grouped.get(feature_name, [])
        if len(scores) < required_history:
            continue
        average_importance = sum(scores) / len(scores)
        if average_importance <= 0.01:
            pruned.append(feature_name)

    max_pruned = max(1, len(feature_names) // 5)
    return sorted(pruned)[:max_pruned]


def _register_lightgbm_model_version(horizon_days, model_version, training_start, training_end, metrics, feature_names, metadata):
    ModelVersion.objects.filter(model_type=ModelVersion.ModelType.LIGHTGBM, is_active=True).update(is_active=False)
    return ModelVersion.objects.create(
        model_type=ModelVersion.ModelType.LIGHTGBM,
        version=model_version,
        status=ModelVersion.Status.READY,
        artifact_path=_get_model_path(horizon_days, model_version),
        metrics=metrics,
        feature_schema=feature_names,
        training_window_start=training_start,
        training_window_end=training_end,
        trained_at=timezone.now(),
        is_active=True,
        metadata=metadata,
    )


def _store_feature_importance_snapshots(artifact, feature_names, importance_values):
    ordered = sorted(
        [(feature_name, float(importance_score)) for feature_name, importance_score in zip(feature_names, importance_values)],
        key=lambda item: item[1],
        reverse=True,
    )
    FeatureImportanceSnapshot.objects.bulk_create([
        FeatureImportanceSnapshot(
            model_artifact=artifact,
            horizon_days=artifact.horizon_days,
            feature_name=feature_name,
            importance_score=importance_score,
            importance_rank=index + 1,
        )
        for index, (feature_name, importance_score) in enumerate(ordered)
    ])
    return ordered


def _refresh_ensemble_weights(snapshot_date, lightgbm_results):
    heuristic_version = ModelVersion.objects.filter(
        model_type=ModelVersion.ModelType.ENSEMBLE,
        is_active=True,
    ).order_by('-trained_at').first()
    lstm_version = ModelVersion.objects.filter(
        model_type=ModelVersion.ModelType.LSTM,
        is_active=True,
    ).order_by('-trained_at').first()

    lightgbm_accuracy = np.mean(
        [result['accuracy'] for result in lightgbm_results.values() if result.get('status') == 'success']
    ) if lightgbm_results else 0.0
    heuristic_accuracy = _safe_float((heuristic_version.metrics if heuristic_version else {}).get('accuracy'), 0.5)
    lstm_accuracy = _safe_float((lstm_version.metrics if lstm_version else {}).get('accuracy'), 0.0)

    total = lightgbm_accuracy + heuristic_accuracy + lstm_accuracy
    if total <= 0:
        weights = (Decimal('0.3333'), Decimal('0.3333'), Decimal('0.3334'))
    else:
        weights = (
            Decimal(str(lightgbm_accuracy / total)).quantize(Decimal('0.0001')),
            Decimal(str(lstm_accuracy / total)).quantize(Decimal('0.0001')),
            Decimal(str(heuristic_accuracy / total)).quantize(Decimal('0.0001')),
        )

    EnsembleWeightSnapshot.objects.update_or_create(
        date=snapshot_date,
        defaults={
            'lightgbm_weight': weights[0],
            'lstm_weight': weights[1],
            'heuristic_weight': weights[2],
            'basis_lookback_days': 60,
            'basis_metrics': {
                'lightgbm_accuracy': lightgbm_accuracy,
                'heuristic_accuracy': heuristic_accuracy,
                'lstm_accuracy': lstm_accuracy,
            },
        },
    )

def _extract_features_for_asset(asset_id, as_of):
    """Extract all available features for a single asset on a given date."""
    features = {}
    recent_rows = _get_recent_ohlcv_rows(asset_id, as_of, limit=25)

    # Phase 10: Technical Indicators
    features['rsi'] = _indicator_value(asset_id, 'RSI', as_of, default=50.0)
    features['mom_5d'] = _indicator_value(asset_id, 'MOM_5D', as_of, default=0.0)
    features['rs_score'] = _indicator_value(asset_id, 'RS_SCORE', as_of, default=0.5)

    for lag_window in LAG_WINDOWS:
        lagged_rsi = _indicator_value(asset_id, 'RSI', as_of, days_ago=lag_window, default=features['rsi'])
        lagged_momentum = _indicator_value(asset_id, 'MOM_5D', as_of, days_ago=lag_window, default=features['mom_5d'])
        lagged_rs = _indicator_value(asset_id, 'RS_SCORE', as_of, days_ago=lag_window, default=features['rs_score'])
        features[f'rsi_lag_{lag_window}d'] = lagged_rsi
        features[f'rsi_delta_{lag_window}d'] = features['rsi'] - lagged_rsi
        features[f'mom_5d_delta_{lag_window}d'] = features['mom_5d'] - lagged_momentum
        features[f'rs_score_delta_{lag_window}d'] = features['rs_score'] - lagged_rs

    features['return_3d'] = _compute_return(recent_rows, 3)
    features['return_5d'] = _compute_return(recent_rows, 5)
    features['return_10d'] = _compute_return(recent_rows, 10)
    current_volume = _row_value(recent_rows, 0, 'volume', 0.0)
    volume_samples_5 = [_row_value(recent_rows, index, 'volume', 0.0) for index in range(min(5, len(recent_rows)))]
    volume_samples_20 = [_row_value(recent_rows, index, 'volume', 0.0) for index in range(min(20, len(recent_rows)))]
    average_volume_5 = float(np.mean(volume_samples_5)) if volume_samples_5 else 0.0
    average_volume_20 = float(np.mean(volume_samples_20)) if volume_samples_20 else 0.0
    features['relative_volume_5d'] = current_volume / average_volume_5 if average_volume_5 else 1.0
    features['relative_volume_20d'] = current_volume / average_volume_20 if average_volume_20 else 1.0
    features['realized_volatility_5d'] = _compute_realized_volatility(recent_rows, window=5)

    # Phase 11: Multi-Factor Scores
    factor = FactorScore.objects.filter(
        asset_id=asset_id,
        date__lte=as_of,
        mode=FactorScore.FactorMode.COMPOSITE,
    ).order_by('-date').first()

    features['pe_percentile'] = _safe_float(getattr(factor, 'pe_percentile_score', 0.5), 0.5) if factor else 0.5
    features['pb_percentile'] = _safe_float(getattr(factor, 'pb_percentile_score', 0.5), 0.5) if factor else 0.5
    features['roe_trend'] = _safe_float(getattr(factor, 'roe_trend_score', 0.5), 0.5) if factor else 0.5
    features['northbound_flow'] = _safe_float(getattr(factor, 'northbound_flow_score', 0.5), 0.5) if factor else 0.5
    features['main_force_flow'] = _safe_float(getattr(factor, 'main_force_flow_score', 0.5), 0.5) if factor else 0.5
    features['margin_flow'] = _safe_float(getattr(factor, 'margin_flow_score', 0.5), 0.5) if factor else 0.5
    features['factor_composite'] = _safe_float(getattr(factor, 'composite_score', 0.5), 0.5) if factor else 0.5

    # Phase 12: Macro Context
    current_context = MarketContext.objects.filter(
        context_key='current',
        is_active=True,
        starts_at__lte=as_of,
    ).order_by('-starts_at').first()

    phase_to_int = {'RECOVERY': 1, 'OVERHEAT': 2, 'STAGFLATION': 3, 'RECESSION': 0}
    features['macro_phase'] = float(phase_to_int.get(getattr(current_context, 'macro_phase', 'RECOVERY'), 1))

    macro_snap = MacroSnapshot.objects.filter(date__lte=as_of).order_by('-date').first()
    features['pmi_manufacturing'] = _safe_float(getattr(macro_snap, 'pmi_manufacturing', 50), 50.0) if macro_snap else 50.0
    features['pmi_non_manufacturing'] = _safe_float(getattr(macro_snap, 'pmi_non_manufacturing', 50), 50.0) if macro_snap else 50.0
    features['yield_curve'] = (
        _safe_float(getattr(macro_snap, 'cn10y_yield', 2.0), 2.0) -
        _safe_float(getattr(macro_snap, 'cn2y_yield', 2.0), 2.0)
    ) if macro_snap else 0.0

    # Phase 13: Sentiment
    sentiment = SentimentScore.objects.filter(
        asset_id=asset_id,
        date__lte=as_of,
        score_type=SentimentScore.ScoreType.ASSET_7D,
    ).order_by('-date').first()
    features['sentiment_7d'] = _safe_float(getattr(sentiment, 'sentiment_score', 0), 0.0) if sentiment else 0.0
    sentiment_window = SentimentScore.objects.filter(
        asset_id=asset_id,
        date__lte=as_of,
        date__gte=as_of - timedelta(days=20),
        score_type=SentimentScore.ScoreType.ASSET_7D,
    )
    features['sentiment_7d_avg_20d'] = _safe_float(sentiment_window.aggregate(avg=Avg('sentiment_score'))['avg'], 0.0)

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
    base_feature_names = [col for col in df.columns if col not in ['date', 'asset_id']]
    df, feature_names = _build_interaction_features(df, base_feature_names)
    df.attrs['feature_names'] = feature_names
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
    feature_names = list(X_df.attrs.get('feature_names') or [col for col in X_df.columns if col not in ['date', 'asset_id']])

    results = {}

    for horizon in [3, 7, 30]:
        print(f'Training model for {horizon}-day horizon...')
        pruned_feature_names = _get_pruned_feature_names(horizon, feature_names)
        selected_feature_names = [name for name in feature_names if name not in pruned_feature_names]

        # Create labels
        labels_dict = _create_labels_for_training(training_start, training_end, horizon)

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

        X_train = X_train_aligned[selected_feature_names].values
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

        # Calibrate probabilities when the model exposes a scikit-learn style API.
        if hasattr(model, 'fit') and (hasattr(model, 'predict_proba') or hasattr(model, 'decision_function')):
            calibrator = CalibratedClassifierCV(model, method='sigmoid', cv=5)
            calibrator.fit(X_train_scaled, y_train)
            calibration_method = 'sigmoid'
        else:
            calibrator = IdentityCalibrator(model)
            calibration_method = 'identity'

        # Calculate metrics
        y_pred_proba = calibrator.predict_proba(X_train_scaled)
        y_pred = np.argmax(y_pred_proba, axis=1)
        accuracy = np.mean(y_pred == y_train)

        # Get feature importance
        importance = model.feature_importance('gain')
        top_features = sorted(
            [(feature_name, float(importance_score)) for feature_name, importance_score in zip(selected_feature_names, importance)],
            key=lambda x: x[1],
            reverse=True
        )[:10]

        # Save artifacts
        model_version = f'lgb-{horizon}d-{training_end.isoformat()}'
        artifacts = {
            'model': model,
            'scaler': scaler,
            'calibrator': calibrator,
            'feature_names': selected_feature_names,
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
                'feature_count': len(selected_feature_names),
            },
            feature_names=selected_feature_names,
            training_window_start=training_start,
            training_window_end=training_end,
            trained_at=timezone.now(),
            is_active=True,
            feature_importance=dict(top_features),
            metadata={
                'engineered_feature_version': 'v2',
                'calibration_method': calibration_method,
                'interaction_features': [name for name in selected_feature_names if '_x_' in name],
                'pruned_features': pruned_feature_names,
                'feature_schema_size': len(selected_feature_names),
            },
        )

        _store_feature_importance_snapshots(artifact, selected_feature_names, importance)
        _register_lightgbm_model_version(
            horizon_days=horizon,
            model_version=model_version,
            training_start=training_start,
            training_end=training_end,
            metrics={
                'accuracy': float(accuracy),
                'training_samples': len(X_train),
                'feature_count': len(selected_feature_names),
            },
            feature_names=selected_feature_names,
            metadata={
                'artifact_id': artifact.id,
                'engineered_feature_version': 'v2',
                'calibration_method': calibration_method,
                'pruned_features': pruned_feature_names,
                'top_features': dict(top_features),
            },
        )

        results[horizon] = {'status': 'success', 'accuracy': accuracy, 'artifact_id': artifact.id}
        print(f'Trained {horizon}-day model: accuracy={accuracy:.4f}')

    _refresh_ensemble_weights(training_end, results)

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
    trade_decision = estimate_trade_decision(
        asset_id=asset_id,
        as_of=target_date,
        horizon_days=horizon_days,
        up_probability=Decimal(str(up_prob)),
        predicted_label=predicted_label,
    )

    return {
        'up_probability': Decimal(str(up_prob)),
        'flat_probability': Decimal(str(flat_prob)),
        'down_probability': Decimal(str(down_prob)),
        'confidence': Decimal(str(confidence)),
        'predicted_label': predicted_label,
        'target_price': trade_decision['target_price'],
        'stop_loss_price': trade_decision['stop_loss_price'],
        'risk_reward_ratio': trade_decision['risk_reward_ratio'],
        'trade_score': trade_decision['trade_score'],
        'suggested': trade_decision['suggested'],
        'feature_snapshot': features_dict,
        'raw_scores': {'down': float(raw_probs[0]), 'flat': float(raw_probs[1]), 'up': float(raw_probs[2])},
        'calibrated_scores': {'down': float(down_prob), 'flat': float(flat_prob), 'up': float(up_prob)},
        'model_artifact': model_artifact,
        'metadata': {
            'source': 'phase14_lightgbm_prediction',
            'trade_decision_engine': 'v1',
        },
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
                    'target_price': pred['target_price'],
                    'stop_loss_price': pred['stop_loss_price'],
                    'risk_reward_ratio': pred['risk_reward_ratio'],
                    'trade_score': pred['trade_score'],
                    'suggested': pred['suggested'],
                    'feature_snapshot': pred['feature_snapshot'],
                    'raw_scores': pred['raw_scores'],
                    'calibrated_scores': pred['calibrated_scores'],
                    'metadata': pred['metadata'],
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
                'target_price': pred['target_price'],
                'stop_loss_price': pred['stop_loss_price'],
                'risk_reward_ratio': pred['risk_reward_ratio'],
                'trade_score': pred['trade_score'],
                'suggested': pred['suggested'],
                'feature_snapshot': pred['feature_snapshot'],
                'raw_scores': pred['raw_scores'],
                'calibrated_scores': pred['calibrated_scores'],
                'metadata': pred['metadata'],
            },
        )
        processed += 1

    return f'LightGBM predictions generated for asset {asset.symbol}, {processed} horizons, date {as_of}'
