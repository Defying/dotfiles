#!/bin/zsh
set -euo pipefail

export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin

TARGET_LABEL="${SD_TARGET_LABEL:-Pi}"
TARGET_SLUG="${SD_TARGET_SLUG:-pi}"
TARGET_HOST="${SD_TARGET_HOST:-}"
BACKUP_ROOT="${SD_BACKUP_ROOT:-/Volumes/Carve/Backups/${TARGET_SLUG}/images}"
IMAGE_PREFIX="${SD_IMAGE_PREFIX:-${TARGET_SLUG}-sd}"
STAMP="$(date +%Y%m%d-%H%M%S)"
EXPECTED_MEDIA_NAME="${SD_EXPECTED_MEDIA_NAME:-}"
EXPECTED_SIZE_BYTES="${SD_EXPECTED_SIZE_BYTES:-}"
EXPECTED_CONTENT="${SD_EXPECTED_CONTENT:-}"

fingerprint_configured() {
  [[ -n "$EXPECTED_MEDIA_NAME" && -n "$EXPECTED_SIZE_BYTES" && -n "$EXPECTED_CONTENT" ]]
}

usage() {
  cat <<EOF
Usage:
  ${0:t} [diskN]
  ${0:t} --list

Target: $TARGET_LABEL${TARGET_HOST:+ ($TARGET_HOST)}
Output root: $BACKUP_ROOT
EOF

  if fingerprint_configured; then
    cat <<EOF

If no disk is given, the script auto-detects the expected card by fingerprint:
  media name: $EXPECTED_MEDIA_NAME
  exact size: $EXPECTED_SIZE_BYTES bytes
  partition map: $EXPECTED_CONTENT
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

    if [[ "$whole" == "true" && "$internal" == "false" && "$removable" == "true" && "$name" == "$EXPECTED_MEDIA_NAME" && "$size" == "$EXPECTED_SIZE_BYTES" && "$content" == "$EXPECTED_CONTENT" ]]; then
      print -r -- "$disk"
      return 0
    fi
  done < <(/usr/sbin/diskutil list external physical | awk '/^\/dev\/disk[0-9]+/ {gsub(":","",$1); sub("/dev/","",$1); print $1}')

  return 1
}

