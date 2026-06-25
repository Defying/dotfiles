#!/usr/bin/env bash
# Short Waybar click wrapper for the Bambu printer panel. The Rust panel owns
# toggle behavior; this only detaches it so Waybar remains responsive.

set -u

panel="${HOME}/.local/bin/bambu-printer-panel"
if [[ ! -x "$panel" ]]; then
  panel="/home/ben/dotfiles/bambu-printer-panel/target/release/bambu-printer-panel"
fi

private_runtime_dir() {
  if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
    printf '%s\n' "$XDG_RUNTIME_DIR"
    return 0
  fi
  local dir="${TMPDIR:-/tmp}/bambu-printer-panel-$(id -u)"
  install -d -m 700 "$dir" 2>/dev/null || return 1
  printf '%s\n' "$dir"
}

runtime_dir="$(private_runtime_dir || printf '%s\n' "${HOME}/.cache")"
log_file="$runtime_dir/bambu-printer-panel.log"

setsid -f "$panel" >"$log_file" 2>&1
