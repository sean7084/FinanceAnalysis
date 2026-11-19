from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from apps.analytics.indicator_params import canonical_param_key
from apps.markets.models import Asset


class TechnicalIndicator(models.Model):
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='indicators',
        verbose_name=_('Asset'),
        # The standalone (asset_id) index is a strict prefix of the hot
        # (asset_id, timestamp, indicator_type) index, which serves every asset-only
        # lookup and the FK cascade just as well.
        db_index=False,
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
        max_digits=24,
        decimal_places=8
    )
    parameters = models.JSONField(
        _('Parameters'),
        default=dict,
        blank=True
    )
    # Uniqueness moves here from ``parameters``: a jsonb key that contains BBANDS' own
    # computed outputs is unique per row, so it cannot enforce idempotency. Nullable with
    # no default on purpose -- NULL marks "not yet backfilled", which a constant default
    # would erase (PG11+ serves it from attmissingval for pre-existing rows).
    # Stays nullable on purpose: NULL is the "not yet backfilled" marker that makes the
    # backfill resumable and testable. save() and both bulk_create choke points always
    # populate it. The unique constraint is NULLS DISTINCT (the default), so pre-backfill
    # NULL keys coexist -- SMA/EMA legitimately hold six variants per (asset, timestamp).
    # A NOT NULL flip would add a full-scan validation and make the pre-backfill state
    # impossible to construct in tests.
    param_key = models.CharField(
        _('Parameter Key'),
        max_length=120,
        null=True,
        blank=True,
        help_text=_('Canonical input-parameter key; excludes computed outputs (BBANDS, FIB_RET).'),
    )

    class Meta:
        verbose_name = _('Technical Indicator')
        verbose_name_plural = _('Technical Indicators')
        # No default ordering: it silently sorted every queryset that did not override it,
        # adding a full Sort node over a 173M-row table. Every production reader orders
        # explicitly; the screener's MACD list carries its own order_by to preserve the
        # response order this used to supply.
        ordering = []
        indexes = []
        # Uniqueness lives on the canonical input key, not on the jsonb payload: a payload
        # that contains its own computed outputs (BBANDS, FIB_RET) is unique per row and
        # cannot enforce idempotency. The database object is a covering index with
        # INCLUDE (value) -- see migration 0015 for why the column order is what it is.
        constraints = [
            models.UniqueConstraint(
                fields=('asset', 'indicator_type', 'timestamp', 'param_key'),
                name='ti_session_variant_uniq',
            ),
        ]

    def save(self, *args, **kwargs):
        # Derived, never caller-supplied: every get_or_create writer routes through here,
        # so the key cannot drift from the payload. bulk_create bypasses save() and must
        # set it explicitly at the construction site.
        self.param_key = canonical_param_key(self.indicator_type, self.parameters)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.asset.symbol} - {self.indicator_type} ({timezone.localtime(self.timestamp).date()}): {self.value}"


class ScreenerTemplate(models.Model):
    class ScreenerType(models.TextChoices):
        PREBUILT = 'PREBUILT', _('Prebuilt')
        CUSTOM = 'CUSTOM', _('Custom')

    name = models.CharField(_('Name'), max_length=120)
    description = models.TextField(_('Description'), blank=True)
    screener_type = models.CharField(
        _('Screener Type'),
        max_length=20,
        choices=ScreenerType.choices,
        default=ScreenerType.CUSTOM,
    )
    config = models.JSONField(_('Config'), default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Screener Template')
        verbose_name_plural = _('Screener Templates')
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['screener_type']),
        ]

    def __str__(self):
        return self.name


class AlertRule(models.Model):
    class ConditionType(models.TextChoices):
        PRICE_ABOVE = 'PRICE_ABOVE', _('Price Above')
        PRICE_BELOW = 'PRICE_BELOW', _('Price Below')
        INDICATOR_ABOVE = 'INDICATOR_ABOVE', _('Indicator Above')
        INDICATOR_BELOW = 'INDICATOR_BELOW', _('Indicator Below')
        FEED_STALE = 'FEED_STALE', _('Feed Stale')
        # Live-versus-backtest drift. Asset-less like FEED_STALE, and evaluated by the ETF
        # forward-track report rather than the five-minute price loop.
        LIVE_DRIFT = 'LIVE_DRIFT', _('Live Drift')

    # Nullable for conditions that are about a data feed rather than one tradable asset
    # (currently FEED_STALE). Price/indicator rules still name an asset; the dispatch and
    # evaluation paths tolerate ``None`` so the same email/websocket plumbing is reused.
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='alert_rules',
        verbose_name=_('Asset'),
        null=True,
        blank=True,
    )
    name = models.CharField(_('Name'), max_length=120)
    condition_type = models.CharField(
        _('Condition Type'),
        max_length=30,
        choices=ConditionType.choices,
    )
    indicator_type = models.CharField(
        _('Indicator Type'),
        max_length=50,
        blank=True,
        help_text=_('Required for indicator based alerts, e.g. RSI or MACD'),
    )
    threshold = models.DecimalField(
        _('Threshold'),
        max_digits=18,
        decimal_places=8,
    )
    custom_condition = models.JSONField(_('Custom Condition'), default=dict, blank=True)
    channels = models.JSONField(
        _('Notification Channels'),
        default=list,
        help_text=_('Allowed channels: email, sms, websocket'),
    )
    cooldown_minutes = models.PositiveIntegerField(_('Cooldown Minutes'), default=60)
    is_active = models.BooleanField(_('Is Active'), default=True)
    last_triggered_at = models.DateTimeField(_('Last Triggered At'), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Alert Rule')
        verbose_name_plural = _('Alert Rules')
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['asset', 'is_active']),
        ]

    def __str__(self):
        return self.name


