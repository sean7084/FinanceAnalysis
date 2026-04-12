from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import re

from celery import shared_task
from django.db.models import Avg
from django.utils import timezone

from .models import NewsArticle, SentimentScore, ConceptHeat

try:
    import jieba  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    jieba = None


POSITIVE_WORDS = {
    '增长', '利好', '突破', '看涨', '回升', '改善', '超预期', '盈利',
    'buy', 'bullish', 'upgrade', 'outperform',
}
NEGATIVE_WORDS = {
    '下滑', '利空', '下跌', '看跌', '风险', '亏损', '恶化', '裁员',
    'sell', 'bearish', 'downgrade', 'underperform',
}


def _tokenize(text):
    if not text:
        return []
    if jieba is not None:
        return [t.strip().lower() for t in jieba.lcut(text) if t.strip()]
    return [t.lower() for t in re.findall(r'[\\w\\u4e00-\\u9fff]+', text)]


def _score_text(text):
    tokens = _tokenize(text)
    if not tokens:
        return Decimal('0.333333'), Decimal('0.333333'), Decimal('0.333333'), Decimal('0')

    pos = sum(1 for t in tokens if t in POSITIVE_WORDS)
    neg = sum(1 for t in tokens if t in NEGATIVE_WORDS)
    total = max(len(tokens), 1)

    raw = Decimal(pos - neg) / Decimal(total)
    sentiment = max(Decimal('-1'), min(Decimal('1'), raw * Decimal('8')))

    positive = max(Decimal('0'), sentiment)
    negative = max(Decimal('0'), -sentiment)
    neutral = Decimal('1') - min(Decimal('1'), positive + negative)

    norm = positive + neutral + negative
    if norm <= 0:
        return Decimal('0.333333'), Decimal('0.333333'), Decimal('0.333333'), Decimal('0')

    return (
        (positive / norm).quantize(Decimal('0.000001')),
        (neutral / norm).quantize(Decimal('0.000001')),
        (negative / norm).quantize(Decimal('0.000001')),
        sentiment.quantize(Decimal('0.000001')),
    )


def _label(score):
    if score >= Decimal('0.15'):
        return SentimentScore.Label.POSITIVE
    if score <= Decimal('-0.15'):
        return SentimentScore.Label.NEGATIVE
    return SentimentScore.Label.NEUTRAL


@shared_task
def ingest_latest_news(news_items=None):
    """Create NewsArticle records from provided items (integration point for AkShare/news crawlers)."""
    news_items = news_items or []
    created = 0
    for item in news_items:
        published = item.get('published_at')
        if isinstance(published, str):
            try:
                published = timezone.datetime.fromisoformat(published)
            except ValueError:
                published = timezone.now()
        published = published or timezone.now()

        obj, was_created = NewsArticle.objects.get_or_create(
            url=item['url'],
            defaults={
                'source': item.get('source', NewsArticle.Source.OTHER),
                'title': item.get('title', ''),
                'published_at': published,
                'content': item.get('content', ''),
                'summary': item.get('summary', ''),
                'language': item.get('language', 'zh'),
                'concept_tags': item.get('concept_tags', []),
                'metadata': item.get('metadata', {}),
            },
        )
        if was_created:
            created += 1
    return f'News ingest complete. Created {created} records.'


