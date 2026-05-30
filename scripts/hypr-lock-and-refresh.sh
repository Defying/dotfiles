#!/usr/bin/env bash
# Run the lock screen and force-refresh AI Waybar modules when it exits.

set -euo pipefail

if pidof hyprlock >/dev/null 2>&1; then
  exit 0
fi

hyprlock || true
/home/ben/dotfiles/scripts/waybar-ai-refresh.sh all || true
