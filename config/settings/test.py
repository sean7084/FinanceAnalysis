from .local import *  # noqa: F401,F403

# Test settings: inherit local.py and replace everything that would reach a
# shared service.
#
# Before this module existed, `manage.py test` ran against
# config.settings.local, whose cache is the development Redis. Every test that
# touched the cache -- and the dashboard/backtest views do it on almost every
# request -- wrote real keys into the same database the running server uses. Two
# consequences: a test run could hand the live dashboard a cache entry built
# from fixture rows, and a cache-related test could pass or fail depending on
# what the previous run left behind.
#
# The database is unchanged on purpose. Tests still run against a real
# PostgreSQL because the queries under test depend on vendor-specific behaviour
# (distinct-on ordering, window functions, JSONB containment) that SQLite gets
# away without.

# In-process cache, discarded with the test runner.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "finance-analysis-tests",
    }
}

# Channels' in-memory layer keeps websocket tests off the shared broker.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}
