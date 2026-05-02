from django.db import migrations
from django_celery_beat.models import PeriodicTasks


OBSOLETE_DAILY_ROWS = [
    {
        'name': 'calculate-signals-daily',
        'task': 'apps.analytics.tasks.calculate_signals_for_all_assets',
        'minute': '0',
        'hour': '16',
    },
    {
        'name': 'sync-capital-flow-daily',
        'task': 'apps.factors.tasks.sync_daily_capital_flow_snapshots',
        'minute': '20',
        'hour': '16',
    },
]


def remove_obsolete_daily_rows(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    PeriodicTask.objects.filter(
        name__in=[row['name'] for row in OBSOLETE_DAILY_ROWS]
    ).delete()
    PeriodicTasks.update_changed()


def restore_obsolete_daily_rows(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    for row in OBSOLETE_DAILY_ROWS:
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=row['minute'],
            hour=row['hour'],
            day_of_week='*',
            day_of_month='*',
            month_of_year='*',
            timezone='UTC',
        )
        PeriodicTask.objects.update_or_create(
            name=row['name'],
            defaults={
                'task': row['task'],
                'crontab': schedule,
                'enabled': False,
                'one_off': False,
            },
        )

    PeriodicTasks.update_changed()


class Migration(migrations.Migration):

    dependencies = [
        ('markets', '0006_benchmarkindexdaily'),
    ]

    operations = [
        migrations.RunPython(remove_obsolete_daily_rows, restore_obsolete_daily_rows),
    ]