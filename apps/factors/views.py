from decimal import Decimal, InvalidOperation
from datetime import date

from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import FundamentalFactorSnapshot, CapitalFlowSnapshot, FactorScore
from .serializers import (
    FundamentalFactorSnapshotSerializer,
    CapitalFlowSnapshotSerializer,
    FactorScoreSerializer,
)
from .tasks import calculate_factor_scores_for_date


def _parse_decimal(value, default):
    if value is None:
        return Decimal(str(default))
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(str(default))


class FundamentalFactorSnapshotViewSet(viewsets.ModelViewSet):
    serializer_class = FundamentalFactorSnapshotSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return FundamentalFactorSnapshot.objects.select_related('asset').order_by('-date', 'asset__symbol')


class CapitalFlowSnapshotViewSet(viewsets.ModelViewSet):
    serializer_class = CapitalFlowSnapshotSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return CapitalFlowSnapshot.objects.select_related('asset').order_by('-date', 'asset__symbol')


class BottomCandidateViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Phase 11 bottom candidates endpoint.
    GET /api/v1/screener/bottom-candidates/
    POST /api/v1/screener/bottom-candidates/recalculate/
    """
    serializer_class = FactorScoreSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        mode = self.request.query_params.get('mode', FactorScore.FactorMode.COMPOSITE).upper()
        if mode not in {FactorScore.FactorMode.COMPOSITE, FactorScore.FactorMode.TECHNICAL, FactorScore.FactorMode.FUNDAMENTAL}:
            mode = FactorScore.FactorMode.COMPOSITE

        as_of = self.request.query_params.get('as_of')
        if as_of:
            try:
                target_date = date.fromisoformat(as_of)
            except ValueError:
                target_date = timezone.now().date()
        else:
            latest = FactorScore.objects.filter(mode=mode).order_by('-date').values_list('date', flat=True).first()
            target_date = latest or timezone.now().date()

        qs = FactorScore.objects.select_related('asset').filter(date=target_date, mode=mode)

        # Optional filter: minimum bottom probability threshold.
        min_score = self.request.query_params.get('min_score')
        if min_score is not None:
            try:
                qs = qs.filter(bottom_probability_score__gte=Decimal(str(min_score)))
            except (InvalidOperation, ValueError):
                pass

        return qs.order_by('-bottom_probability_score', 'asset__symbol')

    def list(self, request, *args, **kwargs):
        top_n = request.query_params.get('top_n', 20)
        try:
            top_n = max(1, min(200, int(top_n)))
        except ValueError:
            top_n = 20

        queryset = self.get_queryset()[:top_n]
        serializer = self.get_serializer(queryset, many=True)
        return Response({
            'count': len(serializer.data),
            'results': serializer.data,
        })

    @action(detail=False, methods=['post'])
    def recalculate(self, request):
        as_of = request.data.get('as_of')
        fw = _parse_decimal(request.data.get('financial_weight'), 0.4)
        cw = _parse_decimal(request.data.get('flow_weight'), 0.3)
        tw = _parse_decimal(request.data.get('technical_weight'), 0.3)

        calculate_factor_scores_for_date.delay(
            target_date=as_of,
            financial_weight=float(fw),
            flow_weight=float(cw),
            technical_weight=float(tw),
        )
        return Response(
            {
                'message': 'Bottom candidate factor scoring queued.',
                'as_of': as_of,
                'weights': {
                    'financial_weight': float(fw),
                    'flow_weight': float(cw),
                    'technical_weight': float(tw),
                },
            },
            status=status.HTTP_202_ACCEPTED,
        )
