from rest_framework import serializers
from .models import TechnicalIndicator


class TechnicalIndicatorSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)
    asset_name = serializers.CharField(source='asset.name', read_only=True)
    
    class Meta:
        model = TechnicalIndicator
        fields = [
            'id', 'asset', 'asset_symbol', 'asset_name',
            'timestamp', 'indicator_type', 'value', 'parameters'
        ]


class TechnicalIndicatorListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views"""
    
    class Meta:
        model = TechnicalIndicator
        fields = ['indicator_type', 'value', 'timestamp']
