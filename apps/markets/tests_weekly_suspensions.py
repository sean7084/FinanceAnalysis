"""#140: the weekly suspension backfill must be bounded, idempotent and independently scheduled.

The daily dispatcher writes only TODAY for the CSI500 union and the catch-up path replays gap
windows from a watermark; neither guarantees an undetected window gets walked. This job re-walks
a trailing 90-day range on a fixed cadence. These tests pin the range arithmetic, that it does
not touch the dispatcher's watermark, and that the seeding migration is reversible.
"""

from datetime import timedelta
from unittest.mock import patch

from django.apps import apps as django_apps
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.markets import tasks as market_tasks


class WeeklyRangeTests(SimpleTestCase):
    def test_range_is_trailing_90_days_ending_today(self):
        captured = {}

        def fake_sync(start_date=None, end_date=None, ts_codes=None):
            captured['start'] = start_date
            captured['end'] = end_date
            captured['ts_codes'] = ts_codes
            return {'windows': 2}

        with patch.object(market_tasks, 'sync_asset_suspensions', side_effect=fake_sync):
            market_tasks.sync_asset_suspensions_weekly()
        today = timezone.localdate()
        self.assertEqual(captured['end'], today)
        self.assertEqual(captured['start'], today - timedelta(days=90))
        self.assertIsNone(captured['ts_codes'], 'must be whole-market, not a symbol subset')

    def test_it_does_not_touch_the_dispatcher_watermark(self):
        import inspect
        source = inspect.getsource(market_tasks.sync_asset_suspensions_weekly)
        self.assertNotIn('last_run_at', source)
        self.assertNotIn('DAILY_DISPATCHER_ENTRY', source)
        self.assertNotIn('PeriodicTask', source)


class SeedingMigrationTests(TestCase):
    def test_seed_creates_an_enabled_row_and_reverse_removes_it(self):
        from django_celery_beat.models import PeriodicTask

        import importlib
        module = importlib.import_module('apps.markets.migrations.0015_seed_weekly_suspension_backfill')
        module.seed(django_apps, None)
        row = PeriodicTask.objects.get(name='sync-asset-suspensions-weekly')
        self.assertTrue(row.enabled)
        self.assertEqual(row.task, 'apps.markets.tasks.sync_asset_suspensions_weekly')
        self.assertEqual(row.crontab.hour, '3')
        self.assertEqual(row.crontab.day_of_week, '0')
        self.assertEqual(str(row.crontab.timezone), 'Asia/Shanghai')

        module.unseed(django_apps, None)
        self.assertFalse(PeriodicTask.objects.filter(name='sync-asset-suspensions-weekly').exists())

    def test_seed_is_idempotent(self):
        from django_celery_beat.models import PeriodicTask
        import importlib
        module = importlib.import_module('apps.markets.migrations.0015_seed_weekly_suspension_backfill')
        module.seed(django_apps, None)
        module.seed(django_apps, None)
        self.assertEqual(
            PeriodicTask.objects.filter(name='sync-asset-suspensions-weekly').count(), 1
        )
        module.unseed(django_apps, None)
