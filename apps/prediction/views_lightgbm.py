from datetime import date

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.markets.models import Asset
from .models_lightgbm import LightGBMModelArtifact, LightGBMPrediction, EnsembleWeightSnapshot
from .serializers_lightgbm import (
    LightGBMModelArtifactSerializer,
    LightGBMPredictionSerializer,
    EnsembleWeightSnapshotSerializer,
)
from .tasks_lightgbm import (
    generate_lightgbm_predictions_for_date,
    generate_lightgbm_prediction_for_asset,
    train_lightgbm_models,
)


class LightGBMModelArtifactViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Registered LightGBM model artifacts.
    GET /api/v1/lightgbm-models/
    GET /api/v1/lightgbm-models/{id}/
    """
    serializer_class = LightGBMModelArtifactSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = LightGBMModelArtifact.objects.all().order_by('-created_at')
        horizon = self.request.query_params.get('horizon_days')
        if horizon:
            try:
                qs = qs.filter(horizon_days=int(horizon))
            except ValueError:
                pass
        return qs


class LightGBMPredictionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Phase 14 LightGBM predictions (parallel to heuristic baseline).
    
    GET /api/v1/lightgbm-predictions/
    POST /api/v1/lightgbm-predictions/train/
    POST /api/v1/lightgbm-predictions/recalculate/
    POST /api/v1/lightgbm-predictions/batch/
    GET /api/v1/lightgbm-predictions/{stock_code}/
    """
    serializer_class = LightGBMPredictionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = LightGBMPrediction.objects.select_related('asset', 'model_artifact').all().order_by('-date', 'asset__symbol', 'horizon_days')
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

    @action(detail=False, methods=['post'])
    def train(self, request):
        """Queue LightGBM model retraining."""
        train_lightgbm_models.delay(
            training_start_date=request.data.get('training_start_date'),
            training_end_date=request.data.get('training_end_date'),
        )
        return Response(
            {'message': 'LightGBM model training queued.'},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=['post'])
    def recalculate(self, request):
        """Queue LightGBM inference for given date and horizons."""
        generate_lightgbm_predictions_for_date.delay(
            target_date=request.data.get('target_date'),
            horizons=request.data.get('horizons', [3, 7, 30]),
        )
        return Response(
            {'message': 'LightGBM prediction generation queued.'},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=['post'])
    def batch(self, request):
        """Batch LightGBM predictions for multiple stocks."""
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

        generate_lightgbm_predictions_for_date.delay(
            target_date=str(target_date),
            horizons=horizons,
        )

        assets = Asset.objects.filter(symbol__in=stock_codes)
        rows = LightGBMPrediction.objects.select_related('asset').filter(
            asset__in=assets,
            date=target_date,
            horizon_days__in=horizons,
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
            })

        return Response({'date': str(target_date), 'results': grouped})


class EnsembleWeightSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Ensemble weights over time.
    GET /api/v1/ensemble-weights/
    """
    serializer_class = EnsembleWeightSnapshotSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return EnsembleWeightSnapshot.objects.all().order_by('-date')
