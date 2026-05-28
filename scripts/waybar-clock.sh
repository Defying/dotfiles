#!/usr/bin/env bash
# JSON clock with a useful calendar tooltip for Waybar.

set -euo pipefail

text=$(date '+%a %b %d  %H:%M' | tr '[:upper:]' '[:lower:]')
agenda=$(date '+%A, %B %-d, %Y')
calendar=$(cal -3 2>/dev/null || cal 2>/dev/null || true)

python3 - "$text" "$agenda" "$calendar" <<'PY'
import json
import sys

text, agenda, calendar = sys.argv[1:4]
tooltip = agenda
if calendar:
    tooltip = f"{agenda}\n\n{calendar}"
print(json.dumps({"text": text, "tooltip": tooltip}))
PY
