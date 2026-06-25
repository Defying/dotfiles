#!/usr/bin/env bash
# Send Mako notifications when the main filesystem is running out of space.

set -euo pipefail

CHECK_PATH="${DISK_SPACE_NOTIFY_PATH:-$HOME}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/disk-space-notify"
POLL_SECONDS="${DISK_SPACE_NOTIFY_POLL_SECONDS:-300}"

WARNING_GIB="${DISK_SPACE_NOTIFY_WARNING_GIB:-10}"
CRITICAL_GIB="${DISK_SPACE_NOTIFY_CRITICAL_GIB:-5}"
WARNING_FREE_PERCENT="${DISK_SPACE_NOTIFY_WARNING_FREE_PERCENT:-15}"
CRITICAL_FREE_PERCENT="${DISK_SPACE_NOTIFY_CRITICAL_FREE_PERCENT:-8}"
RESET_GIB="${DISK_SPACE_NOTIFY_RESET_GIB:-2}"
RESET_PERCENT="${DISK_SPACE_NOTIFY_RESET_PERCENT:-3}"

mkdir -p "$STATE_DIR"

gib_to_bytes() {
  awk -v gib="$1" 'BEGIN { printf "%.0f", gib * 1073741824 }'
}

format_bytes() {
  awk -v bytes="$1" 'BEGIN { printf "%.1f GiB", bytes / 1073741824 }'
}

state_key() {
  local key="${CHECK_PATH#/}"
  key="${key//\//-}"
  key="${key:-root}"
  printf '%s' "$key"
}

sent_file() {
  printf '%s/sent-%s-%s' "$STATE_DIR" "$(state_key)" "$1"
}

sent() {
  [[ -e "$(sent_file "$1")" ]]
}

mark_sent() {
  : >"$(sent_file "$1")"
}

clear_sent() {
  rm -f "$(sent_file "$1")"
}

read_usage() {
  df -B1 --output=avail,size,pcent,target "$CHECK_PATH" |
    awk 'NR == 2 { gsub("%", "", $3); print $1, $2, $3, $4 }'
}

notify_level() {
  local level="$1"
  local avail="$2"
  local size="$3"
  local used_percent="$4"
  local target="$5"
  local free_percent="$(( avail * 100 / size ))"
  local free_label
  local urgency="normal"
  local timeout="15000"
  local title="Disk space low"

  free_label="$(format_bytes "$avail")"

  if [[ "$level" == "critical" ]]; then
    urgency="critical"
    timeout="0"
    title="Disk space critical"
  fi

  notify-send \
    -a "disk-space" \
    -i "drive-harddisk" \
    -u "$urgency" \
    -t "$timeout" \
    -h "string:x-canonical-private-synchronous:disk-space-$level-$(state_key)" \
    "$title" \
    "Only ${free_label} free on ${target} (${used_percent}% used, ${free_percent}% free). Free space before updates, builds, or downloads."
}

check_once() {
  local avail size used_percent target free_percent
  local warning_bytes critical_bytes reset_bytes

  read -r avail size used_percent target < <(read_usage)
  [[ "$avail" =~ ^[0-9]+$ && "$size" =~ ^[0-9]+$ && "$used_percent" =~ ^[0-9]+$ ]] || return 0

  warning_bytes="$(gib_to_bytes "$WARNING_GIB")"
  critical_bytes="$(gib_to_bytes "$CRITICAL_GIB")"
  reset_bytes="$(gib_to_bytes "$RESET_GIB")"
  free_percent="$(( avail * 100 / size ))"

  if (( avail >= critical_bytes + reset_bytes && free_percent >= CRITICAL_FREE_PERCENT + RESET_PERCENT )); then
    clear_sent critical
  fi

  if (( avail >= warning_bytes + reset_bytes && free_percent >= WARNING_FREE_PERCENT + RESET_PERCENT )); then
    clear_sent warning
  fi

  if (( avail <= critical_bytes || free_percent <= CRITICAL_FREE_PERCENT )); then
    if ! sent critical; then
      notify_level critical "$avail" "$size" "$used_percent" "$target"
      mark_sent critical
      mark_sent warning
    fi
    return 0
  fi

  if (( avail <= warning_bytes || free_percent <= WARNING_FREE_PERCENT )); then
    if ! sent warning; then
      notify_level warning "$avail" "$size" "$used_percent" "$target"
      mark_sent warning
    fi
  fi
}

if [[ "${1:-}" == "--once" ]]; then
  check_once
  exit 0
fi

while true; do
  check_once
  sleep "$POLL_SECONDS"
done
