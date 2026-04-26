import json
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
import torch
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from sklearn.preprocessing import StandardScaler
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from .models import ModelVersion, PredictionResult
from .models_lightgbm import LightGBMModelArtifact
from apps.markets.models import Asset
from .tasks_lightgbm import _create_feature_matrix, _create_labels_for_training, _refresh_ensemble_weights
from .odds import estimate_trade_decision


LSTM_MODELS_DIR = os.path.join(settings.BASE_DIR, 'models', 'lstm')
os.makedirs(LSTM_MODELS_DIR, exist_ok=True)


class LSTMClassifier(nn.Module):
    def __init__(self, input_size, hidden_size=64, num_layers=2, dropout=0.2, num_classes=3):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0.0,
            batch_first=True,
        )
        self.classifier = nn.Sequential(
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size, num_classes),
        )

    def forward(self, x):
        output, _ = self.lstm(x)
        last_hidden = output[:, -1, :]
        return self.classifier(last_hidden)


def _parse_training_window(training_start_date=None, training_end_date=None):
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
            floor_raw = getattr(settings, 'HISTORICAL_DATA_FLOOR', '2000-01-01')
            try:
                training_start = date.fromisoformat(str(floor_raw))
            except ValueError:
                training_start = training_end - timedelta(days=365 * 5)
    else:
        floor_raw = getattr(settings, 'HISTORICAL_DATA_FLOOR', '2000-01-01')
        try:
            training_start = date.fromisoformat(str(floor_raw))
        except ValueError:
            training_start = training_end - timedelta(days=365 * 5)

    return training_start, training_end


def _build_sequences(feature_df, labels_dict, horizon_days, feature_names, sequence_length):
    if feature_df.empty:
        return np.empty((0, sequence_length, len(feature_names)), dtype=np.float32), np.empty((0,), dtype=np.int64), []

    label_map = {'DOWN': 0, 'FLAT': 1, 'UP': 2}
    sequences = []
    targets = []
    sample_dates = []

    working_df = feature_df.sort_values(['asset_id', 'date']).copy()
    working_df['date'] = pd.to_datetime(working_df['date'])

    for asset_id, group in working_df.groupby('asset_id', sort=False):
        group = group.sort_values('date').reset_index(drop=True)
        if len(group) < sequence_length:
            continue

        values = group[feature_names].astype(np.float32).to_numpy()
        dates = group['date'].dt.date.to_list()

        for index in range(sequence_length - 1, len(group)):
            target_date = dates[index]
            raw_label = labels_dict.get((target_date, int(asset_id), int(horizon_days)))
            if raw_label not in label_map:
                continue
            sequences.append(values[index - sequence_length + 1:index + 1])
            targets.append(label_map[raw_label])
            sample_dates.append(target_date)

    if not sequences:
        return np.empty((0, sequence_length, len(feature_names)), dtype=np.float32), np.empty((0,), dtype=np.int64), []

    return np.asarray(sequences, dtype=np.float32), np.asarray(targets, dtype=np.int64), sample_dates


def _fit_scaler_on_sequences(X_train):
    scaler = StandardScaler()
    flat = X_train.reshape(-1, X_train.shape[-1])
    scaler.fit(flat)
    return scaler


def _transform_sequences(X, scaler):
    flat = X.reshape(-1, X.shape[-1])
    transformed = scaler.transform(flat)
    return transformed.reshape(X.shape).astype(np.float32)


def _iter_chunks(items, chunk_size):
    for index in range(0, len(items), chunk_size):
        yield items[index:index + chunk_size]


def _prediction_label_from_index(index):
    if index == 2:
        return PredictionResult.Label.UP
    if index == 0:
        return PredictionResult.Label.DOWN
    return PredictionResult.Label.FLAT


def _resolve_lstm_model_version(target_date):
    version = ModelVersion.objects.filter(
        model_type=ModelVersion.ModelType.LSTM,
        is_active=True,
        status=ModelVersion.Status.READY,
    ).order_by('-trained_at', '-created_at').first()
    if version:
        return version

    fallback = ModelVersion.objects.filter(
        model_type=ModelVersion.ModelType.LSTM,
        status=ModelVersion.Status.READY,
    ).order_by('-trained_at', '-created_at').first()
    if fallback:
        return fallback

    raise ValueError(f'No READY LSTM model version available for inference on {target_date}.')


