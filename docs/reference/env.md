<!--
  GENERATED FILE - DO NOT EDIT BY HAND.
  Regenerate: python manage.py export_documentation_facts
  Source of truth: every env() read in config/settings/*.py, cross-checked against .env.example.
-->

# Environment Variable Reference

_Generated: 2026-09-07T15:17:02Z_

`.env` at the repository root is the only env file the native stack needs; `manage.py` sets `DJANGO_READ_DOT_ENV_FILE=True` when it exists. OS environment variables take precedence over `.env` values.

## Variables read by settings

| Variable | Type | Default | In `.env.example` | Read at |
| --- | --- | --- | --- | --- |
| `ALERTS_ENABLE_SMS` | `bool` | `False` | **no** | base.py:401 |
| `CAPITAL_FLOW_DAILY_SYNC_LOOKBACK_DAYS` | `int` | `20` | **no** | base.py:212 |
| `CELERY_BROKER_URL` | `str` | `'redis://localhost:6379/0'` | yes | base.py:176 |
| `DATABASE_URL` | `db` | _required_ | yes | base.py:124 |
| `DEFAULT_FROM_EMAIL` | `str` | `'noreply@financeanalysis.com'` | **no** | base.py:347 |
| `DJANGO_ALLOWED_HOSTS` | `list` | `['localhost', '0.0.0.0', '127.0.0.1', '192.168.31.30']` | yes | local.py:9 |
| `DJANGO_DEBUG` | `bool` | `False` | yes | base.py:37 |
| `DJANGO_EMAIL_BACKEND` | `str` | `'django.core.mail.backends.console.EmailBackend'` | **no** | local.py:17 |
| `DJANGO_READ_DOT_ENV_FILE` | `bool` | `False` | yes | base.py:25 |
| `DJANGO_SECRET_KEY` | `str` | `'django-insecure-change-this-in-production'` | yes | base.py:314, local.py:7 |
| `EMAIL_BACKEND` | `str` | `'django.core.mail.backends.console.EmailBackend'` | **no** | base.py:343 |
| `EMAIL_HOST` | `str` | `'localhost'` | **no** | base.py:389 |
| `EMAIL_HOST_PASSWORD` | `str` | `''` | **no** | base.py:393 |
| `EMAIL_HOST_USER` | `str` | `''` | **no** | base.py:392 |
| `EMAIL_PORT` | `str` | `587` | **no** | base.py:390 |
| `EMAIL_USE_TLS` | `bool` | `True` | **no** | base.py:391 |
| `FRONTEND_URL` | `str` | `'http://localhost:3000'` | **no** | base.py:397 |
| `HISTORICAL_DATA_FLOOR` | `str` | `'2010-01-01'` | **no** | base.py:204 |
| `MACRO_SYNC_FALLBACK_PROVIDER` | `str` | `'akshare'` | **no** | base.py:206 |
| `MACRO_SYNC_PRIMARY_PROVIDER` | `str` | `'tushare'` | **no** | base.py:205 |
| `MACRO_SYNC_PROVIDER_SLEEP_SECONDS` | `float` | `0.2` | **no** | base.py:207 |
| `MACRO_YIELD_BACKFILL_CALL_SLEEP_SECONDS` | `float` | `31.0` | **no** | base.py:211 |
| `MACRO_YIELD_BACKFILL_MAX_RETRIES` | `int` | `3` | **no** | base.py:209 |
| `MACRO_YIELD_BACKFILL_RETRY_SLEEP_SECONDS` | `float` | `65.0` | **no** | base.py:210 |
| `MACRO_YIELD_BACKFILL_WINDOW_MONTHS` | `int` | `36` | **no** | base.py:208 |
| `NEWS_BACKFILL_CHUNK_DAYS` | `int` | `31` | **no** | base.py:215 |
| `NEWS_BACKFILL_ENABLED` | `bool` | `True` | **no** | base.py:213 |
| `NEWS_BACKFILL_FLOOR` | `str` | `'2021-04-15 00:00:00'` | **no** | base.py:216 |
| `NEWS_BACKFILL_LIMIT_PER_PROVIDER` | `int` | `0` | **no** | base.py:217 |
| `NEWS_BACKFILL_PROVIDER` | `str` | `'tushare_major'` | **no** | base.py:214 |
| `REDIS_URL` | `str` | `'redis://localhost:6379/1'` | yes | base.py:328, base.py:336 |
| `SMS_WEBHOOK_URL` | `str` | `''` | **no** | base.py:402 |
| `TUSHARE_TOKEN` | `str` | `None` | yes | base.py:30 |

**25** variables are env-overridable but absent from `.env.example`: `ALERTS_ENABLE_SMS`, `CAPITAL_FLOW_DAILY_SYNC_LOOKBACK_DAYS`, `DEFAULT_FROM_EMAIL`, `DJANGO_EMAIL_BACKEND`, `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_HOST_PASSWORD`, `EMAIL_HOST_USER`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `FRONTEND_URL`, `HISTORICAL_DATA_FLOOR`, `MACRO_SYNC_FALLBACK_PROVIDER`, `MACRO_SYNC_PRIMARY_PROVIDER`, `MACRO_SYNC_PROVIDER_SLEEP_SECONDS`, `MACRO_YIELD_BACKFILL_CALL_SLEEP_SECONDS`, `MACRO_YIELD_BACKFILL_MAX_RETRIES`, `MACRO_YIELD_BACKFILL_RETRY_SLEEP_SECONDS`, `MACRO_YIELD_BACKFILL_WINDOW_MONTHS`, `NEWS_BACKFILL_CHUNK_DAYS`, `NEWS_BACKFILL_ENABLED`, `NEWS_BACKFILL_FLOOR`, `NEWS_BACKFILL_LIMIT_PER_PROVIDER`, `NEWS_BACKFILL_PROVIDER`, `SMS_WEBHOOK_URL`. They all have defaults, so nothing breaks -- but they cannot be discovered from the example file.


## Keys in `.env.example` that settings never read

These are not Django settings. A key consumed by a launcher under `scripts/` still works; a key with no consumer anywhere has no effect at all and should be wired up or removed.

| Key | Status |
| --- | --- |
| `CELERY_RESULT_BACKEND` | read by `scripts/_native_env.ps1`, `scripts/_native_env.sh` |
| `SMOKE_PASSWORD` | read by `scripts/smoke_api_check.sh` |
| `SMOKE_USERNAME` | read by `scripts/smoke_api_check.sh` |
| `WSL2_UBUNTU_ACCOUNT` | **no consumer found -- inert** |
| `WSL2_UBUNTU_PASSWORD` | **no consumer found -- inert** |
