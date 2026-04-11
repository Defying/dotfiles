#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="$REPO_DIR/backups/$(date +%Y%m%d-%H%M%S)"
DID_BACKUP=0

link_file() {
  local source="$1"
  local target="$2"

  mkdir -p "$(dirname "$target")"

  if [ -e "$target" ] || [ -L "$target" ]; then
    if [ -L "$target" ] && [ "$(readlink "$target")" = "$source" ]; then
      echo "ok    $target"
      return
    fi

    mkdir -p "$BACKUP_DIR$(dirname "$target")"
    mv "$target" "$BACKUP_DIR$target"
    DID_BACKUP=1
    echo "backup $target -> $BACKUP_DIR$target"
  fi

  ln -s "$source" "$target"
  echo "link  $target -> $source"
}

link_tree() {
  local source_rel="$1"
  local target_root="$2"
  local source_dir="$REPO_DIR/$source_rel"

  [ -d "$source_dir" ] || return 0

  while IFS= read -r -d '' source; do
    local rel="${source#$source_dir/}"
    local target="$target_root/$rel"
    link_file "$source" "$target"
  done < <(find "$source_dir" -type f -print0 | sort -z)
}

link_tree "home" "$HOME"
link_tree "zsh" "$HOME"
link_tree "git" "$HOME"
link_tree "config" "$HOME/.config"

if [ ! -f "$HOME/.zshrc.local" ] && [ -f "$REPO_DIR/zsh/.zshrc.local.example" ]; then
  cp "$REPO_DIR/zsh/.zshrc.local.example" "$HOME/.zshrc.local"
  echo "create $HOME/.zshrc.local from example"
fi

if [ "$DID_BACKUP" -eq 0 ]; then
  rmdir "$BACKUP_DIR" 2>/dev/null || true
fi

echo
echo "done. reload with: exec zsh -l"
