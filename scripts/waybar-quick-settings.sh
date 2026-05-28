#!/usr/bin/env bash
# Quick settings launcher for Waybar; falls back to fuzzel if the panel fails.

set -u

panel="/home/ben/dotfiles/scripts/quick-settings-panel.py"
runtime_dir="${XDG_RUNTIME_DIR:-/tmp}"
pid_file="$runtime_dir/quick-settings-panel.pid"

if [[ -r "$pid_file" ]]; then
  panel_pid="$(sed -n '1p' "$pid_file")"
  if [[ "$panel_pid" =~ ^[0-9]+$ ]] && kill -0 "$panel_pid" >/dev/null 2>&1; then
    kill "$panel_pid" >/dev/null 2>&1 || true
    exit 0
  fi
  rm -f "$pid_file"
fi

existing_pids="$(pgrep -u "$USER" -f '^python3 /home/ben/dotfiles/scripts/quick-settings-panel.py$|^/home/ben/dotfiles/scripts/quick-settings-panel.py$' || true)"
if [[ -n "$existing_pids" ]]; then
  printf '%s\n' "$existing_pids" | xargs -r kill >/dev/null 2>&1 || true
  exit 0
fi

if [[ -x "$panel" ]]; then
  "$panel" >/tmp/quick-settings-panel.log 2>&1 &
  panel_pid=$!
  sleep 0.15
  if kill -0 "$panel_pid" >/dev/null 2>&1; then
    disown "$panel_pid" 2>/dev/null || true
    exit 0
  fi
fi

notify() {
  notify-send -a "quick-settings" -t 2200 "$1" "${2:-}" >/dev/null 2>&1 || true
}

wifi_state="unknown"
if command -v nmcli >/dev/null 2>&1; then
  wifi_state="$(nmcli radio wifi 2>/dev/null || echo unknown)"
fi

bt_state="unknown"
if command -v bluetoothctl >/dev/null 2>&1; then
  bt_state="$(bluetoothctl show 2>/dev/null | awk -F': ' '/Powered:/ {print tolower($2); exit}')"
fi

choice=$(
  {
    printf 'wifi: %s\n' "$wifi_state"
    printf 'bluetooth: %s\n' "$bt_state"
    printf 'audio devices\n'
    printf 'codex usage\n'
    printf 'codex login\n'
    printf 'codex web\n'
    printf 'claude usage\n'
    printf 'claude login\n'
    printf 'claude web\n'
    printf 'network settings\n'
    printf 'sound settings\n'
    printf 'brightness 25%%\n'
    printf 'brightness 50%%\n'
    printf 'brightness 100%%\n'
    printf 'reload waybar\n'
    printf 'reload hyprland\n'
    printf 'lock\n'
    printf 'power\n'
  } | fuzzel --dmenu --prompt='settings  ' --lines=14 --width=34
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
  "audio devices")
    /home/ben/dotfiles/scripts/audio-menu.sh
    ;;
  "codex usage")
    setsid -f xdg-open "https://chatgpt.com/codex/settings/usage" >/dev/null 2>&1
    ;;
  "codex login")
    if command -v ghostty >/dev/null 2>&1; then
      setsid -f ghostty -e codex login >/dev/null 2>&1
    elif command -v foot >/dev/null 2>&1; then
      setsid -f foot codex login >/dev/null 2>&1
    elif command -v kitty >/dev/null 2>&1; then
      setsid -f kitty codex login >/dev/null 2>&1
    elif command -v alacritty >/dev/null 2>&1; then
      setsid -f alacritty -e codex login >/dev/null 2>&1
    else
      setsid -f xdg-open "https://chatgpt.com/codex" >/dev/null 2>&1
    fi
    ;;
  "codex web")
    setsid -f xdg-open "https://chatgpt.com/codex" >/dev/null 2>&1
    ;;
  "claude usage")
    setsid -f xdg-open "https://claude.ai/settings/usage" >/dev/null 2>&1
    ;;
  "claude login")
    if command -v ghostty >/dev/null 2>&1; then
      setsid -f ghostty -e claude auth login --claudeai >/dev/null 2>&1
    elif command -v foot >/dev/null 2>&1; then
      setsid -f foot claude auth login --claudeai >/dev/null 2>&1
    elif command -v kitty >/dev/null 2>&1; then
      setsid -f kitty claude auth login --claudeai >/dev/null 2>&1
    elif command -v alacritty >/dev/null 2>&1; then
      setsid -f alacritty -e claude auth login --claudeai >/dev/null 2>&1
    else
      setsid -f xdg-open "https://claude.ai" >/dev/null 2>&1
    fi
    ;;
  "claude web")
    setsid -f xdg-open "https://claude.ai" >/dev/null 2>&1
    ;;
  "network settings")
    setsid -f nm-connection-editor >/dev/null 2>&1
    ;;
  "sound settings")
    setsid -f pavucontrol >/dev/null 2>&1
    ;;
  brightness*)
    pct="${choice#brightness }"
    brightnessctl -q set "$pct" && notify "Brightness" "$pct"
    ;;
  "reload waybar")
    pkill -x waybar
    setsid -f waybar >/tmp/waybar.log 2>&1
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
