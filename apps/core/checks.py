"""Project-wide system checks.

One check, and it exists because a setting flip silently doubled a key space.

``TIME_ZONE`` moved from UTC to Asia/Shanghai on 2026-11-02. The only data correction
shipped with it was for ``NewsArticle.published_at``. Every ``TechnicalIndicator`` write
key is built from the *setting*, so rows written before the flip sit at midnight UTC and
rows after at midnight Beijing -- eight hours apart as instants, for the same trade date.
``unique_together`` includes ``timestamp``, so it cannot catch the duplicate, and a
recompute inserts a parallel row instead of updating the existing one.

The writers now use ``apps/core/date_floor.session_key_timestamp``, which takes its zone
explicitly and no longer moves with the setting. This check makes any future flip a loud
failure at startup and in CI instead of another silent migration of the key space: a flip
must enumerate its dependents before it can pass.
"""
from django.conf import settings
from django.core.checks import Error, register

from apps.core.timez import SESSION_TZ_NAME

TIME_ZONE_CHECK_ID = 'core.E001'


@register()
def check_time_zone_matches_session_tz(app_configs, **kwargs):
    """``TIME_ZONE`` and the session time zone must agree.

    They are allowed to differ in principle, but nothing in this project has been
    audited for that case. The session date helpers, the ``timestamp__date`` SQL casts
    and every read window assume they coincide. If they ever need to diverge, this check
    is the list of things to re-verify rather than a rule to delete.
    """
    if settings.TIME_ZONE == SESSION_TZ_NAME:
        return []
    return [
        Error(
            f'TIME_ZONE is {settings.TIME_ZONE!r} but the session time zone is '
            f'{SESSION_TZ_NAME!r}.',
            hint=(
                'The session-date helpers, the timestamp__date casts and every read '
                'window assume these agree. A flip changes what a TechnicalIndicator '
                'write key means, which silently inserts parallel rows for the same '
                'trade date because unique_together includes the timestamp. Audit the '
                'date-keyed tables and ship a data correction before changing this.'
            ),
            id=TIME_ZONE_CHECK_ID,
        )
    ]