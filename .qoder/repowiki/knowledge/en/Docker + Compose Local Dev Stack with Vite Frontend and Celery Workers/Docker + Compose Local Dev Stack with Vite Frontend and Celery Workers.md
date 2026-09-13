---
kind: build_system
name: Docker + Compose Local Dev Stack with Vite Frontend and Celery Workers
category: build_system
scope:
    - '**'
source_files:
    - docker-compose.yml
    - compose/local/django/Dockerfile
    - compose/local/django/entrypoint.sh
    - compose/local/django/start.sh
    - compose/local/django/start-celeryworker
    - compose/local/django/start-celerybeat
    - requirements/base.txt
    - requirements/local.txt
    - requirements/production.txt
    - scripts/_native_env.sh
    - scripts/run_backend.sh
    - scripts/run_celery_worker.sh
    - scripts/run_celery_beat.sh
    - scripts/run_frontend.sh
    - frontend/package.json
    - config/settings/base.py
---

## Build & Artifact Management Overview

The FinanceAnalysis platform is a Django 6 backend paired with a React/Vite frontend, orchestrated via Docker Compose for local development. There is no CI/CD pipeline in this repository; the build system centers on containerized local development, shell helper scripts for native environments, and separate npm-based frontend builds.

### 1. Systems and Tools Used

- **Containerization**: Single `compose/local/django/Dockerfile` based on `python:3.12-slim-bullseye`, used by all three services (Django, Celery worker, Celery Beat) via `docker-compose.yml`. The image installs TA-Lib C library from source, then installs Python dependencies from `requirements/local.txt`.
- **Dependency management**: Three pip requirement files under `requirements/`: `base.txt` pins core packages (Django 6.0.1, DRF, Channels, Celery 5.4, Redis, pandas, LightGBM, PyTorch, MLflow, drf-spectacular), while `local.txt` and `production.txt` both just re-export `base.txt` via `-r base.txt` — environment-specific overrides are not currently split out.
- **Orchestration**: `docker-compose.yml` defines three services (`django`, `celery_worker`, `celery_beat`) sharing the same image and `.env` file, each running a different entrypoint command (`/start`, `/start-celeryworker`, `/start-celerybeat`).
- **Native dev scripts**: Bash/PowerShell helpers under `scripts/` (`run_backend.sh`, `run_celery_worker.sh`, `run_celery_beat.sh`, `run_frontend.sh`, plus `.ps1` equivalents) use a shared `_native_env.sh` that auto-detects WSL vs Windows, resolves the correct `.venv/bin` vs `.venv/Scripts` path, validates the Python/Celery binaries exist, sources `.env`, and sets default `DJANGO_SETTINGS_MODULE=config.settings.local`, `CELERY_BROKER_URL=redis://localhost:6379/0`, and `CELERY_RESULT_BACKEND`.
- **Frontend build**: Standalone Vite + TypeScript project under `frontend/`. Build commands are defined in `frontend/package.json` (`dev`, `build`, `test`, `lint`, `preview`). The build compiles TypeScript then runs `vite build`; artifacts land in `frontend/dist/`.
- **Static assets**: Django's `collectstatic` collects into `staticfiles/` at runtime (admin, DRF browsable API, modeltranslation, app static dirs). The pre-built `staticfiles/` directory is committed to the repo.

### 2. Key Files

- `docker-compose.yml` — service definitions, port mapping (8000), volume mounts (`.` → `/app`), env file injection.
- `compose/local/django/Dockerfile` — Python 3.12 base, TA-Lib C build, pip install of `requirements/local.txt`, copies entrypoint/start scripts.
- `compose/local/django/entrypoint.sh` — runs `manage.py migrate` and `collectstatic --noinput` before exec-ing the CMD.
- `compose/local/django/start.sh`, `start-celeryworker`, `start-celerybeat` — thin wrappers around entrypoint that launch the appropriate process.
- `requirements/base.txt` — pinned dependency manifest (Django 6.0.1, DRF 3.15.1, Channels 4.1.0, Celery 5.4.0, etc.).
- `scripts/_native_env.sh` — cross-platform (.venv resolution, WSL guard, .env sourcing, Celery broker defaults).
- `scripts/run_*.sh` / `scripts/run_*.ps1` — per-process launchers for backend, Celery worker, Celery Beat, and frontend.
- `frontend/package.json` — Vite build/dev/test/lint scripts, TypeScript + Vitest toolchain.
- `config/settings/base.py` — centralizes Celery queues (`ops`, `backtest`, `train-lightgbm`, `train-lstm`), scheduled tasks via `django_celery_beat.DatabaseScheduler`, DRF settings, rate limits, and environment-driven config via `django-environ`.

