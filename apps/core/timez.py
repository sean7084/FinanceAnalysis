"""Project time conventions.

Two different "todays" exist in this codebase, and mixing them is the bug this
module exists to prevent:

``timezone.localdate()``
    The Beijing calendar date. Right for command defaults, freshness math,
    "current month", and user-facing validation.

``session_today()``
    The A-share session date: the Beijing calendar date, minus one day before
    08:00 Beijing. Overnight news and the 00:10-04:15 nightly pipeline belong
    to the session that just closed.

``session_today()`` equals ``timezone.now().date()`` at every instant, because
the UTC date boundary IS 08:00 Beijing. Do not "simplify" it to
``timezone.localdate()``: those two disagree during 00:00-08:00 Beijing, which
is exactly when the nightly chain runs, and a task handed the calendar date
there goes looking for a session that has not opened yet.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

SESSION_TZ_NAME = 'Asia/Shanghai'
SESSION_TZ = ZoneInfo(SESSION_TZ_NAME)

# The hour, in Beijing wall time, at which the trading day rolls over. Chosen so
# that the session date equals the UTC calendar date, which is what every
# long-standing ``timezone.now().date()`` call site in the task layer already
# computes.
SESSION_DAY_BOUNDARY = time(8, 0)


def session_date_of(moment: datetime) -> date:
    """The session date an aware instant belongs to. Naive input is rejected."""
    if timezone.is_naive(moment):
        raise ValueError('session_date_of requires an aware datetime')
    local = moment.astimezone(SESSION_TZ)
    day = local.date()
    if local.time() < SESSION_DAY_BOUNDARY:
        return day - timedelta(days=1)
    return day


def session_today() -> date:
    """The session date of the current instant."""
    return session_date_of(timezone.now())


# A-share continuous trading ends at 15:00 Beijing. Before that instant the
# session named by ``session_today()`` is still in progress, so it cannot have a
# complete daily bar yet and asking a provider for one is a guaranteed miss.
MARKET_CLOSE = time(15, 0)


def session_close_instant(session_day: date) -> datetime:
    """The aware instant at which ``session_day`` stops trading."""
    return datetime.combine(session_day, MARKET_CLOSE, tzinfo=SESSION_TZ)


def session_has_closed(session_day: date, moment: datetime | None = None) -> bool:
    """Whether ``session_day`` is finished trading as of ``moment`` (default now).

    This is a repair-path guard, not an alternative ``as_of``. Catch-up derives
    ``as_of`` from ``session_today()``, which names the in-progress session from
    08:00 Beijing onward; the detectors then legitimately report it as missing
    because no row can exist yet. Repairing that date spends provider quota on a
    hole the nightly sync fills by itself, and records a permanent FAILED /
    PROVIDER_GAP row for it every hour.

    Do not "fix" this by switching ``as_of`` to ``timezone.localdate()``: that
    widens the exposure from ~7h/day (08:00-15:00) to ~15h/day (00:00-15:00),
    because the calendar date names the unfinished session for longer.
    """
    if moment is None:
        moment = timezone.now()
    if timezone.is_naive(moment):
        raise ValueError('session_has_closed requires an aware datetime')
    return moment >= session_close_instant(session_day)
