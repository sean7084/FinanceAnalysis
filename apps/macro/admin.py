from django.contrib import admin

from .models import MacroSnapshot, MarketContext, EventImpactStat


@admin.register(MacroSnapshot)
class MacroSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        'date', 'dxy', 'cny_usd', 'cn10y_yield', 'cn2y_yield',
        'pmi_manufacturing', 'cpi_yoy',
    )
    search_fields = ('date',)
    date_hierarchy = 'date'


@admin.register(MarketContext)
class MarketContextAdmin(admin.ModelAdmin):
    list_display = ('context_key', 'macro_phase', 'event_tag', 'is_active', 'starts_at', 'ends_at')
    list_filter = ('macro_phase', 'is_active')
    search_fields = ('context_key', 'event_tag')


@admin.register(EventImpactStat)
class EventImpactStatAdmin(admin.ModelAdmin):
    list_display = ('event_tag', 'sector', 'horizon_days', 'avg_return', 'excess_return', 'sample_size')
    list_filter = ('event_tag', 'horizon_days')
    search_fields = ('event_tag', 'sector')
