#!/usr/bin/env bash
set -euo pipefail

PROVIDER="${PROVIDER:-tushare_major}"
CHUNK_DAYS="${CHUNK_DAYS:-31}"
BACKFILL_FLOOR="${BACKFILL_FLOOR:-2021-04-15 00:00:00}"
RUN_PIPELINE="${RUN_PIPELINE:-1}"
LIMIT_PER_PROVIDER="${LIMIT_PER_PROVIDER:-0}"
MAX_RETRIES="${MAX_RETRIES:-0}"

current_min=$(docker compose exec django python manage.py shell -c "from apps.sentiment.models import NewsArticle; from django.db.models import Min; value=NewsArticle.objects.aggregate(v=Min('published_at'))['v']; print(value.isoformat() if value else '')" | tail -n 1)

if [[ -z "$current_min" ]]; then
  echo "No NewsArticle rows exist yet. Seed current news first, then rerun this helper." >&2
  exit 1
fi

window=$(
  CURRENT_MIN="$current_min" \
  CHUNK_DAYS="$CHUNK_DAYS" \
  BACKFILL_FLOOR="$BACKFILL_FLOOR" \
  python3 - <<'PY'
from datetime import datetime, timedelta, timezone
import os


def parse_dt(value):
    cleaned = value.strip()
    cleaned = cleaned.replace('T', ' ')
    if cleaned.endswith('Z'):
        cleaned = cleaned[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        parsed = datetime.strptime(cleaned, '%Y-%m-%d %H:%M:%S')
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


current_min = parse_dt(os.environ['CURRENT_MIN'])
floor = parse_dt(os.environ['BACKFILL_FLOOR'])
chunk_days = max(1, int(os.environ['CHUNK_DAYS']))
window_end = current_min - timedelta(seconds=1)
window_start = max(floor, window_end - timedelta(days=chunk_days) + timedelta(seconds=1))

if window_end < floor:
    print('DONE')
else:
    def fmt(value):
        return value.replace(microsecond=0).strftime('%Y-%m-%d %H:%M:%S')

    print(f"{fmt(window_start)}|{fmt(window_end)}")
PY
)

if [[ "$window" == "DONE" ]]; then
  echo "Historical backfill already reached ${BACKFILL_FLOOR}."
  exit 0
fi

start_at="${window%%|*}"
end_at="${window##*|}"

echo "Running ${PROVIDER} staged backfill for ${start_at} -> ${end_at}"

command=(
  docker compose exec django python manage.py backfill_news
  "--providers=${PROVIDER}"
  "--start-at=${start_at}"
  "--end-at=${end_at}"
  "--chunk-days=${CHUNK_DAYS}"
  "--sleep-seconds=0"
  "--max-retries=${MAX_RETRIES}"
  "--limit-per-provider=${LIMIT_PER_PROVIDER}"
)

if [[ "$RUN_PIPELINE" == "1" ]]; then
  command+=("--run-pipeline")
fi

set +e
output=$("${command[@]}" 2>&1)
status=$?
set -e

printf '%s\n' "$output"

if [[ $status -ne 0 ]]; then
  if grep -q '最多访问该接口' <<<"$output"; then
    echo "Provider quota window is still closed for ${PROVIDER}. Rerun this helper after the next hourly reset." >&2
    exit 75
  fi
  exit "$status"
fi

docker compose exec django python manage.py shell -c "from apps.sentiment.models import NewsArticle; from django.db.models import Min, Count; agg=NewsArticle.objects.aggregate(min=Min('published_at'), count=Count('id')); print(f'Earliest article: {agg[\"min\"].isoformat() if agg[\"min\"] else None}'); print(f'Total articles: {agg[\"count\"]}')"