start_cancel_watcher() {
  [[ ! -r /dev/tty ]] && return 0

  {
    typeset key
    while true; do
      if ! read -r -k 1 key < /dev/tty; then
        exit 0
      fi

      [[ "$key" == $'\n' || "$key" == $'\r' ]] && continue

      if [[ "$key" == [xX] ]]; then
        print -u2 -- "\n[info] stop requested, cancelling image..."
        : > "$CANCEL_FLAG"
        [[ -n "${PIPELINE_PID:-}" ]] && {
          pkill -TERM -P "$PIPELINE_PID" 2>/dev/null || true
          kill -TERM "$PIPELINE_PID" 2>/dev/null || true
        }
        exit 0
      fi
    done
  } &
  WATCHER_PID=$!
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
      echo "[error] could not auto-detect the expected $TARGET_LABEL microSD card"
      echo
      echo "External physical disks:"
      list_candidates
      exit 1
    fi
    echo "[ok] auto-detected $TARGET_LABEL microSD as /dev/$disk"
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
    echo "[error] $dev does not match the expected $TARGET_LABEL microSD fingerprint"
    echo "        expected: media=$EXPECTED_MEDIA_NAME size=$EXPECTED_SIZE_BYTES content=$EXPECTED_CONTENT"
    echo "        got:      media=${name:-unknown} size=${size_bytes:-unknown} content=${content:-unknown}"
    exit 1
  fi
fi

outfile="$BACKUP_ROOT/$IMAGE_PREFIX-$STAMP-$disk.img.gz"
IMAGE_COMPLETED=0

cleanup_partial_image() {
  if [[ "${IMAGE_COMPLETED:-0}" != "1" ]]; then
    rm -f "$outfile" "$outfile.sha256"
  fi
}

cat <<EOF
About to create a compressed $TARGET_LABEL microSD image backup.

  target:    $TARGET_LABEL${TARGET_HOST:+ ($TARGET_HOST)}
  disk:      $dev
  name:      ${name:-unknown}
  size:      ${size:-unknown}
  protocol:  ${protocol:-unknown}
  removable: ${removable:-unknown}
  content:   ${content:-unknown}
  output:    $outfile

The card will be unmounted first. This reads the card and writes a gzip-compressed image.
EOF
printf 'Continue? [y/N] '
read -r confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  echo "aborted"
  exit 1
fi

echo "[info] press x at any time to stop this image backup"
sudo -v

CANCEL_FLAG="${TMPDIR:-/tmp}/${IMAGE_PREFIX}-${STAMP}.cancelled"
WATCHER_PID=""
PIPELINE_PID=""
rm -f "$CANCEL_FLAG"

/usr/sbin/diskutil unmountDisk "$dev" >/dev/null
trap 'cleanup_partial_image; /usr/sbin/diskutil mountDisk "$dev" >/dev/null 2>&1 || true; [[ -n "${WATCHER_PID:-}" ]] && kill "$WATCHER_PID" 2>/dev/null || true; rm -f "$CANCEL_FLAG"' EXIT INT TERM

start_cancel_watcher

(
  set -o pipefail
  sudo dd if="$rdev" bs=4m 2>/dev/null | \
    python3 -c 'import sys, time

total = int(sys.argv[1]) if len(sys.argv) > 1 else 0
processed = 0
start = time.time()
last = 0.0
width = 28

while True:
    chunk = sys.stdin.buffer.read(1024 * 1024)
    if not chunk:
        break
    sys.stdout.buffer.write(chunk)
    processed += len(chunk)
    now = time.time()
    if now - last >= 0.2:
        frac = (processed / total) if total else 0.0
        filled = int(width * frac) if total else 0
        bar = "#" * filled + "-" * (width - filled)
        rate = processed / max(now - start, 0.001)
        eta = int((total - processed) / rate) if total and rate > 0 else 0
        sys.stderr.write(f"\r[{bar}] {frac * 100:5.1f}%  {processed / (1024**3):5.1f}/{total / (1024**3):5.1f} GiB  {rate / (1024**2):6.1f} MiB/s  ETA {eta:5d}s")
        sys.stderr.flush()
        last = now

sys.stdout.buffer.flush()
if total:
    rate = processed / max(time.time() - start, 0.001)
    bar = "#" * width
    sys.stderr.write(f"\r[{bar}] 100.0%  {processed / (1024**3):5.1f}/{total / (1024**3):5.1f} GiB  {rate / (1024**2):6.1f} MiB/s  ETA     0s\n")
    sys.stderr.flush()
' "$size_bytes" | \
    gzip -1 > "$outfile"
) &
PIPELINE_PID=$!

if ! wait "$PIPELINE_PID"; then
  [[ -n "$WATCHER_PID" ]] && kill "$WATCHER_PID" 2>/dev/null || true
  if [[ -f "$CANCEL_FLAG" ]]; then
    rm -f "$outfile" "$outfile.sha256"
    echo "[info] image backup cancelled"
    exit 1
  fi
  rm -f "$outfile" "$outfile.sha256"
  echo "[error] image backup failed"
  exit 1
fi

[[ -n "$WATCHER_PID" ]] && kill "$WATCHER_PID" 2>/dev/null || true
/usr/bin/shasum -a 256 "$outfile" > "$outfile.sha256"
IMAGE_COMPLETED=1
/usr/sbin/diskutil mountDisk "$dev" >/dev/null || true
trap - EXIT INT TERM
rm -f "$CANCEL_FLAG"

ls -lh "$outfile" "$outfile.sha256"
echo "[ok] compressed image written to $outfile"
