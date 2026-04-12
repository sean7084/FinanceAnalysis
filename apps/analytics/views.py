from rest_framework import viewsets, filters, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.core.cache import cache
from django_filters.rest_framework import DjangoFilterBackend

from django.utils import timezone
from datetime import timedelta
from .models import TechnicalIndicator, ScreenerTemplate, AlertRule, AlertEvent, SignalEvent
from .serializers import (
    TechnicalIndicatorSerializer,
    TechnicalIndicatorListSerializer,
    ScreenerTemplateSerializer,
    AlertRuleSerializer,
    AlertEventSerializer,
    SignalEventSerializer,
)
from apps.markets.models import Asset, OHLCV
from .tasks import (
    calculate_rsi_for_asset,
    calculate_macd_for_asset,
    calculate_bollinger_bands_for_asset,
    calculate_sma_for_asset,
    calculate_ema_for_asset,
    calculate_stochastic_for_asset,
    calculate_adx_for_asset,
    calculate_obv_for_asset,
    calculate_fibonacci_retracement_for_asset,
    calculate_signals_for_all_assets,
)


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
    
    @action(detail=False, methods=['get'])
    @method_decorator(cache_page(60 * 5))
    def indicator_types(self, request):
        """
        Get list of all available indicator types with counts.
        """
        from django.db.models import Count
        
        indicator_summary = TechnicalIndicator.objects.values(
            'indicator_type'
        ).annotate(
            count=Count('id')
        ).order_by('indicator_type')
        
        return Response(indicator_summary)
    
    @action(detail=False, methods=['get'])
    def compare(self, request):
        """
        Compare multiple indicators for a specific asset over time.
        Query params: asset_id (required), indicator_types (comma-separated), date_from, date_to
        Example: /api/v1/indicators/compare/?asset_id=1&indicator_types=RSI,MACD&date_from=2026-01-01
        """
        asset_id = request.query_params.get('asset_id')
        indicator_types = request.query_params.get('indicator_types', '')
        
        if not asset_id:
            return Response(
                {'error': 'asset_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not indicator_types:
            return Response(
                {'error': 'indicator_types is required (comma-separated list)'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            asset = Asset.objects.get(id=asset_id)
        except Asset.DoesNotExist:
            return Response(
                {'error': 'Asset not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        indicator_list = [t.strip().upper() for t in indicator_types.split(',')]
        
        queryset = TechnicalIndicator.objects.filter(
            asset_id=asset_id,
            indicator_type__in=indicator_list
        )
        
        # Apply date filters
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        
        if date_from:
            queryset = queryset.filter(timestamp__gte=date_from)
        if date_to:
            queryset = queryset.filter(timestamp__lte=date_to)
        
        queryset = queryset.order_by('timestamp')
        
        # Group by indicator type
        result = {
            'asset': {
                'id': asset.id,
                'symbol': asset.symbol,
                'name': asset.name
            },
            'indicators': {}
        }
        
        for indicator_type in indicator_list:
            indicators = queryset.filter(indicator_type=indicator_type)
            serializer = TechnicalIndicatorSerializer(indicators, many=True)
            result['indicators'][indicator_type] = serializer.data
        
        return Response(result)
    
    @action(detail=False, methods=['get'])
    @method_decorator(cache_page(60 * 5))
    def trending_strong(self, request):
        """
        Get assets with high ADX (strong trend) - ADX > 25 indicates strong trend.
        """
        cache_key = 'trending_strong_stocks'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            return Response(cached_data)
        
        strong_trends = self.queryset.filter(
            indicator_type='ADX',
            value__gte=25
        ).order_by('-value')[:20]
        
        serializer = self.get_serializer(strong_trends, many=True)
        cache.set(cache_key, serializer.data, 60 * 5)
        
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    @method_decorator(cache_page(60 * 5))
    def overbought_stoch(self, request):
        """
        Get assets with Stochastic > 80 (overbought).
        """
        cache_key = 'overbought_stoch_stocks'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            return Response(cached_data)
        
        overbought = self.queryset.filter(
            indicator_type='STOCH',
            value__gte=80
        ).order_by('-value')[:20]
        
        serializer = self.get_serializer(overbought, many=True)
        cache.set(cache_key, serializer.data, 60 * 5)
        
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    @method_decorator(cache_page(60 * 5))
    def oversold_stoch(self, request):
        """
        Get assets with Stochastic < 20 (oversold).
        """
        cache_key = 'oversold_stoch_stocks'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            return Response(cached_data)
        
        oversold = self.queryset.filter(
            indicator_type='STOCH',
            value__lte=20
        ).order_by('value')[:20]
        
        serializer = self.get_serializer(oversold, many=True)
        cache.set(cache_key, serializer.data, 60 * 5)
        
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def fibonacci_levels(self, request):
        """
        Get latest Fibonacci retracement levels for an asset.
        Query params: asset_id (required)
        """
        asset_id = request.query_params.get('asset_id')
        if not asset_id:
            return Response({'error': 'asset_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        indicator = TechnicalIndicator.objects.filter(
            asset_id=asset_id,
            indicator_type='FIB_RET',
        ).order_by('-timestamp').first()

        if not indicator:
            return Response({'error': 'No Fibonacci levels found for this asset'}, status=status.HTTP_404_NOT_FOUND)

        return Response(TechnicalIndicatorSerializer(indicator).data)

    @action(detail=False, methods=['post'])
    def recalculate(self, request):
        """
        Queue indicator calculation for a specific asset with custom parameters.
        Body: {"asset_id": 1, "indicator_type": "RSI", "params": {...}}
        """
        asset_id = request.data.get('asset_id')
        indicator_type = str(request.data.get('indicator_type', '')).upper()
        params = request.data.get('params', {}) or {}

        if not asset_id:
            return Response({'error': 'asset_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        if not indicator_type:
            return Response({'error': 'indicator_type is required'}, status=status.HTTP_400_BAD_REQUEST)

        task_map = {
            'RSI': calculate_rsi_for_asset,
            'MACD': calculate_macd_for_asset,
            'BBANDS': calculate_bollinger_bands_for_asset,
            'SMA': calculate_sma_for_asset,
            'EMA': calculate_ema_for_asset,
            'STOCH': calculate_stochastic_for_asset,
            'ADX': calculate_adx_for_asset,
            'OBV': calculate_obv_for_asset,
            'FIB_RET': calculate_fibonacci_retracement_for_asset,
        }

        task = task_map.get(indicator_type)
        if not task:
            return Response({'error': f'Unsupported indicator_type: {indicator_type}'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            asset_id_int = int(asset_id)
        except (TypeError, ValueError):
            return Response({'error': 'asset_id must be an integer'}, status=status.HTTP_400_BAD_REQUEST)

        task.delay(asset_id=asset_id_int, **params)
        return Response(
            {
                'message': 'Indicator calculation queued',
                'asset_id': asset_id_int,
                'indicator_type': indicator_type,
                'params': params,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ScreenerTemplateViewSet(viewsets.ModelViewSet):
    """
    Saved screener templates for users.
    """
    serializer_class = ScreenerTemplateSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        if self.request.user.is_authenticated:
            if self.action in ['list', 'retrieve']:
                return (ScreenerTemplate.objects.filter(owner=self.request.user) | ScreenerTemplate.objects.filter(is_public=True)).distinct()
            return ScreenerTemplate.objects.filter(owner=self.request.user)
        return ScreenerTemplate.objects.filter(is_public=True)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class ScreenerViewSet(viewsets.ViewSet):
    """
    Real-time stock screener endpoints.
    """
    permission_classes = [permissions.IsAuthenticated]

    PREBUILT_SCREENERS = {
        'overbought_oversold',
        'breakout_candidates',
        'high_volume',
        'trend_reversal',
    }

    @action(detail=False, methods=['get'])
    def prebuilt(self, request):
        return Response({'screeners': sorted(self.PREBUILT_SCREENERS)})

    @action(detail=False, methods=['get'])
    def run(self, request):
        screener_type = request.query_params.get('type', '').strip()
        limit = int(request.query_params.get('limit', 20))

        if screener_type not in self.PREBUILT_SCREENERS:
            return Response(
                {'error': 'Unsupported screener type.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if screener_type == 'overbought_oversold':
            high = request.query_params.get('high', 70)
            low = request.query_params.get('low', 30)
            high_qs = TechnicalIndicator.objects.filter(indicator_type='RSI', value__gte=high).order_by('-value')[:limit]
            low_qs = TechnicalIndicator.objects.filter(indicator_type='RSI', value__lte=low).order_by('value')[:limit]
            return Response(
                {
                    'type': screener_type,
                    'high_threshold': high,
                    'low_threshold': low,
                    'overbought': TechnicalIndicatorSerializer(high_qs, many=True).data,
                    'oversold': TechnicalIndicatorSerializer(low_qs, many=True).data,
                }
            )

        if screener_type == 'high_volume':
            lookback_days = int(request.query_params.get('lookback_days', 20))
            volume_ratio = float(request.query_params.get('volume_ratio', 1.5))
            assets = Asset.objects.all()[:500]
            matches = []
            for asset in assets:
                latest = OHLCV.objects.filter(asset=asset).order_by('-date').first()
                avg_vol = OHLCV.objects.filter(asset=asset).order_by('-date').values_list('volume', flat=True)[:lookback_days]
                avg_vol_value = sum(avg_vol) / len(avg_vol) if avg_vol else 0
                if latest and avg_vol_value and latest.volume >= avg_vol_value * volume_ratio:
                    matches.append(
                        {
                            'asset_id': asset.id,
                            'symbol': asset.symbol,
                            'name': asset.name,
                            'latest_volume': latest.volume,
                            'avg_volume': round(avg_vol_value, 2),
                        }
                    )
            matches.sort(key=lambda x: x['latest_volume'], reverse=True)
            return Response({'type': screener_type, 'results': matches[:limit]})

        if screener_type == 'breakout_candidates':
            lookback_days = int(request.query_params.get('lookback_days', 20))
            assets = Asset.objects.all()[:500]
            matches = []
            for asset in assets:
                candles = list(OHLCV.objects.filter(asset=asset).order_by('-date')[:lookback_days])
                if len(candles) < lookback_days:
                    continue
                latest = candles[0]
                period_high = max(float(c.high) for c in candles)
                if float(latest.close) >= period_high * 0.98:
                    matches.append(
                        {
                            'asset_id': asset.id,
                            'symbol': asset.symbol,
                            'name': asset.name,
                            'close': float(latest.close),
                            'period_high': period_high,
                        }
                    )
            matches.sort(key=lambda x: x['close'], reverse=True)
            return Response({'type': screener_type, 'results': matches[:limit]})

        # trend_reversal
        rsi_threshold = request.query_params.get('rsi_threshold', 35)
        rsi_qs = TechnicalIndicator.objects.filter(indicator_type='RSI', value__lte=rsi_threshold).select_related('asset')
        symbols = {r.asset.symbol for r in rsi_qs}
        macd_qs = TechnicalIndicator.objects.filter(indicator_type='MACD', asset__symbol__in=symbols, value__gt=0).select_related('asset')
        results = [
            {
                'asset_id': item.asset.id,
                'symbol': item.asset.symbol,
                'name': item.asset.name,
                'macd': float(item.value),
            }
            for item in macd_qs[:limit]
        ]
        return Response({'type': screener_type, 'results': results})


class AlertRuleViewSet(viewsets.ModelViewSet):
    """
    CRUD API for alert rules.
    """
    serializer_class = AlertRuleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return AlertRule.objects.filter(owner=self.request.user).select_related('asset', 'owner')

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=False, methods=['get'])
    def active(self, request):
        queryset = self.get_queryset().filter(is_active=True)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class AlertEventViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Alert history endpoint.
    """
    serializer_class = AlertEventSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'asset', 'alert_rule']
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        return AlertEvent.objects.filter(alert_rule__owner=self.request.user).select_related('alert_rule', 'asset')


class SignalEventViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only endpoint for Phase 10 technical signal events.
    GET /api/v1/signals/                    – paginated list (filter by asset, signal_type)
    GET /api/v1/signals/recent/?days=7      – signals from the last N days
    POST /api/v1/signals/recalculate/       – queue signal recalculation for all assets
    """
    serializer_class = SignalEventSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['asset', 'signal_type']
    search_fields = ['description', 'asset__symbol', 'asset__name']
    ordering_fields = ['timestamp', 'signal_type']
    ordering = ['-timestamp']

    def get_queryset(self):
        return SignalEvent.objects.select_related('asset').all()

    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Return signals from the last N days (default 7)."""
        try:
            days = max(1, int(request.query_params.get('days', 7)))
        except (TypeError, ValueError):
            days = 7
        since = timezone.now() - timedelta(days=days)
        qs = self.get_queryset().filter(timestamp__gte=since)
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = SignalEventSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        return Response(SignalEventSerializer(qs, many=True).data)

    @action(detail=False, methods=['post'])
    def recalculate(self, request):
        """Queue full signal recalculation for all assets."""
        calculate_signals_for_all_assets.delay()
        return Response({'message': 'Signal recalculation queued.'}, status=status.HTTP_202_ACCEPTED)
