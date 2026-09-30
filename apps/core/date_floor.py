from datetime import date, datetime, timezone

from django.conf import settings


DEFAULT_HISTORICAL_DATA_FLOOR = date(2010, 1, 1)


def get_historical_data_floor():
    floor_raw = getattr(settings, 'HISTORICAL_DATA_FLOOR', DEFAULT_HISTORICAL_DATA_FLOOR.isoformat())
    try:
        return date.fromisoformat(str(floor_raw))
    except ValueError:
        return DEFAULT_HISTORICAL_DATA_FLOOR


def utc_midnight(day):
    """Return an aware UTC midnight for ``day`` (a ``datetime.date``).

    Under ``TIME_ZONE='UTC'``/``USE_TZ=True`` the ORM ``timestamp__date`` lookup casts to
    the UTC date, which is non-sargable and forces a sequential scan on huge tables such
    as ``analytics_technicalindicator``. Filtering the raw ``timestamp`` against these
    UTC-midnight bounds is equivalent and lets PostgreSQL use a
    ``(asset_id, timestamp, indicator_type)`` index instead.
    """
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)