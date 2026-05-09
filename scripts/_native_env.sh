#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_BIN="${VENV_BIN:-$PROJECT_ROOT/.venv/bin}"
PYTHON_BIN="${PYTHON_BIN:-$VENV_BIN/python}"
CELERY_BIN="${CELERY_BIN:-$VENV_BIN/celery}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python runtime not found at $PYTHON_BIN. Create or activate .venv first." >&2
  exit 1
fi

if [[ ! -x "$CELERY_BIN" ]]; then
  echo "Celery runtime not found at $CELERY_BIN. Install requirements/local.txt into .venv first." >&2
  exit 1
fi

if [[ -f "$PROJECT_ROOT/.envs/.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.envs/.local"
  set +a
fi

export CELERY_BROKER_URL="${CELERY_BROKER_URL:-redis://localhost:6379/0}"
export CELERY_RESULT_BACKEND="${CELERY_RESULT_BACKEND:-$CELERY_BROKER_URL}"

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.local}"
export DJANGO_READ_DOT_ENV_FILE="${DJANGO_READ_DOT_ENV_FILE:-True}"

cd "$PROJECT_ROOT"