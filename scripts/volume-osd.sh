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

private_runtime_dir() {
  if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
    printf '%s\n' "$XDG_RUNTIME_DIR"
    return 0
  fi
  local name="${1:-hypr-runtime}"
  local dir="${TMPDIR:-/tmp}/${name}-$(id -u)"
  install -d -m 700 "$dir" 2>/dev/null || return 1
  printf '%s\n' "$dir"
}

liquid_runtime_dir="$(private_runtime_dir liquid-osd || printf '%s\n' "${HOME}/.cache/liquid-osd")"
hypr_runtime_dir="$(private_runtime_dir hypr-runtime || printf '%s\n' "${HOME}/.cache/hypr-runtime")"
liquid_osd_sock="$liquid_runtime_dir/liquid-osd.sock"
bright_fade_pidfile="$hypr_runtime_dir/brightness-fade.pid"

tick_sound="${VOLUME_TICK_SOUND:-/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga}"
play_tick() {
  # Quiet feedback click on volume steps. Fired detached so it never blocks
  # the OSD. Only up/down call this (not mute/brightness). Override the sample
  # with VOLUME_TICK_SOUND, or set it empty to silence.
  [[ -n "$tick_sound" && -r "$tick_sound" ]] || return 0
  paplay --volume=20000 "$tick_sound" >/dev/null 2>&1 &
  disown
}

# Quick brightness step: compute the target up front, fire the small fade
# in the background so the foreground script returns immediately, and echo
# the target percent so the OSD can show the final value right away. Holding
# the key repeats from the live brightness each time, so each press still
# advances the total target even when the previous fade is mid-glide.
fade_brightness() {
  local delta_pct="$1"
  local steps="${BRIGHT_FADE_STEPS:-10}"
  local total_ms="${BRIGHT_FADE_MS:-120}"
  (( steps > 12 )) && steps=12   # bounded — keeps a tap cheap (≤12 sysfs writes)

  # Supersede any in-flight fade first, so held-key repeats (repeat_rate=40)
  # don't stack overlapping fades that fight each other. Kill the fade subshell
  # AND its current child (sleep/brightnessctl) — a plain kill of the subshell
  # alone leaves the in-flight step running. Read live brightness *after* so we
  # glide from wherever the previous fade actually got to.
  if [[ -f "$bright_fade_pidfile" ]]; then
    local oldpid; oldpid="$(cat "$bright_fade_pidfile" 2>/dev/null)"
    if [[ -n "$oldpid" ]]; then
      pkill -P "$oldpid" 2>/dev/null
      kill "$oldpid" 2>/dev/null
    fi
  fi

  local cur max target
  cur=$(brightnessctl get)
  max=$(brightnessctl max)
  target=$(( cur + (max * delta_pct + 50) / 100 ))
  (( target < 1 ))   && target=1
  (( target > max )) && target=$max

  local delay values
  delay=$(awk -v t="$total_ms" -v n="$steps" 'BEGIN { printf "%.4f", (t/1000) / n }')
  # Ease-out cubic (1-(1-t)^3): fast off the mark, settles gently — reads as
  # smooth rather than the old 3-step linear jump. Values precomputed up front
  # so the fade is one flat subshell loop (no pipeline → no orphan grandchildren
  # to chase when superseding).
  values=$(awk -v c="$cur" -v tg="$target" -v n="$steps" \
      'BEGIN { for (i = 1; i <= n; i++) { t = i / n; e = 1 - (1 - t)^3; printf "%d ", c + (tg - c) * e } }')

  (
    for value in $values; do
      brightnessctl -q set "$value" >/dev/null 2>&1 || exit 0
      sleep "$delay"
    done
  ) >/dev/null 2>&1 &
  echo $! > "$bright_fade_pidfile"
  disown

  awk -v v="$target" -v m="$max" 'BEGIN { printf "%d", v * 100 / m + 0.5 }'
}

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
    # Fast path: if the OSD daemon is already up, push the payload straight
    # into its unix socket so we skip Python startup entirely. The protocol
    # is a single JSON line — see liquid-osd.py:send_update.
    if [[ -S "$liquid_osd_sock" ]]; then
      local payload
      printf -v payload '{"title":"%s","value":%d,"icon":"%s"}\n' "$title" "$value" "$icon"
      if printf '%s' "$payload" | socat -t 0.1 - "UNIX-CONNECT:$liquid_osd_sock" >/dev/null 2>&1; then
        return 0
      fi
    fi
    # Cold start (no daemon yet, or socket got cleaned up).
    "$liquid_osd" --title "$title" --value "$value" --icon "$icon" >/dev/null 2>&1 &
    disown
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
    play_tick
    v=$(read_pct "$sink")
    icon="audio-volume-high"
    (( v < 34 )) && icon="audio-volume-low"
    (( v >= 34 && v < 67 )) && icon="audio-volume-medium"
    notify "volume" "$v" "$icon" "volume-osd"
    ;;
  down)
    wpctl set-volume "$sink" "${step}%-" >/dev/null 2>&1
    play_tick
    v=$(read_pct "$sink")
    icon="audio-volume-low"
    (( v >= 34 && v < 67 )) && icon="audio-volume-medium"
    (( v >= 67 )) && icon="audio-volume-high"
    (( v == 0 )) && icon="audio-volume-muted"
    notify "volume" "$v" "$icon" "volume-osd"
    ;;
  mute)
    wpctl set-mute "$sink" toggle >/dev/null 2>&1
    v=$(read_pct "$sink")
    if is_muted "$sink"; then
      notify "muted" 0 "audio-volume-muted" "volume-osd"
    else
      notify "volume" "$v" "audio-volume-high" "volume-osd"
    fi
    ;;
  micmute)
    wpctl set-mute "$src" toggle >/dev/null 2>&1
    if is_muted "$src"; then
      notify "mic muted" 0 "microphone-sensitivity-muted" "mic-osd"
    else
      notify "mic on" 100 "microphone-sensitivity-high" "mic-osd"
    fi
    ;;
  kbd-up|kbd-down)
    dev=kbd_backlight
    if ! command -v brightnessctl >/dev/null 2>&1; then
      exit 1
    fi
    if [[ "$action" == "kbd-up" ]]; then
      brightnessctl -q -d "$dev" set "${step}%+" >/dev/null 2>&1
    else
      brightnessctl -q -d "$dev" set "${step}%-" >/dev/null 2>&1
    fi
    cur=$(brightnessctl -d "$dev" get 2>/dev/null)
    max=$(brightnessctl -d "$dev" max 2>/dev/null)
    pct=$(awk -v c="${cur:-0}" -v m="${max:-1}" 'BEGIN { printf "%d", (m>0 ? c*100/m : 0) + 0.5 }')
    notify "keyboard" "$pct" "keyboard-brightness" "kbd-osd"
    ;;
  bright-up|bright-down)
    if ! command -v brightnessctl >/dev/null 2>&1; then
      notify-send -a "volume-osd" "Brightness" "install brightnessctl"
      exit 1
    fi
    if [[ "$action" == "bright-up" ]]; then
      pct=$(fade_brightness "$step")
    else
      pct=$(fade_brightness "-$step")
    fi
    notify "brightness" "$pct" "display-brightness" "brightness-osd"
    ;;
  *)
    echo "usage: $0 up|down|mute|micmute|bright-up|bright-down [step]" >&2
    exit 2
    ;;
esac
