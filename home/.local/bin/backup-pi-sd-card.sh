#!/bin/zsh
set -euo pipefail

export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/opt/homebrew/sbin:/opt/homebrew/opt/e2fsprogs/bin:/opt/homebrew/opt/e2fsprogs/sbin

TARGET_LABEL="${SD_TARGET_LABEL:-Pi}"
TARGET_SLUG="${SD_TARGET_SLUG:-pi}"
TARGET_HOST="${SD_TARGET_HOST:-}"
TARGET_MEDIA_KIND="${SD_TARGET_MEDIA_KIND:-disk}"
BACKUP_ROOT="${SD_BACKUP_ROOT:-/Volumes/Carve/Backups/${TARGET_SLUG}/images}"
IMAGE_PREFIX="${SD_IMAGE_PREFIX:-${TARGET_SLUG}-sd}"
SCRIPT_NAME="$(basename "$0")"
STAMP="$(date +%Y%m%d-%H%M%S)"
EXPECTED_MEDIA_NAME="${SD_EXPECTED_MEDIA_NAME:-}"
EXPECTED_SIZE_BYTES="${SD_EXPECTED_SIZE_BYTES:-}"
EXPECTED_CONTENT="${SD_EXPECTED_CONTENT:-}"
EXPECTED_REMOVABLE_MEDIA="${SD_EXPECTED_REMOVABLE_MEDIA:-}"
ESTIMATE_HEADROOM_PERCENT="${SD_ESTIMATE_HEADROOM_PERCENT:-25}"
SKIP_SPACE_CHECK="${SD_SKIP_SPACE_CHECK:-0}"

fingerprint_configured() {
  [[ -n "$EXPECTED_MEDIA_NAME" && -n "$EXPECTED_SIZE_BYTES" && -n "$EXPECTED_CONTENT" ]]
}

