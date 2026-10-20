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

# The write path is public now, so an unauthenticated create must succeed, the
# payload must carry no owner, and the row must be deletable again. The probe is
# inactive with an unreachable threshold so the beat-scheduled rule check can
# never evaluate it, and it is removed before this script exits.
probe_payload=$(printf '{"asset": %s, "name": "Smoke anonymous write probe", "condition_type": "PRICE_ABOVE", "threshold": "9999999.00", "channels": [], "is_active": false}' "$first_asset_id")
probe_response=$(curl -sS -X POST "$API_BASE/alerts/" \
  -H 'Content-Type: application/json' -d "$probe_payload" -w '\n%{http_code}')
probe_status=${probe_response##*$'\n'}
probe_rule=${probe_response%$'\n'*}

if [[ "$probe_status" != "201" ]]; then
  echo "Expected anonymous POST /alerts/ to return 201, got $probe_status: $probe_rule" >&2
  exit 1
fi
if printf '%s' "$probe_rule" | grep -q '"owner'; then
  echo 'The alert payload still exposes an owner field.' >&2
  exit 1
fi

probe_id=$(printf '%s' "$probe_rule" | json_get id)
probe_delete=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$API_BASE/alerts/$probe_id/")
if [[ "$probe_delete" != "204" ]]; then
  echo "Expected anonymous DELETE /alerts/$probe_id/ to return 204, got $probe_delete." >&2
  echo "Leave the probe row deleted by hand: python manage.py shell -c \"from apps.analytics.models import AlertRule; AlertRule.objects.filter(pk=$probe_id).delete()\"" >&2
  exit 1
fi
printf 'Anonymous write probe created and removed alert rule %s.\n' "$probe_id"

echo 'Smoke API check passed.'
