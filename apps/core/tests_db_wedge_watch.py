"""The wedge watchdog's classification rules, and the probe's redaction.

The load-bearing test is ``test_a_long_working_scan_is_never_wedged``: an hour-long
``validate_data_quality`` run is ``active`` with the same ``query_start`` across both
samples, exactly like the wedge. What separates them is the wait event -- IO or NULL
versus Client/ClientWrite. Classifying by duration alone would kill the release gate.

The classifier is a pure function so these rules are pinned without a database, and
``scripts/_stack_probe.py`` imports the same function rather than keeping a second
copy: three copies of ``redact()`` once existed and two were defective differently.
"""

import importlib.util
import io
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.test import SimpleTestCase, TestCase

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    path = REPO_ROOT / 'scripts' / name
    spec = importlib.util.spec_from_file_location(name.replace('.py', ''), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watch = _load('db_wedge_watch.py')

QUERY_START = datetime(2026, 11, 8, 1, 0, tzinfo=dt_timezone.utc)


def _backend(**overrides):
    row = {
        'pid': 4242,
        'state': 'active',
        'wait_event_type': 'Client',
        'wait_event': 'ClientWrite',
        'backend_type': 'client backend',
        'application_name': 'finance_analysis',
        'query_start': QUERY_START,
        'xact_start': QUERY_START,
        'query_age': 900.0,
        'xact_age': 900.0,
        'query_prefix': 'FETCH FORWARD 50000 FROM "_django_curs_12349"',
    }
    row.update(overrides)
    return row


class ClassifyBackendTests(SimpleTestCase):
    def test_the_observed_signature_is_wedged(self):
        previous, current = _backend(), _backend()
        self.assertEqual(
            watch.classify_backend(previous, current, client_write_max_age=300),
            watch.WEDGED,
        )

    def test_a_long_working_scan_is_never_wedged(self):
        """The release gate runs for an hour with the same query_start. It must live."""
        for wait_type, wait in (('IO', 'DataFileRead'), (None, None), ('Timeout', 'PgSleep')):
            with self.subTest(wait=(wait_type, wait)):
                row = _backend(wait_event_type=wait_type, wait_event=wait, query_age=3600.0)
                self.assertEqual(
                    watch.classify_backend(row, row, client_write_max_age=300),
                    watch.HEALTHY,
                )

    def test_a_first_sighting_cannot_condemn(self):
        self.assertEqual(
            watch.classify_backend(None, _backend(), client_write_max_age=300),
            watch.UNDECIDED,
        )

    def test_a_query_that_advanced_is_not_wedged(self):
        previous = _backend()
        current = _backend(query_start=datetime(2026, 11, 8, 2, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(
            watch.classify_backend(previous, current, client_write_max_age=300),
            watch.UNDECIDED,
        )

    def test_the_age_is_a_floor_on_the_signature_not_the_test(self):
        row = _backend(query_age=120.0)
        self.assertEqual(
            watch.classify_backend(row, row, client_write_max_age=300),
            watch.UNDECIDED,
        )
        self.assertEqual(
            watch.classify_backend(row, row, client_write_max_age=60),
            watch.WEDGED,
        )

    def test_a_different_pid_is_not_the_same_backend(self):
        self.assertEqual(
            watch.classify_backend(_backend(pid=1), _backend(pid=2), client_write_max_age=300),
            watch.UNDECIDED,
        )

    def test_idle_in_transaction_is_flagged_on_its_own_age(self):
        row = _backend(state='idle in transaction', wait_event_type=None,
                       wait_event=None, xact_age=3600.0)
        self.assertEqual(
            watch.classify_backend(None, row, idle_in_txn_max_age=1800),
            watch.IDLE_IN_TRANSACTION,
        )
        self.assertEqual(
            watch.classify_backend(None, dict(row, xact_age=60.0), idle_in_txn_max_age=1800),
            watch.HEALTHY,
        )

    def test_an_idle_or_missing_backend_is_not_a_finding(self):
        self.assertEqual(
            watch.classify_backend(None, _backend(state='idle'), client_write_max_age=300),
            watch.HEALTHY,
        )
        self.assertEqual(watch.classify_backend(_backend(), None), watch.GONE)

    def test_the_activity_query_filters_on_our_application_name(self):
        self.assertIn('application_name = %s', watch.ACTIVITY_SQL)
        self.assertIn("backend_type = 'client backend'", watch.ACTIVITY_SQL)
        self.assertEqual(watch.APPLICATION_NAME, 'finance_analysis')


class RunPassTests(SimpleTestCase):
    def test_a_clean_pass_reports_and_exits_zero(self):
        out = io.StringIO()
        healthy = _backend(wait_event_type='IO', wait_event='DataFileRead')
        original = watch.sample
        try:
            watch.sample = lambda url: {4242: healthy}
            current, findings, status = watch.run_pass(
                'postgres://unused', {}, client_write_max_age=300,
                idle_in_txn_max_age=1800, do_terminate=False, out=out)
        finally:
            watch.sample = original
        self.assertEqual(findings, [])
        self.assertEqual(status, 0)
        self.assertIn('pgstat: clean', out.getvalue())
        self.assertEqual(current, {4242: healthy})

    def test_a_wedged_pass_reports_and_never_prints_a_dsn(self):
        out = io.StringIO()
        original_sample, original_terminate = watch.sample, watch.terminate
        terminated = []
        try:
            watch.sample = lambda url: {4242: _backend()}
            watch.terminate = lambda url, pid: terminated.append(pid) or True
            _, findings, status = watch.run_pass(
                'postgres://secretuser:secretpass@nas:5432/db', {4242: _backend()},
                client_write_max_age=300, idle_in_txn_max_age=1800,
                do_terminate=True, out=out)
        finally:
            watch.sample, watch.terminate = original_sample, original_terminate
        self.assertEqual(status, 1)
        self.assertEqual(len(findings), 1)
        self.assertEqual(terminated, [4242])
        text = out.getvalue()
        self.assertIn('wedged: pid=4242', text)
        self.assertNotIn('secretpass', text)
        self.assertNotIn('secretuser', text)

    def test_observation_only_does_not_terminate(self):
        out = io.StringIO()
        original_sample, original_terminate = watch.sample, watch.terminate
        terminated = []
        try:
            watch.sample = lambda url: {4242: _backend()}
            watch.terminate = lambda url, pid: terminated.append(pid) or True
            watch.run_pass('postgres://unused', {4242: _backend()},
                           client_write_max_age=300, idle_in_txn_max_age=1800,
                           do_terminate=False, out=out)
        finally:
            watch.sample, watch.terminate = original_sample, original_terminate
        self.assertEqual(terminated, [])
        self.assertIn('observation only', out.getvalue())


class StackProbePgstatTests(SimpleTestCase):
    def test_the_probe_shares_the_watchers_sql_and_classifier(self):
        """No second copy: that is how three redact() helpers drifted apart."""
        probe = _load('_stack_probe.py')
        source = (REPO_ROOT / 'scripts' / '_stack_probe.py').read_text(encoding='utf-8')
        self.assertIn('import db_wedge_watch', source)
        self.assertIn('db_wedge_watch.run_pass', source)
        self.assertNotIn('SELECT pid', source, 'the SQL must not be copied into the probe')
        self.assertTrue(hasattr(probe, 'probe_pgstat'))
        self.assertTrue(hasattr(probe, 'redact'))

    def test_the_probe_dispatches_pgstat_and_all(self):
        probe = _load('_stack_probe.py')
        self.assertEqual(probe.main(['_stack_probe.py', 'nonsense']), 2)

    def test_redact_still_strips_credentials_including_a_malformed_path(self):
        probe = _load('_stack_probe.py')
        self.assertNotIn('pw', probe.redact('postgres://user:pw@host:5432/db'))
        # A mistyped URL can push credentials into the path component; the guard must
        # refuse the whole path rather than try to decide whether it carries one.
        self.assertNotIn('pw', probe.redact('redis://user:pw@host/6379'))
        self.assertNotIn('pw', probe.redact('redis://host/user:pw@6379'))
        # Not a URL at all: must not raise, and must not invent credentials.
        self.assertIsInstance(probe.redact('not a url at all'), str)


class ShortStatementTimeoutTests(TestCase):
    def test_the_timeout_is_set_inside_and_reverted_outside(self):
        from django.db import connection

        from apps.core.db_guard import short_statement_timeout

        if connection.vendor != 'postgresql':
            self.skipTest('SET LOCAL statement_timeout is PostgreSQL-specific')

        def show():
            with connection.cursor() as cursor:
                cursor.execute('SHOW statement_timeout')
                return cursor.fetchone()[0]

        before = show()
        with short_statement_timeout(1234):
            self.assertEqual(show(), '1234ms')
        self.assertEqual(show(), before)

    def test_a_slow_statement_is_cancelled_rather_than_wedging(self):
        from django.db import connection

        from apps.core.db_guard import short_statement_timeout

        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL-specific')

        def show():
            with connection.cursor() as cursor:
                cursor.execute('SHOW statement_timeout')
                return cursor.fetchone()[0]

        with self.assertRaises(Exception):
            with short_statement_timeout(50):
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_sleep(2)')
        # The connection is usable afterwards, and the cancelled statement's timeout
        # did not survive it. TestCase wraps us in an outer atomic, so in_atomic_block
        # is legitimately still True here -- what matters is the setting.
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            self.assertEqual(cursor.fetchone()[0], 1)
        self.assertNotEqual(show(), '50ms')