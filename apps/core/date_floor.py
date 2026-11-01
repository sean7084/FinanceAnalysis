from datetime import date, datetime, time

from django.conf import settings
from django.utils import timezone


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
