Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\_native_env.ps1"

$CeleryLogLevel = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_LOG_LEVEL)) {
  $env:CELERY_LOG_LEVEL
} else {
  'info'
}

$CeleryWorkerPool = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_WORKER_POOL)) {
  $env:CELERY_WORKER_POOL
} else {
  'solo'
}

$CeleryWorkerConcurrency = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_WORKER_CONCURRENCY)) {
  $env:CELERY_WORKER_CONCURRENCY
} elseif ($CeleryWorkerPool -eq 'solo') {
  '1'
} else {
  $null
}

$celeryArguments = @(
  '-A', 'config.celery',
  'worker',
  '-l', $CeleryLogLevel,
  '--pool', $CeleryWorkerPool
)

if (-not [string]::IsNullOrWhiteSpace($CeleryWorkerConcurrency)) {
  $celeryArguments += @('--concurrency', $CeleryWorkerConcurrency)
}

Invoke-CeleryCommand $celeryArguments