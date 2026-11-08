"""The database connection options, and proof they reach the server.

The NAS-hosted Postgres is reached over a WSL2 link. Before this, DATABASES was
two lines -- env.db() plus ATOMIC_REQUESTS -- with no OPTIONS at all: no
connect_timeout, no keepalives, no statement_timeout anywhere in the project. A
silently dead link hung forever, and the only recovery was a manual
pg_terminate_backend.

The application_name assertion is load-bearing rather than cosmetic: the wedge
watchdog selects this project's own backends out of pg_stat_activity by that name,
so if it does not reach the server the watchdog filters on nothing.
"""

from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TestCase


class DatabaseOptionsTests(SimpleTestCase):
    def test_libpq_options_are_present_with_the_documented_defaults(self):
        options = settings.DATABASES['default']['OPTIONS']
        self.assertEqual(options['connect_timeout'], 10)
        self.assertEqual(options['keepalives'], 1)
        self.assertEqual(options['keepalives_idle'], 30)
        self.assertEqual(options['keepalives_interval'], 10)
        self.assertEqual(options['keepalives_count'], 3)

    def test_the_application_name_is_set_for_the_watchdog_to_filter_on(self):
        self.assertEqual(
            settings.DATABASES['default']['OPTIONS']['application_name'],
            'finance_analysis',
        )

    def test_connections_are_not_persistent_by_default(self):
        """A wedged persistent connection would be inherited by every later task."""
        self.assertEqual(settings.DATABASES['default']['CONN_MAX_AGE'], 0)

    def test_every_option_is_declared_in_the_env_template(self):
        from pathlib import Path

        template = (Path(settings.BASE_DIR) / '.env.example').read_text(encoding='utf-8')
        for key in ('DB_CONN_MAX_AGE', 'DB_CONNECT_TIMEOUT_SECONDS',
                    'DB_TCP_KEEPALIVE_IDLE_SECONDS', 'DB_TCP_KEEPALIVE_INTERVAL_SECONDS',
                    'DB_TCP_KEEPALIVE_COUNT'):
            self.assertIn(f'\n{key}=', template, f'{key} is read but undiscoverable')


class DatabaseOptionsReachTheServerTests(TestCase):
    def test_the_server_sees_our_application_name(self):
        if connection.vendor != 'postgresql':
            self.skipTest('libpq options only apply to PostgreSQL')
        with connection.cursor() as cursor:
            cursor.execute('SHOW application_name')
            self.assertEqual(cursor.fetchone()[0], 'finance_analysis')

    def test_our_own_backend_is_selectable_by_name(self):
        """Exactly the filter the watchdog uses, run against a live backend."""
        if connection.vendor != 'postgresql':
            self.skipTest('libpq options only apply to PostgreSQL')
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid()')
            own_pid = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT pid, application_name, backend_type
                FROM pg_stat_activity
                WHERE application_name = %s AND pid = %s
                """,
                ['finance_analysis', own_pid],
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row, 'the watchdog would filter on nothing')
        self.assertEqual(row[1], 'finance_analysis')
        self.assertEqual(row[2], 'client backend')