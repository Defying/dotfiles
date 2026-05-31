#!/usr/bin/env bash
# Switch the Hyprland wallpaper between a plain black background and the normal
# static image. This is glue around the existing swaybg/aerial providers.
set -euo pipefail

WALLPAPER="/home/ben/dotfiles/assets/wallpaper-marek-piwnicki.jpg"
STATE="${XDG_RUNTIME_DIR:-/tmp}/hypr-wallpaper-mode"

notify() {
  notify-send -a "wallpaper" -t 1200 "$1" "${2:-}" >/dev/null 2>&1 || true
}

stop_aerial() {
  if command -v aerial >/dev/null 2>&1; then
    aerial stop >/dev/null 2>&1 || true
  else
    pkill -x mpvpaper >/dev/null 2>&1 || true
  fi
}

start_black() {
  stop_aerial
  pkill -x swaybg >/dev/null 2>&1 || true
  setsid -f swaybg -c 000000 >/dev/null 2>&1
  printf 'black\n' > "$STATE"
  notify "Wallpaper" "black"
}

start_normal() {
  stop_aerial
  pkill -x swaybg >/dev/null 2>&1 || true
  setsid -f swaybg -i "$WALLPAPER" -m fill >/dev/null 2>&1
  printf 'normal\n' > "$STATE"
  notify "Wallpaper" "restored"
}

case "${1:-toggle}" in
  black)
    start_black
    ;;
  reset|normal|restore)
    start_normal
    ;;
  toggle)
    if [[ -f "$STATE" ]] && [[ "$(cat "$STATE")" == "black" ]]; then
      start_normal
    else
      start_black
    fi
    ;;
  *)
    echo "usage: $0 [black|reset|toggle]" >&2
    exit 2
    ;;
esac
