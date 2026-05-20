Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\_native_env.ps1"

$FrontendDir = if (-not [string]::IsNullOrWhiteSpace($env:FRONTEND_DIR)) {
  $env:FRONTEND_DIR
} else {
  Join-Path $ProjectRoot 'frontend'
}

$NpmBin = if (-not [string]::IsNullOrWhiteSpace($env:NPM_BIN)) {
  $env:NPM_BIN
} else {
  'npm'
}

if (-not (Test-Path -Path $FrontendDir -PathType Container)) {
  throw "Frontend directory not found at $FrontendDir."
}

if (-not (Test-Path -Path (Join-Path $FrontendDir 'package.json') -PathType Leaf)) {
  throw "package.json not found in $FrontendDir."
}

if (-not (Get-Command $NpmBin -ErrorAction SilentlyContinue)) {
  throw 'npm runtime not found. Install Node.js and npm first.'
}

Push-Location -Path $FrontendDir
try {
  & $NpmBin run dev
} finally {
  Pop-Location
}