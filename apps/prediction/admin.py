from django.contrib import admin

from .models import ModelVersion, PredictionResult
from .models_lightgbm import LightGBMModelArtifact, LightGBMPrediction, EnsembleWeightSnapshot


@admin.register(ModelVersion)
class ModelVersionAdmin(admin.ModelAdmin):
    list_display = ('model_type', 'version', 'status', 'is_active', 'trained_at')
    list_filter = ('model_type', 'status', 'is_active')
    search_fields = ('version',)


@admin.register(PredictionResult)
class PredictionResultAdmin(admin.ModelAdmin):
    list_display = ('asset', 'date', 'horizon_days', 'predicted_label', 'confidence', 'model_version')
    list_filter = ('date', 'horizon_days', 'predicted_label', 'macro_phase')
    search_fields = ('asset__symbol', 'asset__name', 'event_tag')
    date_hierarchy = 'date'

@admin.register(LightGBMModelArtifact)
class LightGBMModelArtifactAdmin(admin.ModelAdmin):
    list_display = ('horizon_days', 'version', 'status', 'is_active', 'trained_at')
    list_filter = ('horizon_days', 'status', 'is_active')
    search_fields = ('version',)


@admin.register(LightGBMPrediction)
class LightGBMPredictionAdmin(admin.ModelAdmin):
    list_display = ('asset', 'date', 'horizon_days', 'predicted_label', 'confidence', 'model_artifact')
    list_filter = ('date', 'horizon_days', 'predicted_label')
    search_fields = ('asset__symbol', 'asset__name')
    date_hierarchy = 'date'


@admin.register(EnsembleWeightSnapshot)
class EnsembleWeightSnapshotAdmin(admin.ModelAdmin):
    list_display = ('date', 'lightgbm_weight', 'lstm_weight', 'heuristic_weight')
    list_filter = ('date',)
    date_hierarchy = 'date'