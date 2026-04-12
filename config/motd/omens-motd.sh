#!/bin/zsh

[[ -o interactive ]] || return 0 2>/dev/null || exit 0

if [[ -t 1 && "${TERM:-dumb}" != "dumb" ]]; then
  BORDER_COLOR=$'\033[38;5;183m'
  TITLE_COLOR=$'\033[1;38;5;225m'
  DIM_COLOR=$'\033[38;5;244m'
  INFO_COLOR=$'\033[38;5;189m'
  GOOD_COLOR=$'\033[38;5;78m'
  WARN_COLOR=$'\033[38;5;214m'
  BACKUP_COLOR=$'\033[38;5;182m'
  RESET_COLOR=$'\033[0m'
else
  BORDER_COLOR=''
  TITLE_COLOR=''
  DIM_COLOR=''
  INFO_COLOR=''
  GOOD_COLOR=''
  WARN_COLOR=''
  BACKUP_COLOR=''
  RESET_COLOR=''
fi

HOST_NAME=$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo "omens")
OS=$(sw_vers -productVersion 2>/dev/null || uname -r)
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "offline")
TIME_NOW=$(date "+%a %b %d • %I:%M %p")

human_bytes_base() {
  emulate -L zsh
  local bytes="${1:-0}"
  local base="${2:-1024}"
  [[ -z "$bytes" ]] && bytes=0

  local -a units=(B KB MB GB TB PB)
  local unit=1
  local value=$(( bytes * 1.0 ))

  while (( unit < ${#units} && value >= base )); do
    value=$(( value / base ))
    (( unit++ ))
  done

  if (( unit == 1 )); then
    printf '%.0f%s' "$value" "${units[$unit]}"
  elif (( value >= 100 )); then
    printf '%.0f%s' "$value" "${units[$unit]}"
  else
    printf '%.1f%s' "$value" "${units[$unit]}"
  fi
}

human_disk_bytes() {
  human_bytes_base "$1" 1000
}

human_mem_bytes() {
  human_bytes_base "$1" 1024
}

human_age() {
  emulate -L zsh
  local seconds="${1:-0}"
  (( seconds < 0 )) && seconds=0

  local days=$(( seconds / 86400 ))
  local hours=$(( (seconds % 86400) / 3600 ))
  local mins=$(( (seconds % 3600) / 60 ))

  if (( days > 0 )); then
    if (( hours > 0 )); then
      print -r -- "${days}d ${hours}h ago"
    else
      print -r -- "${days}d ago"
    fi
  elif (( hours > 0 )); then
    if (( mins > 0 )); then
      print -r -- "${hours}h ${mins}m ago"
    else
      print -r -- "${hours}h ago"
    fi
  elif (( mins > 0 )); then
    print -r -- "${mins}m ago"
  else
    print -r -- "just now"
  fi
}

count_backup_artifacts() {
  local dir="$1"
  if [[ -d "$dir" ]]; then
    find "$dir" -maxdepth 1 -type f ! -name '.*' ! -name '*.sha256' 2>/dev/null | wc -l | tr -d ' '
  else
    print 0
  fi
}

sum_backup_artifact_bytes() {
  local dir="$1"
  if [[ -d "$dir" ]]; then
    find "$dir" -maxdepth 1 -type f ! -name '.*' ! -name '*.sha256' -exec stat -f '%z' {} + 2>/dev/null | awk '{sum += $1} END {print sum + 0}'
  else
    print 0
  fi
}

UPTIME=$(uptime | sed -E 's/^.*up ([^,]+), .*$/\1/' 2>/dev/null)
LOAD=$(uptime | sed -nE 's/.*load averages?: ([0-9. ]+).*$/\1/p' | awk '{print $1" " $2" " $3}' 2>/dev/null)

INTERNAL_TOTAL_BYTES=0
INTERNAL_FREE_BYTES=0
read INTERNAL_TOTAL_BYTES INTERNAL_FREE_BYTES <<<"$(diskutil info -plist / 2>/dev/null | python3 -c 'import plistlib,sys; raw=sys.stdin.buffer.read(); data=plistlib.loads(raw) if raw else {}; print(data.get("APFSContainerSize", 0), data.get("APFSContainerFree", 0))' 2>/dev/null)"
(( INTERNAL_TOTAL_BYTES > 0 )) || INTERNAL_TOTAL_BYTES=0
(( INTERNAL_FREE_BYTES > 0 )) || INTERNAL_FREE_BYTES=0
INTERNAL_USED_BYTES=$(( INTERNAL_TOTAL_BYTES - INTERNAL_FREE_BYTES ))
(( INTERNAL_USED_BYTES < 0 )) && INTERNAL_USED_BYTES=0
DISK="ssd $(human_disk_bytes "$INTERNAL_USED_BYTES") / $(human_disk_bytes "$INTERNAL_TOTAL_BYTES") used"

TOTAL_MEM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
MEM_USED_BYTES=$(vm_stat 2>/dev/null | awk -v total="$TOTAL_MEM_BYTES" '
/page size of/ {page=$8; gsub(/[^0-9]/, "", page)}
/Pages free/ {free=$3}
/Pages speculative/ {spec=$3}
/Pages purgeable/ {purge=$3}
END {
  gsub("\\.", "", free)
  gsub("\\.", "", spec)
  gsub("\\.", "", purge)
  if (!page) page=4096
  freebytes=(free+spec+purge)*page
  used=total-freebytes
  if (used < 0) used=0
  printf "%.0f", used
}')
[[ -n "$MEM_USED_BYTES" ]] || MEM_USED_BYTES=0
MEM="ram $(human_mem_bytes "$MEM_USED_BYTES") / $(human_mem_bytes "$TOTAL_MEM_BYTES") used"

OPENCLAW_STATUS="gateway asleep"
OPENCLAW_COLOR="$WARN_COLOR"
if [[ -x "/opt/homebrew/bin/openclaw" ]] || command -v openclaw >/dev/null 2>&1; then
  if launchctl list | grep -q 'ai.openclaw.gateway'; then
    OPENCLAW_STATUS="gateway online"
    OPENCLAW_COLOR="$GOOD_COLOR"
  fi
fi

PROJECT_ROOT="/Volumes/Carve/Projects"
PROJECT_SUMMARY="project mount missing"
LATEST_PROJECT="-"
if [[ -d "$PROJECT_ROOT" ]]; then
  COUNT=$(find "$PROJECT_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
  RECENT=$(find "$PROJECT_ROOT" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null | xargs -0 stat -f '%m %N' 2>/dev/null | sort -nr | head -n 1 | cut -d' ' -f2-)
  PROJECT_SUMMARY="$COUNT roots on Carve"
  if [[ -n "$RECENT" ]]; then
    LATEST_PROJECT=$(basename "$RECENT")
  fi
fi

GIT_SUMMARY="no repo context"
GIT_COLOR="$DIM_COLOR"
for repo in "$HOME/.openclaw/workspace" "$PROJECT_ROOT/dotfiles" "$PROJECT_ROOT"; do
  if [[ -d "$repo/.git" ]]; then
    branch=$(git -C "$repo" symbolic-ref --quiet --short HEAD 2>/dev/null)
    if [[ -n "$branch" ]]; then
      ref="$branch"
    else
      ref="detached @ $(git -C "$repo" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
    fi
    dirty=$(git -C "$repo" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    GIT_SUMMARY="$(basename "$repo") • $ref"
    if [[ "$dirty" != "0" ]]; then
      GIT_SUMMARY+=" • $dirty dirty"
      GIT_COLOR="$WARN_COLOR"
    else
      GIT_SUMMARY+=" • clean"
      GIT_COLOR="$GOOD_COLOR"
    fi
    break
  fi
done

BACKUP_STATUS="idle"
if pgrep -f 'backup-orange-pi-to-mini|backup-orangepi-sd-card|backup-raspberrypi-sd-card|backup-pi-sd-card' >/dev/null 2>&1; then
  BACKUP_STATUS="running"
fi

ORANGE_ARCHIVE_COUNT=$(count_backup_artifacts "/Volumes/Carve/Backups/orange-pi/archives")
ORANGE_IMAGE_COUNT=$(count_backup_artifacts "/Volumes/Carve/Backups/orange-pi/images")
RASPBERRY_ARCHIVE_COUNT=$(count_backup_artifacts "/Volumes/Carve/Backups/raspberry-pi/archives")
RASPBERRY_IMAGE_COUNT=$(count_backup_artifacts "/Volumes/Carve/Backups/raspberry-pi/images")

ORANGE_ARCHIVE_BYTES=$(sum_backup_artifact_bytes "/Volumes/Carve/Backups/orange-pi/archives")
ORANGE_IMAGE_BYTES=$(sum_backup_artifact_bytes "/Volumes/Carve/Backups/orange-pi/images")
RASPBERRY_ARCHIVE_BYTES=$(sum_backup_artifact_bytes "/Volumes/Carve/Backups/raspberry-pi/archives")
RASPBERRY_IMAGE_BYTES=$(sum_backup_artifact_bytes "/Volumes/Carve/Backups/raspberry-pi/images")
BACKUP_TOTAL_BYTES=$(( ${ORANGE_ARCHIVE_BYTES:-0} + ${ORANGE_IMAGE_BYTES:-0} + ${RASPBERRY_ARCHIVE_BYTES:-0} + ${RASPBERRY_IMAGE_BYTES:-0} ))

BACKUP_SUMMARY="${BACKUP_STATUS} • $(human_disk_bytes "$BACKUP_TOTAL_BYTES") • orange ${ORANGE_IMAGE_COUNT:-0}/${ORANGE_ARCHIVE_COUNT:-0} • raspberry ${RASPBERRY_IMAGE_COUNT:-0}/${RASPBERRY_ARCHIVE_COUNT:-0}"

TM_NAME=$(tmutil destinationinfo 2>/dev/null | awk -F': ' '/^Name[[:space:]]*:/ {print $2; exit}')
TM_MOUNT=$(tmutil destinationinfo 2>/dev/null | awk -F': ' '/^Mount Point[[:space:]]*:/ {print $2; exit}')
TM_RUNNING=$(tmutil status 2>/dev/null | awk -F'= ' '/Running =/ {gsub(/;/, "", $2); print $2; exit}')
TM_SUMMARY="not configured"
if [[ -n "$TM_NAME" ]]; then
  TM_STATUS="idle"
  [[ "$TM_RUNNING" == "1" ]] && TM_STATUS="running"

  TM_USED_BYTES=0
  TM_LAST_SNAPSHOT=''
  if [[ -n "$TM_MOUNT" ]]; then
    TM_USED_BYTES=$(diskutil info -plist "$TM_MOUNT" 2>/dev/null | python3 -c 'import plistlib,sys; raw=sys.stdin.buffer.read(); data=plistlib.loads(raw) if raw else {}; print(data.get("CapacityInUse", 0))' 2>/dev/null)
    TM_LAST_SNAPSHOT=$(diskutil info "$TM_MOUNT" 2>/dev/null | awk '/Name:[[:space:]]+com\.apple\.TimeMachine\./ {latest=$NF} END {print latest}')
  fi

  [[ -n "$TM_USED_BYTES" ]] || TM_USED_BYTES=0
  TM_SUMMARY="${TM_STATUS} • $(human_disk_bytes "$TM_USED_BYTES") total"

  if [[ -n "$TM_LAST_SNAPSHOT" ]]; then
    TM_STAMP=${TM_LAST_SNAPSHOT#com.apple.TimeMachine.}
    TM_STAMP=${TM_STAMP%.backup}
    TM_EPOCH=$(date -j -f "%Y-%m-%d-%H%M%S" "$TM_STAMP" "+%s" 2>/dev/null || true)
    if [[ -n "$TM_EPOCH" ]]; then
      TM_AGE=$(human_age $(( $(date +%s) - TM_EPOCH )))
      TM_SUMMARY+=" • ${TM_AGE}"
    fi
  else
    TM_SUMMARY+=" • no backups yet"
  fi
fi

repeat_char() {
  local char="$1"
  local count="$2"
  printf '%*s' "$count" '' | tr ' ' "$char"
}

fit_text() {
  local text="$1"
  local max="$2"
  if (( ${#text} > max )); then
    print -r -- "${text[1,$((max-1))]}…"
  else
    print -r -- "$text"
  fi
}

BOX_INNER=66
LABEL_W=9

box_rule() {
  local left="$1"
  local fill="$2"
  local right="$3"
  printf '%b%s%b\n' "$BORDER_COLOR" "${left}$(repeat_char "$fill" $((BOX_INNER + 2)))${right}" "$RESET_COLOR"
}

box_line() {
  local label="$1"
  local label_color="$2"
  local value="$3"
  local value_color="$4"
  local max_value=$((BOX_INNER - LABEL_W - 1))
  local shown
  shown=$(fit_text "$value" "$max_value")
  local pad=$((BOX_INNER - LABEL_W - 1 - ${#shown}))
  (( pad < 0 )) && pad=0

  printf '%b║ %b%-*s%b %b%s%b%*s %b║%b\n' \
    "$BORDER_COLOR" \
    "$label_color" "$LABEL_W" "$label" "$RESET_COLOR" \
    "$value_color" "$shown" "$RESET_COLOR" \
    "$pad" '' \
    "$BORDER_COLOR" "$RESET_COLOR"
}

box_title() {
  local text
  text=$(fit_text "$1" "$BOX_INNER")
  local pad=$((BOX_INNER - ${#text}))
  (( pad < 0 )) && pad=0
  printf '%b║ %b%s%b%*s %b║%b\n' \
    "$BORDER_COLOR" "$TITLE_COLOR" "$text" "$RESET_COLOR" \
    "$pad" '' \
    "$BORDER_COLOR" "$RESET_COLOR"
}

print ""
box_rule '╔' '═' '╗'
box_title "${HOST_NAME} • ${TIME_NOW}"
box_rule '╠' '═' '╣'
box_line 'machine' "$INFO_COLOR" "${HOST_NAME} • macOS ${OS} • ip ${IP}" "$DIM_COLOR"
box_line 'claw' "$OPENCLAW_COLOR" "$OPENCLAW_STATUS" "$OPENCLAW_COLOR"
box_line 'repo' "$GIT_COLOR" "$GIT_SUMMARY" "$GIT_COLOR"
box_line 'projects' "$WARN_COLOR" "${PROJECT_SUMMARY} • latest ${LATEST_PROJECT}" "$DIM_COLOR"
box_line 'backup' "$BACKUP_COLOR" "$BACKUP_SUMMARY" "$DIM_COLOR"
box_line 'tm' "$BACKUP_COLOR" "$TM_SUMMARY" "$DIM_COLOR"
box_line 'system' "$DIM_COLOR" "up ${UPTIME:-unknown} • load ${LOAD:-unknown}" "$DIM_COLOR"
box_line 'storage' "$DIM_COLOR" "${DISK:-unknown} • ${MEM:-unknown}" "$DIM_COLOR"
box_rule '╚' '═' '╝'
print ""
