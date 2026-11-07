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

# migrate --check is the decider. It parses no text, so it cannot be fooled by
# output formatting, and the reachability question was settled above, which leaves
# exactly one reason for it to fail here.
if (cd "$PROJECT_ROOT" && "$PYTHON_BIN" manage.py migrate --check >/dev/null 2>&1); then
  echo "Migrations: all applied"
  exit 0
fi

# Reporting only. showmigrations --list indents every row (" [ ] 0002_foo"), so the
# pattern must not be anchored at column 0. Anchoring it as '^\[ \]' is what made
# this guard inert for its whole life: it printed "all applied" against exactly the
# four-migrations-behind database it was written for. The unanchored form is the one
# docs/how-to/wsl-services.md has always shown.
pending="$(printf '%s\n' "$output" | grep -E '^[[:space:]]*\[ \]' || true)"
echo "Pending migrations: the code expects a schema this database does not have." >&2
printf '%s\n' "${pending:-$output}" >&2
echo "Apply them first:  $PYTHON_BIN manage.py migrate" >&2
exit 1
