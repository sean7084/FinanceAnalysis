#!/usr/bin/env bash
set -euo pipefail

# Times every endpoint the SPA calls on page load and reports p50/p95 per
# endpoint plus a per-page budget. Modelled on smoke_api_check.sh, and like it,
# every request is unauthenticated. Run before and after a change to prove the
# delta.
#
#   ITERATIONS=20 LABEL=warm-before bash scripts/measure_page_api_latency.sh
#   ITERATIONS=3  COLD=1 LABEL=cold-before bash scripts/measure_page_api_latency.sh
#
# The endpoint list is a map of the eight routed pages in App.tsx onto the exact
# query strings their fetch functions build. It goes stale whenever the SPA
# changes a request shape, which is what made the original Dashboard group wrong
# for a release: it kept timing the seven calls `fetchDashboardDataLegacy` makes
# while `fetchDashboardData` had moved to a single `/dashboard/stocks/summary/`.
# apps/core/tests_frontend.py fails CI when a page issues a path missing here.
#
# Raw timings land in $LATENCY_REPORT_ROOT/api_latency_<ts>_<label>/, which
# defaults to the gitignored reports/ tree (same convention as
# validate_data_quality), not docs/reports/ -- that directory is tracked and
# holds narrative reports, not repeated measurement data.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
	set -a
	# shellcheck disable=SC1091
	source "$PROJECT_ROOT/.env"
	set +a
fi

API_BASE="${API_BASE:-http://localhost:8000/api/v1}"
ITERATIONS="${ITERATIONS:-5}"
LABEL="${LABEL:-run}"
REPORT_ROOT="${LATENCY_REPORT_ROOT:-$PROJECT_ROOT/reports}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CURL_TIMEOUT="${CURL_TIMEOUT:-300}"

# COLD=1 discards every cache the page-load path can hit before each iteration,
# so the numbers describe a first visit rather than a warm one. This is the
# configuration the 3s budget is written against, because "slow only on the
# first load" is the failure being fixed.
#
# The three cache tiers have different scopes and each needs its own treatment:
#   - Redis (view payloads, summary, backtest payloads) -> FLUSHDB
#   - the process-local OrderedDicts in apps/backtest/tasks.py
#     (_TRADING_DATES_CACHE, _PRICE_MAP_CACHE, _MATRIX_SIGNAL_CACHE) -> only a
#     process restart reaches them; clear_backtest_process_caches() cannot,
#     because it runs in the caller's process, not the server's
#   - the browser's queryCache (lib/queryCache.ts) -> curl carries no state, so
#     every iteration here is already a cold frontend read
COLD="${COLD:-0}"
# The budget is browser time-to-data; curl can only ever measure the API term of
# it, so this constant is what turns an API floor into a page budget. It stays 0
# until someone reads it off the production build in a browser, and the summary
# says so out loud rather than letting a PASS mean more than it does.
#
# To fill it in, load the built SPA (`npm run preview`, or Django's staticfiles),
# open a route cold, and read what hooks/useApiResource stamped on <body>:
#   copy([document.body.dataset.ttiOpen, document.body.dataset.tti,
#         document.body.dataset.ttiPath].join(' '))
# FRONTEND_OVERHEAD_SECONDS for that route is ttiOpen plus the part of tti that is
# not the request itself (subtract `responseEnd - requestStart` from the matching
# performance.getEntriesByType('resource') entry). tti alone would bill the round
# trip twice, because the view is stamped when its last fetch lands.
FRONTEND_OVERHEAD_SECONDS="${FRONTEND_OVERHEAD_SECONDS:-0}"
COLD_RESET_TIMEOUT="${COLD_RESET_TIMEOUT:-120}"

OUT_DIR="$REPORT_ROOT/api_latency_$(date +%Y%m%d_%H%M%S)_$LABEL"
mkdir -p "$OUT_DIR"

restart_page_load_server() {
	# Restart Django *and* the Celery pools, then wait for readiness. The workers
	# matter because prewarm and reconcile run there, and because their
	# process-local inference caches are part of what "cold" has to discard.
	"${PROJECT_ROOT}/scripts/restart_cold_stack.sh"
}

if [[ "$COLD" == "1" ]]; then
	if [[ ! -x "${PROJECT_ROOT}/scripts/restart_cold_stack.sh" ]]; then
		echo "COLD=1 needs scripts/restart_cold_stack.sh (executable) to restart the stack." >&2
		exit 1
	fi
	echo "Cold mode: Redis flushed + stack restarted before every iteration."
