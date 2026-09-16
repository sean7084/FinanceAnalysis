"""Detection of backtest runs whose worker has gone away.

A chunked run re-queues its own continuation, so ``RUNNING`` is a normal state that
can persist for a long time. That makes "is this run actually progressing?" a real
question, and the answer cannot come from ``status`` alone.

The distinction that matters is between a run whose Celery task reached a terminal
state while the row still says ``RUNNING`` -- genuinely orphaned -- and a run whose
continuation is queued and simply waiting for a free worker. The second case looks
identical from the outside: ``PENDING`` task state, ``RUNNING`` row.

What separates them is ``runtime_state`` / ``progress`` in the report. A chunked
continuation always carries resume state, so ``PENDING`` **with** progress is
legitimate, while ``PENDING`` with no progress and an age past the threshold is
orphaned. Getting this backwards either restarts healthy runs or leaves dead ones
stuck forever.

The threshold is ``BACKTEST_STALE_TASK_MAX_AGE_SECONDS`` (default 2400), read from
the environment via ``config/settings/base.py``.
"""

from celery.result import AsyncResult
from django.conf import settings
from django.utils import timezone

from config.celery import app as celery_app

from .models import BacktestRun


DEFAULT_STALE_TASK_MAX_AGE_SECONDS = 2400
TERMINAL_TASK_STATES = {'FAILURE', 'REVOKED', 'SUCCESS'}


def _stale_task_max_age_seconds():
    raw_value = getattr(settings, 'BACKTEST_STALE_TASK_MAX_AGE_SECONDS', DEFAULT_STALE_TASK_MAX_AGE_SECONDS)
    try:
        return max(int(raw_value), 60)
    except (TypeError, ValueError):
        return DEFAULT_STALE_TASK_MAX_AGE_SECONDS


def get_backtest_run_task_owner_state(run):
    report = dict(getattr(run, 'report', {}) or {})
    has_runtime_progress = 'runtime_state' in report or 'progress' in report

    reference_dt = getattr(run, 'updated_at', None)
    if not has_runtime_progress and getattr(run, 'started_at', None) is not None:
        reference_dt = run.started_at

    age_seconds = None
    if reference_dt is not None:
        age_seconds = max(0, int((timezone.now() - reference_dt).total_seconds()))

    if run.status != BacktestRun.Status.RUNNING:
        return {
            'task_state': '',
            'task_age_seconds': age_seconds,
            'has_stale_task_owner': False,
        }

    task_id = (run.current_task_id or '').strip()
    if not task_id:
        return {
            'task_state': '',
            'task_age_seconds': age_seconds,
            'has_stale_task_owner': age_seconds is not None and age_seconds >= _stale_task_max_age_seconds(),
        }

    try:
        task_state = AsyncResult(task_id, app=celery_app).state
    except Exception:
        task_state = 'UNKNOWN'

    has_stale_task_owner = False
    if task_state in TERMINAL_TASK_STATES:
        has_stale_task_owner = True
    # Chunked backtests queue the next task id before a worker owns it, so a
    # long-running suite can legitimately leave the next owner in PENDING while
    # earlier runs are still consuming worker slots.
    elif (
        task_state == 'PENDING'
        and not has_runtime_progress
        and age_seconds is not None
        and age_seconds >= _stale_task_max_age_seconds()
    ):
        has_stale_task_owner = True

    return {
        'task_state': task_state,
        'task_age_seconds': age_seconds,
        'has_stale_task_owner': has_stale_task_owner,
    }