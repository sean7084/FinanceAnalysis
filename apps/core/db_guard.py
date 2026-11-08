"""Per-statement timeouts for interactive queries over very large tables.

The wedge observed on 2026-11-05 was triggered by a ``COUNT(*)`` over the 174M-row
``analytics_technicalindicator`` running concurrently with a large result-set fetch.
A global ``statement_timeout`` is the wrong instrument: the feature-matrix rebuild,
``validate_data_quality`` and the ETF trainers legitimately run for many minutes, and
a blanket default would kill them. The interactive path is the one that must not be
allowed to sit on a full scan, so the timeout belongs on that statement alone.

``SET LOCAL`` is scoped to the surrounding **transaction**, not to a savepoint. Under
``ATOMIC_REQUESTS`` (and inside Django's ``TestCase``) the ``atomic()`` below is only a
savepoint, and releasing a savepoint does *not* undo ``SET LOCAL`` -- the timeout would
leak into every later query in the same request. So the previous value is captured and
restored explicitly on the success path. On the error path the atomic block rolls back,
and a subtransaction abort *does* undo ``SET LOCAL``, so no restore is needed there.

The explicit ``atomic()`` also makes the helper safe to call from a worker or a command,
where it starts a real transaction and the restore is merely redundant.
"""
from contextlib import contextmanager

from django.db import connection, transaction

#: Long enough for an indexed dashboard aggregate, short enough that a sequential
#: scan over a nine-figure table fails instead of wedging the link.
DEFAULT_SHORT_STATEMENT_TIMEOUT_MS = 30_000


@contextmanager
def short_statement_timeout(milliseconds=DEFAULT_SHORT_STATEMENT_TIMEOUT_MS):
    """Bound the statements run inside this block, and nothing outside it."""
    if connection.vendor != 'postgresql':
        yield
        return
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SHOW statement_timeout')
            previous = cursor.fetchone()[0]
            # SET does not accept a bind parameter. The value is an int from our own
            # constant or caller, never user text.
            cursor.execute(f'SET LOCAL statement_timeout = {int(milliseconds)}')
        try:
            yield
        except BaseException:
            raise
        else:
            with connection.cursor() as cursor:
                cursor.execute('SET LOCAL statement_timeout = %s', [previous])