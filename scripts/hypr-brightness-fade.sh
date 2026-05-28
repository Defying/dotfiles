#!/usr/bin/env bash
# Smooth brightness fades for hypridle.

set -euo pipefail

cmd="${1:-}"
target_pct="${2:-10}"
duration="${3:-1.4}"
state_dir="${XDG_RUNTIME_DIR:-/tmp}/hypr-brightness-fade"
state_file="$state_dir/saved-brightness"

if ! command -v brightnessctl >/dev/null 2>&1; then
  exit 0
fi

current_raw() {
  brightnessctl get
}

max_raw() {
  brightnessctl max
}

clamp_raw() {
  local value="$1" max="$2"
  (( value < 1 )) && value=1
  (( value > max )) && value="$max"
  printf '%s\n' "$value"
}

fade_to_raw() {
  local target="$1" seconds="$2"
  local start max steps delay value

  start="$(current_raw)"
  max="$(max_raw)"
  target="$(clamp_raw "$target" "$max")"
  steps="${HYPR_BRIGHTNESS_FADE_STEPS:-28}"
  delay="$(awk -v s="$seconds" -v n="$steps" 'BEGIN { if (n <= 0) n = 1; printf "%.4f", s / n }')"

  for ((i = 1; i <= steps; i++)); do
    value=$(( start + (target - start) * i / steps ))
    value="$(clamp_raw "$value" "$max")"
    brightnessctl -q set "$value" >/dev/null 2>&1 || exit 0
    sleep "$delay"
  done
}

case "$cmd" in
  dim)
    mkdir -p "$state_dir"
    current_raw >"$state_file"
    max="$(max_raw)"
    target=$(( max * target_pct / 100 ))
    fade_to_raw "$target" "$duration"
    ;;
  restore)
    [[ -r "$state_file" ]] || exit 0
    target="$(sed -n '1p' "$state_file")"
    rm -f "$state_file"
    [[ "$target" =~ ^[0-9]+$ ]] || exit 0
    fade_to_raw "$target" "$duration"
    ;;
  *)
    echo "usage: $0 dim [percent] [seconds] | restore [seconds]" >&2
    exit 2
    ;;
esac
