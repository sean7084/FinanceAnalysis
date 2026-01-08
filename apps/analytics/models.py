from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.markets.models import Asset


class TechnicalIndicator(models.Model):
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='indicators',
        verbose_name=_('Asset')
    )
    timestamp = models.DateTimeField(
        _('Timestamp'),
        db_index=True
    )
    indicator_type = models.CharField(
        _('Indicator Type'),
        max_length=50,
        db_index=True
    )
    value = models.DecimalField(
        _('Value'),
        max_digits=18,
        decimal_places=8
    )
    parameters = models.JSONField(
        _('Parameters'),
        default=dict,
        blank=True
    )

    class Meta:
        verbose_name = _('Technical Indicator')
        verbose_name_plural = _('Technical Indicators')
        ordering = ['-timestamp', 'asset', 'indicator_type']
        indexes = [
            models.Index(fields=['asset', 'timestamp', 'indicator_type']),
        ]
        unique_together = ('asset', 'timestamp', 'indicator_type', 'parameters')

    def __str__(self):
        return f"{self.asset.symbol} - {self.indicator_type} ({self.timestamp.date()}): {self.value}"
