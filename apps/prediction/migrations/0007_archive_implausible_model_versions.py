from django.db import migrations

IMPLAUSIBLE = ('lgb-3d-2026-04-11', 'lgb-7d-2026-04-11')


def archive(apps, schema_editor):
    ModelVersion = apps.get_model('prediction', 'ModelVersion')
    ModelVersion.objects.filter(version__in=IMPLAUSIBLE).update(
        status='ARCHIVED', is_active=False,
    )


def unarchive(apps, schema_editor):
    ModelVersion = apps.get_model('prediction', 'ModelVersion')
    ModelVersion.objects.filter(version__in=IMPLAUSIBLE).update(status='READY')


class Migration(migrations.Migration):
    dependencies = [
        ('prediction', '0006_alter_modelversion_model_type'),
    ]

    operations = [
        migrations.RunPython(archive, unarchive),
    ]
