---
kind: error_handling
name: 'Error Handling: Django/DRF Validation, Celery Task Resilience, and Provider Fallbacks'
category: error_handling
scope:
    - '**'
source_files:
    - config/settings/base.py
    - apps/users/middleware.py
    - apps/core/throttling.py
    - apps/sentiment/providers.py
    - apps/sentiment/tasks.py
    - apps/macro/providers.py
    - apps/macro/tasks.py
    - apps/backtest/tasks.py
    - apps/analytics/management/commands/backfill_signal_events.py
    - apps/analytics/management/commands/backfill_technical_indicators.py
    - apps/analytics/indicator_warmup.py
---

## What system/approach is used

The repository does not define a custom exception hierarchy or centralized error codes. Instead, it relies on:
- **Django REST Framework (DRF) serializers** for request validation errors, returned as `400 Bad Request` with `serializer.errors`.
- **Standard Python exceptions** (`ValueError`, `TypeError`) for invalid business parameters.
- **Django management command `CommandError`** for CLI argument validation failures.
- **Celery task-level resilience**: tasks catch provider/network errors, log them into result metadata, and fall back to alternative data sources rather than failing the whole pipeline.
- **No global DRF exception handler** is configured in `config/settings/base.py`; DRF's default exception-to-response mapping is used.

## Key files and packages

- `apps/users/middleware.py` — `APIUsageMiddleware` records every API response status code; no exception interception.
- `apps/core/throttling.py` — Custom DRF throttles raise DRF's built-in `Throttled` when limits are exceeded; handled by DRF defaults.
- `apps/sentiment/providers.py` — Raises `ValueError('Unsupported provider: ...')` / `ValueError('TUSHARE_TOKEN is required ...')` for bad inputs; callers wrap each provider fetch in `try/except Exception` and collect errors into a list instead of aborting.
- `apps/sentiment/tasks.py` — `fetch_latest_market_news` catches per-provider exceptions, continues ingestion with partial results, and returns a summary string that includes provider errors. `run_hourly_historical_news_backfill` detects provider quota errors via string matching (`_is_provider_quota_error`) and defers instead of raising.
- `apps/macro/providers.py` — `call_tushare_with_retries` retries transient failures up to a configurable number of attempts, recording retry counts and error strings in payload `metadata`. `fetch_macro_snapshot_with_fallback` tries primary then fallback provider, merging missing fields and preserving both `primary_error` and `fallback_error` in metadata. `_safe_call_akshare` swallows all exceptions and returns `None` so AkShare failures never break the pipeline.
- `apps/backtest/tasks.py` — Uses `Decimal` arithmetic with defensive `_to_decimal_or_none` helpers; raises `ValueError` only for misconfigured fee modes (e.g. mixing legacy flat fee with structured fee params). Database connection errors (`InterfaceError`, `OperationalError`) are imported but not shown being caught inline here; chunked execution plus Celery soft/hard time limits (`CELERY_TASK_SOFT_TIME_LIMIT = 60`, `CELERY_TASK_TIME_LIMIT = 5 * 60`) provide process-level resilience.
- `apps/analytics/management/commands/*.py` — Validate CLI arguments and raise `django.core.management.CommandError` with human-readable messages (e.g. `Invalid {label}: {value}. Expected YYYY-MM-DD.`).
- `apps/analytics/indicator_warmup.py` — Raises `ValueError` for unsupported indicator types and missing parameters.

## Architecture and conventions

1. **API layer (views)**: Views validate input via DRF serializers. On `is_valid() == False`, they return `Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)`. No custom exception classes are raised at this boundary; DRF converts serializer/validation errors into JSON responses automatically.
2. **Business logic layer**: Functions raise `ValueError` for invalid parameters (e.g. unsupported providers, negative fees, missing tokens). These are considered programming/configuration errors, not expected runtime conditions.
3. **External calls (providers, APIs)**: Every call to third-party services (TuShare, AkShare, news providers) is wrapped in `try/except Exception`. Errors are captured as strings and stored in a `metadata` dict attached to the resulting payload, so downstream consumers can inspect what failed without the pipeline crashing.
4. **Fallback strategy**: Macro data uses explicit primary/fallback providers (`tushare` → `akshare`). Missing fields from the primary are filled from the fallback, and `metadata.field_sources` tracks which source provided each field. If both fail, an empty payload with `source_used: 'none'` and recorded errors is returned.
5. **Task resilience**: Celery tasks treat provider quota exhaustion as retryable (deferred, not failed). The sentiment pipeline aggregates per-provider errors into a single result string rather than raising.
6. **Management commands**: Use `CommandError` exclusively for user-facing argument validation errors, with `from exc` chaining to preserve original tracebacks.
7. **Database transactions**: `DATABASES["default"]["ATOMIC_REQUESTS"] = True` ensures each HTTP request runs in a transaction that rolls back on unhandled exceptions.
8. **Rate limiting**: Throttling errors are handled by DRF's built-in `Throttled` exception; no custom throttle error format is defined.

## Conventions and constraints

- **Do not swallow `Exception` in views**: View functions delegate validation to serializers and let DRF handle unknown exceptions via its default handler.
- **Always wrap external provider calls in `try/except Exception`**: See `apps/sentiment/tasks.py` (`fetch_latest_market_news`) and `apps/macro/providers.py` (`fetch_macro_snapshot_from_tushare`, `_safe_call_akshare`). Errors must be recorded in `metadata` or collected in an `errors` list, never allowed to bubble uncaught.
- **Use `ValueError` for invalid configuration/parameters**: Used consistently for unsupported providers, missing tokens, invalid fee mode combinations, and bad indicator types.
- **Use `CommandError` for CLI argument validation**: All management commands under `apps/*/management/commands/` raise `CommandError` with descriptive messages.
- **Provider quota errors are special-cased**: In `apps/sentiment/tasks.py`, `_is_provider_quota_error` checks for Chinese quota markers (`最多访问该接口`, `频率超限`) and returns a deferred message instead of raising.
- **Never mix legacy and structured fee configurations**: `apps/backtest/tasks.py._resolve_fee_config` explicitly raises `ValueError` if both `fee_rate` and structured fee keys are supplied, because they price turnover differently.
- **No custom DRF exception handler**: There is no `DEFAULT_EXCEPTION_HANDLER` override in `REST_FRAMEWORK` settings; DRF's default behavior maps exceptions to JSON responses.