#!/usr/bin/env python
"""Shared service and settings probe for ``scripts/verify_local_stack.{sh,ps1}``.

Both verifiers used to embed this logic as Python source inside a shell string -- a
bash heredoc in the ``.sh`` and two PowerShell here-strings in the ``.ps1``. That
produced three separate copies of the URL redaction helper, and they drifted:

* the ``.sh`` settings summary printed ``CELERY_BROKER_URL`` and the cache
  ``LOCATION`` with **no redaction at all**, so a correct ``.env`` meant the real
  Redis password went to stdout -- which is what gets pasted into bug reports and
  captured in CI logs;
* the ``.ps1`` settings probe used a weaker ``redact_url()`` that omitted the
  malformed-path guard, so it leaked credentials whenever a mistyped URL pushed
  them into ``path`` (a single ``:`` typed as ``/`` does it).

Consolidating removes the class of bug rather than the individual instance: there is
now one ``redact()`` and both verifiers call it.

Usage::

    python scripts/_stack_probe.py services   # PostgreSQL + Redis, exit 1 on failure
    python scripts/_stack_probe.py settings   # redacted settings summary
    python scripts/_stack_probe.py pgstat     # wedged-backend check, exit 1 if any
    python scripts/_stack_probe.py pgstat --terminate-orphans
    python scripts/_stack_probe.py all        # all three

The services probe deliberately avoids Django, so it still reports usefully when
settings are broken or the database is unreachable. The settings probe bootstraps
Django itself, which a bare ``python script.py`` invocation does not do for it: the
project root has to be on ``sys.path`` (``sys.path[0]`` is ``scripts/``, not the
root) and ``DJANGO_READ_DOT_ENV_FILE`` has to be set, because only ``manage.py``
sets it otherwise.
"""
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def redact(url):
    """Return a URL safe to print. Never emit credentials, even on failure.

    Every credential-bearing value this script prints must pass through here. Adding
    a new print statement that formats a URL directly reintroduces the leak this
    module exists to prevent.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return '<unparsable>'

    host = parsed.hostname or '<unparsable>'
    try:
        port = f':{parsed.port}' if parsed.port else ''
    except ValueError:
        # urlparse raises on a non-numeric port, which a malformed URL can produce.
        port = ':<invalid>'

    # A malformed URL can push credentials into the path. Refuse to print a path
    # that could carry one rather than trying to decide whether it does.
    path = parsed.path or ''
    if '@' in path or ':' in path:
        path = '/<redacted>'

    return f'{parsed.scheme or "redis"}://{host}{port}{path}'


def probe_services():
    """Ping PostgreSQL and Redis through their drivers. Return 1 if either fails.

    Uses the Python drivers rather than ``pg_isready`` / ``redis-cli``: neither
    binary ships with Windows, so shelling out to them silently degraded the
    verifier into a no-op that still exited 0. That is how a wrong Redis credential
    survived undetected while the verifier reported success.
    """
    import psycopg2
    import redis

    status = 0

    database_url = os.environ.get('DATABASE_URL', 'postgres://localhost:5432')
    try:
        connection = psycopg2.connect(database_url, connect_timeout=5)
    except Exception as exc:
        print(f'PostgreSQL check failed for {redact(database_url)}: {exc}', file=sys.stderr)
        status = 1
    else:
        connection.close()
        print(f'PostgreSQL: ready ({redact(database_url)})')

    for label, url in (
        ('Redis broker', os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')),
        ('Redis cache/channels', os.environ.get('REDIS_URL', 'redis://localhost:6379/1')),
    ):
        try:
            client = redis.Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
            client.ping()
        except Exception as exc:
            print(f'{label} check failed for {redact(url)}: {exc}', file=sys.stderr)
            status = 1
        else:
            print(f'{label}: ready ({redact(url)})')

    return status


def print_settings_summary():
    """Print the resolved connection settings, with every URL redacted."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')
    os.environ.setdefault('DJANGO_READ_DOT_ENV_FILE', 'True')

    import django

    django.setup()

    from django.conf import settings

    database = settings.DATABASES['default']
    print(f"DATABASE_NAME={database['NAME']}")
    print(f"DATABASE_HOST={database['HOST']}")
    print(f"DATABASE_PORT={database['PORT']}")
    print(f"CELERY_BROKER_URL={redact(settings.CELERY_BROKER_URL)}")
    print(f"REDIS_URL={redact(settings.CACHES['default']['LOCATION'])}")
    return 0


def probe_pgstat(terminate_orphans=False):
    """Report wedged backends before a launch, sharing the watcher's own rules.

    A killed client does not free its backend: orphans were seen in ``ClientWrite``
    1,068 s after the client died, so against ``max_connections=100`` a restart can
    inherit a slow leak. This is the pre-launch emptiness check that used to be a
    manual ``pg_stat_activity`` query plus ``pg_terminate_backend``.

    The SQL and the classifier are imported from ``db_wedge_watch`` rather than
    copied. That module's docstring records why copying is the bug class here: three
    copies of ``redact()`` once existed and two were defective differently.

    No DSN is printed. The watcher's output carries pid, state, wait event, age and a
    truncated query -- none of which is a credential -- and any URL would have to go
    through ``redact()`` above.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import db_wedge_watch

    url = db_wedge_watch._database_url()
    try:
        first = db_wedge_watch.sample(url)
        # Classification needs two samples one interval apart; a single sighting can
        # never condemn a backend. Keep the wait short -- this runs before a launch.
        time.sleep(2)
        _, findings, status = db_wedge_watch.run_pass(
            url, first,
            client_write_max_age=db_wedge_watch._env_int(
                'DB_WEDGE_CLIENT_WRITE_MAX_AGE_SECONDS', 300),
            idle_in_txn_max_age=db_wedge_watch._env_int(
                'DB_WEDGE_IDLE_IN_TXN_MAX_AGE_SECONDS', 1800),
            do_terminate=terminate_orphans,
        )
    except Exception as exc:
        print(f'pgstat check failed for {redact(url)}: {type(exc).__name__}: {exc}',
              file=sys.stderr)
        return 1
    if findings and not terminate_orphans:
        print('pgstat: wedged backends present; re-run with --terminate-orphans or '
              'start scripts/db_wedge_watch.py', file=sys.stderr)
    return status


def main(argv):
    command = (argv[1] if len(argv) > 1 else 'all').lower()
    terminate_orphans = '--terminate-orphans' in argv[2:]

    if command == 'services':
        return probe_services()
    if command == 'settings':
        return print_settings_summary()
    if command == 'pgstat':
        return probe_pgstat(terminate_orphans=terminate_orphans)
    if command == 'all':
        status = probe_services()
        # Report every probe even if an earlier one failed, then propagate the failure.
        status = print_settings_summary() or status
        return probe_pgstat(terminate_orphans=terminate_orphans) or status

    print(f'unknown command: {command} (expected services|settings|pgstat|all)',
          file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
