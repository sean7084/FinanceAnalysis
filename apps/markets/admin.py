from django.contrib import admin
from modeltranslation.admin import TranslationAdmin
from .models import Asset, IndexMembership, Market, OHLCV

@admin.register(Market)
class MarketAdmin(admin.ModelAdmin):
    list_display = ('code', 'name')
    search_fields = ('code', 'name')

@admin.register(Asset)
class AssetAdmin(TranslationAdmin):
    list_display = ('ts_code', 'name', 'market', 'listing_status', 'list_date', 'membership_tags_display')
    list_filter = ('market', 'listing_status', 'list_date')
    search_fields = ('ts_code', 'name', 'symbol')

    def membership_tags_display(self, obj):
        tags = obj.membership_tags or []
        return ', '.join(tags) if tags else '-'

    membership_tags_display.short_description = 'Membership Tags'

@admin.register(OHLCV)
class OHLCVAdmin(admin.ModelAdmin):
    list_display = ('asset', 'date', 'open', 'high', 'low', 'close', 'volume')
    list_filter = ('date',)
    search_fields = ('asset__ts_code', 'asset__name')
    date_hierarchy = 'date'


@admin.register(IndexMembership)
class IndexMembershipAdmin(admin.ModelAdmin):
    list_display = ('asset', 'index_code', 'index_name', 'trade_date', 'weight', 'source')
    list_filter = ('index_code', 'trade_date', 'source')
    search_fields = ('asset__ts_code', 'asset__name', 'index_code', 'index_name')
    date_hierarchy = 'trade_date'

