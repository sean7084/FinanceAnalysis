#!/usr/bin/env python
"""Watch for the WSL2 <-> NAS Postgres wedge and terminate the stuck backend.

The failure this exists for was observed twice on 2026-11-05. Three independent
sources have to agree before "hung" is a safe conclusion: the server backend sits in
``wait_event_type=Client, wait_event=ClientWrite``; the client is in
``poll_schedule_timeout`` at **zero CPU**; and ``ss -tnp`` shows the socket ``ESTAB``
with **both** ``Recv-Q`` and ``Send-Q`` at 0. Nothing times out, so it never recovers.
A killed client does not free its backend either -- orphans sat in ``ClientWrite``
1,068 s after the client died, because the server's ``tcp_keepalives_idle=600`` does
not fire on a backend actively blocked writing. Against ``max_connections=100`` that
is a slow leak, and the only remedy was a manual ``pg_terminate_backend``.

Client-side keepalives (``config/settings/base.py``) do **not** fix this. Both socket
queues are empty and ACKed, so TCP sees a healthy connection and probes are answered
by the peer's stack without reaching the blocked backend process. They recover the
adjacent dead-link class. Interrupting the wedge needs a server-side
``statement_timeout`` or an outside observer -- which is this.

**Not a Celery beat task.** django-celery-beat stores its schedule in the database,
and a wedged pool has no free worker to run the watcher: the thing that needs
rescuing would be the thing asked to do the rescuing.

**Classification is by signature, not by duration.** A legitimate
``validate_data_quality`` scan runs for the better part of an hour and must survive.
It is ``active`` with an IO or NULL wait event and it is making progress. The wedge is
``active`` with ``Client``/``ClientWrite`` and the *same* ``query_start`` across two
samples. Duration alone cannot tell those apart, so duration is only a floor on the
signature match. No local CPU-tick cross-check is needed once the server-side
signature is sampled twice; it would add a dependency without discriminating power.

Usage::

    python scripts/db_wedge_watch.py --once --dry-run   # classify, terminate nothing
    python scripts/db_wedge_watch.py --once             # one pass, terminate if enabled
    python scripts/db_wedge_watch.py                    # loop until interrupted

``scripts/_stack_probe.py pgstat`` imports ``ACTIVITY_SQL`` and ``classify_backend``
from here so the pre-launch check and the watcher cannot drift apart.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Only this project's backends. ``application_name`` is set in the DATABASES OPTIONS
#: so the filter is exact rather than a guess based on client address.
APPLICATION_NAME = 'finance_analysis'

#: Two samples one interval apart are required before a backend can be called wedged.
ACTIVITY_SQL = """
    SELECT pid,
           state,
           wait_event_type,
           wait_event,
           backend_type,
           application_name,
           query_start,
           xact_start,
           EXTRACT(EPOCH FROM (now() - query_start)) AS query_age,
           EXTRACT(EPOCH FROM (now() - xact_start))  AS xact_age,
           left(query, 120)                          AS query_prefix
    FROM pg_stat_activity
    WHERE backend_type = 'client backend'
      AND application_name = %s
    ORDER BY pid
