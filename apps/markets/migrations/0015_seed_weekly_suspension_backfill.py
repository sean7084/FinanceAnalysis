"""Seed the weekly suspension range backfill (#140).

DatabaseScheduler dispatches from PeriodicTask rows, so a settings entry alone does not
schedule anything. Reverse deletes the row explicitly: update_from_dict only adds/updates.
"""

from django.db import migrations
from django_celery_beat.models import PeriodicTasks

NAME = 'sync-asset-suspensions-weekly'
TASK = 'apps.markets.tasks.sync_asset_suspensions_weekly'


def seed(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    # Sunday 03:00 Asia/Shanghai, matching CELERY_TIMEZONE and the settings entry.
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute='0', hour='3', day_of_week='0', day_of_month='*', month_of_year='*',
        timezone='Asia/Shanghai',
    )
    PeriodicTask.objects.update_or_create(
        name=NAME,
        defaults={'task': TASK, 'crontab': schedule, 'enabled': True, 'one_off': False},
    )
    PeriodicTasks.update_changed()


def unseed(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
    PeriodicTask.objects.filter(name=NAME).delete()
    PeriodicTasks.update_changed()


class Migration(migrations.Migration):

    dependencies = [
        ('markets', '0014_ohlcv_adj_factor_ohlcv_raw_close_and_more'),
        ('django_celery_beat', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
