#!/usr/bin/env bash
# Volume / mute / brightness OSD using live Hyprland liquid glass, with notifications as fallback.
# Usage: volume-osd.sh up|down|mute|micmute|bright-up|bright-down [step]

set -u
action="${1:-}"
step="${2:-5}"

sink="@DEFAULT_AUDIO_SINK@"
src="@DEFAULT_AUDIO_SOURCE@"

osd_timeout_ms=1400
liquid_osd="/home/ben/dotfiles/scripts/liquid-osd.py"

read_pct() {
  # Returns integer percent for given node id ("@DEFAULT_AUDIO_SINK@" etc).
  local out
  out=$(wpctl get-volume "$1" 2>/dev/null)
  # Format: "Volume: 0.55" or "Volume: 0.55 [MUTED]"
  local v
  v=$(awk '{print $2}' <<<"$out")
  awk -v v="$v" 'BEGIN { printf "%d", v * 100 + 0.5 }'
}

is_muted() {
  wpctl get-volume "$1" 2>/dev/null | grep -q '\[MUTED\]'
}

notify() {
  # $1 title, $2 value (0-100), $3 icon, $4 sync-key
  local title="$1" value="$2" icon="$3" sync="$4"
  if [[ -x "$liquid_osd" ]]; then
    "$liquid_osd" --title "$title" --value "$value" --icon "$icon" >/dev/null 2>&1 &
    return 0
  fi

  notify-send \
    -a "volume-osd" \
    -h "string:x-canonical-private-synchronous:$sync" \
    -h "int:value:$value" \
    -h "string:hlcolor:#c084f5" \
    -i "$icon" \
    -t "$osd_timeout_ms" \
    "$title" "${value}%"
}

case "$action" in
  up)
    wpctl set-volume -l 1.0 "$sink" "${step}%+" >/dev/null 2>&1
    wpctl set-mute "$sink" 0 >/dev/null 2>&1
    v=$(read_pct "$sink")
    icon="audio-volume-high"
    (( v < 34 )) && icon="audio-volume-low"
    (( v >= 34 && v < 67 )) && icon="audio-volume-medium"
    notify "Volume" "$v" "$icon" "volume-osd"
    ;;
  down)
    wpctl set-volume "$sink" "${step}%-" >/dev/null 2>&1
    v=$(read_pct "$sink")
    icon="audio-volume-low"
    (( v >= 34 && v < 67 )) && icon="audio-volume-medium"
    (( v >= 67 )) && icon="audio-volume-high"
    (( v == 0 )) && icon="audio-volume-muted"
    notify "Volume" "$v" "$icon" "volume-osd"
    ;;
  mute)
    wpctl set-mute "$sink" toggle >/dev/null 2>&1
    v=$(read_pct "$sink")
    if is_muted "$sink"; then
      notify "Muted" 0 "audio-volume-muted" "volume-osd"
    else
      notify "Volume" "$v" "audio-volume-high" "volume-osd"
    fi
    ;;
  micmute)
    wpctl set-mute "$src" toggle >/dev/null 2>&1
    if is_muted "$src"; then
      notify "Mic muted" 0 "microphone-sensitivity-muted" "mic-osd"
    else
      notify "Mic on" 100 "microphone-sensitivity-high" "mic-osd"
    fi
    ;;
  bright-up|bright-down)
    if ! command -v brightnessctl >/dev/null 2>&1; then
      notify-send -a "volume-osd" "Brightness" "install brightnessctl"
      exit 1
    fi
    if [[ "$action" == "bright-up" ]]; then
      brightnessctl set "${step}%+" >/dev/null
    else
      brightnessctl set "${step}%-" >/dev/null
    fi
    cur=$(brightnessctl get)
    max=$(brightnessctl max)
    pct=$(( cur * 100 / max ))
    notify "Brightness" "$pct" "display-brightness" "brightness-osd"
    ;;
  *)
    echo "usage: $0 up|down|mute|micmute|bright-up|bright-down [step]" >&2
    exit 2
    ;;
esac