"""

WEDGED = 'wedged'
IDLE_IN_TRANSACTION = 'idle_in_transaction'
HEALTHY = 'healthy'
UNDECIDED = 'undecided'
GONE = 'gone'


def classify_backend(previous, current, *, client_write_max_age=300,
                     idle_in_txn_max_age=1800):
    """Classify one backend from two samples. Pure, so the rules are testable.

    ``previous`` may be None (first sighting), which can never be enough to condemn a
    backend: the whole point is that the *same* query has been stuck in the *same*
    wait state across an interval.
    """
    if current is None:
        return GONE
    state = current.get('state')
    if state == 'idle in transaction':
        age = current.get('xact_age') or 0
        return IDLE_IN_TRANSACTION if age > idle_in_txn_max_age else HEALTHY
    if state != 'active':
        return HEALTHY
    if (current.get('wait_event_type') != 'Client'
            or current.get('wait_event') != 'ClientWrite'):
        # A long scan waiting on IO or on nothing is working, not wedged. This is the
        # branch that keeps an hour-long validate_data_quality run alive.
        return HEALTHY
    if previous is None or previous.get('pid') != current.get('pid'):
        return UNDECIDED
    if previous.get('query_start') != current.get('query_start'):
        return UNDECIDED
    age = current.get('query_age') or 0
    return WEDGED if age > client_write_max_age else UNDECIDED


def _connect(database_url):
    """A fresh connection per pass, with its own short statement timeout.

    Never reused across passes: a watcher that inherits a wedged connection is just
    another wedged client. The statement timeout bounds the watcher's own query so it
    cannot become the next thing that needs rescuing.
    """
    import psycopg2
    import psycopg2.extras

    return psycopg2.connect(
        database_url,
        connect_timeout=5,
        options='-c statement_timeout=10000 -c application_name=db_wedge_watch',
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def sample(database_url):
    connection = _connect(database_url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(ACTIVITY_SQL, [APPLICATION_NAME])
            return {row['pid']: dict(row) for row in cursor.fetchall()}
    finally:
        connection.close()


def terminate(database_url, pid):
    connection = _connect(database_url)
    try:
        with connection.cursor() as cursor:
            # Named explicitly: the connection uses RealDictCursor, so fetchone() returns a
            # dict and a positional index raises KeyError. That is how the terminate path
            # failed on its first live run -- the classifier was right and the action threw.
            cursor.execute('SELECT pg_terminate_backend(%s) AS terminated', [pid])
            return bool(cursor.fetchone()['terminated'])
    finally:
        connection.close()


def _database_url():
    url = os.environ.get('DATABASE_URL')
    if url:
        return url
    env_file = PROJECT_ROOT / '.env'
    if env_file.exists():
        import environ

        environ.Env.read_env(str(env_file))
    return os.environ.get('DATABASE_URL', 'postgres://localhost:5432')


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def run_pass(database_url, previous, *, client_write_max_age, idle_in_txn_max_age,
             do_terminate, out=sys.stdout):
    """One sample-and-classify pass. Returns (current_samples, findings, exit_status)."""
    current = sample(database_url)
    findings = []
    for pid, row in current.items():
        verdict = classify_backend(
            previous.get(pid) if previous else None, row,
            client_write_max_age=client_write_max_age,
            idle_in_txn_max_age=idle_in_txn_max_age,
        )
        if verdict in (WEDGED, IDLE_IN_TRANSACTION):
            findings.append((pid, verdict, row))

    for pid, verdict, row in findings:
        age = row.get('query_age') if verdict == WEDGED else row.get('xact_age')
        print(
            f'{verdict}: pid={pid} state={row.get("state")} '
            f'wait={row.get("wait_event_type")}/{row.get("wait_event")} '
            f'age={age}s query={row.get("query_prefix")!r}',
            file=out,
        )
        if do_terminate:
            try:
                killed = terminate(database_url, pid)
            except Exception as exc:
                print(f'  terminate failed for pid={pid}: {type(exc).__name__}: {exc}',
                      file=sys.stderr)
                continue
            print(f'  pg_terminate_backend({pid}) -> {killed}', file=out)
        else:
            print('  observation only; pass --terminate to act', file=out)

    if not findings:
        print(f'pgstat: clean ({len(current)} backend(s) for {APPLICATION_NAME})', file=out)
    return current, findings, 1 if findings else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--once', action='store_true',
                        help='Sample twice one interval apart, report, and exit.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Classify and report but never terminate.')
    parser.add_argument('--terminate', action='store_true', default=None,
                        help='Terminate matched backends. Overrides DB_WEDGE_TERMINATE.')
    parser.add_argument('--interval', type=int,
                        default=_env_int('DB_WEDGE_WATCH_INTERVAL_SECONDS', 15),
                        help='Seconds between samples (default: 15).')
    parser.add_argument('--client-write-max-age', type=int,
                        default=_env_int('DB_WEDGE_CLIENT_WRITE_MAX_AGE_SECONDS', 300))
    parser.add_argument('--idle-in-txn-max-age', type=int,
                        default=_env_int('DB_WEDGE_IDLE_IN_TXN_MAX_AGE_SECONDS', 1800))
    options = parser.parse_args(argv)

    do_terminate = (options.terminate if options.terminate is not None
                    else _env_bool('DB_WEDGE_TERMINATE', True)) and not options.dry_run
    database_url = _database_url()
    kwargs = {
        'client_write_max_age': options.client_write_max_age,
        'idle_in_txn_max_age': options.idle_in_txn_max_age,
        'do_terminate': do_terminate,
    }

    previous = {}
    while True:
        try:
            current, findings, status = run_pass(database_url, previous, **kwargs)
        except Exception as exc:
            # The watcher must not die because the link it is watching is broken.
            print(f'watch pass failed: {type(exc).__name__}: {exc}', file=sys.stderr)
            current, findings, status = {}, [], 1
        previous = current
        if options.once:
            # A single pass cannot condemn anything: classification needs two samples.
            # Take the second one so --once is a real check rather than a no-op.
            time.sleep(min(options.interval, 5) if not findings else 0)
            try:
                _, findings, status = run_pass(database_url, previous, **kwargs)
            except Exception as exc:
                print(f'second pass failed: {type(exc).__name__}: {exc}', file=sys.stderr)
                return 1
            return status
        time.sleep(options.interval)


if __name__ == '__main__':
    sys.exit(main())