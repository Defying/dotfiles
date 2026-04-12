#!/bin/zsh

[[ -o interactive ]] || return 0 2>/dev/null || exit 0

autoload -Uz colors && colors

USER_NAME=${USER:-$(whoami)}
HOST_NAME=$(scutil --get LocalHostName 2>/dev/null || hostname -s 2>/dev/null || echo "omens")
OS=$(sw_vers -productVersion 2>/dev/null || uname -r)
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "offline")
TIME_NOW=$(date "+%a %b %d • %I:%M %p")

UPTIME=$(uptime | sed -E 's/^.*up ([^,]+), .*$/\1/' 2>/dev/null)
LOAD=$(uptime | sed -nE 's/.*load averages?: ([0-9. ]+).*$/\1/p' | awk '{print $1" " $2" " $3}' 2>/dev/null)
DISK=$(df -h / 2>/dev/null | awk 'NR==2 {print $3"/"$2" ("$5")"}')
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
  printf "%.1fG used • %.1fG free", used, avail;
}')

OPENCLAW_STATUS="offline"
OPENCLAW_COLOR=160
if [[ -x "/opt/homebrew/bin/openclaw" ]] || command -v openclaw >/dev/null 2>&1; then
  if launchctl list | grep -q 'ai.openclaw.gateway'; then
    OPENCLAW_STATUS="gateway online"
    OPENCLAW_COLOR=78
  else
    OPENCLAW_STATUS="installed, gateway asleep"
    OPENCLAW_COLOR=214
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
GIT_COLOR=244
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
      GIT_COLOR=214
    else
      GIT_SUMMARY+=" • clean"
      GIT_COLOR=78
    fi
    break
  fi
done

BACKUP_ROOT="/Volumes/Carve/Backups/orange-pi/archives"
BACKUP_SUMMARY="no archive yet"
if [[ -d "$BACKUP_ROOT" ]]; then
  LATEST_BACKUP=$(find "$BACKUP_ROOT" -maxdepth 1 -type f -name '*.tar.zst' -print0 2>/dev/null | xargs -0 stat -f '%m %N' 2>/dev/null | sort -nr | head -n 1 | cut -d' ' -f2-)
  if [[ -n "$LATEST_BACKUP" ]]; then
    BACKUP_SUMMARY="$(basename "$LATEST_BACKUP")"
  fi
fi

print -P ""
print -P "%F{45}╔══════════════════════════════════════════════════════════════════════╗%f"
print -P "%F{45}║%f %B%F{117}omens cockpit%f%b %F{244}for ${USER_NAME}%f %F{244}•%f %F{81}${TIME_NOW}%f"
print -P "%F{45}╠══════════════════════════════════════════════════════════════════════╣%f"
print -P "%F{45}║%f %F{81}machine%f   %B${HOST_NAME}%b %F{244}• macOS ${OS} • ip ${IP}%f"
print -P "%F{45}║%f %F{${OPENCLAW_COLOR}}claw%f      ${OPENCLAW_STATUS}"
print -P "%F{45}║%f %F{${GIT_COLOR}}repo%f      ${GIT_SUMMARY}"
print -P "%F{45}║%f %F{214}projects%f  ${PROJECT_SUMMARY} %F{244}• latest ${LATEST_PROJECT}%f"
print -P "%F{45}║%f %F{110}backup%f    ${BACKUP_SUMMARY}"
print -P "%F{45}║%f %F{244}system%f    up ${UPTIME:-unknown} %F{244}• load ${LOAD:-unknown}%f"
print -P "%F{45}║%f %F{244}storage%f   ${DISK_DISPLAY:-unknown} %F{244}• ram ${MEM:-unknown}%f"
print -P "%F{45}╚══════════════════════════════════════════════════════════════════════╝%f"
print -P ""
