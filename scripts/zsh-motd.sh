#!/usr/bin/env bash
# Lightweight login banner. Reads only cheap local sources (sysfs, a cached
# JSON, one local git call) — no network, no `codex`/`dnf`/`claude` spawns — so
# it stays near-instant. Sourced from .zshrc for interactive login shells
# outside tmux (see the guard there), so it shows once per terminal, not per
# tmux pane.

set -u

P=$'\033[38;5;183m'   # purple
C=$'\033[38;5;81m'    # cyan
D=$'\033[38;5;245m'   # dim
W=$'\033[38;5;255m'   # bright
G=$'\033[38;5;78m'    # green
Y=$'\033[38;5;215m'   # amber
R=$'\033[38;5;203m'   # red
N=$'\033[0m'          # reset

# ── host · uptime · load ────────────────────────────────────────────────────
up=$(uptime -p 2>/dev/null | sed 's/^up //') ; up=${up:-up}
read -r load1 _ < /proc/loadavg 2>/dev/null || load1="?"
printf '%s\n' "${P} ${W}$(hostname -s)${N}  ${D}${up}  ·  load ${load1}${N}"

# ── battery ─────────────────────────────────────────────────────────────────
cap="" st=""
for ps in /sys/class/power_supply/*; do
  [[ -r "$ps/type" && "$(<"$ps/type")" == Battery ]] || continue
  [[ -r "$ps/capacity" ]] && cap="$(<"$ps/capacity")"
  [[ -r "$ps/status"   ]] && st="$(<"$ps/status")"
  break
done
if [[ -n "$cap" ]]; then
  case "$st" in
    Charging)      bicon="${G}⚡" ;;
    Full)          bicon="${G}" ;;
    "Not charging") bicon="${G}" ;;     # macsmc holds at the 80% cap
    *)             bicon="${Y}" ;;
  esac
  bcol="$G"; (( cap <= 30 )) && bcol="$Y"; (( cap <= 15 )) && bcol="$R"
  printf '%s\n' "${bicon} ${bcol}${cap}%${N} ${D}${st,,}${N}"
fi

# ── Claude usage (from the waybar cache; never refreshes here) ───────────────
claude_cache="${XDG_CACHE_HOME:-$HOME/.cache}/waybar/claude-usage.json"
if [[ -r "$claude_cache" ]] && command -v jq >/dev/null 2>&1; then
  read -r five week upd < <(jq -r '[(.usage.five_hour.utilization // empty),
                                    (.usage.seven_day.utilization // empty),
                                    (.updated_at // 0)] | @tsv' "$claude_cache" 2>/dev/null \
                            | tr "\t" " ")
  if [[ -n "${five:-}" ]]; then
    fr=$(( 100 - ${five%.*} )); wr=""
    [[ -n "${week:-}" && "$week" != "null" ]] && wr=$(( 100 - ${week%.*} ))
    now=$(date +%s); age=$(( (now - ${upd%.*}) / 60 ))
    line="${P}󰧑 ${D}claude ${W}${fr}%${D} 5h"
    [[ -n "$wr" ]] && line="${line}  ${W}${wr}%${D} week"
    (( age > 0 )) && line="${line}  ${D}(${age}m old)"
    printf '%s\n' "${line}${N}"
  fi
fi

# ── dotfiles working tree ────────────────────────────────────────────────────
if dirty=$(git -C "$HOME/dotfiles" status --porcelain 2>/dev/null); then
  n=$(printf '%s' "$dirty" | grep -c .)
  if (( n > 0 )); then
    printf '%s\n' "${Y} ${W}${n}${Y} uncommitted${D} in dotfiles${N}"
  fi
fi
