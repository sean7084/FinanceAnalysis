"""Project-wide system checks.

Two checks, each there because the failure it catches is invisible to every other gate.

The first: ``TIME_ZONE`` moved from UTC to Asia/Shanghai on 2026-11-02, and the only data
correction shipped with it was for the news article timestamps
(``sentiment_newsarticle.published_at``, a table and model both since deleted with the news
module -- see docs/reference/sentiment-data-corrections.md). Every ``TechnicalIndicator``
write key is built from the *setting*, so rows written before the flip sit at midnight UTC
and rows after at midnight Beijing -- eight hours apart as instants, for the same trade date.
``unique_together`` includes ``timestamp``, so it cannot catch the duplicate, and a
recompute inserts a parallel row instead of updating the existing one.

The writers now use ``apps/core/date_floor.session_key_timestamp``, which takes its zone
explicitly and no longer moves with the setting. This check makes any future flip a loud
failure at startup and in CI instead of another silent migration of the key space: a flip
must enumerate its dependents before it can pass.

The second: deleting a ``CELERY_BEAT_SCHEDULE`` entry does not stop the schedule. See the
check's own docstring for why nothing else in the repo could notice.
"""
import importlib.util

from django.apps import apps as django_apps
from django.conf import settings
from django.core.checks import Error, register

from apps.core.timez import SESSION_TZ_NAME

def _module_absent(module_name):
    """True when a module cannot be located at all.

    find_spec returns None for a missing submodule but raises ModuleNotFoundError when the
    *parent package* is gone, which is exactly the case for an app whose directory was deleted
    (#121 removed apps/sentiment whole). Both answers mean absent here, so the check must not
    crash while describing the absence it exists to report.
    """
    try:
        return importlib.util.find_spec(module_name) is None
    except (ImportError, ValueError):
        return True


TIME_ZONE_CHECK_ID = 'core.E001'
PERIODIC_TASK_CHECK_ID = 'core.E002'


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


@register()
def check_enabled_periodic_tasks_are_dispatchable(app_configs, **kwargs):
    """Every enabled ``PeriodicTask`` row must name a task a worker can actually import.

    Beat runs on ``django_celery_beat.schedulers.DatabaseScheduler``, which builds its live
    schedule from ``PeriodicTask.objects.enabled()`` and pulls ``CELERY_BEAT_SCHEDULE`` into
    it through ``update_from_dict`` -- a method that only adds and updates. Deleting a
    scheduled task from settings therefore does not stop it: the row stays enabled and keeps
    firing, and if the module behind it has been deleted the worker answers with an unregistered-task
    error on every tick.

    That is how #110 left three rows pointing at ``apps.sentiment.tasks`` after the entries
    were removed from settings and the module itself was deleted with the news module (#117).
    No test could see it: the beat table in CI is created empty, so the settings-side tests
    in ``apps/core/tests_beat_schedule.py`` pass while production dispatches dead tasks. This
    check compares the two halves that actually have to agree -- the rows beat reads and the
    registry the worker reads.
    """
    if not django_apps.is_installed('django_celery_beat'):
        return []

    from django.db import DatabaseError

    try:
        from django_celery_beat.models import PeriodicTask
        rows = list(PeriodicTask.objects.filter(enabled=True).values_list('name', 'task'))
    except DatabaseError:
        # No beat tables yet: during a fresh migrate or a test-DB build there is nothing to
        # disagree with, and a check that errors before the schema exists would block the
        # very command that creates it.
        return []

    if not rows:
        return []

    from config.celery import app

    # Not `app.loader.import_default_modules()`, which is what a worker uses. That call
    # sends the `import_modules` signal, and celery's Django fixup answers it with
    # `validate_models()` -> `run_checks()` -> this function: an unbounded recursion that
    # dies with a RecursionError instead of reporting anything. autodiscover_tasks() only
    # ever imports `<app>.tasks`, so importing exactly those here produces the same registry
    # without re-entering the check machinery.
    broken_modules = {}
    for app_config in django_apps.get_app_configs():
        module_name = f'{app_config.name}.tasks'
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                broken_modules[module_name] = f'{type(exc).__name__}: {exc}'
            # exc.name == module_name means the app simply has no tasks module.
        except Exception as exc:  # noqa: BLE001 -- any import failure is the finding itself
            broken_modules[module_name] = f'{type(exc).__name__}: {exc}'

    registered = set(app.tasks)

    errors = []
    for name, task in sorted(rows):
        if task in registered:
            continue
        module, _, _ = task.rpartition('.')
        if module in broken_modules:
            detail = f', whose module failed to import ({broken_modules[module]})'
        elif module and _module_absent(module):
            detail = ', and the module no longer exists'
        else:
            detail = ''
        errors.append(
            Error(
                f'Enabled periodic task {name!r} dispatches {task!r}, which is not in the '
                f'celery task registry{detail}.',
                hint=(
                    'Beat reads PeriodicTask rows from the database, not CELERY_BEAT_SCHEDULE, '
                    'and it never prunes a row whose settings entry has gone. Either delete the '
                    'row (the task is gone for good) or restore the module it names. A row that '
                    'stays enabled while unimportable fires on every scheduled tick and the '
                    'worker rejects it.'
                ),
                id=PERIODIC_TASK_CHECK_ID,
            )
        )
    return errors