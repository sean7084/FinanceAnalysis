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
    total_share = models.DecimalField(_('Total Share'), max_digits=20, decimal_places=4, null=True, blank=True)
    float_share = models.DecimalField(_('Float Share'), max_digits=20, decimal_places=4, null=True, blank=True)
    free_share = models.DecimalField(_('Free Float Share'), max_digits=20, decimal_places=4, null=True, blank=True)
    total_mv = models.DecimalField(_('Total Market Value'), max_digits=20, decimal_places=4, null=True, blank=True)
    circ_mv = models.DecimalField(_('Circulating Market Value'), max_digits=20, decimal_places=4, null=True, blank=True)
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


class AssetMoneyFlowSnapshot(models.Model):
    """Raw per-stock daily money flow snapshot from TuShare moneyflow."""
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='money_flow_snapshots',
        verbose_name=_('Asset'),
    )
    date = models.DateField(_('Date'), db_index=True)
    buy_sm_amount = models.DecimalField(_('Small Buy Amount'), max_digits=18, decimal_places=4, null=True, blank=True)
    sell_sm_amount = models.DecimalField(_('Small Sell Amount'), max_digits=18, decimal_places=4, null=True, blank=True)
    buy_md_amount = models.DecimalField(_('Medium Buy Amount'), max_digits=18, decimal_places=4, null=True, blank=True)
    sell_md_amount = models.DecimalField(_('Medium Sell Amount'), max_digits=18, decimal_places=4, null=True, blank=True)
    buy_lg_amount = models.DecimalField(_('Large Buy Amount'), max_digits=18, decimal_places=4, null=True, blank=True)
    sell_lg_amount = models.DecimalField(_('Large Sell Amount'), max_digits=18, decimal_places=4, null=True, blank=True)
    buy_elg_amount = models.DecimalField(_('Extra Large Buy Amount'), max_digits=18, decimal_places=4, null=True, blank=True)
    sell_elg_amount = models.DecimalField(_('Extra Large Sell Amount'), max_digits=18, decimal_places=4, null=True, blank=True)
    net_mf_amount = models.DecimalField(_('Net Main Flow Amount'), max_digits=18, decimal_places=4, null=True, blank=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Asset Money Flow Snapshot')
        verbose_name_plural = _('Asset Money Flow Snapshots')
        unique_together = ('asset', 'date')
        indexes = [
            models.Index(fields=['asset', 'date']),
            models.Index(fields=['date']),
        ]


class AssetMarginDetailSnapshot(models.Model):
    """Raw per-stock daily margin detail snapshot from TuShare margin_detail."""
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='margin_detail_snapshots',
        verbose_name=_('Asset'),
    )
    date = models.DateField(_('Date'), db_index=True)
    rzye = models.DecimalField(_('Financing Balance'), max_digits=20, decimal_places=4, null=True, blank=True)
    rqye = models.DecimalField(_('Securities Lending Balance'), max_digits=20, decimal_places=4, null=True, blank=True)
    rzmre = models.DecimalField(_('Financing Buy Amount'), max_digits=20, decimal_places=4, null=True, blank=True)
    rzche = models.DecimalField(_('Financing Repayment Amount'), max_digits=20, decimal_places=4, null=True, blank=True)
    rqyl = models.DecimalField(_('Securities Lending Volume'), max_digits=20, decimal_places=4, null=True, blank=True)
    rqchl = models.DecimalField(_('Securities Lending Repayment Volume'), max_digits=20, decimal_places=4, null=True, blank=True)
    rqmcl = models.DecimalField(_('Securities Lending Sell Volume'), max_digits=20, decimal_places=4, null=True, blank=True)
    rzrqye = models.DecimalField(_('Margin Balance'), max_digits=20, decimal_places=4, null=True, blank=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Asset Margin Detail Snapshot')
        verbose_name_plural = _('Asset Margin Detail Snapshots')
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
