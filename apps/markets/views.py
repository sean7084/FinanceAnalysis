from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.core.cache import cache
from django_filters.rest_framework import DjangoFilterBackend

from .models import Market, Asset, OHLCV
from .serializers import (
    MarketSerializer, AssetSerializer, AssetListSerializer,
    OHLCVSerializer, OHLCVListSerializer
)


class MarketViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for viewing markets.
    """
    queryset = Market.objects.all()
    serializer_class = MarketSerializer
    
    @method_decorator(cache_page(60 * 60 * 24))  # Cache for 24 hours
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
    
    @method_decorator(cache_page(60 * 60 * 24))
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)


class AssetViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for viewing assets (stocks).
    Supports filtering by market, symbol, and searching by name.
    """
    queryset = Asset.objects.select_related('market').all()
    serializer_class = AssetSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['market__code', 'listing_status']
    search_fields = ['symbol', 'ts_code', 'name']
    ordering_fields = ['symbol', 'name']
    ordering = ['symbol']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return AssetListSerializer
        return AssetSerializer
    
    @method_decorator(cache_page(60 * 60 * 2))  # Cache for 2 hours
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
    
    @method_decorator(cache_page(60 * 60 * 2))
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)


class OHLCVViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for viewing OHLCV data.
    Supports filtering by asset and date range.
    """
    queryset = OHLCV.objects.select_related('asset').all()
    serializer_class = OHLCVSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['asset', 'date']
    ordering_fields = ['date']
    ordering = ['-date']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return OHLCVListSerializer
        return OHLCVSerializer
    
    def get_queryset(self):
        """
        Optionally filter by asset (query param: asset=<id>)
        and date range (query params: date_from, date_to)
        """
        queryset = super().get_queryset()
        
        # Filter by asset
        asset_id = self.request.query_params.get('asset')
        if asset_id:
            queryset = queryset.filter(asset_id=asset_id)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        
        if date_from:
            queryset = queryset.filter(date__gte=date_from)
        if date_to:
            queryset = queryset.filter(date__lte=date_to)
        
        return queryset
    
    @method_decorator(cache_page(60 * 60 * 2))  # Cache for 2 hours (data doesn't change after market close)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
    
    @method_decorator(cache_page(60 * 60 * 2))
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
