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
    elif task_state == 'PENDING' and age_seconds is not None and age_seconds >= _stale_task_max_age_seconds():
        has_stale_task_owner = True

    return {
        'task_state': task_state,
        'task_age_seconds': age_seconds,
        'has_stale_task_owner': has_stale_task_owner,
    }