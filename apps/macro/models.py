from django.db import models
from django.utils.translation import gettext_lazy as _


class MacroSnapshot(models.Model):
    """Daily/monthly macro data snapshot."""
    date = models.DateField(_('Date'), unique=True, db_index=True)
    dxy = models.DecimalField(_('US Dollar Index (DXY)'), max_digits=10, decimal_places=4, null=True, blank=True)
    cny_usd = models.DecimalField(_('CNY/USD'), max_digits=10, decimal_places=4, null=True, blank=True)
    cn10y_yield = models.DecimalField(_('China 10Y Yield'), max_digits=8, decimal_places=4, null=True, blank=True)
    cn2y_yield = models.DecimalField(_('China 2Y Yield'), max_digits=8, decimal_places=4, null=True, blank=True)
    pmi_manufacturing = models.DecimalField(_('PMI Manufacturing'), max_digits=7, decimal_places=3, null=True, blank=True)
    pmi_non_manufacturing = models.DecimalField(_('PMI Non-Manufacturing'), max_digits=7, decimal_places=3, null=True, blank=True)
    cpi_yoy = models.DecimalField(_('CPI YoY'), max_digits=7, decimal_places=3, null=True, blank=True)
    ppi_yoy = models.DecimalField(_('PPI YoY'), max_digits=7, decimal_places=3, null=True, blank=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Macro Snapshot')
        verbose_name_plural = _('Macro Snapshots')
        ordering = ['-date']


class MarketContext(models.Model):
    class MacroPhase(models.TextChoices):
        RECOVERY = 'RECOVERY', _('Recovery')
        OVERHEAT = 'OVERHEAT', _('Overheat')
        STAGFLATION = 'STAGFLATION', _('Stagflation')
        RECESSION = 'RECESSION', _('Recession')

    context_key = models.CharField(_('Context Key'), max_length=50, default='current', db_index=True)
    macro_phase = models.CharField(_('Macro Phase'), max_length=20, choices=MacroPhase.choices, db_index=True)
    event_tag = models.CharField(_('Event Tag'), max_length=100, blank=True, db_index=True)
    is_active = models.BooleanField(_('Is Active'), default=True, db_index=True)
    starts_at = models.DateField(_('Starts At'))
    ends_at = models.DateField(_('Ends At'), null=True, blank=True)
    notes = models.TextField(_('Notes'), blank=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Market Context')
        verbose_name_plural = _('Market Contexts')
        ordering = ['-starts_at', '-updated_at']
        indexes = [
            models.Index(fields=['context_key', 'is_active']),
            models.Index(fields=['macro_phase', 'is_active']),
        ]


class EventImpactStat(models.Model):
    """Historical impact stats for tagged macro/policy events."""
    event_tag = models.CharField(_('Event Tag'), max_length=100, db_index=True)
    sector = models.CharField(_('Sector'), max_length=100, blank=True, db_index=True)
    horizon_days = models.PositiveIntegerField(_('Horizon Days'), default=20)
    avg_return = models.DecimalField(_('Average Return'), max_digits=10, decimal_places=6)
    excess_return = models.DecimalField(_('Excess Return'), max_digits=10, decimal_places=6, default=0)
    sample_size = models.PositiveIntegerField(_('Sample Size'), default=0)
    observations_start = models.DateField(_('Observations Start'), null=True, blank=True)
    observations_end = models.DateField(_('Observations End'), null=True, blank=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Event Impact Statistic')
        verbose_name_plural = _('Event Impact Statistics')
        ordering = ['event_tag', 'sector', 'horizon_days']
        unique_together = ('event_tag', 'sector', 'horizon_days')
