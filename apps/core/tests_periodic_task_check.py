"""core.E002: an enabled PeriodicTask row must name a task a worker can import.

This test exists because a settings-side change cannot be verified against the database that
actually drives beat. #110 removed three entries from ``CELERY_BEAT_SCHEDULE``; the rows
beat reads stayed enabled and pointed at ``apps.sentiment.tasks``, which #117 then deleted.
Every settings-based test in ``tests_beat_schedule.py`` stayed green, because CI's beat
table is created empty. The bug was only reachable through a live database, and it would
have surfaced as a worker rejecting an unregistered task on every tick.

The check therefore compares the two things that must agree: the rows ``DatabaseScheduler``
loads and the registry the worker loads. These tests put a row on each side of that line.
"""

from django.apps import apps as django_apps
from django.test import TestCase
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from apps.core.checks import PERIODIC_TASK_CHECK_ID, check_enabled_periodic_tasks_are_dispatchable

DEAD_TASK = 'apps.sentiment.tasks.fetch_latest_market_news'


class EnabledPeriodicTaskCheckTests(TestCase):
    def setUp(self):
        self.crontab = CrontabSchedule.objects.create(minute='0', hour='*')

    def _row(self, name, task, enabled=True):
        return PeriodicTask.objects.create(
            name=name, task=task, crontab=self.crontab, enabled=enabled,
        )

    def _ids(self):
        return [issue.id for issue in check_enabled_periodic_tasks_are_dispatchable(None)]

    def test_a_row_naming_a_module_that_no_longer_exists_is_an_error(self):
        """The exact shape #110 left behind: enabled row, deleted module.

        Asserted rather than assumed: ``apps.sentiment.tasks`` must be gone for this test to
        mean what it says, so the deletion in #117 is checked here too.
        """
        import importlib.util

        self.assertIsNone(
            importlib.util.find_spec('apps.sentiment.tasks'),
            'apps.sentiment.tasks came back; this test is asserting a deleted module',
        )
        self._row('fetch-latest-market-news-daily', DEAD_TASK)
        self.assertEqual(self._ids(), [PERIODIC_TASK_CHECK_ID])

    def test_a_row_mirroring_a_live_settings_entry_passes(self):
        """A row whose task really is registered must not be reported.

        The task name is taken from ``CELERY_BEAT_SCHEDULE`` rather than hardcoded, so this
        keeps working as tasks are renamed -- and it fails if the check ever stops importing
        the task modules it depends on, because then a live name would look unregistered.
        """
        live_name, entry = next(iter(sorted(self._settings_schedule().items())), (None, None))
        if entry is None:
            self.skipTest('no CELERY_BEAT_SCHEDULE entry points at an installed app tasks module')
        self._row(f'test-{live_name}', entry['task'])
        self.assertEqual(self._ids(), [])

    def test_a_disabled_row_is_not_reported(self):
        """Beat builds its schedule from ``objects.enabled()``, so a disabled row is inert."""
        self._row('disabled-orphan', DEAD_TASK, enabled=False)
        self.assertEqual(self._ids(), [])

    def test_an_empty_beat_table_reports_nothing(self):
        """Zero rows means zero findings -- and must not be reported as a failure.

        This is also the CI path: nothing in CI creates beat rows, so every unrelated run of
        ``manage.py check`` returns before touching celery. The table is emptied rather than
        assumed empty, because the claim under test is about the zero-row branch, not about
        what the runner happened to leave behind.
        """
        PeriodicTask.objects.all().delete()
        self.assertEqual(self._ids(), [])

    @staticmethod
    def _settings_schedule():
        from django.conf import settings

        schedule = dict(settings.CELERY_BEAT_SCHEDULE)
        installed = {c.name for c in django_apps.get_app_configs()}
        return {
            name: entry for name, entry in schedule.items()
            if entry['task'].rpartition('.')[0] in {f'{a}.tasks' for a in installed}
        }
