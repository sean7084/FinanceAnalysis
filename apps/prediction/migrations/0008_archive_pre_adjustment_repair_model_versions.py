from django.db import migrations

# Every model version trained before the 2026-11-05 OHLCV adjustment-basis repair. Their
# features came from `close` values sitting on two different qfq bases at once, so the
# accuracies they report are measured against prices that were wrong by 1-3% at every
# ex-dividend seam. Replaced by the five `adj-fix` / `ensemble-2026-11-03` rows.
#
# Listed explicitly rather than selected by a trained_at comparison so the migration does
# the same thing on every host it runs against, and so the record says exactly which
# generations were retired.
#
# `lgb-3d-2026-04-11` and `lgb-7d-2026-04-11` are deliberately absent: `0007` already
# archived them for implausible metrics, and `unarchive` below restores by setting READY,
# so including them would silently undo that migration.
CONTAMINATED = (
    'ensemble-2020-01-01',
    'ensemble-2024-12-31',
    'ensemble-2026-04-15',
    'ensemble-2026-04-18',
    'ensemble-2026-04-25',
    'ensemble-2026-05-02',
    'ensemble-2026-05-09',
    'ensemble-2026-05-16',
    'ensemble-2026-05-23',
    'ensemble-2026-09-21',
    'lgb-30d-2020-01-01',
    'lgb-30d-2024-12-31',
    'lgb-30d-2024-12-31-core80-v1',
    'lgb-30d-2024-12-31-regstrong-v1',
    'lgb-30d-2024-12-31-sec5b-v1',
    'lgb-30d-2024-12-31-stored-ti-v1',
    'lgb-30d-2024-12-31-stored-ti-v2',
    'lgb-30d-2026-04-15',
    'lgb-30d-2026-09-21',
    'lgb-3d-2020-01-01',
    'lgb-3d-2024-12-31',
    'lgb-3d-2024-12-31-core80-v1',
    'lgb-3d-2024-12-31-regstrong-v1',
    'lgb-3d-2024-12-31-sec5b-v1',
    'lgb-3d-2024-12-31-stored-ti-v1',
    'lgb-3d-2024-12-31-stored-ti-v2',
    'lgb-3d-2026-04-15',
    'lgb-3d-2026-09-21',
    'lgb-7d-2020-01-01',
    'lgb-7d-2024-12-31',
    'lgb-7d-2024-12-31-core80-v1',
    'lgb-7d-2024-12-31-regstrong-v1',
    'lgb-7d-2024-12-31-sec5b-v1',
    'lgb-7d-2024-12-31-stored-ti-v1',
    'lgb-7d-2024-12-31-stored-ti-v2',
    'lgb-7d-2026-04-15',
    'lgb-7d-2026-09-21',
    'lightgbm-2026-05-16',
    'lstm-2020-01-01',
    'lstm-2024-12-31',
    'lstm-2024-12-31-sec5b-v1',
    'lstm-2024-12-31-stored-ti-v1',
    'lstm-2024-12-31-stored-ti-v2',
    'lstm-2026-05-16',
    'lstm-2026-09-21',
)


def archive(apps, schema_editor):
    ModelVersion = apps.get_model('prediction', 'ModelVersion')
    # Bulk update, not instance save(): ModelVersion.save() runs validate_activation_metrics
    # whenever the row is active and not already archived, and that gate raises.
    ModelVersion.objects.filter(version__in=CONTAMINATED).update(
        status='ARCHIVED', is_active=False,
    )


def unarchive(apps, schema_editor):
    ModelVersion = apps.get_model('prediction', 'ModelVersion')
    ModelVersion.objects.filter(version__in=CONTAMINATED).update(status='READY')


class Migration(migrations.Migration):
    dependencies = [
        ('prediction', '0007_archive_implausible_model_versions'),
    ]

    operations = [
        migrations.RunPython(archive, unarchive),
    ]
