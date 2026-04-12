from rest_framework import serializers

from .models import NewsArticle, SentimentScore, ConceptHeat


class NewsArticleSerializer(serializers.ModelSerializer):
    related_symbols = serializers.SerializerMethodField()

    class Meta:
        model = NewsArticle
        fields = [
            'id', 'source', 'title', 'url', 'published_at', 'content', 'summary',
            'language', 'related_assets', 'related_symbols', 'concept_tags',
            'metadata', 'created_at',
        ]

    def get_related_symbols(self, obj):
        return list(obj.related_assets.values_list('symbol', flat=True))


class SentimentScoreSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source='asset.symbol', read_only=True)

    class Meta:
        model = SentimentScore
        fields = [
            'id', 'article', 'asset', 'asset_symbol', 'date', 'score_type',
            'positive_score', 'neutral_score', 'negative_score',
            'sentiment_score', 'sentiment_label', 'metadata', 'created_at',
        ]


class ConceptHeatSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConceptHeat
        fields = [
            'id', 'concept_name', 'date', 'heat_score', 'article_count',
            'up_limit_count', 'net_inflow', 'metadata', 'created_at',
        ]
