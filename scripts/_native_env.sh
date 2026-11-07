#!/usr/bin/env bash
set -euo pipefail

is_wsl() {
  [[ -n "${WSL_DISTRO_NAME:-}" ]] && return 0
  grep -qi microsoft /proc/version 2>/dev/null
}

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IS_WSL=0
if is_wsl; then
  IS_WSL=1
fi

if [[ "$IS_WSL" -eq 1 && "$PROJECT_ROOT" == /mnt/* ]]; then
  echo "WSL detected but project root is on a Windows mount: $PROJECT_ROOT" >&2
  echo "Clone the repository into the WSL ext4 filesystem before running backend workloads." >&2
  exit 1
fi

if [[ "$IS_WSL" -eq 1 ]]; then
  DEFAULT_VENV_BIN="$PROJECT_ROOT/.venv/bin"
elif [[ -d "$PROJECT_ROOT/.venv/Scripts" ]]; then
  DEFAULT_VENV_BIN="$PROJECT_ROOT/.venv/Scripts"
else
  DEFAULT_VENV_BIN="$PROJECT_ROOT/.venv/bin"
fi

VENV_BIN="${VENV_BIN:-$DEFAULT_VENV_BIN}"
PYTHON_BIN="${PYTHON_BIN:-$VENV_BIN/python}"
CELERY_BIN="${CELERY_BIN:-$VENV_BIN/celery}"

# Exported, not merely assigned. run_backend.sh and verify_local_stack.sh invoke
# _check_migrations.sh as a CHILD bash, and that script asserts these two with
# ${PYTHON_BIN:?}; unexported assignments stop at the process boundary, the child
# aborts, the caller's set -e aborts with it, and runserver is never exec'd. The
# backend died on every start through run_backend.sh for exactly that reason, and
# nothing surfaced it because the pipeline wrapper reported the status of its last
# element rather than the service's.
export PROJECT_ROOT PYTHON_BIN CELERY_BIN VENV_BIN

if [[ ! -x "$PYTHON_BIN" && -x "$VENV_BIN/python.exe" ]]; then
  PYTHON_BIN="$VENV_BIN/python.exe"
fi

if [[ ! -x "$CELERY_BIN" && -x "$VENV_BIN/celery.exe" ]]; then
  CELERY_BIN="$VENV_BIN/celery.exe"
fi

if [[ "$IS_WSL" -eq 1 && ( "$PYTHON_BIN" == *.exe || "$CELERY_BIN" == *.exe ) ]]; then
  echo "WSL detected but Windows executables were selected from $VENV_BIN." >&2
  echo "Recreate .venv inside the WSL clone and use .venv/bin for backend workloads." >&2
  exit 1
fi

if [[ "$IS_WSL" -eq 1 && -d "$PROJECT_ROOT/.venv/Scripts" && ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  echo "WSL detected but only a Windows virtual environment was found at $PROJECT_ROOT/.venv." >&2
  echo "Recreate .venv inside the WSL clone before running backend workloads." >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python runtime not found at $PYTHON_BIN. Create or activate .venv first." >&2
  exit 1
fi

if ! "$PYTHON_BIN" --version >/dev/null 2>&1; then
  if [[ "$IS_WSL" -eq 1 ]]; then
    echo "Python runtime at $PYTHON_BIN is not usable. The virtual environment may have been copied from Windows; recreate .venv inside WSL or set PYTHON_BIN explicitly." >&2
  else
    echo "Python runtime at $PYTHON_BIN is not usable. The virtual environment may have been copied from Linux; recreate .venv on Windows or set PYTHON_BIN explicitly." >&2
  fi
  exit 1
fi

if [[ ! -x "$CELERY_BIN" ]]; then
  echo "Celery runtime not found at $CELERY_BIN. Install requirements/local.txt into .venv first." >&2
  exit 1
fi

if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

export CELERY_BROKER_URL="${CELERY_BROKER_URL:-redis://localhost:6379/0}"
export CELERY_RESULT_BACKEND="${CELERY_RESULT_BACKEND:-$CELERY_BROKER_URL}"

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.local}"
export DJANGO_READ_DOT_ENV_FILE="${DJANGO_READ_DOT_ENV_FILE:-True}"

# SPA asset mode is decided in config/settings/base.py (DJANGO_VITE_DEV_MODE,
# default False). Set it to True in .env for a Django-served HMR session.

cd "$PROJECT_ROOT"