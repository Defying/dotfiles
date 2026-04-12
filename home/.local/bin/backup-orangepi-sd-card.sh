#!/bin/zsh
set -euo pipefail

export SD_TARGET_LABEL="Orange Pi"
export SD_TARGET_SLUG="orange-pi"
export SD_TARGET_HOST="ben@10.0.0.30"
export SD_BACKUP_ROOT="/Volumes/Carve/Backups/orange-pi/images"
export SD_IMAGE_PREFIX="orange-pi-sd"
export SD_EXPECTED_MEDIA_NAME="1081CS0"
export SD_EXPECTED_SIZE_BYTES="125671833600"
export SD_EXPECTED_CONTENT="FDisk_partition_scheme"

exec "${0:A:h}/backup-pi-sd-card.sh" "$@"
