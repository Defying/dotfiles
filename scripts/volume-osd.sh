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
bright_manual_marker="$hypr_runtime_dir/brightness-manual-active"
bright_target_file="$hypr_runtime_dir/brightness-target"
bright_lock_file="$hypr_runtime_dir/brightness-fade.lock"
backlight_dir="/sys/class/backlight/apple-panel-bl"

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
# the target percent so the OSD can show the final value right away. Held-key
# repeats update one shared target, so repeat events do not spawn overlapping
# fades that fight over the panel.
fade_brightness() {
  local delta_pct="$1"
  local cur max base delta target pid pct

  brightness_worker_alive() {
    local pid="$1"
    [[ -n "$pid" && -d "/proc/$pid" ]]
  }

  read_brightness_raw() {
    if [[ -r "$backlight_dir/brightness" ]]; then
      sed -n '1p' "$backlight_dir/brightness"
    else
      brightnessctl get 2>/dev/null
    fi
  }

  read_brightness_max() {
    if [[ -r "$backlight_dir/max_brightness" ]]; then
      sed -n '1p' "$backlight_dir/max_brightness"
    else
      brightnessctl max 2>/dev/null
    fi
  }

  raw_delta_from_pct() {
    local pct="$1" max="$2"
    if (( pct >= 0 )); then
      printf '%s\n' $(((max * pct + 50) / 100))
    else
      printf '%s\n' $((-((max * -pct + 50) / 100)))
    fi
  }

  write_brightness_raw() {
    local value="$1"
    if [[ -w "$backlight_dir/brightness" ]]; then
      printf '%s\n' "$value" > "$backlight_dir/brightness"
    else
      brightnessctl -q set "$value" >/dev/null 2>&1
    fi
  }

  start_brightness_worker() {
    (
      local cur max target delta abs step_raw next idle tick
      tick="${BRIGHT_FADE_TICK:-0.016}"
      idle=0
      while :; do
        : > "$bright_manual_marker"
        cur=$(read_brightness_raw) || exit 0
        max=$(read_brightness_max) || exit 0
        target="$(sed -n '1p' "$bright_target_file" 2>/dev/null)"
        [[ "$cur" =~ ^[0-9]+$ && "$max" =~ ^[0-9]+$ ]] || exit 0
        [[ "$target" =~ ^[0-9]+$ ]] || exit 0
        (( target < 1 )) && target=1
        (( target > max )) && target=$max

        delta=$(( target - cur ))
        abs="${delta#-}"
        if (( abs <= 1 )); then
          write_brightness_raw "$target" || exit 0
          idle=$(( idle + 1 ))
          (( idle >= 2 )) && break
        else
          idle=0
          step_raw=$(( delta / 3 ))
          if (( step_raw == 0 )); then
            if (( delta > 0 )); then
              step_raw=1
            else
              step_raw=-1
            fi
          fi
          next=$(( cur + step_raw ))
          write_brightness_raw "$next" || exit 0
        fi
        sleep "$tick"
      done
      rm -f "$bright_fade_pidfile"
    ) >/dev/null 2>&1 &
    echo $! > "$bright_fade_pidfile"
    disown
  }

  {
    flock -x 9
    : > "$bright_manual_marker"
    cur=$(read_brightness_raw)
    max=$(read_brightness_max)
    [[ "$cur" =~ ^[0-9]+$ && "$max" =~ ^[0-9]+$ ]] || return 1
    pid="$(sed -n '1p' "$bright_fade_pidfile" 2>/dev/null || true)"
    if brightness_worker_alive "$pid"; then
      base="$(sed -n '1p' "$bright_target_file" 2>/dev/null || true)"
      [[ "$base" =~ ^[0-9]+$ ]] || base="$cur"
    else
      base="$cur"
    fi

    delta="$(raw_delta_from_pct "$delta_pct" "$max")"
    target=$(( base + delta ))
    (( target < 1 )) && target=1
    (( target > max )) && target=$max
    printf '%s\n' "$target" > "$bright_target_file"

    if ! brightness_worker_alive "$pid"; then
      start_brightness_worker
    fi
    pct=$(awk -v v="$target" -v m="$max" 'BEGIN { printf "%d", v * 100 / m + 0.5 }')
  } 9>"$bright_lock_file"

  printf '%s\n' "$pct"
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
