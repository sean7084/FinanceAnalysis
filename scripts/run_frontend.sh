#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_native_env.sh"

is_wsl() {
  [[ -n "${WSL_DISTRO_NAME:-}" ]] && return 0
  grep -qi microsoft /proc/version 2>/dev/null
}

FRONTEND_DIR="${FRONTEND_DIR:-$PROJECT_ROOT/frontend}"
NPM_BIN="${NPM_BIN:-npm}"
NODE_BIN="${NODE_BIN:-node}"
NVM_DIR="${NVM_DIR:-$HOME/.nvm}"

if [[ -f "$NVM_DIR/nvm.sh" ]]; then
  # shellcheck disable=SC1090
  source "$NVM_DIR/nvm.sh"
fi

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

if ! command -v "$NODE_BIN" >/dev/null 2>&1; then
  echo "node runtime not found. Install a Linux Node.js runtime before running the frontend." >&2
  exit 1
fi

NPM_PATH="$(command -v "$NPM_BIN")"
NODE_PATH="$(command -v "$NODE_BIN")"

if is_wsl && { [[ "$NPM_PATH" == /mnt/* ]] || [[ "$NODE_PATH" == /mnt/* ]] || [[ "$NPM_PATH" == *.cmd ]] || [[ "$NODE_PATH" == *.exe ]]; }; then
  echo "WSL detected but frontend is resolving Windows Node/npm binaries:" >&2
  echo "  npm:  $NPM_PATH" >&2
  echo "  node: $NODE_PATH" >&2
  echo "Install Node.js inside WSL (for example with nvm or the distro package manager) and rerun the task from the WSL clone." >&2
  exit 1
fi

cd "$FRONTEND_DIR"
exec "$NPM_BIN" run dev