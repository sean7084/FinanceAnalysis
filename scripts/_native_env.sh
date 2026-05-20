#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -d "$PROJECT_ROOT/.venv/Scripts" ]]; then
  DEFAULT_VENV_BIN="$PROJECT_ROOT/.venv/Scripts"
else
  DEFAULT_VENV_BIN="$PROJECT_ROOT/.venv/bin"
fi

VENV_BIN="${VENV_BIN:-$DEFAULT_VENV_BIN}"
PYTHON_BIN="${PYTHON_BIN:-$VENV_BIN/python}"
CELERY_BIN="${CELERY_BIN:-$VENV_BIN/celery}"

if [[ ! -x "$PYTHON_BIN" && -x "$VENV_BIN/python.exe" ]]; then
  PYTHON_BIN="$VENV_BIN/python.exe"
fi

if [[ ! -x "$CELERY_BIN" && -x "$VENV_BIN/celery.exe" ]]; then
  CELERY_BIN="$VENV_BIN/celery.exe"
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python runtime not found at $PYTHON_BIN. Create or activate .venv first." >&2
  exit 1
fi

if ! "$PYTHON_BIN" --version >/dev/null 2>&1; then
  echo "Python runtime at $PYTHON_BIN is not usable. The virtual environment may have been copied from Linux; recreate .venv on Windows or set PYTHON_BIN explicitly." >&2
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