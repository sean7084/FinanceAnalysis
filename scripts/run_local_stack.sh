#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICES=(
  "backend|bash|$SCRIPT_DIR/run_backend.sh"
  "celery-worker|bash|$SCRIPT_DIR/run_celery_worker.sh"
  "celery-beat|bash|$SCRIPT_DIR/run_celery_beat.sh"
  "frontend|bash|$SCRIPT_DIR/run_frontend.sh"
)

prefix_stream() {
  local name="$1"
  while IFS= read -r line || [[ -n "$line" ]]; do
    printf '[%s] %s\n' "$name" "$line"
  done
}

check_stack() {
  local service command program

  for service_def in "${SERVICES[@]}"; do
    IFS='|' read -r service command program <<<"$service_def"
    if [[ ! -f "$program" ]]; then
      echo "Missing launcher for $service at $program." >&2
      exit 1
    fi
    if ! command -v "$command" >/dev/null 2>&1; then
      echo "Required command '$command' for $service is not available." >&2
      exit 1
    fi
  done

  echo "[stack] launchers and required runtimes are available."
}

if [[ "${1:-}" == "--check" ]]; then
  check_stack
  exit 0
fi

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

  (
    "$command" "$program" 2>&1 | prefix_stream "$name"
  ) &

  pids+=("$!")
  names+=("$name")
  echo "[stack] started $name"
}

trap cleanup INT TERM EXIT

echo "[stack] starting local development stack..."
for service_def in "${SERVICES[@]}"; do
  IFS='|' read -r service command program <<<"$service_def"
  start_service "$service" "$command" "$program"
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