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
    membership_tags = models.JSONField(
        _("Membership Tags"),
        default=list,
        blank=True,
        help_text=_("Current benchmark/index memberships, e.g. CSI300 and CSIA500."),
    )

    class Meta:
        verbose_name = _("Asset")
        verbose_name_plural = _("Assets")
        unique_together = ('market', 'symbol')

    def __str__(self):
        return f"{self.name} ({self.ts_code})"


class IndexMembership(models.Model):
    """
    Stores historical benchmark/index membership snapshots for an asset.
    """
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='index_memberships',
        verbose_name=_("Asset"),
    )
    index_code = models.CharField(_("Index Code"), max_length=20, db_index=True)
    index_name = models.CharField(_("Index Name"), max_length=100)
    trade_date = models.DateField(_("Trade Date"), db_index=True)
    weight = models.DecimalField(_("Weight"), max_digits=12, decimal_places=6, null=True, blank=True)
    source = models.CharField(_("Source"), max_length=50, default='tushare_index_weight')

    class Meta:
        verbose_name = _("Index Membership")
        verbose_name_plural = _("Index Membership")
        ordering = ['-trade_date', 'index_code', 'asset_id']
        indexes = [
            models.Index(fields=['index_code', 'trade_date']),
            models.Index(fields=['asset', 'index_code']),
        ]
        unique_together = ('asset', 'index_code', 'trade_date')

    def __str__(self):
        return f"{self.asset.ts_code} {self.index_code} {self.trade_date}"


class BenchmarkIndexDaily(models.Model):
    """
    Stores official daily benchmark index history used for backtest comparisons.
    """
    index_code = models.CharField(_("Index Code"), max_length=20, db_index=True)
    index_name = models.CharField(_("Index Name"), max_length=100)
    trade_date = models.DateField(_("Trade Date"), db_index=True)
    open = models.DecimalField(_("Open"), max_digits=12, decimal_places=4, null=True, blank=True)
    high = models.DecimalField(_("High"), max_digits=12, decimal_places=4, null=True, blank=True)
    low = models.DecimalField(_("Low"), max_digits=12, decimal_places=4, null=True, blank=True)
    close = models.DecimalField(_("Close"), max_digits=12, decimal_places=4)
    source = models.CharField(_("Source"), max_length=50, default='tushare_index_daily')

    class Meta:
        verbose_name = _("Benchmark Index Daily")
        verbose_name_plural = _("Benchmark Index Daily")
        ordering = ['-trade_date', 'index_code']
        indexes = [
            models.Index(fields=['index_code', 'trade_date']),
        ]
        unique_together = ('index_code', 'trade_date')

    def __str__(self):
        return f"{self.index_code} on {self.trade_date}"


class PointInTimeBenchmarkDaily(models.Model):
    """
    Stores the internal point-in-time union benchmark used by backtests.
    """
    benchmark_code = models.CharField(_("Benchmark Code"), max_length=40, db_index=True)
    benchmark_name = models.CharField(_("Benchmark Name"), max_length=120)
    trade_date = models.DateField(_("Trade Date"), db_index=True)
    daily_return = models.DecimalField(_("Daily Return"), max_digits=12, decimal_places=8, default=0)
    nav = models.DecimalField(_("Net Asset Value"), max_digits=20, decimal_places=8)
    constituent_count = models.PositiveIntegerField(_("Constituent Count"), default=0)
    overlap_count = models.PositiveIntegerField(_("Overlap Count"), default=0)
    weighting_method = models.CharField(_("Weighting Method"), max_length=60, default='free_float_market_cap')
    metadata = models.JSONField(_("Metadata"), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Point In Time Benchmark Daily")
        verbose_name_plural = _("Point In Time Benchmark Daily")
        ordering = ['-trade_date', 'benchmark_code']
        indexes = [
            models.Index(fields=['benchmark_code', 'trade_date']),
        ]
        unique_together = ('benchmark_code', 'trade_date')

    def __str__(self):
        return f"{self.benchmark_code} on {self.trade_date}"

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
