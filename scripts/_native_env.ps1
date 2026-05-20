Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-FirstExistingPath {
  param(
    [string[]]$Candidates
  )

  foreach ($candidate in $Candidates) {
    if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -Path $candidate)) {
      return $candidate
    }
  }

  return $null
}

function Import-DotEnvFile {
  param(
    [string]$Path
  )

  if (-not (Test-Path -Path $Path -PathType Leaf)) {
    return
  }

  foreach ($line in Get-Content -Path $Path) {
    $trimmed = $line.Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith('#')) {
      continue
    }

    $separator = $trimmed.IndexOf('=')
    if ($separator -lt 1) {
      continue
    }

    $name = $trimmed.Substring(0, $separator).Trim()
    $value = $trimmed.Substring($separator + 1).Trim().Trim("`r")
    if ($value.Length -ge 2) {
      $quotedWithDoubleQuotes = $value.StartsWith('"') -and $value.EndsWith('"')
      $quotedWithSingleQuotes = $value.StartsWith("'") -and $value.EndsWith("'")
      if ($quotedWithDoubleQuotes -or $quotedWithSingleQuotes) {
        $value = $value.Substring(1, $value.Length - 2)
      }
    }

    Set-Item -Path "Env:$name" -Value $value
  }
}

function Set-EnvDefault {
  param(
    [string]$Name,
    [string]$Value
  )

  if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Name))) {
    Set-Item -Path "Env:$Name" -Value $Value
  }
}

function Test-ExecutableWorks {
  param(
    [string]$Executable,
    [string[]]$Arguments = @('--version')
  )

  if ([string]::IsNullOrWhiteSpace($Executable) -or -not (Test-Path -Path $Executable)) {
    return $false
  }

  try {
    & $Executable @Arguments *> $null
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  }
}

function Invoke-CeleryCommand {
  param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
  )

  if (-not [string]::IsNullOrWhiteSpace($CeleryBin) -and (Test-Path -Path $CeleryBin)) {
    & $CeleryBin @Arguments
    return
  }

  & $PythonBin -m celery @Arguments
}

function Get-DatabaseEndpoint {
  if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) {
    return @{
      Host = 'localhost'
      Port = '5432'
    }
  }

  $uri = [Uri]$env:DATABASE_URL
  return @{
    Host = if ([string]::IsNullOrWhiteSpace($uri.Host)) { 'localhost' } else { $uri.Host }
    Port = if ($uri.Port -gt 0) { [string]$uri.Port } else { '5432' }
  }
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Import-DotEnvFile -Path (Join-Path $ProjectRoot '.envs\.local')

$VenvBin = if (-not [string]::IsNullOrWhiteSpace($env:VENV_BIN)) {
  $env:VENV_BIN
} else {
  Get-FirstExistingPath -Candidates @(
    (Join-Path $ProjectRoot '.venv\Scripts'),
    (Join-Path $ProjectRoot '.venv\bin')
  )
}

if ([string]::IsNullOrWhiteSpace($VenvBin)) {
  $VenvBin = Join-Path $ProjectRoot '.venv\Scripts'
}

$PythonBin = if (-not [string]::IsNullOrWhiteSpace($env:PYTHON_BIN)) {
  $env:PYTHON_BIN
} else {
  Get-FirstExistingPath -Candidates @(
    (Join-Path $VenvBin 'python.exe'),
    (Join-Path $VenvBin 'python')
  )
}

if ([string]::IsNullOrWhiteSpace($PythonBin) -or -not (Test-Path -Path $PythonBin)) {
  throw "Python runtime not found. Create or activate .venv first."
}

if (-not (Test-ExecutableWorks -Executable $PythonBin)) {
  throw "Python runtime at $PythonBin is not usable. The virtual environment may have been copied from Linux; recreate .venv on Windows or set PYTHON_BIN explicitly."
}

$CeleryBin = if (-not [string]::IsNullOrWhiteSpace($env:CELERY_BIN)) {
  $env:CELERY_BIN
} else {
  Get-FirstExistingPath -Candidates @(
    (Join-Path $VenvBin 'celery.exe'),
    (Join-Path $VenvBin 'celery')
  )
}

if (-not [string]::IsNullOrWhiteSpace($CeleryBin) -and -not (Test-ExecutableWorks -Executable $CeleryBin)) {
  $CeleryBin = $null
}

Set-EnvDefault -Name 'CELERY_BROKER_URL' -Value 'redis://localhost:6379/0'
Set-EnvDefault -Name 'CELERY_RESULT_BACKEND' -Value $env:CELERY_BROKER_URL
Set-EnvDefault -Name 'DJANGO_SETTINGS_MODULE' -Value 'config.settings.local'
Set-EnvDefault -Name 'DJANGO_READ_DOT_ENV_FILE' -Value 'True'

Set-Location -Path $ProjectRoot