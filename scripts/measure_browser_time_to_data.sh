#!/usr/bin/env bash
set -euo pipefail

# The browser half of the 3s page-load budget.
#
# scripts/measure_page_api_latency.sh measures the API term with curl and can
# therefore only ever report a server-side floor: its FRONTEND_OVERHEAD_SECONDS
# sat at 0 because nothing had read the app's own time-to-data stamps. This takes
# that reading, in a real browser, the way the plan's Verification step 7
# describes it -- each of the 8 routes in a fresh profile against the production
# build -- without adding a devDependency or a 170MB Chromium download, by using
# the browser already installed on the Windows host through WSL interop.
#
# Cold means the same tiers the API harness resets: Redis emptied, the serving
# process restarted, and a brand-new Chrome user-data-dir per iteration so neither
# an HTTP cache nor lib/queryCache.ts survives. FULL_STACK=1 additionally restarts
# Django on :8000 and every worker pool through restart_cold_stack.sh, which is
# the literal bar in the plan; the wrapper turns it on for cold runs by default.
#
# Env:
#   TT_CHROME      path to a Chrome/Edge/Chromium binary. Without one this refuses
#                  to run rather than reporting browser numbers it did not take --
#                  an assumed overhead term is how the last round of perf work
#                  looked green without being green.
#   UPSTREAM_PORT  prod-asset Django port (default 8001)
#   ITERATIONS     per route (default 1 cold, 3 warm)
#   BUDGET_MS      per-route verdict (default 3000)

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
	set -a
	# shellcheck disable=SC1091
	source "$PROJECT_ROOT/.env"
	set +a
fi

PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
NODE_BIN="${NODE_BIN:-node}"
UPSTREAM_PORT="${UPSTREAM_PORT:-8001}"
LABEL="${LABEL:-cold}"
COLD="${COLD:-1}"
BUDGET_MS="${BUDGET_MS:-3000}"
REPORT_ROOT="${LATENCY_REPORT_ROOT:-$PROJECT_ROOT/reports}"
OUT_DIR="$REPORT_ROOT/browser_tti_$(date +%Y%m%d_%H%M%S)_$LABEL"
SERVER_PID_FILE="$PROJECT_ROOT/tmp/browser_tti_server.pid"
SERVER_LOG="$PROJECT_ROOT/tmp/browser_tti_server.log"
RUN_TAG="qoder-tti-$$"
mkdir -p "$PROJECT_ROOT/tmp" "$OUT_DIR"

if [[ "$COLD" == "1" ]]; then
	FULL_STACK="${FULL_STACK:-1}"
else
	FULL_STACK="${FULL_STACK:-0}"
fi

find_browser() {
	local candidate
	for candidate in \
		"${TT_CHROME:-}" \
		"/mnt/c/Program Files/Google/Chrome/Application/chrome.exe" \
		"/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe" \
		"/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
		"$(command -v google-chrome 2>/dev/null || true)" \
		"$(command -v chromium 2>/dev/null || true)" \
		"$(command -v chromium-browser 2>/dev/null || true)"; do
		if [[ -n "$candidate" && -x "$candidate" ]]; then
			echo "$candidate"
			return 0
		fi
	done
	return 1
}

if ! BROWSER="$(find_browser)"; then
	cat >&2 <<'EOF'
No browser found, so the browser term of the budget cannot be measured.

Point TT_CHROME at a Chrome or Edge binary. Under WSL2 the Windows host usually
has one and interop runs it directly:
  TT_CHROME='/mnt/c/Program Files/Google/Chrome/Application/chrome.exe' \
    bash scripts/measure_browser_time_to_data.sh

Refusing to fall back to an assumed number is the point: FRONTEND_OVERHEAD_SECONDS
is the constant that has been reported as zero for too long.
EOF
	exit 2
fi

# The browser needs a scratch profile it can write. Windows binaries want a
# Windows-style path, so take one from the host's own temp directories.
LINUX_TEMP=""
for candidate in /mnt/c/Users/*/AppData/Local/Temp; do
	if [[ -d "$candidate" && -w "$candidate" ]]; then
		LINUX_TEMP="$candidate"
		break
	fi
done
if [[ -z "$LINUX_TEMP" && -n "${TT_WINDOWS_TEMP:-}" ]]; then
	LINUX_TEMP="/mnt$(echo "${TT_WINDOWS_TEMP#*:}" | tr -d '\r')"
elif [[ -z "$LINUX_TEMP" ]]; then
	echo "no writable Windows temp dir under /mnt/c/Users/*/AppData/Local/Temp; set TT_WINDOWS_TEMP." >&2
	exit 1
