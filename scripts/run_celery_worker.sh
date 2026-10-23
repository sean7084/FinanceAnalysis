#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_native_env.sh"

CELERY_LOG_LEVEL="${CELERY_LOG_LEVEL:-info}"
CELERY_WORKER_QUEUES="${CELERY_WORKER_QUEUES:-ops}"
CELERY_WORKER_CONCURRENCY="${CELERY_WORKER_CONCURRENCY:-}"
CELERY_WORKER_NODE_SUFFIX="${CELERY_WORKER_NODE_SUFFIX:-${CELERY_WORKER_QUEUES//,/__}}"
CELERY_WORKER_HOSTNAME="${CELERY_WORKER_HOSTNAME:-${CELERY_WORKER_NODE_SUFFIX}@%h}"

# An unset concurrency means nproc. For the train queues that is wrong by an order
# of magnitude: a second concurrent LSTM needs its own 12 GB of VRAM, so the extra
# slots are OOMs rather than throughput. Pin it here, not in run_local_stack.sh
# alone, because scripts/restart_cold_stack.sh starts workers directly.
if [[ -z "$CELERY_WORKER_CONCURRENCY" && "$CELERY_WORKER_QUEUES" == *train* ]]; then
	CELERY_WORKER_CONCURRENCY="${CELERY_TRAIN_WORKER_CONCURRENCY:-2}"
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

exec "$CELERY_BIN" "${celery_arguments[@]}"