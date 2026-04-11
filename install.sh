#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="$REPO_DIR/backups/$(date +%Y%m%d-%H%M%S)"
DID_BACKUP=0

link_file() {
  local source_rel="$1"
  local target="$2"
  local source="$REPO_DIR/$source_rel"

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

link_file "zsh/.zshenv" "$HOME/.zshenv"
link_file "zsh/.zprofile" "$HOME/.zprofile"
link_file "zsh/.zshrc" "$HOME/.zshrc"
link_file "git/.gitconfig" "$HOME/.gitconfig"
link_file "config/motd/omens-motd.sh" "$HOME/.config/motd/omens-motd.sh"

if [ ! -f "$HOME/.zshrc.local" ] && [ -f "$REPO_DIR/zsh/.zshrc.local.example" ]; then
  cp "$REPO_DIR/zsh/.zshrc.local.example" "$HOME/.zshrc.local"
  echo "create $HOME/.zshrc.local from example"
fi

if [ "$DID_BACKUP" -eq 0 ]; then
  rmdir "$BACKUP_DIR" 2>/dev/null || true
fi

echo
echo "done. reload with: exec zsh -l"
