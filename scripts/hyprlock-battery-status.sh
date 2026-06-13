#!/usr/bin/env bash
set -euo pipefail

for ps in /sys/class/power_supply/*; do
  [[ -r "$ps/type" && "$(<"$ps/type")" == Battery ]] || continue

  capacity=""
  status=""
  [[ -r "$ps/capacity" ]] && capacity="$(<"$ps/capacity")"
  [[ -r "$ps/status" ]] && status="$(<"$ps/status")"
  [[ -n "$capacity" ]] || continue

  case "$status" in
    Charging) state="charging" ;;
    Discharging) state="on battery" ;;
    Full) state="full" ;;
    "Not charging") state="not charging" ;;
    "") state="battery" ;;
    *) state="${status,,}" ;;
  esac

  printf 'battery %s%%' "$capacity"
  [[ -n "$state" ]] && printf ' - %s' "$state"
  printf '\n'
  exit 0
done

printf 'battery unavailable\n'
