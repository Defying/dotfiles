#!/bin/zsh
set -euo pipefail

export FSB_TARGET_LABEL="Raspberry Pi"
export FSB_TARGET_SLUG="raspberry-pi"
export FSB_TARGET_HOST="ben@pi.local"
export FSB_TARGET_MEDIA_KIND="USB SSD"
export FSB_BACKUP_ROOT="/Volumes/Carve/Backups/raspberry-pi/filesystems"
export FSB_ARCHIVE_PREFIX="raspberry-pi-filesystems"
export FSB_EXPECTED_MEDIA_NAME="SA510 2.5 1000GB"
export FSB_EXPECTED_SIZE_BYTES="1000204886016"
export FSB_EXPECTED_CONTENT="FDisk_partition_scheme"
export FSB_EXPECTED_REMOVABLE_MEDIA="false"
export FSB_BOOT_PARTITION_INDEX="1"
export FSB_ROOT_PARTITION_INDEX="2"
export FSB_HEADROOM_PERCENT="25"
export FSB_SCRIPT_NAME="backup-raspberrypi-sd-card.sh"

exec "${0:A:h}/backup-linux-filesystems-from-disk.sh" "$@"
