from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.utils import timezone

from .comparison import build_backtest_comparison_payload
from .models import BacktestRun, BacktestTrade
from .serializers import BacktestRunSerializer, BacktestTradeSerializer
from .tasks import reset_backtest_run_for_restart, revoke_backtest_task, run_backtest


def queue_backtest_run(run):
    result = run_backtest.delay(run.id)
    next_task_id = getattr(result, 'id', '') or ''
    run.current_task_id = str(next_task_id) if next_task_id else ''
    run.pending_control_action = BacktestRun.ControlAction.NONE
    run.updated_at = timezone.now()
    run.save(update_fields=['current_task_id', 'pending_control_action', 'updated_at'])
    return result


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
        transaction.on_commit(lambda: queue_backtest_run(run))

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.status_code = status.HTTP_202_ACCEPTED
        return response

    def _queue_existing_run(self, run):
        transaction.on_commit(lambda: queue_backtest_run(run))

    def _restart_run(self, run):
        revoke_backtest_task(run, terminate=True)

        if run.status == BacktestRun.Status.RUNNING:
            run.pending_control_action = BacktestRun.ControlAction.RESTART
            run.error_message = ''
            run.save(update_fields=['pending_control_action', 'error_message', 'updated_at'])
            return Response(
                {'message': 'Backtest restart requested. It will restart after the current chunk.', 'id': run.id},
                status=status.HTTP_202_ACCEPTED,
            )

        reset_backtest_run_for_restart(run)
        self._queue_existing_run(run)
        return Response({'message': 'Backtest restart queued.', 'id': run.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def rerun(self, request, pk=None):
        run = self.get_object()
        return self._restart_run(run)

    @action(detail=True, methods=['post'])
    def restart(self, request, pk=None):
        run = self.get_object()
        return self._restart_run(run)

    @action(detail=True, methods=['post'])
    def pause(self, request, pk=None):
        run = self.get_object()
        if run.status not in {BacktestRun.Status.PENDING, BacktestRun.Status.RUNNING}:
            return Response({'message': 'Only pending or running backtests can be paused.'}, status=status.HTTP_400_BAD_REQUEST)

        if run.status == BacktestRun.Status.PENDING:
            run.status = BacktestRun.Status.PAUSED
            run.pending_control_action = BacktestRun.ControlAction.NONE
            run.save(update_fields=['status', 'pending_control_action', 'updated_at'])
            revoke_backtest_task(run, terminate=False)
            return Response({'message': 'Backtest paused.', 'id': run.id}, status=status.HTTP_202_ACCEPTED)

        run.pending_control_action = BacktestRun.ControlAction.PAUSE
        run.save(update_fields=['pending_control_action', 'updated_at'])
        revoke_backtest_task(run, terminate=False)
        return Response(
            {'message': 'Backtest pause requested. It will pause after the current chunk.', 'id': run.id},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['post'])
    def resume(self, request, pk=None):
        run = self.get_object()
        if run.status != BacktestRun.Status.PAUSED:
            return Response({'message': 'Only paused backtests can be resumed.'}, status=status.HTTP_400_BAD_REQUEST)

        run.status = BacktestRun.Status.PENDING
        run.pending_control_action = BacktestRun.ControlAction.NONE
        run.error_message = ''
        run.completed_at = None
        run.save(update_fields=['status', 'pending_control_action', 'error_message', 'completed_at', 'updated_at'])
        self._queue_existing_run(run)
        return Response({'message': 'Backtest resume queued.', 'id': run.id}, status=status.HTTP_202_ACCEPTED)

    def destroy(self, request, *args, **kwargs):
        run = self.get_object()

        if run.status == BacktestRun.Status.RUNNING:
            run.pending_control_action = BacktestRun.ControlAction.DELETE
            run.save(update_fields=['pending_control_action', 'updated_at'])
            revoke_backtest_task(run, terminate=True)
            return Response(
                {'message': 'Backtest removal requested. It will be removed after the current chunk.', 'id': run.id},
                status=status.HTTP_202_ACCEPTED,
            )

        revoke_backtest_task(run, terminate=True)
        self.perform_destroy(run)
        return Response(status=status.HTTP_204_NO_CONTENT)

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
