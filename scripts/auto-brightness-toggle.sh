#!/usr/bin/env bash
# Toggle adaptive (light-sensor) brightness on/off by flipping the off-flag the
# hypr-auto-brightness daemon checks each tick.
set -u
flag="${XDG_CACHE_HOME:-$HOME/.cache}/hypr/auto-brightness.off"
mkdir -p "$(dirname "$flag")"
if [[ -e "$flag" ]]; then
  rm -f "$flag"
  notify-send -a "brightness" -i "display-brightness-symbolic" -t 1800 -h "string:x-canonical-private-synchronous:autobright" \
    "Adaptive brightness" "on"
else
  : > "$flag"
  notify-send -a "brightness" -i "display-brightness-symbolic" -t 1800 -h "string:x-canonical-private-synchronous:autobright" \
    "Adaptive brightness" "off"
fi
