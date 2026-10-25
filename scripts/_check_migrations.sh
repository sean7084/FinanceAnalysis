#!/usr/bin/env bash
# Fail when a committed migration has not been applied to this database.
#
# Why this exists (2026-10-25): four migrations were merged and never applied locally. Every
# test passed, because the test database is built from the migrations and therefore always
# matches the models; the dev database did not, and the first query touching the new columns
# surfaced as a generic "ETF data failed to load" banner in the SPA. A schema change verified
# only against a fresh test database is not verified. This check turns that silent state into
# a startup failure.
#
# Sourced callers must have PYTHON_BIN and PROJECT_ROOT set (see _native_env.sh). A database
# that cannot be reached is NOT a migration failure -- service probing owns connectivity --
# so that case warns and returns 0.
set -euo pipefail

: "${PYTHON_BIN:?PYTHON_BIN must be set before checking migrations}"
: "${PROJECT_ROOT:?PROJECT_ROOT must be set before checking migrations}"

if ! output="$(cd "$PROJECT_ROOT" && "$PYTHON_BIN" manage.py showmigrations --list 2>&1)"; then
  echo "Migrations: could not read state (is the database reachable?); skipping the check." >&2
  exit 0
fi

pending="$(printf '%s\n' "$output" | grep '^\[ \]' || true)"
if [ -n "$pending" ]; then
  echo "Pending migrations: the code expects a schema this database does not have." >&2
  printf '%s\n' "$pending" >&2
  echo "Apply them first:  $PYTHON_BIN manage.py migrate" >&2
  exit 1
fi

echo "Migrations: all applied"
