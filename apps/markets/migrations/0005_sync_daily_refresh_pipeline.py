from django.db import migrations
from django_celery_beat.models import PeriodicTasks


def enable_post_sync_pipeline(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    sync_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute='10',
        hour='16',
        day_of_week='*',
        day_of_month='*',
        month_of_year='*',
        timezone='UTC',
    )
    PeriodicTask.objects.update_or_create(
        name='sync-a-shares-daily-from-tushare',
        defaults={
            'task': 'apps.markets.tasks.sync_daily_a_shares',
            'crontab': sync_schedule,
            'enabled': True,
            'one_off': False,
        },
    )
    PeriodicTask.objects.filter(
        task__in=[
            'apps.analytics.tasks.calculate_signals_for_all_assets',
            'apps.factors.tasks.sync_daily_capital_flow_snapshots',
        ]
    ).update(enabled=False)
    PeriodicTasks.update_changed()


def restore_standalone_daily_tasks(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    signal_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute='0',
        hour='16',
        day_of_week='*',
        day_of_month='*',
        month_of_year='*',
        timezone='UTC',
    )
    capital_flow_schedule, _ = CrontabSchedule.objects.get_or_create(
        minute='20',
        hour='16',
        day_of_week='*',
        day_of_month='*',
        month_of_year='*',
        timezone='UTC',
    )

    PeriodicTask.objects.update_or_create(
        name='calculate-signals-daily',
        defaults={
            'task': 'apps.analytics.tasks.calculate_signals_for_all_assets',
            'crontab': signal_schedule,
            'enabled': True,
            'one_off': False,
        },
    )
    PeriodicTask.objects.update_or_create(
        name='sync-capital-flow-daily',
        defaults={
            'task': 'apps.factors.tasks.sync_daily_capital_flow_snapshots',
            'crontab': capital_flow_schedule,
            'enabled': True,
            'one_off': False,
        },
    )
    PeriodicTasks.update_changed()


class Migration(migrations.Migration):

    dependencies = [
        ('django_celery_beat', '0019_alter_periodictasks_options'),
        ('markets', '0004_rename_markets_ind_index_c_47f1e6_idx_markets_ind_index_c_eaead1_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(enable_post_sync_pipeline, restore_standalone_daily_tasks),
    ]