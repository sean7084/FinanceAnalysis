#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_native_env.sh"

DJANGO_BIND="${DJANGO_BIND:-0.0.0.0:8000}"

exec "$PYTHON_BIN" manage.py runserver "$DJANGO_BIND"