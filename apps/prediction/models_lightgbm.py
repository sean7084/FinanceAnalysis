from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.markets.models import Asset


class LightGBMModelArtifact(models.Model):
    """Registry for trained LightGBM model files and metadata."""
    class Status(models.TextChoices):
        TRAINING = 'TRAINING', _('Training')
        READY = 'READY', _('Ready')
        FAILED = 'FAILED', _('Failed')
        ARCHIVED = 'ARCHIVED', _('Archived')

    horizon_days = models.PositiveIntegerField(_('Horizon Days'), choices=[(3, '3'), (7, '7'), (30, '30')], db_index=True)
    version = models.CharField(_('Version'), max_length=50)
    status = models.CharField(_('Status'), max_length=20, choices=Status.choices, default=Status.TRAINING)
    artifact_path = models.CharField(_('Artifact Path'), max_length=500, help_text='Path to model.bin, scaler.pkl, etc.')
    metrics_json = models.JSONField(_('Metrics'), default=dict, blank=True, help_text='{"accuracy": 0.62, "f1_macro": 0.60, ...}')
    feature_names = models.JSONField(_('Feature Names'), default=list, blank=True)
    training_window_start = models.DateField(_('Training Window Start'), null=True, blank=True)
    training_window_end = models.DateField(_('Training Window End'), null=True, blank=True)
    trained_at = models.DateTimeField(_('Trained At'), null=True, blank=True)
    is_active = models.BooleanField(_('Is Active'), default=False, db_index=True)
    feature_importance = models.JSONField(_('Feature Importance'), default=dict, blank=True, help_text='Top features by importance')
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('LightGBM Model Artifact')
        verbose_name_plural = _('LightGBM Model Artifacts')
        ordering = ['-created_at']
        unique_together = ('horizon_days', 'version')
        indexes = [
            models.Index(fields=['horizon_days', 'is_active']),
            models.Index(fields=['status']),
        ]


class LightGBMPrediction(models.Model):
    """Daily LightGBM-generated predictions (parallel to heuristic PredictionResult)."""
    class Label(models.TextChoices):
        UP = 'UP', _('Up')
        FLAT = 'FLAT', _('Flat')
        DOWN = 'DOWN', _('Down')

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='lightgbm_predictions',
        verbose_name=_('Asset'),
    )
    date = models.DateField(_('Date'), db_index=True)
    horizon_days = models.PositiveIntegerField(_('Horizon Days'), choices=[(3, '3'), (7, '7'), (30, '30')], db_index=True)

    up_probability = models.DecimalField(_('Up Probability'), max_digits=7, decimal_places=6)
    flat_probability = models.DecimalField(_('Flat Probability'), max_digits=7, decimal_places=6)
    down_probability = models.DecimalField(_('Down Probability'), max_digits=7, decimal_places=6)
    predicted_label = models.CharField(_('Predicted Label'), max_length=10, choices=Label.choices, db_index=True)
    confidence = models.DecimalField(_('Confidence'), max_digits=7, decimal_places=6)
    target_price = models.DecimalField(_('Target Price'), max_digits=12, decimal_places=4, null=True, blank=True)
    stop_loss_price = models.DecimalField(_('Stop Loss Price'), max_digits=12, decimal_places=4, null=True, blank=True)
    risk_reward_ratio = models.DecimalField(_('Risk Reward Ratio'), max_digits=12, decimal_places=6, null=True, blank=True)
    trade_score = models.DecimalField(_('Trade Score'), max_digits=12, decimal_places=6, null=True, blank=True)
    suggested = models.BooleanField(_('Suggested'), default=False, db_index=True)

    model_artifact = models.ForeignKey(
        LightGBMModelArtifact,
        on_delete=models.SET_NULL,
        related_name='predictions',
        null=True,
        blank=True,
        verbose_name=_('Model Artifact'),
    )

    # Feature snapshot for debugging/analysis
    feature_snapshot = models.JSONField(_('Feature Snapshot'), default=dict, blank=True, help_text='Input features used for this prediction')
    raw_scores = models.JSONField(_('Raw Scores'), default=dict, blank=True, help_text='Before calibration')
    calibrated_scores = models.JSONField(_('Calibrated Scores'), default=dict, blank=True, help_text='After Platt scaling')

    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('LightGBM Prediction')
        verbose_name_plural = _('LightGBM Predictions')
        ordering = ['-date', 'asset__symbol', 'horizon_days']
        unique_together = ('asset', 'date', 'horizon_days', 'model_artifact')
        indexes = [
            models.Index(fields=['date', 'horizon_days']),
            models.Index(fields=['asset', 'date', 'horizon_days']),
        ]


class EnsembleWeightSnapshot(models.Model):
    """Track ensemble weights over time for retrospective analysis."""
    date = models.DateField(_('Date'), unique=True, db_index=True)
    lightgbm_weight = models.DecimalField(_('LightGBM Weight'), max_digits=5, decimal_places=4)
    lstm_weight = models.DecimalField(_('LSTM Weight'), max_digits=5, decimal_places=4)
    heuristic_weight = models.DecimalField(_('Heuristic Weight'), max_digits=5, decimal_places=4)
    basis_lookback_days = models.PositiveIntegerField(_('Basis Lookback Days'), default=60)
    basis_metrics = models.JSONField(_('Basis Metrics'), default=dict, blank=True, help_text='Accuracy per model over lookback window')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Ensemble Weight Snapshot')
        verbose_name_plural = _('Ensemble Weight Snapshots')
        ordering = ['-date']


class FeatureImportanceSnapshot(models.Model):
    """Historical per-feature importance for a trained LightGBM artifact."""

    model_artifact = models.ForeignKey(
        LightGBMModelArtifact,
        on_delete=models.CASCADE,
        related_name='feature_importance_snapshots',
        verbose_name=_('Model Artifact'),
    )
    horizon_days = models.PositiveIntegerField(_('Horizon Days'), choices=[(3, '3'), (7, '7'), (30, '30')], db_index=True)
    feature_name = models.CharField(_('Feature Name'), max_length=120, db_index=True)
    importance_score = models.FloatField(_('Importance Score'), default=0.0)
    importance_rank = models.PositiveIntegerField(_('Importance Rank'), default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Feature Importance Snapshot')
        verbose_name_plural = _('Feature Importance Snapshots')
        ordering = ['horizon_days', 'importance_rank', 'feature_name']
        unique_together = ('model_artifact', 'feature_name')
        indexes = [
            models.Index(fields=['horizon_days', 'feature_name'], name='prediction__horizon_145c65_idx'),
            models.Index(fields=['model_artifact', 'importance_rank'], name='prediction__model_a_eed271_idx'),
        ]
