from django.db import models
from django.utils.translation import gettext_lazy as _


def default_defensive_asset():
    """The cash-substitute ETF, resolved from settings at row-creation time.

    A plain module constant would freeze whatever ``ETF_DEFENSIVE_ASSET`` was when the
    process imported this module, and a migration-time import of models.py is a real
    context in which settings may not be what the deployer configured. ``rotation_engine``
    and ``backtest_engine`` read the same setting; this is the fourth copy of the default
    collapsed onto it.
    """
    from django.conf import settings

    return str(getattr(settings, 'ETF_DEFENSIVE_ASSET', '511880.SH'))


class ETFCategory(models.TextChoices):
    """Classification buckets used to keep the rotation pool diverse.

    Derived from TuShare's ``invest_type`` + ``benchmark`` + ``name`` -- see
    ``etf_universe.classify_etf``. TuShare's ``fund_type`` column has no
    concept of index/QDII/commodity, so it cannot be the primary signal.
    """
    BROAD = 'broad', _('Broad Based')
    SECTOR_TECH = 'sector_tech', _('Sector Technology')
    SECTOR_FINANCE = 'sector_finance', _('Sector Finance')
    SECTOR_CONSUMER = 'sector_consumer', _('Sector Consumer')
    SECTOR_MEDICAL = 'sector_medical', _('Sector Medical')
    SECTOR_INDUSTRIAL = 'sector_industrial', _('Sector Industrial')
    SECTOR_ENERGY = 'sector_energy', _('Sector Energy')
    SECTOR_MATERIAL = 'sector_material', _('Sector Material')
    STYLE_DIVIDEND = 'style_dividend', _('Style Dividend')
    STYLE_GROWTH = 'style_growth', _('Style Growth')
    COMMODITY = 'commodity', _('Commodity')
    BOND = 'bond', _('Bond')
    QDII = 'qdii', _('QDII')
    MONEY = 'money', _('Money Market')
    OTHER = 'other', _('Other')