fi
WIN_TEMP="$(wslpath -w "$LINUX_TEMP" 2>/dev/null || echo "${LINUX_TEMP/#\/mnt\/c/C:}")"

if [[ ! -f "$PROJECT_ROOT/frontend/dist/.vite/manifest.json" ]]; then
	echo "frontend/dist has no manifest; run 'npm run build' in frontend/ first." >&2
	exit 1
fi

start_prod_server() {
	# Stop the previous run's server and wait for the port to actually be free.
	# Two ways this used to fail a run: the pid file's server was sent TERM and the
	# new one started immediately, so it lost the bind race; and on a loaded machine
	# Django took longer than the old 90 s to answer its first request, which reads
	# out as "never became ready" for a server that was fine two minutes later.
	if [[ -f "$SERVER_PID_FILE" ]] && kill -0 "$(cat "$SERVER_PID_FILE")" 2>/dev/null; then
		kill -TERM "$(cat "$SERVER_PID_FILE")" 2>/dev/null || true
	fi
	local waited=0
	while ss -ltn 2>/dev/null | grep -q ":$UPSTREAM_PORT "; do
		sleep 1
		waited=$((waited + 1))
		if ((waited > 30)); then
			echo "port :$UPSTREAM_PORT is still held after 30s; kill whatever owns it before re-running." >&2
			exit 1
		fi
	done
	DJANGO_VITE_DEV_MODE=False setsid nohup "$PYTHON_BIN" "$PROJECT_ROOT/manage.py" \
		runserver "0.0.0.0:$UPSTREAM_PORT" --noreload >"$SERVER_LOG" 2>&1 < /dev/null &
	echo "$!" >"$SERVER_PID_FILE"
	trap '[[ -f "$SERVER_PID_FILE" ]] && kill -TERM "$(cat "$SERVER_PID_FILE")" 2>/dev/null || true' EXIT
	local deadline=$((SECONDS + 300))
	while ((SECONDS < deadline)); do
		if [[ "$(curl -s -o /dev/null --max-time 10 -w '%{http_code}' \
			"http://127.0.0.1:$UPSTREAM_PORT/api/v1/assets/?page_size=1" || echo 000)" == "200" ]]; then
			break
		fi
		sleep 2
	done
	if ((SECONDS >= deadline)); then
		echo "the prod-asset server on :$UPSTREAM_PORT never became ready within 300s; see $SERVER_LOG" >&2
		exit 1
	fi

	# Dev mode would measure the wrong thing: hundreds of unbundled ESM requests
	# make a cold browser figure meaningless, and DJANGO_VITE_DEV_MODE defaults to
	# DEBUG, so a config change here silently changes what this harness proves.
	# The manifest's own hashed entry chunk is the positive check; Vite's client
	# script is the negative one.
	local html entry
	html="$(curl -s --max-time 15 "http://127.0.0.1:$UPSTREAM_PORT/")"
	entry="$("$PYTHON_BIN" - "$PROJECT_ROOT/frontend/dist/.vite/manifest.json" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
entries = [v.get("file", "") for v in manifest.values() if v.get("isEntry")]
print(entries[0] if entries else "")
PY
)"
	if [[ -n "$html" && "$html" == *"@vite/client"* ]]; then
		echo "the server is serving the dev-mode bundle, so these browser numbers would measure Vite's unbundled module graph rather than the production build." >&2
		exit 1
	fi
	if [[ -z "$html" ]]; then
		echo "the prod-asset server answered nothing for /; see $SERVER_LOG" >&2
		exit 1
	fi
	if [[ -n "$entry" && "$html" != *"${entry##*/}"* ]]; then
		echo "the served HTML does not reference the manifest entry chunk ($entry); the prod build and the server are out of step. Run 'npm run build' in frontend/ and collectstatic." >&2
		exit 1
	fi
}

reset_cold_state() {
	if [[ "$FULL_STACK" == "1" ]]; then
		bash "$PROJECT_ROOT/scripts/restart_cold_stack.sh" >/dev/null
	fi
	"$PYTHON_BIN" "$PROJECT_ROOT/manage.py" shell -v 0 -c '
from django.core.cache import cache
cache.clear()
print("redis cache cleared")
' >/dev/null
	start_prod_server
}

start_prod_server

# A real symbol for the Stock Detail route, taken from the universe the pages
# actually read rather than hardcoded, so the route under test exists.
STOCK_SYMBOL="$("$PYTHON_BIN" - "$UPSTREAM_PORT" <<'PY'
import json, sys, urllib.request
with urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/v1/assets/?page_size=1", timeout=30) as r:
    rows = json.load(r)["results"]
print(rows[0]["symbol"] if rows else "")
PY
)"
if [[ -z "$STOCK_SYMBOL" ]]; then
	echo "no asset rows to build a Stock Detail route from; is the database reachable?" >&2
	exit 1
fi

ROUTES=("/" "/indicator-board" "/stock/$STOCK_SYMBOL" "/macro" "/models" "/backtest" "/alerts" "/settings")
RAW="$OUT_DIR/raw.jsonl"
: >"$RAW"
ITERATIONS="${ITERATIONS:-$([[ "$COLD" == "1" ]] && echo 1 || echo 3)}"

echo "browser:     $BROWSER"
echo "prod build:  http://localhost:$UPSTREAM_PORT (DJANGO_VITE_DEV_MODE=False)"
echo "cold:        $COLD (full stack restart: $FULL_STACK)   iterations per route: $ITERATIONS"
echo "out:         $OUT_DIR"
echo

for route in "${ROUTES[@]}"; do
	for ((i = 1; i <= ITERATIONS; i++)); do
		if [[ "$COLD" == "1" ]]; then
			reset_cold_state
		fi
		# Settings mounts no page-load resource, so it never stamps by design.
		expect=1
		if [[ "$route" == "/settings" ]]; then
			expect=0
		fi
		# `|| true` is not used here: the probe's own exit code is the retry signal,
		# since it exits 1 exactly when it produced no number. Its JSON record, which
		# says *why*, still goes into raw.jsonl.
		#
		# One retry per iteration, because the failure this caught was a Chrome
		# instance that started and never painted -- a hung spawn, not a slow page. In
		# cold mode the retry resets first: an attempt that navigated before hanging
		# left Redis warm, and re-asking without that reset would report a cold page
		# using a warm server.
		probe_rc=1
		for attempt in 1 2; do
			if [[ "$attempt" == "2" && "$COLD" == "1" ]]; then
				reset_cold_state
			fi
			probe_rc=0
			TT_UPSTREAM="http://127.0.0.1:$UPSTREAM_PORT" \
			TT_CHROME="$BROWSER" \
			TT_PROFILE="$WIN_TEMP\\$RUN_TAG-$i-$attempt" \
			TT_EXPECT_STAMP="$expect" \
			TT_DEADLINE_MS="${TT_DEADLINE_MS:-30000}" \
			"$NODE_BIN" "$PROJECT_ROOT/scripts/browser_time_to_data.mjs" --route "$route" --label "$LABEL" --json \
				>"$OUT_DIR/.attempt" 2>/dev/null || probe_rc=$?
			line="$(cat "$OUT_DIR/.attempt")"
			if [[ "$line" != \{* ]]; then
				line="{\"label\":\"$LABEL\",\"route\":\"$route\",\"method\":\"deadline\",\"stamped\":false,\"browserTotalMs\":null,\"openMs\":null,\"waitMs\":null,\"apiWaitMs\":null,\"overheadMs\":null,\"firstContentfulPaintMs\":null,\"note\":null,\"error\":\"probe produced no record\"}"
				probe_rc=1
			fi
			echo "$line" >>"$RAW"
			printf '%-26s %s\n' "$route" "$(echo "$line" | "$PYTHON_BIN" -c '
import json, sys
d = json.load(sys.stdin)
print("total=%sms  open=%sms  wait=%sms  network=%sms  by=%s  err=%s  %s" % (
    d.get("browserTotalMs"), d.get("openMs"), d.get("waitMs"), d.get("apiWaitMs"),
    d.get("method"), d.get("error"), d.get("note") or ""))')"
			rm -rf "$LINUX_TEMP/$RUN_TAG-$i-$attempt" "$OUT_DIR/.attempt" 2>/dev/null || true
			[[ "$probe_rc" == "0" ]] && break
		done
	done
done

summary_rc=0
"$PYTHON_BIN" - "$RAW" "$OUT_DIR" "$LABEL" "$BUDGET_MS" <<'PY' || summary_rc=$?
import csv, json, sys
from collections import OrderedDict

raw, out_dir, label, budget = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
rows = [json.loads(line) for line in open(raw, encoding="utf-8") if line.strip()]

by_route = OrderedDict()
for row in rows:
    by_route.setdefault(row["route"], []).append(row)

with open(f"{out_dir}/raw.csv", "w", newline="", encoding="utf-8") as fh:
    writer = csv.writer(fh)
    writer.writerow(["route", "method", "browser_total_ms", "open_ms", "wait_ms",
                     "api_wait_ms", "overhead_ms", "fcp_ms", "note", "error"])
    for row in rows:
        writer.writerow([row["route"], row.get("method", "deadline"), row["browserTotalMs"],
                         row["openMs"], row["waitMs"], row["apiWaitMs"], row["overheadMs"],
                         row["firstContentfulPaintMs"], row.get("note") or "", row["error"] or ""])

lines = [f"# Browser time-to-data ({label})", "",
         f"Budget: {budget} ms from navigationStart to the page's last first value,",
         "taken in the installed browser against the production build.", "",
         "| route | browser total | how | open (bundle+chunk) | wait (data) | network span | browser term | verdict |",
         "| --- | --- | --- | --- | --- | --- | --- | --- |"]
worst = 0
overheads = []
missing = []
incomplete = {}
fallback = []
for route, samples in by_route.items():
    scored = [s for s in samples if s["browserTotalMs"] is not None]
    failed = len(samples) - len(scored)
    if not scored:
        missing.append(route)
        lines.append(f"| {route} | -- | -- | -- | -- | -- | -- | **no measurement** |")
        continue
    # One sample end to end. Taking the worst total from one probe and the method,
    # open and wait from another is how a warm run reported Settings as `deadline`
    # with another probe's 8.4s shell total -- a row that looked like a measurement
    # and was a mixture of two.
    s = max(scored, key=lambda r: r["browserTotalMs"])
    total = s["browserTotalMs"]
    if failed:
        incomplete[route] = failed
    method = s.get("method", "deadline")
    if method != "stamp":
        fallback.append(f"{route} ({method})")
    # Only stamped routes contribute the constant: a fallback total contains the
    # settle window the probe waited in, and Settings has no page-load request to
    # subtract, so neither is a clean measure of browser-only cost.
    stamped_over = [r["overheadMs"] for r in scored if r["stamped"] and r["overheadMs"] is not None]
    overheads += stamped_over
    worst = max(worst, total)
    verdict = "PASS" if total <= budget else "OVER BUDGET"
    lines.append(f"| {route} | {total} ms | {method} | {s['openMs']} ms | {s['waitMs']} ms | "
                 f"{s['apiWaitMs']} ms | {max(stamped_over) if stamped_over else '--'} ms | {verdict} |")

lines += ["", f"Worst page: {worst} ms against a {budget} ms budget.", ""]
if missing:
    lines.append(
        "Routes with no measurement at all: " + ", ".join(missing) + ". A route that "
        "produced no number is not a pass, so this run exits non-zero rather than "
        "reporting seven pages and calling it the eight."
    )
if incomplete:
    lines.append(
        "Probes that produced no number even after the retry: "
        + ", ".join(f"{r} ({n})" for r, n in incomplete.items())
        + ". Those iterations are in raw.jsonl with their error; the route row above "
        "is the surviving sample, so a route here with fewer usable samples than "
        "ITERATIONS is evidence read at a smaller n than the run asked for."
    )
if fallback:
    lines.append(
        "Routes measured from Resource Timing / first paint instead of the app's own "
        "`data-tti`: " + ", ".join(fallback) + ". These totals add the settle window the "
        "probe waited in, so they overstate rather than understate the wait. Only a route "
        "the wrapper declares makes no page-load request may be measured this way: on a "
        "route that does, a fallback is indistinguishable from a page whose request had "
        "not started yet, so the probe keeps waiting and the run reports no measurement."
    )
if overheads:
    lines += ["",
              f"`FRONTEND_OVERHEAD_SECONDS` for measure_page_api_latency.sh is "
              f"{max(overheads) / 1000:.3f} -- the largest browser-only term across the "
              "stamped routes, i.e. bundle, route chunk, render and post-response paint.",
              "",
              "Browser totals already contain the API wait, so that column and the total",
              "are not additive; the browser term is what to add to a curl p95 when the",
              "two are measured separately."]
open(f"{out_dir}/summary.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("\n".join(lines))
sys.exit(1 if missing else 0)
PY

echo
echo "wrote $OUT_DIR/summary.md"
exit "$summary_rc"
