#!/usr/bin/env bash
# Output JSON for waybar's custom/notifications module: bell icon + badge count.

set -u

bell="$(printf '')"          # FA fa-bell
bell_slash="$(printf '')"    # FA fa-bell-slash

dnd_active=0
if makoctl mode 2>/dev/null | grep -qE '(^|[* ])do-not-disturb$'; then
  dnd_active=1
fi

# Count active mako notifications (sum across all notification groups). jq
# instead of python keeps this 5s-interval module off the ~29ms Python
# interpreter spawn — makoctl and jq are both small C programs.
count="$(makoctl list -j 2>/dev/null \
  | jq -r 'if type=="array"
           then ([.[] | if (type=="object" and has("notifications"))
                        then (.notifications|length) else 1 end] | add) // 0
           else 0 end' 2>/dev/null)"
count="${count:-0}"

if [[ "$dnd_active" -eq 1 ]]; then
  text="$bell_slash"
  klass="dnd"
elif [[ "$count" -gt 0 ]]; then
  text="$bell  $count"
  klass="active"
else
  text="$bell"
  klass="idle"
fi

tooltip="notifications: $count active"
[[ "$dnd_active" -eq 1 ]] && tooltip="do-not-disturb · $tooltip"

printf '{"text": "%s", "tooltip": "%s", "class": "%s"}\n' "$text" "$tooltip" "$klass"