### 3. Architecture and Conventions

- **Single-image multi-service compose**: All three processes (Django ASGI/WSGI server, Celery worker, Celery Beat scheduler) share one Docker image and differ only in their command. This keeps the build surface small but means environment differences must be handled via settings/env rather than separate images.
- **Entrypoint-first startup**: The container entrypoint always migrates the database and collects static files before launching the requested command, ensuring schema and assets are ready regardless of which service starts first.
- **Environment-driven configuration**: `django-environ` reads from `.env` (when `DJANGO_READ_DOT_ENV_FILE=True`) and OS env vars. Settings like `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, `DJANGO_SECRET_KEY`, and data provider URLs are all externalized.
- **Celery task routing convention**: Tasks are routed to dedicated queues by name pattern in `CELERY_TASK_ROUTES` (e.g., `apps.backtest.tasks.*` → `backtest` queue, `apps.prediction.tasks_lightgbm.*` → `train-lightgbm`), with time limits enforced globally (`TASK_TIME_LIMIT=5*60`, `SOFT_TIME_LIMIT=60`).
- **Scheduled jobs as code**: All recurring backfills, syncs, alert checks, and prediction generations are declared in `CELERY_BEAT_SCHEDULE` using `crontab()` expressions — no external scheduler UI is required.
- **Separate frontend build**: The React dashboard is built independently from the Django backend. There is no webpack/Django template integration; the frontend is a standalone Vite SPA served separately (default `FRONTEND_URL=http://localhost:3000`).
- **Model artifacts are versioned directories**: Trained models live under `models/lightgbm/<horizon>-<model>-<date>[/<variant>]` and `models/lstm/<model>-<date>[/<variant>]`, each containing `model.pkl`/`.pt`, `scaler.pkl`, `calibrator.pkl`, and `metadata.json` — treated as immutable artifacts consumed by prediction tasks.

### 4. Conventions and Constraints

- **Python virtual environment location**: Scripts expect `.venv` at the project root; on Windows it uses `.venv/Scripts`, on Linux/WSL `.venv/bin`. The `_native_env.sh` script enforces that the detected Python binary is executable and exits with an error if missing.
- **WSL guard**: If the project root is mounted from Windows (`/mnt/*`) inside WSL, scripts refuse to run and instruct cloning into the WSL ext4 filesystem. Conversely, if a Windows `.venv` is found in WSL, scripts exit asking to recreate it natively.
- **Celery broker/backend defaults**: When not set, `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` default to `redis://localhost:6379/0`, so a local Redis instance is required for native development.
- **Settings module default**: `DJANGO_SETTINGS_MODULE` defaults to `config.settings.local` when not provided, enabling local-only settings without explicit env setup.
- **No production Docker image**: Only a local development image exists (`finance_analysis_django`). There is no multi-stage production Dockerfile, no `Dockerfile.prod`, and no registry push step in the repo.
- **No Makefile or top-level build orchestration**: All orchestration lives in shell scripts and `docker-compose.yml`; there is no `Makefile`.
- **Frontend build is decoupled**: The backend build does not invoke `npm build`; the frontend must be built separately via `npm run build` in `frontend/`. No integration step bundles the Vite output into Django's `staticfiles/`.
- **Database migrations are applied automatically**: Every container start runs `manage.py migrate`, so schema changes take effect on restart without a separate migration step.