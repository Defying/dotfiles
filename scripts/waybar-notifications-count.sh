#!/usr/bin/env bash
# Output JSON for waybar's custom/notifications module: bell icon + badge count.

set -u

bell="$(printf '')"          # FA fa-bell
bell_slash="$(printf '')"    # FA fa-bell-slash

dnd_active=0
if makoctl mode 2>/dev/null | grep -qE '(^|[* ])do-not-disturb$'; then
  dnd_active=1
fi

# Count active mako notifications (sum across all notification groups).
count="$(makoctl list -j 2>/dev/null \
  | python3 -c 'import json,sys
try:
    data=json.load(sys.stdin)
except Exception:
    print(0); raise SystemExit
n=0
if isinstance(data, list):
    for e in data:
        if isinstance(e, dict) and "notifications" in e:
            n += len(e.get("notifications") or [])
        else:
            n += 1
print(n)' 2>/dev/null)"
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
