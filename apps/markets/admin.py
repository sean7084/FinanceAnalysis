from django.contrib import admin
from modeltranslation.admin import TranslationAdmin
from .models import Market, Asset, OHLCV

@admin.register(Market)
class MarketAdmin(admin.ModelAdmin):
    list_display = ('code', 'name')
    search_fields = ('code', 'name')

@admin.register(Asset)
class AssetAdmin(TranslationAdmin):
    list_display = ('ts_code', 'name', 'market', 'listing_status')
    list_filter = ('market', 'listing_status')
    search_fields = ('ts_code', 'name', 'symbol')

@admin.register(OHLCV)
class OHLCVAdmin(admin.ModelAdmin):
    list_display = ('asset', 'date', 'open', 'high', 'low', 'close', 'volume')
    list_filter = ('date',)
    search_fields = ('asset__ts_code', 'asset__name')
    date_hierarchy = 'date'

