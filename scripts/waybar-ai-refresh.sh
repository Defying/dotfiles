#!/usr/bin/env bash
# Force-refresh a Waybar AI usage module.

set -euo pipefail

service="${1:-}"
signal="${2:-}"

case "$service" in
  codex)
    setsid -f /home/ben/dotfiles/scripts/waybar-openai-tokens.py --refresh --signal "${signal:-8}" >/dev/null 2>&1
    exit 0
    ;;
  claude)
    setsid -f /home/ben/dotfiles/scripts/waybar-claude-usage.py --refresh --signal "${signal:-9}" >/dev/null 2>&1
    exit 0
    ;;
  *)
    exit 2
    ;;
esac

if [[ "$signal" =~ ^[0-9]+$ ]]; then
  pkill "-RTMIN+$signal" -x waybar >/dev/null 2>&1 || true
fi
