<!--
  GENERATED FILE - DO NOT EDIT BY HAND.
  Regenerate: python manage.py export_documentation_facts
  Source of truth: every env() read in config/settings/*.py, cross-checked against .env.example.
-->

# Environment Variable Reference

_Generated: 2026-10-22T03:01:42Z_

`.env` at the repository root is the only env file the native stack needs; `manage.py` sets `DJANGO_READ_DOT_ENV_FILE=True` when it exists. OS environment variables take precedence over `.env` values.

## Variables read by settings

| Variable | Type | Default | In `.env.example` | Read at |
| --- | --- | --- | --- | --- |
| `ALERTS_ENABLE_SMS` | `bool` | `False` | yes | base.py:541 |
| `ALERT_NOTIFY_EMAILS` | `list` | `[]` | yes | base.py:545 |
| `ARTIFACT_LOCAL_CACHE_ROOT` | `str` | `str(BASE_DIR)` | **no** | base.py:307 |
| `ARTIFACT_S3_ACCESS_KEY_ID` | `str` | `''` | yes | base.py:310 |
| `ARTIFACT_S3_ADDRESSING_STYLE` | `str` | `'path'` | yes | base.py:313 |
| `ARTIFACT_S3_BUCKET` | `str` | `'finance-analysis-artifacts'` | yes | base.py:309 |
| `ARTIFACT_S3_ENDPOINT_URL` | `str` | `''` | yes | base.py:308 |
| `ARTIFACT_S3_REGION` | `str` | `'us-east-1'` | yes | base.py:312 |
| `ARTIFACT_S3_SECRET_ACCESS_KEY` | `str` | `''` | yes | base.py:311 |
| `ARTIFACT_STORE_BACKEND` | `str` | `'local'` | yes | base.py:306 |
| `BACKTEST_PAYLOAD_CACHE_SECONDS` | `int` | `3600` | yes | base.py:320 |
| `BACKTEST_STALE_TASK_MAX_AGE_SECONDS` | `int` | `2400` | yes | base.py:316 |
| `CAPITAL_FLOW_DAILY_SYNC_LOOKBACK_DAYS` | `int` | `20` | yes | base.py:264 |
| `CELERY_BROKER_URL` | `str` | `'redis://localhost:6379/0'` | yes | base.py:188 |
| `DASHBOARD_INDICATOR_LOOKBACK_DAYS` | `int` | `60` | yes | base.py:335 |
| `DASHBOARD_STOCKS_CACHE_SECONDS` | `int` | `86400` | yes | base.py:325 |
| `DASHBOARD_SUMMARY_CACHE_SECONDS` | `int` | `60` | yes | base.py:328 |
| `DATABASE_URL` | `db` | _required_ | yes | base.py:129 |
| `DEFAULT_FROM_EMAIL` | `str` | `'noreply@financeanalysis.com'` | yes | base.py:487 |
| `DJANGO_ALLOWED_HOSTS` | `list` | `['localhost', '0.0.0.0', '127.0.0.1', '192.168.31.30']` | yes | local.py:9 |
| `DJANGO_DEBUG` | `bool` | `False` | yes | base.py:37 |
| `DJANGO_EMAIL_BACKEND` | `str` | `'django.core.mail.backends.console.EmailBackend'` | yes | local.py:17 |
| `DJANGO_READ_DOT_ENV_FILE` | `bool` | `False` | yes | base.py:25 |
| `DJANGO_SECRET_KEY` | `str` | `'django-insecure-local-secret-key'` | yes | local.py:7 |
| `DJANGO_VITE_DEV_MODE` | `bool` | `DEBUG` | yes | base.py:524 |
| `DJANGO_VITE_DEV_SERVER_HOST` | `str` | `'localhost'` | yes | base.py:528 |
| `DJANGO_VITE_DEV_SERVER_PORT` | `int` | `5173` | yes | base.py:529 |
| `EMAIL_BACKEND` | `str` | `'django.core.mail.backends.console.EmailBackend'` | yes | base.py:483 |
| `EMAIL_HOST` | `str` | `'localhost'` | yes | base.py:508 |
| `EMAIL_HOST_PASSWORD` | `str` | `''` | yes | base.py:512 |
| `EMAIL_HOST_USER` | `str` | `''` | yes | base.py:511 |
| `EMAIL_PORT` | `str` | `587` | yes | base.py:509 |
| `EMAIL_USE_TLS` | `bool` | `True` | yes | base.py:510 |
| `ETF_BENCHMARK_CODE` | `str` | `'H00905.CSI'` | **no** | base.py:290 |
| `ETF_COMMISSION_RATE` | `float` | `0.0003` | **no** | base.py:294 |
| `ETF_DEFENSIVE_ASSET` | `str` | `'511880.SH'` | **no** | base.py:288 |
| `ETF_LABEL_HORIZON_DAYS` | `int` | `20` | **no** | base.py:292 |
| `ETF_LIQUIDITY_FLOOR_MULTIPLE` | `float` | `10.0` | **no** | base.py:284 |
| `ETF_MAX_POSITIONS` | `int` | `5` | **no** | base.py:286 |
| `ETF_MIN_AVG_AMOUNT_YUAN` | `int` | `50000000` | **no** | base.py:282 |
| `ETF_MIN_COMMISSION` | `int` | `5` | **no** | base.py:296 |
| `ETF_MIN_LISTING_AGE_DAYS` | `int` | `183` | **no** | base.py:279 |
| `ETF_SLIPPAGE` | `float` | `0.001` | **no** | base.py:298 |
| `ETF_SYNC_MAX_RETRIES` | `int` | `5` | **no** | base.py:277 |
| `ETF_SYNC_REQUEST_SLEEP_SECONDS` | `float` | `0.15` | **no** | base.py:275 |
| `ETF_SYNC_RETRY_SLEEP_SECONDS` | `float` | `65.0` | **no** | base.py:276 |
| `HISTORICAL_DATA_FLOOR` | `str` | `'2010-01-01'` | yes | base.py:256 |
| `MACRO_SYNC_FALLBACK_PROVIDER` | `str` | `'akshare'` | yes | base.py:258 |
| `MACRO_SYNC_PRIMARY_PROVIDER` | `str` | `'tushare'` | yes | base.py:257 |
| `MACRO_SYNC_PROVIDER_SLEEP_SECONDS` | `float` | `0.2` | yes | base.py:259 |
| `MACRO_YIELD_BACKFILL_CALL_SLEEP_SECONDS` | `float` | `31.0` | yes | base.py:263 |
| `MACRO_YIELD_BACKFILL_MAX_RETRIES` | `int` | `3` | yes | base.py:261 |
| `MACRO_YIELD_BACKFILL_RETRY_SLEEP_SECONDS` | `float` | `65.0` | yes | base.py:262 |
| `MACRO_YIELD_BACKFILL_WINDOW_MONTHS` | `int` | `36` | yes | base.py:260 |
| `NEWS_BACKFILL_CHUNK_DAYS` | `int` | `31` | yes | base.py:267 |
| `NEWS_BACKFILL_ENABLED` | `bool` | `True` | yes | base.py:265 |
| `NEWS_BACKFILL_FLOOR` | `str` | `'2021-04-15 00:00:00'` | yes | base.py:268 |
| `NEWS_BACKFILL_LIMIT_PER_PROVIDER` | `int` | `0` | yes | base.py:269 |
| `NEWS_BACKFILL_PROVIDER` | `str` | `'tushare_major'` | yes | base.py:266 |
| `PREDICTION_GENERATION_LOCK_SECONDS` | `int` | `120` | yes | base.py:356 |
| `PREDICTION_RECONCILE_LOOKBACK_TRADING_DAYS` | `int` | `5` | yes | base.py:345 |
| `PREDICTION_RECONCILE_MAX_ASSETS_PER_RUN` | `int` | `250` | yes | base.py:352 |
| `REDIS_URL` | `str` | `'redis://localhost:6379/1'` | yes | base.py:468, base.py:476 |
| `SMS_WEBHOOK_URL` | `str` | `''` | yes | base.py:542 |
| `TUSHARE_TOKEN` | `str` | `None` | yes | base.py:30 |

**14** variables are env-overridable but absent from `.env.example`: `ARTIFACT_LOCAL_CACHE_ROOT`, `ETF_BENCHMARK_CODE`, `ETF_COMMISSION_RATE`, `ETF_DEFENSIVE_ASSET`, `ETF_LABEL_HORIZON_DAYS`, `ETF_LIQUIDITY_FLOOR_MULTIPLE`, `ETF_MAX_POSITIONS`, `ETF_MIN_AVG_AMOUNT_YUAN`, `ETF_MIN_COMMISSION`, `ETF_MIN_LISTING_AGE_DAYS`, `ETF_SLIPPAGE`, `ETF_SYNC_MAX_RETRIES`, `ETF_SYNC_REQUEST_SLEEP_SECONDS`, `ETF_SYNC_RETRY_SLEEP_SECONDS`. They all have defaults, so nothing breaks -- but they cannot be discovered from the example file.


## Keys in `.env.example` that settings never read

These are not Django settings. A key consumed by a launcher under `scripts/` or a Compose entrypoint under `compose/` still works; a key with no consumer anywhere has no effect at all and should be wired up or removed.

| Key | Status |
| --- | --- |
| `CELERY_RESULT_BACKEND` | read by `scripts/_native_env.ps1`, `scripts/_native_env.sh` |
| `CELERY_WORKER_QUEUES` | read by `scripts/restart_cold_stack.sh`, `scripts/run_celery_worker.ps1`, `scripts/run_celery_worker.sh`, `scripts/run_local_stack.sh`, `compose/local/django/start-celeryworker` |
