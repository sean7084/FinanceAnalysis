#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_native_env.sh"

FRONTEND_DIR="${FRONTEND_DIR:-$PROJECT_ROOT/frontend}"
NPM_BIN="${NPM_BIN:-npm}"

if [[ ! -d "$FRONTEND_DIR" ]]; then
  echo "Frontend directory not found at $FRONTEND_DIR." >&2
  exit 1
fi

if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
  echo "package.json not found in $FRONTEND_DIR." >&2
  exit 1
fi

if ! command -v "$NPM_BIN" >/dev/null 2>&1; then
  echo "npm runtime not found. Install Node.js and npm first." >&2
  exit 1
fi

cd "$FRONTEND_DIR"
exec "$NPM_BIN" run dev