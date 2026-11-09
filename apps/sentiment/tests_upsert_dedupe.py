"""The SentimentScore dedupe must actually collapse NULL-keyed groups.

The first version of this repair deleted nothing: its `IN` subquery compared
`(score_type, article_id, asset_id, date)` tuples, and SQL `IN` uses plain equality, so a
group whose `article_id` is NULL never matched its own key. The migration raised only
because it re-counted afterwards -- which is the behaviour worth keeping: it is the reason
a no-op dedupe cannot quietly "succeed" and leave production with 2,047 duplicate groups
and a constraint that then fails to install.

Tested against a constraint-free temp table, because the real table cannot hold the
duplicates any more -- that is the fix.
"""

from datetime import date, datetime, time
from decimal import Decimal

from django.db import connection, transaction
from django.test import TestCase
from django.utils import timezone

from apps.sentiment.migrations._upsert_key_logic import (
    COLLAPSE,
    COUNT_DISAGREEING_GROUPS,
    COUNT_DUPLICATE_GROUPS,
    collapse,
)
from apps.sentiment.models import SentimentScore

TEMP = 'tmp_sentiment_duplicates'
DAY = date(2026, 10, 21)


class _Editor:
    """The migration passes a schema editor; only .connection is used."""

    @property
    def connection(self):
        return connection


class CollapseLogicTests(TestCase):
    def setUp(self):
        with connection.cursor() as cursor:
            cursor.execute(
                f'DROP TABLE IF EXISTS {TEMP}'
            )
            cursor.execute(
                f"""
                CREATE TABLE {TEMP} (
                    id serial PRIMARY KEY,
                    article_id integer,
                    asset_id integer,
                    date date NOT NULL,
                    score_type varchar(20) NOT NULL,
                    sentiment_score numeric(8, 6) NOT NULL DEFAULT 0,
                    created_at timestamp NOT NULL DEFAULT now()
                )
                """
            )
        self.addCleanup(self._drop)

    def _drop(self):
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS {TEMP}')

    def _insert(self, article_id, asset_id, score='0.000000', created_at=None):
        moment = created_at or datetime(2026, 10, 21, 12, 0, 0)
        with connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {TEMP} (article_id, asset_id, date, score_type, sentiment_score, created_at)'
                f' VALUES (%s, %s, %s, %s, %s, %s) RETURNING id',
                [article_id, asset_id, DAY, 'ASSET_7D', score, moment],
            )
            return cursor.fetchone()[0]

    def test_it_collapses_groups_whose_key_column_is_null(self):
        """The exact shape production had: 1,582 ASSET_7D groups with NULL article."""
        kept = self._insert(None, 7, created_at=datetime(2026, 10, 21, 15, 0, 0))
        dropped = [self._insert(None, 7) for _ in range(2)]
        other = self._insert(None, 8)

        removed = collapse(_Editor(), table=TEMP)

        self.assertEqual(removed, 2)
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT id FROM {TEMP} ORDER BY id')
            surviving = [row[0] for row in cursor.fetchall()]
        self.assertEqual(surviving, sorted([kept, other]))
        self.assertNotIn(dropped[0], surviving)

    def test_it_collapses_groups_where_both_key_columns_are_null(self):
        self._insert(None, None)
        self._insert(None, None)

        self.assertEqual(collapse(_Editor(), table=TEMP), 1)

    def test_it_refuses_when_two_copies_disagree_on_the_score(self):
        self._insert(None, 7, score='0.500000')
        self._insert(None, 7, score='0.200000')

        with self.assertRaises(RuntimeError), transaction.atomic():
            collapse(_Editor(), table=TEMP)

        # Nothing is destroyed by a refusal: the guard has to fire before the delete.
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT count(*) FROM {TEMP}')
            self.assertEqual(cursor.fetchone()[0], 2)

    def test_a_table_without_duplicates_is_a_no_op(self):
        self._insert(None, 7)
        self.assertEqual(collapse(_Editor(), table=TEMP), 0)

    def test_the_real_table_can_no_longer_hold_a_duplicate_key(self):
        """Positive control: the constraint this migration installed is what is being
        defended, so the duplicate pattern must be impossible in production now."""
        from django.db import IntegrityError

        row = SentimentScore(
            article_id=None, asset_id=None, date=DAY, score_type='MARKET_7D',
            positive_score=Decimal('0'), neutral_score=Decimal('1'), negative_score=Decimal('0'),
            sentiment_score=Decimal('0'), sentiment_label='NEUTRAL', metadata={},
            created_at=timezone.now(),
        )
        row.save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SentimentScore.objects.create(
                article_id=None, asset_id=None, date=DAY, score_type='MARKET_7D',
                positive_score=Decimal('0'), neutral_score=Decimal('1'), negative_score=Decimal('0'),
                sentiment_score=Decimal('0'), sentiment_label='NEUTRAL', metadata={},
            )

    def test_sql_module_strings_target_the_named_table(self):
        """Guards against the replace() in collapse() silently missing a template."""
        self.assertIn('sentiment_sentimentscore', COUNT_DUPLICATE_GROUPS)
        self.assertIn('sentiment_sentimentscore', COUNT_DISAGREEING_GROUPS)
        self.assertIn('{table}', COLLAPSE)
