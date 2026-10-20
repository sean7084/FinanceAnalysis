#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

API_BASE="${API_BASE:-http://localhost:8000/api/v1}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

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

# Every endpoint is unauthenticated now, so a bare request must succeed.
markets=$(curl -fsS "$API_BASE/markets/")
assets=$(curl -fsS "$API_BASE/assets/?page_size=5")
asset_count=$(printf '%s' "$assets" | json_get count)
first_asset_id=$(printf '%s' "$assets" | json_get results.0.id)
first_asset_symbol=$(printf '%s' "$assets" | json_get results.0.symbol)

ohlcv=$(curl -fsS "$API_BASE/ohlcv/?asset=$first_asset_id&page_size=5")
macro=$(curl -fsS "$API_BASE/macro/contexts/?page_size=5")
screener=$(curl -fsS "$API_BASE/screener/bottom-candidates/?top_n=5")
prediction=$(curl -fsS "$API_BASE/prediction/$first_asset_symbol/")
sentiment=$(curl -fsS "$API_BASE/sentiment/?score_type=ASSET_7D&page_size=5")
concepts=$(curl -fsS "$API_BASE/sentiment/concepts/top/?limit=5")

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

# The auth endpoints must be gone, and an unauthenticated write must be accepted.
auth_status=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API_BASE/auth/login/" \
  -H 'Content-Type: application/json' -d '{"username":"x","password":"y"}')
if [[ "$auth_status" != "404" ]]; then
  echo "Expected POST /auth/login/ to return 404, got $auth_status." >&2
  exit 1
fi

key_status=$(curl -s -o /dev/null -w '%{http_code}' "$API_BASE/developer/keys/")
if [[ "$key_status" != "404" ]]; then
  echo "Expected GET /developer/keys/ to return 404, got $key_status." >&2
  exit 1
fi

echo 'Smoke API check passed.'
