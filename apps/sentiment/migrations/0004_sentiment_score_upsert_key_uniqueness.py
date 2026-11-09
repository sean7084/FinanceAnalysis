"""Collapse the duplicate SentimentScore rows the NULL-distinct unique_together allowed.

Every writer in tasks.py upserts on ``(article, asset, date, score_type)``. Two of those
columns are nullable, Postgres treats NULLs as distinct, and so the old constraint never
applied to the rows written most often. Measured on production 2026-11-09 before this
migration: 2,047 key groups holding more than one row (1,582 ASSET_7D with NULL article,
464 article-level rows with NULL asset, 1 MARKET_7D with both NULL), 2,065 excess rows, and
zero groups whose copies disagreed on ``sentiment_score`` -- which is why collapsing them
loses nothing, and why this migration asserts exactly that before deleting.

The dedupe runs first: adding NULLS-NOT-DISTINCT on top of live duplicates would fail.
"""

from django.db import migrations, transaction
from django.db.models import UniqueConstraint

COLLAPSE = """
WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY score_type, article_id, asset_id, date
               ORDER BY created_at DESC, id DESC
           ) AS position
      FROM sentiment_sentimentscore
     WHERE (score_type, article_id, asset_id, date) IN (
               SELECT score_type, article_id, asset_id, date
                 FROM sentiment_sentimentscore
                GROUP BY 1, 2, 3, 4
               HAVING count(*) > 1
           )
)
DELETE FROM sentiment_sentimentscore
 WHERE id IN (SELECT id FROM ranked WHERE position > 1)
"""

COUNT_GROUPS = """
SELECT count(*)
  FROM (
    SELECT score_type, article_id, asset_id, date, count(DISTINCT sentiment_score) AS variants
      FROM sentiment_sentimentscore
     GROUP BY 1, 2, 3, 4
    HAVING count(*) > 1
) t
 WHERE variants > 1
"""

COUNT_ANY = """
SELECT count(*)
  FROM (SELECT 1 FROM sentiment_sentimentscore
         GROUP BY score_type, article_id, asset_id, date
        HAVING count(*) > 1) t
"""


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ('markets', '0013_purge_csi300_csia500_universe'),
        ('sentiment', '0003_shift_legacy_published_at_to_beijing'),
    ]

    @staticmethod
    def collapse_duplicates(apps, schema_editor):
        score_model = apps.get_model('sentiment', 'SentimentScore')
        db = schema_editor.connection
        if not db.vendor == 'postgresql':
            # SQLite/CI builds start from migrations, not from production data; the
            # constraint alone is what matters there.
            return

        with db.cursor() as cursor:
            cursor.execute(COUNT_ANY)
            groups = cursor.fetchone()[0]
            if not groups:
                return

            # Keep-the-newest is only safe while no group holds two different scores. If
            # any did, silently deleting one would change published sentiment, so the
            # migration refuses and a human picks which value wins.
            cursor.execute(COUNT_GROUPS)
            disagreeing = cursor.fetchone()[0]
            if disagreeing:
                raise RuntimeError(
                    f'{disagreeing} duplicate SentimentScore key groups hold differing '
                    f'sentiment_score values; collapsing them would change published data. '
                    f'Resolve by hand, then re-run.'
                )

            with transaction.atomic(using=db.alias):
                cursor.execute(COLLAPSE)
                removed = cursor.rowcount
                cursor.execute(COUNT_ANY)
                remaining = cursor.fetchone()[0]
            if remaining:
                raise RuntimeError(f'{remaining} duplicate key groups survived the collapse')
            print(f'sentiment: collapsed {removed} duplicate SentimentScore rows in {groups} key groups')

    @staticmethod
    def restore_nothing(apps, schema_editor):
        # Re-seeding deleted rows is not a thing this migration can do, and the reverse is
        # never executed in practice: the constraint stays.
        pass

    operations = [
        migrations.RunPython(collapse_duplicates, restore_nothing),
        migrations.AlterUniqueTogether(
            name='sentimentscore',
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name='sentimentscore',
            constraint=UniqueConstraint(
                fields=('article', 'asset', 'date', 'score_type'),
                name='sentiment_score_upsert_key_uniq',
                nulls_distinct=False,
            ),
        ),
    ]
