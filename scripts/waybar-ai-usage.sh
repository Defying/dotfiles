#!/usr/bin/env bash
# Short Waybar click wrapper for the AI usage panels. The Rust panel owns the
# actual toggle logic; this wrapper just detaches it so Waybar can receive the
# next click while the panel is open.

set -u

service="${1:-codex}"
case "$service" in
  codex|claude) ;;
  *)
    echo "usage: $0 codex|claude" >&2
    exit 2
    ;;
esac

panel="${HOME}/.local/bin/ai-usage-panel"
if [[ ! -x "$panel" ]]; then
  panel="/home/ben/dotfiles/ai-usage-panel/target/release/ai-usage-panel"
fi

private_runtime_dir() {
  if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
    printf '%s\n' "$XDG_RUNTIME_DIR"
    return 0
  fi
  local dir="${TMPDIR:-/tmp}/waybar-ai-usage-$(id -u)"
  install -d -m 700 "$dir" 2>/dev/null || return 1
  printf '%s\n' "$dir"
}

runtime_dir="$(private_runtime_dir || printf '%s\n' "${HOME}/.cache")"
log_file="$runtime_dir/ai-usage-${service}.log"

setsid -f "$panel" "$service" >"$log_file" 2>&1
