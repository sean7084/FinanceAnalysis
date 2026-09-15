---
name: Data Pipeline Issue
about: Report data ingestion, API provider, or Celery task failures
title: "[DATA] Brief description"
labels: ["bug", "triage"]
assignees: []
---

## Issue Type
- [ ] Tushare API failure / rate limit / missing data
- [ ] Celery task error (ops / backtest / train-lightgbm / train-lstm queue)
- [ ] Technical indicator calculation error
- [ ] Fundamental data materialization failure
- [ ] Macro data sync failure
- [ ] Sentiment / news backfill issue
- [ ] Stale or missing price data

## Provider
- Provider: [e.g., Tushare, Yahoo Finance, AKShare]
- Token/credential rotated: [Yes / No / N/A]

## Affected Data
- Ticker(s): [e.g., 000001.SZ, CSI 300 constituents]
- Date range: [e.g., 2024-01-01 to 2024-12-31]
- Frequency: [daily / weekly / minute]

## Error Details
Paste the relevant Celery task traceback, log excerpt, or API error message:

```
[paste error here]
```

## Queue / Task
- Queue: [e.g., ops, backtest, train-lightgbm]
- Task name: [e.g., apps.markets.tasks.sync_daily_prices]
- Task ID (if available): ...

## Steps to Reproduce
1. Run command: `python manage.py ...` or trigger Celery task ...
2. Observe ...

## Environment
- Django settings module: [e.g., config.settings.local]
- Redis reachable: [Yes / No]
- PostgreSQL reachable: [Yes / No]

## Additional Context
Any other relevant information (recent config changes, network issues, etc.).
