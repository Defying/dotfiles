#!/usr/bin/env bash
# Quick settings launcher for Waybar; falls back to fuzzel if the panel fails.

set -u

panel="/home/ben/dotfiles/scripts/quick-settings-panel.py"
private_runtime_dir() {
  if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
    printf '%s\n' "$XDG_RUNTIME_DIR"
    return 0
  fi
  local dir="${TMPDIR:-/tmp}/waybar-quick-settings-$(id -u)"
  install -d -m 700 "$dir" 2>/dev/null || return 1
  printf '%s\n' "$dir"
}

runtime_dir="$(private_runtime_dir || printf '%s\n' "${HOME}/.cache")"
pid_file="$runtime_dir/quick-settings-panel.pid"
log_file="$runtime_dir/quick-settings-panel.log"
awake_pid_file="$runtime_dir/awake.pid"
awake_state_file="$runtime_dir/awake.json"
power_state_file="$runtime_dir/power.json"
low_power_profile="powersave"
default_power_profile="balanced"
battery_device="/org/freedesktop/UPower/devices/battery_macsmc_battery"
tailscale_admin_url="https://login.tailscale.com/admin/machines"

if [[ -r "$pid_file" ]]; then
  panel_pid="$(sed -n '1p' "$pid_file")"
  if [[ "$panel_pid" =~ ^[0-9]+$ ]] && kill -0 "$panel_pid" >/dev/null 2>&1; then
    kill "$panel_pid" >/dev/null 2>&1 || true
    exit 0
  fi
  rm -f "$pid_file"
fi

existing_pids="$(pgrep -u "$USER" -f '/home/ben/dotfiles/scripts/quick-settings-panel.py' || true)"
if [[ -n "$existing_pids" ]]; then
  printf '%s\n' "$existing_pids" | xargs -r kill >/dev/null 2>&1 || true
  exit 0
fi

if [[ -x "$panel" ]]; then
  "$panel" >"$log_file" 2>&1 &
  panel_pid=$!
  sleep 0.15
  if kill -0 "$panel_pid" >/dev/null 2>&1; then
    disown "$panel_pid" 2>/dev/null || true
    exit 0
  fi
fi

notify() {
  notify-send -a "quick-settings" -i "preferences-system-symbolic" -t 2200 "$1" "${2:-}" >/dev/null 2>&1 || true
}

stop_awake() {
  local pid
  pid="$(python3 - "$awake_state_file" "$awake_pid_file" <<'PY' 2>/dev/null || true
import json
import sys
from pathlib import Path

state = Path(sys.argv[1])
legacy = Path(sys.argv[2])
if state.exists():
    try:
        print(json.loads(state.read_text()).get("pid") or "")
        raise SystemExit
    except Exception:
        pass
if legacy.exists():
    print(legacy.read_text().strip())
PY
)"
  if [[ "$pid" =~ ^[0-9]+$ ]]; then
    kill -- "-$pid" >/dev/null 2>&1 || kill "$pid" >/dev/null 2>&1 || true
  fi
  rm -f "$awake_state_file" "$awake_pid_file"
}

start_awake() {
  local mode="$1"
  local what="$2"
  local label="$3"
  if ! command -v systemd-inhibit >/dev/null 2>&1; then
    notify "Awake" "systemd-inhibit not found"
    return 0
  fi
  stop_awake
  setsid systemd-inhibit \
    --what="$what" \
    --who=quick-settings \
    --why="Awake mode: $label" \
    --mode=block \
    sleep infinity >/dev/null 2>&1 &
  local pid=$!
  printf '%s\n' "$pid" >"$awake_pid_file"
  python3 - "$awake_state_file" "$pid" "$mode" "$what" <<'PY' >/dev/null 2>&1 || true
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(json.dumps({"pid": int(sys.argv[2]), "mode": sys.argv[3], "what": sys.argv[4]}), encoding="utf-8")
PY
  notify "Awake" "$label"
}

profile_exists() {
  tuned-adm list 2>/dev/null | awk '{ if ($1 == "-" && $2 == p) found = 1 } END { exit found ? 0 : 1 }' p="$1"
}

current_power_profile() {
  tuned-adm active 2>/dev/null | awk -F': ' '/Current active profile:/ { print $2; exit }'
}

write_power_restore() {
  install -d -m 700 "$(dirname "$power_state_file")" 2>/dev/null || return 0
  python3 - "$power_state_file" "$1" <<'PY' >/dev/null 2>&1 || true
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(json.dumps({"restore_profile": sys.argv[2]}, sort_keys=True), encoding="utf-8")
PY
}

read_power_restore() {
  python3 - "$power_state_file" "$default_power_profile" <<'PY' 2>/dev/null || printf '%s\n' "$default_power_profile"
import json
import sys
from pathlib import Path

try:
    print(json.loads(Path(sys.argv[1]).read_text()).get("restore_profile") or sys.argv[2])
except Exception:
    print(sys.argv[2])
PY
}

