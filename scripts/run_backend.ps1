Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\_native_env.ps1"

$DjangoBind = if (-not [string]::IsNullOrWhiteSpace($env:DJANGO_BIND)) {
  $env:DJANGO_BIND
} else {
  '0.0.0.0:8000'
}

& $PythonBin manage.py runserver $DjangoBind