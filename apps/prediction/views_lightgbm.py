from datetime import date

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.markets.models import Asset
from .models_lightgbm import (
    EnsembleWeightSnapshot,
    FeatureImportanceSnapshot,
    LightGBMModelArtifact,
    LightGBMPrediction,
)
from .serializers_lightgbm import (
    LightGBMModelArtifactSerializer,
    LightGBMPredictionSerializer,
    EnsembleWeightSnapshotSerializer,
    FeatureImportanceSnapshotSerializer,
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

    @action(detail=False, methods=['get'], url_path='feature-importance-trends')
    def feature_importance_trends(self, request):
        horizon = request.query_params.get('horizon_days')
        limit_models = request.query_params.get('limit_models', '5')
        top_n = request.query_params.get('top_n', '10')

        try:
            limit_models = max(1, min(int(limit_models), 20))
        except ValueError:
            limit_models = 5

        try:
            top_n = max(1, min(int(top_n), 50))
        except ValueError:
            top_n = 10

        artifacts = self.get_queryset().filter(status=LightGBMModelArtifact.Status.READY)
        if horizon:
            try:
                artifacts = artifacts.filter(horizon_days=int(horizon))
            except ValueError:
                pass

        artifacts = list(artifacts[:limit_models])
        snapshots = FeatureImportanceSnapshot.objects.select_related('model_artifact').filter(model_artifact__in=artifacts)
        serialized_rows = FeatureImportanceSnapshotSerializer(snapshots, many=True).data

        grouped = {}
        for row in serialized_rows:
            horizon_key = row['horizon_days']
            grouped.setdefault(horizon_key, {})
            grouped[horizon_key].setdefault(row['feature_name'], []).append({
                'model_artifact': row['model_artifact'],
                'model_version': row['model_version'],
                'trained_at': row['trained_at'],
                'importance_score': row['importance_score'],
                'importance_rank': row['importance_rank'],
                'created_at': row['created_at'],
            })

        results = []
        for horizon_key, feature_map in sorted(grouped.items()):
            ranked_features = sorted(
                feature_map.items(),
                key=lambda item: sum(point['importance_score'] for point in item[1]),
                reverse=True,
            )[:top_n]
            feature_trends = []
            for feature_name, history in ranked_features:
                feature_trends.append({
                    'feature_name': feature_name,
                    'snapshots': sorted(
                        history,
                        key=lambda item: item['trained_at'] or item['created_at'] or '',
                    ),
                })
            results.append({
                'horizon_days': horizon_key,
                'feature_trends': feature_trends,
            })

        return Response({
            'limit_models': limit_models,
            'top_n': top_n,
            'results': results,
        })


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
        qs = LightGBMPrediction.objects.select_related('asset', 'model_artifact').filter(
            asset=asset,
            date=target_date,
            horizon_days__in=horizons,
        ).order_by('horizon_days')

        if not qs.exists():
            generate_lightgbm_prediction_for_asset(
                asset_id=asset.id,
                target_date=str(target_date),
                horizons=horizons,
            )
            qs = LightGBMPrediction.objects.select_related('asset', 'model_artifact').filter(
                asset=asset,
                date=target_date,
                horizon_days__in=horizons,
            ).order_by('horizon_days')

        results = LightGBMPredictionSerializer(qs, many=True).data
        output = {
            'stock_code': asset.symbol,
            'date': str(target_date),
            'results': [],
        }
        for row in results:
            output['results'].append({
                'horizon_days': row['horizon_days'],
                'up': row['up_probability'],
                'flat': row['flat_probability'],
                'down': row['down_probability'],
                'confidence': row['confidence'],
                'predicted_label': row['predicted_label'],
                'model_version': row['model_version'],
                'target_price': row['target_price'],
                'stop_loss_price': row['stop_loss_price'],
                'risk_reward_ratio': row['risk_reward_ratio'],
                'trade_score': row['trade_score'],
                'suggested': row['suggested'],
            })

        return Response(output)

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
                'target_price': float(row.target_price) if row.target_price is not None else None,
                'stop_loss_price': float(row.stop_loss_price) if row.stop_loss_price is not None else None,
                'risk_reward_ratio': float(row.risk_reward_ratio) if row.risk_reward_ratio is not None else None,
                'trade_score': float(row.trade_score) if row.trade_score is not None else None,
                'suggested': row.suggested,
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
