#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:---install}"

files=(
  system/usr/local/bin/asahi-hyprland-disable-config
  system/usr/local/bin/asahi-hyprland-logged
  system/usr/local/bin/asahi-hyprland-rollback-plasma
  system/usr/local/bin/asahi-hyprland-safe
  system/usr/local/share/asahi-hyprland/hyprland-safe.conf
  system/usr/share/wayland-sessions/hyprland-logged.desktop
  system/usr/share/wayland-sessions/hyprland-safe.desktop
  system/usr/share/wayland-sessions/plasma-rollback-hyprland.desktop
)

usage() {
  printf 'usage: %s [--install|--check|--list]\n' "$0" >&2
}

target_for() {
  local rel="$1"
  printf '/%s\n' "${rel#system/}"
}

expected_mode_for() {
  local source="$1"

  if [ -x "$source" ]; then
    printf '755\n'
  else
    printf '644\n'
  fi
}

list_files() {
  local rel

  for rel in "${files[@]}"; do
    printf '%s -> %s\n' "$rel" "$(target_for "$rel")"
  done
}

install_files() {
  local rel source target expected_mode

  for rel in "${files[@]}"; do
    source="$repo_dir/$rel"
    target="$(target_for "$rel")"
    expected_mode="$(expected_mode_for "$source")"

    if [ ! -f "$source" ]; then
      printf 'missing source: %s\n' "$source" >&2
      exit 1
    fi

    sudo install -D -m "$expected_mode" "$source" "$target"
    printf 'installed %s mode=%s\n' "$target" "$expected_mode"
  done
}

check_files() {
  local rel source target expected_mode actual_mode actual_owner
  local failures=0

  for rel in "${files[@]}"; do
    source="$repo_dir/$rel"
    target="$(target_for "$rel")"
    expected_mode="$(expected_mode_for "$source")"

    if [ ! -f "$source" ]; then
      printf 'FAIL  missing source: %s\n' "$source"
      failures=$((failures + 1))
      continue
    fi

    if [ ! -f "$target" ]; then
      printf 'FAIL  missing installed file: %s\n' "$target"
      failures=$((failures + 1))
      continue
    fi

    if ! cmp -s "$source" "$target"; then
      printf 'FAIL  content differs: %s\n' "$target"
      failures=$((failures + 1))
      continue
    fi

    actual_mode="$(stat -c '%a' "$target")"
    actual_owner="$(stat -c '%U:%G' "$target")"

    if [ "$actual_mode" != "$expected_mode" ]; then
      printf 'FAIL  mode differs: %s expected=%s actual=%s\n' "$target" "$expected_mode" "$actual_mode"
      failures=$((failures + 1))
      continue
    fi

    if [ "$actual_owner" != "root:root" ]; then
      printf 'FAIL  owner differs: %s expected=root:root actual=%s\n' "$target" "$actual_owner"
      failures=$((failures + 1))
      continue
    fi

    printf 'ok    %s\n' "$target"
  done

  if [ "$failures" -ne 0 ]; then
    printf '\n%s system file check(s) failed.\n' "$failures" >&2
    exit 1
  fi
}

case "$mode" in
  --install|install)
    install_files
    ;;
  --check|check)
    check_files
    ;;
  --list|list)
    list_files
    ;;
  *)
    usage
    exit 2
    ;;
esac