def _load_lstm_artifact(model_version, horizon_days):
    model_dir = model_version.artifact_path or os.path.join(LSTM_MODELS_DIR, model_version.version)
    file_path = os.path.join(model_dir, f'{int(horizon_days)}d_model.pt')
    if not os.path.exists(file_path):
        return None

    payload = torch.load(file_path, map_location='cpu')
    feature_names = list(payload.get('feature_names') or [])
    sequence_length = int(payload.get('sequence_length') or 20)
    hidden_size = int(payload.get('hidden_size') or 64)
    num_layers = int(payload.get('num_layers') or 2)
    dropout = float(payload.get('dropout') or 0.2)
    if not feature_names:
        return None

    model = LSTMClassifier(
        input_size=len(feature_names),
        hidden_size=hidden_size,
        num_layers=num_layers,
        dropout=dropout,
        num_classes=3,
    )
    model.load_state_dict(payload['state_dict'])
    model.eval()

    scaler_mean = np.asarray(payload.get('scaler_mean') or [0.0] * len(feature_names), dtype=np.float32)
    scaler_scale = np.asarray(payload.get('scaler_scale') or [1.0] * len(feature_names), dtype=np.float32)
    scaler_scale = np.where(scaler_scale == 0, 1.0, scaler_scale)

    return {
        'model': model,
        'feature_names': feature_names,
        'sequence_length': sequence_length,
        'scaler_mean': scaler_mean,
        'scaler_scale': scaler_scale,
        'file_path': file_path,
    }


def _build_inference_sequence(asset_id, target_date, feature_names, sequence_length):
    lookback_days = max(120, int(sequence_length * 4))
    start_date = target_date - timedelta(days=lookback_days)
    feature_df = _create_feature_matrix(start_date, target_date, asset_ids=[int(asset_id)])
    if feature_df.empty:
        return None, None

    rows = feature_df[feature_df['asset_id'] == int(asset_id)].copy()
    if rows.empty:
        return None, None

    rows['date'] = pd.to_datetime(rows['date'])
    rows = rows.sort_values('date').tail(int(sequence_length))
    if len(rows) < int(sequence_length):
        return None, None

    for feature in feature_names:
        if feature not in rows.columns:
            rows[feature] = 0.0

    sequence = rows[feature_names].astype(np.float32).to_numpy()
    if sequence.shape[0] != int(sequence_length):
        return None, None

    latest_snapshot = {
        feature: float(sequence[-1, index])
        for index, feature in enumerate(feature_names)
    }
    return sequence, latest_snapshot


def _predict_with_lstm(asset_id, target_date, horizon_days, model_version=None, cache=None):
    version = model_version or _resolve_lstm_model_version(target_date)
    if cache is None:
        cache = {}

    cache_key = (version.id, int(horizon_days))
    artifact = cache.get(cache_key)
    if artifact is None:
        artifact = _load_lstm_artifact(version, horizon_days)
        if artifact is None:
            return None
        cache[cache_key] = artifact

    sequence, feature_snapshot = _build_inference_sequence(
        asset_id=asset_id,
        target_date=target_date,
        feature_names=artifact['feature_names'],
        sequence_length=artifact['sequence_length'],
    )
    if sequence is None:
        return None

    normalized = (sequence - artifact['scaler_mean']) / artifact['scaler_scale']
    tensor = torch.from_numpy(normalized.reshape(1, artifact['sequence_length'], -1)).float()

    with torch.no_grad():
        logits = artifact['model'](tensor)
        probs = torch.softmax(logits, dim=1).cpu().numpy()[0]

    down_prob = float(probs[0])
    flat_prob = float(probs[1])
    up_prob = float(probs[2])
    predicted_index = int(np.argmax(probs))
    predicted_label = _prediction_label_from_index(predicted_index)
    confidence = float(np.max(probs))

    trade_decision = estimate_trade_decision(
        asset_id=int(asset_id),
        as_of=target_date,
        horizon_days=int(horizon_days),
        up_probability=up_prob,
        predicted_label=predicted_label,
    )

    return {
        'model_version': version,
        'up_probability': up_prob,
        'flat_probability': flat_prob,
        'down_probability': down_prob,
        'confidence': confidence,
        'predicted_label': predicted_label,
        'feature_snapshot': feature_snapshot,
        'raw_scores': {
            'down': float(logits[0, 0].item()),
            'flat': float(logits[0, 1].item()),
            'up': float(logits[0, 2].item()),
        },
        'calibrated_scores': {
            'down': down_prob,
            'flat': flat_prob,
            'up': up_prob,
        },
        'trade_decision': trade_decision,
        'artifact_path': artifact['file_path'],
        'sequence_length': artifact['sequence_length'],
    }