class ETFUniverse(models.Model):
    """Exchange-listed fund (ETF) master data, synced from TuShare ``fund_basic(market='E')``.

    Delisted funds are kept. A backtest that only knows about today's survivors
    scores momentum on the funds that happened to work out, so ``status`` is
    never used to filter what gets stored -- eligibility over time is decided
    at query time from ``list_date``/``delist_date`` instead.
    """
    class Status(models.TextChoices):
        LISTED = 'L', _('Listed')
        DELISTED = 'D', _('Delisted')
        UNKNOWN = '', _('Unknown')

    ts_code = models.CharField(_('TS Code'), max_length=20, unique=True)
    symbol = models.CharField(_('Symbol'), max_length=10, db_index=True)
    name = models.CharField(_('Name'), max_length=100)
    fund_type = models.CharField(
        _('Fund Type'), max_length=50, blank=True,
        help_text=_("TuShare fund_type: 股票型/混合型/债券型/REITs/其他/货币型 -- no index/QDII/commodity value."),
    )
    invest_type = models.CharField(
        _('Invest Type'), max_length=50, blank=True,
        help_text=_("TuShare invest_type: 被动指数型/增强指数型/货币型/黄金现货合约/...; blank for many funds."),
    )
    benchmark = models.CharField(
        _('Benchmark'), max_length=200, blank=True,
        help_text=_("TuShare benchmark -- the tracked index description this module treats as track_index."),
    )
    management = models.CharField(_('Management Company'), max_length=100, blank=True)
    custodian = models.CharField(_('Custodian Bank'), max_length=100, blank=True)
    list_date = models.DateField(_('List Date'), null=True, blank=True)
    delist_date = models.DateField(_('Delist Date'), null=True, blank=True)
    first_trade_date = models.DateField(
        _('First Trade Date'), null=True, blank=True,
        help_text=_("Earliest ETFDaily date. Fallback for list_date, which TuShare leaves blank on some funds."),
    )
    status = models.CharField(_('Status'), max_length=2, choices=Status.choices, blank=True, default='')
    is_active = models.BooleanField(_('Is Active'), default=True, db_index=True)
    is_rankable = models.BooleanField(
        _('Is Rankable'), default=True, db_index=True,
        help_text=_(
            'False for stubs the daily sync created for a ts_code fund_basic(market="E") never '
            'returned, and for on-exchange vehicles that are not ETFs (REITs, LOFs, active funds). '
            'The rotation pool filters on this; it is never derived from ``status``, which would '
            ' reintroduce survivorship bias.'
        ),
    )
    category = models.CharField(
        _('Category'), max_length=30, choices=ETFCategory.choices, default=ETFCategory.OTHER, db_index=True,
    )
    sub_category = models.CharField(_('Sub Category'), max_length=50, blank=True)
    track_index_key = models.CharField(
        _('Track Index Key'), max_length=100, blank=True, db_index=True,
        help_text=_(
            'Normalised tracked-index string derived from ``benchmark`` by '
            '``etf_universe.normalize_track_index``. Two funds sharing a key track the same '
            'index, so this is the axis the rotation pool deduplicates on -- ``category`` is '
            'too coarse and raw ``benchmark`` is too finely decorated to be one.'
        ),
    )
    shares_outstanding = models.DecimalField(
        _('Shares Outstanding'), max_digits=20, decimal_places=4, null=True, blank=True,
        help_text=_('Total fund shares in 份, from TuShare ``fund_share.fd_share`` (万份 x 10000).'),
    )
    share_date = models.DateField(
        _('Share Date'), null=True, blank=True,
        help_text=_('The ``trade_date`` ``shares_outstanding`` was read at. It is a period stamp.'),
    )
    m_fee = models.DecimalField(_('Management Fee'), max_digits=8, decimal_places=4, null=True, blank=True)
    c_fee = models.DecimalField(_('Custody Fee'), max_digits=8, decimal_places=4, null=True, blank=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('ETF Universe')
        verbose_name_plural = _('ETF Universe')
        ordering = ['ts_code']
        indexes = [
            models.Index(fields=['category', 'is_active']),
            models.Index(fields=['list_date']),
        ]

    def __str__(self):
        return f"{self.ts_code} {self.name}"

    @property
    def effective_list_date(self):
        """Listing date to age against, with the missing-``list_date`` fallback.

        TuShare leaves ``list_date`` blank on some funds, which would otherwise
        drop them out of the eligibility filter for reasons that have nothing to
        do with how old the fund is.
        """
        return self.list_date or self.first_trade_date


class ETFIndexMapping(models.Model):
    """Which index an ETF tracks, and how confidently that was resolved.

    ``ETFUniverse.track_index_key`` is free text lifted from the fund's own prospectus
    (``沪深300指数+中信标普全债``), so it cannot be joined to ``index_dailybasic``. This table
    holds the resolved code together with the evidence: the benchmark string that was
    matched, whether the match covers the whole benchmark or only its equity leg, and the
    source that produced it. A missing row means "not resolved", never "no index".
    """

    class Confidence(models.TextChoices):
        EXACT = 'exact', _('Exact')
        PARTIAL = 'partial', _('Partial')

    etf = models.OneToOneField(
        ETFUniverse, on_delete=models.CASCADE, related_name='index_mapping', verbose_name=_('ETF'),
    )
    index_code = models.CharField(_('Index Code'), max_length=20, db_index=True)
    index_name = models.CharField(_('Index Name'), max_length=100, blank=True)
    confidence = models.CharField(
        _('Confidence'), max_length=16, choices=Confidence.choices, default=Confidence.EXACT,
    )
    benchmark_text = models.TextField(_('Benchmark Text'), blank=True)
    source = models.CharField(_('Source'), max_length=40, blank=True)
    updated_at = models.DateTimeField(_('Updated At'), auto_now=True)

    class Meta:
        verbose_name = _('ETF Index Mapping')
        verbose_name_plural = _('ETF Index Mappings')

    def __str__(self):
        return f'{self.etf_id} -> {self.index_code} ({self.confidence})'


class IndexValuationDaily(models.Model):
    """Daily valuation of an index an ETF tracks (TuShare ``index_dailybasic``).

    Published after the close, so a feature built from it may only use **D-1** and earlier.
    Only ``pe_ttm``, ``pb``, ``turnover_rate`` and the market-value columns are available
    here; TuShare does not publish a dividend yield on this endpoint, which is why the
    feature set in ``features.py`` does not claim one.
    """

    index_code = models.CharField(_('Index Code'), max_length=20, db_index=True)
    date = models.DateField(_('Date'), db_index=True)
    pe_ttm = models.FloatField(_('PE TTM'), null=True, blank=True)
    pb = models.FloatField(_('PB'), null=True, blank=True)
    turnover_rate = models.FloatField(_('Turnover Rate'), null=True, blank=True)
    total_mv = models.FloatField(_('Total Market Value'), null=True, blank=True)
    float_mv = models.FloatField(_('Float Market Value'), null=True, blank=True)
    source = models.CharField(_('Source'), max_length=40, blank=True, default='tushare:index_dailybasic')

    class Meta:
        verbose_name = _('Index Valuation Daily')
        verbose_name_plural = _('Index Valuation Daily')
        unique_together = ('index_code', 'date')
        indexes = [models.Index(fields=['index_code', 'date'])]

    def __str__(self):
        return f'{self.index_code} {self.date}'


class IndexWeightSnapshot(models.Model):
    """Monthly constituent weights of an index (TuShare ``index_weight``).

    The only constituent-level fact available for the mapped indices. Publication is monthly
    and after the close, so a breadth feature reads the latest snapshot at or before **D-1**
    and never a future one.
    """

    index_code = models.CharField(_('Index Code'), max_length=20, db_index=True)
    con_code = models.CharField(_('Constituent Code'), max_length=20, db_index=True)
    trade_date = models.DateField(_('Trade Date'), db_index=True)
    weight = models.FloatField(_('Weight'), null=True, blank=True)
    source = models.CharField(_('Source'), max_length=40, blank=True, default='tushare:index_weight')

    class Meta:
        verbose_name = _('Index Weight Snapshot')
        verbose_name_plural = _('Index Weight Snapshots')
        unique_together = ('index_code', 'con_code', 'trade_date')
        indexes = [models.Index(fields=['index_code', 'trade_date'])]

    def __str__(self):
        return f'{self.index_code} {self.con_code} {self.trade_date}'


class ETFShareDaily(models.Model):
    """Daily fund shares outstanding (TuShare ``fund_share``), the basis of flow features.

    ``ETFUniverse.shares_outstanding`` is a single period stamp, so a share *change* -- the
    creation/redemption flow that is specific to ETFs -- needs this history. Published with a
    one-session lag, so a feature may only use D-1.
    """

    etf = models.ForeignKey(
        ETFUniverse, on_delete=models.CASCADE, related_name='share_history', verbose_name=_('ETF'),
    )
    date = models.DateField(_('Date'))
    shares = models.FloatField(_('Shares'), null=True, blank=True,
                               help_text=_('Fund shares outstanding, in 10k units as TuShare reports it.'))
    source = models.CharField(_('Source'), max_length=40, blank=True, default='tushare:fund_share')

    class Meta:
        verbose_name = _('ETF Share Daily')
        verbose_name_plural = _('ETF Share Daily')
        unique_together = ('etf', 'date')
        indexes = [models.Index(fields=['etf', 'date'])]

    def __str__(self):
        return f'{self.etf_id} {self.date}'


class ETFDaily(models.Model):
    """Daily ETF bars from TuShare ``fund_daily`` plus ``fund_adj`` adjustment factors.

    TuShare reports fund volume in 手 (100 份) and amount in 千元. Both are
    converted on ingest so this table matches ``markets.OHLCV``'s 元 convention;
    ``test_unit_conversion_matches_vwap`` locks that conversion down.
    """
    etf = models.ForeignKey(
        ETFUniverse, on_delete=models.CASCADE, related_name='daily_data', verbose_name=_('ETF'),
    )
    date = models.DateField(_('Date'))
    open = models.DecimalField(_('Open'), max_digits=12, decimal_places=4)
    high = models.DecimalField(_('High'), max_digits=12, decimal_places=4)
    low = models.DecimalField(_('Low'), max_digits=12, decimal_places=4)
    close = models.DecimalField(_('Close'), max_digits=12, decimal_places=4)
    adj_close = models.DecimalField(_('Adjusted Close'), max_digits=12, decimal_places=4)
    volume = models.BigIntegerField(_('Volume'), help_text=_('Traded volume, in 份 (TuShare 手 x 100).'))
    amount = models.DecimalField(
        _('Amount'), max_digits=20, decimal_places=4,
        help_text=_('Turnover amount, in 元 (TuShare 千元 x 1000).'),
    )
    adj_factor = models.DecimalField(
        _('Adjustment Factor'), max_digits=12, decimal_places=6, default=1,
        help_text=_('Raw fund_adj factor; adj_close = close * adj_factor.'),
    )

    class Meta:
        verbose_name = _('ETF Daily')
        verbose_name_plural = _('ETF Daily')
        ordering = ['-date']
        indexes = [
            models.Index(fields=['etf', 'date']),
            models.Index(fields=['date']),
        ]
        unique_together = ('etf', 'date')

    def __str__(self):
        return f"{self.etf_id} on {self.date}"


class ETFFeatureSnapshot(models.Model):
    """One row per (ETF, date) holding the point-in-time feature vector.

    Every ``*_nd`` feature is computed from bars up to and including ``date``.
    The three ``label_*``/``fwd_return`` columns are the opposite -- they look
    forward -- and exist here only so building a training frame is a single
    query. ``features.FEATURE_COLUMNS`` is the whitelist the models read from;
    nothing may infer feature membership from ``_meta.fields``.
    """
    etf = models.ForeignKey(
        ETFUniverse, on_delete=models.CASCADE, related_name='features', verbose_name=_('ETF'),
    )
    date = models.DateField(_('Date'), db_index=True)

    mom_5d = models.FloatField(_('Momentum 5D'), null=True, blank=True)
    mom_10d = models.FloatField(_('Momentum 10D'), null=True, blank=True)
    mom_20d = models.FloatField(_('Momentum 20D'), null=True, blank=True)
    mom_60d = models.FloatField(_('Momentum 60D'), null=True, blank=True)
    mom_120d = models.FloatField(_('Momentum 120D'), null=True, blank=True)
    mom_250d = models.FloatField(_('Momentum 250D'), null=True, blank=True)
    mom_accel = models.FloatField(_('Momentum Acceleration'), null=True, blank=True)

    vol_20d = models.FloatField(_('Volatility 20D'), null=True, blank=True)
    vol_60d = models.FloatField(_('Volatility 60D'), null=True, blank=True)
    sharpe_20d = models.FloatField(_('Sharpe 20D'), null=True, blank=True)
    sharpe_60d = models.FloatField(_('Sharpe 60D'), null=True, blank=True)

    rs_vs_benchmark_20d = models.FloatField(_('Relative Strength 20D'), null=True, blank=True)
    rs_vs_benchmark_60d = models.FloatField(_('Relative Strength 60D'), null=True, blank=True)
    corr_with_benchmark_60d = models.FloatField(_('Correlation With Benchmark 60D'), null=True, blank=True)
    rank_pct_20d = models.FloatField(_('Cross-Section Rank Pct 20D'), null=True, blank=True)
    rank_pct_60d = models.FloatField(_('Cross-Section Rank Pct 60D'), null=True, blank=True)

    rsi_14 = models.FloatField(_('RSI 14'), null=True, blank=True)
    ma5_ratio = models.FloatField(_('MA5 Ratio'), null=True, blank=True)
    ma20_ratio = models.FloatField(_('MA20 Ratio'), null=True, blank=True)
    ma60_ratio = models.FloatField(_('MA60 Ratio'), null=True, blank=True)
    bollinger_pos = models.FloatField(_('Bollinger Position'), null=True, blank=True)
    drawdown_from_high_60d = models.FloatField(_('Drawdown From 60D High'), null=True, blank=True)

    idx_pe_pct_3y = models.FloatField(
        _('Index PE Percentile 3Y'), null=True, blank=True,
        help_text=_('Percentile of the tracked index PE(TTM) in its own 3-year history, as of D-1.'),
    )
    idx_pb_pct_3y = models.FloatField(
        _('Index PB Percentile 3Y'), null=True, blank=True,
        help_text=_('Percentile of the tracked index PB in its own 3-year history, as of D-1.'),
    )
    idx_turnover_z = models.FloatField(
        _('Index Turnover Z'), null=True, blank=True,
        help_text=_('Z-score of the tracked index turnover rate against its own 250-session mean.'),
    )
    share_growth_5d = models.FloatField(
        _('Share Growth 5D'), null=True, blank=True,
        help_text=_('Five-session change in fund shares outstanding, as of D-1.'),
    )
    share_growth_20d = models.FloatField(
        _('Share Growth 20D'), null=True, blank=True,
        help_text=_('Twenty-session change in fund shares outstanding, as of D-1.'),
    )
    flow_x_mom = models.FloatField(
        _('Flow x Momentum'), null=True, blank=True,
        help_text=_('Share growth over 20 sessions times the fund 20-session momentum.'),
    )
    has_share_flow = models.BooleanField(
        _('Has Share Flow'), null=True, blank=True,
        help_text=_('Whether share history existed for this fund on this date.'),
    )
    has_index_valuation = models.BooleanField(
        _('Has Index Valuation'), null=True, blank=True,
        help_text=_('Whether the ETF resolved to an index with valuation history on this date. '
                    'Carried so coverage bias stays measurable.'),
    )
    idx_share_above_ma60 = models.FloatField(
        _('Index Share Above MA60'), null=True, blank=True,
        help_text=_('Weighted share of the tracked index constituents above their 60-session '
                    'mean, as of D-1.'),
    )
    idx_ret_dispersion_20d = models.FloatField(
        _('Index Return Dispersion 20D'), null=True, blank=True,
        help_text=_('Weighted cross-sectional standard deviation of constituent 20-session '
                    'returns, as of D-1.'),
    )
    idx_mom_consistency = models.FloatField(
        _('Index Momentum Consistency'), null=True, blank=True,
        help_text=_('Weighted share of constituents whose 20-session return has the same sign '
                    'as the index mean, as of D-1.'),
    )
    has_index_breadth = models.BooleanField(
        _('Has Index Breadth'), null=True, blank=True,
        help_text=_('Whether the ETF resolved to an index with constituent breadth on this date.'),
    )
    avg_amount_20d = models.FloatField(_('Avg Amount 20D'), null=True, blank=True,
                                       help_text=_('Mean daily turnover over 20 sessions, in 元.'))
    volume_ratio_5d_20d = models.FloatField(_('Volume Ratio 5D/20D'), null=True, blank=True)

    is_eligible = models.BooleanField(
        _('Is Eligible'), default=False, db_index=True,
        help_text=_('Whether this ETF passed the pool filter on this date (point-in-time).'),
    )

    label_horizon_days = models.PositiveIntegerField(_('Label Horizon Days'), null=True, blank=True)
    fwd_return = models.FloatField(_('Forward Return'), null=True, blank=True,
                                   help_text=_('LOOK-ONLY FOR LABELS. Returns over label_horizon_days after date.'))
    label_rank_pct = models.FloatField(_('Label Rank Pct'), null=True, blank=True,
                                       help_text=_('LOOK-ONLY FOR LABELS. Forward return percentile within the pool.'))
    fwd_max_drawdown = models.FloatField(
        _('Forward Max Drawdown'), null=True, blank=True,
        help_text=_('LOOK-ONLY FOR LABELS. Largest peak-to-trough fall over the horizon after '
                    'date, as a positive magnitude.'),
    )
    fwd_downside_deviation = models.FloatField(
        _('Forward Downside Deviation'), null=True, blank=True,
        help_text=_('LOOK-ONLY FOR LABELS. Root-mean-square of negative forward daily returns '
                    'over the horizon.'),
    )
    label_dd_rank = models.FloatField(
        _('Label Drawdown Rank Pct'), null=True, blank=True,
        help_text=_('LOOK-ONLY FOR LABELS. Per-date percentile of -fwd_max_drawdown: the '
                    'safest fund in the pool ranks 1.0.'),
    )
    label_downside_rank = models.FloatField(
        _('Label Downside Rank Pct'), null=True, blank=True,
        help_text=_('LOOK-ONLY FOR LABELS. Per-date percentile of -fwd_downside_deviation: '
                    'the fund with the least downside risk in the pool ranks 1.0.'),
    )
    label_top_bucket = models.BooleanField(_('Label Top Bucket'), null=True, blank=True,
                                           help_text=_('LOOK-ONLY FOR LABELS. Forward return landed in the top 30%.'))

    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)

    class Meta:
        verbose_name = _('ETF Feature Snapshot')
        verbose_name_plural = _('ETF Feature Snapshots')
        ordering = ['-date', 'etf']
        indexes = [
            models.Index(fields=['etf', 'date']),
            models.Index(fields=['date', 'is_eligible']),
        ]
        unique_together = ('etf', 'date')

    def __str__(self):
        return f"{self.etf_id} {self.date}"


