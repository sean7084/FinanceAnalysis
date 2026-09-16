<!--
  GENERATED FILE - DO NOT EDIT BY HAND.
  Regenerate: python manage.py export_documentation_facts
  Source of truth: every env() read in config/settings/*.py, cross-checked against .env.example.
-->

# Environment Variable Reference

_Generated: 2026-09-16T15:25:01Z_

`.env` at the repository root is the only env file the native stack needs; `manage.py` sets `DJANGO_READ_DOT_ENV_FILE=True` when it exists. OS environment variables take precedence over `.env` values.

## Variables read by settings

| Variable | Type | Default | In `.env.example` | Read at |
| --- | --- | --- | --- | --- |
| `ALERTS_ENABLE_SMS` | `bool` | `False` | yes | base.py:447 |
| `BACKTEST_STALE_TASK_MAX_AGE_SECONDS` | `int` | `2400` | yes | base.py:234 |
| `CAPITAL_FLOW_DAILY_SYNC_LOOKBACK_DAYS` | `int` | `20` | yes | base.py:226 |
| `CELERY_BROKER_URL` | `str` | `'redis://localhost:6379/0'` | yes | base.py:190 |
| `DATABASE_URL` | `db` | _required_ | yes | base.py:131 |
| `DEFAULT_FROM_EMAIL` | `str` | `'noreply@financeanalysis.com'` | yes | base.py:364 |
| `DJANGO_ALLOWED_HOSTS` | `list` | `['localhost', '0.0.0.0', '127.0.0.1', '192.168.31.30']` | yes | local.py:9 |
| `DJANGO_DEBUG` | `bool` | `False` | yes | base.py:37 |
| `DJANGO_EMAIL_BACKEND` | `str` | `'django.core.mail.backends.console.EmailBackend'` | yes | local.py:17 |
| `DJANGO_READ_DOT_ENV_FILE` | `bool` | `False` | yes | base.py:25 |
| `DJANGO_SECRET_KEY` | `str` | `'django-insecure-change-this-in-production'` | yes | base.py:331, local.py:7 |
| `DJANGO_VITE_DEV_MODE` | `bool` | `DEBUG` | yes | base.py:434 |
| `DJANGO_VITE_DEV_SERVER_HOST` | `str` | `'localhost'` | yes | base.py:438 |
| `DJANGO_VITE_DEV_SERVER_PORT` | `int` | `5173` | yes | base.py:439 |
| `EMAIL_BACKEND` | `str` | `'django.core.mail.backends.console.EmailBackend'` | yes | base.py:360 |
| `EMAIL_HOST` | `str` | `'localhost'` | yes | base.py:408 |
| `EMAIL_HOST_PASSWORD` | `str` | `''` | yes | base.py:412 |
| `EMAIL_HOST_USER` | `str` | `''` | yes | base.py:411 |
| `EMAIL_PORT` | `str` | `587` | yes | base.py:409 |
| `EMAIL_USE_TLS` | `bool` | `True` | yes | base.py:410 |
| `FRONTEND_URL` | `str` | `'http://localhost:8000'` | yes | base.py:422 |
| `HISTORICAL_DATA_FLOOR` | `str` | `'2010-01-01'` | yes | base.py:218 |
| `MACRO_SYNC_FALLBACK_PROVIDER` | `str` | `'akshare'` | yes | base.py:220 |
| `MACRO_SYNC_PRIMARY_PROVIDER` | `str` | `'tushare'` | yes | base.py:219 |
| `MACRO_SYNC_PROVIDER_SLEEP_SECONDS` | `float` | `0.2` | yes | base.py:221 |
| `MACRO_YIELD_BACKFILL_CALL_SLEEP_SECONDS` | `float` | `31.0` | yes | base.py:225 |
| `MACRO_YIELD_BACKFILL_MAX_RETRIES` | `int` | `3` | yes | base.py:223 |
| `MACRO_YIELD_BACKFILL_RETRY_SLEEP_SECONDS` | `float` | `65.0` | yes | base.py:224 |
| `MACRO_YIELD_BACKFILL_WINDOW_MONTHS` | `int` | `36` | yes | base.py:222 |
| `NEWS_BACKFILL_CHUNK_DAYS` | `int` | `31` | yes | base.py:229 |
| `NEWS_BACKFILL_ENABLED` | `bool` | `True` | yes | base.py:227 |
| `NEWS_BACKFILL_FLOOR` | `str` | `'2021-04-15 00:00:00'` | yes | base.py:230 |
| `NEWS_BACKFILL_LIMIT_PER_PROVIDER` | `int` | `0` | yes | base.py:231 |
| `NEWS_BACKFILL_PROVIDER` | `str` | `'tushare_major'` | yes | base.py:228 |
| `REDIS_URL` | `str` | `'redis://localhost:6379/1'` | yes | base.py:345, base.py:353 |
| `SMS_WEBHOOK_URL` | `str` | `''` | yes | base.py:448 |
| `TUSHARE_TOKEN` | `str` | `None` | yes | base.py:30 |

## Keys in `.env.example` that settings never read

These are not Django settings. A key consumed by a launcher under `scripts/` or a Compose entrypoint under `compose/` still works; a key with no consumer anywhere has no effect at all and should be wired up or removed.

| Key | Status |
| --- | --- |
| `CELERY_RESULT_BACKEND` | read by `scripts/_native_env.ps1`, `scripts/_native_env.sh` |
| `CELERY_WORKER_QUEUES` | read by `scripts/run_celery_worker.ps1`, `scripts/run_celery_worker.sh`, `compose/local/django/start-celeryworker` |
| `SMOKE_PASSWORD` | read by `scripts/smoke_api_check.sh` |
| `SMOKE_USERNAME` | read by `scripts/smoke_api_check.sh` |
