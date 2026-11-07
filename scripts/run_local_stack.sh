#!/usr/bin/env bash
set -euo pipefail

# The teardown loop names the service that finished with `wait -n -p`, which only
# exists from bash 5.1. Failing here says so; failing later would look like a
# mysterious stack that stops itself.
if ((BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 1))); then
  echo "[stack] needs bash >= 5.1 (wait -n -p); found ${BASH_VERSION:-unknown}." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Service tuples are "name|command|program|env": the fourth field carries
# per-service environment (used to give each Celery worker its own queue list);
# an empty fourth field means no extra env.
#
# The backend, its Celery workers and beat are always required. The frontend is
# not a service any more: Django serves the built SPA itself, so there is one port
# and no proxy in front of the API. Set STACK_FRONTEND=1 to bring Vite back with
# HMR when working on frontend/src, which needs npm.
#
# Set STACK_ALL_QUEUES=0 to start only the `ops` worker and skip the backtest, train and
# ETF workers. See docs/how-to/wsl-services.md.
NPM_BIN="${NPM_BIN:-npm}"
STACK_ALL_QUEUES="${STACK_ALL_QUEUES:-1}"
# The SPA is served by Django on :8000 from frontend/dist by default. Vite is only
# started for HMR sessions, and only when npm exists to run it.
STACK_FRONTEND="${STACK_FRONTEND:-0}"

# SERVICES is the resolved list actually started; populated by build_services.
SERVICES=()

# The train queue runs the LightGBM/LSTM retrains. Concurrency is pinned because a second
# concurrent LSTM needs its own GPU memory: 12GB VRAM is the binding constraint, not how
# often the card is free. Overridable for a CPU-only host.
STACK_TRAIN_CONCURRENCY="${STACK_TRAIN_CONCURRENCY:-2}"
# Set STACK_SKIP_BEAT=1 to start everything except the scheduler (used when a beat is already
# running elsewhere and only the rest of the stack is needed).
STACK_SKIP_BEAT="${STACK_SKIP_BEAT:-0}"

# Bound on the TERM-then-KILL teardown. Celery escalates warm-to-cold shutdown on
# its own worker_shutdown_timeout (unset here, so the 10s default) and beat needs
# a few seconds to unlink its pidfile, so this has to outlast both.
STACK_STOP_TIMEOUT="${STACK_STOP_TIMEOUT:-15}"
# Budget for the API to answer before an opt-in Vite is started. Same figure as
# restart_cold_stack.sh's COLD_READY_TIMEOUT, so the two cold paths wait alike.
STACK_READY_TIMEOUT="${STACK_READY_TIMEOUT:-120}"
STACK_LOG_DIR="${STACK_LOG_DIR:-$PROJECT_ROOT/tmp/cold_stack}"
CELERY_BEAT_PID_FILE="${CELERY_BEAT_PID_FILE:-$PROJECT_ROOT/celerybeat.pid}"
API_BASE="${API_BASE:-http://localhost:8000/api/v1}"

# Ports are checked before anything starts, because a leftover listener on either
# one poisons the attempt: Vite runs with strictPort and aborts within a second
# when 5173 is held, and that abort used to take the whole stack down with it.
BACKEND_PORT="${DJANGO_BIND:-0.0.0.0:8000}"
BACKEND_PORT="${BACKEND_PORT##*:}"
# Hard-coded in frontend/vite.config.ts server.port.
FRONTEND_PORT=5173
# Refusing is the correct default, per docs/how-to/wsl-services.md -- a stale
# stack is not a lock to work around. Set 1 to reclaim the port instead.
STACK_RECLAIM_PORTS="${STACK_RECLAIM_PORTS:-0}"

# Never signal this group: it is the supervisor's own, and killing it mid-teardown
# is exactly how an interrupted cleanup leaves services behind.
SELF_PGID="$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')"

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

# Listening sockets make no promise about ordering, and a dual-stack listener
# prints two rows for one process, so dedupe. ss omits process info it cannot
# see, which yields an empty result rather than an error.
port_holders() {
  ss -ltnpH "sport = :$1" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u
}

port_descriptor() {
  ps -o etime=,comm= -p "$1" 2>/dev/null | tr -s ' ' | sed 's/^ //'
}

reclaim_port() {
  local port="$1" pid deadline
  for pid in $(port_holders "$port"); do
    echo "[stack] reclaiming port $port from pid $pid ($(port_descriptor "$pid"))."
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true
  done
  deadline=$((SECONDS + 10))
  while ((SECONDS < deadline)); do
    [[ -z "$(port_holders "$port")" ]] && return 0
    sleep 1
  done
  for pid in $(port_holders "$port"); do
    kill -KILL "$pid" 2>/dev/null || true
  done
  sleep 1
  [[ -z "$(port_holders "$port")" ]] || {
    echo "[stack] could not free port $port." >&2
    return 1
  }
}

