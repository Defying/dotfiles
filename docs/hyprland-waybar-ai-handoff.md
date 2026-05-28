# Hyprland / Waybar / AI Usage Handoff

Date: 2026-05-25
Repo: `/home/ben/dotfiles`

## Current Goal

The desktop is being tuned around a glass-style Hyprland + Waybar setup with:

- Separate Waybar bubbles for workspaces, Codex usage, Claude usage, tray, weather, system status, and quick settings.
- AI usage indicators that use provider auth/API paths, not local transcript logs.
- A quick settings panel that opens from the right side under the gear.
- Mako notifications and OSDs styled as readable glass, without global screen shader overlays that distort text.

## Current Waybar Layout

File: `config/waybar/config.jsonc`

Important state:

- Global bar height: `56`
- Tooltip delay: `100`
- Left modules:
  - `hyprland/workspaces`
  - `custom/codex-tokens`
  - `custom/claude-tokens`
- Center:
  - `custom/clock`
- Right modules:
  - `tray`
  - `custom/weather`
  - `group/status`
  - `custom/quick-settings`
- `group/status` contains:
  - `network`
  - `pulseaudio`
  - `battery`

AI refresh intervals are both set to `300` seconds:

- `custom/codex-tokens.interval = 300`
- `custom/claude-tokens.interval = 300`

Battery charging text was removed again. Charging/plugged state should stay compact:

- `format-charging = " {capacity}%"`
- `format-plugged = " {capacity}%"`

## Waybar Styling Notes

File: `config/waybar/style.css`

Current design:

- Bubbles use translucent glass backgrounds with inset highlights.
- External drop shadow was removed from the main bubble rule because it was clipping at the 56px layer boundary and appearing as a faint full-width separator line between the bar and desktop.
- Do not add Hyprland `blur` layerrules for the `waybar` namespace. That caused rectangular peach blur blocks behind each bubble.
- Tooltip CSS has been rounded/glass-styled, but compositor-level tooltip blur is still a tricky area. Do not re-add `layerrule = blur true, match:namespace ^waybar$`.

AI warning state:

- `warn`/auth/error/rate classes use bright amber text plus amber-tinted bubble background.
- `danger` uses light red text plus darker red-tinted bubble background.
- The user could not read the original red text, so keep danger text high-contrast.

## Codex Usage Indicator

File: `scripts/waybar-openai-tokens.py`

Current behavior:

- Uses `codex app-server --listen stdio://`.
- Sends JSON-RPC:
  - `initialize`
  - `initialized`
  - `account/rateLimits/read`
- Reads Codex subscription limits from the response.
- Does not use OpenAI API keys.
- Does not read logs.
- Displays percent remaining, not percent used.
- Current tested output was:
  - `codex critical 7%`
  - Tooltip showed 5h and weekly windows at 7% remaining.

Low-limit behavior:

- `<= 30% remaining`: text becomes `codex low NN%`, class `warn`.
- `<= 10% remaining` or hard limit reached: text becomes `codex critical NN%`, class `danger`.
- Sends a mako notification via `notify-send` from app `AI usage`.
- Notification cooldown is 45 minutes per service/level/reset-window using files in `${XDG_CACHE_HOME:-~/.cache}/waybar`.

## Claude Usage Indicator

File: `scripts/waybar-claude-usage.py`

Current behavior:

- Reads Claude Code OAuth credentials from `~/.claude/.credentials.json`.
- Calls Anthropic OAuth usage endpoint:
  - `https://api.anthropic.com/api/oauth/usage`
  - `Authorization: Bearer <Claude Code OAuth token>`
- Does not use API keys.
- Does not read Claude transcript logs.
- Displays percent remaining, not percent used.
- Current tested output was:
  - `claude 57%`
  - 5h reset: 9:10 PM on 2026-05-25
  - weekly reset: Sunday 2026-05-31 at 4:00 AM

Caching/rate behavior:

- Cache file: `${XDG_CACHE_HOME:-~/.cache}/waybar/claude-usage.json`
- Cache TTL: 300 seconds.
- If a fresh cache exists, the script uses it and does not call Anthropic.
- If the endpoint returns 429 and a cache exists, it keeps showing cached percent and notes stale data in the tooltip.
- If the endpoint returns 429 and no cache exists, it shows `claude rate`.
- Once a later refresh succeeds, it returns to `claude NN%`.

Low-limit behavior:

