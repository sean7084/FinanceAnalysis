#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path

# A test run must not reach the shared development cache. config/settings/test.py
# puts the cache in-process precisely so that tests cannot hand the running server
# a cache entry built from fixture rows, or pass or fail depending on what the
# previous run left behind. Everything else that resolves a live backend is a
# mistake waiting to happen, so manage.py refuses it rather than letting it run
# green and corrupt.
TEST_SAFE_CACHE_BACKENDS = frozenset({
    "django.core.cache.backends.locmem.LocMemCache",
    "django.core.cache.backends.dummy.DummyCache",
})

LIVE_CACHE_REFUSAL = """\
Refusing to run tests against a live cache.

Resolved default cache backend: {backend}
(settings module: {settings_module}). Tests would read and write the shared
development Redis used by the running server: they pollute the live cache and
can pass or fail depending on what a previous run left behind (see
config/settings/test.py).

Run instead:

    python manage.py test --settings=config.settings.test

or: export DJANGO_SETTINGS_MODULE=config.settings.test

Deliberate exception (you will hit the live cache):

    ALLOW_LIVE_CACHE_TESTS=1 python manage.py test ...
"""


def _settings_module_from_argv(argv, default):
    """The settings module Django will use, honouring an explicit ``--settings``.

    Django does not apply ``--settings`` until ``BaseCommand.execute`` runs, which is
    after this guard, so reading ``settings.CACHES`` without resolving it first would
    refuse the very invocation the docs recommend. Both argparse forms are accepted.
    """
    for index, token in enumerate(argv):
        if token.startswith("--settings="):
            return token.split("=", 1)[1]
        if token == "--settings" and index + 1 < len(argv):
            return argv[index + 1]
    return default


def _invokes_test(argv):
    """Whether this command line runs ``manage.py test``.

    Django resolves management commands by exact name, so there is no abbreviation
    to follow here. Help is left alone: ``manage.py test --help`` should still print
    usage, not a refusal.
    """
    if "--help" in argv or "-h" in argv:
        return False
    return (argv[1] if len(argv) > 1 else "") == "test"


def _reject_live_cache_tests():
    """Refuse a test run whose resolved default cache is a shared live backend.

    The check is on the backend rather than the settings module name, so it also
    catches ``--settings=config.settings.production`` and any future module that
    forgets to isolate the cache. Reading ``settings.CACHES`` only forces lazy
    settings configuration; ``execute_from_command_line`` still does the single
    real ``django.setup()`` afterwards.
    """
    from django.conf import settings

    default_cache = settings.CACHES.get("default", {})
    backend = default_cache.get("BACKEND", "") if isinstance(default_cache, dict) else ""
    if not backend or backend in TEST_SAFE_CACHE_BACKENDS:
        return

    if os.environ.get("ALLOW_LIVE_CACHE_TESTS") == "1":
        sys.stderr.write(
            f"WARNING: ALLOW_LIVE_CACHE_TESTS=1 -- this run will read and write the "
            f"live cache ({backend}).\n"
        )
        return

    sys.stderr.write(LIVE_CACHE_REFUSAL.format(
        backend=backend,
        settings_module=os.environ.get("DJANGO_SETTINGS_MODULE", "unset"),
    ))
    sys.exit(2)


def main():
    """Run administrative tasks."""
    base_dir = Path(__file__).resolve().parent
    if (base_dir / ".env").exists():
        os.environ.setdefault("DJANGO_READ_DOT_ENV_FILE", "True")
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    if _invokes_test(sys.argv):
        os.environ['DJANGO_SETTINGS_MODULE'] = _settings_module_from_argv(
            sys.argv, os.environ['DJANGO_SETTINGS_MODULE'])
        _reject_live_cache_tests()
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
