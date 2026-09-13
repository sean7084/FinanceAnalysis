---
kind: dependency_management
name: Dual Python/Node Dependency Management with Locked Lockfiles and Dockerized Builds
category: dependency_management
scope:
    - '**'
source_files:
    - requirements/base.txt
    - requirements/local.txt
    - requirements/production.txt
    - compose/local/django/Dockerfile
    - frontend/package.json
    - frontend/package-lock.json
    - scripts/_native_env.sh
---

## What system/approach is used

The repository manages dependencies for two separate stacks:

- **Python backend (Django + Celery)**: Uses `pip` with a layered `requirements/*.txt` file strategy. There is no `Pipfile`, `pyproject.toml`, or virtualenv manager — only plain `requirements.txt` files plus a host `.venv` directory.
- **Frontend (React + Vite)**: Uses `npm` with `package.json` and a committed `package-lock.json` lockfile under `frontend/`.

There is no vendoring of third-party Python packages; all Python wheels are installed from PyPI at build time. The frontend does not use `yarn.lock` or `pnpm-lock.yaml`; it exclusively uses npm's lockfile.

## Key files and packages

- `requirements/base.txt` — the single source of truth for backend dependencies. It pins versions for core libraries (`django==6.0.1`, `djangorestframework==3.15.1`, `celery==5.4.0`, `channels==4.1.0`, `channels-redis==4.2.0`, `django-celery-beat`, `pandas==2.3.3`, `drf-spectacular==0.27.2`) while leaving data-science / market-data packages unpinned (`akshare`, `tushare`, `numpy`, `TA-Lib`, `lightgbm`, `torch`, `scikit-learn`, `mlflow`).
- `requirements/local.txt` and `requirements/production.txt` — both simply include `-r base.txt`, so there is currently no environment-specific override layer in practice.
- `compose/local/django/Dockerfile` — installs the C-level `ta-lib` library via `apt-get` and then runs `pip install -r /app/requirements/local.txt` to install Python deps into the container image.
- `frontend/package.json` — declares runtime dependencies (`react`, `react-dom`, `react-router-dom`, `recharts`, `lightweight-charts`) and dev dependencies (`vite`, `typescript`, `eslint`, `vitest`, testing libs). All use caret ranges (`^x.y.z`).
- `frontend/package-lock.json` — committed lockfile that pins every transitive dependency to an exact version, providing deterministic frontend builds.
- `scripts/_native_env.sh` — helper that expects a `.venv` created from `requirements/local.txt` and references `CELERY_BIN` inside it.

## Architecture and conventions

1. **Single pinned manifest per stack.** Backend pinning lives entirely in `requirements/base.txt`; local and production environments share it through the `-r base.txt` include pattern in `local.txt` and `production.txt`. This means any new backend dependency must be added to `base.txt`.
2. **Mixed pinning discipline.** Core framework dependencies are pinned to exact versions (e.g., `django==6.0.1`, `celery==5.4.0`), but heavy ML/data packages (`akshare`, `tushare`, `numpy`, `lightgbm`, `torch`, `scikit-learn`, `mlflow`, `TA-Lib`) are left unpinned, allowing pip to resolve the latest compatible version. This is a deliberate convention visible in the file rather than enforced by tooling.
3. **Lockfile-only determinism on the frontend.** The frontend relies on `package-lock.json` (lockfileVersion 3) for reproducible installs. No `node_modules` is committed; `node_modules/` appears in `.gitignore` implicitly via standard patterns. Runtime deps use caret ranges, letting minor/patch updates flow automatically while major bumps require manual edits.
4. **Containerized install as the canonical build path.** The Dockerfile copies `./requirements/` into `/app/requirements/` and runs `pip install -r /app/requirements/local.txt`, making the container image the authoritative place where pinned Python deps are resolved. Local development uses a host `.venv` (see `scripts/_native_env.sh`), but the Dockerfile is what guarantees parity between environments.
5. **No private registry configuration found.** There is no `pip.conf`, `~/.netrc`, `PYPI_URL`, `PIP_INDEX_URL`, `--extra-index-url`, `GITHUB_TOKEN`, or npm `registry=` / `.npmrc` overrides in the checked-in code. Dependencies are expected to come from public PyPI and the default npm registry.
6. **C/C++ build-time deps are baked into the image.** `TA-Lib` is downloaded from SourceForge and compiled during the Docker build (`wget ... ta-lib-0.4.0-src.tar.gz`), so the Python `TA-Lib` wheel depends on this pre-installed system library.

## Conventions and constraints

- **Add backend dependencies only to `requirements/base.txt`** — `local.txt` and `production.txt` are thin wrappers that include it; adding them elsewhere would be silently ignored by the Docker build.
- **Pin critical framework versions exactly** — the existing convention in `base.txt` pins Django, DRF, Channels, Celery, Redis, modeltranslation, and drf-spectacular to exact versions; newer additions should follow this pattern to keep deployments deterministic.
- **ML/data packages may stay unpinned** — the current file leaves `akshare`, `tushare`, `numpy`, `TA-Lib`, `lightgbm`, `torch`, `scikit-learn`, `mlflow` without `==` pins, which allows automatic upgrades but risks drift between environments unless the lockfile (or CI) re-resolves frequently.
- **Frontend dependency changes require updating `package-lock.json`** — because the lockfile is committed, running `npm install` locally and committing the updated `package-lock.json` is the required workflow to propagate dependency changes.
- **Runtime environment variables control dependency resolution** — the Dockerfile sets `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1`; no `PIP_*` environment variables are set, so pip uses its defaults (public PyPI).
- **Local vs. production parity is enforced by the Dockerfile**, not by separate requirement files — since both `local.txt` and `production.txt` include `base.txt`, the only way to diverge environments today is to edit those wrapper files or add `PIP_*` env vars in the compose/Docker setup.