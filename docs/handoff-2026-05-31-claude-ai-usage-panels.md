# Claude Handoff: Waybar AI Usage Panels

Date: 2026-05-31
Repo: `/home/ben/dotfiles`

## User Goal

The user does not want clicked Waybar UI built on the shared transparent
`GlassPopup` overlay path. They specifically called out the Codex/Claude AI
usage click panels as ugly/broken and asked to make them separate opaque panels
like the rest of Waybar.

## Completed In This Pass

- Added a repo rule in `AGENTS.md`:
  - Do not build new clicked Waybar panels on `scripts/glass_popup.py`,
    `GlassPopup`, or full-screen transparent click-away overlays.
  - Use dedicated opaque layer-shell panels/windows like quick settings and the
    launcher.
- Reworked `scripts/ai-usage-popup.py`:
  - Removed `GlassPopup` inheritance/import.
  - Added a dedicated `GtkLayerShell` `Panel` with namespace `ai-usage`.
  - Uses a mostly opaque panel background: `rgba(12, 16, 24, 0.92)`.
  - Corrected top margin to sit just below the bar (`y=50` live) instead of
    leaving a large gap.
  - Anchors under the left Waybar AI bubbles:
    - Codex: `x=142`
    - Claude: `x=266`
  - Added icon-only controls with lowercase tooltips:
    - `` refresh
    - `` open usage
    - `` account
    - `` close
  - Lowercased visible static labels in the AI panel.
  - Keeps cached usage parsing, progress bars, Refresh, Open usage, and Codex
    Account actions.
  - Keeps same-service click toggle via per-service pid files.
  - Closes the other AI panel before opening a new one.
  - Supports close button, Escape, focus-out close, and pointer-grab outside
    click close.
- Added `scripts/waybar-ai-usage.sh` and updated `config/waybar/config.jsonc`:
  - Waybar now calls a short detached wrapper for Codex/Claude panel clicks.
  - This prevents Waybar from being blocked by the long-running panel process,
    so a second click can toggle the panel.
- Reworked `scripts/notification-panel.py`:
  - Removed `GlassPopup`; it is now a dedicated opaque `GtkLayerShell` panel.
  - Uses namespace `notification-panel`, not Mako's `notifications` namespace.
  - Anchors at the right side near the notification bubble (`x=1168 y=50`
    live on the current 1600 logical-pixel monitor).
  - Uses smaller typography, tighter rows, icon controls, Escape, focus-out,
    and pointer-grab outside click close.
- Updated `scripts/waybar-notifications.sh`:
  - Starts the notification panel with `setsid -f` so the wrapper exits
    immediately and the panel survives the shell exit.
- Updated `config/hypr/hyprland.conf`:
  - Added `ignore_alpha 0.01` for namespace `ai-usage`.
  - Added blur/ignore_alpha rules for `notification-panel`.
  - Updated the legacy `glass-popup` comment so it no longer claims AI usage is
    part of that path.
- Updated `config/mako/config`:
  - Reduced font size from 11 to 10.
  - Tightened width/height/padding/icon sizes/radii.
  - Reduced top outer margin from 16 to 6 for normal notifications.
  - Reduced the `volume-osd` top outer margin from 64 to 6.
  - Reloaded Mako.

## Verification Done

- `python3 -m py_compile scripts/ai-usage-popup.py scripts/quick-settings-panel.py`
- `python3 -m py_compile scripts/ai-usage-popup.py scripts/notification-panel.py`
- `bash -n scripts/waybar-ai-usage.sh scripts/waybar-notifications.sh`
- `hyprctl reload` returned `ok`.
- `makoctl reload` returned success.
- `systemctl --user restart waybar.service`; `systemctl --user is-active
  waybar.service` returned `active`.
- Live layer-shell smoke tests:
  - `/home/ben/dotfiles/scripts/waybar-ai-usage.sh codex`
    - `hyprctl layers -j` showed namespace `ai-usage` at `x=142 y=50 w=340 h=280`.
    - Re-running the wrapper toggled it closed.
  - `/home/ben/dotfiles/scripts/waybar-ai-usage.sh claude`
    - `hyprctl layers -j` showed namespace `ai-usage` at `x=266 y=50 w=340 h=280`.
    - Re-running the wrapper toggled it closed.
  - `/home/ben/dotfiles/scripts/waybar-notifications.sh open`
    - `hyprctl layers -j` showed namespace `notification-panel` at
      `x=1168 y=50 w=360 h=512`.
    - `waybar-notifications.sh toggle` closed it.
  - Mako native notifications are now at `y=50` in `hyprctl layers -j`.
- Confirmed no lingering `ai-usage-popup.py` process remains.
- Cleaned up the stray Codex-owned `waybar-helper sysmon 1400` process from an
  earlier aborted probe; the real Waybar-owned sysmon process remains.

## Not Done / Watch Points

- No screenshot or pixel-level visual inspection was taken.
- Could not synthesize a real outside pointer click because `ydotoold` cannot
  access `/dev/uinput` as this user (`Permission denied`). The panels now use
  `Gdk.Seat.grab(... POINTER ...)` plus outside-coordinate detection to close
  on outside pointer press, but this still needs a manual click check.
- The original sysmon legibility request was not completed in this pass because
  the user redirected to the AI panel issue.
- Left offsets are hard-coded. If the Waybar left modules change width, the
  panel may need margin adjustment.
- `network-popup.py`, `weather-popup.py`, `notification-panel.py`, and
  `emoji-picker.py` still use or refer to `GlassPopup`; do not expand that path.
  If the user asks to unify those, migrate them to dedicated opaque panels
  instead.
- The worktree had unrelated dirty files before this pass. Do not revert them.

## Files Touched By This Pass

- `AGENTS.md`
- `scripts/ai-usage-popup.py`
- `scripts/waybar-ai-usage.sh`
- `scripts/notification-panel.py`
- `scripts/waybar-notifications.sh`
- `config/waybar/config.jsonc`
- `config/mako/config`
- `config/hypr/hyprland.conf`
- `docs/handoff-2026-05-31-claude-ai-usage-panels.md`

## Claude Goal Prompt

Continue in `/home/ben/dotfiles`. The user wants clicked Waybar UI to stop using
the transparent full-screen `GlassPopup` overlay path. Respect `AGENTS.md`: do
not build new clicked Waybar panels on `scripts/glass_popup.py` or `GlassPopup`;
use dedicated opaque layer-shell panels/windows.

Start by reviewing the current diff for:

- `AGENTS.md`
- `scripts/ai-usage-popup.py`
- `config/hypr/hyprland.conf`

Then manually verify the live Hyprland desktop with real clicks:

- Click Codex and Claude Waybar bubbles; each should open an opaque panel at
  `y=50`, use lowercase labels, and have icon-only controls.
- Click the same bubble again; it should close.
- Click outside each panel; it should close via the pointer grab.
- Click notifications; the custom notification panel should open near the bell
  at `y=50`, not far below the bar.
- Trigger or observe a Mako notification; it should be smaller, tighter, and at
  `y=50`.

If needed, adjust only panel offsets/styling/click-close behavior. Do not touch
unrelated dirty files. If the user returns to the original sysmon legibility
request, handle that as a separate narrow change after the click panels and
Mako placement are confirmed.
