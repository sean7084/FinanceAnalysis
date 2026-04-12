from datetime import date

from django.db.models import Avg
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import NewsArticle, SentimentScore, ConceptHeat
from .serializers import NewsArticleSerializer, SentimentScoreSerializer, ConceptHeatSerializer
from .tasks import ingest_latest_news, run_daily_sentiment_pipeline


class NewsArticleViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NewsArticleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = NewsArticle.objects.prefetch_related('related_assets').all().order_by('-published_at')
        source = self.request.query_params.get('source')
        if source:
            qs = qs.filter(source=source)
        return qs

    @action(detail=False, methods=['post'])
    def ingest(self, request):
        ingest_latest_news.delay(news_items=request.data.get('items', []))
        return Response({'message': 'News ingest queued.'}, status=status.HTTP_202_ACCEPTED)


class SentimentScoreViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Main Phase 13 endpoint: /api/v1/sentiment/
    """
    serializer_class = SentimentScoreSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = SentimentScore.objects.select_related('asset', 'article').all().order_by('-date', '-created_at')
        score_type = self.request.query_params.get('score_type')
        asset_id = self.request.query_params.get('asset')
        if score_type:
            qs = qs.filter(score_type=score_type)
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        return qs

    @action(detail=False, methods=['get'])
    def latest(self, request):
        asset_id = request.query_params.get('asset')
        score_type = request.query_params.get('score_type', SentimentScore.ScoreType.ASSET_7D)
        date_str = request.query_params.get('date')

        if date_str:
            try:
                target_date = date.fromisoformat(date_str)
            except ValueError:
                target_date = timezone.now().date()
        else:
            target_date = timezone.now().date()

        qs = SentimentScore.objects.filter(date=target_date, score_type=score_type)
        if asset_id:
            qs = qs.filter(asset_id=asset_id)
        if not asset_id and score_type == SentimentScore.ScoreType.ASSET_7D:
            agg = qs.aggregate(avg_sentiment=Avg('sentiment_score'))
            return Response({'date': str(target_date), 'score_type': score_type, 'avg_sentiment': agg['avg_sentiment']})

        data = SentimentScoreSerializer(qs.order_by('-sentiment_score')[:50], many=True).data
        return Response({'date': str(target_date), 'score_type': score_type, 'results': data})

    @action(detail=False, methods=['post'])
    def recalculate(self, request):
        run_daily_sentiment_pipeline.delay(target_date=request.data.get('target_date'))
        return Response({'message': 'Sentiment pipeline queued.'}, status=status.HTTP_202_ACCEPTED)


class ConceptHeatViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ConceptHeatSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = ConceptHeat.objects.all().order_by('-date', '-heat_score')
        concept = self.request.query_params.get('concept_name')
        if concept:
            qs = qs.filter(concept_name=concept)
        return qs

    @action(detail=False, methods=['get'])
    def top(self, request):
        limit = int(request.query_params.get('limit', 20))
        latest_date = ConceptHeat.objects.order_by('-date').values_list('date', flat=True).first()
        if not latest_date:
            return Response({'results': []})
        rows = ConceptHeatSerializer(
            ConceptHeat.objects.filter(date=latest_date).order_by('-heat_score')[:max(1, min(100, limit))],
            many=True,
        ).data
        return Response({'date': str(latest_date), 'results': rows})
