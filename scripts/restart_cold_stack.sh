#!/usr/bin/env bash
set -euo pipefail

# Discard every cache a page-load request can reach, so the next request is a
# genuine first visit. scripts/measure_page_api_latency.sh calls this before each
# iteration when COLD=1.
#
# The three tiers need three different actions because they live in three
# different places:
#
#   Redis          view payloads, the dashboard summary, backtest run payloads.
#                  Shared across processes, survives deploys, cleared by
#                  cache.clear(). Done through Django rather than redis-cli so the
#                  password in REDIS_URL never lands on a command line and the
#                  cache alias/key prefix from config/settings is respected.
#
#   Python process _TRADING_DATES_CACHE, _PRICE_MAP_CACHE and
#                  _MATRIX_SIGNAL_CACHE in apps/backtest/tasks.py are module-level
#                  OrderedDicts. Nothing reaches them from outside:
#                  clear_backtest_process_caches() runs in the caller's process.
#                  A page-load request runs inference in the Django process, so
#                  restarting Django is what actually discards them. The workers
#                  hold their own copies and run prewarm and reconcile, so they
#                  are restarted too or the warmed payload is built from a surface
#                  that outlived the thing it was supposed to replace.
#
#   Browser        lib/queryCache.ts (60s fresh, 5min stale). curl is stateless,
#                  so every harness iteration is already cold here.
#
# Override the pools with STACK_CELERY_QUEUES; readiness waits with
# COLD_READY_TIMEOUT.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
	set -a
	# shellcheck disable=SC1091
	source "$PROJECT_ROOT/.env"
	set +a
fi

PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
COLD_READY_TIMEOUT="${COLD_READY_TIMEOUT:-120}"
STACK_LOG_DIR="${STACK_LOG_DIR:-$PROJECT_ROOT/tmp/cold_stack}"
API_BASE="${API_BASE:-http://localhost:8000/api/v1}"

# The queue pools this project runs day to day. train-lightgbm and train-lstm
# share a worker, hence the comma.
STACK_CELERY_QUEUES="${STACK_CELERY_QUEUES:-ops backtest train-lightgbm,train-lstm etf-rotation}"

mkdir -p "$STACK_LOG_DIR"

# 1. Redis: the only tier that is shared, so the only one with a clear API.
"$PYTHON_BIN" "$PROJECT_ROOT/manage.py" shell -v 0 -c '
from django.core.cache import cache
cache.clear()
print("redis cache cleared")
' 2>/dev/null || {
	echo "could not clear the Django cache; is the database reachable?" >&2
	exit 1
}

# 2. Stop everything that holds a process-local cache. TERM, not KILL: a worker
#    mid-prewarm should finish the task rather than leave a half-written payload.
pids_for() {
	pgrep -f "$1" 2>/dev/null || true
}

STOP_PATTERNS=("manage.py runserver" "celery -A config.celery worker" "celery -A config.celery beat")
for pattern in "${STOP_PATTERNS[@]}"; do
	for pid in $(pids_for "$pattern"); do
		kill -TERM "$pid" 2>/dev/null || true
	done
done

# Workers spawn children; give the pool a moment to drain before checking.
for _ in $(seq 1 30); do
	remaining=$(pids_for "config.celery \(worker\|beat\)" | wc -l)
	runserver_left=$(pids_for "manage.py runserver" | wc -l)
	((remaining == 0 && runserver_left == 0)) && break
	sleep 1
done

# 3. Bring the stack back on the current tree. Logs go to tmp/ so a failed restart
#    is diagnosable instead of silent -- a cold run against a dead server otherwise
#    records timeouts and looks like a performance finding.
start_detached() {
	local name="$1"
	shift
	local log="$STACK_LOG_DIR/$name.log"
	setsid nohup "$@" >"$log" 2>&1 < /dev/null &
	disown
	echo "  started $name -> $log"
}

start_detached beat env -C "$PROJECT_ROOT" bash scripts/run_celery_beat.sh
for queues in $STACK_CELERY_QUEUES; do
	start_detached "worker-${queues//,/-}" env -C "$PROJECT_ROOT" \
		CELERY_WORKER_QUEUES="$queues" bash scripts/run_celery_worker.sh
done
start_detached runserver env -C "$PROJECT_ROOT" \
	"$PYTHON_BIN" manage.py runserver 0.0.0.0:8000

# 4. Readiness. /assets/ is cheap and cache_page'd, so a 200 here means Django,
#    the database and Redis are all answering -- not just that the port is open.
echo "waiting for the API to answer..."
deadline=$((SECONDS + COLD_READY_TIMEOUT))
while ((SECONDS < deadline)); do
	status=$(curl -s -o /dev/null --max-time 10 -w '%{http_code}' "$API_BASE/assets/?page_size=1" 2>/dev/null || echo 000)
	if [[ "$status" == "200" ]]; then
		echo "stack is cold and ready."
		exit 0
	fi
	sleep 2
done

echo "the API did not become ready within ${COLD_READY_TIMEOUT}s; see $STACK_LOG_DIR" >&2
exit 1