class AlertEvent(models.Model):
    class Status(models.TextChoices):
        TRIGGERED = 'TRIGGERED', _('Triggered')
        SENT = 'SENT', _('Sent')
        FAILED = 'FAILED', _('Failed')

    alert_rule = models.ForeignKey(
        AlertRule,
        on_delete=models.CASCADE,
        related_name='events',
        verbose_name=_('Alert Rule'),
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='alert_events',
        verbose_name=_('Asset'),
        null=True,
        blank=True,
    )
    status = models.CharField(
        _('Status'),
        max_length=20,
        choices=Status.choices,
        default=Status.TRIGGERED,
    )
    trigger_value = models.DecimalField(
        _('Trigger Value'),
        max_digits=18,
        decimal_places=8,
        null=True,
        blank=True,
    )
    message = models.TextField(_('Message'))
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    dispatched_channels = models.JSONField(_('Dispatched Channels'), default=list, blank=True)
    notified_at = models.DateTimeField(_('Notified At'), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Alert Event')
        verbose_name_plural = _('Alert Events')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['alert_rule', 'created_at']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        subject = self.asset.symbol if self.asset_id else 'feed'
        return f"{self.alert_rule.name} - {subject} ({self.created_at})"


class SignalEvent(models.Model):
    """
    Stores technical signal events generated by Phase 10 indicator analysis.
    Signals include golden/death crosses, Bollinger Band breakouts, volume spikes,
    momentum signals, and combined reversal conditions.
    """
    class SignalType(models.TextChoices):
        # Moving average signals
        GOLDEN_CROSS = 'GOLDEN_CROSS', _('Golden Cross (MA5 crosses above MA20)')
        DEATH_CROSS = 'DEATH_CROSS', _('Death Cross (MA5 crosses below MA20)')
        MA_BULL_ALIGN = 'MA_BULL_ALIGN', _('Bull MA Alignment (MA5 > MA10 > MA20 > MA60)')
        MA_BEAR_ALIGN = 'MA_BEAR_ALIGN', _('Bear MA Alignment (MA5 < MA10 < MA20 < MA60)')
        # Bollinger Band signals
        BB_SQUEEZE = 'BB_SQUEEZE', _('Bollinger Band Squeeze (Low Volatility)')
        BB_BREAKOUT_UP = 'BB_BREAKOUT_UP', _('Price Breakout Above Upper Band')
        BB_BREAKOUT_DOWN = 'BB_BREAKOUT_DOWN', _('Price Breakout Below Lower Band')
        BB_RSI_OVERBOUGHT = 'BB_RSI_OVERBOUGHT', _('Overbought: Upper Band + RSI > 70')
        BB_RSI_OVERSOLD = 'BB_RSI_OVERSOLD', _('Oversold: Lower Band + RSI < 30')
        # Volume signals
        VOLUME_SPIKE = 'VOLUME_SPIKE', _('Volume Spike (> 2x 20-day Average)')
        VOLUME_PRICE_DIVERGENCE = 'VOLUME_PRICE_DIVERGENCE', _('Volume-Price Divergence')
        # Momentum signals
        MOMENTUM_UP_5D = 'MOMENTUM_UP_5D', _('Strong 5-Day Upward Momentum (> +5%)')
        MOMENTUM_DOWN_5D = 'MOMENTUM_DOWN_5D', _('Strong 5-Day Downward Momentum (< -5%)')
        HIGH_RS_SCORE = 'HIGH_RS_SCORE', _('High Relative Strength Score (Top 20%)')
        # Reversal signals
        OVERSOLD_COMBINATION = 'OVERSOLD_COMBINATION', _('Oversold: RSI + Lower BB + Volume Contraction')

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='signal_events',
        verbose_name=_('Asset'),
    )
    signal_type = models.CharField(
        _('Signal Type'),
        max_length=50,
        choices=SignalType.choices,
        db_index=True,
    )
    timestamp = models.DateTimeField(_('Timestamp'), db_index=True)
    description = models.TextField(_('Description'), blank=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Signal Event')
        verbose_name_plural = _('Signal Events')
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['asset', 'timestamp', 'signal_type']),
            models.Index(fields=['signal_type', 'timestamp']),
        ]
        unique_together = ('asset', 'timestamp', 'signal_type')

    def __str__(self):
        return f"{self.asset.symbol} - {self.signal_type} ({timezone.localtime(self.timestamp).date()})"
