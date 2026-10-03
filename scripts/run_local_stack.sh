#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Service tuples are "name|command|program|env": the fourth field carries
# per-service environment (used to give each Celery worker its own queue list);
# an empty fourth field means no extra env.
#
# The backend, its Celery workers and beat are always required. The frontend is
# optional: it needs npm, which the WSL2 clone does not have (the SPA runs on the
# Windows side instead). Starting the frontend unconditionally used to make
# `wait -n` return on its exit 1, and the EXIT trap then tore the backend down
# with it. Detect npm and skip the frontend when it is absent.
#
# Set STACK_ALL_QUEUES=0 to start only the `ops` worker and skip the backtest and
# train workers. See docs/how-to/wsl-services.md.
NPM_BIN="${NPM_BIN:-npm}"
STACK_ALL_QUEUES="${STACK_ALL_QUEUES:-1}"

# SERVICES is the resolved list actually started; populated by build_services.
SERVICES=()

frontend_available() {
  command -v "$NPM_BIN" >/dev/null 2>&1
}

prefix_stream() {
  local name="$1"
  while IFS= read -r line || [[ -n "$line" ]]; do
    printf '[%s] %s\n' "$name" "$line"
  done
}

# Resolve which services to start. Always: backend, the `ops` worker, and beat.
# When STACK_ALL_QUEUES=1 (the default) also start the backtest and train workers,
# so queued backtests and LightGBM/LSTM retrains are consumed with no extra steps.
# The frontend joins only when npm is present.
build_services() {
  SERVICES=(
    "backend|bash|$SCRIPT_DIR/run_backend.sh|"
    "celery-worker-ops|bash|$SCRIPT_DIR/run_celery_worker.sh|CELERY_WORKER_QUEUES=ops"
  )
  if [[ "$STACK_ALL_QUEUES" == "1" ]]; then
    SERVICES+=(
      "celery-worker-backtest|bash|$SCRIPT_DIR/run_celery_worker.sh|CELERY_WORKER_QUEUES=backtest"
      "celery-worker-train|bash|$SCRIPT_DIR/run_celery_worker.sh|CELERY_WORKER_QUEUES=train-lightgbm,train-lstm"
    )
  fi
  SERVICES+=("celery-beat|bash|$SCRIPT_DIR/run_celery_beat.sh|")
  if frontend_available; then
    SERVICES+=("frontend|bash|$SCRIPT_DIR/run_frontend.sh|")
  fi
}

check_stack() {
  local service command program envspec

  for service_def in "${SERVICES[@]}"; do
    IFS='|' read -r service command program envspec <<<"$service_def"
    if [[ ! -f "$program" ]]; then
      echo "Missing launcher for $service at $program." >&2
      exit 1
    fi
    if ! command -v "$command" >/dev/null 2>&1; then
      echo "Required command '$command' for $service is not available." >&2
      exit 1
    fi
  done

  local queues="ops"
  if [[ "$STACK_ALL_QUEUES" == "1" ]]; then
    queues="ops, backtest, train-lightgbm, train-lstm"
  fi
  echo "[stack] celery queues covered: $queues"

  if frontend_available; then
    echo "[stack] launchers and runtimes available; frontend will start (npm found)."
  else
    echo "[stack] npm not found — frontend will be skipped."
    echo "[stack] run the SPA on the host instead: cd frontend && npm run dev"
  fi
}

if [[ "${1:-}" == "--check" ]]; then
  build_services
  check_stack
  exit 0
fi

build_services
check_stack

pids=()
names=()

cleanup() {
  local status=$?
  trap - INT TERM EXIT

  if [[ ${#pids[@]} -gt 0 ]]; then
    echo "[stack] stopping local development stack..."
    for pid in "${pids[@]}"; do
      kill "$pid" 2>/dev/null || true
    done
    wait "${pids[@]}" 2>/dev/null || true
  fi

  exit "$status"
}

start_service() {
  local name="$1"
  local command="$2"
  local program="$3"
  local envspec="$4"

  (
    # envspec is a single KEY=VALUE token (queue lists use commas, never spaces),
    # so unquoted expansion is safe and lets `env` apply it to the launcher.
    if [[ -n "$envspec" ]]; then
      env $envspec "$command" "$program" 2>&1 | prefix_stream "$name"
    else
      "$command" "$program" 2>&1 | prefix_stream "$name"
    fi
  ) &

  pids+=("$!")
  names+=("$name")
  echo "[stack] started $name${envspec:+ ($envspec)}"
}

trap cleanup INT TERM EXIT

echo "[stack] starting local development stack..."
for service_def in "${SERVICES[@]}"; do
  IFS='|' read -r service command program envspec <<<"$service_def"
  start_service "$service" "$command" "$program" "$envspec"
done

set +e
wait -n "${pids[@]}"
status=$?
set -e

failed_service="unknown"
for index in "${!pids[@]}"; do
  if ! kill -0 "${pids[$index]}" 2>/dev/null; then
    failed_service="${names[$index]}"
    break
  fi
done

echo "[stack] $failed_service exited with status $status. Stopping remaining services." >&2
exit "$status"