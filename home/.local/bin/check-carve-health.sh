#!/bin/zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

MOUNT_POINT="/Volumes/Carve"

trim() {
  sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

if [[ ! -d "$MOUNT_POINT" ]]; then
  echo "[issue] Carve is not mounted at $MOUNT_POINT"
  exit 2
fi

vol_info="$(diskutil info "$MOUNT_POINT")"
volume_dev="$(awk -F: '/Device Identifier:/ {print $2; exit}' <<<"$vol_info" | trim)"
smart_status="$(awk -F: '/SMART Status:/ {print $2; exit}' <<<"$vol_info" | trim)"
physical_store="$(awk -F: '/APFS Physical Store:/ {print $2; exit}' <<<"$vol_info" | trim)"

if [[ -z "$physical_store" ]]; then
  echo "[issue] could not determine Carve physical store from diskutil info"
  exit 2
fi

whole_disk="$(sed -E 's/s[0-9]+$//' <<<"$physical_store")"
disk_info="$(diskutil info "/dev/$whole_disk")"
media_name="$(awk -F: '/Device \/ Media Name:/ {print $2; exit}' <<<"$disk_info" | trim)"
protocol="$(awk -F: '/Protocol:/ {print $2; exit}' <<<"$disk_info" | trim)"
disk_size="$(awk -F: '/Disk Size:/ {print $2; exit}' <<<"$disk_info" | trim)"

smartctl_path="$(command -v smartctl || true)"
smartctl_summary="not available"
smartctl_issue=""
smartctl_note=""
model_number=""
serial_number=""
firmware_version=""

if [[ -n "$smartctl_path" ]]; then
  io_path="$(smartctl --scan-open 2>/dev/null | sed -n "/${media_name//\//\\/}/ s/ -d .*//p" | head -n 1)"
  smart_output=""
  smart_rc=0
  if [[ -n "$io_path" ]]; then
    smart_output="$(smartctl -a -d nvme "$io_path" 2>&1)" || smart_rc=$?
  else
    smart_output="$(smartctl -a -d nvme "/dev/$whole_disk" 2>&1)" || smart_rc=$?
  fi

  model_number="$(awk -F: '/Model Number:/ {print $2; exit}' <<<"$smart_output" | trim)"
  serial_number="$(awk -F: '/Serial Number:/ {print $2; exit}' <<<"$smart_output" | trim)"
  firmware_version="$(awk -F: '/Firmware Version:/ {print $2; exit}' <<<"$smart_output" | trim)"

  if grep -q 'Read NVMe SMART/Health Information .* failed: GetLogPage failed: .*code=706' <<<"$smart_output"; then
    smartctl_summary="identity visible, full NVMe SMART health log blocked by USB bridge on macOS"
    smartctl_note="USB/NVMe bridge is blocking the detailed SMART/self-test log pages (GetLogPage code 706)."
  elif [[ $smart_rc -eq 0 ]]; then
    smartctl_summary="full SMART query succeeded"
  elif [[ -n "$smart_output" ]]; then
    smartctl_summary="smartctl returned rc=$smart_rc"
    smartctl_issue="smartctl returned rc=$smart_rc: $(head -n 1 <<<"$smart_output")"
  fi
fi

verify_output="$(diskutil verifyVolume "$MOUNT_POINT" 2>&1)" || true
verify_summary="unknown"
verify_issue=""
if grep -q 'appears to be OK' <<<"$verify_output"; then
  verify_summary="APFS verify OK"
elif grep -q 'Error:' <<<"$verify_output"; then
  verify_summary="APFS verify reported an error"
  verify_issue="$(grep 'Error:' <<<"$verify_output" | tail -n 1 | trim)"
else
  verify_summary="APFS verify completed without explicit OK"
fi

issues=()
if [[ "$smart_status" != "Verified" ]]; then
  issues+=("diskutil SMART status is '$smart_status'")
fi
if [[ -n "$verify_issue" ]]; then
  issues+=("$verify_issue")
fi
if [[ -n "$smartctl_issue" ]]; then
  issues+=("$smartctl_issue")
fi

summary_line="Carve SSD: ${media_name:-unknown} on /dev/$whole_disk (${disk_size:-unknown}, ${protocol:-unknown})"
model_line="Drive: ${model_number:-unknown}"
[[ -n "$serial_number" ]] && model_line+=" • S/N ${serial_number}"
[[ -n "$firmware_version" ]] && model_line+=" • FW ${firmware_version}"

if (( ${#issues[@]} == 0 )); then
  echo "[ok] $summary_line"
  echo "[ok] $model_line"
  echo "[ok] diskutil SMART status: ${smart_status:-unknown}"
  echo "[ok] smartctl: $smartctl_summary"
  [[ -n "$smartctl_note" ]] && echo "[note] $smartctl_note"
  echo "[ok] filesystem: $verify_summary"
  exit 0
fi

echo "[issue] $summary_line"
echo "[issue] $model_line"
echo "[issue] diskutil SMART status: ${smart_status:-unknown}"
echo "[issue] smartctl: $smartctl_summary"
[[ -n "$smartctl_note" ]] && echo "[note] $smartctl_note"
echo "[issue] filesystem: $verify_summary"
for issue in "${issues[@]}"; do
  echo "[issue] $issue"
done
exit 1
