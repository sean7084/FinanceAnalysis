from django.contrib import admin
from .models import TechnicalIndicator, ScreenerTemplate, AlertRule, AlertEvent, SignalEvent

@admin.register(TechnicalIndicator)
class TechnicalIndicatorAdmin(admin.ModelAdmin):
    list_display = ('asset', 'timestamp', 'indicator_type', 'value', 'parameters')
    list_filter = ('indicator_type', 'asset__symbol')
    search_fields = ('asset__symbol',)
    date_hierarchy = 'timestamp'


@admin.register(ScreenerTemplate)
class ScreenerTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'screener_type', 'is_public', 'updated_at')
    list_filter = ('screener_type', 'is_public')
    search_fields = ('name', 'owner__username')


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'asset', 'condition_type', 'threshold', 'is_active', 'last_triggered_at')
    list_filter = ('condition_type', 'is_active')
    search_fields = ('name', 'owner__username', 'asset__symbol')


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = ('alert_rule', 'asset', 'status', 'trigger_value', 'created_at')
    list_filter = ('status',)
    search_fields = ('alert_rule__name', 'asset__symbol')
    date_hierarchy = 'created_at'


@admin.register(SignalEvent)
class SignalEventAdmin(admin.ModelAdmin):
    list_display = ('asset', 'signal_type', 'timestamp', 'created_at')
    list_filter = ('signal_type',)
    search_fields = ('asset__symbol', 'description')
    date_hierarchy = 'timestamp'
    readonly_fields = ('created_at',)