set_power_profile() {
  sudo -n tuned-adm profile "$1" >/dev/null 2>&1
}

wifi_state="unknown"
if command -v nmcli >/dev/null 2>&1; then
  wifi_state="$(nmcli radio wifi 2>/dev/null || echo unknown)"
fi

bt_state="unknown"
if command -v bluetoothctl >/dev/null 2>&1; then
  bt_state="$(bluetoothctl show 2>/dev/null | awk -F': ' '/Powered:/ {print tolower($2); exit}')"
fi

awake_mode="off"
awake_pid="$(python3 - "$awake_state_file" "$awake_pid_file" <<'PY' 2>/dev/null || true
import json
import sys
from pathlib import Path

state = Path(sys.argv[1])
legacy = Path(sys.argv[2])
if state.exists():
    try:
        data = json.loads(state.read_text())
        print(f"{data.get('pid') or ''}\t{data.get('mode') or ''}")
        raise SystemExit
    except Exception:
        pass
if legacy.exists():
    print(f"{legacy.read_text().strip()}\tdisplay")
PY
)"
if [[ -n "$awake_pid" ]]; then
  awake_mode="${awake_pid#*$'\t'}"
  awake_pid="${awake_pid%%$'\t'*}"
  if [[ "$awake_pid" =~ ^[0-9]+$ ]] && kill -0 "$awake_pid" >/dev/null 2>&1; then
    :
  else
    awake_mode="off"
    rm -f "$awake_state_file" "$awake_pid_file"
  fi
fi

dnd_state="off"
if makoctl mode 2>/dev/null | grep -qE '(^|[* ])do-not-disturb$'; then
  dnd_state="on"
fi

power_profile="$(current_power_profile)"
low_power_state="unsupported"
if command -v tuned-adm >/dev/null 2>&1 && profile_exists "$low_power_profile"; then
  if [[ "$power_profile" == "$low_power_profile" ]]; then
    low_power_state="on"
  else
    low_power_state="off"
  fi
fi

charge_limit_state="unsupported"
if busctl get-property org.freedesktop.UPower "$battery_device" org.freedesktop.UPower.Device ChargeThresholdSupported 2>/dev/null | grep -q 'b true'; then
  if busctl get-property org.freedesktop.UPower "$battery_device" org.freedesktop.UPower.Device ChargeThresholdEnabled 2>/dev/null | grep -q 'b true'; then
    charge_limit_state="on"
  else
    charge_limit_state="off"
  fi
fi

tailscale_state="missing"
if command -v tailscale >/dev/null 2>&1; then
  tailscale_state="$(
    tailscale status --json 2>/dev/null | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    print("error")
    raise SystemExit

state = data.get("BackendState") or "unknown"
auth_url = data.get("AuthURL") or ""
health = data.get("Health") or []
if auth_url or "login" in state.lower():
    print("login needed")
elif state == "Running" and health:
    print("warning")
elif state == "Running":
    print("on")
elif state in ("Stopped", "NoState"):
    print("off")
else:
    print(state.lower())
' 2>/dev/null || printf 'error'
  )"
fi

choice=$(
  {
    printf 'wifi: %s\n' "$wifi_state"
    printf 'bluetooth: %s\n' "$bt_state"
    printf 'tailscale: %s\n' "$tailscale_state"
    printf 'dnd: %s\n' "$dnd_state"
    printf 'system awake: %s\n' "$([[ "$awake_mode" == "system" ]] && printf on || printf off)"
    printf 'display awake: %s\n' "$([[ "$awake_mode" == "display" ]] && printf on || printf off)"
    printf 'display sleep now\n'
    printf 'low power: %s\n' "$low_power_state"
    printf 'charge limit: %s\n' "$charge_limit_state"
    printf 'awake blockers\n'
    printf 'audio devices\n'
    printf 'network settings\n'
    printf 'tailscale status\n'
    printf 'tailscale admin\n'
    printf 'sound settings\n'
    printf 'brightness 25%%\n'
    printf 'brightness 50%%\n'
    printf 'brightness 100%%\n'
    printf 'keyboard 0%%\n'
    printf 'keyboard 50%%\n'
    printf 'keyboard 100%%\n'
    printf 'reload waybar\n'
    printf 'reload hyprland\n'
    printf 'lock\n'
    printf 'power\n'
  } | fuzzel --dmenu --prompt='settings  ' --lines=24 --width=36
)
choice="${choice%$'\n'}"
[[ -z "$choice" ]] && exit 0