# refuse_port prints what a caller must do, with the pid and its age, because the
# whole reason this check exists is that a bare port conflict reads as a crash.
refuse_port() {
  local port="$1" pid
  echo "[stack] port $port is already held; starting here would collide with a stack this script cannot stop." >&2
  for pid in $(port_holders "$port"); do
    echo "[stack]   pid $pid ($(port_descriptor "$pid")) -- kill -TERM -- -$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')" >&2
  done
  echo "[stack] stop it first, or set STACK_RECLAIM_PORTS=1 to reclaim the port." >&2
}

# Resolve which services to start. Always: backend, the `ops` worker, and beat.
# When STACK_ALL_QUEUES=1 (the default) also start the backtest, train and ETF workers, so
# queued backtests, LightGBM/LSTM retrains and ETF rotation jobs are consumed with no extra
# steps. The frontend joins only for an HMR session: STACK_FRONTEND=1 and npm present.
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
  if [[ "$STACK_FRONTEND" == "1" ]] && frontend_available; then
    SERVICES+=("frontend|bash|$SCRIPT_DIR/run_frontend.sh|")
  fi
}

# frontend_wanted reports whether this run will actually start Vite, which is what
# decides both the port check and the readiness gate.
frontend_wanted() {
  [[ "$STACK_FRONTEND" == "1" ]] && frontend_available
}

# check_stack takes the resolved SERVICES list and validates it. ``mode`` is "start" or
# "check": a live beat or a held port only blocks a real start, because --check starts
# nothing and the scheduler is expected to be up already on a host running the stack.
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

  # Django serves the SPA from the built bundle, so a missing manifest is not a
  # cosmetic gap: {% vite_asset %} raises on every page and each one answers 500.
  if [[ ! -f "$PROJECT_ROOT/frontend/dist/.vite/manifest.json" ]]; then
    if [[ "$mode" == "check" ]]; then
      echo "[stack] note: frontend/dist has no manifest; the SPA at :8000 would not render."
    else
      echo "[stack] frontend/dist has no manifest; run 'cd frontend && npm run build' first." >&2
      exit 1
    fi
  fi

  local ports=("$BACKEND_PORT")
  if frontend_wanted; then
    ports+=("$FRONTEND_PORT")
  fi
  local port
  for port in "${ports[@]}"; do
    if [[ -n "$(port_holders "$port")" ]]; then
      if [[ "$STACK_RECLAIM_PORTS" == "1" ]]; then
        reclaim_port "$port" || exit 1
      elif [[ "$mode" == "check" ]]; then
        echo "[stack] note: port $port is held; a real start would refuse."
      else
        refuse_port "$port"
        exit 1
      fi
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

  echo "[stack] launchers and runtimes available; SPA served by Django at http://localhost:$BACKEND_PORT/"
  if frontend_wanted; then
    echo "[stack] frontend will start for HMR (STACK_FRONTEND=1) at http://localhost:$FRONTEND_PORT/"
  elif [[ "$STACK_FRONTEND" == "1" ]]; then
    echo "[stack] STACK_FRONTEND=1 but npm not found — no HMR server; the built SPA still serves."
  fi
}

if [[ "${1:-}" == "--check" ]]; then
  build_services
  check_stack check
  exit 0
fi

build_services
check_stack start

# Catch up on the occurrences beat never replayed. Deliberately NOT a SERVICES
# entry: start_service appends to pids, and this script tears the whole stack down
# when a required pid exits (the wait loop below), so a catch-up that finished
# normally would take the backend with it. setsid + disown keeps it out of the
# cleanup trap too -- an accidental kill mid-repair is harmless, because the
# detectors are data-level and the next run re-detects whatever is left.
CATCHUP_PYTHON="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
mkdir -p "$STACK_LOG_DIR"
setsid nohup env -C "$PROJECT_ROOT" "$CATCHUP_PYTHON" manage.py catch_up \
  --trigger startup --max-seconds 1800 \
  >>"$STACK_LOG_DIR/catchup.log" 2>&1 < /dev/null &
disown
echo "[stack] started catchup -> $STACK_LOG_DIR/catchup.log"

pids=()
names=()
LAST_STARTED_PID=""

