from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.markets.models import Asset


class ModelVersion(models.Model):
    class ModelType(models.TextChoices):
        LIGHTGBM = 'LIGHTGBM', _('LightGBM')
        LSTM = 'LSTM', _('LSTM')
        ENSEMBLE = 'ENSEMBLE', _('Ensemble')

    class Status(models.TextChoices):
        TRAINING = 'TRAINING', _('Training')
        READY = 'READY', _('Ready')
        FAILED = 'FAILED', _('Failed')
        ARCHIVED = 'ARCHIVED', _('Archived')

    model_type = models.CharField(_('Model Type'), max_length=20, choices=ModelType.choices, db_index=True)
    version = models.CharField(_('Version'), max_length=50)
    status = models.CharField(_('Status'), max_length=20, choices=Status.choices, default=Status.TRAINING)
    artifact_path = models.CharField(_('Artifact Path'), max_length=500, blank=True)
    metrics = models.JSONField(_('Metrics'), default=dict, blank=True)
    feature_schema = models.JSONField(_('Feature Schema'), default=list, blank=True)
    training_window_start = models.DateField(_('Training Window Start'), null=True, blank=True)
    training_window_end = models.DateField(_('Training Window End'), null=True, blank=True)
    trained_at = models.DateTimeField(_('Trained At'), null=True, blank=True)
    is_active = models.BooleanField(_('Is Active'), default=False, db_index=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Model Version')
        verbose_name_plural = _('Model Versions')
        ordering = ['-created_at']
        unique_together = ('model_type', 'version')
        indexes = [
            models.Index(fields=['model_type', 'is_active']),
        ]


class PredictionResult(models.Model):
    class Horizon(models.IntegerChoices):
        D3 = 3, _('3 Days')
        D7 = 7, _('7 Days')
        D30 = 30, _('30 Days')

    class Label(models.TextChoices):
        UP = 'UP', _('Up')
        FLAT = 'FLAT', _('Flat')
        DOWN = 'DOWN', _('Down')

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='prediction_results',
        verbose_name=_('Asset'),
    )
    date = models.DateField(_('Date'), db_index=True)
    horizon_days = models.PositiveIntegerField(_('Horizon Days'), choices=Horizon.choices, db_index=True)

    up_probability = models.DecimalField(_('Up Probability'), max_digits=7, decimal_places=6)
    flat_probability = models.DecimalField(_('Flat Probability'), max_digits=7, decimal_places=6)
    down_probability = models.DecimalField(_('Down Probability'), max_digits=7, decimal_places=6)
    confidence = models.DecimalField(_('Confidence'), max_digits=7, decimal_places=6)
    predicted_label = models.CharField(_('Predicted Label'), max_length=10, choices=Label.choices, db_index=True)
    target_price = models.DecimalField(_('Target Price'), max_digits=12, decimal_places=4, null=True, blank=True)
    stop_loss_price = models.DecimalField(_('Stop Loss Price'), max_digits=12, decimal_places=4, null=True, blank=True)
    risk_reward_ratio = models.DecimalField(_('Risk Reward Ratio'), max_digits=12, decimal_places=6, null=True, blank=True)
    trade_score = models.DecimalField(_('Trade Score'), max_digits=12, decimal_places=6, null=True, blank=True)
    suggested = models.BooleanField(_('Suggested'), default=False, db_index=True)

    model_version = models.ForeignKey(
        ModelVersion,
        on_delete=models.SET_NULL,
        related_name='predictions',
        null=True,
        blank=True,
        verbose_name=_('Model Version'),
    )
    macro_phase = models.CharField(_('Macro Phase'), max_length=20, blank=True, db_index=True)
    event_tag = models.CharField(_('Event Tag'), max_length=100, blank=True, db_index=True)
    feature_payload = models.JSONField(_('Feature Payload'), default=dict, blank=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Prediction Result')
        verbose_name_plural = _('Prediction Results')
        ordering = ['-date', 'asset__symbol', 'horizon_days']
        unique_together = ('asset', 'date', 'horizon_days', 'model_version')
        indexes = [
            models.Index(fields=['date', 'horizon_days', 'predicted_label']),
            models.Index(fields=['asset', 'date', 'horizon_days']),
        ]


# Import LightGBM models for Django migration discovery
from .models_lightgbm import (  # noqa: F401, E402
    LightGBMModelArtifact,
    LightGBMPrediction,
    EnsembleWeightSnapshot,
    FeatureImportanceSnapshot,
)
