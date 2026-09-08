#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_native_env.sh"

status=0

echo "Project root: $PROJECT_ROOT"
echo "Python: $PYTHON_BIN"
echo "Django settings: $DJANGO_SETTINGS_MODULE"

if ! "$PYTHON_BIN" - <<'PY'
import os
import sys
from urllib.parse import urlparse

import psycopg2
import redis


def redact(url):
    """Return a URL safe to print. Never emit credentials, even on failure."""
    try:
        parsed = urlparse(url)
    except Exception:
        return '<unparsable>'
    host = parsed.hostname or '<unparsable>'
    port = f':{parsed.port}' if parsed.port else ''
    # A malformed URL can push credentials into the path; refuse to print those.
    path = parsed.path or ''
    if '@' in path or ':' in path:
        path = '/<redacted>'
    return f'{parsed.scheme or "redis"}://{host}{port}{path}'


status = 0
database_url = os.environ.get('DATABASE_URL', 'postgres://localhost:5432')

try:
    connection = psycopg2.connect(database_url, connect_timeout=5)
except Exception as exc:
    print(f'PostgreSQL check failed for {redact(database_url)}: {exc}', file=sys.stderr)
    status = 1
else:
    connection.close()
    print(f'PostgreSQL: ready ({redact(database_url)})')

for label, url in (
    ('Redis broker', os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')),
    ('Redis cache/channels', os.environ.get('REDIS_URL', 'redis://localhost:6379/1')),
):
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
        client.ping()
    except Exception as exc:
        print(f'{label} check failed for {redact(url)}: {exc}', file=sys.stderr)
        status = 1
    else:
        print(f'{label}: ready ({redact(url)})')

sys.exit(status)
PY
then
  status=1
fi

"$PYTHON_BIN" -c "import celery, django, psycopg2, redis, talib; print('Python dependencies: ok')"
"$PYTHON_BIN" manage.py check
"$PYTHON_BIN" manage.py shell -c "from django.conf import settings; print(f'DATABASE_URL={settings.DATABASES[\"default\"][\"NAME\"]}'); print(f'CELERY_BROKER_URL={settings.CELERY_BROKER_URL}'); print(f'REDIS_URL={settings.CACHES[\"default\"][\"LOCATION\"]}')"

exit "$status"