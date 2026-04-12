from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.markets.models import Asset


class NewsArticle(models.Model):
    class Source(models.TextChoices):
        EASTMONEY = 'EASTMONEY', _('Eastmoney')
        TONGHUASHUN = 'TONGHUASHUN', _('Tonghuashun')
        SINA = 'SINA', _('Sina Finance')
        EXCHANGE = 'EXCHANGE', _('Exchange Announcement')
        OTHER = 'OTHER', _('Other')

    source = models.CharField(_('Source'), max_length=20, choices=Source.choices, default=Source.OTHER)
    title = models.CharField(_('Title'), max_length=500)
    url = models.URLField(_('URL'), max_length=1000, unique=True)
    published_at = models.DateTimeField(_('Published At'), db_index=True)
    content = models.TextField(_('Content'), blank=True)
    summary = models.TextField(_('Summary'), blank=True)
    language = models.CharField(_('Language'), max_length=10, default='zh')
    related_assets = models.ManyToManyField(Asset, related_name='news_articles', blank=True)
    concept_tags = models.JSONField(_('Concept Tags'), default=list, blank=True)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('News Article')
        verbose_name_plural = _('News Articles')
        ordering = ['-published_at']
        indexes = [
            models.Index(fields=['source', 'published_at']),
        ]


class SentimentScore(models.Model):
    class ScoreType(models.TextChoices):
        ARTICLE = 'ARTICLE', _('Article Sentiment')
        ASSET_7D = 'ASSET_7D', _('Asset 7D Aggregation')
        MARKET_7D = 'MARKET_7D', _('Market 7D Aggregation')

    class Label(models.TextChoices):
        POSITIVE = 'POSITIVE', _('Positive')
        NEUTRAL = 'NEUTRAL', _('Neutral')
        NEGATIVE = 'NEGATIVE', _('Negative')

    article = models.ForeignKey(
        NewsArticle,
        on_delete=models.CASCADE,
        related_name='sentiment_scores',
        null=True,
        blank=True,
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name='sentiment_scores',
        null=True,
        blank=True,
    )
    date = models.DateField(_('Date'), db_index=True)
    score_type = models.CharField(_('Score Type'), max_length=20, choices=ScoreType.choices, db_index=True)
    positive_score = models.DecimalField(_('Positive Score'), max_digits=7, decimal_places=6)
    neutral_score = models.DecimalField(_('Neutral Score'), max_digits=7, decimal_places=6)
    negative_score = models.DecimalField(_('Negative Score'), max_digits=7, decimal_places=6)
    sentiment_score = models.DecimalField(_('Sentiment Score'), max_digits=8, decimal_places=6)
    sentiment_label = models.CharField(_('Sentiment Label'), max_length=10, choices=Label.choices)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Sentiment Score')
        verbose_name_plural = _('Sentiment Scores')
        ordering = ['-date', '-created_at']
        indexes = [
            models.Index(fields=['asset', 'date', 'score_type']),
            models.Index(fields=['score_type', 'date']),
        ]
        unique_together = ('article', 'asset', 'date', 'score_type')


class ConceptHeat(models.Model):
    concept_name = models.CharField(_('Concept Name'), max_length=120, db_index=True)
    date = models.DateField(_('Date'), db_index=True)
    heat_score = models.DecimalField(_('Heat Score'), max_digits=8, decimal_places=4)
    article_count = models.PositiveIntegerField(_('Article Count'), default=0)
    up_limit_count = models.PositiveIntegerField(_('Up-Limit Count'), default=0)
    net_inflow = models.DecimalField(_('Net Inflow'), max_digits=18, decimal_places=4, default=0)
    metadata = models.JSONField(_('Metadata'), default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Concept Heat')
        verbose_name_plural = _('Concept Heat')
        ordering = ['-date', '-heat_score']
        unique_together = ('concept_name', 'date')
