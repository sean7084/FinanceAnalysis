#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_native_env.sh"

CELERY_LOG_LEVEL="${CELERY_LOG_LEVEL:-info}"
CELERY_WORKER_QUEUES="${CELERY_WORKER_QUEUES:-ops}"
CELERY_WORKER_CONCURRENCY="${CELERY_WORKER_CONCURRENCY:-}"
CELERY_WORKER_PREFETCH_MULTIPLIER="${CELERY_WORKER_PREFETCH_MULTIPLIER:-}"
CELERY_WORKER_MAX_TASKS_PER_CHILD="${CELERY_WORKER_MAX_TASKS_PER_CHILD:-}"
CELERY_WORKER_NODE_SUFFIX="${CELERY_WORKER_NODE_SUFFIX:-${CELERY_WORKER_QUEUES//,/__}}"
CELERY_WORKER_HOSTNAME="${CELERY_WORKER_HOSTNAME:-${CELERY_WORKER_NODE_SUFFIX}@%h}"

normalized_worker_queues="${CELERY_WORKER_QUEUES//[[:space:]]/}"
if [[ "$normalized_worker_queues" == "backtest" ]]; then
	CELERY_WORKER_CONCURRENCY="${CELERY_WORKER_CONCURRENCY:-1}"
	CELERY_WORKER_PREFETCH_MULTIPLIER="${CELERY_WORKER_PREFETCH_MULTIPLIER:-1}"
	CELERY_WORKER_MAX_TASKS_PER_CHILD="${CELERY_WORKER_MAX_TASKS_PER_CHILD:-1}"
fi

celery_arguments=(
	-A config.celery
	worker
	-l "$CELERY_LOG_LEVEL"
	-n "$CELERY_WORKER_HOSTNAME"
)

if [[ -n "$CELERY_WORKER_QUEUES" ]]; then
	celery_arguments+=( -Q "$CELERY_WORKER_QUEUES" )
fi

if [[ -n "$CELERY_WORKER_CONCURRENCY" ]]; then
	celery_arguments+=( --concurrency "$CELERY_WORKER_CONCURRENCY" )
fi

if [[ -n "$CELERY_WORKER_PREFETCH_MULTIPLIER" ]]; then
	celery_arguments+=( --prefetch-multiplier "$CELERY_WORKER_PREFETCH_MULTIPLIER" )
fi

if [[ -n "$CELERY_WORKER_MAX_TASKS_PER_CHILD" ]]; then
	celery_arguments+=( --max-tasks-per-child "$CELERY_WORKER_MAX_TASKS_PER_CHILD" )
fi

exec "$CELERY_BIN" "${celery_arguments[@]}"