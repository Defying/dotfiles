#!/usr/bin/env bash
# Rebuild Aquamarine from main and install over the Fedora package.
# Fixes the Asahi split-DRM bug where Wayland clients fall back to llvmpipe.
# Required while sdegler/hyprland copr ships <= v0.11.0 (no #291 yet).
# See ~/.claude/projects/-home-ben/memory/project_asahi_wayland_gpu.md
set -euo pipefail

SRC="${HOME}/src/aquamarine"
LIB="/usr/lib64/libaquamarine.so.0.11.0"
BACKUP="${LIB}.fc44-original"

if [[ ! -d "$SRC/.git" ]]; then
  mkdir -p "$(dirname "$SRC")"
  git clone https://github.com/hyprwm/aquamarine.git "$SRC"
fi

cd "$SRC"
git fetch --quiet origin main
git checkout --quiet main
git reset --hard --quiet origin/main

cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr >/dev/null
cmake --build build --parallel

NEW="$SRC/build/libaquamarine.so.0.11.0"
if ! strings "$NEW" | grep -q "sole renderD on the system"; then
  echo "ERROR: built lib missing the #291 render-node fallback — refusing to install"
  exit 1
fi

if [[ ! -f "$BACKUP" ]]; then
  sudo cp -a "$LIB" "$BACKUP"
  echo "backed up original to $BACKUP"
fi

sudo install -m 0755 "$NEW" "$LIB"
echo "installed new aquamarine. log out + back in to apply:  hyprctl dispatch exit"
echo "verify after restart:  eglinfo 2>/dev/null | grep -A1 'Wayland platform' | grep renderer  (should say Apple M1)"
echo "rollback:  sudo cp $BACKUP $LIB"
