#!/usr/bin/env bash
set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8000/api/v1}"
SMOKE_USERNAME="${SMOKE_USERNAME:-}"
SMOKE_PASSWORD="${SMOKE_PASSWORD:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ -z "$SMOKE_USERNAME" || -z "$SMOKE_PASSWORD" ]]; then
  echo "SMOKE_USERNAME and SMOKE_PASSWORD are required." >&2
  exit 1
fi

json_get() {
  local key="$1"
  "$PYTHON_BIN" -c '
import json
import sys

key = sys.argv[1]
payload = json.load(sys.stdin)
value = payload
for part in key.split("."):
  if part.isdigit():
    value = value[int(part)]
  else:
    value = value[part]
if isinstance(value, (dict, list)):
  print(json.dumps(value))
else:
  print(value)
' "$key"
}

auth_payload=$(curl -fsS -X POST "$API_BASE/auth/token/" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$SMOKE_USERNAME\",\"password\":\"$SMOKE_PASSWORD\"}")
token=$(printf '%s' "$auth_payload" | json_get access)

auth_header=( -H "Authorization: Bearer $token" )

markets=$(curl -fsS "$API_BASE/markets/")
assets=$(curl -fsS "$API_BASE/assets/?page_size=5")
asset_count=$(printf '%s' "$assets" | json_get count)
first_asset_id=$(printf '%s' "$assets" | json_get results.0.id)
first_asset_symbol=$(printf '%s' "$assets" | json_get results.0.symbol)

ohlcv=$(curl -fsS "$API_BASE/ohlcv/?asset=$first_asset_id&page_size=5")
macro=$(curl -fsS "${auth_header[@]}" "$API_BASE/macro/contexts/?page_size=5")
screener=$(curl -fsS "${auth_header[@]}" "$API_BASE/screener/bottom-candidates/?top_n=5")
prediction=$(curl -fsS "${auth_header[@]}" "$API_BASE/prediction/$first_asset_symbol/")
sentiment=$(curl -fsS "${auth_header[@]}" "$API_BASE/sentiment/?score_type=ASSET_7D&page_size=5")
concepts=$(curl -fsS "${auth_header[@]}" "$API_BASE/sentiment/concepts/top/?limit=5")

printf 'Markets count: %s\n' "$(printf '%s' "$markets" | json_get count)"
printf 'Assets count: %s\n' "$asset_count"
printf 'OHLCV count: %s\n' "$(printf '%s' "$ohlcv" | json_get count)"
printf 'Macro contexts: %s\n' "$(printf '%s' "$macro" | json_get count)"
printf 'Screener rows: %s\n' "$(printf '%s' "$screener" | json_get count)"
printf 'Prediction rows: %s\n' "$(printf '%s' "$prediction" | json_get results | "$PYTHON_BIN" -c 'import json,sys; print(len(json.load(sys.stdin)))')"
printf 'Sentiment rows: %s\n' "$(printf '%s' "$sentiment" | json_get count)"
printf 'Concept rows: %s\n' "$(printf '%s' "$concepts" | json_get results | "$PYTHON_BIN" -c 'import json,sys; print(len(json.load(sys.stdin)))')"

printf '%s' "$asset_count" | "$PYTHON_BIN" -c 'import sys; assert int(sys.stdin.read().strip()) > 0'
printf '%s' "$ohlcv" | json_get count | "$PYTHON_BIN" -c 'import sys; assert int(sys.stdin.read().strip()) > 0'
printf '%s' "$screener" | json_get count | "$PYTHON_BIN" -c 'import sys; assert int(sys.stdin.read().strip()) > 0'
printf '%s' "$prediction" | json_get results | "$PYTHON_BIN" -c 'import json,sys; assert len(json.load(sys.stdin)) > 0'

echo 'Smoke API check passed.'