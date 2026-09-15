from unittest.mock import patch

from django.db import connections
from django.db.utils import InterfaceError, OperationalError

from apps.backtest.tasks import clear_backtest_process_caches, run_backtest


def run_backtest_inline_to_completion(root_run_id):
    pending_run_ids = [int(root_run_id)]

    def _enqueue(run_id):
        pending_run_ids.append(int(run_id))

    with patch('apps.backtest.tasks.run_backtest.delay', side_effect=_enqueue):
        clear_backtest_process_caches()
        try:
            while pending_run_ids:
                current_run_id = pending_run_ids.pop(0)
                for attempt in range(2):
                    try:
                        run_backtest(current_run_id)
                        break
                    except (OperationalError, InterfaceError):
                        connections.close_all()
                        if attempt == 1:
                            raise
        finally:
            clear_backtest_process_caches()


def run_backtest_inline_chunk_once(root_run_id):
    def _noop_queue_backtest_run(_run):
        return None

    with patch('apps.backtest.tasks.queue_backtest_run', side_effect=_noop_queue_backtest_run):
        clear_backtest_process_caches()
        try:
            for attempt in range(2):
                try:
                    return run_backtest(int(root_run_id))
                except (OperationalError, InterfaceError):
                    connections.close_all()
                    if attempt == 1:
                        raise
        finally:
            clear_backtest_process_caches()
