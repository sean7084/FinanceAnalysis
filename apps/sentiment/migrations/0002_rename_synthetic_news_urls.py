from django.db import migrations
from django.db.models.functions import Replace
from django.db.models import Value

OLD = 'https://synthetic.financeanalysis.local/'
NEW = 'https://synthetic.armillary.local/'


def rewrite(apps, schema_editor, src, dst):
    NewsArticle = apps.get_model('sentiment', 'NewsArticle')
    # `url` is unique and is the dedup identity, so renaming the prefix without
    # renaming the rows would let every linkless article re-ingest under a new
    # URL. Replace() touches all occurrences, but the prefix can only ever appear
    # at position 0: the rest is a provider name plus a hex digest.
    NewsArticle.objects.filter(url__startswith=src).update(
        url=Replace('url', Value(src), Value(dst)),
    )


def forwards(apps, schema_editor):
    rewrite(apps, schema_editor, OLD, NEW)


def backwards(apps, schema_editor):
    rewrite(apps, schema_editor, NEW, OLD)


class Migration(migrations.Migration):
    dependencies = [
        ('sentiment', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