def _save_lstm_artifact(model, scaler, feature_names, sequence_length, horizon_days, version, train_meta):
    model_dir = os.path.join(LSTM_MODELS_DIR, version)
    os.makedirs(model_dir, exist_ok=True)

    payload = {
        'state_dict': model.state_dict(),
        'feature_names': feature_names,
        'sequence_length': sequence_length,
        'input_size': len(feature_names),
        'hidden_size': train_meta['hidden_size'],
        'num_layers': train_meta['num_layers'],
        'dropout': train_meta['dropout'],
        'class_labels': ['DOWN', 'FLAT', 'UP'],
        'scaler_mean': scaler.mean_.tolist(),
        'scaler_scale': scaler.scale_.tolist(),
        'train_meta': train_meta,
    }

    artifact_path = os.path.join(model_dir, f'{horizon_days}d_model.pt')
    torch.save(payload, artifact_path)

    metrics_path = os.path.join(model_dir, f'{horizon_days}d_metrics.json')
    with open(metrics_path, 'w', encoding='utf-8') as file_handle:
        json.dump(train_meta, file_handle, ensure_ascii=True, indent=2)

    return artifact_path


def _train_single_horizon_lstm(X, y, dates, feature_names, horizon_days, version, sequence_length, device):
    if len(y) < 400:
        return {
            'status': 'insufficient_data',
            'training_samples': int(len(y)),
            'reason': 'not_enough_sequence_samples',
        }

    order = np.argsort(np.array(dates, dtype='datetime64[D]'))
    X = X[order]
    y = y[order]

    split_index = max(int(len(y) * 0.8), 1)
    split_index = min(split_index, len(y) - 1)
    if split_index <= 0:
        return {
            'status': 'insufficient_data',
            'training_samples': int(len(y)),
            'reason': 'unable_to_split_train_validation',
        }

    X_train, X_val = X[:split_index], X[split_index:]
    y_train, y_val = y[:split_index], y[split_index:]

    scaler = _fit_scaler_on_sequences(X_train)
    X_train = _transform_sequences(X_train, scaler)
    X_val = _transform_sequences(X_val, scaler)

    train_ds = TensorDataset(torch.from_numpy(X_train), torch.from_numpy(y_train))
    val_ds = TensorDataset(torch.from_numpy(X_val), torch.from_numpy(y_val))

    train_loader = DataLoader(train_ds, batch_size=128, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=256, shuffle=False)

    model = LSTMClassifier(input_size=X.shape[-1], hidden_size=64, num_layers=2, dropout=0.2, num_classes=3).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()

    best_state = None
    best_val_acc = -1.0
    epochs = 8

    for _ in range(epochs):
        model.train()
        for features_batch, labels_batch in train_loader:
            features_batch = features_batch.to(device)
            labels_batch = labels_batch.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(features_batch)
            loss = criterion(logits, labels_batch)
            loss.backward()
            optimizer.step()

        model.eval()
        correct = 0
        total = 0
        with torch.no_grad():
            for features_batch, labels_batch in val_loader:
                features_batch = features_batch.to(device)
                labels_batch = labels_batch.to(device)
                logits = model(features_batch)
                preds = torch.argmax(logits, dim=1)
                correct += (preds == labels_batch).sum().item()
                total += labels_batch.shape[0]

        val_acc = float(correct / total) if total else 0.0
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)

    train_meta = {
        'horizon_days': int(horizon_days),
        'version': version,
        'sequence_length': int(sequence_length),
        'training_samples': int(len(y_train)),
        'validation_samples': int(len(y_val)),
        'accuracy': float(max(best_val_acc, 0.0)),
        'epochs': int(epochs),
        'hidden_size': 64,
        'num_layers': 2,
        'dropout': 0.2,
        'trained_at': timezone.now().isoformat(),
    }

    artifact_path = _save_lstm_artifact(
        model=model,
        scaler=scaler,
        feature_names=feature_names,
        sequence_length=sequence_length,
        horizon_days=horizon_days,
        version=version,
        train_meta=train_meta,
    )

    return {
        'status': 'success',
        'accuracy': train_meta['accuracy'],
        'training_samples': train_meta['training_samples'],
        'validation_samples': train_meta['validation_samples'],
        'artifact_path': artifact_path,
        'feature_count': len(feature_names),
        'sequence_length': sequence_length,
    }


