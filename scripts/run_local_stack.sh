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
# Set STACK_ALL_QUEUES=0 to start only the `ops` worker and skip the backtest, train and
# ETF workers. See docs/how-to/wsl-services.md.
NPM_BIN="${NPM_BIN:-npm}"
STACK_ALL_QUEUES="${STACK_ALL_QUEUES:-1}"

# SERVICES is the resolved list actually started; populated by build_services.
SERVICES=()

# The train queue runs the LightGBM/LSTM retrains. Concurrency is pinned because a second
# concurrent LSTM needs its own GPU memory: 12GB VRAM is the binding constraint, not how
# often the card is free. Overridable for a CPU-only host.
STACK_TRAIN_CONCURRENCY="${STACK_TRAIN_CONCURRENCY:-2}"
# Set STACK_SKIP_BEAT=1 to start everything except the scheduler (used when a beat is already
# running elsewhere and only the rest of the stack is needed).
STACK_SKIP_BEAT="${STACK_SKIP_BEAT:-0}"

# Beat is singular by construction: two schedulers each dispatch every entry in
# CELERY_BEAT_SCHEDULE, so every scheduled job runs twice. That has happened by running this
# script again while the first stack was still up. A pidfile check cannot catch it --
# run_celery_beat.sh deletes the pidfile on start, so a newer beat clears the older one's entry
# and both then look stopped. Match the live scheduler command line instead.
live_beat_pids() {
  pgrep -f '[-]A config\.celery beat' 2>/dev/null || true
}

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
# When STACK_ALL_QUEUES=1 (the default) also start the backtest, train and ETF workers, so
# queued backtests, LightGBM/LSTM retrains and ETF rotation jobs are consumed with no extra
# steps. The frontend joins only when npm is present.
build_services() {
  SERVICES=(
    "backend|bash|$SCRIPT_DIR/run_backend.sh|"
    "celery-worker-ops|bash|$SCRIPT_DIR/run_celery_worker.sh|CELERY_WORKER_QUEUES=ops"
  )
  if [[ "$STACK_ALL_QUEUES" == "1" ]]; then
    SERVICES+=(
      "celery-worker-backtest|bash|$SCRIPT_DIR/run_celery_worker.sh|CELERY_WORKER_QUEUES=backtest"
      "celery-worker-train|bash|$SCRIPT_DIR/run_celery_worker.sh|CELERY_WORKER_QUEUES=train-lightgbm,train-lstm CELERY_WORKER_CONCURRENCY=$STACK_TRAIN_CONCURRENCY"
      "celery-worker-etf|bash|$SCRIPT_DIR/run_celery_worker.sh|CELERY_WORKER_QUEUES=etf-rotation"
    )
  fi
  if [[ "$STACK_SKIP_BEAT" != "1" ]]; then
    SERVICES+=("celery-beat|bash|$SCRIPT_DIR/run_celery_beat.sh|")
  fi
  if frontend_available; then
    SERVICES+=("frontend|bash|$SCRIPT_DIR/run_frontend.sh|")
  fi
}

# check_stack takes the resolved SERVICES list and validates it. ``mode`` is "start" or
# "check": a live beat only blocks a real start, because --check starts nothing and the
# scheduler is expected to be up already on a host running the stack.
check_stack() {
  local mode="${1:-start}"
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
    queues="ops, backtest, train-lightgbm, train-lstm, etf-rotation"
  fi
  echo "[stack] celery queues covered: $queues"
  echo "[stack] train worker concurrency: $STACK_TRAIN_CONCURRENCY"
  if [[ "$STACK_SKIP_BEAT" == "1" ]]; then
    echo "[stack] celery-beat skipped (STACK_SKIP_BEAT=1): no new scheduled jobs will fire."
  else
    local existing
    existing="$(live_beat_pids | paste -sd' ' -)"
    if [[ -n "$existing" ]]; then
      if [[ "$mode" == "check" ]]; then
        echo "[stack] note: scheduler(s) already live: pid $existing -- a real start would refuse."
      else
        echo "[stack] refusing to start celery-beat: scheduler(s) already live: pid $existing." >&2
        echo "[stack] two beats double-dispatch every scheduled job. Stop that process first," >&2
        echo "[stack] or set STACK_SKIP_BEAT=1 to start the rest of the stack without one." >&2
        exit 1
      fi
    fi
  fi

  if frontend_available; then
    echo "[stack] launchers and runtimes available; frontend will start (npm found)."
  else
    echo "[stack] npm not found — frontend will be skipped."
    echo "[stack] run the SPA on the host instead: cd frontend && npm run dev"
  fi
}

if [[ "${1:-}" == "--check" ]]; then
  build_services
  check_stack check
  exit 0
fi

build_services
check_stack start

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
    # envspec is one or more KEY=VALUE tokens separated by spaces (queue lists use commas,
    # never spaces), so unquoted expansion is safe and lets `env` apply each one.
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