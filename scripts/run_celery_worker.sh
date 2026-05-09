#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_native_env.sh"

CELERY_LOG_LEVEL="${CELERY_LOG_LEVEL:-info}"

exec "$CELERY_BIN" -A config.celery worker -l "$CELERY_LOG_LEVEL"