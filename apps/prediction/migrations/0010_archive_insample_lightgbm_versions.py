"""Archive the stock LIGHTGBM rows whose accuracy was measured in-sample.

Before the purged holdout, ``train_lightgbm_models`` fitted on the whole aligned window
and then scored ``mean(pred == y_train)``. Every stock LIGHTGBM ``ModelVersion`` written
that way carries ``accuracy`` and no ``test_accuracy`` -- the holdout path always writes
``test_accuracy``. Their numbers are not comparable to anything: not to each other across
windows, and not to the ETF arms' ``rank_ic`` series.

Archiving rather than deleting keeps the rows inspectable while stopping them being
selected. ``ModelVersion.save`` refuses ``is_active=True`` on an ARCHIVED row, and the
pinned loaders refuse archived versions, so the update below is deliberate: bulk update,
which bypasses ``save``, with ``is_active`` cleared in the same statement.
"""
from django.db import migrations


def archive_insample(apps, schema_editor):
    ModelVersion = apps.get_model('prediction', 'ModelVersion')
    rows = ModelVersion.objects.filter(model_type='LIGHTGBM').exclude(
        status='ARCHIVED',
    )
    stale = [row.pk for row in rows if 'test_accuracy' not in (row.metrics or {})]
    ModelVersion.objects.filter(pk__in=stale).update(status='ARCHIVED', is_active=False)


def unarchive(apps, schema_editor):
    """No-op: un-archiving cannot know which rows this migration touched."""
    return


class Migration(migrations.Migration):
    dependencies = [
        ('prediction', '0009_archive_pre_adjustment_repair_etf_model_versions'),
    ]

    operations = [
        migrations.RunPython(archive_insample, unarchive),
    ]