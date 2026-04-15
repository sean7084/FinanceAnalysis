from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import re

from celery import shared_task
from django.conf import settings
from django.db.models import Avg, Min
from django.utils import timezone

from apps.markets.models import Asset
from .models import NewsArticle, SentimentScore, ConceptHeat
from .providers import DEFAULT_PROVIDER_NAMES, fetch_normalized_news_items

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

CONCEPT_KEYWORDS = {
    'AI': {'ai', '人工智能', '大模型', '算力'},
    '芯片': {'芯片', '半导体', '集成电路', '光刻'},
    '新能源': {'新能源', '锂电', '光伏', '储能', '电池'},
    '白酒': {'白酒', '茅台', '五粮液', '泸州老窖'},
    '医药': {'医药', '创新药', '医疗', '医保'},
    '消费': {'消费', '零售', '电商', '食品饮料'},
    '银行': {'银行', '信贷', '存款', '贷款'},
    '保险': {'保险', '寿险', '财险'},
    '地产': {'地产', '房地产', '楼市', '物业'},
    '军工': {'军工', '航天', '航空装备', '国防'},
    '汽车': {'汽车', '新能源车', 'robotaxi', '无人驾驶'},
    '航运': {'航运', '港口', '海运'},
    '航空': {'航空', '客机', '机场'},
}

LEGAL_NAME_SUFFIXES = ('股份有限公司', '有限公司', '股份', '集团', '科技', '控股')

ASSET_ALIAS_OVERRIDES = {
    '贵州茅台': {'茅台'},
    '比亚迪': {'BYD'},
    '中国平安': {'平安'},
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


def _normalize_match_text(text):
    if not text:
        return ''
    return re.sub(r'\s+', '', str(text).lower())


def _asset_aliases(asset):
    aliases = {asset.name.strip(), asset.symbol.strip(), asset.ts_code.strip().lower()}
    stripped = asset.name.strip()
    for suffix in LEGAL_NAME_SUFFIXES:
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)].strip()
    if len(stripped) >= 2:
        aliases.add(stripped)
    aliases.update(ASSET_ALIAS_OVERRIDES.get(asset.name.strip(), set()))
    return {alias for alias in aliases if alias}


def _build_asset_match_index():
    assets = Asset.objects.filter(listing_status=Asset.ListingStatus.ACTIVE).only('id', 'symbol', 'ts_code', 'name')
    entries = []
    for asset in assets:
        for alias in _asset_aliases(asset):
            entries.append((alias, _normalize_match_text(alias), asset))
    entries.sort(key=lambda item: len(item[1]), reverse=True)
    return entries


def _match_related_assets(title, summary, content, index, max_matches=8):
    raw_text = ' '.join(filter(None, [title, summary, content]))
    normalized_text = _normalize_match_text(raw_text)
    matched_assets = []
    matched_ids = set()

    for raw_alias, normalized_alias, asset in index:
        if asset.id in matched_ids:
            continue
        if raw_alias.isdigit():
            if not re.search(rf'(?<!\d){re.escape(raw_alias)}(?!\d)', raw_text):
                continue
        elif normalized_alias not in normalized_text:
            continue
        matched_assets.append(asset)
        matched_ids.add(asset.id)
        if len(matched_assets) >= max_matches:
            break

    return matched_assets


def _infer_concept_tags(title, summary, content):
    normalized_text = _normalize_match_text(' '.join(filter(None, [title, summary, content])))
    matched = []
    for concept, keywords in CONCEPT_KEYWORDS.items():
        if any(_normalize_match_text(keyword) in normalized_text for keyword in keywords):
            matched.append(concept)
    return matched


def _prepare_news_items(news_items):
    asset_index = _build_asset_match_index()
    prepared = []

    for item in news_items or []:
        title = str(item.get('title') or '').strip()
        summary = str(item.get('summary') or '').strip()
        content = str(item.get('content') or '').strip()
        matched_assets = _match_related_assets(title, summary, content, asset_index)
        inferred_concepts = _infer_concept_tags(title, summary, content)
        concept_tags = sorted({str(tag).strip() for tag in (item.get('concept_tags') or []) if str(tag).strip()} | set(inferred_concepts))
        metadata = dict(item.get('metadata') or {})
        metadata.update({
            'matched_asset_ids': [asset.id for asset in matched_assets],
            'matched_symbols': [asset.symbol for asset in matched_assets],
            'asset_match_count': len(matched_assets),
            'concept_tags_inferred': inferred_concepts,
            'ingested_at': timezone.now().isoformat(),
        })
        prepared.append({
            'source': item.get('source', NewsArticle.Source.OTHER),
            'title': title,
            'url': str(item.get('url') or '').strip(),
            'published_at': item.get('published_at') or timezone.now(),
            'content': content,
            'summary': summary,
            'language': str(item.get('language') or 'zh'),
            'concept_tags': concept_tags,
            'metadata': metadata,
            '_matched_asset_ids': [asset.id for asset in matched_assets],
        })

    return prepared


