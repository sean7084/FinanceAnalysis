Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\_native_env.ps1"

$CeleryLogLevel = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_LOG_LEVEL)) {
  $env:CELERY_LOG_LEVEL
} else {
  'info'
}

$CeleryBeatPidFile = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_BEAT_PID_FILE)) {
  $env:CELERY_BEAT_PID_FILE
} else {
  Join-Path $ProjectRoot 'celerybeat.pid'
}

if (Test-Path -Path $CeleryBeatPidFile) {
  Remove-Item -Path $CeleryBeatPidFile -Force
}

$celeryArguments = @(
  '-A', 'config.celery',
  'beat',
  '-l', $CeleryLogLevel,
  '--scheduler', 'django_celery_beat.schedulers:DatabaseScheduler',
  '--pidfile', $CeleryBeatPidFile
)
Invoke-CeleryCommand $celeryArguments