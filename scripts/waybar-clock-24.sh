#!/usr/bin/env bash
# 24-hour JSON clock for Waybar.

set -euo pipefail

text=$(date '+%H:%M')
tooltip=$(date '+%H:%M:%S')

python3 - "$text" "$tooltip" <<'PY'
import json
import sys

text, tooltip = sys.argv[1:3]
print(json.dumps({"text": text, "tooltip": tooltip}))
PY
