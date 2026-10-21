from django.contrib import admin

from .models import (
    ETFUniverse,
    ETFDaily,
    ETFFeatureSnapshot,
    ETFRotationSignal,
    ETFRotationPosition,
    ETFRotationBacktest,
)


@admin.register(ETFUniverse)
class ETFUniverseAdmin(admin.ModelAdmin):
    list_display = ('ts_code', 'name', 'category', 'invest_type', 'list_date', 'delist_date', 'status', 'is_active')
    list_filter = ('category', 'status', 'is_active', 'fund_type')
    search_fields = ('ts_code', 'symbol', 'name', 'benchmark')
    date_hierarchy = 'list_date'


@admin.register(ETFDaily)
class ETFDailyAdmin(admin.ModelAdmin):
    list_display = ('etf', 'date', 'close', 'adj_close', 'volume', 'amount')
    list_filter = ('date',)
    search_fields = ('etf__ts_code', 'etf__name')
    date_hierarchy = 'date'


@admin.register(ETFFeatureSnapshot)
class ETFFeatureSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        'etf', 'date', 'mom_20d', 'mom_60d', 'vol_20d', 'rank_pct_20d',
        'avg_amount_20d', 'is_eligible', 'label_rank_pct',
    )
    list_filter = ('date', 'is_eligible')
    search_fields = ('etf__ts_code', 'etf__name')
    date_hierarchy = 'date'


@admin.register(ETFRotationSignal)
class ETFRotationSignalAdmin(admin.ModelAdmin):
    list_display = ('date', 'signal_type', 'model_type', 'max_positions', 'defensive_allocation', 'model_version')
    list_filter = ('signal_type', 'model_type', 'date')
    date_hierarchy = 'date'


@admin.register(ETFRotationPosition)
class ETFRotationPositionAdmin(admin.ModelAdmin):
    list_display = ('etf', 'entry_date', 'exit_date', 'status', 'weight', 'return_pct')
    list_filter = ('status', 'entry_date')
    search_fields = ('etf__ts_code', 'etf__name')
    date_hierarchy = 'entry_date'


@admin.register(ETFRotationBacktest)
class ETFRotationBacktestAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'status', 'model_type', 'start_date', 'end_date',
        'total_return', 'annualized_return', 'max_drawdown', 'sharpe_ratio', 'alpha_vs_benchmark',
    )
    list_filter = ('status', 'model_type', 'rebalance_frequency')
    date_hierarchy = 'created_at'