class ETFRotationSignal(models.Model):
    """A generated rotation ranking and its resulting selection for one date."""
    class SignalType(models.TextChoices):
        WEEKLY = 'WEEKLY', _('Weekly')
        BIWEEKLY = 'BIWEEKLY', _('Bi-Weekly')
        MONTHLY = 'MONTHLY', _('Monthly')
        TRIGGER = 'TRIGGER', _('Trigger')
        # A parallel book whose only purpose is the ML rematch: an ML model is scored into
        # the same rotation rules and the ledger keeps its legs separate from production,
        # so forward evidence accrues without the production signal ever being replaced.
        # ``views`` hides this type unless a caller asks for it by name.
        SHADOW = 'SHADOW', _('Shadow (ML)')

    class ModelType(models.TextChoices):
        RULE = 'RULE', _('Rule')
        LIGHTGBM = 'LIGHTGBM', _('LightGBM')
        LSTM = 'LSTM', _('LSTM')
        ENSEMBLE = 'ENSEMBLE', _('Ensemble')

    # Unique on (date, signal_type): re-running a day must overwrite rather than
    # append, or "latest signal" becomes whichever duplicate row the ordering picked.
    date = models.DateField(_('Date'), db_index=True)
    signal_type = models.CharField(_('Signal Type'), max_length=10, choices=SignalType.choices, db_index=True)
    arm = models.CharField(
        _('Arm'), max_length=32, blank=True, default='', db_index=True,
        help_text=_('Which candidate book this signal belongs to: empty for the production book, '
                    'a short label ("lgb-veto") for a forward-observation arm. Two arms may '
                    'publish on the same date and cadence because its uniqueness key includes '
                    'this field.'),
    )
    model_type = models.CharField(_('Model Type'), max_length=10, choices=ModelType.choices,
                                  default=ModelType.RULE, db_index=True)
    rankings = models.JSONField(_('Rankings'), default=list, blank=True,
                                help_text=_('[{ts_code, name, score, rank, category}, ...]'))
    selected_etfs = models.JSONField(_('Selected ETFs'), default=list, blank=True,
                                     help_text=_('[{ts_code, name, weight, reason}, ...]'))
    defensive_allocation = models.FloatField(_('Defensive Allocation'), default=0.0,
                                             help_text=_('Fraction of capital parked in the defensive asset.'))
    defensive_asset = models.CharField(_('Defensive Asset'), max_length=20, blank=True)
    model_version = models.CharField(_('Model Version'), max_length=50, blank=True)
    benchmark_return_20d = models.FloatField(_('Benchmark Return 20D'), null=True, blank=True)
    max_positions = models.PositiveIntegerField(_('Max Positions'), default=3)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('ETF Rotation Signal')
        verbose_name_plural = _('ETF Rotation Signals')
        ordering = ['-date', 'signal_type']
        indexes = [
            models.Index(fields=['date', 'signal_type']),
            models.Index(fields=['model_type', 'date']),
        ]
        unique_together = ('date', 'signal_type', 'arm')

    def __str__(self):
        suffix = f' [{self.arm}]' if self.arm else ''
        return f"{self.get_signal_type_display()} {self.date}{suffix}"


