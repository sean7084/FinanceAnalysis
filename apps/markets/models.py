from django.db import models
from django.utils.translation import gettext_lazy as _

class Market(models.Model):
    """
    Represents a financial market or exchange.
    e.g., SSE (Shanghai), SZSE (Shenzhen), HKEX (Hong Kong)
    """
    code = models.CharField(_("Market Code"), max_length=20, unique=True)
    name = models.CharField(_("Market Name"), max_length=100)

    class Meta:
        verbose_name = _("Market")
        verbose_name_plural = _("Markets")

    def __str__(self):
        return self.name

class Asset(models.Model):
    """
    Represents a financial asset, such as a stock or fund.
    """
    class ListingStatus(models.TextChoices):
        ACTIVE = 'A', _('Active')
        DELISTED = 'D', _('Delisted')

    market = models.ForeignKey(Market, on_delete=models.CASCADE, related_name='assets', verbose_name=_("Market"))
    symbol = models.CharField(_("Symbol"), max_length=20, help_text=_("e.g., 600519"))
    ts_code = models.CharField(_("Tushare Code"), max_length=30, unique=True, help_text=_("e.g., 600519.SH"))
    name = models.CharField(_("Asset Name"), max_length=255)
    listing_status = models.CharField(
        _("Listing Status"),
        max_length=1,
        choices=ListingStatus.choices,
        default=ListingStatus.ACTIVE
    )
    list_date = models.DateField(
        _("List Date"),
        null=True,
        blank=True,
        help_text=_("IPO/listing date from TuShare stock_basic")
    )

    class Meta:
        verbose_name = _("Asset")
        verbose_name_plural = _("Assets")
        unique_together = ('market', 'symbol')

    def __str__(self):
        return f"{self.name} ({self.ts_code})"

class OHLCV(models.Model):
    """
    Stores daily Open, High, Low, Close, and Volume data for an asset.
    """
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='ohlcv_data', verbose_name=_("Asset"))
    date = models.DateField(_("Date"))
    open = models.DecimalField(_("Open"), max_digits=12, decimal_places=4)
    high = models.DecimalField(_("High"), max_digits=12, decimal_places=4)
    low = models.DecimalField(_("Low"), max_digits=12, decimal_places=4)
    close = models.DecimalField(_("Close"), max_digits=12, decimal_places=4)
    adj_close = models.DecimalField(_("Adjusted Close"), max_digits=12, decimal_places=4)
    volume = models.BigIntegerField(_("Volume"))
    amount = models.DecimalField(_("Amount"), max_digits=20, decimal_places=4, help_text=_("Turnover amount"))

    class Meta:
        verbose_name = _("OHLCV Data")
        verbose_name_plural = _("OHLCV Data")
        ordering = ['-date']
        indexes = [
            models.Index(fields=['asset', 'date']),
        ]
        unique_together = ('asset', 'date')

    def __str__(self):
        return f"{self.asset.ts_code} on {self.date}"
