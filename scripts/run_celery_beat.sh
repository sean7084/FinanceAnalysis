#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_native_env.sh"

CELERY_LOG_LEVEL="${CELERY_LOG_LEVEL:-info}"
CELERY_BEAT_PID_FILE="${CELERY_BEAT_PID_FILE:-$PROJECT_ROOT/celerybeat.pid}"

# Two schedulers each dispatch every entry in CELERY_BEAT_SCHEDULE, so every
# scheduled job runs twice. The pidfile cannot detect that: this script deletes it
# on start, so a newer beat clears the older one's entry and both then look
# stopped. Match the live command line instead, and do it here rather than only in
# run_local_stack.sh, because scripts/restart_cold_stack.sh starts beat directly
# and bypassed a guard that lived in the other script.
if live_beat="$(pgrep -f '[-]A config\.celery beat' 2>/dev/null)"; then
	echo "celery beat is already running (pid ${live_beat//$'\n'/ }); refusing to start a second scheduler." >&2
	exit 1
fi

rm -f "$CELERY_BEAT_PID_FILE"

# Beat's output is the only evidence that a scheduled task was actually published, and
# the callers redirect it into a file (run_local_stack.sh, restart_cold_stack.sh). That
# redirection means stdout is not a tty, so anything reaching it through CPython's own
# io layer waits for a buffer to fill; PYTHONUNBUFFERED makes each record land when it
# is written. stdbuf is NOT the lever here -- it adjusts glibc stdio, and CPython writes
# to the file descriptor directly, so it would have changed nothing.
#
# The banner exists because a file that stops updating is read one of two ways: either
# the scheduler is dead, or this is not the file the live scheduler writes to. Those two
# answers need different fixes and nothing in the log distinguished them, so say which
# process this is and when it started at the top of every block.
export PYTHONUNBUFFERED=1
echo "celery beat starting: pid $$ at $(date -u +%Y-%m-%dT%H:%M:%SZ), pidfile $CELERY_BEAT_PID_FILE, level $CELERY_LOG_LEVEL"

exec "$CELERY_BIN" -A config.celery beat -l "$CELERY_LOG_LEVEL" --scheduler django_celery_beat.schedulers:DatabaseScheduler --pidfile "$CELERY_BEAT_PID_FILE"