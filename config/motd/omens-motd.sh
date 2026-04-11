#!/bin/zsh

[[ -o interactive ]] || return 0 2>/dev/null || exit 0

autoload -Uz colors && colors

USER_NAME=${USER:-$(whoami)}
HOST_NAME=$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo "omens")
OS=$(sw_vers -productVersion 2>/dev/null || uname -r)
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "offline")
TIME_NOW=$(date "+%a %b %d, %I:%M %p")

UPTIME=$(uptime | sed -E 's/^.*up ([^,]+), .*$/\1/' 2>/dev/null)
LOAD=$(uptime | sed -nE 's/.*load averages?: ([0-9. ]+).*$/\1/p' | awk '{print $1" "$2" "$3}' 2>/dev/null)

DISK=$(df -h / 2>/dev/null | awk 'NR==2 {print $3"/"$2" used ("$5")"}')
DISK_DISPLAY=${DISK//\%/%%}
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
  printf "%.1fG used, %.1fG free", used, avail;
}')

OPENCLAW_STATUS="not installed"
OPENCLAW_COLOR=244
if [[ -x "/opt/homebrew/bin/openclaw" ]] || command -v openclaw >/dev/null 2>&1; then
  if launchctl list | grep -q 'ai.openclaw.gateway'; then
    OPENCLAW_STATUS="gateway up"
    OPENCLAW_COLOR=70
  else
    OPENCLAW_STATUS="installed, gateway off"
    OPENCLAW_COLOR=214
  fi
fi

PROJECT_ROOT="/Volumes/Carve/Projects"
PROJECT_SUMMARY="no project mount"
if [[ -d "$PROJECT_ROOT" ]]; then
  COUNT=$(find "$PROJECT_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
  RECENT=$(find "$PROJECT_ROOT" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null | xargs -0 stat -f '%m %N' 2>/dev/null | sort -nr | head -n 1 | cut -d' ' -f2-)
  if [[ -n "$RECENT" ]]; then
    PROJECT_SUMMARY="$COUNT projects, latest $(basename "$RECENT")"
  else
    PROJECT_SUMMARY="$COUNT projects"
  fi
fi

GIT_SUMMARY="no repo context"
GIT_COLOR=244
for repo in "$HOME/.openclaw/workspace" "$PROJECT_ROOT/music-dl" "$PROJECT_ROOT"; do
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
      GIT_SUMMARY="$GIT_SUMMARY • $dirty change"
      [[ "$dirty" != "1" ]] && GIT_SUMMARY+="s"
      GIT_COLOR=214
    else
      GIT_SUMMARY="$GIT_SUMMARY • clean"
      GIT_COLOR=70
    fi
    break
  fi
done

printf "\n"
print -P "%F{39}╭─%f %B%F{81}${HOST_NAME}%f%b  %F{244}for ${USER_NAME}%f  %F{244}macOS ${OS}%f  %F{244}ip ${IP}%f"
print -P "%F{39}├─%f %F{${OPENCLAW_COLOR}}claw ${OPENCLAW_STATUS}%f"
print -P "%F{39}├─%f %F{${GIT_COLOR}}git ${GIT_SUMMARY}%f"
print -P "%F{39}├─%f %F{244}proj ${PROJECT_SUMMARY}%f"
print -P "%F{39}├─%f %F{244}sys  ${UPTIME:-unknown} up • load ${LOAD:-unknown}%f"
print -P "%F{39}├─%f %F{244}disk ${DISK_DISPLAY:-unknown} • ram ${MEM:-unknown}%f"
print -P "%F{39}╰─%f %F{244}${TIME_NOW}%f"
printf "\n"
