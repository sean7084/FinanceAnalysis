"""Tests for the #50 fix and for registry completeness.

Scope honesty, stated up front because it is the interesting part. These tests prove that the
resilience settings **reach the redis client**, and that the result-storage opt-out is exactly
two tasks. They do not prove that a silently half-open socket is recovered on the NAS link. A
`CLIENT KILL` test cannot show that either: killing a connection produces a dead socket, and
redis-py reconnects on a dead socket whether or not ``health_check_interval`` is set. The bug is
the opposite case -- the socket still looks writable, so the first anyone hears of it is an
Errno 104 in the middle of ``mark_as_done``. Only the interval itself addresses that, and
demonstrating it needs a TCP proxy that drops traffic without closing the connection.

So the recovery claim is deferred to Phase 1 Step 6 (live observation with beat running) rather
than dressed up as a unit test. What is asserted here is the part that can be asserted exactly:
that the knobs are present in the connection parameters Celery will hand the pool, and -- via
the negative control -- that they come from this project's settings rather than from a library
default that would be present either way.

Why a probe app is needed: ``config/celery.py:13`` populates ``app.conf`` from Django settings
at import time, and Celery does not listen for ``setting_changed``. ``override_settings``
therefore cannot change what an already-built backend reads. The positive case reads the real
app; the negative case builds a fresh one.
"""

from pathlib import Path

from celery import Celery
from celery.backends.redis import RedisBackend
from django.conf import settings
from django.test import SimpleTestCase, TestCase

from config.celery import app as project_app

EXPECTED_KNOBS = {
    'health_check_interval': 25,
    'socket_keepalive': True,
    'retry_on_timeout': True,
    'socket_connect_timeout': 10,
}

BACKTEST_TASKS = {
    'apps.backtest.tasks.run_backtest',
    'apps.etf_rotation.tasks.run_etf_rotation_backtest',
}


def _bare_backend(**overrides):
    """A backend built from an app with the resilience knobs explicitly unset."""
    probe = Celery('connparams-probe')
    probe.conf.update(
        broker_url='redis://localhost:6379/0',
        result_backend='redis://localhost:6379/0',
        redis_backend_health_check_interval=None,
        redis_socket_keepalive=None,
        redis_retry_on_timeout=None,
        redis_socket_connect_timeout=None,
        redis_socket_timeout=None,
    )
    if overrides:
        probe.conf.update(overrides)
    return RedisBackend(app=probe)


class RedisBackendConnparamsTests(SimpleTestCase):
    def test_the_project_backend_carries_every_resilience_knob(self):
        params = RedisBackend(app=project_app).connparams
        for key, expected in EXPECTED_KNOBS.items():
            self.assertIn(
                key, params,
                f'{key} never reached connparams: the setting name is wrong, or celery reads '
                'it somewhere else. This is the assertion that fails if the fix is written as '
                'result_backend_transport_options, which celery 5.4 does not merge here.',
            )
            self.assertEqual(params[key], expected, f'{key} reached connparams with the wrong value')

    def test_the_knobs_come_from_settings_not_from_library_defaults(self):
        """Negative control. Without it the test above could pass on someone else's defaults.

        kombu's redis transport defaults health_check_interval to 25 on its own, so a test that
        only looked at the real backend could be satisfied by a broker default leaking in. With
        the knobs unset the backend must show nothing: no interval, no keepalive, and
        retry_on_timeout False.
        """
        params = _bare_backend().connparams
        self.assertNotIn('health_check_interval', params)
        self.assertNotIn('socket_keepalive', params)
        self.assertIs(params['socket_connect_timeout'], None)
        self.assertIs(params['retry_on_timeout'], False)

    def test_broker_transport_options_are_not_the_channel_for_backend_health(self):
        """Pinned so a future "cleanup" does not move these to the setting that does nothing."""
        params = _bare_backend(**{'result_backend_transport_options': {
            'health_check_interval': 25, 'socket_keepalive': True, 'retry_on_timeout': True,
        }}).connparams
        self.assertNotIn('health_check_interval', params)
        self.assertNotIn('socket_keepalive', params)


class ResultStorageTests(TestCase):
    """TestCase, not SimpleTestCase: import_default_modules() needs a database.

    That call fires celery's ``import_modules`` signal, whose Django fixup runs
    ``validate_models()`` -> ``run_checks()``, and ``core.E002`` is now one of the registered
    checks -- so it reads the beat table. Same trap the check itself had to be written around,
    arriving from the test side this time.
    """

    def test_results_are_not_stored_by_default(self):
        self.assertTrue(project_app.conf.task_ignore_result)

    def test_only_the_two_task_health_consumers_store_results(self):
        """task_health reads terminal state for these two and for nobody else.

        Asserted on the effective attribute, because that is how celery actually works: the
        option is resolved from config onto the task class at decoration, so every task ends up
        carrying a real boolean rather than the ``None`` I first assumed would mark an explicit
        declaration. That makes this the stronger statement anyway -- not "who declared
        something" but "who will actually write a result", which is the code path #50 dies in.
        """
        project_app.loader.import_default_modules()
        storing = {
            name for name, task in project_app.tasks.items()
            if name.startswith('apps.') and not task.ignore_result
        }
        self.assertEqual(
            storing, BACKTEST_TASKS,
            'tasks other than the two task_health consumers will write results to the backend, '
            'reinstating the failing mark_as_done path for all of them',
        )

    def test_result_expiry_outlives_the_stale_run_threshold(self):
        """An expired result reads as PENDING, which is the signal orphan detection depends on."""
        self.assertGreater(settings.CELERY_RESULT_EXPIRES, settings.BACKTEST_STALE_TASK_MAX_AGE_SECONDS)


class RegistryCompletenessTests(TestCase):
    def test_every_routed_and_scheduled_task_is_registered(self):
        project_app.loader.import_default_modules()
        registered = set(project_app.tasks)

        routed = {
            name for name in settings.CELERY_TASK_ROUTES
            if isinstance(name, str) and name.startswith('apps.')
        }
        scheduled = {
            entry['task'] for entry in settings.CELERY_BEAT_SCHEDULE.values()
            if str(entry.get('task', '')).startswith('apps.')
        }
        missing = sorted((routed | scheduled) - registered)
        self.assertEqual(
            missing, [],
            'routed or scheduled tasks that no worker can resolve. autodiscover_tasks() only '
            'imports <app>.tasks, so anything declared elsewhere needs CELERY_IMPORTS.',
        )

    def test_the_etf_trainers_are_registered_without_a_command_import(self):
        """These two live outside tasks.py and used to appear only when a command imported them.

        That import order is why the generated celery.md counted 55 project tasks while
        `--only celery` counted 54: the commands sheet builder imports every command module,
        and two of them import the trainer at module level.
        """
        project_app.loader.import_default_modules()
        for name in ('apps.etf_rotation.tasks.train_etf_rotation_lightgbm',
                     'apps.etf_rotation.tasks.train_etf_rotation_lstm'):
            self.assertIn(name, project_app.tasks, f'{name} is routed but not registered')

    def test_env_example_documents_the_new_knobs(self):
        text = (Path(settings.BASE_DIR) / '.env.example').read_text(encoding='utf-8')
        for key in ('CELERY_REDIS_BACKEND_HEALTH_CHECK_INTERVAL', 'CELERY_REDIS_SOCKET_KEEPALIVE',
                    'CELERY_REDIS_RETRY_ON_TIMEOUT', 'CELERY_REDIS_SOCKET_CONNECT_TIMEOUT',
                    'CELERY_RESULT_EXPIRES'):
            self.assertIn(key, text)