# signal_groups sends one signal to every process each service started, not just
# the pid this script recorded. The distinction is the whole point: run_backend.sh
# execs into runserver, which then forks the autoreload child that owns the
# listening socket; run_frontend.sh execs into npm, which forks sh -c, which execs
# node. There is no moment at which reading "the real pid" would be correct,
# because the tree changes again after you look. A process group is the only
# handle stable across exec and fork, and it still works once the leader has exited.
# The recorded pid IS the process group id, because start_service verifies that
# setsid gave each service its own group led by that pid. Signal the group directly
# rather than re-deriving it from ps: a group leader that has already exited still
# owns the group, and looking the pgid up from a dead pid returns nothing, which
# silently degrades the group kill back into the per-pid kill that orphans services.
group_alive() {
  ps -e -o pgid= 2>/dev/null | tr -d ' ' | grep -qx "$1"
}

signal_groups() {
  local sig="$1" pid
  for pid in ${pids[@]+"${pids[@]}"}; do
    # Never signal our own group: that would kill the supervisor mid-teardown.
    if [[ "$pid" != "$$" && "$pid" != "$SELF_PGID" ]]; then
      kill "-$sig" -- "-$pid" 2>/dev/null || true
    fi
  done
}

cleanup() {
  local status=$?
  trap - TERM EXIT
  # A second Ctrl+C during Celery's warm shutdown must not abort the teardown: an
  # interrupted teardown is precisely how the orphans this replaces got created.
  trap '' INT

  if [[ ${#pids[@]} -gt 0 ]]; then
    echo "[stack] stopping local development stack..."
    signal_groups TERM
    # Ask each group whether any member still lives, not whether the recorded pid
    # does. The wrapper exits on signal immediately while a Celery worker drains an
    # in-flight task for seconds, and a fixed sleep would either wait far too long
    # or declare the stack stopped while four workers were still running.
    local deadline=$((SECONDS + STACK_STOP_TIMEOUT)) alive=1 pid
    while ((SECONDS < deadline)); do
      alive=0
      for pid in ${pids[@]+"${pids[@]}"}; do
        group_alive "$pid" && alive=1
      done
      ((alive == 0)) && break
      sleep 1
    done
    if ((alive != 0)); then
      echo "[stack] still running after ${STACK_STOP_TIMEOUT}s; escalating to SIGKILL." >&2
    fi
    signal_groups KILL
    wait ${pids[@]+"${pids[@]}"} 2>/dev/null || true
    echo "[stack] stopped."
  fi

  # Beat unlinks its pidfile only on a clean exit. Nothing else reads it
  # (run_celery_beat.sh rm -f's it on start), so this is hygiene: a SIGKILLed beat
  # leaves behind a pid the kernel is free to hand to an unrelated process.
  if [[ -z "$(live_beat_pids)" ]]; then
    rm -f "$CELERY_BEAT_PID_FILE"
  fi

  exit "$status"
}

# wait_service_pgid resolves the group a freshly started service now leads. The
# poll covers the window between fork and the setsid() call, during which the
# process still reports the supervisor's group.
wait_service_pgid() {
  local pid="$1" pgid="" deadline=$((SECONDS + 2))
  while ((SECONDS < deadline)); do
    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')"
    [[ "$pgid" == "$pid" ]] && break
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.05
  done
  printf '%s' "$pgid"
}

start_service() {
  local name="$1"
  local command="$2"
  local program="$3"
  local envspec="$4"
  local log="$STACK_LOG_DIR/$name.log"

  # -o pipefail is not a style choice. A `bash -c` child does not inherit the
  # parent's pipefail, so without it the wrapper's status is the status of the last
  # pipeline element -- the prefixer, which always exits 0. Every service then
  # "exited with status 0", including the ones that crashed, which is why a failed
  # start never said why it failed.
  #
  # The prefixer and the log writer must run INSIDE this session. A reader left
  # outside it blocks forever on the pipe whenever a service survives the teardown,
  # holding the supervisor's own subshell alive with it.
  #
  # envspec is one or more KEY=VALUE tokens separated by spaces (queue lists use
  # commas, never spaces), so unquoted expansion is safe and lets `env` apply each
  # one. It reaches the child positionally rather than by interpolation because the
  # child cannot see this shell's variables, and export -f would leak
  # BASH_FUNC_ entries into npm, celery and python.
  setsid bash -o pipefail -c '
    if [ -n "$4" ]; then
      env $4 "$2" "$3" 2>&1
    else
      "$2" "$3" 2>&1
    fi | tee -a "$5" | sed -u "s|^|[$1] |"
  ' _ "$name" "$command" "$program" "$envspec" "$log" &

  local started_pid="$!"
  pids+=("$started_pid")
  names+=("$name")
  LAST_STARTED_PID="$started_pid"

  # Assert the group exists rather than trust it: if setsid ever did fork, cleanup's
  # negative-pid kill would match nothing and the orphan bug would return with no
  # error anywhere. A service that already exited has no group to assert, and the
  # wait loop reports its status instead, which is the honest failure.
  if kill -0 "$started_pid" 2>/dev/null; then
    local pgid
    pgid="$(wait_service_pgid "$started_pid")"
    if [[ -n "$pgid" && "$pgid" != "$started_pid" ]]; then
      echo "[stack] internal: $name did not get its own process group; refusing to start a stack this script cannot stop." >&2
      exit 1
    fi
  fi
  echo "[stack] started $name${envspec:+ ($envspec)} -> $log"
}

# wait_for_api_ready holds the frontend back until the API has actually answered.
# Polling the endpoint rather than the port matters: runserver's autoreload child
# binds :8000 long before Django, PostgreSQL and Redis can serve, and a 200 from
# the cheap cache_page'd /assets/ is the established proof of all three. It also
# absorbs run_backend.sh's migration pre-flight, which takes seconds.
wait_for_api_ready() {
  local backend_pid="$1" deadline=$((SECONDS + STACK_READY_TIMEOUT)) status=""
  while ((SECONDS < deadline)); do
    if ! kill -0 "$backend_pid" 2>/dev/null; then
      echo "[stack] backend exited before the API answered; not starting the frontend." >&2
      return 1
    fi
    status=$(curl -s -o /dev/null --max-time 10 -w '%{http_code}' "$API_BASE/assets/?page_size=1" 2>/dev/null || echo 000)
    if [[ "$status" == "200" ]]; then
      echo "[stack] API ready ($API_BASE/assets/)."
      return 0
    fi
    sleep 2
  done
  echo "[stack] API not ready within ${STACK_READY_TIMEOUT}s (last status $status); not starting the frontend." >&2
  echo "[stack] the backend and workers are still running; see $STACK_LOG_DIR/backend.log" >&2
  return 1
}

trap cleanup INT TERM EXIT

echo "[stack] starting local development stack..."
backend_pid=""
for service_def in "${SERVICES[@]}"; do
  IFS='|' read -r service command program envspec <<<"$service_def"
  if [[ "$service" == "frontend" ]]; then
    # Vite's proxy turns "Django is not answering yet" into a 502 with an empty
    # body, and the SPA had no retry, so a browser that loaded a few seconds early
    # showed a permanent error on a stack that came up fine afterwards.
    if ! wait_for_api_ready "$backend_pid"; then
      echo "[stack] skipping the frontend." >&2
      continue
    fi
  fi
  start_service "$service" "$command" "$program" "$envspec"
  if [[ "$service" == "backend" ]]; then
    backend_pid="$LAST_STARTED_PID"
  fi
done

# The frontend is optional in the same sense npm already is: build_services skips it
# when npm is absent, and it can be started on its own with run_frontend.sh. Vite's
# strictPort aborts it within a second when a previous run left 5173 held, and the
# old `wait -n` plus EXIT trap then took the backend and four workers down with it,
# which is how a port conflict reached the user looking like a dead backend.
is_optional() { [[ "$1" == "frontend" ]]; }

while :; do
  set +e
  wait -n -p finished_pid ${pids[@]+"${pids[@]}"}
  status=$?
  set -e

  index=-1
  for i in "${!pids[@]}"; do
    if [[ "${pids[$i]}" == "$finished_pid" ]]; then
      index="$i"
      break
    fi
  done
  if ((index < 0)); then
    echo "[stack] reaped unknown pid ${finished_pid:-none}." >&2
    continue
  fi
  name="${names[$index]}"

  if ! is_optional "$name"; then
    echo "[stack] $name exited with status $status. Stopping remaining services." >&2
    exit "$status"
  fi

  echo "[stack] $name exited with status $status; optional, leaving the rest running." >&2
  echo "[stack] fix the cause and start it separately: $SCRIPT_DIR/run_frontend.sh" >&2
  # An already-reaped pid handed back to wait -n returns immediately with 127, so
  # it has to leave the list or this loop spins.
  unset "pids[$index]" "names[$index]"
  pids=(${pids[@]+"${pids[@]}"})
  names=(${names[@]+"${names[@]}"})
  if [[ ${#pids[@]} -eq 0 ]]; then
    echo "[stack] nothing left running." >&2
    exit "$status"
  fi
done