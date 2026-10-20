#!/usr/bin/env bash
set -euo pipefail

# Times every endpoint the SPA calls on page load and reports p50/p95 per
# endpoint plus a summed per-page budget. Modelled on smoke_api_check.sh's
# login flow. Run before and after a change to prove the delta.
#
#   ITERATIONS=5 LABEL=baseline bash scripts/measure_page_api_latency.sh
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

OUT_DIR="$REPORT_ROOT/api_latency_$(date +%Y%m%d_%H%M%S)_$LABEL"
mkdir -p "$OUT_DIR"

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
# from frontend/src/lib/dashboardCandidateFilters.ts, sent by fetchDashboardStocks.
DASHBOARD_STOCKS="/dashboard/stocks/?prediction_horizon=7&page_size=120&prediction_source=lightgbm&horizon_days=7&candidate_mode=top_n&up_threshold=0.45&top_n=8&top_n_metric=up_prob_7d&trade_score_scope=independent&trade_score_threshold=1&max_positions=5&use_macro_context=true"

# page|label|path -- page groups sum into the per-page budget.
ENDPOINTS=(
	"Dashboard|stocks (live inference)|$DASHBOARD_STOCKS"
	'Dashboard|macro current|/macro/contexts/current/'
	'Dashboard|macro list|/macro/contexts/?page_size=1'
	'Dashboard|hot concepts|/sentiment/concepts/top/?limit=3'
	'Dashboard|recent signals|/signals/recent/?days=1&page_size=200'
	'Dashboard|alert events|/alert-events/?page_size=200'
	'Dashboard|backtests|/backtest/?page_size=50'
	'Dashboard|bottom candidates|/screener/bottom-candidates/?top_n=20'
	'Indicator Board|stocks (no candidate filter)|/dashboard/stocks/?prediction_horizon=7&page_size=120'
	"Stock Detail|asset search|/assets/?search=$ASSET_SYMBOL"
	"Stock Detail|ohlcv page 1|/ohlcv/?asset=$ASSET_ID&page_size=1000"
	"Stock Detail|prediction|/prediction/$ASSET_SYMBOL/"
	"Stock Detail|lightgbm prediction|/lightgbm-predictions/$ASSET_SYMBOL/"
	'Stock Detail|sentiment|/sentiment/?score_type=ASSET_7D&page_size=5'
	'Backtest|runs|/backtest/?page_size=100'
	'Models|model versions|/prediction-model-versions/?page_size=20'
	'Models|lightgbm models|/lightgbm-models/?page_size=20'
	'Models|ensemble weights|/ensemble-weights/?page_size=20'
	'Models|feature importance|/lightgbm-models/feature-importance-trends/?limit_models=5&top_n=5'
)

if [[ -n "$BACKTEST_ID" ]]; then
	ENDPOINTS+=("Backtest|trades|/backtest/$BACKTEST_ID/trades/")
	ENDPOINTS+=("Backtest|comparison curve|/backtest/$BACKTEST_ID/comparison_curve/")
else
	echo "note: no backtest runs found; skipping trades/comparison_curve" >&2
fi

RAW="$OUT_DIR/raw.csv"
echo 'page,label,iteration,seconds,http_status' >"$RAW"

echo "Measuring ${#ENDPOINTS[@]} endpoints x $ITERATIONS iterations -> $OUT_DIR"
for entry in "${ENDPOINTS[@]}"; do
	IFS='|' read -r page label path <<<"$entry"
	printf '  %-16s %-32s' "$page" "$label"
	for i in $(seq 1 "$ITERATIONS"); do
		result=$(curl -s -o /dev/null --max-time "$CURL_TIMEOUT" \
			-w '%{time_total}|%{http_code}' \
			"$API_BASE$path" 2>/dev/null) || result="0|000"
		seconds=${result%%|*}
		status=${result##*|}
		printf '%s,%s,%s,%s,%s\n' "$page" "$label" "$i" "$seconds" "$status" >>"$RAW"
		printf '%s ' "$seconds"
	done
	printf '\n'
done

"$PYTHON_BIN" - "$RAW" "$OUT_DIR" "$ITERATIONS" "$LABEL" <<'PY'
import csv, os, statistics, sys
from collections import OrderedDict, defaultdict

raw_path, out_dir, iterations, label = sys.argv[1:5]

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

lines = [
    f'# API latency: {label}',
    '',
    f'Iterations per endpoint: {iterations}. p50/p95 in seconds.',
    '',
    '## Per-page budget',
    '',
    'A page load fires its calls concurrently, so the wall-clock floor is the',
    'slowest call, not the sum. Both are shown: `sum(p50)` is the total server',
    'work, `max(p95)` is the realistic critical path.',
    '',
    '| Page | Calls | sum(p50) | max(p95) | Budget | Verdict |',
    '| --- | --- | --- | --- | --- | --- |',
]

BUDGETS = {'Dashboard': 0.8, 'Indicator Board': 0.8, 'Stock Detail': 0.8,
           'Backtest': 0.8, 'Models': 0.8}

for page, items in pages.items():
    total = sum(i['p50'] for i in items)
    worst = max(i['p95'] for i in items)
    budget = BUDGETS.get(page, 0.8)
    timed_out = any('000' in i['statuses'] for i in items)
    if timed_out:
        verdict = '**TIMEOUT**'
    else:
        verdict = 'PASS' if worst <= budget else '**OVER**'
    lines.append(f'| {page} | {len(items)} | {total:.3f}s | {worst:.3f}s | <={budget}s p95 | {verdict} |')

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
