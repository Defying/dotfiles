#!/usr/bin/env bash
# Send one mako notification when battery charge crosses low thresholds.

set -euo pipefail

BATTERY="${BATTERY_LOW_NOTIFY_BATTERY:-/sys/class/power_supply/macsmc-battery}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/battery-low-notify"
POLL_SECONDS="${BATTERY_LOW_NOTIFY_POLL_SECONDS:-30}"
RESET_MARGIN=3

mkdir -p "$STATE_DIR"

read_prop() {
  local name="$1"
  local path="$BATTERY/$name"

  [[ -r "$path" ]] || return 1
  <"$path"
}

notify_threshold() {
  local threshold="$1"
  local capacity="$2"
  local urgency="normal"
  local expire="9000"
  local icon="battery-low-symbolic"

  if (( threshold <= 10 )); then
    urgency="critical"
    expire="0"
    icon="battery-caution-symbolic"
  fi

  notify-send \
    -a "battery" \
    -i "$icon" \
    -u "$urgency" \
    -t "$expire" \
    -h "string:x-canonical-private-synchronous:battery-low-$threshold" \
    "Low battery" \
    "Battery is at ${capacity}%"
}

mark_sent() {
  : >"$STATE_DIR/sent-$1"
}

clear_sent() {
  rm -f "$STATE_DIR/sent-$1"
}

sent() {
  [[ -e "$STATE_DIR/sent-$1" ]]
}

check_once() {
  local capacity status threshold

  capacity="$(read_prop capacity || true)"
  status="$(read_prop status || true)"
  [[ "$capacity" =~ ^[0-9]+$ ]] || return 0

  if [[ "$status" != "Discharging" ]]; then
    clear_sent 20
    clear_sent 10
    return 0
  fi

  for threshold in 20 10; do
    if (( capacity <= threshold )); then
      if ! sent "$threshold"; then
        notify_threshold "$threshold" "$capacity"
        mark_sent "$threshold"
      fi
    elif (( capacity >= threshold + RESET_MARGIN )); then
      clear_sent "$threshold"
    fi
  done
}

if [[ "${1:-}" == "--once" ]]; then
  check_once
  exit 0
fi

while true; do
  check_once
  sleep "$POLL_SECONDS"
done
