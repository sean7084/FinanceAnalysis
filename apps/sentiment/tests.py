from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.factors.tasks import calculate_factor_scores_for_date
from apps.factors.models import FactorScore, FundamentalFactorSnapshot, CapitalFlowSnapshot
from apps.markets.models import Market, Asset
from .models import NewsArticle, SentimentScore
from .tasks import calculate_daily_sentiment, fetch_latest_market_news, ingest_latest_news, run_hourly_historical_news_backfill


class Phase13SentimentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='phase13_user',
            email='phase13@example.com',
            password='Passw0rd!123',
        )
        self.market = Market.objects.create(code='P13', name='Phase 13 Market')
        self.asset = Asset.objects.create(
            market=self.market,
            symbol='603001',
            ts_code='603001.SH',
            name='Sentiment Asset',
        )

    def _auth(self):
        self.client.force_authenticate(user=self.user)

    def test_sentiment_requires_auth(self):
        response = self.client.get('/api/v1/sentiment/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_daily_sentiment_creates_article_and_asset_scores(self):
        article = NewsArticle.objects.create(
            source=NewsArticle.Source.SINA,
            title='公司业绩增长 利好',
            summary='盈利改善 突破',
            content='增长 利好 看涨',
            url='https://example.com/news-1',
            published_at=timezone.now(),
        )
        article.related_assets.add(self.asset)

        calculate_daily_sentiment(target_date=str(timezone.now().date()))

        self.assertTrue(
            SentimentScore.objects.filter(article=article, score_type=SentimentScore.ScoreType.ARTICLE).exists()
        )
        self.assertTrue(
            SentimentScore.objects.filter(asset=self.asset, score_type=SentimentScore.ScoreType.ARTICLE).exists()
        )

    @patch('apps.sentiment.views.run_daily_sentiment_pipeline.delay')
    def test_recalculate_endpoint_queues_pipeline(self, mock_delay):
        self._auth()
        response = self.client.post('/api/v1/sentiment/recalculate/', {'target_date': str(timezone.now().date())}, format='json')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once()

    def test_sentiment_integrates_into_factor_score(self):
        d = timezone.now().date()
        FundamentalFactorSnapshot.objects.create(
            asset=self.asset, date=d,
            pe=Decimal('9'), pb=Decimal('1.2'), roe=Decimal('0.12'), roe_qoq=Decimal('0.03'),
        )
        CapitalFlowSnapshot.objects.create(
            asset=self.asset, date=d,
            main_force_net_5d=Decimal('800000'),
            margin_balance_change_5d=Decimal('200000'),
        )
        SentimentScore.objects.create(
            article=None,
            asset=self.asset,
            date=d,
            score_type=SentimentScore.ScoreType.ASSET_7D,
            positive_score=Decimal('0.7'),
            neutral_score=Decimal('0.2'),
            negative_score=Decimal('0.1'),
            sentiment_score=Decimal('0.6'),
            sentiment_label=SentimentScore.Label.POSITIVE,
        )

        calculate_factor_scores_for_date(
            target_date=str(d),
            financial_weight=0.35,
            flow_weight=0.25,
            technical_weight=0.20,
            sentiment_weight=0.20,
        )

        fs = FactorScore.objects.get(asset=self.asset, date=d, mode=FactorScore.FactorMode.COMPOSITE)
        self.assertIsNotNone(fs.sentiment_score)
        self.assertGreater(fs.sentiment_weight, 0)

    def test_sentiment_latest_endpoint(self):
        self._auth()
        d = timezone.now().date()
        SentimentScore.objects.create(
            article=None,
            asset=self.asset,
            date=d,
            score_type=SentimentScore.ScoreType.ASSET_7D,
            positive_score=Decimal('0.6'),
            neutral_score=Decimal('0.3'),
            negative_score=Decimal('0.1'),
            sentiment_score=Decimal('0.5'),
            sentiment_label=SentimentScore.Label.POSITIVE,
        )
        response = self.client.get(f'/api/v1/sentiment/latest/?asset={self.asset.id}&score_type=ASSET_7D&date={d}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)

    def test_ingest_links_assets_and_infers_concepts(self):
        message = ingest_latest_news(news_items=[{
            'source': NewsArticle.Source.EASTMONEY,
            'title': 'Sentiment Asset 切入 AI 芯片 赛道',
            'summary': '603001 推进半导体和人工智能业务。',
            'content': '公司在芯片与人工智能领域持续扩张。',
            'url': 'https://example.com/real-news',
            'published_at': timezone.now(),
            'language': 'zh',
            'concept_tags': ['自定义概念'],
            'metadata': {'provider': 'test'},
        }])

        article = NewsArticle.objects.get(url='https://example.com/real-news')
        self.assertIn('Created', message)
        self.assertEqual(list(article.related_assets.values_list('id', flat=True)), [self.asset.id])
        self.assertIn('AI', article.concept_tags)
        self.assertIn('芯片', article.concept_tags)
        self.assertIn('自定义概念', article.concept_tags)

    @patch('apps.sentiment.tasks.fetch_normalized_news_items')
    def test_fetch_latest_market_news_handles_provider_payloads(self, mock_fetch):
        mock_fetch.return_value = [{
            'source': NewsArticle.Source.SINA,
            'title': 'Sentiment Asset 利好',
            'summary': '增长突破',
            'content': 'Sentiment Asset 业绩增长',
            'url': 'https://example.com/provider-news',
            'published_at': timezone.now(),
            'language': 'zh',
            'concept_tags': [],
            'metadata': {'provider': 'mock'},
        }]

        message = fetch_latest_market_news(providers=['sina'], limit_per_provider=5)
        self.assertIn('Fetched 1 items', message)
        self.assertTrue(NewsArticle.objects.filter(url='https://example.com/provider-news').exists())

    @patch('apps.sentiment.management.commands.backfill_news.fetch_normalized_news_items')
    def test_backfill_command_ingests_rows(self, mock_fetch):
        mock_fetch.return_value = [{
            'source': NewsArticle.Source.TONGHUASHUN,
            'title': 'Sentiment Asset 获得新订单',
            'summary': '业务扩张',
            'content': 'Sentiment Asset 业务扩张并获得新订单。',
            'url': 'https://example.com/command-news',
            'published_at': timezone.now(),
            'language': 'zh',
            'concept_tags': [],
            'metadata': {'provider': 'mock'},
        }]

        call_command('backfill_news', '--providers=eastmoney', '--limit-per-provider=2')
        self.assertTrue(NewsArticle.objects.filter(url='https://example.com/command-news').exists())

    @patch('apps.sentiment.tasks.fetch_normalized_news_items')
    def test_hourly_historical_backfill_fetches_next_window(self, mock_fetch):
        NewsArticle.objects.create(
            source=NewsArticle.Source.SINA,
            title='Current boundary',
            summary='Boundary row',
            content='Boundary row',
            url='https://example.com/boundary-news',
            published_at=timezone.make_aware(timezone.datetime(2026, 4, 7)),
        )
        mock_fetch.return_value = [{
            'source': NewsArticle.Source.OTHER,
            'title': 'Historical row',
            'summary': 'Historical summary',
            'content': 'Historical content',
            'url': 'https://example.com/historical-news',
            'published_at': timezone.make_aware(timezone.datetime(2026, 4, 1)),
            'language': 'zh',
            'concept_tags': [],
            'metadata': {'provider': 'mock'},
        }]

        message = run_hourly_historical_news_backfill()

        self.assertIn('Historical backfill window 2026-03-07 00:00:00 -> 2026-04-06 23:59:59', message)
        self.assertTrue(NewsArticle.objects.filter(url='https://example.com/historical-news').exists())
        mock_fetch.assert_called_once_with(
            providers=['tushare_major'],
            limit_per_provider=0,
            start_at='2026-03-07 00:00:00',
            end_at='2026-04-06 23:59:59',
        )

    @patch('apps.sentiment.tasks.fetch_normalized_news_items')
    def test_hourly_historical_backfill_returns_quota_message(self, mock_fetch):
        NewsArticle.objects.create(
            source=NewsArticle.Source.SINA,
            title='Current boundary',
            summary='Boundary row',
            content='Boundary row',
            url='https://example.com/boundary-news-2',
            published_at=timezone.make_aware(timezone.datetime(2026, 4, 7)),
        )
        mock_fetch.side_effect = Exception('抱歉，您每小时最多访问该接口4次')

        message = run_hourly_historical_news_backfill()

        self.assertIn('Historical backfill deferred for tushare_major', message)
        self.assertIn('provider quota', message)
