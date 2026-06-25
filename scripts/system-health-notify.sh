#!/usr/bin/env bash
# Notify for system states that need prompt attention.

set -euo pipefail

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/system-health-notify"
POLL_SECONDS="${SYSTEM_HEALTH_NOTIFY_POLL_SECONDS:-300}"
WARNING_MEM_MIB="${SYSTEM_HEALTH_NOTIFY_WARNING_MEM_MIB:-1536}"
CRITICAL_MEM_MIB="${SYSTEM_HEALTH_NOTIFY_CRITICAL_MEM_MIB:-768}"
WARNING_SWAP_MIB="${SYSTEM_HEALTH_NOTIFY_WARNING_SWAP_MIB:-2048}"
CRITICAL_SWAP_MIB="${SYSTEM_HEALTH_NOTIFY_CRITICAL_SWAP_MIB:-1024}"

mkdir -p "$STATE_DIR"

state_file() {
  printf '%s/%s.state' "$STATE_DIR" "$1"
}

clear_issue() {
  rm -f "$(state_file "$1")"
}

notify_issue() {
  local key="$1"
  local urgency="$2"
  local icon="$3"
  local title="$4"
  local body="$5"
  local timeout="15000"
  local state

  if [[ "$urgency" == "critical" ]]; then
    timeout="0"
  fi

  state="${urgency}"$'\n'"${title}"$'\n'"${body}"
  if [[ -r "$(state_file "$key")" ]] && [[ "$(cat "$(state_file "$key")")" == "$state" ]]; then
    return 0
  fi

  notify-send \
    -a "system-health" \
    -i "$icon" \
    -u "$urgency" \
    -t "$timeout" \
    -h "string:x-canonical-private-synchronous:system-health-$key" \
    "$title" \
    "$body"

  printf '%s' "$state" >"$(state_file "$key")"
}

join_first_units() {
  awk 'NR <= 4 { out = out (out ? ", " : "") $0 } END { print out }'
}

check_failed_units() {
  local key="$1"
  local label="$2"
  shift 2

  local units count shown
  units="$("$@" list-units --state=failed --no-legend --plain 2>/dev/null |
    awk 'NF { gsub(/^●/, "", $1); print $1 }' || true)"

  if [[ -z "$units" ]]; then
    clear_issue "$key"
    return 0
  fi

  count="$(printf '%s\n' "$units" | awk 'NF { count++ } END { print count + 0 }')"
  shown="$(printf '%s\n' "$units" | join_first_units)"
  notify_issue \
    "$key" \
    "critical" \
    "dialog-warning-symbolic" \
    "$label services failed" \
    "${count} failed unit(s): ${shown}. Open systemctl status for details."
}

check_dnf_history() {
  if ! command -v dnf >/dev/null 2>&1; then
    clear_issue dnf-history
    return 0
  fi

  if pgrep -x dnf >/dev/null 2>&1 || pgrep -x rpm >/dev/null 2>&1; then
    return 0
  fi

  local info status description
  info="$(dnf history info last 2>/dev/null | sed -n '1,12p' || true)"
  status="$(printf '%s\n' "$info" | awk -F: '/^Status/ { sub(/^[[:space:]]+/, "", $2); print $2; exit }')"
  description="$(printf '%s\n' "$info" | awk -F: '/^Description/ { sub(/^[[:space:]]+/, "", $2); print $2; exit }')"

  case "$status" in
    Started|Failed)
      notify_issue \
        "dnf-history" \
        "critical" \
        "software-update-urgent-symbolic" \
        "Package update needs attention" \
        "Last DNF transaction is '${status}': ${description:-unknown command}. Run dnf check before the next update."
      ;;
    *)
      clear_issue dnf-history
      ;;
  esac
}

read_meminfo_kib() {
  awk '
    $1 == "MemAvailable:" { mem_available = $2 }
    $1 == "SwapFree:" { swap_free = $2 }
    END {
      print mem_available + 0, swap_free + 0
    }
  ' /proc/meminfo
}

check_memory_pressure() {
  local mem_available_kib swap_free_kib mem_available_mib swap_free_mib

  read -r mem_available_kib swap_free_kib < <(read_meminfo_kib)
  mem_available_mib="$(( mem_available_kib / 1024 ))"
  swap_free_mib="$(( swap_free_kib / 1024 ))"

  if (( mem_available_mib <= CRITICAL_MEM_MIB && swap_free_mib <= CRITICAL_SWAP_MIB )); then
    notify_issue \
      "memory-pressure" \
      "critical" \
      "utilities-system-monitor" \
      "Memory pressure critical" \
      "Only ${mem_available_mib} MiB RAM and ${swap_free_mib} MiB swap are available. Close heavy apps now."
    return 0
  fi

  if (( mem_available_mib <= WARNING_MEM_MIB && swap_free_mib <= WARNING_SWAP_MIB )); then
    notify_issue \
      "memory-pressure" \
      "normal" \
      "utilities-system-monitor" \
      "Memory pressure high" \
      "${mem_available_mib} MiB RAM and ${swap_free_mib} MiB swap are available."
    return 0
  fi

  clear_issue memory-pressure
}

check_once() {
  check_failed_units user systemctl --user
  check_failed_units system systemctl
  check_dnf_history
  check_memory_pressure
}

if [[ "${1:-}" == "--once" ]]; then
  check_once
  exit 0
fi

while true; do
  check_once
  sleep "$POLL_SECONDS"
done
