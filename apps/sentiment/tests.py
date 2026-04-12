from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.factors.tasks import calculate_factor_scores_for_date
from apps.factors.models import FactorScore, FundamentalFactorSnapshot, CapitalFlowSnapshot
from apps.markets.models import Market, Asset
from .models import NewsArticle, SentimentScore
from .tasks import calculate_daily_sentiment


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
            northbound_net_5d=Decimal('1000000'),
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