@shared_task
def calculate_daily_sentiment(target_date=None):
    if target_date:
        try:
            d = date.fromisoformat(str(target_date))
        except ValueError:
            d = timezone.now().date()
    else:
        d = timezone.now().date()

    day_start = timezone.make_aware(timezone.datetime.combine(d, timezone.datetime.min.time()))
    day_end = day_start + timedelta(days=1)

    articles = NewsArticle.objects.filter(published_at__gte=day_start, published_at__lt=day_end)
    for article in articles:
        text = f"{article.title} {article.summary} {article.content}"
        pos, neu, neg, sent = _score_text(text)

        SentimentScore.objects.update_or_create(
            article=article,
            asset=None,
            date=d,
            score_type=SentimentScore.ScoreType.ARTICLE,
            defaults={
                'positive_score': pos,
                'neutral_score': neu,
                'negative_score': neg,
                'sentiment_score': sent,
                'sentiment_label': _label(sent),
                'metadata': {'source': article.source},
            },
        )

        for asset in article.related_assets.all():
            SentimentScore.objects.update_or_create(
                article=article,
                asset=asset,
                date=d,
                score_type=SentimentScore.ScoreType.ARTICLE,
                defaults={
                    'positive_score': pos,
                    'neutral_score': neu,
                    'negative_score': neg,
                    'sentiment_score': sent,
                    'sentiment_label': _label(sent),
                    'metadata': {'source': article.source},
                },
            )

    # Build 7d aggregate per asset
    lookback = d - timedelta(days=6)
    asset_daily = (
        SentimentScore.objects.filter(
            score_type=SentimentScore.ScoreType.ARTICLE,
            asset__isnull=False,
            date__gte=lookback,
            date__lte=d,
        )
        .values('asset')
        .annotate(avg_sentiment=Avg('sentiment_score'))
    )

    for row in asset_daily:
        avg_sent = Decimal(str(row['avg_sentiment'] or 0)).quantize(Decimal('0.000001'))
        pos = max(Decimal('0'), avg_sent)
        neg = max(Decimal('0'), -avg_sent)
        neu = max(Decimal('0'), Decimal('1') - min(Decimal('1'), pos + neg))
        SentimentScore.objects.update_or_create(
            article=None,
            asset_id=row['asset'],
            date=d,
            score_type=SentimentScore.ScoreType.ASSET_7D,
            defaults={
                'positive_score': pos,
                'neutral_score': neu,
                'negative_score': neg,
                'sentiment_score': avg_sent,
                'sentiment_label': _label(avg_sent),
                'metadata': {'window_days': 7},
            },
        )

    # Market-wide 7d aggregate
    market_avg = SentimentScore.objects.filter(
        score_type=SentimentScore.ScoreType.ARTICLE,
        date__gte=lookback,
        date__lte=d,
    ).aggregate(avg_sentiment=Avg('sentiment_score'))['avg_sentiment']
    market_avg = Decimal(str(market_avg or 0)).quantize(Decimal('0.000001'))
    pos = max(Decimal('0'), market_avg)
    neg = max(Decimal('0'), -market_avg)
    neu = max(Decimal('0'), Decimal('1') - min(Decimal('1'), pos + neg))
    SentimentScore.objects.update_or_create(
        article=None,
        asset=None,
        date=d,
        score_type=SentimentScore.ScoreType.MARKET_7D,
        defaults={
            'positive_score': pos,
            'neutral_score': neu,
            'negative_score': neg,
            'sentiment_score': market_avg,
            'sentiment_label': _label(market_avg),
            'metadata': {'window_days': 7},
        },
    )

    return f'Sentiment scores updated for {d}'


@shared_task
def calculate_concept_heat(target_date=None):
    if target_date:
        try:
            d = date.fromisoformat(str(target_date))
        except ValueError:
            d = timezone.now().date()
    else:
        d = timezone.now().date()

    day_start = timezone.make_aware(timezone.datetime.combine(d, timezone.datetime.min.time()))
    day_end = day_start + timedelta(days=1)

    counts = defaultdict(int)
    for article in NewsArticle.objects.filter(published_at__gte=day_start, published_at__lt=day_end):
        for tag in article.concept_tags or []:
            if tag:
                counts[str(tag)] += 1

    for concept, count in counts.items():
        heat = Decimal(count)
        ConceptHeat.objects.update_or_create(
            concept_name=concept,
            date=d,
            defaults={
                'heat_score': heat,
                'article_count': count,
                'up_limit_count': 0,
                'net_inflow': Decimal('0'),
                'metadata': {'source': 'news_tag_frequency'},
            },
        )

    return f'Concept heat updated for {d}. Concepts: {len(counts)}'


@shared_task
def run_daily_sentiment_pipeline(target_date=None):
    calculate_daily_sentiment.delay(target_date=target_date)
    calculate_concept_heat.delay(target_date=target_date)
    return 'Daily sentiment pipeline queued.'
