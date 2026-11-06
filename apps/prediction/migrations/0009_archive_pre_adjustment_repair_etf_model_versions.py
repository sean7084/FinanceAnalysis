from django.db import migrations

# ETF rotation model versions trained before the 2026-11-05 OHLCV adjustment-basis repair.
# `apps/prediction/features.py` builds the constituent-level ETF features from the members'
# `adj_close`, so every ETFFeatureSnapshot row inherited the same mixed-basis prices the stock
# cascade had to be rebuilt for -- 2,649,487 snapshots recomputed on 2026-11-06 to clear it.
#
# Listed explicitly rather than selected on trained_at so the migration behaves identically on
# every host, and so the record names the generations retired. The three `adj-fix` rows that
# replace these are absent because they are active: selecting on `is_active=False` is the guard
# that kept 0008 from archiving the ensemble built minutes earlier.
CONTAMINATED = (
    'etf-ensemble-2026-10-20',
    'etf-ensemble-2026-10-20-oos23',
    'etf-lgb-20d-lambdarank-2023-11-30-oos23',
    'etf-lgb-20d-lambdarank-2026-09-14',
    'etf-lgb-20d-lambdarank-2026-09-17-post-release',
    'etf-lstm-2023-11-30-oos23',
    'etf-lstm-2023-12-29-oos-lstm-v2',
    'etf-lstm-2023-12-29-t-15bc3f78',
    'etf-lstm-2023-12-29-t-1becb8db',
    'etf-lstm-2023-12-29-t-27c2548a',
    'etf-lstm-2023-12-29-t-2fd85c66',
    'etf-lstm-2023-12-29-t-3764f738',
    'etf-lstm-2023-12-29-t-3b39a23e',
    'etf-lstm-2023-12-29-t-3ca96e66',
    'etf-lstm-2023-12-29-t-3cd95031',
    'etf-lstm-2023-12-29-t-3ddb961e',
    'etf-lstm-2023-12-29-t-43a584d6',
    'etf-lstm-2023-12-29-t-465b242a',
    'etf-lstm-2023-12-29-t-48a3a44f',
    'etf-lstm-2023-12-29-t-4da7ba13',
    'etf-lstm-2023-12-29-t-50cbd9f4',
    'etf-lstm-2023-12-29-t-526e79aa',
    'etf-lstm-2023-12-29-t-625c11fd',
    'etf-lstm-2023-12-29-t-6ef67633',
    'etf-lstm-2023-12-29-t-84dc6607',
    'etf-lstm-2023-12-29-t-894273e9',
    'etf-lstm-2023-12-29-t-95ab0141',
    'etf-lstm-2023-12-29-t-9a8eee8c',
    'etf-lstm-2023-12-29-t-9ac4dfba',
    'etf-lstm-2023-12-29-t-9d712399',
    'etf-lstm-2023-12-29-t-9fa1140b',
    'etf-lstm-2023-12-29-t-ac65802d',
    'etf-lstm-2023-12-29-t-c6514865',
    'etf-lstm-2023-12-29-t-ca3d925e',
    'etf-lstm-2023-12-29-t-cb1ae1d7',
    'etf-lstm-2023-12-29-t-cf1276f1',
    'etf-lstm-2023-12-29-t-d0086700',
    'etf-lstm-2023-12-29-t-d4c0f43f',
    'etf-lstm-2023-12-29-t-d9d06d3d',
    'etf-lstm-2023-12-29-t-ea816a41',
    'etf-lstm-2023-12-29-t-fd4deb21',
    'etf-lstm-2026-09-14',
    'etf-lstm-2026-09-17-post-release',
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
        ('prediction', '0008_archive_pre_adjustment_repair_model_versions'),
    ]

    operations = [
        migrations.RunPython(archive, unarchive),
    ]
