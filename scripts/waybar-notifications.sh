#!/usr/bin/env bash
# Open / close / toggle the notification panel; falls back to a fuzzel
# quick-action menu if the panel can't start. Verb is $1 (default: toggle):
#   toggle  flip open<->closed (waybar bubble click)
#   open    open if not already open (swipe-left-from-right-edge gesture)
#   close   close if open        (swipe-right gesture)

set -u

verb="${1:-toggle}"
panel="/home/ben/dotfiles/scripts/notification-panel.py"
runtime_dir="${XDG_RUNTIME_DIR:-/tmp}"
pid_file="$runtime_dir/notification-panel.pid"

panel_pid() {
  # Echo a live panel PID, or nothing.
  if [[ -r "$pid_file" ]]; then
    local p; p="$(sed -n '1p' "$pid_file")"
    if [[ "$p" =~ ^[0-9]+$ ]] && kill -0 "$p" >/dev/null 2>&1; then
      printf '%s\n' "$p"; return 0
    fi
    rm -f "$pid_file"
  fi
  # Fallback: match the script path anywhere in argv (covers /usr/bin/python3
  # <panel> as well as a direct ./panel exec).
  pgrep -u "$USER" -f "notification-panel.py" 2>/dev/null | head -1
}

do_close() {
  local pids; pids="$(panel_pid)"
  [[ -n "$pids" ]] || return 0
  printf '%s\n' "$pids" | xargs -r kill >/dev/null 2>&1 || true
}

do_open() {
  [[ -n "$(panel_pid)" ]] && return 0   # already open
  if [[ -x "$panel" ]]; then
    "$panel" >/tmp/notification-panel.log 2>&1 &
    local pid=$!
    sleep 0.15
    if kill -0 "$pid" >/dev/null 2>&1; then
      disown "$pid" 2>/dev/null || true
      return 0
    fi
  fi
  # Fallback: fuzzel quick actions
  local choice
  choice=$(
    {
      printf 'dismiss last\n'; printf 'dismiss all\n'
      printf 'restore last\n'; printf 'toggle do-not-disturb\n'
    } | fuzzel --dmenu --prompt='notifs  ' --lines=4 --width=24
  )
  case "${choice%$'\n'}" in
    "dismiss last")  makoctl dismiss ;;
    "dismiss all")   makoctl dismiss --all ;;
    "restore last")  makoctl restore ;;
    "toggle do-not-disturb") makoctl mode -t do-not-disturb ;;
  esac
}

case "$verb" in
  open)   do_open ;;
  close)  do_close ;;
  toggle) if [[ -n "$(panel_pid)" ]]; then do_close; else do_open; fi ;;
  *)      echo "usage: $0 [open|close|toggle]" >&2; exit 2 ;;
esac
