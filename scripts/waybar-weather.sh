#!/usr/bin/env bash
# wttr.in → JSON for waybar: bar shows glyph + temp, tooltip shows fuller forecast.
out=$(curl -s --max-time 4 'https://wttr.in/?format=%c+%t')
if [[ -z "$out" ]]; then
  printf '{"text":"","tooltip":"weather unavailable"}\n'
  exit 0
fi

emoji=${out%%+*}
temp=${out#*+}
temp=${temp//°F/}
temp=${temp//°C/}
temp=${temp//+/}
temp=${temp// /}

SUN=$''
CLOUD_SUN=$''
CLOUD=$''
RAIN=$''
SNOW=$''
BOLT=$''
SMOG=$''

case "$emoji" in
  *☀*)                  icon="$SUN" ;;
  *⛅*|*🌤*|*🌥*)        icon="$CLOUD_SUN" ;;
  *☁*)                  icon="$CLOUD" ;;
  *🌧*|*🌦*)             icon="$RAIN" ;;
  *🌨*|*❄*)             icon="$SNOW" ;;
  *⛈*)                  icon="$BOLT" ;;
  *🌫*)                  icon="$SMOG" ;;
  *)                    icon="$CLOUD_SUN" ;;
esac

# Tooltip: condition + feels-like + humidity + wind + today hi/lo + next 2 day summary
tip=$(curl -s --max-time 4 'https://wttr.in/?format=%l:+%C\nfeels+%f++humidity+%h\nwind+%w++%p+precip\nsun+%S+→+%s' 2>/dev/null)
forecast=$(curl -s --max-time 6 'https://wttr.in/?T&0' 2>/dev/null | sed -n '1,7p')

if [[ -n "$tip" || -n "$forecast" ]]; then
  tooltip="${tip}"
  [[ -n "$forecast" ]] && tooltip="${tooltip}

${forecast}"
else
  tooltip="weather details unavailable"
fi

python3 - "$icon" "$temp" "$tooltip" <<'PY'
import json
import sys

icon, temp, tooltip = sys.argv[1:4]
print(json.dumps({"text": f"{icon} {temp}°", "tooltip": tooltip}, ensure_ascii=False))
PY