case "$choice" in
  wifi:*)
    if [[ "$wifi_state" == "enabled" ]]; then
      nmcli radio wifi off && notify "Wi-Fi off"
    else
      nmcli radio wifi on && notify "Wi-Fi on"
    fi
    ;;
  bluetooth:*)
    if [[ "$bt_state" == "yes" ]]; then
      bluetoothctl power off >/dev/null && notify "Bluetooth off"
    else
      bluetoothctl power on >/dev/null && notify "Bluetooth on"
    fi
    ;;
  tailscale:*)
    if ! command -v tailscale >/dev/null 2>&1; then
      notify "tailscale" "not installed"
    elif [[ "$tailscale_state" == "on" || "$tailscale_state" == "warning" ]]; then
      tailscale down >/dev/null 2>&1 && notify "tailscale" "off"
    else
      sudo -n systemctl enable --now tailscaled >/dev/null 2>&1 || true
      tailscale up >/dev/null 2>&1
      notify "tailscale" "$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; data=json.load(sys.stdin); print((data.get("BackendState") or "unknown").lower())' 2>/dev/null || printf 'started')"
    fi
    pkill -RTMIN+11 -x waybar >/dev/null 2>&1 || true
    ;;
  "audio devices")
    /home/ben/dotfiles/scripts/audio-menu.sh
    ;;
  dnd:*)
    if [[ "$dnd_state" == "on" ]]; then
      makoctl mode -r do-not-disturb >/dev/null 2>&1
      notify "Do Not Disturb off"
    else
      makoctl mode -a do-not-disturb >/dev/null 2>&1
      notify "Do Not Disturb on"
    fi
    pkill -RTMIN+10 -x waybar >/dev/null 2>&1 || true
    ;;
  system\ awake:*)
    if [[ "$awake_mode" == "system" ]]; then
      stop_awake
      notify "Awake off"
    else
      start_awake system sleep "system awake"
    fi
    ;;
  display\ awake:*)
    if [[ "$awake_mode" == "display" ]]; then
      stop_awake
      notify "Awake off"
    else
      start_awake display idle:sleep "display awake"
    fi
    ;;
  "display sleep now")
    hyprctl dispatch dpms off >/dev/null 2>&1
    notify "Display sleep"
    ;;
  low\ power:*)
    if [[ "$low_power_state" == "unsupported" ]]; then
      notify "Low Power" "not supported"
    elif [[ "$low_power_state" == "on" ]]; then
      restore="$(read_power_restore)"
      if [[ "$restore" == "$low_power_profile" ]] || ! profile_exists "$restore"; then
        restore="$default_power_profile"
      fi
      if set_power_profile "$restore"; then
        rm -f "$power_state_file"
        notify "Low Power" "off · $restore"
      else
        notify "Low Power" "sudo unavailable"
      fi
    else
      if [[ -n "$power_profile" && "$power_profile" != "$low_power_profile" ]]; then
        write_power_restore "$power_profile"
      else
        write_power_restore "$default_power_profile"
      fi
      if set_power_profile "$low_power_profile"; then
        notify "Low Power" "on"
      else
        notify "Low Power" "sudo unavailable"
      fi
    fi
    ;;
  charge\ limit:*)
    if [[ "$charge_limit_state" == "unsupported" ]]; then
      notify "Charge limit" "not supported"
    elif [[ "$charge_limit_state" == "on" ]]; then
      busctl call org.freedesktop.UPower "$battery_device" org.freedesktop.UPower.Device EnableChargeThreshold b false >/dev/null 2>&1
      notify "Charge limit off"
    else
      busctl call org.freedesktop.UPower "$battery_device" org.freedesktop.UPower.Device EnableChargeThreshold b true >/dev/null 2>&1
      notify "Charge limit on"
    fi
    ;;
  "awake blockers")
    blockers="$(systemd-inhibit --list --no-pager --no-legend 2>/dev/null | sed -n '1,5p')"
    notify "Awake blockers" "${blockers:-none}"
    ;;
  "network settings")
    setsid -f nm-connection-editor >/dev/null 2>&1
    ;;
  "tailscale status")
    setsid -f ghostty -e sh -lc 'tailscale status; printf "\npress enter to close "; read -r _' >/dev/null 2>&1
    ;;
  "tailscale admin")
    setsid -f xdg-open "$tailscale_admin_url" >/dev/null 2>&1
    ;;
  "sound settings")
    setsid -f pavucontrol >/dev/null 2>&1
    ;;
  brightness*)
    pct="${choice#brightness }"
    brightnessctl -q set "$pct" && notify "Brightness" "$pct"
    ;;
  keyboard*)
    pct="${choice#keyboard }"
    brightnessctl -q -d kbd_backlight set "$pct" && notify "Keyboard" "$pct"
    ;;
  "reload waybar")
    pkill -x waybar
    setsid -f waybar >"$runtime_dir/waybar.log" 2>&1
    ;;
  "reload hyprland")
    hyprctl reload >/dev/null && notify "Hyprland reloaded"
    ;;
  lock)
    loginctl lock-session
    ;;
  power)
    setsid -f /home/ben/.local/bin/hypr-power-menu >/dev/null 2>&1
    ;;
esac