fi

json_get() {
	"$PYTHON_BIN" -c '
import json, sys
key = sys.argv[1]
value = json.load(sys.stdin)
try:
    for part in key.split("."):
        value = value[int(part)] if part.isdigit() else value[part]
except (KeyError, IndexError, TypeError, ValueError):
    print("")
    raise SystemExit(0)
print(json.dumps(value) if isinstance(value, (dict, list)) else value)
' "$1"
}

get_json() {
	curl -fsS --max-time "$CURL_TIMEOUT" "$API_BASE$1"
}

# Discover the identifiers the detail pages need, so the harness measures real
# rows rather than empty result sets.
assets_json=$(get_json '/assets/?page_size=1')
ASSET_ID=$(printf '%s' "$assets_json" | json_get results.0.id)
ASSET_SYMBOL=$(printf '%s' "$assets_json" | json_get results.0.symbol)
BACKTEST_ID=$(get_json '/backtest/?page_size=1' | json_get results.0.id)

if [[ -z "$ASSET_ID" ]]; then
	echo "No assets returned; cannot measure detail pages." >&2
	exit 1
fi

# The Dashboard's real default request: createDefaultDashboardCandidateFilters()
# from frontend/src/lib/dashboardCandidateFilters.ts, sent by fetchDashboardStocks
# through the URLSearchParams order in fetchDashboardStocks. The cache key covers
# these parameters, so a change here is a different cache entry -- that is the
# whole reason the prewarm cannot cover the surface.
DASHBOARD_STOCKS="/dashboard/stocks/?prediction_horizon=7&page_size=120&prediction_source=lightgbm&horizon_days=7&candidate_mode=top_n&up_threshold=0.45&top_n=8&top_n_metric=up_prob_7d&trade_score_scope=independent&trade_score_threshold=1&max_positions=5&use_macro_context=true"

# Stock Detail requests 3000 bars and fetchOhlcvByAsset pins page_size at 1000,
# so pages 2 and 3 go out concurrently with page 1 resolved. `ordering=-date` is
# part of the real URL and therefore part of the query cost; omitting it measures
# a different query than the page makes.
OHLCV_BASE="/ohlcv/?asset=$ASSET_ID&ordering=-date&page_size=1000"

# page|label|path -- page groups sum into the per-page budget. Each group mirrors
# one routed page in frontend/src/App.tsx.
ENDPOINTS=(
	# Dashboard: fetchDashboardData is now a single summary call, and
	# fetchDashboardStocks is the candidate table. The seven calls the legacy
	# helper still makes are not on the load path.
	'Dashboard|summary|/dashboard/stocks/summary/'
	"Dashboard|stocks (candidate filters)|$DASHBOARD_STOCKS"
	# Indicator Board: fetchDashboardStocks with no candidateFilters, so the
	# request skips inference and pays for the wide row over the whole universe.
	'Indicator Board|stocks (no candidate filter)|/dashboard/stocks/?prediction_horizon=7&page_size=120'
	# Stock Detail: fetchAssets(150) for the picker, then the composed five.
	"Stock Detail|asset list|/assets/?page_size=150"
	"Stock Detail|asset search|/assets/?search=$ASSET_SYMBOL"
	"Stock Detail|ohlcv page 1|$OHLCV_BASE"
	"Stock Detail|ohlcv page 2|${OHLCV_BASE}&page=2"
	"Stock Detail|ohlcv page 3|${OHLCV_BASE}&page=3"
	"Stock Detail|prediction|/prediction/$ASSET_SYMBOL/"
	"Stock Detail|lightgbm prediction|/lightgbm-predictions/$ASSET_SYMBOL/"
	'Stock Detail|sentiment|/sentiment/?score_type=ASSET_7D&page_size=5'
	# Macro Context: fetchMacroContexts(30), not the Dashboard's page_size=1.
	'Macro Context|macro contexts|/macro/contexts/?page_size=30'
	'Models|model versions|/prediction-model-versions/?page_size=20'
	'Models|lightgbm models|/lightgbm-models/?page_size=20'
	'Models|ensemble weights|/ensemble-weights/?page_size=20'
	'Models|feature importance|/lightgbm-models/feature-importance-trends/?limit_models=5&top_n=5'
	'Backtest|runs|/backtest/?page_size=100'
	# Alert Center: page_size=20. The old entry measured the Dashboard's 200.
	'Alert Center|alert events|/alert-events/?page_size=20'
	# Settings renders from useI18n alone -- no API call -- so its cost is the SPA
	# shell. Unconditional: a page group that appears only when frontend/dist
	# happens to be built silently drops the count and the run still reports all
	# green. apps/core/tests_frontend.py asserts this group is always present.
	'Settings|SPA document|/'
)

