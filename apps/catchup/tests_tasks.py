"""Tests for the Celery entry point."""

from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.catchup.models import CatchUpRun
from apps.catchup.tasks import catch_up_missed_runs

LOC_MEM_CACHE = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'catchup-task-tests',
    },
}


@override_settings(CACHES=LOC_MEM_CACHE)
class CatchUpTaskTests(TestCase):
    def test_task_passes_beat_trigger(self):
        with patch('apps.catchup.tasks.run_catch_up', return_value=[]) as mocked:
            catch_up_missed_runs.run(max_calls=10, max_seconds=20)
        mocked.assert_called_once_with(max_calls=10, max_seconds=20,
                                       trigger=CatchUpRun.Trigger.BEAT)

    def test_task_returns_summary_string(self):
        row = CatchUpRun(series='etf_daily', triggered_by='beat', outcome='OK')
        with patch('apps.catchup.tasks.run_catch_up', return_value=[row]):
            self.assertEqual(catch_up_missed_runs.run(), 'etf_daily=OK')

    def test_task_reports_a_held_lock(self):
        with patch('apps.catchup.tasks.run_catch_up', return_value=[]):
            self.assertIn('in flight', catch_up_missed_runs.run())

    def test_task_overrides_the_global_time_limits(self):
        # The global pair is soft 60 / hard 300, which cannot cover a pass that
        # walks seventeen series in dependency order.
        self.assertEqual(catch_up_missed_runs.soft_time_limit, 1200)
        self.assertEqual(catch_up_missed_runs.time_limit, 1500)
