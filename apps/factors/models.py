from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.markets.models import Asset


class FundamentalFactorSnapshot(models.Model):
    """Daily fundamental snapshot used for multi-factor scoring."""
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='fundamental_factor_snapshots',
        verbose_name=_('Asset'),
    )
    date = models.DateField(_('Date'), db_index=True)
    pe = models.DecimalField(_('PE'), max_digits=12, decimal_places=4, null=True, blank=True)
    pb = models.DecimalField(_('PB'), max_digits=12, decimal_places=4, null=True, blank=True)
    roe = models.DecimalField(_('ROE'), max_digits=12, decimal_places=6, null=True, blank=True)
    roe_qoq = models.DecimalField(_('ROE QoQ'), max_digits=12, decimal_places=6, null=True, blank=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Fundamental Factor Snapshot')
        verbose_name_plural = _('Fundamental Factor Snapshots')
        unique_together = ('asset', 'date')
        indexes = [
            models.Index(fields=['asset', 'date']),
            models.Index(fields=['date']),
        ]


class CapitalFlowSnapshot(models.Model):
    """Daily capital flow snapshot used for multi-factor scoring."""
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='capital_flow_snapshots',
        verbose_name=_('Asset'),
    )
    date = models.DateField(_('Date'), db_index=True)
    northbound_net_5d = models.DecimalField(_('Northbound Net 5D'), max_digits=18, decimal_places=4, null=True, blank=True)
    northbound_net_10d = models.DecimalField(_('Northbound Net 10D'), max_digits=18, decimal_places=4, null=True, blank=True)
    northbound_net_20d = models.DecimalField(_('Northbound Net 20D'), max_digits=18, decimal_places=4, null=True, blank=True)
    main_force_net_5d = models.DecimalField(_('Main Force Net 5D'), max_digits=18, decimal_places=4, null=True, blank=True)
    margin_balance_change_5d = models.DecimalField(_('Margin Balance Change 5D'), max_digits=18, decimal_places=4, null=True, blank=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Capital Flow Snapshot')
        verbose_name_plural = _('Capital Flow Snapshots')
        unique_together = ('asset', 'date')
        indexes = [
            models.Index(fields=['asset', 'date']),
            models.Index(fields=['date']),
        ]


class FactorScore(models.Model):
    """Daily multi-factor score and bottom probability output."""
    class FactorMode(models.TextChoices):
        TECHNICAL = 'TECHNICAL', _('Technical')
        FUNDAMENTAL = 'FUNDAMENTAL', _('Fundamental')
        COMPOSITE = 'COMPOSITE', _('Composite')

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='factor_scores',
        verbose_name=_('Asset'),
    )
    date = models.DateField(_('Date'), db_index=True)

    # Raw component metrics
    pe_percentile_score = models.DecimalField(_('PE Percentile Score'), max_digits=7, decimal_places=6, null=True, blank=True)
    pb_percentile_score = models.DecimalField(_('PB Percentile Score'), max_digits=7, decimal_places=6, null=True, blank=True)
    roe_trend_score = models.DecimalField(_('ROE Trend Score'), max_digits=7, decimal_places=6, null=True, blank=True)
    northbound_flow_score = models.DecimalField(_('Northbound Flow Score'), max_digits=7, decimal_places=6, null=True, blank=True)
    main_force_flow_score = models.DecimalField(_('Main Force Flow Score'), max_digits=7, decimal_places=6, null=True, blank=True)
    margin_flow_score = models.DecimalField(_('Margin Flow Score'), max_digits=7, decimal_places=6, null=True, blank=True)
    technical_reversal_score = models.DecimalField(_('Technical Reversal Score'), max_digits=7, decimal_places=6, null=True, blank=True)
    sentiment_score = models.DecimalField(_('Sentiment Score'), max_digits=7, decimal_places=6, null=True, blank=True)

    # Aggregates
    fundamental_score = models.DecimalField(_('Fundamental Score'), max_digits=7, decimal_places=6, default=0)
    capital_flow_score = models.DecimalField(_('Capital Flow Score'), max_digits=7, decimal_places=6, default=0)
    technical_score = models.DecimalField(_('Technical Score'), max_digits=7, decimal_places=6, default=0)

    financial_weight = models.DecimalField(_('Financial Weight'), max_digits=6, decimal_places=4, default=0.4)
    flow_weight = models.DecimalField(_('Flow Weight'), max_digits=6, decimal_places=4, default=0.3)
    technical_weight = models.DecimalField(_('Technical Weight'), max_digits=6, decimal_places=4, default=0.3)
    sentiment_weight = models.DecimalField(_('Sentiment Weight'), max_digits=6, decimal_places=4, default=0.0)

    composite_score = models.DecimalField(_('Composite Score'), max_digits=7, decimal_places=6, default=0)
    bottom_probability_score = models.DecimalField(_('Bottom Probability Score'), max_digits=7, decimal_places=6, default=0)
    mode = models.CharField(_('Mode'), max_length=20, choices=FactorMode.choices, default=FactorMode.COMPOSITE)

    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Factor Score')
        verbose_name_plural = _('Factor Scores')
        unique_together = ('asset', 'date', 'mode')
        ordering = ['-date', '-bottom_probability_score']
        indexes = [
            models.Index(fields=['date', 'mode', 'bottom_probability_score']),
            models.Index(fields=['asset', 'date']),
        ]
