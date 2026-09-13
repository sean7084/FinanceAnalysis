---
kind: configuration_system
name: Django Settings Layering with django-environ and .env Files
category: configuration_system
scope:
    - '**'
source_files:
    - config/settings/base.py
    - config/settings/local.py
    - config/settings/production.py
    - config/settings/__init__.py
    - .env.example
    - compose/local/django/.env
    - docs/reference/env.md
    - scripts/_native_env.sh
    - scripts/_native_env.ps1
---

## What system/approach is used

The platform uses a standard Django settings layering strategy combined with the `django-environ` package to load configuration from environment variables, with optional `.env` file support. There are three setting files under `config/settings/`: `base.py` (shared defaults), `local.py` (development overrides), and `production.py` (production overrides). The project root's `config/settings/__init__.py` imports `from .local import *`, making `local.py` the default active profile; production deployments must override this to import `production.py` instead.

Configuration values are read exclusively via `environ.Env()` calls (`env.bool`, `env.int`, `env.float`, `env.list`, `env.db`) in `base.py` and `local.py`. Secrets such as `DJANGO_SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, and `TUSHARE_TOKEN` are injected through environment variables rather than hard-coded.

## Key files and packages

- `config/settings/base.py` — central settings: database, Celery queues/schedules, Redis cache/channel layers, REST framework, SimpleJWT, email, Spectacular/OpenAPI, alerting toggles, data provider backfill parameters, and all env-backed constants.
- `config/settings/local.py` — development-only overrides: `DEBUG = True`, permissive `ALLOWED_HOSTS`, console email backend.
- `config/settings/production.py` — placeholder for production overrides (currently only re-exports base).
- `config/settings/__init__.py` — selects the active profile by importing `local`.
- `.env.example` — template of required/optional environment variables for local setup.
- `compose/local/django/.env` — Docker Compose-specific env file (e.g. `TUSHARE_TOKEN`) mounted into the container.
- `docs/reference/env.md` — auto-generated reference listing every env variable read by settings, its type, default, and source line; regenerated via `python manage.py export_documentation_facts`.
- `scripts/_native_env.sh` / `_native_env.ps1` — shell helpers that source `.env` and export keys like `CELERY_RESULT_BACKEND` for native script execution.

## Architecture and conventions

1. **Layered settings**: All shared configuration lives in `base.py`; environment-specific tweaks go in `local.py` or `production.py`. New settings should be added to `base.py` with sensible defaults and overridden in the appropriate profile.
2. **Environment-first**: Every runtime value is sourced via `environ.Env()`. Defaults are provided inline so missing env vars never crash the app at startup.
3. **Optional `.env` loading**: `DJANGO_READ_DOT_ENV_FILE=True` enables reading the repository-root `.env` file; OS environment variables always take precedence over `.env` values. This is documented in both `base.py` and `docs/reference/env.md`.
4. **Single source of truth for env docs**: `docs/reference/env.md` is generated automatically from the codebase and cross-checked against `.env.example`; it is marked "DO NOT EDIT BY HAND" and lists which variables are consumed by settings versus only by launcher scripts.
5. **Containerized env isolation**: Docker Compose mounts `compose/local/django/.env` into the container separately from the repo `.env`, keeping secrets out of version control.
6. **Feature/data-provider flags as env**: Data pipeline behavior (macro sync providers, news backfill windows/retries/chunk sizes, capital flow lookback days, historical data floor) is controlled entirely through env variables defined in `base.py`, enabling per-deployment tuning without code changes.
7. **Celery queue/routing config**: Queues (`ops`, `backtest`, `train-lightgbm`, `train-lstm`) and task-to-queue routing are declared in `base.py` and driven by `CELERY_BROKER_URL`.

## Conventions and constraints

- **All runtime configuration goes through `environ.Env()`** in `config/settings/*.py`; no direct `os.environ` reads for configuration values.
- **Defaults are mandatory**: Every `env(...)` call supplies a default, ensuring the application starts even when an env var is absent.
- **`.env.example` documents required variables**: Keys like `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_DEBUG`, `DJANGO_READ_DOT_ENV_FILE`, and `TUSHARE_TOKEN` are listed here as the canonical starter set.
- **Profile selection is explicit**: To run production, `config/settings/__init__.py` must be changed to `from .production import *`; the shipped default is `local`.
- **Env documentation is auto-generated**: `docs/reference/env.md` is produced by `python manage.py export_documentation_facts` and reflects every `env()` read in settings plus a check against `.env.example`; any new env var should be added there and reflected in the example file.
- **Secrets are never committed**: `.env` and `compose/local/django/.env` are gitignored; only `.env.example` is tracked.
- **OS env overrides `.env`**: The precedence rule is enforced in `base.py` where `READ_DOT_ENV_FILE` gates `.env` loading after the process environment is already populated.