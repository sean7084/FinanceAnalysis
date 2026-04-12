from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import MacroSnapshot, MarketContext, EventImpactStat
from .serializers import (
    MacroSnapshotSerializer,
    MarketContextSerializer,
    EventImpactStatSerializer,
)
from .tasks import sync_macro_data_monthly, refresh_current_market_context


class MacroSnapshotViewSet(viewsets.ModelViewSet):
    serializer_class = MacroSnapshotSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return MacroSnapshot.objects.order_by('-date')

    @action(detail=False, methods=['post'])
    def sync(self, request):
        sync_macro_data_monthly.delay(payload=request.data)
        return Response({'message': 'Macro sync queued.'}, status=status.HTTP_202_ACCEPTED)


class MarketContextViewSet(viewsets.ModelViewSet):
    serializer_class = MarketContextSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return MarketContext.objects.order_by('-starts_at', '-updated_at')

    @action(detail=False, methods=['get'])
    def current(self, request):
        current = MarketContext.objects.filter(context_key='current', is_active=True).order_by('-starts_at', '-updated_at').first()
        if not current:
            return Response({'detail': 'No active context found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(MarketContextSerializer(current).data)

    @action(detail=False, methods=['post'])
    def refresh(self, request):
        refresh_current_market_context.delay(
            snapshot_id=request.data.get('snapshot_id'),
            event_tag=request.data.get('event_tag', ''),
        )
        return Response({'message': 'Market context refresh queued.'}, status=status.HTTP_202_ACCEPTED)


class EventImpactStatViewSet(viewsets.ModelViewSet):
    serializer_class = EventImpactStatSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = EventImpactStat.objects.all().order_by('event_tag', 'sector', 'horizon_days')
        event_tag = self.request.query_params.get('event_tag')
        if event_tag:
            qs = qs.filter(event_tag=event_tag)
        return qs
