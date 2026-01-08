from django.contrib import admin
from .models import TechnicalIndicator

@admin.register(TechnicalIndicator)
class TechnicalIndicatorAdmin(admin.ModelAdmin):
    list_display = ('asset', 'timestamp', 'indicator_type', 'value', 'parameters')
    list_filter = ('indicator_type', 'asset__symbol')
    search_fields = ('asset__symbol',)
    date_hierarchy = 'timestamp'
