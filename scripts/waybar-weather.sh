#!/usr/bin/env bash
# wttr.in → JSON for waybar: glyph + temp in bar, fuller forecast in tooltip.
# Caches the last successful response so a transient curl failure does not
# blank the bar to "--" until the next interval tick.

set -u

cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/waybar-weather"
mkdir -p "$cache_dir"
cache_file="$cache_dir/last.json"

emit_cached_or_blank() {
  if [[ -s "$cache_file" ]]; then
    cat "$cache_file"
  else
    printf '{"text":" --°","tooltip":"weather unavailable","class":"unavailable"}\n'
  fi
}

out=$(curl -s --max-time 8 'https://wttr.in/?format=%c+%t' 2>/dev/null)
if [[ -z "$out" || "$out" == *"Unknown location"* ]]; then
  emit_cached_or_blank
  exit 0
fi

emoji=${out%%+*}
temp=${out#*+}
temp=${temp//°F/}
temp=${temp//°C/}
temp=${temp//+/}
temp=${temp// /}

if [[ -z "$temp" ]]; then
  emit_cached_or_blank
  exit 0
fi

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

tip=$(curl -s --max-time 8 'https://wttr.in/?format=%l:+%C\nfeels+%f++humidity+%h\nwind+%w++%p+precip\nsun+%S+→+%s' 2>/dev/null)
forecast=$(curl -s --max-time 10 'https://wttr.in/?T&0' 2>/dev/null | sed -n '1,7p')

if [[ -n "$tip" || -n "$forecast" ]]; then
  tooltip="${tip}"
  [[ -n "$forecast" ]] && tooltip="${tooltip}

${forecast}"
else
  tooltip="weather details unavailable"
fi

json=$(python3 - "$icon" "$temp" "$tooltip" <<'PY'
import json
import sys

icon, temp, tooltip = sys.argv[1:4]
print(json.dumps({"text": f"{icon} {temp}°", "tooltip": tooltip}, ensure_ascii=False))
PY
)

if [[ -n "$json" ]]; then
  printf '%s\n' "$json" | tee "$cache_file"
else
  emit_cached_or_blank
fi
