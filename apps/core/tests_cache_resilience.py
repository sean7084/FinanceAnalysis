"""The Django cache must survive the idle WSL2<->NAS link, like the broker already does.

A0 classified the active post-deploy Errno-104 task failures (catch_up_missed_runs,
prewarm_dashboard_stocks_cache) onto the Django cache: ``CACHES['default']`` had no
OPTIONS, so its redis client never health-checked an idle connection and the first
use after the link dropped it raised ConnectionResetError. The broker has zero such
failures precisely because CELERY_BROKER_TRANSPORT_OPTIONS carries these knobs.

Scope honesty, mirroring tests_celery_backend: these tests prove the resilience knobs
**reach the cache's connection pool**. They do not prove a half-open socket is
recovered on the NAS link -- that needs live observation (Phase A2), because a unit
test cannot reproduce a link that drops traffic without closing the connection. What
is asserted exactly is that the knobs are present in the connection kwargs the pool
will hand redis-py, and -- via the negative control -- that they come from this
project's CACHES OPTIONS rather than a redis-py default.

Why read base.CACHES: config.settings.test replaces the *effective* CACHES with
LocMemCache so tests never touch the shared Redis. The production config lives in
config.settings.base (local/production do not override it), so the positive case
builds a RedisCache from that dict. No connection is opened: ConnectionPool.from_url
only stores connection_kwargs.
"""

from django.core.cache.backends.redis import RedisCache
from django.test import SimpleTestCase

from config.settings import base

EXPECTED_CACHE_KNOBS = {
    'health_check_interval': 25,
    'socket_keepalive': True,
    'retry_on_timeout': True,
    'socket_connect_timeout': 10,
    'socket_timeout': 30,
}


def _pool_kwargs(params):
    cache = RedisCache(params['LOCATION'], params)
    pool = cache._cache._get_connection_pool(write=True)
    return pool.connection_kwargs


class CacheResilienceConnparamsTests(SimpleTestCase):
    def test_the_production_cache_carries_every_resilience_knob(self):
        kwargs = _pool_kwargs(base.CACHES['default'])
        for key, expected in EXPECTED_CACHE_KNOBS.items():
            # assertTrue, not assertIn: assertIn prints the whole container on
            # failure, and connection_kwargs holds the Redis password. Never
            # echo the dict into a test log.
            self.assertTrue(
                key in kwargs,
                f'{key} never reached the cache connection pool: CACHES OPTIONS is '
                'missing it, so an idle NAS connection is used without a health check '
                'and the first read raises Errno 104.',
            )
            self.assertEqual(kwargs[key], expected, f'{key} reached the pool with the wrong value')

    def test_the_knobs_come_from_options_not_from_redis_defaults(self):
        """Negative control: with OPTIONS removed the pool must show no resilience.

        redis-py defaults health_check_interval to 0 (off) and socket_keepalive to
        False, so without this control the positive test could be satisfied by a
        library default that is present either way.
        """
        params = dict(base.CACHES['default'])
        params.pop('OPTIONS', None)
        kwargs = _pool_kwargs(params)
        self.assertFalse(kwargs.get('health_check_interval'),
                         'health check must be off when OPTIONS is absent')
        self.assertFalse(kwargs.get('socket_keepalive'),
                         'keepalive must be off when OPTIONS is absent')
        self.assertFalse(kwargs.get('retry_on_timeout'),
                         'retry must be off when OPTIONS is absent')
