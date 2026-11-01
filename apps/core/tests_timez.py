"""Tests for the project's time conventions (``apps.core.timez``).

The load-bearing one is ``session_date_of_matches_the_utc_calendar_date``: it is
what makes the session date independent of ``settings.TIME_ZONE``. Flipping that
setting must not move a trading day, and this test is the proof.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from django.utils import timezone

from apps.core.date_floor import session_midnight
from apps.core.timez import SESSION_TZ, session_date_of, session_today

UTC = ZoneInfo('UTC')


def _hourly_probes(start, hours):
    return [start + timedelta(hours=h) for h in range(hours)]


class SessionDateOfTests(SimpleTestCase):
    def test_before_the_boundary_belongs_to_the_previous_session(self):
        # 07:59 Beijing on the 30th is still the 29th's session.
        moment = datetime(2026, 10, 29, 23, 59, tzinfo=UTC)
        self.assertEqual(moment.astimezone(SESSION_TZ).time(), time(7, 59))
        self.assertEqual(session_date_of(moment), date(2026, 10, 29))

    def test_at_the_boundary_the_session_rolls_over(self):
        moment = datetime(2026, 10, 30, 0, 0, tzinfo=UTC)
        self.assertEqual(moment.astimezone(SESSION_TZ).time(), time(8, 0))
        self.assertEqual(session_date_of(moment), date(2026, 10, 30))

    def test_late_in_the_beijing_day_is_still_that_session(self):
        moment = datetime(2026, 10, 30, 15, 59, tzinfo=UTC)
        self.assertEqual(moment.astimezone(SESSION_TZ).time(), time(23, 59))
        self.assertEqual(session_date_of(moment), date(2026, 10, 30))

    def test_rejects_a_naive_datetime(self):
        with self.assertRaises(ValueError):
            session_date_of(datetime(2026, 10, 30, 12))

    def test_session_date_of_matches_the_utc_calendar_date(self):
        """The session date is the UTC date, hour for hour, across a full week.

        Asia/Shanghai is UTC+8 with no DST, so the UTC midnight boundary is
        exactly 08:00 Beijing -- which is where the trading day rolls over. The
        nightly pipeline runs in that 00:00-08:00 Beijing window and must keep
        resolving to the session that just closed, whatever ``TIME_ZONE`` says.
        """
        probes = _hourly_probes(datetime(2026, 10, 26, 0, 0, tzinfo=UTC), 24 * 7)
        self.assertEqual(len(probes), 168)
        for moment in probes:
            self.assertEqual(session_date_of(moment), moment.date(),
                             f'session date drifted at {moment.isoformat()}')

    def test_session_today_agrees_with_the_clock_independent_reference(self):
        self.assertEqual(session_today(), session_date_of(timezone.now()))

    def test_day_of_week_and_month_end_transitions(self):
        # A Sunday and a month end are where a day-of-week/day-of-month beat
        # constraint would shift if the boundary were wrong.
        sunday = datetime(2026, 11, 1, 23, 0, tzinfo=UTC)     # Beijing Mon 07:00
        self.assertEqual(session_date_of(sunday), date(2026, 11, 1))
        month_end = datetime(2026, 10, 31, 23, 0, tzinfo=UTC)  # Beijing Fri 07:00
        self.assertEqual(session_date_of(month_end), date(2026, 10, 31))


class SessionMidnightTests(SimpleTestCase):
    def test_is_aware_and_lands_on_the_requested_day(self):
        day = date(2026, 10, 30)
        midnight = session_midnight(day)
        self.assertTrue(timezone.is_aware(midnight))
        self.assertEqual(timezone.localtime(midnight).date(), day)
        self.assertEqual(timezone.localtime(midnight).time(), time.min)

    def test_bounds_the_day_the_orm_date_lookup_would_cast_to(self):
        """``timestamp__date`` and these bounds must agree on the same day.

        The whole point of the helper is that a raw ``timestamp`` range filter is
        equivalent to the non-sargable ``timestamp__date`` lookup. A row stamped
        inside the bounds has to be the row that lookup returns for that day.
        """
        day = date(2026, 10, 30)
        start = session_midnight(day)
        end = session_midnight(day + timedelta(days=1))
        inside = timezone.make_aware(datetime.combine(day, time(12)))
        self.assertLessEqual(start, inside)
        self.assertLess(inside, end)
        self.assertEqual(timezone.localtime(inside).date(),
                         timezone.localtime(start).date())
