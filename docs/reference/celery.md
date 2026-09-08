<!--
  GENERATED FILE - DO NOT EDIT BY HAND.
  Regenerate: python manage.py export_documentation_facts
  Source of truth: the Celery app registry, CELERY_TASK_ROUTES, and CELERY_BEAT_SCHEDULE.
-->

# Celery Task and Queue Reference

_Generated: 2026-09-07T15:17:02Z_

Operational guidance -- which worker to start, how to scale backtests, what to do when a task times out -- lives in `docs/how-to/local-setup.md` and `docs/how-to/runbook-sync-failure.md`.

## Queue topology

| Queue | Role |
| --- | --- |
| `ops` | periodic syncs, indicators, sentiment, daily predictions (default) |
| `backtest` | `run_backtest`; long-running and CPU-heavy |
| `train-lightgbm` | `train_lightgbm_models` |
| `train-lstm` | `train_lstm_models` |

Default queue: `ops`.


## Time limits

| Scope | Soft limit (s) | Hard limit (s) |
| --- | --- | --- |
| Global default | 60 | 300 |

A task decorated with its own `soft_time_limit` / `time_limit` overrides the global pair. Overrides are listed per task below and are the usual explanation for a task that ran far longer than 60s without being killed.


## Beat schedule

| Entry | Task | Crontab (min hour dow dom moy) | Options |
| --- | --- | --- | --- |
| `check-alert-rules-every-5-min` | `apps.analytics.tasks.check_alert_rules` | `*/5 * * * *` | &mdash; |
| `fetch-latest-market-news-daily` | `apps.sentiment.tasks.fetch_latest_market_news` | `35 16 * * *` | &mdash; |
| `generate-lightgbm-predictions-daily` | `apps.prediction.tasks_lightgbm.generate_lightgbm_predictions_for_date` | `30 18 * * *` | &mdash; |
| `generate-predictions-daily` | `apps.prediction.tasks.generate_predictions_for_date` | `0 18 * * *` | &mdash; |
| `run-daily-sentiment-pipeline` | `apps.sentiment.tasks.run_daily_sentiment_pipeline` | `0 17 * * *` | &mdash; |
| `run-hourly-historical-news-backfill` | `apps.sentiment.tasks.run_hourly_historical_news_backfill` | `12 * * * *` | &mdash; |
| `sync-a-shares-daily-from-tushare` | `apps.markets.tasks.sync_daily_a_shares` | `10 16 * * *` | &mdash; |
| `sync-index-memberships-monthly` | `apps.markets.tasks.sync_monthly_index_memberships` | `15 2 * 1 *` | &mdash; |
| `sync-macro-data-monthly` | `apps.macro.tasks.sync_macro_data_monthly` | `10 0 * 2-8 *` | &mdash; |

**44** registered project tasks, **9** beat entries, **35** reachable only through management commands, API actions, or other tasks.


## Task inventory

| Task | Queue | Routing | Soft limit | Hard limit | Beat entries |
| --- | --- | --- | --- | --- | --- |
| `apps.analytics.tasks.calculate_adx_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_bollinger_bands_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_bollinger_signals_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_ema_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_fibonacci_retracement_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_indicators_for_all_assets` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_ma_signals_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_macd_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_momentum_signals_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_obv_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_reversal_signals_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_rs_scores_for_all_assets` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_rsi_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_signals_for_all_assets` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_sma_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_stochastic_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.calculate_volume_signals_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.analytics.tasks.check_alert_rules` | `ops` | default | global | global | `check-alert-rules-every-5-min` |
| `apps.analytics.tasks.send_alert_notifications` | `ops` | default | global | global | &mdash; |
| `apps.backtest.tasks.run_backtest` | `backtest` | explicit | **1800** | **2100** | &mdash; |
| `apps.factors.tasks.calculate_factor_scores_for_date` | `ops` | default | global | global | &mdash; |
| `apps.factors.tasks.sync_daily_capital_flow_snapshots` | `ops` | default | global | global | &mdash; |
| `apps.macro.tasks.refresh_current_market_context` | `ops` | default | global | global | &mdash; |
| `apps.macro.tasks.sync_macro_data_monthly` | `ops` | default | global | global | `sync-macro-data-monthly` |
| `apps.markets.tasks.run_post_sync_universal_refresh` | `ops` | default | global | global | &mdash; |
| `apps.markets.tasks.sync_asset_history` | `ops` | default | global | global | &mdash; |
| `apps.markets.tasks.sync_daily_a_shares` | `ops` | default | global | global | `sync-a-shares-daily-from-tushare` |
| `apps.markets.tasks.sync_monthly_index_memberships` | `ops` | default | global | global | `sync-index-memberships-monthly` |
| `apps.markets.tasks.sync_official_benchmark_index_history` | `ops` | default | global | global | &mdash; |
| `apps.prediction.tasks.generate_prediction_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.prediction.tasks.generate_predictions_for_date` | `ops` | default | global | global | `generate-predictions-daily` |
| `apps.prediction.tasks.train_prediction_models` | `ops` | default | global | global | &mdash; |
| `apps.prediction.tasks_lightgbm.generate_lightgbm_prediction_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.prediction.tasks_lightgbm.generate_lightgbm_predictions_for_date` | `ops` | default | global | global | `generate-lightgbm-predictions-daily` |
| `apps.prediction.tasks_lightgbm.train_lightgbm_models` | `train-lightgbm` | explicit | global | global | &mdash; |
| `apps.prediction.tasks_lstm.generate_lstm_prediction_for_asset` | `ops` | default | global | global | &mdash; |
| `apps.prediction.tasks_lstm.generate_lstm_predictions_for_date` | `ops` | default | global | global | &mdash; |
| `apps.prediction.tasks_lstm.train_lstm_models` | `train-lstm` | explicit | global | global | &mdash; |
| `apps.sentiment.tasks.calculate_concept_heat` | `ops` | default | global | global | &mdash; |
| `apps.sentiment.tasks.calculate_daily_sentiment` | `ops` | default | global | global | &mdash; |
| `apps.sentiment.tasks.fetch_latest_market_news` | `ops` | default | global | global | `fetch-latest-market-news-daily` |
| `apps.sentiment.tasks.ingest_latest_news` | `ops` | default | global | global | &mdash; |
| `apps.sentiment.tasks.run_daily_sentiment_pipeline` | `ops` | default | global | global | `run-daily-sentiment-pipeline` |
| `apps.sentiment.tasks.run_hourly_historical_news_backfill` | `ops` | default | global | global | `run-hourly-historical-news-backfill` |

### Tasks overriding the global time limits

| Task | Soft limit | Hard limit |
| --- | --- | --- |
| `apps.backtest.tasks.run_backtest` | **1800** | **2100** |

These overrides exist because the global soft limit is short enough to protect the `ops` queue from a stuck sync, while backtests and retrains legitimately run for tens of minutes.

