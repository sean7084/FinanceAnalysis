#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_native_env.sh"

status=0

echo "Project root: $PROJECT_ROOT"
echo "Python: $PYTHON_BIN"
echo "Django settings: $DJANGO_SETTINGS_MODULE"

# Service and settings probing lives in _stack_probe.py, shared with
# verify_local_stack.ps1. It used to be inlined here as a heredoc, which meant the URL
# redaction helper existed in three copies across the two scripts and they drifted:
# the summary at the bottom of this file printed CELERY_BROKER_URL and the cache
# LOCATION with no redaction whatsoever, so a correctly configured .env meant the real
# Redis password went to stdout. One implementation now, called from both scripts.
if ! "$PYTHON_BIN" "$PROJECT_ROOT/scripts/_stack_probe.py" services; then
  status=1
fi

"$PYTHON_BIN" -c "import celery, django, psycopg2, redis, talib; print('Python dependencies: ok')"
"$PYTHON_BIN" manage.py check
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/_stack_probe.py" settings

exit "$status"