if [[ -n "$BACKTEST_ID" ]]; then
	ENDPOINTS+=("Backtest|trades|/backtest/$BACKTEST_ID/trades/")
	ENDPOINTS+=("Backtest|comparison curve|/backtest/$BACKTEST_ID/comparison_curve/")
else
	echo "note: no backtest runs found; skipping trades/comparison_curve" >&2
fi

# A cold iteration restarts the stack and then waits out real inference, so the
# pages that already pass are not worth paying for repeatedly. ONLY_PAGES narrows
# the run to a comma-separated subset without changing what the full run measures.
if [[ -n "${ONLY_PAGES:-}" ]]; then
	filtered=()
	for entry in "${ENDPOINTS[@]}"; do
		page=${entry%%|*}
		if [[ ",$ONLY_PAGES," == *",$page,"* ]]; then
			filtered+=("$entry")
		fi
	done
	if ((${#filtered[@]} == 0)); then
		echo "ONLY_PAGES='$ONLY_PAGES' matched no endpoint." >&2
		exit 1
	fi
	ENDPOINTS=("${filtered[@]}")
fi

RAW="$OUT_DIR/raw.csv"
echo 'page,label,iteration,seconds,http_status' >"$RAW"

# Settings renders from useI18n alone -- no API call. Its budget is the SPA shell,
# so time the document and the entry chunk django-vite serves.
SETTINGS_ASSET_URL=""
SETTINGS_ASSET=""
if [[ -f "$PROJECT_ROOT/frontend/dist/.vite/manifest.json" ]]; then
	SETTINGS_ASSET=$("$PYTHON_BIN" -c '
import json
manifest = json.load(open("'"$PROJECT_ROOT"'/frontend/dist/.vite/manifest.json"))
entry = manifest.get("src/main.tsx") or {}
print(entry.get("file", ""))
')
	if [[ -n "$SETTINGS_ASSET" ]]; then
		SETTINGS_ASSET_URL="http://localhost:8000/static/${SETTINGS_ASSET}"
		ENDPOINTS+=("Settings|entry chunk|$SETTINGS_ASSET_URL")
	fi
fi

echo "Measuring ${#ENDPOINTS[@]} endpoints x $ITERATIONS iterations -> $OUT_DIR"
for entry in "${ENDPOINTS[@]}"; do
	IFS='|' read -r page label path <<<"$entry"
	printf '  %-16s %-32s' "$page" "$label"
	for i in $(seq 1 "$ITERATIONS"); do
		if [[ "$COLD" == "1" && "$label" != "SPA document" && "$label" != "entry chunk" ]]; then
			restart_page_load_server
		fi
		target="$API_BASE$path"
		# The two Settings rows are static assets served outside /api/v1.
		case "$label" in
		"SPA document") target="http://localhost:8000/" ;;
		"entry chunk") target="$SETTINGS_ASSET_URL" ;;
		esac
		result=$(curl -s -o /dev/null --max-time "$CURL_TIMEOUT" \
			-w '%{time_total}|%{http_code}' \
			"$target" 2>/dev/null) || result="0|000"
		seconds=${result%%|*}
		status=${result##*|}
		printf '%s,%s,%s,%s,%s\n' "$page" "$label" "$i" "$seconds" "$status" >>"$RAW"
		printf '%s ' "$seconds"
	done
	printf '\n'
done

"$PYTHON_BIN" - "$RAW" "$OUT_DIR" "$ITERATIONS" "$LABEL" "$COLD" "$FRONTEND_OVERHEAD_SECONDS" <<'PY'
import csv, os, statistics, sys
from collections import OrderedDict, defaultdict

raw_path, out_dir, iterations, label, cold, overhead = sys.argv[1:7]
iterations = int(iterations)
overhead = float(overhead)
is_cold = cold == '1'

rows = list(csv.DictReader(open(raw_path)))
by_endpoint = OrderedDict()
for r in rows:
    by_endpoint.setdefault((r['page'], r['label']), []).append(r)

def pct(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)

summary = []
for (page, lbl), rs in by_endpoint.items():
    times = [float(r['seconds']) for r in rs]
    codes = {r['http_status'] for r in rs}
    summary.append({
        'page': page, 'label': lbl, 'iterations': len(times),
        'p50': pct(times, 50), 'p95': pct(times, 95),
        'mean': statistics.fmean(times), 'max': max(times),
        'statuses': '/'.join(sorted(codes)),
    })

with open(os.path.join(out_dir, 'summary.csv'), 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
    w.writeheader()
    w.writerows(summary)

pages = OrderedDict()
for s in summary:
    pages.setdefault(s['page'], []).append(s)

mode_note = 'COLD (Redis flushed, server and workers restarted each iteration)' if is_cold \
    else 'WARM (caches left standing)'

lines = [
    f'# Page load latency: {label}',
    '',
    f'Mode: {mode_note}.',
    f'Iterations per endpoint: {iterations}. p50/p95 in seconds.',
    '',
]
if is_cold and iterations < 20:
    lines += [
        f'At n={iterations} the interpolated p95 is the maximum -- linear interpolation',
        'has nowhere to put a 95th percentile. That is stated rather than hidden: cold',
        f'iterations of a multi-minute path are expensive, so {iterations} runs report',
        'worst-case and read it as "no cold run exceeded this".',
        '',
    ]

lines += [
    '## Per-page budget',
    '',
    'A page load fires its calls concurrently, so the wall-clock floor is the',
    'slowest call, not the sum. Both are shown: `sum(p50)` is the total server',
    'work, `max(p95)` is the realistic critical path.',
    '',
    'The budget is browser time-to-data, so the API critical path is charged',
    f'`FRONTEND_OVERHEAD_SECONDS` ({overhead:.3f}s) for bundle, route chunk and',
    (
        'render. That constant is measured from the production build, not assumed.'
        if overhead > 0 else
        'render -- and that charge is zero, because nothing has read a '
        '`page-data-ready` measure off the production build in a browser yet. So '
        'these are server-side floors: every PASS below is a PASS only for the API, '
        'and the smallest margin in the table is the headroom the browser gets to '
        'spend.'
    ),
    '',
    '| Page | Calls | sum(p50) | max(p95) | +frontend | Budget | Verdict |',
    '| --- | --- | --- | --- | --- | --- | --- |',
]

# One budget per routed page in frontend/src/App.tsx. Settings is in the list
# even though it makes no API call, because its cost is the SPA shell.
BUDGETS = {page: 3.0 for page in (
    'Dashboard', 'Indicator Board', 'Stock Detail', 'Macro Context', 'Models',
    'Backtest', 'Alert Center', 'Settings',
)}

for page, items in pages.items():
    total = sum(i['p50'] for i in items)
    worst = max(i['p95'] for i in items)
    browser = worst + overhead
    budget = BUDGETS.get(page, 3.0)
    timed_out = any('000' in i['statuses'] for i in items)
    if timed_out:
        verdict = '**TIMEOUT**'
    else:
        verdict = 'PASS' if browser <= budget else '**OVER**'
    lines.append(
        f'| {page} | {len(items)} | {total:.3f}s | {worst:.3f}s '
        f'| {browser:.3f}s | <={budget}s p95 | {verdict} |')

missing = sorted(set(BUDGETS) - set(pages))
if missing:
    lines += ['', f'**Pages with no measurement: {", ".join(missing)}**',
              'A routed page missing from the endpoint list silently passes.']

lines += ['', '## Per endpoint', '',
          '| Page | Endpoint | p50 | p95 | mean | max | status |',
          '| --- | --- | --- | --- | --- | --- | --- |']
for s in sorted(summary, key=lambda x: -x['p95']):
    lines.append(
        f"| {s['page']} | {s['label']} | {s['p50']:.3f}s | {s['p95']:.3f}s "
        f"| {s['mean']:.3f}s | {s['max']:.3f}s | {s['statuses']} |")

non200 = [s for s in summary if s['statuses'] != '200']
if non200:
    lines += ['', '## Endpoints that did not return 200', '']
    for s in non200:
        note = 'did not complete within the curl timeout' if '000' in s['statuses'] else s['statuses']
        lines.append(f"- {s['page']} / {s['label']}: {note}")

open(os.path.join(out_dir, 'summary.md'), 'w').write('\n'.join(lines) + '\n')
print('\n'.join(lines))
PY

echo
echo "Wrote $OUT_DIR/{raw.csv,summary.csv,summary.md}"
