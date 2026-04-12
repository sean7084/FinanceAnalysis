from django.contrib import admin

from .models import NewsArticle, SentimentScore, ConceptHeat


@admin.register(NewsArticle)
class NewsArticleAdmin(admin.ModelAdmin):
    list_display = ('source', 'title', 'published_at')
    list_filter = ('source', 'published_at')
    search_fields = ('title', 'url')
    date_hierarchy = 'published_at'


@admin.register(SentimentScore)
class SentimentScoreAdmin(admin.ModelAdmin):
    list_display = ('asset', 'date', 'score_type', 'sentiment_score', 'sentiment_label')
    list_filter = ('score_type', 'sentiment_label', 'date')
    search_fields = ('asset__symbol', 'article__title')
    date_hierarchy = 'date'


@admin.register(ConceptHeat)
class ConceptHeatAdmin(admin.ModelAdmin):
    list_display = ('concept_name', 'date', 'heat_score', 'article_count')
    list_filter = ('date',)
    search_fields = ('concept_name',)
    date_hierarchy = 'date'
