"""Prune the three beat rows #110 left behind when its settings entries went away.

``CELERY_BEAT_SCHEDULE`` is only the seed. ``DatabaseScheduler`` builds the live schedule from
``PeriodicTask.objects.enabled()`` and syncs settings into it through ``update_from_dict``,
which adds and updates but never deletes. Removing an entry from settings therefore stops
nothing: the row stays enabled and keeps dispatching. #110 removed
``fetch-latest-market-news-daily``, ``run-hourly-historical-news-backfill`` and
``run-daily-sentiment-pipeline`` from settings, and #117 deleted the module they named, so
each surviving row became a tick that the worker answers with an unregistered-task error --
the same 24-failures-a-day noise #52 was opened about, reinstalled through a different door.

Nothing in CI could see this: the beat table in a test database is not populated from a
running schedule, and every settings-side assertion in ``apps/core/tests_beat_schedule.py``
passed. The repo already knows the correct instrument -- ``markets.0007`` and ``markets.0011``
prune obsolete rows exactly this way -- so this is that pattern applied to the rows this
decommission orphaned.

The reverse is a deliberate no-op, unlike those two precedents. Restoring a row that names a
module deleted in the same campaign would reinstall the bug the forward half fixes, and there
is no crontab to get right: the schedule for these tasks lives in settings, where bringing
the news module back would put it.
"""
from django.db import migrations
from django.db.models import Q
from django_celery_beat.models import PeriodicTasks

NEWS_BEAT_ROWS = [
    {'name': 'fetch-latest-market-news-daily',
     'task': 'apps.sentiment.tasks.fetch_latest_market_news'},
    {'name': 'run-hourly-historical-news-backfill',
     'task': 'apps.sentiment.tasks.run_hourly_historical_news_backfill'},
    {'name': 'run-daily-sentiment-pipeline',
     'task': 'apps.sentiment.tasks.run_daily_sentiment_pipeline'},
]


def prune_news_beat_rows(apps, schema_editor):
    PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

    names = [row['name'] for row in NEWS_BEAT_ROWS]
    tasks = [row['task'] for row in NEWS_BEAT_ROWS]
    # Matched on either key: a row is identified by what it dispatches, and a hand-edited
    # name should not be the reason a dead schedule survives.
    PeriodicTask.objects.filter(Q(name__in=names) | Q(task__in=tasks)).delete()
    PeriodicTasks.update_changed()


def noop_reverse(apps, schema_editor):
    """See the module docstring: re-creating these rows would re-create the defect."""


class Migration(migrations.Migration):

    dependencies = [
        ('django_celery_beat', '0019_alter_periodictasks_options'),
        ('sentiment', '0005_delete_sentiment_tables'),
    ]

    operations = [
        migrations.RunPython(prune_news_beat_rows, noop_reverse),
    ]
