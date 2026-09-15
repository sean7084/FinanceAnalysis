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
& $PythonBin "$ProjectRoot\scripts\_stack_probe.py" settings

exit $status
