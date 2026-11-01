from celery import shared_task

from .models import CatchUpRun
from .service import run_catch_up


@shared_task(bind=True, name='apps.catchup.tasks.catch_up_missed_runs',
             soft_time_limit=1200, time_limit=1500)
def catch_up_missed_runs(self, max_calls=None, max_seconds=None):
    """Converge the holes an outage left, one bounded pass per hour.

    Overrides the global 60s/300s pair for the same reason
    ``run_post_sync_universal_refresh`` does: one pass walks seventeen series in
    dependency order and the capital-flow leg alone spends >=350s in inter-call
    pauses. Unlike that task this one does not try to finish -- it repairs the
    oldest unit per series and lets the hourly repetition converge, so 1200s is
    ample without being a job nobody would approve a budget for. The heavy
    full-window repair belongs to the cold-start hook, which runs
    ``manage.py catch_up`` in the foreground with no Celery time limit at all.
    """
    runs = run_catch_up(max_calls=max_calls, max_seconds=max_seconds,
                        trigger=CatchUpRun.Trigger.BEAT)
    if not runs:
        return 'no work: another catch-up is in flight'
    return '; '.join(f'{row.series}={row.outcome}' for row in runs)
