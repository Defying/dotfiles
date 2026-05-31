#!/usr/bin/env bash
# Force-refresh a Waybar AI usage module.

set -euo pipefail

service="${1:-}"
signal="${2:-}"

refresh_codex() {
  local sig="${1:-8}"
  setsid -f /home/ben/.local/bin/waybar-helper codex --refresh --signal "$sig" >/dev/null 2>&1
}

refresh_claude() {
  local sig="${1:-9}"
  setsid -f /home/ben/.local/bin/waybar-helper claude --refresh --signal "$sig" >/dev/null 2>&1
}

case "$service" in
  codex)
    refresh_codex "${signal:-8}"
    exit 0
    ;;
  claude)
    refresh_claude "${signal:-9}"
    exit 0
    ;;
  all)
    refresh_codex 8
    refresh_claude 9
    exit 0
    ;;
  *)
    exit 2
    ;;
esac
