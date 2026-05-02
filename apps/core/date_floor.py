from datetime import date

from django.conf import settings


DEFAULT_HISTORICAL_DATA_FLOOR = date(2010, 1, 1)


def get_historical_data_floor():
    floor_raw = getattr(settings, 'HISTORICAL_DATA_FLOOR', DEFAULT_HISTORICAL_DATA_FLOOR.isoformat())
    try:
        return date.fromisoformat(str(floor_raw))
    except ValueError:
        return DEFAULT_HISTORICAL_DATA_FLOOR