@shared_task
def train_lstm_models(
    training_start_date=None,
    training_end_date=None,
    horizons=None,
    sequence_length=20,
    asset_chunk_size=60,
    max_samples_per_horizon=30000,
):
    default_horizons = [3, 7, 30]
    if horizons is None:
        selected_horizons = default_horizons
    else:
        selected_horizons = []
        for value in horizons:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed in default_horizons and parsed not in selected_horizons:
                selected_horizons.append(parsed)
        if not selected_horizons:
            selected_horizons = default_horizons

    training_start, training_end = _parse_training_window(training_start_date, training_end_date)
    print(f'Training LSTM models from {training_start} to {training_end}')

    all_asset_ids = list(Asset.objects.values_list('id', flat=True))
    if not all_asset_ids:
        return {
            horizon: {
                'status': 'insufficient_data',
                'reason': 'no_assets',
                'training_samples': 0,
            }
            for horizon in selected_horizons
        }

    labels_by_horizon = {
        horizon: _create_labels_for_training(training_start, training_end, horizon)
        for horizon in selected_horizons
    }

    feature_names = []
    buffered = {
        horizon: {
            'X': [],
            'y': [],
            'dates': [],
        }
        for horizon in selected_horizons
    }
    sample_counts = {horizon: 0 for horizon in selected_horizons}

    for chunk_asset_ids in _iter_chunks(all_asset_ids, max(int(asset_chunk_size), 1)):
        chunk_df = _create_feature_matrix(training_start, training_end, asset_ids=chunk_asset_ids)
        if chunk_df.empty:
            continue

        if not feature_names:
            feature_names = list(
                chunk_df.attrs.get('feature_names')
                or [col for col in chunk_df.columns if col not in ['date', 'asset_id']]
            )
            if not feature_names:
                continue

        for horizon in selected_horizons:
            if sample_counts[horizon] >= int(max_samples_per_horizon):
                continue

            X_chunk, y_chunk, d_chunk = _build_sequences(
                feature_df=chunk_df,
                labels_dict=labels_by_horizon[horizon],
                horizon_days=horizon,
                feature_names=feature_names,
                sequence_length=int(sequence_length),
            )
            if len(y_chunk) == 0:
                continue

            remaining = int(max_samples_per_horizon) - sample_counts[horizon]
            if remaining <= 0:
                continue

            X_take = X_chunk[:remaining]
            y_take = y_chunk[:remaining]
            d_take = d_chunk[:remaining]

            buffered[horizon]['X'].append(X_take)
            buffered[horizon]['y'].append(y_take)
            buffered[horizon]['dates'].extend(d_take)
            sample_counts[horizon] += len(y_take)

        if all(sample_counts[h] >= int(max_samples_per_horizon) for h in selected_horizons):
            break

    if not feature_names:
        return {
            horizon: {
                'status': 'insufficient_data',
                'reason': 'feature_matrix_empty',
                'training_samples': 0,
            }
            for horizon in selected_horizons
        }

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    version = f'lstm-{training_end.isoformat()}'

    results = {}
    for horizon in selected_horizons:
        if not buffered[horizon]['y']:
            results[horizon] = {
                'status': 'insufficient_data',
                'training_samples': 0,
                'reason': 'no_sequences_collected',
            }
            continue

        X = np.concatenate(buffered[horizon]['X'], axis=0)
        y = np.concatenate(buffered[horizon]['y'], axis=0)
        sample_dates = buffered[horizon]['dates']

        results[horizon] = _train_single_horizon_lstm(
            X=X,
            y=y,
            dates=sample_dates,
            feature_names=feature_names,
            horizon_days=horizon,
            version=version,
            sequence_length=int(sequence_length),
            device=device,
        )

    success_accuracies = [float(payload['accuracy']) for payload in results.values() if payload.get('status') == 'success']
    aggregate_accuracy = float(np.mean(success_accuracies)) if success_accuracies else 0.0

    aggregate_artifact_path = os.path.join(LSTM_MODELS_DIR, version)
    os.makedirs(aggregate_artifact_path, exist_ok=True)

    with open(os.path.join(aggregate_artifact_path, 'summary.json'), 'w', encoding='utf-8') as file_handle:
        json.dump(
            {
                'version': version,
                'training_window_start': training_start.isoformat(),
                'training_window_end': training_end.isoformat(),
                'horizons': selected_horizons,
                'sequence_length': int(sequence_length),
                'results': results,
                'aggregate_accuracy': aggregate_accuracy,
            },
            file_handle,
            ensure_ascii=True,
            indent=2,
        )

    ModelVersion.objects.filter(
        model_type=ModelVersion.ModelType.LSTM,
        is_active=True,
    ).exclude(version=version).update(is_active=False)

    row, _ = ModelVersion.objects.update_or_create(
        model_type=ModelVersion.ModelType.LSTM,
        version=version,
        defaults={
            'status': ModelVersion.Status.READY,
            'artifact_path': aggregate_artifact_path,
            'metrics': {
                'accuracy': aggregate_accuracy,
                'accuracy_by_horizon': {
                    str(horizon): payload.get('accuracy', 0.0)
                    for horizon, payload in results.items()
                },
            },
            'feature_schema': feature_names,
            'training_window_start': training_start,
            'training_window_end': training_end,
            'trained_at': timezone.now(),
            'is_active': True,
            'metadata': {
                'source': 'pytorch_lstm_pipeline',
                'horizons': selected_horizons,
                'sequence_length': int(sequence_length),
                'results': results,
                'model_architecture': {
                    'hidden_size': 64,
                    'num_layers': 2,
                    'dropout': 0.2,
                },
            },
        },
    )

    lightgbm_results = {}
    for artifact in LightGBMModelArtifact.objects.filter(
        is_active=True,
        status=LightGBMModelArtifact.Status.READY,
    ):
        lightgbm_results[artifact.horizon_days] = {
            'status': 'success',
            'accuracy': float((artifact.metrics_json or {}).get('accuracy') or 0.0),
        }

    _refresh_ensemble_weights(training_end, lightgbm_results)

    return {
        'version': row.version,
        'status': 'completed',
        'training_window_start': training_start.isoformat(),
        'training_window_end': training_end.isoformat(),
        'results': results,
        'aggregate_accuracy': aggregate_accuracy,
        'device': str(device),
    }


