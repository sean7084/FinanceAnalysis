from django.db import models
from django.contrib.auth.models import User
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


class ScreenerTemplate(models.Model):
    class ScreenerType(models.TextChoices):
        PREBUILT = 'PREBUILT', _('Prebuilt')
        CUSTOM = 'CUSTOM', _('Custom')

    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='screener_templates',
        verbose_name=_('Owner'),
        null=True,
        blank=True,
    )
    name = models.CharField(_('Name'), max_length=120)
    description = models.TextField(_('Description'), blank=True)
    screener_type = models.CharField(
        _('Screener Type'),
        max_length=20,
        choices=ScreenerType.choices,
        default=ScreenerType.CUSTOM,
    )
    config = models.JSONField(_('Config'), default=dict)
    is_public = models.BooleanField(_('Is Public'), default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Screener Template')
        verbose_name_plural = _('Screener Templates')
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['owner', 'is_public']),
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

    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='alert_rules',
        verbose_name=_('Owner'),
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='alert_rules',
        verbose_name=_('Asset'),
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
            models.Index(fields=['owner', 'is_active']),
            models.Index(fields=['asset', 'is_active']),
        ]

    def __str__(self):
        return f"{self.owner.username} - {self.name}"


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
        return f"{self.alert_rule.name} - {self.asset.symbol} ({self.created_at})"


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
        return f"{self.asset.symbol} - {self.signal_type} ({self.timestamp.date()})"
