Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\_native_env.ps1"

$status = 0

Write-Output "Project root: $ProjectRoot"
Write-Output "Python: $PythonBin"
Write-Output "Django settings: $env:DJANGO_SETTINGS_MODULE"

# Probe PostgreSQL and Redis through their Python drivers rather than through
# pg_isready / redis-cli. Neither binary ships with Windows, so shelling out to them
# silently degraded this script into a no-op that still exited 0 -- which is how a
# wrong Redis credential survived undetected while this verifier reported success.
$serviceProbe = @'
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
    port = ':%s' % parsed.port if parsed.port else ''
    # A malformed URL can push credentials into the path; refuse to print those.
    path = parsed.path or ''
    if '@' in path or ':' in path:
        path = '/<redacted>'
    return '%s://%s%s%s' % (parsed.scheme or 'redis', host, port, path)


status = 0

database_url = os.environ.get('DATABASE_URL', 'postgres://localhost:5432')
try:
    connection = psycopg2.connect(database_url, connect_timeout=5)
except Exception as exc:
    print('PostgreSQL check failed for %s: %s' % (redact(database_url), exc), file=sys.stderr)
    status = 1
else:
    connection.close()
    print('PostgreSQL: ready (%s)' % redact(database_url))

for label, url in (
    ('Redis broker', os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')),
    ('Redis cache/channels', os.environ.get('REDIS_URL', 'redis://localhost:6379/1')),
):
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
        client.ping()
    except Exception as exc:
        print('%s check failed for %s: %s' % (label, redact(url), exc), file=sys.stderr)
        status = 1
    else:
        print('%s: ready (%s)' % (label, redact(url)))

sys.exit(status)
'@

& $PythonBin -c $serviceProbe
if ($LASTEXITCODE -ne 0) {
  $status = 1
}

& $PythonBin -c "import celery, django, psycopg2, redis, talib; print('Python dependencies: ok')"
& $PythonBin manage.py check

$settingsProbe = @'
from urllib.parse import urlparse

from django.conf import settings


def redact_url(value):
  parsed = urlparse(value)
  host = parsed.hostname or ''
  port = f':{parsed.port}' if parsed.port else ''
  path = parsed.path or ''
  return f'{parsed.scheme}://{host}{port}{path}'


database = settings.DATABASES['default']
print(f"DATABASE_NAME={database['NAME']}")
print(f"DATABASE_HOST={database['HOST']}")
print(f"DATABASE_PORT={database['PORT']}")
print(f"CELERY_BROKER_URL={redact_url(settings.CELERY_BROKER_URL)}")
print(f"REDIS_URL={redact_url(settings.CACHES['default']['LOCATION'])}")
'@

& $PythonBin manage.py shell -c $settingsProbe

exit $status