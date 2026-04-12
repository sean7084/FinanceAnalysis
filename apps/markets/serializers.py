from rest_framework import serializers
from .models import Market, Asset, OHLCV


class MarketSerializer(serializers.ModelSerializer):
    class Meta:
        model = Market
        fields = ['id', 'code', 'name']


class AssetSerializer(serializers.ModelSerializer):
    market_name = serializers.CharField(source='market.name', read_only=True)
    market_code = serializers.CharField(source='market.code', read_only=True)
    
    class Meta:
        model = Asset
        fields = [
            'id', 'symbol', 'ts_code', 'name', 
            'market_name', 'market_code', 'listing_status'
        ]


class AssetListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views"""
    market_code = serializers.CharField(source='market.code', read_only=True)
    
    class Meta:
        model = Asset
        fields = ['id', 'symbol', 'ts_code', 'name', 'market_code']


class OHLCVSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    
    class Meta:
        model = OHLCV
        fields = [
            'id', 'asset', 'asset_symbol', 'asset_name', 'date',
            'open', 'high', 'low', 'close', 'adj_close', 'volume', 'amount'
        ]


class OHLCVListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views"""
    
    class Meta:
        model = OHLCV
        fields = ['date', 'open', 'high', 'low', 'close', 'volume']
