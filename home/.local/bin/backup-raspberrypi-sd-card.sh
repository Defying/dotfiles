#!/bin/zsh
set -euo pipefail

export SD_TARGET_LABEL="Raspberry Pi"
export SD_TARGET_SLUG="raspberry-pi"
export SD_TARGET_HOST="ben@pi.local"
export SD_BACKUP_ROOT="/Volumes/Carve/Backups/raspberry-pi/images"
export SD_IMAGE_PREFIX="raspberry-pi-sd"

exec "${0:A:h}/backup-pi-sd-card.sh" "$@"
