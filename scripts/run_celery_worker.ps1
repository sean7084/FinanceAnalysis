Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\_native_env.ps1"

$CeleryLogLevel = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_LOG_LEVEL)) {
  $env:CELERY_LOG_LEVEL
} else {
  'info'
}

$CeleryWorkerQueues = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_WORKER_QUEUES)) {
  $env:CELERY_WORKER_QUEUES
} else {
  'ops'
}

$CeleryWorkerNodeSuffix = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_WORKER_NODE_SUFFIX)) {
  $env:CELERY_WORKER_NODE_SUFFIX
} elseif (-not [string]::IsNullOrWhiteSpace($CeleryWorkerQueues)) {
  ($CeleryWorkerQueues -replace '[^A-Za-z0-9_-]+', '__')
} else {
  'ops'
}

$CeleryWorkerHostname = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_WORKER_HOSTNAME)) {
  $env:CELERY_WORKER_HOSTNAME
} else {
  "$CeleryWorkerNodeSuffix@%h"
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

$NormalizedWorkerQueues = ($CeleryWorkerQueues -replace '\s+', '')

if ($NormalizedWorkerQueues -eq 'backtest') {
  if ([string]::IsNullOrWhiteSpace($CeleryWorkerConcurrency)) {
    $CeleryWorkerConcurrency = '1'
  }
  $CeleryWorkerPrefetchMultiplier = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_WORKER_PREFETCH_MULTIPLIER)) {
    $env:CELERY_WORKER_PREFETCH_MULTIPLIER
  } else {
    '1'
  }
  $CeleryWorkerMaxTasksPerChild = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_WORKER_MAX_TASKS_PER_CHILD)) {
    $env:CELERY_WORKER_MAX_TASKS_PER_CHILD
  } else {
    '1'
  }
} else {
  $CeleryWorkerPrefetchMultiplier = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_WORKER_PREFETCH_MULTIPLIER)) {
    $env:CELERY_WORKER_PREFETCH_MULTIPLIER
  } else {
    $null
  }
  $CeleryWorkerMaxTasksPerChild = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_WORKER_MAX_TASKS_PER_CHILD)) {
    $env:CELERY_WORKER_MAX_TASKS_PER_CHILD
  } else {
    $null
  }
}

$celeryArguments = @(
  '-A', 'config.celery',
  'worker',
  '-l', $CeleryLogLevel,
  '-n', $CeleryWorkerHostname,
  '--pool', $CeleryWorkerPool
)

if (-not [string]::IsNullOrWhiteSpace($CeleryWorkerQueues)) {
  $celeryArguments += @('-Q', $CeleryWorkerQueues)
}

if (-not [string]::IsNullOrWhiteSpace($CeleryWorkerConcurrency)) {
  $celeryArguments += @('--concurrency', $CeleryWorkerConcurrency)
}

if (-not [string]::IsNullOrWhiteSpace($CeleryWorkerPrefetchMultiplier)) {
  $celeryArguments += @('--prefetch-multiplier', $CeleryWorkerPrefetchMultiplier)
}

if (-not [string]::IsNullOrWhiteSpace($CeleryWorkerMaxTasksPerChild)) {
  $celeryArguments += @('--max-tasks-per-child', $CeleryWorkerMaxTasksPerChild)
}

Invoke-CeleryCommand $celeryArguments