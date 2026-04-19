#!/usr/bin/env bash
set -euo pipefail

INTERVAL="${1:-2}"
shift || true
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUS_SCRIPT="$SCRIPT_DIR/status.sh"

while true; do
  clear
  bash "$STATUS_SCRIPT" "$@"
  sleep "$INTERVAL"
done
