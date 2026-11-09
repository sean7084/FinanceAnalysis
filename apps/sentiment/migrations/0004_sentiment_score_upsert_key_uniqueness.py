"""Collapse the duplicate SentimentScore rows the NULL-distinct unique_together allowed.

Every writer in tasks.py upserts on ``(article, asset, date, score_type)``. Two of those
columns are nullable, Postgres treats NULLs as distinct, and so the old constraint never
applied to the rows written most often. Measured on production 2026-11-09 before this
migration: 2,047 key groups holding more than one row (1,582 ASSET_7D with NULL article,
464 article-level rows with NULL asset, 1 MARKET_7D with both NULL), 2,065 excess rows, and
zero groups whose copies disagreed on ``sentiment_score`` -- which is why collapsing them
loses nothing, and why ``collapse`` asserts exactly that before deleting.

The dedupe runs first: adding NULLS-NOT-DISTINCT on top of live duplicates would fail.
Statements live in ``_upsert_key_logic.py`` where the NULL semantics can be tested; the
first version of this migration deleted nothing at all and only said so because it
re-counted afterwards.
"""

from django.db import migrations, models

from apps.sentiment.migrations._upsert_key_logic import collapse


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ('markets', '0013_purge_csi300_csia500_universe'),
        ('sentiment', '0003_shift_legacy_published_at_to_beijing'),
    ]

    @staticmethod
    def collapse_duplicates(apps, schema_editor):
        if schema_editor.connection.vendor != 'postgresql':
            # SQLite/CI builds arrive here from migrations, so there are no duplicates to
            # collapse; the constraint is what matters there.
            return
        removed = collapse(schema_editor, table='sentiment_sentimentscore')
        if removed:
            print(f'sentiment: collapsed {removed} duplicate SentimentScore rows')

    @staticmethod
    def restore_nothing(apps, schema_editor):
        """Re-seeding deleted rows is not something this migration can do, and the reverse
        is never executed in practice: the constraint stays."""

    operations = [
        migrations.RunPython(collapse_duplicates, restore_nothing),
        migrations.AlterUniqueTogether(name='sentimentscore', unique_together=set()),
        migrations.AddConstraint(
            model_name='sentimentscore',
            constraint=models.UniqueConstraint(
                fields=('article', 'asset', 'date', 'score_type'),
                name='sentiment_score_upsert_key_uniq',
                nulls_distinct=False,
            ),
        ),
    ]
