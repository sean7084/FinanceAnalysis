#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_native_env.sh"

DJANGO_BIND="${DJANGO_BIND:-0.0.0.0:8000}"

# Refuse to serve a schema this database does not have. See _check_migrations.sh for the
# 2026-10-25 incident this prevents: tests green, dev database four migrations behind.
bash "$PROJECT_ROOT/scripts/_check_migrations.sh"

exec "$PYTHON_BIN" manage.py runserver "$DJANGO_BIND"