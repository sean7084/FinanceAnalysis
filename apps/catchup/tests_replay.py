"""Tests for the scheduler-watermark helper and the registry's beat-entry names."""

from datetime import date, timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from apps.catchup import registry
from apps.catchup.detectors import CatchUpContext, SeriesGap
from apps.catchup.replay import classify_cause, missed_window
from apps.catchup.repairs import delegate_predictions
from apps.catchup.tests_detectors import seed_periodic_task, watermark_at

AS_OF = date(2026, 10, 30)
OPEN_DAYS = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28)]

make_task = seed_periodic_task


class MissedWindowTests(TestCase):
    ENTRY = 'run-daily-sentiment-pipeline'

    def test_missed_window_when_last_run_at_is_stale(self):
        make_task(self.ENTRY, watermark_at(AS_OF, 3))
        window = missed_window(self.ENTRY, AS_OF, grace_days=1)
        self.assertIsNotNone(window)
        start, end = window
        self.assertEqual(start.date(), AS_OF - timedelta(days=3))
        self.assertEqual(end.date(), AS_OF)

    def test_no_window_when_watermark_fresh(self):
        make_task(self.ENTRY, watermark_at(AS_OF, 0))
        self.assertIsNone(missed_window(self.ENTRY, AS_OF, grace_days=1))

    def test_no_window_inside_grace(self):
        make_task(self.ENTRY, watermark_at(AS_OF, 1))
        self.assertIsNone(missed_window(self.ENTRY, AS_OF, grace_days=1))

    def test_no_window_for_unknown_entry(self):
        self.assertIsNone(missed_window('not-a-real-entry', AS_OF, grace_days=1))

    def test_no_window_for_disabled_entry(self):
        make_task(self.ENTRY, watermark_at(AS_OF, 5), enabled=False)
        self.assertIsNone(missed_window(self.ENTRY, AS_OF, grace_days=1))

    def test_no_window_for_empty_task_name(self):
        self.assertIsNone(missed_window('', AS_OF, grace_days=1))
        self.assertIsNone(missed_window(None, AS_OF, grace_days=1))


class ClassifyCauseTests(TestCase):
    ENTRY = 'sync-a-shares-daily-from-tushare'

    def test_beat_outage_when_the_watermark_is_stale(self):
        make_task(self.ENTRY, watermark_at(AS_OF, 4))
        gap = SeriesGap('ohlcv', 'dates', missing_dates=(AS_OF,))
        self.assertEqual(classify_cause(self.ENTRY, AS_OF, 1, gap), 'beat_outage')

    def test_provider_gap_when_beat_kept_up(self):
        make_task(self.ENTRY, watermark_at(AS_OF, 0))
        gap = SeriesGap('ohlcv', 'dates', missing_dates=(AS_OF,))
        self.assertEqual(classify_cause(self.ENTRY, AS_OF, 1, gap), 'provider_gap')

    def test_unknown_when_there_is_no_gap(self):
        make_task(self.ENTRY, watermark_at(AS_OF, 0))
        self.assertEqual(
            classify_cause(self.ENTRY, AS_OF, 1, SeriesGap('ohlcv', 'dates')), 'unknown')


class RegistryBeatNameTests(TestCase):
    def test_registry_beat_task_names_match_schedule(self):
        # A typo here fails silently: missed_window returns None for an entry that
        # does not exist, so the series would simply never be caught up.
        known = set(settings.CELERY_BEAT_SCHEDULE)
        referenced = {d.beat_task_name for d in registry.SERIES if d.beat_task_name}
        self.assertEqual(referenced - known, set())

    def test_priorities_are_unique_and_ordered(self):
        priorities = [d.priority for d in registry.SERIES]
        self.assertEqual(len(priorities), len(set(priorities)))
        self.assertEqual(priorities, sorted(priorities))

    def test_calendar_is_first(self):
        self.assertEqual(registry.ordered()[0].name, registry.CALENDAR_SERIES)

    def test_dependencies_name_real_series(self):
        names = set(registry.all_names())
        for descriptor in registry.SERIES:
            self.assertEqual(set(descriptor.depends_on) - names, set(),
                             f'{descriptor.name} depends on an unknown series')

    def test_no_dependency_cycles(self):
        seen, stack = set(), []

        def visit(name):
            self.assertNotIn(name, stack, f'dependency cycle at {name}')
            if name in seen:
                return
            stack.append(name)
            for parent in registry.BY_NAME[name].depends_on:
                visit(parent)
            stack.pop()
            seen.add(name)

        for name in registry.all_names():
            visit(name)

    def test_series_filter_preserves_priority_order(self):
        selected = registry.ordered(['predictions', 'ohlcv'])
        self.assertEqual([d.name for d in selected], ['ohlcv', 'predictions'])


class PredictionDelegationTests(TestCase):
    def ctx(self):
        return CatchUpContext(as_of=AS_OF, open_dates=tuple(OPEN_DAYS),
                              window_start=OPEN_DAYS[0])

    def test_predictions_delegate_not_generate(self):
        # Generating predictions here would double-run against the hourly
        # reconcile-prediction-coverage entry, which already owns this repair.
        gap = SeriesGap('predictions', 'replay', missing_dates=tuple(OPEN_DAYS))
        budget = patch('apps.catchup.service.Budget')
        with patch('apps.prediction.tasks.reconcile_prediction_coverage') as reconcile, \
                patch('apps.prediction.tasks.generate_predictions_for_date') as generate:
            result = delegate_predictions(self.ctx(), gap, budget)
        reconcile.delay.assert_called_once_with(target_date=OPEN_DAYS[0].isoformat())
        generate.assert_not_called()
        # Oldest date first, one per run: the rest converge over later ticks.
        self.assertEqual(result.units_done, 1)
        self.assertEqual(result.units_remaining, 2)
