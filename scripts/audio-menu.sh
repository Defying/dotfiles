#!/usr/bin/env bash
# Quick audio picker for waybar pulseaudio click.
# Lists paired bluetooth devices for one-click connect/disconnect,
# plus mute toggle and a shortcut to pavucontrol.

set -u

private_runtime_dir() {
  if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
    printf '%s\n' "$XDG_RUNTIME_DIR"
    return 0
  fi
  local dir="${TMPDIR:-/tmp}/waybar-audio-$(id -u)"
  install -d -m 700 "$dir" 2>/dev/null || return 1
  printf '%s\n' "$dir"
}

runtime_dir="$(private_runtime_dir || printf '%s\n' "${HOME}/.cache")"
LOG="$runtime_dir/audio-menu.log"
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >>"$LOG"; }
log "--- audio-menu invoked ---"

# Friendly notify wrapper (mako)
notify() {
  local title="$1" body="${2:-}" icon="${3:-audio-headphones}"
  notify-send -a "audio-menu" -i "$icon" -t 3000 \
    -h "string:x-canonical-private-synchronous:audio-menu" \
    "$title" "$body" 2>>"$LOG"
}

menu_items=()
declare -A actions=()
declare -A names=()

# Paired bluetooth devices
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  mac=$(awk '{print $2}' <<<"$line")
  name=$(cut -d' ' -f3- <<<"$line")
  [[ -z "$mac" ]] && continue

  if bluetoothctl info "$mac" 2>/dev/null | grep -q "Connected: yes"; then
    label="  disconnect  $name"
    actions["$label"]="disconnect $mac"
  else
    label="  connect     $name"
    actions["$label"]="connect $mac"
  fi
  names["$label"]="$name"
  menu_items+=("$label")
done < <(bluetoothctl devices Paired 2>/dev/null)

if (( ${#menu_items[@]} > 0 )); then
  menu_items+=("─────────────")
  actions["─────────────"]="noop"
fi

# Mute toggle + settings
mute_state=$(wpctl get-volume @DEFAULT_AUDIO_SINK@ 2>/dev/null)
if [[ "$mute_state" == *"[MUTED]"* ]]; then
  menu_items+=("  unmute output")
  actions["  unmute output"]="unmute"
else
  menu_items+=("  mute output")
  actions["  mute output"]="mute"
fi

menu_items+=("  sound settings")
actions["  sound settings"]="pavucontrol"

choice=$(printf '%s\n' "${menu_items[@]}" | fuzzel --dmenu --prompt='audio  ' --lines=10 --width=36 2>>"$LOG")
choice="${choice%$'\n'}"
log "choice: '$choice'"

[[ -z "$choice" ]] && { log "empty choice, exit"; exit 0; }

action="${actions[$choice]:-noop}"
log "action: '$action'"

case "$action" in
  noop)
    ;;
  connect\ *)
    mac="${action#connect }"
    nm="${names[$choice]:-$mac}"
    notify "Connecting…" "$nm" "audio-headphones"
    if out=$(bluetoothctl connect "$mac" 2>&1); then
      log "connect ok: $out"
      notify "Connected" "$nm" "audio-headphones"
    else
      log "connect fail: $out"
      notify "Connect failed" "$nm — $(printf '%s' "$out" | tail -1)" "dialog-error"
    fi
    ;;
  disconnect\ *)
    mac="${action#disconnect }"
    nm="${names[$choice]:-$mac}"
    notify "Disconnecting…" "$nm" "audio-headphones"
    if out=$(bluetoothctl disconnect "$mac" 2>&1); then
      log "disconnect ok: $out"
      notify "Disconnected" "$nm" "audio-headphones"
    else
      log "disconnect fail: $out"
      notify "Disconnect failed" "$nm" "dialog-error"
    fi
    ;;
  mute)
    wpctl set-mute @DEFAULT_AUDIO_SINK@ 1
    ;;
  unmute)
    wpctl set-mute @DEFAULT_AUDIO_SINK@ 0
    ;;
  pavucontrol)
    setsid -f pavucontrol >/dev/null 2>&1
    ;;
esac

log "done"
