Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\_native_env.ps1"

$status = 0

Write-Output "Project root: $ProjectRoot"
Write-Output "Python: $PythonBin"
Write-Output "Django settings: $env:DJANGO_SETTINGS_MODULE"

# Service and settings probing lives in scripts\_stack_probe.py, shared with
# verify_local_stack.sh. It used to be inlined here as two here-strings, which meant
# the URL redaction helper existed in three copies across the two scripts and they
# drifted: the settings probe used a weaker redact_url() without the malformed-path
# guard, so it leaked credentials whenever a mistyped URL pushed them into the path
# component. One implementation now, called from both scripts.
#
# The probes go through the Python drivers rather than pg_isready / redis-cli. Neither
# binary ships with Windows, so shelling out to them silently degraded this script into
# a no-op that still exited 0 -- which is how a wrong Redis credential survived
# undetected while this verifier reported success.
& $PythonBin "$ProjectRoot\scripts\_stack_probe.py" services
if ($LASTEXITCODE -ne 0) {
  $status = 1
}

& $PythonBin -c "import celery, django, psycopg2, redis, talib; print('Python dependencies: ok')"
& $PythonBin manage.py check
# A committed-but-unapplied migration is a startup failure, not a footnote: on 2026-10-25
# four migrations were merged and never applied, every test passed (the test database is
# built from them), and the SPA showed a generic "ETF data failed to load" banner.
$migrationOutput = & $PythonBin manage.py showmigrations --list 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host "Migrations: could not read state (is the database reachable?); skipping the check."
} else {
  $pendingMigrations = @($migrationOutput | Where-Object { $_ -match '^\[ \]' })
  if ($pendingMigrations.Count -gt 0) {
    Write-Host "Pending migrations: the code expects a schema this database does not have."
    $pendingMigrations | ForEach-Object { Write-Host "  $_" }
    Write-Host "Apply them first:  $PythonBin manage.py migrate"
    $status = 1
  } else {
    Write-Host "Migrations: all applied"
  }
}
& $PythonBin "$ProjectRoot\scripts\_stack_probe.py" settings

exit $status
