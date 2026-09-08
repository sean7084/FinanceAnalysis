"""Backtest persistence.

Two tables only. A run carries its configuration, its results, and its execution
state; trades carry the ledger.

``BacktestRun.parameters`` is the escape hatch that keeps the schema stable while
the strategy surface grows -- candidate mode, trade-score scope, entry weekdays,
fee overrides, TP/SL policy, and LightGBM inference backend all live there rather
than as columns. ``BacktestRun.report`` holds the equity curve, the benchmark
curve, strategy metadata, and ``runtime_state`` for chunked resume.

The consequence is that most run behaviour is **not** queryable through the ORM.
Filtering runs by a parameter means filtering in Python over the JSON, which is why
the export commands read a bounded id range rather than querying by configuration.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.markets.models import Asset


class BacktestRun(models.Model):
    """One backtest: its parameters, results, and chunked execution state.

    ``status`` tracks the lifecycle (``PENDING`` -> ``RUNNING`` -> ``COMPLETED`` or
    ``FAILED``, with ``PAUSED`` reachable from ``RUNNING``). ``current_task_id`` and
    ``pending_control_action`` are what make an asynchronous, resumable run
    controllable from the API; ``apps.backtest.task_health`` reads them to detect a
    run whose worker has gone away.

    ``report.runtime_state`` is resume bookkeeping rather than a result -- a run is
    complete when the state says so, not when ``status`` is ``COMPLETED``.
    """
    class StrategyType(models.TextChoices):
        BOTTOM_CANDIDATE = 'BOTTOM_CANDIDATE', _('Bottom Candidate')
        PREDICTION_THRESHOLD = 'PREDICTION_THRESHOLD', _('Prediction Threshold')
        MACRO_ROTATION = 'MACRO_ROTATION', _('Macro Rotation')

    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        RUNNING = 'RUNNING', _('Running')
        PAUSED = 'PAUSED', _('Paused')
        COMPLETED = 'COMPLETED', _('Completed')
        FAILED = 'FAILED', _('Failed')

    class ControlAction(models.TextChoices):
        NONE = 'NONE', _('None')
        PAUSE = 'PAUSE', _('Pause')
        RESTART = 'RESTART', _('Restart')
        DELETE = 'DELETE', _('Delete')

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='backtest_runs',
        null=True,
        blank=True,
        verbose_name=_('User'),
    )
    name = models.CharField(_('Name'), max_length=120)
    strategy_type = models.CharField(
        _('Strategy Type'),
        max_length=40,
        choices=StrategyType.choices,
        default=StrategyType.PREDICTION_THRESHOLD,
        db_index=True,
    )
    status = models.CharField(
        _('Status'),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    start_date = models.DateField(_('Start Date'), db_index=True)
    end_date = models.DateField(_('End Date'), db_index=True)
    initial_capital = models.DecimalField(_('Initial Capital'), max_digits=18, decimal_places=2, default=100000)
    cash = models.DecimalField(_('Cash'), max_digits=18, decimal_places=2, default=100000)
    final_value = models.DecimalField(_('Final Value'), max_digits=18, decimal_places=2, default=0)

    total_return = models.DecimalField(_('Total Return'), max_digits=10, decimal_places=6, default=0)
    annualized_return = models.DecimalField(_('Annualized Return'), max_digits=10, decimal_places=6, default=0)
    max_drawdown = models.DecimalField(_('Max Drawdown'), max_digits=10, decimal_places=6, default=0)
    sharpe_ratio = models.DecimalField(_('Sharpe Ratio'), max_digits=10, decimal_places=6, default=0)
    win_rate = models.DecimalField(_('Win Rate'), max_digits=10, decimal_places=6, default=0)

    total_trades = models.PositiveIntegerField(_('Total Trades'), default=0)
    winning_trades = models.PositiveIntegerField(_('Winning Trades'), default=0)

    parameters = models.JSONField(_('Parameters'), default=dict, blank=True)
    report = models.JSONField(_('Report'), default=dict, blank=True)
    error_message = models.TextField(_('Error Message'), blank=True)
    current_task_id = models.CharField(_('Current Task ID'), max_length=255, blank=True)
    pending_control_action = models.CharField(
        _('Pending Control Action'),
        max_length=20,
        choices=ControlAction.choices,
        default=ControlAction.NONE,
        db_index=True,
    )

    started_at = models.DateTimeField(_('Started At'), null=True, blank=True)
    completed_at = models.DateTimeField(_('Completed At'), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Backtest Run')
        verbose_name_plural = _('Backtest Runs')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['strategy_type', 'status', 'created_at']),
            models.Index(fields=['start_date', 'end_date']),
        ]


class BacktestTrade(models.Model):
    """One executed buy or sell leg of a run.

    Rows are legs, not round trips: a closed position produces a ``BUY`` and a
    ``SELL``. Reported ``total_trades`` counts closed positions, so it is not
    comparable to a row count here.

    ``signal_payload`` carries the candidate's rank, the metric it ranked on,
    threshold pass/fail, selection state, trade-decision levels, and model
    provenance. It is the only record of *why* an entry happened, and it is what
    ``_collect_run_model_references`` reads to attribute a run to its models.
    """
    class Side(models.TextChoices):
        BUY = 'BUY', _('Buy')
        SELL = 'SELL', _('Sell')

    backtest_run = models.ForeignKey(
        BacktestRun,
        on_delete=models.CASCADE,
        related_name='trades',
        verbose_name=_('Backtest Run'),
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='backtest_trades',
        verbose_name=_('Asset'),
    )
    trade_date = models.DateField(_('Trade Date'), db_index=True)
    side = models.CharField(_('Side'), max_length=10, choices=Side.choices, db_index=True)
    quantity = models.DecimalField(_('Quantity'), max_digits=18, decimal_places=4)
    price = models.DecimalField(_('Price'), max_digits=12, decimal_places=4)
    fee = models.DecimalField(_('Fee'), max_digits=12, decimal_places=4, default=0)
    slippage = models.DecimalField(_('Slippage'), max_digits=12, decimal_places=4, default=0)
    amount = models.DecimalField(_('Amount'), max_digits=18, decimal_places=4)
    pnl = models.DecimalField(_('PnL'), max_digits=18, decimal_places=4, default=0)
    signal_payload = models.JSONField(_('Signal Payload'), default=dict, blank=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Backtest Trade')
        verbose_name_plural = _('Backtest Trades')
        ordering = ['trade_date', 'id']
        indexes = [
            models.Index(fields=['backtest_run', 'trade_date']),
            models.Index(fields=['asset', 'trade_date']),
        ]
