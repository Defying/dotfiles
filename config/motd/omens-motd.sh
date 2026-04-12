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

UPTIME=$(uptime | sed -E 's/^.*up ([^,]+), .*$/\1/' 2>/dev/null)
LOAD=$(uptime | sed -nE 's/.*load averages?: ([0-9. ]+).*$/\1/p' | awk '{print $1" " $2" " $3}' 2>/dev/null)
DISK=$(df -h / 2>/dev/null | awk 'NR==2 {print $3"/"$2" ("$5")"}')
MEM=$(vm_stat 2>/dev/null | awk '
/Pages free/ {free=$3}
/Pages active/ {active=$3}
/Pages inactive/ {inactive=$3}
/Pages speculative/ {spec=$3}
/Pages wired down/ {wired=$4}
/Pages occupied by compressor/ {comp=$5}
END {
  gsub("\\.","",free); gsub("\\.","",active); gsub("\\.","",inactive); gsub("\\.","",spec); gsub("\\.","",wired); gsub("\\.","",comp);
  page=4096;
  used=(active+inactive+spec+wired+comp)*page/1024/1024/1024;
  avail=free*page/1024/1024/1024;
  printf "%.1fG used • %.1fG free", used, avail;
}')

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

BACKUP_ROOT="/Volumes/Carve/Backups/orange-pi/archives"
BACKUP_SUMMARY="no archive yet"
if [[ -d "$BACKUP_ROOT" ]]; then
  LATEST_BACKUP=$(find "$BACKUP_ROOT" -maxdepth 1 -type f -name '*.tar.zst' -print0 2>/dev/null | xargs -0 stat -f '%m %N' 2>/dev/null | sort -nr | head -n 1 | cut -d' ' -f2-)
  if [[ -n "$LATEST_BACKUP" ]]; then
    BACKUP_SUMMARY=$(basename "$LATEST_BACKUP")
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
box_line 'system' "$DIM_COLOR" "up ${UPTIME:-unknown} • load ${LOAD:-unknown}" "$DIM_COLOR"
box_line 'storage' "$DIM_COLOR" "${DISK:-unknown} • ram ${MEM:-unknown}" "$DIM_COLOR"
box_rule '╚' '═' '╝'
print ""
