<!--
  GENERATED FILE - DO NOT EDIT BY HAND.
  Regenerate: python manage.py export_documentation_facts
  Source of truth: every env() read in config/settings/*.py, cross-checked against .env.example.
-->

# Environment Variable Reference

_Generated: 2026-11-10T15:08:28Z_

`.env` at the repository root is the only env file the native stack needs; `manage.py` sets `DJANGO_READ_DOT_ENV_FILE=True` when it exists. OS environment variables take precedence over `.env` values.

## Variables read by settings

| Variable | Type | Default | In `.env.example` | Read at |
| --- | --- | --- | --- | --- |
| `ALERTS_ENABLE_SMS` | `bool` | `False` | yes | base.py:784 |
| `ALERT_NOTIFY_EMAILS` | `list` | `[]` | yes | base.py:788 |
| `ARTIFACT_LOCAL_CACHE_ROOT` | `str` | `str(BASE_DIR)` | **no** | base.py:471 |
| `ARTIFACT_S3_ACCESS_KEY_ID` | `str` | `''` | yes | base.py:474 |
| `ARTIFACT_S3_ADDRESSING_STYLE` | `str` | `'path'` | yes | base.py:477 |
| `ARTIFACT_S3_BUCKET` | `str` | `'armillary-artifacts'` | yes | base.py:473 |
| `ARTIFACT_S3_ENDPOINT_URL` | `str` | `''` | yes | base.py:472 |
| `ARTIFACT_S3_REGION` | `str` | `'us-east-1'` | yes | base.py:476 |
| `ARTIFACT_S3_SECRET_ACCESS_KEY` | `str` | `''` | yes | base.py:475 |
| `ARTIFACT_STORE_BACKEND` | `str` | `'local'` | yes | base.py:470 |
| `BACKTEST_PAYLOAD_CACHE_SECONDS` | `int` | `3600` | yes | base.py:484 |
| `BACKTEST_STALE_TASK_MAX_AGE_SECONDS` | `int` | `2400` | yes | base.py:480 |
| `CAPITAL_FLOW_DAILY_SYNC_LOOKBACK_DAYS` | `int` | `20` | yes | base.py:351 |
| `CATCHUP_CROSS_SECTION_MIN_RATIO` | `float` | `0.5` | yes | base.py:542 |
| `CATCHUP_LOCK_SECONDS` | `int` | `1500` | yes | base.py:535 |
| `CATCHUP_LOOKBACK_TRADING_DAYS` | `int` | `60` | yes | base.py:529 |
| `CATCHUP_MAX_CALLS_PER_RUN` | `int` | `200` | yes | base.py:530 |
| `CATCHUP_MAX_SECONDS_PER_RUN` | `int` | `900` | yes | base.py:531 |
| `CELERY_BROKER_URL` | `str` | `'redis://localhost:6379/0'` | yes | base.py:225 |
| `DASHBOARD_INDICATOR_LOOKBACK_DAYS` | `int` | `60` | yes | base.py:499 |
| `DASHBOARD_STOCKS_CACHE_SECONDS` | `int` | `86400` | yes | base.py:489 |
| `DASHBOARD_SUMMARY_CACHE_SECONDS` | `int` | `60` | yes | base.py:492 |
| `DATABASE_URL` | `db` | _required_ | yes | base.py:138 |
| `DB_CONNECT_TIMEOUT_SECONDS` | `int` | `10` | yes | base.py:163 |
| `DB_CONN_MAX_AGE` | `int` | `0` | yes | base.py:146 |
| `DB_TCP_KEEPALIVE_COUNT` | `int` | `3` | yes | base.py:167 |
| `DB_TCP_KEEPALIVE_IDLE_SECONDS` | `int` | `30` | yes | base.py:165 |
| `DB_TCP_KEEPALIVE_INTERVAL_SECONDS` | `int` | `10` | yes | base.py:166 |
| `DEFAULT_FROM_EMAIL` | `str` | `'noreply@armillary.local'` | yes | base.py:730 |
| `DJANGO_ALLOWED_HOSTS` | `list` | `['localhost', '0.0.0.0', '127.0.0.1', '192.168.31.30']` | yes | local.py:9 |
| `DJANGO_DEBUG` | `bool` | `False` | yes | base.py:45 |
| `DJANGO_EMAIL_BACKEND` | `str` | `'django.core.mail.backends.console.EmailBackend'` | yes | local.py:17 |
| `DJANGO_READ_DOT_ENV_FILE` | `bool` | `False` | yes | base.py:33 |
| `DJANGO_SECRET_KEY` | `str` | `'django-insecure-local-secret-key'` | yes | local.py:7 |
| `DJANGO_VITE_DEV_MODE` | `bool` | `False` | yes | base.py:767 |
| `DJANGO_VITE_DEV_SERVER_HOST` | `str` | `'localhost'` | yes | base.py:771 |
| `DJANGO_VITE_DEV_SERVER_PORT` | `int` | `5173` | yes | base.py:772 |
| `EMAIL_BACKEND` | `str` | `'django.core.mail.backends.console.EmailBackend'` | yes | base.py:726 |
| `EMAIL_HOST` | `str` | `'localhost'` | yes | base.py:751 |
| `EMAIL_HOST_PASSWORD` | `str` | `''` | yes | base.py:755 |
| `EMAIL_HOST_USER` | `str` | `''` | yes | base.py:754 |
| `EMAIL_PORT` | `str` | `587` | yes | base.py:752 |
| `EMAIL_USE_TLS` | `bool` | `True` | yes | base.py:753 |
| `ETF_BENCHMARK_CODE` | `str` | `'H00905.CSI'` | **no** | base.py:422 |
| `ETF_COMMISSION_RATE` | `float` | `0.0003` | **no** | base.py:452 |
| `ETF_DEDUP_TRACK_INDEX` | `bool` | `True` | **no** | base.py:385 |
| `ETF_DEFENSIVE_ASSET` | `str` | `'511880.SH'` | **no** | base.py:390 |
| `ETF_LABEL_HORIZON_DAYS` | `int` | `20` | **no** | base.py:429 |
| `ETF_LGB_NUM_THREADS` | `int` | `max(1, min(16, (os.cpu_count() or 4) - 2))` | yes | base.py:434 |
| `ETF_LIQUIDITY_FLOOR_MULTIPLE` | `float` | `10.0` | **no** | base.py:373 |
| `ETF_LIVE_DRIFT_MEAN_RETURN_TOLERANCE` | `float` | `0.03` | yes | base.py:419 |
| `ETF_LIVE_DRIFT_MIN_CLOSED_TRADES` | `int` | `10` | yes | base.py:420 |
| `ETF_LIVE_DRIFT_WIN_RATE_TOLERANCE` | `float` | `0.2` | yes | base.py:418 |
| `ETF_LIVE_REFERENCE_BACKTEST_ID` | `int` | `0` | yes | base.py:417 |
| `ETF_LSTM_BATCH_SIZE` | `int` | `256` | yes | base.py:448 |
| `ETF_LSTM_DROPOUT` | `float` | `0.2` | yes | base.py:446 |
| `ETF_LSTM_EPOCHS` | `int` | `50` | yes | base.py:447 |
| `ETF_LSTM_HIDDEN_SIZE` | `int` | `64` | yes | base.py:444 |
| `ETF_LSTM_LEARNING_RATE` | `float` | `0.001` | yes | base.py:449 |
| `ETF_LSTM_NUM_LAYERS` | `int` | `2` | yes | base.py:445 |
| `ETF_LSTM_PATIENCE` | `int` | `5` | yes | base.py:450 |
| `ETF_LSTM_SEQUENCE_LENGTH` | `int` | `20` | yes | base.py:443 |
| `ETF_MAX_CONCURRENT_BACKTESTS` | `int` | `2` | **no** | base.py:462 |
| `ETF_MAX_PER_CATEGORY` | `int` | `2` | **no** | base.py:388 |
| `ETF_MAX_POSITIONS` | `int` | `3` | **no** | base.py:378 |
| `ETF_MIN_AVG_AMOUNT_YUAN` | `int` | `50000000` | **no** | base.py:371 |
| `ETF_MIN_COMMISSION` | `int` | `5` | **no** | base.py:454 |
| `ETF_MIN_LISTING_AGE_DAYS` | `int` | `183` | **no** | base.py:368 |
| `ETF_ML_SHADOW_ARMS` | `json` | `[]` | yes | base.py:407 |
| `ETF_ML_SHADOW_MODEL` | `str` | `''` | yes | base.py:402 |
| `ETF_SECONDARY_BENCHMARK_CODES` | `list` | `['000905.SH']` | **no** | base.py:427 |
| `ETF_SHARE_FLOW_ADJUST_FOR_SPLITS` | `bool` | `False` | yes | base.py:411 |
| `ETF_SIM_CAPITAL_BASE` | `int` | `1000000` | yes | base.py:395 |
| `ETF_SLIPPAGE` | `float` | `0.001` | **no** | base.py:456 |
| `ETF_SYNC_MAX_RETRIES` | `int` | `5` | **no** | base.py:366 |
| `ETF_SYNC_REQUEST_SLEEP_SECONDS` | `float` | `0.15` | **no** | base.py:364 |
| `ETF_SYNC_RETRY_SLEEP_SECONDS` | `float` | `65.0` | **no** | base.py:365 |
| `ETF_WEIGHTING` | `str` | `'score'` | **no** | base.py:383 |
| `HISTORICAL_DATA_FLOOR` | `str` | `'2010-01-01'` | yes | base.py:328 |
| `MACRO_PUBLICATION_LAG_DAYS_FALLBACK` | `int` | `35` | yes | base.py:336 |
| `MACRO_RELEASE_LAG_DAYS` | `json` | `{'pmi_manufacturing': 1, 'pmi_non_manufacturing': 1, 'cpi_yoy': 9, 'ppi_yoy': 9, 'dxy': 1, 'cny_usd': 1, 'cn10y_yield': 2, 'cn3y_yield': 2}` | yes | base.py:337 |
| `MACRO_SYNC_FALLBACK_PROVIDER` | `str` | `'akshare'` | yes | base.py:330 |
| `MACRO_SYNC_PRIMARY_PROVIDER` | `str` | `'tushare'` | yes | base.py:329 |
| `MACRO_SYNC_PROVIDER_SLEEP_SECONDS` | `float` | `0.2` | yes | base.py:331 |
| `MACRO_YIELD_BACKFILL_CALL_SLEEP_SECONDS` | `float` | `31.0` | yes | base.py:350 |
| `MACRO_YIELD_BACKFILL_MAX_RETRIES` | `int` | `3` | yes | base.py:348 |
| `MACRO_YIELD_BACKFILL_RETRY_SLEEP_SECONDS` | `float` | `65.0` | yes | base.py:349 |
| `MACRO_YIELD_BACKFILL_WINDOW_MONTHS` | `int` | `36` | yes | base.py:347 |
| `NEWS_BACKFILL_CHUNK_DAYS` | `int` | `31` | yes | base.py:354 |
| `NEWS_BACKFILL_ENABLED` | `bool` | `True` | yes | base.py:352 |
| `NEWS_BACKFILL_FLOOR` | `str` | `'2021-04-15 00:00:00'` | yes | base.py:357 |
| `NEWS_BACKFILL_LIMIT_PER_PROVIDER` | `int` | `0` | yes | base.py:358 |
| `NEWS_BACKFILL_PROVIDER` | `str` | `'tushare_major'` | yes | base.py:353 |
| `PREDICTION_GENERATION_LOCK_SECONDS` | `int` | `120` | yes | base.py:520 |
| `PREDICTION_RECONCILE_LOOKBACK_TRADING_DAYS` | `int` | `5` | yes | base.py:509 |
| `PREDICTION_RECONCILE_MAX_ASSETS_PER_RUN` | `int` | `250` | yes | base.py:516 |
| `REDIS_URL` | `str` | `'redis://localhost:6379/1'` | yes | base.py:711, base.py:719 |
| `SMS_WEBHOOK_URL` | `str` | `''` | yes | base.py:785 |
| `TUSHARE_TOKEN` | `str` | `None` | yes | base.py:38 |

**19** variables are env-overridable but absent from `.env.example`: `ARTIFACT_LOCAL_CACHE_ROOT`, `ETF_BENCHMARK_CODE`, `ETF_COMMISSION_RATE`, `ETF_DEDUP_TRACK_INDEX`, `ETF_DEFENSIVE_ASSET`, `ETF_LABEL_HORIZON_DAYS`, `ETF_LIQUIDITY_FLOOR_MULTIPLE`, `ETF_MAX_CONCURRENT_BACKTESTS`, `ETF_MAX_PER_CATEGORY`, `ETF_MAX_POSITIONS`, `ETF_MIN_AVG_AMOUNT_YUAN`, `ETF_MIN_COMMISSION`, `ETF_MIN_LISTING_AGE_DAYS`, `ETF_SECONDARY_BENCHMARK_CODES`, `ETF_SLIPPAGE`, `ETF_SYNC_MAX_RETRIES`, `ETF_SYNC_REQUEST_SLEEP_SECONDS`, `ETF_SYNC_RETRY_SLEEP_SECONDS`, `ETF_WEIGHTING`. They all have defaults, so nothing breaks -- but they cannot be discovered from the example file.


## Keys in `.env.example` that settings never read

These are not Django settings. A key consumed by a launcher under `scripts/` or a Compose entrypoint under `compose/` still works; a key with no consumer anywhere has no effect at all and should be wired up or removed.

| Key | Status |
| --- | --- |
| `CELERY_RESULT_BACKEND` | read by `scripts/_native_env.ps1`, `scripts/_native_env.sh` |
| `CELERY_WORKER_QUEUES` | read by `scripts/restart_cold_stack.sh`, `scripts/run_celery_worker.ps1`, `scripts/run_celery_worker.sh`, `scripts/run_local_stack.sh`, `compose/local/django/start-celeryworker` |
| `DB_WEDGE_CLIENT_WRITE_MAX_AGE_SECONDS` | **no consumer found -- inert** |
| `DB_WEDGE_IDLE_IN_TXN_MAX_AGE_SECONDS` | **no consumer found -- inert** |
| `DB_WEDGE_TERMINATE` | read by `scripts/run_local_stack.sh` |
| `DB_WEDGE_WATCH_INTERVAL_SECONDS` | **no consumer found -- inert** |
