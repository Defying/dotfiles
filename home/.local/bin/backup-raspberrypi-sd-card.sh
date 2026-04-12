#!/bin/zsh
set -euo pipefail

export SD_TARGET_LABEL="Raspberry Pi"
export SD_TARGET_SLUG="raspberry-pi"
export SD_TARGET_HOST="ben@pi.local"
export SD_TARGET_MEDIA_KIND="USB SSD"
export SD_BACKUP_ROOT="/Volumes/Carve/Backups/raspberry-pi/images"
export SD_IMAGE_PREFIX="raspberry-pi-sd"
export SD_EXPECTED_MEDIA_NAME="SA510 2.5 1000GB"
export SD_EXPECTED_SIZE_BYTES="1000204886016"
export SD_EXPECTED_CONTENT="FDisk_partition_scheme"
export SD_EXPECTED_REMOVABLE_MEDIA="false"

exec "${0:A:h}/backup-pi-sd-card.sh" "$@"
