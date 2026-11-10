"""Drop the two sentiment columns from FactorScore.

``sentiment_weight`` was NOT NULL with no database-level default -- Django's ``default=0.0``
lives in the model, so every INSERT named the column. That is why this migration and the
model change deploy together with the Celery worker stopped: while old code runs, an INSERT
references a column this migration drops; with new code against the old schema, the column is
still NOT NULL and simply absent from the INSERT. Neither ordering is safe on a live worker,
and the gap is minutes long rather than a race you can win.

Both columns are restored as empty structure on reverse. The values are not coming back: they
were derived from a corpus this campaign deletes, and measured before deletion
``sentiment_score`` was non-null on all 2,043,000 rows with exactly one distinct value, so the
"history" being preserved was a constant.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('factors', '0006_add_pe_ttm_fields'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='factorscore',
            name='sentiment_score',
        ),
        migrations.RemoveField(
            model_name='factorscore',
            name='sentiment_weight',
        ),
    ]
