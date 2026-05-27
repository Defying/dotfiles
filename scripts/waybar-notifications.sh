#!/usr/bin/env bash
# Toggle the notification panel; falls back to a fuzzel quick-action menu.

set -u

panel="/home/ben/dotfiles/scripts/notification-panel.py"
runtime_dir="${XDG_RUNTIME_DIR:-/tmp}"
pid_file="$runtime_dir/notification-panel.pid"

if [[ -r "$pid_file" ]]; then
  panel_pid="$(sed -n '1p' "$pid_file")"
  if [[ "$panel_pid" =~ ^[0-9]+$ ]] && kill -0 "$panel_pid" >/dev/null 2>&1; then
    kill "$panel_pid" >/dev/null 2>&1 || true
    exit 0
  fi
  rm -f "$pid_file"
fi

existing_pids="$(pgrep -u "$USER" -f "^python3 $panel$|^$panel$" || true)"
if [[ -n "$existing_pids" ]]; then
  printf '%s\n' "$existing_pids" | xargs -r kill >/dev/null 2>&1 || true
  exit 0
fi

if [[ -x "$panel" ]]; then
  "$panel" >/tmp/notification-panel.log 2>&1 &
  panel_pid=$!
  sleep 0.15
  if kill -0 "$panel_pid" >/dev/null 2>&1; then
    disown "$panel_pid" 2>/dev/null || true
    exit 0
  fi
fi

# Fallback: fuzzel-based quick actions
choice=$(
  {
    printf 'dismiss last\n'
    printf 'dismiss all\n'
    printf 'restore last\n'
    printf 'toggle do-not-disturb\n'
  } | fuzzel --dmenu --prompt='notifs  ' --lines=4 --width=24
)
choice="${choice%$'\n'}"
case "$choice" in
  "dismiss last")  makoctl dismiss ;;
  "dismiss all")   makoctl dismiss --all ;;
  "restore last")  makoctl restore ;;
  "toggle do-not-disturb") makoctl mode -t do-not-disturb ;;
esac