@shared_task
def generate_lstm_predictions_for_date(target_date=None, horizons=None):
    if target_date:
        try:
            as_of = date.fromisoformat(str(target_date))
        except ValueError:
            as_of = timezone.now().date()
    else:
        as_of = timezone.now().date()

    horizons = horizons or [3, 7, 30]
    selected_horizons = [int(value) for value in horizons if str(value).isdigit() and int(value) in {3, 7, 30}]
    if not selected_horizons:
        selected_horizons = [3, 7, 30]

    model_version = _resolve_lstm_model_version(as_of)
    runtime_cache = {}
    processed = 0

    for asset in Asset.objects.all():
        for horizon in selected_horizons:
            prediction = _predict_with_lstm(
                asset_id=asset.id,
                target_date=as_of,
                horizon_days=horizon,
                model_version=model_version,
                cache=runtime_cache,
            )
            if prediction is None:
                continue

            trade_decision = prediction['trade_decision']
            PredictionResult.objects.update_or_create(
                asset=asset,
                date=as_of,
                horizon_days=int(horizon),
                model_version=model_version,
                defaults={
                    'up_probability': prediction['up_probability'],
                    'flat_probability': prediction['flat_probability'],
                    'down_probability': prediction['down_probability'],
                    'confidence': prediction['confidence'],
                    'predicted_label': prediction['predicted_label'],
                    'target_price': trade_decision['target_price'],
                    'stop_loss_price': trade_decision['stop_loss_price'],
                    'risk_reward_ratio': trade_decision['risk_reward_ratio'],
                    'trade_score': trade_decision['trade_score'],
                    'suggested': trade_decision['suggested'],
                    'macro_phase': '',
                    'event_tag': '',
                    'feature_payload': prediction['feature_snapshot'],
                    'metadata': {
                        'source': 'lstm_inference',
                        'model_type': 'LSTM',
                        'raw_scores': prediction['raw_scores'],
                        'calibrated_scores': prediction['calibrated_scores'],
                        'artifact_path': prediction['artifact_path'],
                        'sequence_length': prediction['sequence_length'],
                    },
                },
            )
            processed += 1

    return f'LSTM predictions generated for {processed} asset-horizon rows on {as_of}'