usage() {
  cat <<EOF_USAGE
Usage:
  $SCRIPT_NAME [diskN]
  $SCRIPT_NAME --list
  $SCRIPT_NAME --check-space [diskN]

Target: $TARGET_LABEL${TARGET_HOST:+ ($TARGET_HOST)}
Output root: $BACKUP_ROOT
EOF_USAGE

  if fingerprint_configured; then
    cat <<EOF_USAGE

If no disk is given, the script auto-detects the expected $TARGET_MEDIA_KIND by fingerprint:
  media name: $EXPECTED_MEDIA_NAME
  exact size: $EXPECTED_SIZE_BYTES bytes
  partition map: $EXPECTED_CONTENT${EXPECTED_REMOVABLE_MEDIA:+
  removable: $EXPECTED_REMOVABLE_MEDIA}
EOF_USAGE
  else
    cat <<'EOF_USAGE'

No stored fingerprint is configured for this target yet.
If you do not pass a disk, the script will list external physical disks and ask.
EOF_USAGE
  fi

  cat <<'EOF_USAGE'

Examples:
  script --list
  script --check-space
  script disk8
EOF_USAGE
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

info_field() {
  typeset disk="$1"
  typeset key="$2"
  /usr/sbin/diskutil info "$disk" | awk -F: -v key="$key" '$1 ~ key {sub(/^[[:space:]]+/, "", $2); print $2; exit}'
}

plist_field() {
  typeset disk="$1"
  typeset key="$2"
  /usr/sbin/diskutil info -plist "$disk" | plutil -extract "$key" raw -o - - 2>/dev/null || true
}

list_candidates() {
  /usr/sbin/diskutil list external physical
}

resolve_tune2fs() {
  typeset candidate
  for candidate in \
    /opt/homebrew/opt/e2fsprogs/sbin/tune2fs \
    /opt/homebrew/sbin/tune2fs \
    "$(command -v tune2fs 2>/dev/null || true)"
  do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      print -r -- "$candidate"
      return 0
    fi
  done
  return 1
}

E2FS_TUNE2FS="${SD_E2FS_TUNE2FS_BIN:-$(resolve_tune2fs || true)}"

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

child_partitions() {
  typeset parent="$1"
  parent="${parent#/dev/}"
  /usr/sbin/diskutil list "/dev/$parent" | awk -v parent="$parent" '/^[[:space:]]*[0-9]+:/ { id=$NF; if (id ~ ("^" parent "s[0-9]+$")) print "/dev/" id }'
}

estimate_partition_live_bytes() {
  typeset part="$1"
  typeset total_bytes content stats block_count free_blocks block_size used_bytes

  total_bytes="$(plist_field "$part" TotalSize)"
  content="$(plist_field "$part" Content)"

  if [[ -n "$E2FS_TUNE2FS" ]]; then
    if stats="$(run_privileged "$E2FS_TUNE2FS" -l "$part" 2>/dev/null)"; then
      block_count="$(print -r -- "$stats" | awk -F: '/^Block count:/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
      free_blocks="$(print -r -- "$stats" | awk -F: '/^Free blocks:/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
      block_size="$(print -r -- "$stats" | awk -F: '/^Block size:/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"

      if [[ -n "$block_count" && -n "$free_blocks" && -n "$block_size" ]]; then
        used_bytes=$(( (block_count - free_blocks) * block_size ))
        print -r -- "${used_bytes}|ext-allocated|${content:-unknown}|${total_bytes:-0}"
        return 0
      fi
    fi
  fi

  if [[ -n "$total_bytes" ]]; then
    print -r -- "${total_bytes}|full-partition|${content:-unknown}|${total_bytes}"
    return 0
  fi

  return 1
}

destination_free_bytes() {
  /bin/df -kP "$BACKUP_ROOT" | awk 'NR==2 {printf "%.0f", $4 * 1024}'
}

run_space_preflight() {
  typeset parent="$1"
  typeset part entry part_est method content total_bytes
  typeset -i estimate_bytes=0 estimated_parts=0 dest_free required_bytes

  dest_free="$(destination_free_bytes)"
  echo "[preflight] destination free: $(human_bytes "$dest_free")"

  while read -r part; do
    [[ -z "$part" ]] && continue
    entry="$(estimate_partition_live_bytes "$part")" || continue
    IFS='|' read -r part_est method content total_bytes <<< "$entry"
    estimate_bytes=$(( estimate_bytes + part_est ))
    estimated_parts=$(( estimated_parts + 1 ))
    echo "[preflight] $part ${content:-unknown}: $(human_bytes "$part_est") estimated via $method (partition $(human_bytes "${total_bytes:-0}"))"
  done < <(child_partitions "$parent")

  if (( estimated_parts == 0 )); then
    echo "[warn] could not estimate filesystem usage on $parent, skipping free-space preflight"
    return 0
  fi

  required_bytes=$(( estimate_bytes + (estimate_bytes * ESTIMATE_HEADROOM_PERCENT / 100) ))
  echo "[preflight] estimated live data total: $(human_bytes "$estimate_bytes")"
  echo "[preflight] required with ${ESTIMATE_HEADROOM_PERCENT}% cushion: $(human_bytes "$required_bytes")"
  echo "[preflight] note: passing this check does not guarantee success, because raw imaging still reads unallocated blocks too"

  if [[ "$SKIP_SPACE_CHECK" == "1" ]]; then
    echo "[warn] skipping free-space enforcement because SD_SKIP_SPACE_CHECK=1"
    return 0
  fi

  if (( dest_free < required_bytes )); then
    echo "[error] destination free space is below the estimated minimum for this backup"
    echo "        free:     $(human_bytes "$dest_free")"
    echo "        estimate: $(human_bytes "$estimate_bytes")"
    echo "        required: $(human_bytes "$required_bytes") (estimate + ${ESTIMATE_HEADROOM_PERCENT}% cushion)"
    echo "        override with SD_SKIP_SPACE_CHECK=1 if you really want to force a raw image anyway"
    return 1
  fi

  echo "[ok] destination free space clears the estimated live-data footprint (+${ESTIMATE_HEADROOM_PERCENT}% cushion)"
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
    printf 'Disk to image for %s (for example disk8): ' "$TARGET_LABEL"
    read -r disk
  fi
fi

disk="$(normalize_disk "$disk")"
dev="/dev/$disk"
rdev="/dev/r$disk"

if [[ ! -e "$dev" ]]; then
  echo "[error] $dev does not exist"
  exit 1
fi

whole="$(plist_field "$dev" WholeDisk)"
internal="$(plist_field "$dev" Internal)"
name="$(plist_field "$dev" MediaName)"
size="$(info_field "$dev" '^[[:space:]]*Disk Size')"
protocol="$(plist_field "$dev" BusProtocol)"
removable="$(plist_field "$dev" RemovableMedia)"
size_bytes="$(plist_field "$dev" TotalSize)"
content="$(plist_field "$dev" Content)"

if [[ "$whole" != "true" ]]; then
  echo "[error] $dev is not a whole disk device"
  exit 1
fi

if [[ "$internal" == "true" ]]; then
  echo "[error] refusing to image an internal disk ($dev)"
  exit 1
fi

if fingerprint_configured; then
  if [[ "$name" != "$EXPECTED_MEDIA_NAME" || "$size_bytes" != "$EXPECTED_SIZE_BYTES" || "$content" != "$EXPECTED_CONTENT" ]]; then
    echo "[error] $dev does not match the expected $TARGET_LABEL $TARGET_MEDIA_KIND fingerprint"
    echo "        expected: media=$EXPECTED_MEDIA_NAME size=$EXPECTED_SIZE_BYTES content=$EXPECTED_CONTENT${EXPECTED_REMOVABLE_MEDIA:+ removable=$EXPECTED_REMOVABLE_MEDIA}"
    echo "        got:      media=${name:-unknown} size=${size_bytes:-unknown} content=${content:-unknown}${removable:+ removable=$removable}"
    exit 1
  fi
  if [[ -n "$EXPECTED_REMOVABLE_MEDIA" && "$removable" != "$EXPECTED_REMOVABLE_MEDIA" ]]; then
    echo "[error] $dev does not match the expected $TARGET_LABEL $TARGET_MEDIA_KIND fingerprint"
    echo "        expected removable=$EXPECTED_REMOVABLE_MEDIA"
    echo "        got      removable=${removable:-unknown}"
    exit 1
  fi
fi

SCRIPT_DIR="${0:A:h}"
outfile="$BACKUP_ROOT/$IMAGE_PREFIX-$STAMP-$disk.img.gz"
tmpfile="$outfile.part"
IMAGE_COMPLETED=0

cleanup_partial_image() {
  if [[ "${IMAGE_COMPLETED:-0}" != "1" ]]; then
    rm -f "$tmpfile" "$outfile" "$outfile.sha256"
  fi
}

refresh_privileges

if ! run_space_preflight "$dev"; then
  exit 1
fi

if (( CHECK_SPACE_ONLY )); then
  echo "[ok] preflight passed, no image created"
  exit 0
fi

cat <<EOF_INFO
About to create a compressed $TARGET_LABEL $TARGET_MEDIA_KIND image backup.

  target:    $TARGET_LABEL${TARGET_HOST:+ ($TARGET_HOST)}
  disk:      $dev
  name:      ${name:-unknown}
  size:      ${size:-unknown}
  protocol:  ${protocol:-unknown}
  removable: ${removable:-unknown}
  content:   ${content:-unknown}
  output:    $outfile

The device will be unmounted first. This reads the device and writes a gzip-compressed image.
EOF_INFO

echo "[info] starting image backup, press x at any time to stop"
rm -f "$tmpfile" "$outfile" "$outfile.sha256"

/usr/sbin/diskutil unmountDisk "$dev" >/dev/null
trap 'cleanup_partial_image; /usr/sbin/diskutil mountDisk "$dev" >/dev/null 2>&1 || true' EXIT INT TERM

if ! run_privileged /usr/bin/env python3 "$SCRIPT_DIR/compress-disk-image.py" \
  --input "$rdev" \
  --output "$tmpfile" \
  --total-bytes "$size_bytes" \
  --label "$TARGET_LABEL" \
  --compress-level 1; then
  exit_code=$?
  cleanup_partial_image
  if [[ $exit_code -eq 130 ]]; then
    echo "[info] image backup cancelled"
  else
    echo "[error] image backup failed"
  fi
  exit 1
fi

mv "$tmpfile" "$outfile"
/usr/bin/shasum -a 256 "$outfile" > "$outfile.sha256"
IMAGE_COMPLETED=1
/usr/sbin/diskutil mountDisk "$dev" >/dev/null || true
trap - EXIT INT TERM

ls -lh "$outfile" "$outfile.sha256"
echo "[ok] compressed image written to $outfile"
