from rest_framework import serializers

from .models_lightgbm import LightGBMModelArtifact, LightGBMPrediction, EnsembleWeightSnapshot


class LightGBMModelArtifactSerializer(serializers.ModelSerializer):
    class Meta:
        model = LightGBMModelArtifact
        fields = [
            'id', 'horizon_days', 'version', 'status', 'artifact_path',
            'metrics_json', 'feature_names', 'training_window_start', 'training_window_end',
            'trained_at', 'is_active', 'feature_importance', 'metadata', 'created_at', 'updated_at',
        ]


class LightGBMPredictionSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    model_version = serializers.CharField(source='model_artifact.version', read_only=True)
    model_horizon = serializers.IntegerField(source='model_artifact.horizon_days', read_only=True)

    class Meta:
        model = LightGBMPrediction
        fields = [
            'id', 'asset', 'asset_symbol', 'asset_name', 'date', 'horizon_days',
            'up_probability', 'flat_probability', 'down_probability',
            'predicted_label', 'confidence',
            'model_artifact', 'model_version', 'model_horizon',
            'feature_snapshot', 'raw_scores', 'calibrated_scores',
            'metadata', 'created_at', 'updated_at',
        ]


class EnsembleWeightSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnsembleWeightSnapshot
        fields = [
            'id', 'date', 'lightgbm_weight', 'lstm_weight', 'heuristic_weight',
            'basis_lookback_days', 'basis_metrics', 'created_at',
        ]
