#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/Volumes/Carve/Projects/dotfiles}"
BRANCH="${BRANCH:-main}"
LOCK_DIR="${TMPDIR:-/tmp}/dotfiles-auto-sync.lock"

cd "$REPO_DIR"

if [ ! -d .git ]; then
  echo "not a git repo: $REPO_DIR" >&2
  exit 1
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "sync already running"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

git update-index -q --refresh || true
git add -u

if git diff --cached --quiet; then
  echo "nothing to sync"
  exit 0
fi

changed_files=$(git diff --cached --name-only | wc -l | tr -d ' ')
stamp=$(date "+%Y-%m-%d %H:%M")

git commit -m "chore: sync dotfiles ($stamp)"
git push origin "$BRANCH"

commit=$(git rev-parse --short HEAD)
echo "synced $commit ($changed_files files)"
