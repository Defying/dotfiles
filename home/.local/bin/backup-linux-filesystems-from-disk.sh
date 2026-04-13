#!/bin/zsh
set -euo pipefail

export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/opt/homebrew/sbin:/opt/homebrew/opt/e2fsprogs/bin:/opt/homebrew/opt/e2fsprogs/sbin
export COPYFILE_DISABLE=1

TARGET_LABEL="${FSB_TARGET_LABEL:-Linux disk}"
TARGET_SLUG="${FSB_TARGET_SLUG:-linux-disk}"
TARGET_HOST="${FSB_TARGET_HOST:-}"
TARGET_MEDIA_KIND="${FSB_TARGET_MEDIA_KIND:-disk}"
BACKUP_ROOT="${FSB_BACKUP_ROOT:-/Volumes/Carve/Backups/${TARGET_SLUG}/filesystems}"
ARCHIVE_PREFIX="${FSB_ARCHIVE_PREFIX:-${TARGET_SLUG}-filesystems}"
EXPECTED_MEDIA_NAME="${FSB_EXPECTED_MEDIA_NAME:-}"
EXPECTED_SIZE_BYTES="${FSB_EXPECTED_SIZE_BYTES:-}"
EXPECTED_CONTENT="${FSB_EXPECTED_CONTENT:-}"
EXPECTED_REMOVABLE_MEDIA="${FSB_EXPECTED_REMOVABLE_MEDIA:-}"
BOOT_PARTITION_INDEX="${FSB_BOOT_PARTITION_INDEX:-1}"
ROOT_PARTITION_INDEX="${FSB_ROOT_PARTITION_INDEX:-2}"
HEADROOM_PERCENT="${FSB_HEADROOM_PERCENT:-25}"
SCRIPT_NAME="${FSB_SCRIPT_NAME:-$(basename "$0")}"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOCK_DIR="/Users/ben/Library/Caches/com.carve.${TARGET_SLUG}.filesystem-backup.lock"

fingerprint_configured() {
  [[ -n "$EXPECTED_MEDIA_NAME" && -n "$EXPECTED_SIZE_BYTES" && -n "$EXPECTED_CONTENT" ]]
}

usage() {
  cat <<EOF
Usage:
  $SCRIPT_NAME [diskN]
  $SCRIPT_NAME --list
  $SCRIPT_NAME --check-space [diskN]

Target: $TARGET_LABEL${TARGET_HOST:+ ($TARGET_HOST)}
Output root: $BACKUP_ROOT
EOF

  if fingerprint_configured; then
    cat <<EOF

If no disk is given, the script auto-detects the expected $TARGET_MEDIA_KIND by fingerprint:
  media name: $EXPECTED_MEDIA_NAME
  exact size: $EXPECTED_SIZE_BYTES bytes
  partition map: $EXPECTED_CONTENT${EXPECTED_REMOVABLE_MEDIA:+
  removable: $EXPECTED_REMOVABLE_MEDIA}
EOF
  fi

  cat <<'EOF'

Examples:
  script --check-space
  script disk11
EOF
}

human_bytes() {
  /usr/bin/env python3 - "$1" <<'PY'
import sys
value = int(sys.argv[1])
units = ["B", "KB", "MB", "GB", "TB", "PB"]
size = float(value)
for unit in units:
    if size < 1024 or unit == units[-1]:
        if unit == "B":
            print(f"{int(size)}{unit}")
        else:
            print(f"{size:.1f}{unit}")
        break
    size /= 1024
PY
}

normalize_disk() {
  typeset disk="$1"
  disk="${disk#/dev/r}"
  disk="${disk#/dev/}"
  print -r -- "$disk"
}

plist_field() {
  typeset disk="$1"
  typeset key="$2"
  /usr/sbin/diskutil info -plist "$disk" | plutil -extract "$key" raw -o - - 2>/dev/null || true
}

list_candidates() {
  /usr/sbin/diskutil list external physical
}

resolve_tool() {
  typeset name="$1"
  shift
  typeset candidate
  for candidate in "$@"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      print -r -- "$candidate"
      return 0
    fi
  done
  candidate="$(command -v "$name" 2>/dev/null || true)"
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    print -r -- "$candidate"
    return 0
  fi
  return 1
}

