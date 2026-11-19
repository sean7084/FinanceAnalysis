"""Run a measurement inside a transaction and roll it back.

The decision-day A/B arm replays one session through the backtest engine, which writes
``BacktestRun`` rows and backfills trade decisions. The pre-registration for this campaign
requires that arm to run "inside a rolled-back transaction", so the measurement leaves the
database exactly as it found it.

``run_core_backtest_matrix`` is compatible with this: it never calls ``transaction.commit()``
and does not touch autocommit, ``--execute-inline`` runs in-process, and the engine's own
``transaction.atomic()`` blocks are nested savepoints. Raising inside the outer block
therefore discards everything written.

Files are NOT transactional: an ``--output-dir`` export survives the rollback, which is what
makes the measurement observable at all.
"""
from __future__ import annotations

from django.db import transaction


class _Rollback(Exception):
    """Sentinel used to unwind the transaction without an error path."""


def run_in_rolled_back_transaction(operation):
    """Call ``operation()`` inside a transaction, then roll the transaction back.

    Returns whatever ``operation`` returned. Any row written during the call is discarded;
    anything the operation returned (or wrote to disk) survives, which is how the caller
    reads its measurement.
    """
    result = None
    try:
        with transaction.atomic():
            result = operation()
            raise _Rollback()
    except _Rollback:
        pass
    return result