from datetime import date

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.markets.models import Asset
from .models import PredictionResult
from .serializers import PredictionResultSerializer
from .tasks_lstm import (
    generate_lstm_prediction_for_asset,
    generate_lstm_predictions_for_date,
    train_lstm_models,
)


class LSTMPredictionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    LSTM predictions stored in PredictionResult rows with model_type=LSTM.

    GET /api/v1/lstm-predictions/
    GET /api/v1/lstm-predictions/{stock_code}/
    POST /api/v1/lstm-predictions/recalculate/
    POST /api/v1/lstm-predictions/train/
    POST /api/v1/lstm-predictions/batch/
    """

    serializer_class = PredictionResultSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = (
            PredictionResult.objects.select_related('asset', 'model_version')
            .filter(model_version__model_type='LSTM')
            .order_by('-date', 'asset__symbol', 'horizon_days')
        )
        date_str = self.request.query_params.get('date')
        horizon = self.request.query_params.get('horizon_days')
        if date_str:
            try:
                target_date = date.fromisoformat(date_str)
                qs = qs.filter(date=target_date)
            except ValueError:
                pass
        if horizon:
            try:
                qs = qs.filter(horizon_days=int(horizon))
            except ValueError:
                pass
        return qs

    @action(detail=False, methods=['get'], url_path=r'(?P<stock_code>(?!(?:train|recalculate|batch)(?:/|$))[^/.]+)')
    def stock(self, request, stock_code=None):
        date_str = request.query_params.get('date')
        if date_str:
            try:
                target_date = date.fromisoformat(date_str)
            except ValueError:
                target_date = timezone.now().date()
        else:
            target_date = timezone.now().date()

        horizons = request.query_params.get('horizons', '3,7,30').split(',')
        horizons = [int(h) for h in horizons if h.strip().isdigit()]
        if not horizons:
            horizons = [3, 7, 30]

        asset = get_object_or_404(Asset, symbol=stock_code)
        qs = PredictionResult.objects.select_related('asset', 'model_version').filter(
            asset=asset,
            date=target_date,
            horizon_days__in=horizons,
            model_version__model_type='LSTM',
        ).order_by('horizon_days')

        if not qs.exists():
            generate_lstm_prediction_for_asset(
                asset_id=asset.id,
                target_date=str(target_date),
                horizons=horizons,
            )
            qs = PredictionResult.objects.select_related('asset', 'model_version').filter(
                asset=asset,
                date=target_date,
                horizon_days__in=horizons,
                model_version__model_type='LSTM',
            ).order_by('horizon_days')

        serialized = PredictionResultSerializer(qs, many=True).data
        output = {
            'stock_code': asset.symbol,
            'date': str(target_date),
            'results': [],
        }
        for row in serialized:
            output['results'].append({
                'horizon_days': row['horizon_days'],
                'up': row['up_probability'],
                'flat': row['flat_probability'],
                'down': row['down_probability'],
                'confidence': row['confidence'],
                'predicted_label': row['predicted_label'],
                'model_version': row['model_version_name'],
                'target_price': row['target_price'],
                'stop_loss_price': row['stop_loss_price'],
                'risk_reward_ratio': row['risk_reward_ratio'],
                'trade_score': row['trade_score'],
                'suggested': row['suggested'],
            })

        return Response(output)

    @action(detail=False, methods=['post'])
    def train(self, request):
        train_lstm_models.delay(
            training_start_date=request.data.get('training_start_date'),
            training_end_date=request.data.get('training_end_date'),
            horizons=request.data.get('horizons', [3, 7, 30]),
            sequence_length=request.data.get('sequence_length', 20),
            asset_chunk_size=request.data.get('asset_chunk_size', 60),
            max_samples_per_horizon=request.data.get('max_samples_per_horizon', 30000),
        )
        return Response(
            {'message': 'LSTM model training queued.'},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=['post'])
    def recalculate(self, request):
        generate_lstm_predictions_for_date.delay(
            target_date=request.data.get('target_date'),
            horizons=request.data.get('horizons', [3, 7, 30]),
        )
        return Response(
            {'message': 'LSTM prediction generation queued.'},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=['post'])
    def batch(self, request):
        stock_codes = request.data.get('stock_codes', [])
        if not isinstance(stock_codes, list) or not stock_codes:
            return Response({'detail': 'stock_codes must be a non-empty list.'}, status=status.HTTP_400_BAD_REQUEST)

        date_str = request.data.get('date')
        if date_str:
            try:
                target_date = date.fromisoformat(str(date_str))
            except ValueError:
                target_date = timezone.now().date()
        else:
            target_date = timezone.now().date()

        horizons = request.data.get('horizons', [3, 7, 30])
        if not isinstance(horizons, list):
            horizons = [3, 7, 30]
        horizons = [int(h) for h in horizons if str(h).isdigit()]
        horizons = horizons or [3, 7, 30]

        generate_lstm_predictions_for_date.delay(
            target_date=str(target_date),
            horizons=horizons,
        )

        assets = Asset.objects.filter(symbol__in=stock_codes)
        rows = PredictionResult.objects.select_related('asset', 'model_version').filter(
            asset__in=assets,
            date=target_date,
            horizon_days__in=horizons,
            model_version__model_type='LSTM',
        ).order_by('asset__symbol', 'horizon_days')

        grouped = {}
        for row in rows:
            grouped.setdefault(row.asset.symbol, []).append({
                'horizon_days': row.horizon_days,
                'up': float(row.up_probability),
                'flat': float(row.flat_probability),
                'down': float(row.down_probability),
                'confidence': float(row.confidence),
                'predicted_label': row.predicted_label,
                'model_version': row.model_version.version if row.model_version else None,
                'target_price': float(row.target_price) if row.target_price is not None else None,
                'stop_loss_price': float(row.stop_loss_price) if row.stop_loss_price is not None else None,
                'risk_reward_ratio': float(row.risk_reward_ratio) if row.risk_reward_ratio is not None else None,
                'trade_score': float(row.trade_score) if row.trade_score is not None else None,
                'suggested': row.suggested,
            })

        return Response({'date': str(target_date), 'results': grouped})
