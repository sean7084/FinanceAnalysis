---
kind: logging_system
name: Minimal Python logging with no centralized configuration
category: logging_system
scope:
    - '**'
source_files:
    - apps/markets/tasks.py
    - apps/analytics/tasks.py
    - config/settings/base.py
    - config/settings/local.py
    - config/settings/production.py
---

## What system/approach is used

The repository uses the **Python standard library `logging` module** only. There is no third-party logging framework (e.g., `structlog`, `loguru`, `sentry-sdk`, `python-json-logger`) and no Django `LOGGING` setting configured anywhere in `config/settings/`. The project relies on Django's default logger setup, which routes log records to the console via a basic `StreamHandler` when `DEBUG=True`.

## Key files and packages

- `apps/markets/tasks.py` — the only file that imports `logging` and creates a module-level logger via `logger = logging.getLogger(__name__)`; it emits structured-ish messages using `logger.warning(...)` (e.g., around Tushare provider retries).
- `config/settings/base.py`, `local.py`, `production.py` — none of these define a `LOGGING` dict or configure handlers/formatters; they only import from `base` and override settings like `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`, and `EMAIL_BACKEND`.
- `apps/analytics/tasks.py` — contains many `print(...)` calls for technical indicator calculations (RSI, MACD, Bollinger Bands, SMA, EMA, Stochastic, ADX, OBV), indicating ad-hoc console output rather than structured logging.

## Architecture and conventions

- **Per-module logger pattern**: The single established convention is to create a module-level logger with `logger = logging.getLogger(__name__)` at the top of a file (seen in `apps/markets/tasks.py`). This gives each module its own logger name rooted at the package path.
- **No global formatter or handler**: Because there is no `LOGGING` configuration, all log records use Python's default formatter (`'%(message)s'`) and are sent to stderr via the root logger's default handler. Log levels are not centrally tuned.
- **Mixed output style**: Some code paths use the proper `logging` module (`apps/markets/tasks.py`), while others fall back to bare `print()` statements (notably `apps/analytics/tasks.py` for indicator computation progress). There is no enforcement mechanism preventing either approach.
- **No structured fields**: Logs are plain strings; there are no JSON payloads, correlation IDs, request IDs, user context, or Celery task IDs attached to log records.
- **No sinks beyond stdout/stderr**: Without a configured handler, logs cannot be routed to files, syslog, an external APM, or a log aggregation service. Production deployments would need to rely on process supervision / container orchestration to capture stdout/stderr.

## Conventions and constraints

- **Observed convention**: When logging is needed, add `import logging` and `logger = logging.getLogger(__name__)` at module scope, then call `logger.debug/info/warning/error/critical` with string messages. This pattern is demonstrated in `apps/markets/tasks.py`.
- **No enforced rule**: There is no linter rule, shared base class, or central logger factory enforcing this pattern; developers can freely choose between `logging` and `print`.
- **Environment-driven behavior**: Logging verbosity is effectively controlled by the process environment (stdout/stderr visibility) and Django's `DEBUG` flag, which enables verbose traceback rendering but does not change log routing.
- **Celery tasks**: Background tasks run via Celery (configured in `config/settings/base.py`) inherit the same unconfigured logging, so task logs appear on the worker's stdout/stderr alongside any `print` output.