def _parse_backfill_floor(value):
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            parsed = datetime.strptime(value, fmt)
            return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed
        except ValueError:
            continue
    raise ValueError(f'Invalid NEWS_BACKFILL_FLOOR: {value}')


def _compute_historical_backfill_window(chunk_days, floor_value):
    earliest = NewsArticle.objects.aggregate(value=Min('published_at'))['value']
    if earliest is None:
        return None, None, 'No NewsArticle rows exist yet. Seed current news before historical auto-backfill.'

    floor = _parse_backfill_floor(floor_value)
    if earliest <= floor:
        return None, None, f'Historical backfill already reached floor {floor.isoformat()}.'

    window_end = earliest - timedelta(seconds=1)
    window_start = max(floor, window_end - timedelta(days=max(1, int(chunk_days))) + timedelta(seconds=1))
    return window_start, window_end, None


@shared_task
def ingest_latest_news(news_items=None):
    """Create NewsArticle records from provided items (integration point for AkShare/news crawlers)."""
    prepared_items = _prepare_news_items(news_items)
    created = 0
    updated = 0
    linked = 0

    for item in prepared_items:
        matched_asset_ids = item.pop('_matched_asset_ids', [])
        published = item.get('published_at')
        if isinstance(published, str):
            try:
                published = timezone.datetime.fromisoformat(published)
            except ValueError:
                published = timezone.now()
        published = published or timezone.now()

        obj, was_created = NewsArticle.objects.update_or_create(
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
        if matched_asset_ids:
            obj.related_assets.set(Asset.objects.filter(id__in=matched_asset_ids))
            linked += 1
        elif not was_created:
            obj.related_assets.clear()

        if was_created:
            created += 1
        else:
            updated += 1

    return f'News ingest complete. Created {created}, updated {updated}, linked {linked} records.'


@shared_task
def fetch_latest_market_news(providers=None, limit_per_provider=50, start_at=None, end_at=None):
    provider_names = providers or DEFAULT_PROVIDER_NAMES
    payloads = []
    errors = []

    for provider_name in provider_names:
        try:
            payloads.extend(
                fetch_normalized_news_items(
                    providers=[provider_name],
                    limit_per_provider=int(limit_per_provider),
                    start_at=start_at,
                    end_at=end_at,
                )
            )
        except Exception as exc:
            errors.append(f'{provider_name}: {exc}')

    ingest_message = ingest_latest_news(news_items=payloads)
    if errors:
        return f'Fetched {len(payloads)} items with provider errors: {"; ".join(errors)}. {ingest_message}'
    return f'Fetched {len(payloads)} items. {ingest_message}'


@shared_task
def run_hourly_historical_news_backfill():
    if not getattr(settings, 'NEWS_BACKFILL_ENABLED', True):
        return 'Historical news backfill is disabled by settings.'

    provider = getattr(settings, 'NEWS_BACKFILL_PROVIDER', 'tushare_major')
    chunk_days = int(getattr(settings, 'NEWS_BACKFILL_CHUNK_DAYS', 31))
    floor_value = getattr(settings, 'NEWS_BACKFILL_FLOOR', '2021-04-15 00:00:00')
    limit_per_provider = int(getattr(settings, 'NEWS_BACKFILL_LIMIT_PER_PROVIDER', 0))

    window_start, window_end, status_message = _compute_historical_backfill_window(chunk_days, floor_value)
    if status_message:
        return status_message

    start_at = window_start.strftime('%Y-%m-%d %H:%M:%S')
    end_at = window_end.strftime('%Y-%m-%d %H:%M:%S')

    try:
        items = fetch_normalized_news_items(
            providers=[provider],
            limit_per_provider=limit_per_provider,
            start_at=start_at,
            end_at=end_at,
        )
    except Exception as exc:
        message = str(exc)
        if '最多访问该接口' in message:
            return (
                f'Historical backfill deferred for {provider} '
                f'({start_at} -> {end_at}) due to provider quota: {message}'
            )
        raise

    ingest_message = ingest_latest_news(news_items=items)
    return (
        f'Historical backfill window {start_at} -> {end_at} fetched {len(items)} items via {provider}. '
        f'{ingest_message}'
    )


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

    aggregated_asset_ids = set()
    for row in asset_daily:
        aggregated_asset_ids.add(row['asset'])
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

    # Ensure each active asset has a 7d sentiment row to avoid N/A UI states.
    for asset in Asset.objects.filter(listing_status=Asset.ListingStatus.ACTIVE):
        if asset.id in aggregated_asset_ids:
            continue
        SentimentScore.objects.update_or_create(
            article=None,
            asset=asset,
            date=d,
            score_type=SentimentScore.ScoreType.ASSET_7D,
            defaults={
                'positive_score': Decimal('0.000000'),
                'neutral_score': Decimal('1.000000'),
                'negative_score': Decimal('0.000000'),
                'sentiment_score': Decimal('0.000000'),
                'sentiment_label': SentimentScore.Label.NEUTRAL,
                'metadata': {'window_days': 7, 'fallback': 'no_related_article'},
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
