Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\_native_env.ps1"

$status = 0

Write-Output "Project root: $ProjectRoot"
Write-Output "Python: $PythonBin"
Write-Output "Django settings: $env:DJANGO_SETTINGS_MODULE"

$databaseEndpoint = Get-DatabaseEndpoint

$pgIsReady = Get-Command pg_isready -ErrorAction SilentlyContinue
if ($pgIsReady) {
  & $pgIsReady.Source -h $databaseEndpoint.Host -p $databaseEndpoint.Port *> $null
  if ($LASTEXITCODE -ne 0) {
    Write-Error "PostgreSQL is not ready on $($databaseEndpoint.Host):$($databaseEndpoint.Port)"
    $status = 1
  } else {
    Write-Output 'PostgreSQL: ready'
  }
} else {
  Write-Warning 'pg_isready not found; skipping PostgreSQL readiness probe'
}

$redisCli = Get-Command redis-cli -ErrorAction SilentlyContinue
if ($redisCli) {
  & $redisCli.Source -u $env:CELERY_BROKER_URL ping *> $null
  if ($LASTEXITCODE -ne 0) {
    Write-Error "Redis broker check failed for $($env:CELERY_BROKER_URL)"
    $status = 1
  } else {
    Write-Output 'Redis broker: ready'
  }

  & $redisCli.Source -u $env:REDIS_URL ping *> $null
  if ($LASTEXITCODE -ne 0) {
    Write-Error "Redis cache/channels check failed for $($env:REDIS_URL)"
    $status = 1
  } else {
    Write-Output 'Redis cache/channels: ready'
  }
} else {
  Write-Warning 'redis-cli not found; skipping Redis readiness probes'
}

& $PythonBin -c "import celery, django, redis, talib; print('Python dependencies: ok')"
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