"""The upsert key of SentimentScore must actually be unique when its columns are NULL.

Why these exist: every writer in tasks.py upserts on ``(article, asset, date,
score_type)``, but ``article`` is NULL for ASSET_7D/MARKET_7D rows and ``asset`` is NULL
for article-level rows, and Postgres treats NULLs as distinct -- so ``unique_together``
silently failed to constrain precisely the rows that are written by the million. On
production 2026-11-09 that had produced 2,047 duplicate key groups, and the sentiment
recompute died on five sessions with ``MultipleObjectsReturned``. The dedupe plus
``NULLS NOT DISTINCT`` (sentiment.0004) is the fix; the batched writer is the other commit
in this branch.
"""

from datetime import date, datetime, time
from decimal import Decimal

from django.db import connection, IntegrityError, transaction
from django.db.models import Count
from django.test import TestCase
from django.utils import timezone

from apps.markets.models import Asset, Market
from apps.sentiment.models import NewsArticle, SentimentScore
from apps.sentiment.tasks import calculate_daily_sentiment

SESSION_DAY = date(2026, 5, 19)


def session_start(day):
    """The same window tasks.py uses: the UTC date IS the session date."""
    return timezone.make_aware(datetime.combine(day, time.min))


class UpsertKeyUniquenessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.market = Market.objects.create(code='P49', name='P49 Market')
        cls.asset = Asset.objects.create(
            market=cls.market, symbol='600001', ts_code='600001.SH', name='壹化集团',
            list_date=date(2020, 1, 2),
        )

    def _null_keyed_row(self, score_type):
        return SentimentScore(
            article_id=None,
            asset_id=self.asset.id if score_type == 'ASSET_7D' else None,
            date=SESSION_DAY,
            score_type=score_type,
            positive_score=Decimal('0.000000'),
            neutral_score=Decimal('1.000000'),
            negative_score=Decimal('0.000000'),
            sentiment_score=Decimal('0.000000'),
            sentiment_label=SentimentScore.Label.NEUTRAL,
            metadata={'window_days': 7},
        )

    def _reject_second(self, score_type):
        SentimentScore.objects.bulk_create([self._null_keyed_row(score_type)])
        # assertRaises must be the OUTER context: with atomic outside, the IntegrityError
        # is swallowed and Django then tries to RELEASE a savepoint on an already-aborted
        # transaction, which raises InternalError instead of passing the test.
        with self.assertRaises(IntegrityError), transaction.atomic():
            SentimentScore.objects.bulk_create([self._null_keyed_row(score_type)])

    def test_a_second_null_keyed_asset_row_is_rejected(self):
        """The exact shape that used to slip through: article NULL, asset set."""
        self._reject_second('ASSET_7D')

    def test_a_second_null_keyed_market_row_is_rejected(self):
        """Both FKs NULL -- the other way the old constraint was silent."""
        self._reject_second('MARKET_7D')

    def test_the_schema_declares_nulls_not_distinct(self):
        """Ask the server what it has, rather than trusting a migration name."""
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint '
                'WHERE conrelid = %s::regclass',
                [SentimentScore._meta.db_table],
            )
            definitions = dict(cursor.fetchall())
            cursor.execute(
                'SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s',
                [SentimentScore._meta.db_table],
            )
            definitions.update(dict(cursor.fetchall()))

        matching = [
            name for name, definition in definitions.items()
            if 'NULLS NOT DISTINCT' in definition.upper()
            and 'article_id' in definition and 'asset_id' in definition
        ]
        self.assertEqual(len(matching), 1, definitions)

        stale = [
            name for name, definition in definitions.items()
            if definition.upper().startswith('UNIQUE (article_id, asset_id, date, score_type)')
            and name != matching[0]
        ]
        self.assertEqual(stale, [], definitions)

    def test_no_duplicate_upsert_keys_in_the_test_database(self):
        duplicates = (
            SentimentScore.objects.values('article', 'asset', 'date', 'score_type')
            .annotate(n=Count('id'))
            .filter(n__gt=1)
        )
        self.assertEqual(list(duplicates), [])


class BatchedWriterEquivalenceTests(TestCase):
    """The batched writer must produce what the per-row writer produced."""

    @classmethod
    def setUpTestData(cls):
        cls.day = date(2026, 5, 19)
        cls.market = Market.objects.create(code='P49B', name='P49 Batch')
        cls.mentioned = Asset.objects.create(
            market=cls.market, symbol='600002', ts_code='600002.SH', name='利通电子',
            list_date=date(2020, 1, 2),
        )
        cls.silent = Asset.objects.create(
            market=cls.market, symbol='600003', ts_code='600003.SH', name='飞乐音响',
            list_date=date(2020, 1, 2),
        )
        cls.article = NewsArticle.objects.create(
            title='利通电子业绩大幅增长利好', summary='净利润创历史新高', content='机构看好',
            url='https://example.test/p49/1', source='eastmoney', published_at=session_start(cls.day),
        )
        cls.article.related_assets.add(cls.mentioned)

    def _counts(self):
        return {
            score_type: SentimentScore.objects.filter(date=self.day, score_type=score_type).count()
            for score_type in ('ARTICLE', 'ASSET_7D', 'MARKET_7D')
        }

    def test_running_the_same_session_twice_leaves_one_row_per_key(self):
        calculate_daily_sentiment(target_date=self.day.isoformat())
        first = self._counts()
        calculate_daily_sentiment(target_date=self.day.isoformat())
        self.assertEqual(first, self._counts())
        self.assertEqual(first, {'ARTICLE': 2, 'ASSET_7D': 2, 'MARKET_7D': 1})

    def test_values_follow_the_same_derivation_as_the_loop(self):
        calculate_daily_sentiment(target_date=self.day.isoformat())
        article_level = SentimentScore.objects.get(
            article=self.article, asset__isnull=True, date=self.day, score_type='ARTICLE'
        )
        mentioned = SentimentScore.objects.get(
            article__isnull=True, asset=self.mentioned, date=self.day, score_type='ASSET_7D'
        )
        silent = SentimentScore.objects.get(
            article__isnull=True, asset=self.silent, date=self.day, score_type='ASSET_7D'
        )
        market = SentimentScore.objects.get(
            article__isnull=True, asset__isnull=True, date=self.day, score_type='MARKET_7D'
        )

        self.assertEqual(silent.sentiment_score, Decimal('0.000000'))
        self.assertEqual(silent.sentiment_label, SentimentScore.Label.NEUTRAL)
        self.assertEqual(silent.metadata['fallback'], 'no_related_article')

        # With one mentioning article, the asset's 7d mean IS that article's score, and the
        # market mean is taken over ARTICLE rows -- both must be positive here.
        self.assertEqual(mentioned.sentiment_score, article_level.sentiment_score)
        self.assertGreater(mentioned.sentiment_score, Decimal('0'))
        self.assertEqual(mentioned.metadata, {'window_days': 7})
        self.assertGreater(market.sentiment_score, Decimal('0'))

    def test_article_level_and_asset_linked_rows_coexist(self):
        """Same key columns, different NULL pattern: both exist, neither duplicated."""
        calculate_daily_sentiment(target_date=self.day.isoformat())
        rows = SentimentScore.objects.filter(date=self.day, score_type='ARTICLE')
        self.assertEqual(rows.filter(asset__isnull=True).count(), 1)
        self.assertEqual(rows.filter(asset=self.mentioned).count(), 1)