DEBUGFS_BIN="$(resolve_tool debugfs /opt/homebrew/opt/e2fsprogs/sbin/debugfs /opt/homebrew/sbin/debugfs || true)"
TUNE2FS_BIN="$(resolve_tool tune2fs /opt/homebrew/opt/e2fsprogs/sbin/tune2fs /opt/homebrew/sbin/tune2fs || true)"
ZSTD_BIN="$(resolve_tool zstd /opt/homebrew/bin/zstd /opt/homebrew/sbin/zstd || true)"

SUDO_PREFIX=()
SUDO_CAN_REFRESH=0
if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  SUDO_PREFIX=()
elif /usr/bin/sudo -n true 2>/dev/null; then
  SUDO_PREFIX=(/usr/bin/sudo -n)
  SUDO_CAN_REFRESH=0
else
  SUDO_PREFIX=(/usr/bin/sudo)
  SUDO_CAN_REFRESH=1
fi

run_privileged() {
  if (( ${#SUDO_PREFIX[@]} )); then
    "${SUDO_PREFIX[@]}" "$@"
  else
    "$@"
  fi
}

refresh_privileges() {
  if (( SUDO_CAN_REFRESH )); then
    "${SUDO_PREFIX[@]}" -v
  fi
}

find_expected_disk() {
  typeset disk name size whole internal content removable

  while read -r disk; do
    [[ -z "$disk" ]] && continue

    name="$(plist_field "/dev/$disk" MediaName)"
    size="$(plist_field "/dev/$disk" TotalSize)"
    whole="$(plist_field "/dev/$disk" WholeDisk)"
    internal="$(plist_field "/dev/$disk" Internal)"
    content="$(plist_field "/dev/$disk" Content)"
    removable="$(plist_field "/dev/$disk" RemovableMedia)"

    if [[ "$whole" == "true" && "$internal" == "false" && "$name" == "$EXPECTED_MEDIA_NAME" && "$size" == "$EXPECTED_SIZE_BYTES" && "$content" == "$EXPECTED_CONTENT" ]]; then
      if [[ -n "$EXPECTED_REMOVABLE_MEDIA" && "$removable" != "$EXPECTED_REMOVABLE_MEDIA" ]]; then
        continue
      fi
      print -r -- "$disk"
      return 0
    fi
  done < <(/usr/sbin/diskutil list external physical | awk '/^\/dev\/disk[0-9]+/ {gsub(":","",$1); sub("/dev/","",$1); print $1}')

  return 1
}

estimate_rootfs_bytes() {
  typeset part="$1"
  typeset stats block_count free_blocks block_size

  if [[ -z "$TUNE2FS_BIN" ]]; then
    return 1
  fi

  stats="$(run_privileged "$TUNE2FS_BIN" -l "$part" 2>/dev/null)" || return 1
  block_count="$(print -r -- "$stats" | awk -F: '/^Block count:/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
  free_blocks="$(print -r -- "$stats" | awk -F: '/^Free blocks:/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
  block_size="$(print -r -- "$stats" | awk -F: '/^Block size:/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"

  [[ -n "$block_count" && -n "$free_blocks" && -n "$block_size" ]] || return 1
  print -r -- $(( (block_count - free_blocks) * block_size ))
}

destination_free_bytes() {
  /bin/df -kP "$BACKUP_ROOT" | awk 'NR==2 {printf "%.0f", $4 * 1024}'
}

preflight_space() {
  typeset boot_part="$1"
  typeset root_part="$2"
  typeset boot_bytes root_bytes total_bytes required_bytes free_bytes

  boot_bytes="$(plist_field "$boot_part" TotalSize)"
  root_bytes="$(estimate_rootfs_bytes "$root_part" || true)"
  if [[ -z "$root_bytes" ]]; then
    root_bytes="$(plist_field "$root_part" TotalSize)"
    echo "[warn] tune2fs estimate unavailable, falling back to full root partition size"
  fi

  total_bytes=$(( boot_bytes + root_bytes ))
  required_bytes=$(( (total_bytes * (200 + HEADROOM_PERCENT)) / 100 ))
  free_bytes="$(destination_free_bytes)"

  echo "[preflight] destination free: $(human_bytes "$free_bytes")"
  echo "[preflight] $boot_part bootfs estimate: $(human_bytes "$boot_bytes")"
  echo "[preflight] $root_part rootfs estimate: $(human_bytes "$root_bytes")"
  echo "[preflight] estimated filesystem data total: $(human_bytes "$total_bytes")"
  echo "[preflight] estimated working space needed: $(human_bytes "$required_bytes") (staging + archive + ${HEADROOM_PERCENT}% cushion)"
  echo "[preflight] note: this is for direct filesystem backup, not a raw disk image"

  if (( free_bytes < required_bytes )); then
    echo "[error] not enough free space for a filesystem-level backup run"
    echo "        free:     $(human_bytes "$free_bytes")"
    echo "        required: $(human_bytes "$required_bytes")"
    return 1
  fi

  echo "[ok] destination free space clears the estimated working footprint"
}

BOOT_WAS_MOUNTED=0
BOOT_MOUNT_POINT=""

ensure_boot_mount() {
  typeset part="$1"
  local mounted
  mounted="$(plist_field "$part" Mounted)"
  if [[ "$mounted" == "true" ]]; then
    BOOT_WAS_MOUNTED=1
    BOOT_MOUNT_POINT="$(plist_field "$part" MountPoint)"
  else
    /usr/sbin/diskutil mount "$part" >/dev/null
    BOOT_WAS_MOUNTED=0
    BOOT_MOUNT_POINT="$(plist_field "$part" MountPoint)"
  fi

  [[ -n "$BOOT_MOUNT_POINT" && -d "$BOOT_MOUNT_POINT" ]] || {
    echo "[error] could not access boot partition mount point"
    exit 1
  }
}

restore_boot_mount_state() {
  typeset part="$1"
  if [[ "$BOOT_WAS_MOUNTED" == "0" ]]; then
    /usr/sbin/diskutil unmount "$part" >/dev/null 2>&1 || true
  fi
}

CHECK_SPACE_ONLY=0
typeset disk=""
while (( $# > 0 )); do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --list)
      list_candidates
      exit 0
      ;;
    --check-space)
      CHECK_SPACE_ONLY=1
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "[error] unknown option: $1"
      usage
      exit 1
      ;;
    *)
      if [[ -n "$disk" ]]; then
        echo "[error] unexpected extra argument: $1"
        usage
        exit 1
      fi
      disk="$1"
      ;;
  esac
  shift
done

if [[ -z "$DEBUGFS_BIN" || -z "$TUNE2FS_BIN" || -z "$ZSTD_BIN" ]]; then
  echo "[error] required tools are missing. Need debugfs, tune2fs, and zstd available locally."
  exit 1
fi

if [[ ! -d /Volumes/Carve || ! -w /Volumes/Carve ]]; then
  echo "[error] /Volumes/Carve is not mounted or writable"
  exit 1
fi

mkdir -p "$BACKUP_ROOT"

if [[ -z "$disk" ]]; then
  if fingerprint_configured; then
    if ! disk="$(find_expected_disk)"; then
      echo "[error] could not auto-detect the expected $TARGET_LABEL $TARGET_MEDIA_KIND"
      echo
      echo "External physical disks:"
      list_candidates
      exit 1
    fi
    echo "[ok] auto-detected $TARGET_LABEL $TARGET_MEDIA_KIND as /dev/$disk"
  else
    echo "External physical disks:"
    list_candidates
    echo
    printf 'Disk to back up for %s (for example disk11): ' "$TARGET_LABEL"
    read -r disk
  fi
fi

disk="$(normalize_disk "$disk")"
DEV="/dev/$disk"
BOOT_PART="${DEV}s${BOOT_PARTITION_INDEX}"
ROOT_PART="${DEV}s${ROOT_PARTITION_INDEX}"
WHOLE="$(plist_field "$DEV" WholeDisk)"
INTERNAL="$(plist_field "$DEV" Internal)"
NAME="$(plist_field "$DEV" MediaName)"
SIZE_BYTES="$(plist_field "$DEV" TotalSize)"
CONTENT="$(plist_field "$DEV" Content)"
REMOVABLE="$(plist_field "$DEV" RemovableMedia)"

if [[ ! -e "$DEV" ]]; then
  echo "[error] $DEV does not exist"
  exit 1
fi
if [[ "$WHOLE" != "true" ]]; then
  echo "[error] $DEV is not a whole disk device"
  exit 1
fi
if [[ "$INTERNAL" == "true" ]]; then
  echo "[error] refusing to back up an internal disk ($DEV)"
  exit 1
fi
if fingerprint_configured; then
  if [[ "$NAME" != "$EXPECTED_MEDIA_NAME" || "$SIZE_BYTES" != "$EXPECTED_SIZE_BYTES" || "$CONTENT" != "$EXPECTED_CONTENT" ]]; then
    echo "[error] $DEV does not match the expected $TARGET_LABEL $TARGET_MEDIA_KIND fingerprint"
    exit 1
  fi
  if [[ -n "$EXPECTED_REMOVABLE_MEDIA" && "$REMOVABLE" != "$EXPECTED_REMOVABLE_MEDIA" ]]; then
    echo "[error] $DEV removable flag does not match the expected fingerprint"
    exit 1
  fi
fi
if [[ ! -e "$BOOT_PART" || ! -e "$ROOT_PART" ]]; then
  echo "[error] expected partitions $BOOT_PART and $ROOT_PART were not found"
  exit 1
fi

refresh_privileges
preflight_space "$BOOT_PART" "$ROOT_PART"
if (( CHECK_SPACE_ONLY )); then
  echo "[ok] preflight passed, no backup created"
  exit 0
fi

ARCHIVE_ROOT="$BACKUP_ROOT/archives"
STAGING_ROOT="$BACKUP_ROOT/.staging"
LATEST_LINK="$BACKUP_ROOT/latest"
ARCHIVE_BASENAME="$ARCHIVE_PREFIX-$STAMP"
STAGING_DIR="$STAGING_ROOT/$ARCHIVE_BASENAME"
ARCHIVE_PATH="$ARCHIVE_ROOT/$ARCHIVE_BASENAME.tar.zst"

mkdir -p "$ARCHIVE_ROOT" "$STAGING_ROOT" "/Users/ben/Library/Caches"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[error] could not acquire backup lock: $LOCK_DIR"
  exit 1
fi

cleanup() {
  restore_boot_mount_state "$BOOT_PART"
  if [[ -d "$STAGING_DIR" ]]; then
    run_privileged rm -rf "$STAGING_DIR" || true
  fi
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

run_privileged mkdir -p "$STAGING_DIR/bootfs" "$STAGING_DIR/rootfs" "$STAGING_DIR/meta"

ensure_boot_mount "$BOOT_PART"
echo "[info] copying boot partition from $BOOT_MOUNT_POINT"
run_privileged /usr/bin/rsync -aH --delete --exclude '.DS_Store' "$BOOT_MOUNT_POINT/" "$STAGING_DIR/bootfs/"

echo "[info] dumping ext4 root filesystem from $ROOT_PART"
run_privileged "$DEBUGFS_BIN" -R "rdump / $STAGING_DIR/rootfs" "$ROOT_PART" >/dev/null

{
  echo "timestamp=$STAMP"
  echo "target_label=$TARGET_LABEL"
  echo "target_host=$TARGET_HOST"
  echo "disk=$DEV"
  echo "boot_partition=$BOOT_PART"
  echo "root_partition=$ROOT_PART"
  echo "archive_path=$ARCHIVE_PATH"
} > "$STAGING_DIR/meta/backup-info.txt"

/usr/sbin/diskutil list "$DEV" > "$STAGING_DIR/meta/diskutil-list.txt"
/usr/sbin/diskutil info "$DEV" > "$STAGING_DIR/meta/disk-info.txt"
/usr/sbin/diskutil info "$BOOT_PART" > "$STAGING_DIR/meta/bootfs-info.txt"
/usr/sbin/diskutil info "$ROOT_PART" > "$STAGING_DIR/meta/rootfs-info.txt"
run_privileged "$TUNE2FS_BIN" -l "$ROOT_PART" > "$STAGING_DIR/meta/rootfs-tune2fs.txt"

run_privileged /usr/bin/tar -C "$STAGING_ROOT" -cf - "$ARCHIVE_BASENAME" | "$ZSTD_BIN" -T0 -6 -q -o "$ARCHIVE_PATH"
/usr/bin/shasum -a 256 "$ARCHIVE_PATH" > "$ARCHIVE_PATH.sha256"
/bin/ln -sfn "$ARCHIVE_PATH" "$LATEST_LINK"

run_privileged du -sh "$STAGING_DIR/bootfs" "$STAGING_DIR/rootfs" > "$STAGING_DIR/meta/staging-sizes.txt" || true
run_privileged rm -rf "$STAGING_DIR"

echo "[ok] filesystem archive written to $ARCHIVE_PATH"
ls -lh "$ARCHIVE_PATH" "$ARCHIVE_PATH.sha256"
