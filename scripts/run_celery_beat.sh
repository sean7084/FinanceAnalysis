#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_native_env.sh"

CELERY_LOG_LEVEL="${CELERY_LOG_LEVEL:-info}"
CELERY_BEAT_PID_FILE="${CELERY_BEAT_PID_FILE:-$PROJECT_ROOT/celerybeat.pid}"

rm -f "$CELERY_BEAT_PID_FILE"

exec "$CELERY_BIN" -A config.celery beat -l "$CELERY_LOG_LEVEL" --scheduler django_celery_beat.schedulers:DatabaseScheduler --pidfile "$CELERY_BEAT_PID_FILE"