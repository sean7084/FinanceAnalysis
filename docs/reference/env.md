<!--
  GENERATED FILE - DO NOT EDIT BY HAND.
  Regenerate: python manage.py export_documentation_facts
  Source of truth: every env() read in config/settings/*.py, cross-checked against .env.example.
-->

# Environment Variable Reference

_Generated: 2026-10-24T09:14:13Z_

`.env` at the repository root is the only env file the native stack needs; `manage.py` sets `DJANGO_READ_DOT_ENV_FILE=True` when it exists. OS environment variables take precedence over `.env` values.

## Variables read by settings

| Variable | Type | Default | In `.env.example` | Read at |
| --- | --- | --- | --- | --- |
| `ALERTS_ENABLE_SMS` | `bool` | `False` | yes | base.py:611 |
| `ALERT_NOTIFY_EMAILS` | `list` | `[]` | yes | base.py:615 |
| `ARTIFACT_LOCAL_CACHE_ROOT` | `str` | `str(BASE_DIR)` | **no** | base.py:346 |
| `ARTIFACT_S3_ACCESS_KEY_ID` | `str` | `''` | yes | base.py:349 |
| `ARTIFACT_S3_ADDRESSING_STYLE` | `str` | `'path'` | yes | base.py:352 |
| `ARTIFACT_S3_BUCKET` | `str` | `'finance-analysis-artifacts'` | yes | base.py:348 |
| `ARTIFACT_S3_ENDPOINT_URL` | `str` | `''` | yes | base.py:347 |
| `ARTIFACT_S3_REGION` | `str` | `'us-east-1'` | yes | base.py:351 |
| `ARTIFACT_S3_SECRET_ACCESS_KEY` | `str` | `''` | yes | base.py:350 |
| `ARTIFACT_STORE_BACKEND` | `str` | `'local'` | yes | base.py:345 |
| `BACKTEST_PAYLOAD_CACHE_SECONDS` | `int` | `3600` | yes | base.py:359 |
| `BACKTEST_STALE_TASK_MAX_AGE_SECONDS` | `int` | `2400` | yes | base.py:355 |
| `CAPITAL_FLOW_DAILY_SYNC_LOOKBACK_DAYS` | `int` | `20` | yes | base.py:282 |
| `CELERY_BROKER_URL` | `str` | `'redis://localhost:6379/0'` | yes | base.py:188 |
| `DASHBOARD_INDICATOR_LOOKBACK_DAYS` | `int` | `60` | yes | base.py:374 |
| `DASHBOARD_STOCKS_CACHE_SECONDS` | `int` | `86400` | yes | base.py:364 |
| `DASHBOARD_SUMMARY_CACHE_SECONDS` | `int` | `60` | yes | base.py:367 |
| `DATABASE_URL` | `db` | _required_ | yes | base.py:129 |
| `DEFAULT_FROM_EMAIL` | `str` | `'noreply@financeanalysis.com'` | yes | base.py:557 |
| `DJANGO_ALLOWED_HOSTS` | `list` | `['localhost', '0.0.0.0', '127.0.0.1', '192.168.31.30']` | yes | local.py:9 |
| `DJANGO_DEBUG` | `bool` | `False` | yes | base.py:37 |
| `DJANGO_EMAIL_BACKEND` | `str` | `'django.core.mail.backends.console.EmailBackend'` | yes | local.py:17 |
| `DJANGO_READ_DOT_ENV_FILE` | `bool` | `False` | yes | base.py:25 |
| `DJANGO_SECRET_KEY` | `str` | `'django-insecure-local-secret-key'` | yes | local.py:7 |
| `DJANGO_VITE_DEV_MODE` | `bool` | `DEBUG` | yes | base.py:594 |
| `DJANGO_VITE_DEV_SERVER_HOST` | `str` | `'localhost'` | yes | base.py:598 |
| `DJANGO_VITE_DEV_SERVER_PORT` | `int` | `5173` | yes | base.py:599 |
| `EMAIL_BACKEND` | `str` | `'django.core.mail.backends.console.EmailBackend'` | yes | base.py:553 |
| `EMAIL_HOST` | `str` | `'localhost'` | yes | base.py:578 |
| `EMAIL_HOST_PASSWORD` | `str` | `''` | yes | base.py:582 |
| `EMAIL_HOST_USER` | `str` | `''` | yes | base.py:581 |
| `EMAIL_PORT` | `str` | `587` | yes | base.py:579 |
| `EMAIL_USE_TLS` | `bool` | `True` | yes | base.py:580 |
| `ETF_BENCHMARK_CODE` | `str` | `'H00905.CSI'` | **no** | base.py:318 |
| `ETF_COMMISSION_RATE` | `float` | `0.0003` | **no** | base.py:327 |
| `ETF_DEDUP_TRACK_INDEX` | `bool` | `True` | **no** | base.py:311 |
| `ETF_DEFENSIVE_ASSET` | `str` | `'511880.SH'` | **no** | base.py:316 |
| `ETF_LABEL_HORIZON_DAYS` | `int` | `20` | **no** | base.py:325 |
| `ETF_LIQUIDITY_FLOOR_MULTIPLE` | `float` | `10.0` | **no** | base.py:302 |
| `ETF_MAX_CONCURRENT_BACKTESTS` | `int` | `2` | **no** | base.py:337 |
| `ETF_MAX_PER_CATEGORY` | `int` | `2` | **no** | base.py:314 |
| `ETF_MAX_POSITIONS` | `int` | `5` | **no** | base.py:304 |
| `ETF_MIN_AVG_AMOUNT_YUAN` | `int` | `50000000` | **no** | base.py:300 |
| `ETF_MIN_COMMISSION` | `int` | `5` | **no** | base.py:329 |
| `ETF_MIN_LISTING_AGE_DAYS` | `int` | `183` | **no** | base.py:297 |
| `ETF_SECONDARY_BENCHMARK_CODES` | `list` | `['000905.SH']` | **no** | base.py:323 |
| `ETF_SLIPPAGE` | `float` | `0.001` | **no** | base.py:331 |
| `ETF_SYNC_MAX_RETRIES` | `int` | `5` | **no** | base.py:295 |
| `ETF_SYNC_REQUEST_SLEEP_SECONDS` | `float` | `0.15` | **no** | base.py:293 |
| `ETF_SYNC_RETRY_SLEEP_SECONDS` | `float` | `65.0` | **no** | base.py:294 |
| `ETF_WEIGHTING` | `str` | `'score'` | **no** | base.py:309 |
| `HISTORICAL_DATA_FLOOR` | `str` | `'2010-01-01'` | yes | base.py:274 |
| `MACRO_SYNC_FALLBACK_PROVIDER` | `str` | `'akshare'` | yes | base.py:276 |
| `MACRO_SYNC_PRIMARY_PROVIDER` | `str` | `'tushare'` | yes | base.py:275 |
| `MACRO_SYNC_PROVIDER_SLEEP_SECONDS` | `float` | `0.2` | yes | base.py:277 |
| `MACRO_YIELD_BACKFILL_CALL_SLEEP_SECONDS` | `float` | `31.0` | yes | base.py:281 |
| `MACRO_YIELD_BACKFILL_MAX_RETRIES` | `int` | `3` | yes | base.py:279 |
| `MACRO_YIELD_BACKFILL_RETRY_SLEEP_SECONDS` | `float` | `65.0` | yes | base.py:280 |
| `MACRO_YIELD_BACKFILL_WINDOW_MONTHS` | `int` | `36` | yes | base.py:278 |
| `NEWS_BACKFILL_CHUNK_DAYS` | `int` | `31` | yes | base.py:285 |
| `NEWS_BACKFILL_ENABLED` | `bool` | `True` | yes | base.py:283 |
| `NEWS_BACKFILL_FLOOR` | `str` | `'2021-04-15 00:00:00'` | yes | base.py:286 |
| `NEWS_BACKFILL_LIMIT_PER_PROVIDER` | `int` | `0` | yes | base.py:287 |
| `NEWS_BACKFILL_PROVIDER` | `str` | `'tushare_major'` | yes | base.py:284 |
| `PREDICTION_GENERATION_LOCK_SECONDS` | `int` | `120` | yes | base.py:395 |
| `PREDICTION_RECONCILE_LOOKBACK_TRADING_DAYS` | `int` | `5` | yes | base.py:384 |
| `PREDICTION_RECONCILE_MAX_ASSETS_PER_RUN` | `int` | `250` | yes | base.py:391 |
| `REDIS_URL` | `str` | `'redis://localhost:6379/1'` | yes | base.py:538, base.py:546 |
| `SMS_WEBHOOK_URL` | `str` | `''` | yes | base.py:612 |
| `TUSHARE_TOKEN` | `str` | `None` | yes | base.py:30 |

**19** variables are env-overridable but absent from `.env.example`: `ARTIFACT_LOCAL_CACHE_ROOT`, `ETF_BENCHMARK_CODE`, `ETF_COMMISSION_RATE`, `ETF_DEDUP_TRACK_INDEX`, `ETF_DEFENSIVE_ASSET`, `ETF_LABEL_HORIZON_DAYS`, `ETF_LIQUIDITY_FLOOR_MULTIPLE`, `ETF_MAX_CONCURRENT_BACKTESTS`, `ETF_MAX_PER_CATEGORY`, `ETF_MAX_POSITIONS`, `ETF_MIN_AVG_AMOUNT_YUAN`, `ETF_MIN_COMMISSION`, `ETF_MIN_LISTING_AGE_DAYS`, `ETF_SECONDARY_BENCHMARK_CODES`, `ETF_SLIPPAGE`, `ETF_SYNC_MAX_RETRIES`, `ETF_SYNC_REQUEST_SLEEP_SECONDS`, `ETF_SYNC_RETRY_SLEEP_SECONDS`, `ETF_WEIGHTING`. They all have defaults, so nothing breaks -- but they cannot be discovered from the example file.


## Keys in `.env.example` that settings never read

These are not Django settings. A key consumed by a launcher under `scripts/` or a Compose entrypoint under `compose/` still works; a key with no consumer anywhere has no effect at all and should be wired up or removed.

| Key | Status |
| --- | --- |
| `CELERY_RESULT_BACKEND` | read by `scripts/_native_env.ps1`, `scripts/_native_env.sh` |
| `CELERY_WORKER_QUEUES` | read by `scripts/restart_cold_stack.sh`, `scripts/run_celery_worker.ps1`, `scripts/run_celery_worker.sh`, `scripts/run_local_stack.sh`, `compose/local/django/start-celeryworker` |
