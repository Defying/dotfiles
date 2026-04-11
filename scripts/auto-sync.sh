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

is_blocked_path() {
  local path="$1"
  case "$path" in
    *.local|*.pem|*.key|*.p12|*.pfx|*.mobileprovision|*.kdbx|*.sqlite|*.db)
      return 0
      ;;
    .env|.env.*|*/.env|*/.env.*)
      return 0
      ;;
    */.cloudflared/*|.cloudflared/*)
      return 0
      ;;
    */id_rsa|*/id_rsa.pub|*/id_ed25519|*/id_ed25519.pub|*/id_ecdsa|*/id_ecdsa.pub)
      return 0
      ;;
  esac
  return 1
}

has_secret_like_content() {
  local path="$1"
  local staged
  staged=$(git show ":$path" 2>/dev/null || true)
  [ -n "$staged" ] || return 1

  local -a patterns=(
    '-----BEGIN (OPENSSH|RSA|DSA|EC|PGP|AGE|.*PRIVATE KEY)-----'
    '-----BEGIN ARGO TUNNEL TOKEN-----'
    'gh[opusr]_[A-Za-z0-9_]{20,}'
    'github_pat_[A-Za-z0-9_]{20,}'
    'AKIA[0-9A-Z]{16}'
    'xox[baprs]-[A-Za-z0-9-]{10,}'
    'glpat-[A-Za-z0-9_-]{20,}'
  )

  local pattern
  for pattern in "${patterns[@]}"; do
    if printf '%s' "$staged" | LC_ALL=C rg -n -I --pcre2 "$pattern" >/dev/null 2>&1; then
      return 0
    fi
  done

  return 1
}

git update-index -q --refresh || true
git add -A

mapfile -t staged_paths < <(git diff --cached --name-only)
if [ "${#staged_paths[@]}" -eq 0 ]; then
  echo "nothing to sync"
  exit 0
fi

blocked=0
for path in "${staged_paths[@]}"; do
  if is_blocked_path "$path"; then
    echo "blocked sensitive path: $path" >&2
    blocked=1
    continue
  fi

  if has_secret_like_content "$path"; then
    echo "blocked secret-like content: $path" >&2
    blocked=1
  fi
done

if [ "$blocked" -ne 0 ]; then
  echo "sync aborted to avoid pushing secrets" >&2
  exit 1
fi

changed_files=${#staged_paths[@]}
stamp=$(date "+%Y-%m-%d %H:%M")

git commit -m "chore: sync dotfiles ($stamp)"
git push origin "$BRANCH"

commit=$(git rev-parse --short HEAD)
echo "synced $commit ($changed_files files)"
