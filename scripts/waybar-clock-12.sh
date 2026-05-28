#!/usr/bin/env bash
# 12-hour JSON clock for Waybar.

set -euo pipefail

text=$(date '+%-I:%M %p' | tr '[:upper:]' '[:lower:]')
tooltip=$(date '+%-I:%M:%S %p' | tr '[:upper:]' '[:lower:]')

python3 - "$text" "$tooltip" <<'PY'
import json
import sys

text, tooltip = sys.argv[1:3]
print(json.dumps({"text": text, "tooltip": tooltip}))
PY
