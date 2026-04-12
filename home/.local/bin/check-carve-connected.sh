#!/bin/zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

MOUNT_POINT="/Volumes/Carve"
EXPECTED_MEDIA_NAME="Extreme 55AE"

trim() {
  sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

if [[ ! -d "$MOUNT_POINT" ]]; then
  echo "[issue] Carve is not mounted at $MOUNT_POINT"
  exit 2
fi

if ! mount | grep -Fq " on $MOUNT_POINT "; then
  echo "[issue] $MOUNT_POINT exists but is not currently mounted"
  exit 2
fi

if ! vol_info="$(diskutil info "$MOUNT_POINT" 2>&1)"; then
  echo "[issue] diskutil could not read $MOUNT_POINT"
  echo "$vol_info"
  exit 2
fi

mounted="$(awk -F: '/Mounted:/ {print $2; exit}' <<<"$vol_info" | trim)"
volume_dev="$(awk -F: '/Device Identifier:/ {print $2; exit}' <<<"$vol_info" | trim)"
physical_store="$(awk -F: '/APFS Physical Store:/ {print $2; exit}' <<<"$vol_info" | trim)"

if [[ "$mounted" != "Yes" ]]; then
  echo "[issue] Carve volume is present but Mounted is '$mounted'"
  exit 2
fi

if [[ -z "$physical_store" ]]; then
  echo "[issue] could not determine Carve physical store"
  exit 2
fi

whole_disk="$(sed -E 's/s[0-9]+$//' <<<"$physical_store")"
if ! disk_info="$(diskutil info "/dev/$whole_disk" 2>&1)"; then
  echo "[issue] diskutil could not read /dev/$whole_disk"
  echo "$disk_info"
  exit 2
fi

media_name="$(awk -F: '/Device \/ Media Name:/ {print $2; exit}' <<<"$disk_info" | trim)"
protocol="$(awk -F: '/Protocol:/ {print $2; exit}' <<<"$disk_info" | trim)"
location="$(awk -F: '/Device Location:/ {print $2; exit}' <<<"$disk_info" | trim)"
smart_status="$(awk -F: '/SMART Status:/ {print $2; exit}' <<<"$disk_info" | trim)"

if [[ -n "$EXPECTED_MEDIA_NAME" && "$media_name" != "$EXPECTED_MEDIA_NAME" ]]; then
  echo "[issue] Carve is mounted, but the backing disk name changed: expected '$EXPECTED_MEDIA_NAME', got '$media_name'"
  exit 1
fi

if [[ -n "$smart_status" && "$smart_status" != "Verified" && "$smart_status" != "Not Supported" ]]; then
  echo "[issue] Carve is mounted but SMART status is '$smart_status'"
  exit 1
fi

echo "[ok] Carve is mounted at $MOUNT_POINT"
echo "[ok] volume: ${volume_dev:-unknown} via ${whole_disk:-unknown}"
echo "[ok] backing disk: ${media_name:-unknown} • ${protocol:-unknown} • ${location:-unknown}"
[[ -n "$smart_status" ]] && echo "[ok] SMART status: $smart_status"
exit 0
