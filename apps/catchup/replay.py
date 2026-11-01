"""Which scheduled occurrences beat never replayed, as a date window.

Beat records the last time it *published* an entry in
``django_celery_beat.PeriodicTask.last_run_at``. That field survives a beat
restart -- ``ModelEntry._unpack_fields`` builds the ``update_or_create`` defaults
from schedule/args/kwargs/options only, never from the run counters -- so after an
outage it still holds the last dispatch before the box went down.

Enumerating the individual crontab occurrences in between is deliberately not
done: ``celery.schedules.crontab`` exposes ``is_due`` but no "list occurrences in
range" API, and reimplementing it would duplicate celery internals. A window is
enough, because every repair entry point here takes an explicit date range and is
idempotent.

The limitation to keep in mind: ``last_run_at`` proves publication, not success.
A task beat published and a worker then dropped, or that TuShare throttled, leaves
a fresh watermark and missing rows. That case is covered by the data-level
detectors in ``detectors.py``, not by this module.
"""

from datetime import datetime, time, timedelta

from django.utils import timezone
from django_celery_beat.models import PeriodicTask

# Grace before a stale watermark counts as a missed occurrence. Sized per series
# in the registry: a daily entry needs a day, a monthly one needs its whole
# cadence or every run looks like an outage.
DEFAULT_GRACE_DAYS = 1


def missed_window(task_name, as_of, grace_days=DEFAULT_GRACE_DAYS):
    """``(start, end)`` datetimes beat failed to cover, or ``None`` if it kept up.

    ``None`` also covers "no such entry" and "entry disabled", which are not
    outages -- a task nobody scheduled has no occurrences to miss.
    """
    if not task_name:
        return None
    task = PeriodicTask.objects.filter(name=task_name, enabled=True).first()
    if task is None or task.last_run_at is None:
        return None
    last = timezone.localtime(task.last_run_at).date()
    if last >= as_of - timedelta(days=max(int(grace_days), 0)):
        return None
    start = timezone.make_aware(datetime.combine(last, time.min))
    end = timezone.make_aware(datetime.combine(as_of, time.max))
    return (start, end)


def classify_cause(task_name, as_of, grace_days, gap):
    """Name why a gap exists, for ``CatchUpRun.cause``."""
    from .models import CatchUpRun

    if missed_window(task_name, as_of, grace_days) is not None:
        return CatchUpRun.Cause.BEAT_OUTAGE
    if gap is not None and not gap.is_empty:
        return CatchUpRun.Cause.PROVIDER_GAP
    return CatchUpRun.Cause.UNKNOWN
