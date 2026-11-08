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
from apps.core.timez import (
    MARKET_CLOSE,
    SESSION_TZ,
    session_close_instant,
    session_date_of,
    session_has_closed,
    session_today,
)

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


class SessionHasClosedTests(SimpleTestCase):
    """The repair-path guard for catch-up's in-progress-session false gap."""

    def test_close_instant_is_fifteen_hundred_beijing(self):
        instant = session_close_instant(date(2026, 10, 30))
        self.assertTrue(timezone.is_aware(instant))
        self.assertEqual(instant.astimezone(SESSION_TZ).time(), MARKET_CLOSE)
        self.assertEqual(instant.astimezone(UTC), datetime(2026, 10, 30, 7, 0, tzinfo=UTC))

    def test_before_close_is_open_and_at_close_is_closed(self):
        day = date(2026, 10, 30)
        self.assertFalse(session_has_closed(day, datetime(2026, 10, 30, 6, 59, tzinfo=UTC)))
        self.assertTrue(session_has_closed(day, datetime(2026, 10, 30, 7, 0, tzinfo=UTC)))
        self.assertTrue(session_has_closed(day, datetime(2026, 10, 30, 7, 1, tzinfo=UTC)))

    def test_a_past_session_is_closed_at_any_hour(self):
        # --as-of overrides and the nightly pipeline both name a finished session.
        self.assertTrue(session_has_closed(
            date(2026, 10, 29), datetime(2026, 10, 30, 2, 0, tzinfo=UTC)))

    def test_a_future_session_is_not_closed(self):
        self.assertFalse(session_has_closed(
            date(2026, 11, 2), datetime(2026, 10, 30, 12, 0, tzinfo=UTC)))

    def test_rejects_a_naive_datetime(self):
        with self.assertRaises(ValueError):
            session_has_closed(date(2026, 10, 30), datetime(2026, 10, 30, 12))

    def test_the_exposure_window_is_seven_hours_not_fifteen(self):
        """Pins why the guard belongs on the repair path and not on ``as_of``.

        Catch-up derives ``as_of`` from ``session_today()``. Hour by hour across a
        day, that session is still open for exactly the 08:00-15:00 Beijing window
        -- seven hours. Deriving it from ``timezone.localdate()`` instead would
        leave it open from 00:00, widening the window to fifteen hours while
        looking more "correct". The guard must cover the seven, not the fifteen.
        """
        probes = _hourly_probes(datetime(2026, 10, 29, 16, 0, tzinfo=UTC), 24)
        session_open = [
            m for m in probes if not session_has_closed(session_date_of(m), m)
        ]
        calendar_open = [
            m for m in probes if not session_has_closed(m.astimezone(SESSION_TZ).date(), m)
        ]
        self.assertEqual(len(session_open), 7)
        self.assertEqual(len(calendar_open), 15)
        self.assertEqual(
            [m.astimezone(SESSION_TZ).hour for m in session_open], list(range(8, 15)))

    def test_is_independent_of_the_time_zone_setting(self):
        from django.test import override_settings

        day = date(2026, 10, 30)
        moment = datetime(2026, 10, 30, 6, 0, tzinfo=UTC)  # 14:00 Beijing, still open
        with override_settings(TIME_ZONE='UTC'):
            self.assertFalse(session_has_closed(day, moment))
        with override_settings(TIME_ZONE='America/New_York'):
            self.assertFalse(session_has_closed(day, moment))
