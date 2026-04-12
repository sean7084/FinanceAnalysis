from django.contrib import admin

from .models import BacktestRun, BacktestTrade


@admin.register(BacktestRun)
class BacktestRunAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'name', 'strategy_type', 'status', 'start_date', 'end_date',
        'initial_capital', 'final_value', 'total_return', 'win_rate', 'created_at',
    )
    list_filter = ('strategy_type', 'status', 'start_date', 'end_date')
    search_fields = ('name', 'user__username')
    date_hierarchy = 'created_at'


@admin.register(BacktestTrade)
class BacktestTradeAdmin(admin.ModelAdmin):
    list_display = ('backtest_run', 'asset', 'trade_date', 'side', 'price', 'quantity', 'amount', 'pnl')
    list_filter = ('side', 'trade_date')
    search_fields = ('asset__symbol', 'asset__name', 'backtest_run__name')
    date_hierarchy = 'trade_date'
