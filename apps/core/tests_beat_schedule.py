"""The beat schedule must fire at exactly the instants it always did.

``TIME_ZONE`` and ``CELERY_TIMEZONE`` were flipped to Asia/Shanghai and every
crontab hour number rewritten to match. That is a pure relabelling: ``16:10
UTC`` and ``00:10 CST`` are the same moment. It is also the single change in
this refactor that can take the nightly pipeline down eight hours in either
direction, and a schedule that is wrong by eight hours looks completely healthy
until the data lands late.

So the old UTC table is pinned here verbatim and never edited. The proof is
that both tables select the same minutes: ``crontab`` decides by testing each
calendar field against the wall-clock reading of a candidate minute in its own
timezone, so the set of matching minutes *is* the schedule. Comparing those
sets is exact. ``crontab.remaining_estimate`` cannot be used — it measures from
``self.now()``, the real wall clock, so ``probe + remaining_estimate(probe)``
mixes two reference frames and reports a drift that does not exist.

The window is a full week of minutes, chosen to contain a Sunday, a month end
and a month start, so both day-of-week and day-of-month constraints are
exercised either side of a firing. That is where a naive ``hour + 8`` goes
silently wrong.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from celery.schedules import crontab
from django.conf import settings
from django.test import SimpleTestCase

UTC = ZoneInfo('UTC')
BEIJING = ZoneInfo('Asia/Shanghai')

# The beat table as it stood under TIME_ZONE='UTC'. This is the regression
# baseline: change it only to record a deliberate schedule move.
PRE_UNIFICATION_UTC = {
    'sync-a-shares-daily-from-tushare': {'hour': '16', 'minute': '10'},
    'sync-index-memberships-monthly': {'day_of_month': '1', 'hour': '2', 'minute': '15'},
    'check-alert-rules-every-5-min': {'minute': '*/5'},
    'sync-macro-data-monthly': {'day_of_month': '2-8', 'hour': '0', 'minute': '10'},
    'fetch-latest-market-news-daily': {'hour': '16', 'minute': '35'},
    'run-hourly-historical-news-backfill': {'minute': '12'},
    'run-daily-sentiment-pipeline': {'hour': '17', 'minute': '0'},
    'generate-predictions-daily': {'hour': '18', 'minute': '0'},
    'generate-lightgbm-predictions-daily': {'hour': '18', 'minute': '30'},
    'prewarm-dashboard-stocks-cache': {'hour': '19', 'minute': '15'},
    'reconcile-prediction-coverage-hourly': {'minute': '45'},
    'catch-up-missed-series-hourly': {'minute': '25'},
    'sync-etf-daily-bars-daily': {'hour': '16', 'minute': '45'},
    'sync-etf-benchmark-index-daily': {'hour': '17', 'minute': '5'},
    'compute-etf-features-daily': {'hour': '17', 'minute': '20'},
    'check-etf-rotation-feed-health-daily': {'hour': '17', 'minute': '45'},
    'generate-etf-rotation-signal-daily': {'hour': '19', 'minute': '45'},
    'generate-etf-rotation-shadow-signal-daily': {'hour': '19', 'minute': '50'},
    'mark-etf-rotation-positions-daily': {'hour': '20', 'minute': '15'},
    'report-etf-rotation-live-track-monthly': {'hour': '21', 'minute': '0',
                                               'day_of_month': '1'},
    'sync-etf-fund-shares-weekly': {'hour': '18', 'minute': '10', 'day_of_week': '6'},
}

WINDOW_START = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
WINDOW_MINUTES = 24 * 60 * 7

# (month, day, dow, hour, minute) for every minute of the window, read in each
# frame. Celery numbers Sunday as 0, hence ``isoweekday() % 7``.
_WINDOW = [WINDOW_START + timedelta(minutes=m) for m in range(WINDOW_MINUTES)]


def _wall_clock_keys(zone):
    return [
        (wall.month, wall.day, wall.isoweekday() % 7, wall.hour, wall.minute)
        for wall in (instant.astimezone(zone) for instant in _WINDOW)
    ]


UTC_KEYS = _wall_clock_keys(UTC)
BEIJING_KEYS = _wall_clock_keys(BEIJING)


def _matching_minutes(parsed, wall_clock_keys):
    """Window offsets at which ``parsed`` selects the wall-clock reading."""
    return [
        index for index, (month, day, dow, hour, minute) in enumerate(wall_clock_keys)
        if month in parsed.month_of_year and day in parsed.day_of_month
        and dow in parsed.day_of_week and hour in parsed.hour and minute in parsed.minute
    ]


class BeatScheduleTests(SimpleTestCase):
    def test_settings_and_baseline_cover_the_same_entries(self):
        self.assertEqual(set(settings.CELERY_BEAT_SCHEDULE), set(PRE_UNIFICATION_UTC),
                         'the beat table changed shape; the pinned baseline must be '
                         'updated deliberately, not silently')

    def test_settings_timezone_and_celery_timezone_agree(self):
        """Celery does not fall back to Django's TIME_ZONE, so both must be set."""
        self.assertEqual(settings.TIME_ZONE, 'Asia/Shanghai')
        self.assertEqual(settings.CELERY_TIMEZONE, settings.TIME_ZONE)

    def test_no_entry_uses_the_illegal_day_of_week_seven(self):
        """celery 5.4 raises ValueError: Invalid end range: 7 > 6 at beat startup.

        ``sync-etf-fund-shares-weekly`` was Saturday in UTC terms and becomes
        Sunday under +8; Sunday is '0' in celery's numbering, not '7'.
        """
        for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            self.assertNotIn('7', str(entry['schedule']._orig_day_of_week), name)

    def test_time_zone_is_declared_exactly_once(self):
        import pathlib

        text = pathlib.Path(settings.BASE_DIR / 'config' / 'settings' / 'base.py').read_text()
        assignments = [line for line in text.splitlines()
                       if line.startswith('TIME_ZONE =')]
        self.assertEqual(len(assignments), 1,
                         f'TIME_ZONE declared {len(assignments)} times; the later '
                         'one would silently win')

    def test_every_entry_fires_at_the_same_instants_as_before(self):
        for name, old_fields in PRE_UNIFICATION_UTC.items():
            new = settings.CELERY_BEAT_SCHEDULE[name]['schedule']
            before = [_WINDOW[i] for i in _matching_minutes(crontab(**old_fields), UTC_KEYS)]
            after = [_WINDOW[i] for i in _matching_minutes(new, BEIJING_KEYS)]
            self.assertTrue(before, f'{name} never fires inside the probe window')
            self.assertEqual(
                before, after,
                f'{name} moved: {len(before)} UTC-era instants vs {len(after)} now')

    def test_the_proof_can_fail(self):
        """A harness that cannot fail proves nothing.

        Two mistakes this window must catch: an entry left at its UTC hour
        number, and an entry whose day constraint was not shifted along with an
        hour that now rolls past midnight.
        """
        unflipped = crontab(hour='16', minute='45')
        self.assertNotEqual(
            _matching_minutes(crontab(**PRE_UNIFICATION_UTC['sync-etf-daily-bars-daily']), UTC_KEYS),
            _matching_minutes(unflipped, BEIJING_KEYS))

        dom_left_behind = crontab(hour='5', minute='0', day_of_month='1')
        self.assertNotEqual(
            _matching_minutes(
                crontab(**PRE_UNIFICATION_UTC['report-etf-rotation-live-track-monthly']), UTC_KEYS),
            _matching_minutes(dom_left_behind, BEIJING_KEYS))

    def test_the_two_midnight_crossing_entries_kept_their_day_constraints_aligned(self):
        """The only two entries whose +8 rolls past midnight also carry a day
        constraint, so the constraint had to move with the hour."""
        report = settings.CELERY_BEAT_SCHEDULE[
            'report-etf-rotation-live-track-monthly']['schedule']
        self.assertEqual(sorted(report._orig_day_of_month), ['2'])
        self.assertEqual(sorted(report._orig_hour), ['5'])

        shares = settings.CELERY_BEAT_SCHEDULE[
            'sync-etf-fund-shares-weekly']['schedule']
        self.assertEqual(sorted(shares._orig_day_of_week), ['0'])
        self.assertEqual(sorted(shares._orig_hour), ['2'])

    def test_the_month_end_sequence_still_runs_in_the_same_order(self):
        """The comments that pin these entries between each other describe one
        Beijing night: the ledger closes the month's last displaced leg at
        04:15 CST, the live-track report reads it at 05:00 CST on the 2nd."""
        fired = {}
        for name in ('mark-etf-rotation-positions-daily',
                     'report-etf-rotation-live-track-monthly'):
            schedule = settings.CELERY_BEAT_SCHEDULE[name]['schedule']
            fired[name] = [_WINDOW[i] for i in _matching_minutes(schedule, BEIJING_KEYS)]

        for report_fire in fired['report-etf-rotation-live-track-monthly']:
            ledgers_before = [t for t in fired['mark-etf-rotation-positions-daily']
                              if t < report_fire]
            self.assertTrue(ledgers_before, f'{report_fire} has no ledger before it')
            gap = report_fire - max(ledgers_before)
            self.assertLess(gap, timedelta(minutes=60),
                            f'the report ran {gap} after the ledger, not on its heels')
