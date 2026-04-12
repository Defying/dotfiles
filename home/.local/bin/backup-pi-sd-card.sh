#!/bin/zsh
set -euo pipefail

export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin

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

fingerprint_configured() {
  [[ -n "$EXPECTED_MEDIA_NAME" && -n "$EXPECTED_SIZE_BYTES" && -n "$EXPECTED_CONTENT" ]]
}

usage() {
  cat <<EOF
Usage:
  $SCRIPT_NAME [diskN]
  $SCRIPT_NAME --list

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
  else
    cat <<'EOF'

No stored fingerprint is configured for this target yet.
If you do not pass a disk, the script will list external physical disks and ask.
EOF
  fi

  cat <<'EOF'

Examples:
  script --list
  script disk8
EOF
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

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--list" ]]; then
  list_candidates
  exit 0
fi

if [[ ! -d /Volumes/Carve || ! -w /Volumes/Carve ]]; then
  echo "[error] /Volumes/Carve is not mounted or writable"
  exit 1
fi

mkdir -p "$BACKUP_ROOT"

typeset disk="${1:-}"
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

cat <<EOF
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
EOF

echo "[info] starting image backup, press x at any time to stop"
sudo -v
rm -f "$tmpfile" "$outfile" "$outfile.sha256"

/usr/sbin/diskutil unmountDisk "$dev" >/dev/null
trap 'cleanup_partial_image; /usr/sbin/diskutil mountDisk "$dev" >/dev/null 2>&1 || true' EXIT INT TERM

if ! sudo /usr/bin/env python3 "$SCRIPT_DIR/compress-disk-image.py" \
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
