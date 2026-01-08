from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.core.cache import cache
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q

from .models import TechnicalIndicator
from .serializers import TechnicalIndicatorSerializer, TechnicalIndicatorListSerializer


class TechnicalIndicatorViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for viewing technical indicators.
    Supports filtering by asset, indicator type, and date range.
    """
    queryset = TechnicalIndicator.objects.select_related('asset').all()
    serializer_class = TechnicalIndicatorSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['asset', 'indicator_type']
    ordering_fields = ['timestamp']
    ordering = ['-timestamp']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return TechnicalIndicatorListSerializer
        return TechnicalIndicatorSerializer
    
    def get_queryset(self):
        """
        Optionally filter by asset, indicator_type, and timestamp range
        """
        queryset = super().get_queryset()
        
        # Filter by asset
        asset_id = self.request.query_params.get('asset')
        if asset_id:
            queryset = queryset.filter(asset_id=asset_id)
        
        # Filter by indicator type
        indicator_type = self.request.query_params.get('indicator_type')
        if indicator_type:
            queryset = queryset.filter(indicator_type=indicator_type)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        
        if date_from:
            queryset = queryset.filter(timestamp__gte=date_from)
        if date_to:
            queryset = queryset.filter(timestamp__lte=date_to)
        
        return queryset
    
    @method_decorator(cache_page(60 * 60 * 2))  # Cache for 2 hours
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
    
    @method_decorator(cache_page(60 * 60 * 2))
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
    
    @action(detail=False, methods=['get'])
    @method_decorator(cache_page(60 * 5))  # Cache for 5 minutes
    def top_rsi(self, request):
        """
        Get assets with highest RSI values (potentially overbought).
        Cached for 5 minutes.
        """
        cache_key = 'top_rsi_stocks'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            return Response(cached_data)
        
        # Get latest RSI indicators
        top_rsi = self.queryset.filter(
            indicator_type='RSI'
        ).order_by('-value')[:20]
        
        serializer = self.get_serializer(top_rsi, many=True)
        cache.set(cache_key, serializer.data, 60 * 5)  # 5 minutes
        
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    @method_decorator(cache_page(60 * 5))  # Cache for 5 minutes
    def bottom_rsi(self, request):
        """
        Get assets with lowest RSI values (potentially oversold).
        Cached for 5 minutes.
        """
        cache_key = 'bottom_rsi_stocks'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            return Response(cached_data)
        
        # Get latest RSI indicators
        bottom_rsi = self.queryset.filter(
            indicator_type='RSI'
        ).order_by('value')[:20]
        
        serializer = self.get_serializer(bottom_rsi, many=True)
        cache.set(cache_key, serializer.data, 60 * 5)  # 5 minutes
        
        return Response(serializer.data)