- `<= 30% remaining`: text becomes `claude low NN%`, class `warn`.
- `<= 10% remaining`: text becomes `claude critical NN%`, class `danger`.
- Sends a mako notification via `notify-send` from app `AI usage`.
- Notification cooldown is 45 minutes per service/level/reset-window.

## Quick Settings

Files:

- `scripts/quick-settings-panel.py`
- `scripts/waybar-quick-settings.sh`

Current behavior:

- Waybar gear is on the right side, after the Wi-Fi/volume/battery status bubble.
- Quick settings panel is anchored top-right with a 28px right margin and 62px top margin.
- The panel toggles as a singleton using a PID file in `$XDG_RUNTIME_DIR`.
- Panel now loads Codex/Claude statuses asynchronously so it does not block initial display on API calls.
- Fallback fuzzel menu exists in `waybar-quick-settings.sh`.
- Power is available inside quick settings and opens `/home/ben/.local/bin/hypr-power-menu`.

Known user complaint:

- Quick settings was considered slow and not glass enough. It was restyled and made async, but it may still need visual refinement.

## Hyprland Layer Rules

File: `config/hypr/hyprland.conf`

Important state:

- Keep the base screen shader:
  - `decoration:screen_shader = /home/ben/dotfiles/config/hypr/shaders/rounded-corners.frag`
- Notification liquid-glass global shader autostart was removed/commented because it distorted notification text.
- Current intended layer blur rules:
  - `notifications`
  - `liquid-osd`
  - `launcher`
  - `weather-popup`
  - `quick-settings`
- Current intended `ignore_alpha` rules match those namespaces.
- Do not add `waybar` to these rules unless the rectangular background artifact is solved another way.

The removed/problematic rules were:

```conf
layerrule = blur true, match:namespace ^waybar$
layerrule = match:namespace waybar, ignore_alpha 0.01
```

Those caused square/rectangular backgrounds behind the Waybar bubbles.

## Mako Notifications

File: `config/mako/config`

Current state:

- Mako was too black/opaque.
- Background changed to translucent glass:
  - `background-color=#14171e88`
- Border changed to:
  - `border-color=#ffffff4d`
- Critical notification background:
  - `background-color=#2a0b128c`
- Mako still relies on Hyprland `layerrule = blur true, match:namespace ^notifications$`.
- `notification-glass.py` is no longer started by Hyprland because it applies a global screen shader over notification content.

## Volume / Brightness OSD

Files:

- `scripts/volume-osd.sh`
- `scripts/liquid-osd.py`
- Hyprland keybinds in `config/hypr/hyprland.conf`

Current user complaint:

- Volume/brightness OSDs were hard to read.

Recent partial change:

- `scripts/liquid-osd.py` was patched to stop importing/using `glass_shader` for OSD content and to draw a darker Cairo glass backing with higher contrast text.
- This needs verification because the turn was interrupted around this work. Run:

```sh
python3 -m py_compile scripts/liquid-osd.py
/home/ben/dotfiles/scripts/volume-osd.sh up
/home/ben/dotfiles/scripts/volume-osd.sh bright-up
```

Then visually inspect or screenshot the OSD.

## Validation Commands Used

Useful checks:

```sh
jq . config/waybar/config.jsonc >/dev/null
python3 -m py_compile scripts/waybar-openai-tokens.py scripts/waybar-claude-usage.py scripts/quick-settings-panel.py scripts/liquid-osd.py
Hyprland --verify-config --config /home/ben/dotfiles/config/hypr/hyprland.conf
hyprctl reload
hyprctl configerrors
pkill -x waybar; setsid -f waybar >/tmp/waybar.log 2>&1
tail -n 80 /tmp/waybar.log
```

Screenshot commands:

```sh
grim -g "0,0 1536x140" /tmp/waybar-top.png
grim -g "1030,80 500x260" /tmp/mako-glass-test.png
```

## Current Live Outputs

At the time this report was written:

```json
{"text":"codex critical 7%","class":"danger"}
{"text":"claude 57%","class":"subscription"}
```

Codex is critically low. Claude is not currently low.

## Watch Outs

- The user is sensitive to visual regressions. Screenshot before declaring visual fixes complete.
- Do not solve Waybar tooltip glass by adding blur rules to the whole `waybar` namespace; that created bad rectangular artifacts.
- Do not use OpenAI API keys for Codex usage. The user wants subscription usage, not API billing.
- Do not read Codex or Claude logs for usage.
- Keep AI refreshes at 5 minutes, not 1 minute.
- Quick settings should stay right-anchored under the right-side gear.
- The worktree has many unrelated dirty files; do not revert unrelated changes.
