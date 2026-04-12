from django.contrib import admin

from .models import FundamentalFactorSnapshot, CapitalFlowSnapshot, FactorScore


@admin.register(FundamentalFactorSnapshot)
class FundamentalFactorSnapshotAdmin(admin.ModelAdmin):
    list_display = ('asset', 'date', 'pe', 'pb', 'roe', 'roe_qoq')
    list_filter = ('date',)
    search_fields = ('asset__symbol', 'asset__name')
    date_hierarchy = 'date'


@admin.register(CapitalFlowSnapshot)
class CapitalFlowSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        'asset', 'date', 'northbound_net_5d', 'main_force_net_5d',
        'margin_balance_change_5d',
    )
    list_filter = ('date',)
    search_fields = ('asset__symbol', 'asset__name')
    date_hierarchy = 'date'


@admin.register(FactorScore)
class FactorScoreAdmin(admin.ModelAdmin):
    list_display = (
        'asset', 'date', 'mode', 'composite_score', 'bottom_probability_score',
        'fundamental_score', 'capital_flow_score', 'technical_score',
    )
    list_filter = ('date', 'mode')
    search_fields = ('asset__symbol', 'asset__name')
    date_hierarchy = 'date'
