#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_native_env.sh"

status=0

echo "Project root: $PROJECT_ROOT"
echo "Python: $PYTHON_BIN"
echo "Django settings: $DJANGO_SETTINGS_MODULE"

readarray -t database_endpoint < <(
  "$PYTHON_BIN" -c "from urllib.parse import urlparse; import os; parsed = urlparse(os.environ.get('DATABASE_URL', 'postgres://localhost:5432')); print(parsed.hostname or 'localhost'); print(parsed.port or 5432)"
)
DB_HOST="${database_endpoint[0]:-localhost}"
DB_PORT="${database_endpoint[1]:-5432}"

if command -v pg_isready >/dev/null 2>&1; then
  if ! pg_isready -h "$DB_HOST" -p "$DB_PORT" >/dev/null; then
    echo "PostgreSQL is not ready on ${DB_HOST}:${DB_PORT}" >&2
    status=1
  else
    echo "PostgreSQL: ready"
  fi
else
  echo "pg_isready not found; skipping PostgreSQL readiness probe" >&2
fi

if command -v redis-cli >/dev/null 2>&1; then
  if ! redis-cli -u "${CELERY_BROKER_URL:-redis://localhost:6379/0}" ping >/dev/null; then
    echo "Redis broker check failed for ${CELERY_BROKER_URL:-redis://localhost:6379/0}" >&2
    status=1
  else
    echo "Redis broker: ready"
  fi
  if ! redis-cli -u "${REDIS_URL:-redis://localhost:6379/1}" ping >/dev/null; then
    echo "Redis cache/channels check failed for ${REDIS_URL:-redis://localhost:6379/1}" >&2
    status=1
  else
    echo "Redis cache/channels: ready"
  fi
else
  echo "redis-cli not found; skipping Redis readiness probes" >&2
fi

"$PYTHON_BIN" -c "import celery, django, redis, talib; print('Python dependencies: ok')"
"$PYTHON_BIN" manage.py check
"$PYTHON_BIN" manage.py shell -c "from django.conf import settings; print(f'DATABASE_URL={settings.DATABASES[\"default\"][\"NAME\"]}'); print(f'CELERY_BROKER_URL={settings.CELERY_BROKER_URL}'); print(f'REDIS_URL={settings.CACHES[\"default\"][\"LOCATION\"]}')"

exit "$status"