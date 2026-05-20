Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\_native_env.ps1"

$CeleryLogLevel = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_LOG_LEVEL)) {
  $env:CELERY_LOG_LEVEL
} else {
  'info'
}

$celeryArguments = @('-A', 'config.celery', 'worker', '-l', $CeleryLogLevel)
Invoke-CeleryCommand $celeryArguments