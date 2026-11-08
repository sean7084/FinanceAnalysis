from datetime import date, datetime, time

from django.conf import settings
from django.utils import timezone

from apps.core.timez import SESSION_TZ


DEFAULT_HISTORICAL_DATA_FLOOR = date(2010, 1, 1)


def get_historical_data_floor():
    floor_raw = getattr(settings, 'HISTORICAL_DATA_FLOOR', DEFAULT_HISTORICAL_DATA_FLOOR.isoformat())
    try:
        return date.fromisoformat(str(floor_raw))
    except ValueError:
        return DEFAULT_HISTORICAL_DATA_FLOOR


def session_midnight(day):
    """Return an aware midnight in the active ``TIME_ZONE`` for ``day``.

    The ORM ``timestamp__date`` lookup casts to a date in the current timezone,
    which is non-sargable and forces a sequential scan on huge tables such as
    ``analytics_technicalindicator``. Filtering the raw ``timestamp`` against
    midnight bounds built the same way is equivalent -- both sides resolve
    through ``TIME_ZONE``, so the pairing keeps working whatever the setting is
    set to -- and lets PostgreSQL use a ``(asset_id, timestamp,
    indicator_type)`` index instead.
    """
    return timezone.make_aware(datetime.combine(day, time.min))


def session_key_timestamp(day):
    """Zone-stable write key: midnight of ``day`` in ``SESSION_TZ``, explicitly.

    Every ``TechnicalIndicator`` write keys on
    ``(asset, timestamp, indicator_type, parameters)``. ``session_midnight`` above takes
    its zone from the mutable ``TIME_ZONE`` setting, so the same trade date produces a
    *different instant* before and after a setting change -- and a recompute then inserts
    a parallel row instead of updating the existing one. ``unique_together`` cannot catch
    it because the timestamps differ. The 2026-11-02 flip from UTC to Asia/Shanghai did
    exactly that: rows written before it sit at ``00:00+00``, rows after at ``00:00+08``,
    which is eight hours *earlier* as an instant.

    So use this for write keys and keep ``session_midnight`` for read windows. The read
    helper's ``timestamp__date`` pairing is correct precisely because both sides resolve
    through the same mutable setting; that is the asymmetry, and it is deliberate.

    ``day`` may be a ``datetime`` or a pandas ``Timestamp``; only the calendar date is used.
    """
    if isinstance(day, datetime):
        day = day.date()
    return datetime.combine(day, time.min, tzinfo=SESSION_TZ)