class ETFRotationSignalRun(models.Model):
    """One attempt to produce a signal, or to prove the feed is healthy.

    ``ETFRotationSignal`` records what the strategy said; this records that the job *ran*.
    Without it a refused signal left no row anywhere -- ``tasks.py`` swallowed the error into
    a worker log -- so "the market told us to hold" and "nothing ran for two days" looked
    identical on the page. Rows are append-only on purpose: a refused attempt followed by a
    successful retry is two observations, not one row to overwrite.
    """
    class SignalType(models.TextChoices):
        WEEKLY = 'WEEKLY', _('Weekly')
        BIWEEKLY = 'BIWEEKLY', _('Bi-Weekly')
        MONTHLY = 'MONTHLY', _('Monthly')
        TRIGGER = 'TRIGGER', _('Trigger')
        HEALTH = 'HEALTH', _('Feed Health')

    class Outcome(models.TextChoices):
        OK = 'OK', _('Ok')
        REFUSED = 'REFUSED', _('Refused')
        FAILED = 'FAILED', _('Failed')
        FEED_STALE = 'FEED_STALE', _('Feed Stale')

    date = models.DateField(_('Date'), db_index=True,
                            help_text=_('As-of trading date the attempt was about.'))
    signal_type = models.CharField(_('Signal Type'), max_length=10,
                                   choices=SignalType.choices, db_index=True)
    outcome = models.CharField(_('Outcome'), max_length=12, choices=Outcome.choices,
                               db_index=True)
    reason_code = models.CharField(_('Reason Code'), max_length=40, blank=True)
    message = models.TextField(_('Message'), blank=True)
    diagnostics = models.JSONField(_('Diagnostics'), default=dict, blank=True)
    task_id = models.CharField(_('Task ID'), max_length=64, blank=True)
    duration_ms = models.PositiveIntegerField(_('Duration (ms)'), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('ETF Rotation Signal Run')
        verbose_name_plural = _('ETF Rotation Signal Runs')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['date', 'signal_type']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return f'{self.date} {self.signal_type} {self.outcome}'


class ETFRotationPosition(models.Model):
    """One holding leg, open or closed, attributable to the signal that created it."""
    class Status(models.TextChoices):
        OPEN = 'OPEN', _('Open')
        CLOSED = 'CLOSED', _('Closed')

    signal = models.ForeignKey(
        ETFRotationSignal, on_delete=models.SET_NULL, related_name='positions',
        null=True, blank=True, verbose_name=_('Signal'),
    )
    etf = models.ForeignKey(
        ETFUniverse, on_delete=models.CASCADE, related_name='positions', verbose_name=_('ETF'),
    )
    entry_date = models.DateField(_('Entry Date'))
    exit_date = models.DateField(_('Exit Date'), null=True, blank=True)
    entry_price = models.DecimalField(_('Entry Price'), max_digits=12, decimal_places=4)
    exit_price = models.DecimalField(_('Exit Price'), max_digits=12, decimal_places=4, null=True, blank=True)
    weight = models.FloatField(_('Weight'), help_text=_('Target portfolio weight, 0-1.'))
    shares = models.BigIntegerField(_('Shares'), null=True, blank=True)
    pnl = models.FloatField(_('PnL'), null=True, blank=True)
    return_pct = models.FloatField(_('Return Pct'), null=True, blank=True)
    status = models.CharField(_('Status'), max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    # Why a leg left the book. Today only the daily ledger writes this: a newer signal of
    # the same type no longer selects the fund. Kept as a column (not metadata) so closed
    # history can be grouped and counted without reading JSON.
    exit_reason = models.CharField(
        _('Exit Reason'), max_length=32, blank=True, default='',
        choices=[('DISPLACED_BY_SIGNAL', _('Displaced by signal'))],
        help_text=_('Empty while OPEN; the ledger sets it when the leg is closed.'),
    )
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('ETF Rotation Position')
        verbose_name_plural = _('ETF Rotation Positions')
        ordering = ['-entry_date']
        indexes = [
            models.Index(fields=['status', 'entry_date']),
            models.Index(fields=['etf', 'status']),
            # The ledger's two reads: which legs are still open, and the most recently
            # closed history the ledger page shows first.
            models.Index(fields=['status', 'exit_date']),
        ]

    def __str__(self):
        return f"{self.etf_id} {self.entry_date} {self.status}"


class ETFRotationBacktest(models.Model):
    """A parameterised rotation backtest and its result set."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        RUNNING = 'RUNNING', _('Running')
        # A paused row keeps its ``report['runtime_state']`` and resumes from the
        # cursor, so it needs a status distinct from PENDING to tell the operator
        # there is progress on the row worth continuing rather than starting over.
        PAUSED = 'PAUSED', _('Paused')
        COMPLETED = 'COMPLETED', _('Completed')
        FAILED = 'FAILED', _('Failed')

    class ControlAction(models.TextChoices):
        """An out-of-band request the engine honours at the next chunk boundary.

        Pause cannot be honoured mid-chunk: the engine only persists resumable
        state between chunks, so a control written during a chunk takes minutes
        to land. That is the same trade-off the stock backtest makes.
        """
        NONE = 'NONE', _('None')
        PAUSE = 'PAUSE', _('Pause')
        RESTART = 'RESTART', _('Restart')
        DELETE = 'DELETE', _('Delete')

    class ModelType(models.TextChoices):
        RULE = 'RULE', _('Rule')
        LIGHTGBM = 'LIGHTGBM', _('LightGBM')
        LSTM = 'LSTM', _('LSTM')
        ENSEMBLE = 'ENSEMBLE', _('Ensemble')

    class Weighting(models.TextChoices):
        EQUAL = 'equal', _('Equal Weight')
        SCORE = 'score', _('Score Weight')
        INV_VOL = 'invvol', _('Inverse Volatility Weight')

    class RebalanceFrequency(models.TextChoices):
        WEEKLY = 'WEEKLY', _('Weekly')
        BIWEEKLY = 'BIWEEKLY', _('Bi-Weekly')
        MONTHLY = 'MONTHLY', _('Monthly')
        TRIGGER = 'TRIGGER', _('Trigger')

    class LiquidityMode(models.TextChoices):
        REJECT = 'reject', _('Reject the Leg')
        SCALE = 'scale', _('Scale the Leg')

    name = models.CharField(_('Name'), max_length=120)
    status = models.CharField(_('Status'), max_length=20, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    start_date = models.DateField(_('Start Date'))
    end_date = models.DateField(_('End Date'))
    rebalance_frequency = models.CharField(_('Rebalance Frequency'), max_length=10,
                                           choices=RebalanceFrequency.choices, default=RebalanceFrequency.MONTHLY)
    max_positions = models.PositiveIntegerField(_('Max Positions'), default=3)
    # The grid sweeps lookback as well; without this column a run's ranking
    # horizon is unrecoverable and grid results cannot be attributed.
    momentum_lookback = models.PositiveIntegerField(_('Momentum Lookback'), default=60)
    defensive_asset = models.CharField(_('Defensive Asset'), max_length=20, default=default_defensive_asset)
    absolute_momentum_filter = models.BooleanField(_('Absolute Momentum Filter'), default=True)
    # Promoted out of ``parameters['weighting']`` for the same reason
    # ``momentum_lookback`` was: a grid dimension that only exists in a JSON blob
    # cannot be filtered or attributed in SQL. ``score`` is the default because
    # that is what the production RULE winner (row 82) is weighted with.
    weighting = models.CharField(_('Weighting'), max_length=10, choices=Weighting.choices,
                                 default=Weighting.SCORE)
    # The two selection-constraint knobs. They are columns rather than ``parameters`` JSON
    # keys for the same reason ``weighting`` is: a run created through the API is rebuilt
    # from its own columns by ``views._backtest_config_for``, so a knob that lives only in
    # the blob cannot round-trip and the duplicate-run digest silently disagrees.
    dedup_track_index = models.BooleanField(
        _('Dedup Track Index'), default=True,
        help_text=_('Hold at most one fund per tracked index (``ETFUniverse.track_index_key``).'),
    )
    max_per_category = models.PositiveIntegerField(
        _('Max Per Category'), default=2,
        help_text=_('Cap on how many names one ``category`` may contribute; 0 means no cap.'),
    )
    initial_capital = models.DecimalField(_('Initial Capital'), max_digits=18, decimal_places=2, default=480000)
    commission_rate = models.FloatField(_('Commission Rate'), default=0.0003,
                                        help_text=_('Two-way broker commission. ETF trades carry no stamp duty.'))
    min_commission = models.DecimalField(_('Min Commission'), max_digits=8, decimal_places=2, default=5)
    slippage = models.FloatField(_('Slippage'), default=0.001)
    liquidity_floor_multiple = models.FloatField(_('Liquidity Floor Multiple'), default=10.0,
                                                 help_text=_('Required daily turnover multiple of the trade notional.'))
    min_trade_notional_fraction = models.FloatField(
        _('Min Trade Notional Fraction'), default=0.005,
        help_text=_('Rebalance deltas below this share of equity are skipped rather than paying the commission floor.'),
    )
    drift_band_fraction = models.FloatField(
        _('Drift Band Fraction'), default=0.0,
        help_text=_('A delta below this share of its target notional is left to drift. 0 disables the band.'),
    )
    liquidity_mode = models.CharField(
        _('Liquidity Mode'), max_length=10, choices=LiquidityMode.choices,
        default=LiquidityMode.REJECT,
        help_text=_('Reject a leg whose notional breaches the turnover floor, or scale it to the ADV cap.'),
    )
    adv_participation_cap = models.FloatField(
        _('ADV Participation Cap'), default=0.0,
        help_text=_('Scale mode: leg notional may not exceed this multiple of trailing daily turnover. 0 disables.'),
    )
    # The exposure/sizing knobs used to live only in ``parameters``, which is enough for an
    # inline run but not for a queued one: the worker rebuilds its config from the row's own
    # columns, so a knob with no column silently re-defaults and a --queue call would run a
    # configuration the operator never asked for. They are columns for the same reason
    # ``weighting`` is. Defaults reproduce every pre-existing row exactly.
    kelly_fraction = models.FloatField(
        _('Kelly Fraction'), default=0.0,
        help_text=_('Fraction of the Kelly-optimal exposure used to cap the risk budget. 0 disables.'),
    )
    kelly_cap = models.FloatField(
        _('Kelly Cap'), default=1.0,
        help_text=_('Upper bound on the Kelly exposure scale; 1.0 never levers.'),
    )
    kelly_floor = models.FloatField(
        _('Kelly Floor'), default=0.0,
        help_text=_('Lower bound on the Kelly exposure scale.'),
    )
    kelly_lookback_trades = models.PositiveIntegerField(
        _('Kelly Lookback Trades'), default=60,
        help_text=_('Closed trades the win/payoff estimate is taken over.'),
    )
    cash_yield_annual = models.FloatField(
        _('Cash Yield Annual'), default=0.0,
        help_text=_('Annualised rate credited to cash the liquidity floor left idle. 0 reproduces the no-yield model.'),
    )

    model_type = models.CharField(_('Model Type'), max_length=10, choices=ModelType.choices,
                                  default=ModelType.RULE, db_index=True)
    walk_forward_steps = models.PositiveIntegerField(_('Walk-Forward Steps'), default=0)
    benchmark_code = models.CharField(_('Benchmark Code'), max_length=20, default='H00905.CSI')

    total_return = models.FloatField(_('Total Return'), null=True, blank=True)
    annualized_return = models.FloatField(_('Annualized Return'), null=True, blank=True)
    max_drawdown = models.FloatField(_('Max Drawdown'), null=True, blank=True)
    sharpe_ratio = models.FloatField(_('Sharpe Ratio'), null=True, blank=True)
    calmar_ratio = models.FloatField(_('Calmar Ratio'), null=True, blank=True)
    win_rate = models.FloatField(_('Win Rate'), null=True, blank=True)
    turnover_annual = models.FloatField(_('Annual Turnover'), null=True, blank=True)
    alpha_vs_benchmark = models.FloatField(_('Alpha Vs Benchmark'), null=True, blank=True)
    beta_vs_benchmark = models.FloatField(_('Beta Vs Benchmark'), null=True, blank=True)
    information_ratio = models.FloatField(_('Information Ratio'), null=True, blank=True)
    benchmark_return = models.FloatField(_('Benchmark Return'), null=True, blank=True)
    total_fees = models.FloatField(_('Total Fees'), null=True, blank=True)

    equity_curve = models.JSONField(_('Equity Curve'), default=list, blank=True)
    trade_log = models.JSONField(_('Trade Log'), default=list, blank=True)
    parameters = models.JSONField(_('Parameters'), default=dict, blank=True)
    report = models.JSONField(_('Report'), default=dict, blank=True)
    error_message = models.TextField(_('Error Message'), blank=True)

    # The id of the Celery task currently owning this row. Both the stale-owner
    # check and revoke need it, and ``apps/core/task_health`` reads it.
    current_task_id = models.CharField(_('Current Task ID'), max_length=64, blank=True)
    pending_control_action = models.CharField(
        _('Pending Control Action'), max_length=20, choices=ControlAction.choices,
        default=ControlAction.NONE, db_index=True,
    )

    started_at = models.DateTimeField(_('Started At'), null=True, blank=True)
    completed_at = models.DateTimeField(_('Completed At'), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('ETF Rotation Backtest')
        verbose_name_plural = _('ETF Rotation Backtests')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['model_type', 'rebalance_frequency']),
        ]

    def __str__(self):
        return f"{self.name} ({self.status})"

