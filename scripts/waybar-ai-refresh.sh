#!/usr/bin/env bash
# Force-refresh a Waybar AI usage module.

set -euo pipefail

service="${1:-}"
signal="${2:-}"
cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/waybar"

case "$service" in
  codex)
    ;;
  claude)
    rm -f "$cache_dir/claude-usage.json"
    ;;
  *)
    exit 2
    ;;
esac

if [[ "$signal" =~ ^[0-9]+$ ]]; then
  pkill "-RTMIN+$signal" waybar >/dev/null 2>&1 || true
fi
