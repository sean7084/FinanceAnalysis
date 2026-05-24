from django.db import migrations
from django.db.models import Q
from django_celery_beat.models import PeriodicTasks


RETRAIN_TASK_ROWS = [
    {
        'name': 'train-prediction-models-weekly',
        'task': 'apps.prediction.tasks.train_prediction_models',
        'minute': '0',
        'hour': '4',
        'day_of_week': 'sat',
    },
    {
        'name': 'train-lightgbm-models-weekly',
        'task': 'apps.prediction.tasks_lightgbm.train_lightgbm_models',
        'minute': '0',
        'hour': '5',
        'day_of_week': 'sun',
    },
]


def remove_scheduled_retraining_rows(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    names = [row['name'] for row in RETRAIN_TASK_ROWS]
    tasks = [row['task'] for row in RETRAIN_TASK_ROWS]
    PeriodicTask.objects.filter(Q(name__in=names) | Q(task__in=tasks)).delete()
    PeriodicTasks.update_changed()


def restore_scheduled_retraining_rows(apps, schema_editor):
    CrontabSchedule = apps.get_model('django_celery_beat', 'CrontabSchedule')
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    for row in RETRAIN_TASK_ROWS:
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=row['minute'],
            hour=row['hour'],
            day_of_week=row['day_of_week'],
            day_of_month='*',
            month_of_year='*',
            timezone='UTC',
        )
        PeriodicTask.objects.update_or_create(
            name=row['name'],
            defaults={
                'task': row['task'],
                'crontab': schedule,
                'enabled': True,
                'one_off': False,
                'args': '[]',
                'kwargs': '{}',
            },
        )

    PeriodicTasks.update_changed()


class Migration(migrations.Migration):

    dependencies = [
        ('django_celery_beat', '0019_alter_periodictasks_options'),
        ('markets', '0010_exchangetradingcalendar_is_open'),
    ]

    operations = [
        migrations.RunPython(
            remove_scheduled_retraining_rows,
            restore_scheduled_retraining_rows,
        ),
    ]