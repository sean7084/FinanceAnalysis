from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction

from .models import BacktestRun, BacktestTrade
from .serializers import BacktestRunSerializer, BacktestTradeSerializer
from .tasks import run_backtest


class BacktestRunViewSet(viewsets.ModelViewSet):
    serializer_class = BacktestRunSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = BacktestRun.objects.select_related('user').all().order_by('-created_at')
        strategy = self.request.query_params.get('strategy_type')
        status_q = self.request.query_params.get('status')
        if strategy:
            qs = qs.filter(strategy_type=strategy.upper())
        if status_q:
            qs = qs.filter(status=status_q.upper())
        return qs

    def perform_create(self, serializer):
        run = serializer.save(user=self.request.user)
        transaction.on_commit(lambda: run_backtest.delay(run.id))

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.status_code = status.HTTP_202_ACCEPTED
        return response

    @action(detail=True, methods=['post'])
    def rerun(self, request, pk=None):
        run = self.get_object()
        run.status = BacktestRun.Status.PENDING
        run.error_message = ''
        run.save(update_fields=['status', 'error_message', 'updated_at'])
        transaction.on_commit(lambda: run_backtest.delay(run.id))
        return Response({'message': 'Backtest rerun queued.', 'id': run.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'])
    def trades(self, request, pk=None):
        run = self.get_object()
        rows = run.trades.select_related('asset').all().order_by('trade_date', 'id')
        return Response(BacktestTradeSerializer(rows, many=True).data)


class BacktestTradeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = BacktestTradeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = BacktestTrade.objects.select_related('asset', 'backtest_run').all().order_by('trade_date', 'id')
        run_id = self.request.query_params.get('backtest_run')
        if run_id and run_id.isdigit():
            qs = qs.filter(backtest_run_id=int(run_id))
        return qs
