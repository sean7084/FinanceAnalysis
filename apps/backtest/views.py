from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction

from .comparison import build_backtest_comparison_payload
from .models import BacktestRun, BacktestTrade
from .serializers import BacktestRunSerializer, BacktestTradeSerializer
from .tasks import run_backtest


def _parse_extra_compare_run_ids(query_params):
    raw_values = []
    raw_values.extend(query_params.getlist('extra_compare_run_id'))
    csv_value = query_params.get('extra_compare_run_ids')
    if csv_value:
        raw_values.extend(csv_value.split(','))

    parsed_ids = []
    seen_ids = set()
    for raw_value in raw_values:
        try:
            run_id = int(str(raw_value).strip())
        except (TypeError, ValueError):
            continue
        if run_id <= 0 or run_id in seen_ids:
            continue
        seen_ids.add(run_id)
        parsed_ids.append(run_id)
    return parsed_ids


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

    @action(detail=True, methods=['get'])
    def comparison_curve(self, request, pk=None):
        run = self.get_object()
        extra_compare_run_ids = _parse_extra_compare_run_ids(request.query_params)
        return Response(build_backtest_comparison_payload(run, extra_compare_run_ids=extra_compare_run_ids))


class BacktestTradeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = BacktestTradeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = BacktestTrade.objects.select_related('asset', 'backtest_run').all().order_by('trade_date', 'id')
        run_id = self.request.query_params.get('backtest_run')
        if run_id and run_id.isdigit():
            qs = qs.filter(backtest_run_id=int(run_id))
        return qs
