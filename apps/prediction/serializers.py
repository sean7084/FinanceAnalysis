from rest_framework import serializers

from .models import ModelVersion, PredictionResult


class ModelVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelVersion
        fields = [
            'id', 'model_type', 'version', 'status', 'artifact_path',
            'metrics', 'feature_schema', 'training_window_start', 'training_window_end',
            'trained_at', 'is_active', 'metadata', 'created_at', 'updated_at',
        ]


class PredictionResultSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    model_type = serializers.CharField(source='model_version.model_type', read_only=True)
    model_version_name = serializers.CharField(source='model_version.version', read_only=True)

    class Meta:
        model = PredictionResult
        fields = [
            'id', 'asset', 'asset_symbol', 'asset_name', 'date', 'horizon_days',
            'up_probability', 'flat_probability', 'down_probability',
            'confidence', 'predicted_label',
            'model_version', 'model_type', 'model_version_name',
            'macro_phase', 'event_tag', 'feature_payload', 'metadata',
            'created_at', 'updated_at',
        ]