@shared_task
def generate_lstm_prediction_for_asset(asset_id, target_date=None, horizons=None):
    if target_date:
        try:
            as_of = date.fromisoformat(str(target_date))
        except ValueError:
            as_of = timezone.now().date()
    else:
        as_of = timezone.now().date()

    try:
        asset = Asset.objects.get(id=asset_id)
    except Asset.DoesNotExist:
        return f'Asset {asset_id} not found.'

    horizons = horizons or [3, 7, 30]
    selected_horizons = [int(value) for value in horizons if str(value).isdigit() and int(value) in {3, 7, 30}]
    if not selected_horizons:
        selected_horizons = [3, 7, 30]

    model_version = _resolve_lstm_model_version(as_of)
    runtime_cache = {}
    processed = 0

    for horizon in selected_horizons:
        prediction = _predict_with_lstm(
            asset_id=asset.id,
            target_date=as_of,
            horizon_days=horizon,
            model_version=model_version,
            cache=runtime_cache,
        )
        if prediction is None:
            continue

        trade_decision = prediction['trade_decision']
        PredictionResult.objects.update_or_create(
            asset=asset,
            date=as_of,
            horizon_days=int(horizon),
            model_version=model_version,
            defaults={
                'up_probability': prediction['up_probability'],
                'flat_probability': prediction['flat_probability'],
                'down_probability': prediction['down_probability'],
                'confidence': prediction['confidence'],
                'predicted_label': prediction['predicted_label'],
                'target_price': trade_decision['target_price'],
                'stop_loss_price': trade_decision['stop_loss_price'],
                'risk_reward_ratio': trade_decision['risk_reward_ratio'],
                'trade_score': trade_decision['trade_score'],
                'suggested': trade_decision['suggested'],
                'macro_phase': '',
                'event_tag': '',
                'feature_payload': prediction['feature_snapshot'],
                'metadata': {
                    'source': 'lstm_inference',
                    'model_type': 'LSTM',
                    'raw_scores': prediction['raw_scores'],
                    'calibrated_scores': prediction['calibrated_scores'],
                    'artifact_path': prediction['artifact_path'],
                    'sequence_length': prediction['sequence_length'],
                },
            },
        )
        processed += 1

    return f'LSTM predictions generated for asset {asset.symbol} ({processed} rows) on {as_of}'
