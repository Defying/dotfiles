# OmniWM/Hiro Windowing Migration

Date: 2026-06-01
Repo: `/home/ben/dotfiles`

## Intent

Migrate the *feeling* of the current Hyprland windowing setup to macOS with
OmniWM/Hiro. This is not a Waybar or Linux desktop port.

The goal is the minimum useful window-manager muscle memory:

- automatic tiling
- fast keyboard focus
- workspace switching
- moving windows between workspaces
- resize/swap/fullscreen/floating controls
- launcher / command palette
- Ghostty terminal workflow
- scratchpad or quake-terminal equivalent if available

## Do Not Port

Do not port the Linux bar and panel stack unless the user explicitly asks later:

- Waybar
- AI usage bubbles or menus
- quick settings / notification / weather / network glass panels
- Mako notification workflow
- Linux OSDs
- brightness/audio scripts
- aerial wallpaper controls
- clipboard history UI
- power/logout menu

Use the macOS menu bar, macOS notifications, macOS screenshots, macOS media keys,
macOS brightness, and native app menus.

## Source Of Truth

Read these first:

- `config/hypr/hyprland.conf`
- `home/.local/bin/hypr-keybindings`
- `docs/macos-parity-2026-05-28.md` only if more context is needed

The relevant Hyprland shape:

- `$mainMod = SUPER`, used as Cmd-like muscle memory
- `layout = dwindle`
- numbered workspaces 1-10
- Cmd-arrow focus
- Cmd-Shift-arrow swap
- Cmd-Alt-arrow resize
- Cmd-Return terminal
- Cmd-Space launcher
- Cmd-F fullscreen
- Cmd-T floating/tiling
- Cmd-S scratchpad

## Target Mapping

Prefer OmniWM/Hiro built-ins and `~/.config/omniwm/settings.toml`. Avoid custom
glue unless a built-in feature cannot do it.

Set the default layout to Dwindle if supported. If not, document the closest
available layout and why.

Map the core keys:

- `Cmd+Return`: open Ghostty
- `Cmd+Space`: OmniWM/Hiro command palette or launcher
- `Cmd+1..0`: workspace 1-10
- `Cmd+Ctrl+1..0`: move focused window to workspace 1-10
- `Cmd+Tab`: next workspace, if supported
- `Cmd+Shift+Tab`: previous workspace, if supported
- `Cmd+Arrow`: focus window
- `Cmd+Shift+Arrow`: swap or move window
- `Cmd+Alt+Arrow`: resize window
- `Cmd+F`: fullscreen
- `Cmd+T`: toggle floating/tiling, if supported cleanly
- `Cmd+S`: scratchpad/sticky/quakey equivalent, if supported
- `Cmd+K`: show a short keybinding reference, if easy

Add app rules only for obvious floaters:

- preferences/settings dialogs
- auth/login dialogs
- calculators
- tiny utility windows
- transient system panels

## Machine And Build Rules

This Asahi M1 MacBook Air is mostly a thin client. The M4 Mac mini is where
compilations and substantive build work should happen, then artifacts should be
transferred back.

For this migration, use SSH to inspect and configure the macOS side directly.
If any source build is required, build on the M4 Mac mini and transfer only the
needed result.

## Execution Notes

Before changing macOS config, back up existing OmniWM/Hiro settings.

Verify macOS prerequisites instead of assuming them:

- OmniWM/Hiro installed and launchable
- Accessibility permission
- Input Monitoring permission if custom hotkeys need it
- Mission Control setting required by OmniWM/Hiro, if applicable

Make incremental changes and test each category live:

- launch terminal
- switch workspaces
- move window to workspace
- focus by arrow
- swap/move by arrow
- resize
- fullscreen
- floating
- scratchpad/quake equivalent

## Deliverables

Leave a concise update in this file or a sibling dated handoff with:

- what macOS host was changed
- settings file paths touched
- backup paths created
- final key map
- unsupported Hyprland behaviors and nearest equivalents
- rollback steps

Completion means the windowing muscle memory works well enough to use, not that
the full Linux desktop stack has been recreated.

## Execution Result 2026-06-01

Host changed: `mini` over SSH, hostname `omens`, macOS 26.4.1 arm64.
OmniWM is installed at `/Applications/OmniWM.app`, bundle
`com.barut.OmniWM`, version `0.4.9.6`.

Settings touched:

- `/Users/ben/.config/omniwm/settings.toml`

Backups created:

- `/Users/ben/.config/omniwm/settings.toml.pre-codex-omniwm-hiro-20260601-104452`
- `/Users/ben/.config/omniwm/settings.toml.pre-codex-omniwm-hiro-supported-test-20260601-105426`
- `/Users/ben/.config/omniwm/settings.toml.pre-codex-omniwm-hiro-workspaces-test-20260601-105523`
- `/Users/ben/.config/omniwm/settings.toml.pre-codex-omniwm-hiro-hotkey10-test-20260601-105552`

Final config state:

- `defaultLayoutType = "dwindle"`
- `ipcEnabled = true`
- workspaces `1` through `10` exist and use Dwindle
- existing workspace display names and monitor assignments were preserved
- no Linux Waybar, Mako, panel, OSD, wallpaper, clipboard, or power-menu stack
  was ported
- no new app rules were added, because the active GUI was locked at
  `loginwindow` and no managed app windows were visible to inspect

Final key map:

- `Cmd+Return`: `toggleQuakeTerminal`
- `Cmd+Space`: `openCommandPalette`
- `Cmd+1..9`: switch to workspaces `1..9`
- `Cmd+Ctrl+1..9`: move focused window to workspaces `1..9`
- `Cmd+Tab`: next workspace
- `Cmd+Shift+Tab`: previous workspace
- `Cmd+Arrow`: focus by direction
- `Cmd+Shift+Arrow`: move/swap by direction
- `Cmd+Option+Arrow`: resize/grow by direction
- `Cmd+F`: toggle fullscreen
- `Cmd+T`: toggle focused window floating
- `Cmd+S`: toggle scratchpad
- `Cmd+Option+S`: assign focused window to scratchpad
- `Cmd+K`: open OmniWM menu anywhere, used as the nearest built-in key/help
  surface

Unsupported or nearest equivalents:

- OmniWM 0.4.9.6 rejected added `switchWorkspace.9` and
  `moveToWorkspace.9` hotkey IDs. Workspace `10` exists and
  `omniwmctl command switch-workspace 10` works, but `Cmd+0` and
  `Cmd+Ctrl+0` are not persistently bindable through the current settings
  schema.
- OmniWM does not expose a generic persisted "launch Ghostty" hotkey in
  `settings.toml` or `omniwmctl`. `Cmd+Return` is mapped to the built-in
  quake terminal as the closest supported terminal workflow.
- Window-level verification was limited because the active GUI focus was
  `loginwindow`; `omniwmctl query focused-window-decision` reported
  `attribute-fetch-failed` for `com.apple.loginwindow`, and
  `omniwmctl query windows` returned no managed windows even after starting
  Ghostty and Calculator from SSH. Unlock the macOS session before final
  hand testing.

Verification performed:

- parsed `settings.toml` with Python `tomllib`
- relaunched OmniWM and confirmed it reads the edited settings file with `lsof`
- confirmed `omniwmctl ping` returns `pong`
- confirmed `omniwmctl version` returns `0.4.9.6 (protocol 5)`
- confirmed Accessibility TCC entry exists with `auth_value = 2`
- confirmed live workspaces query reports workspaces `1..10` with Dwindle
- confirmed `omniwmctl command switch-workspace 10` works, then restored
  active workspace `1`
- confirmed command paths execute for focus, move, resize, fullscreen, command
  palette, menu anywhere, and quake terminal; scratchpad toggle returns
  `not_found` until a managed focused window is assigned

Rollback:

```sh
ssh mini 'pkill -x OmniWM >/dev/null 2>&1 || true; cp -p /Users/ben/.config/omniwm/settings.toml.pre-codex-omniwm-hiro-supported-test-20260601-105426 /Users/ben/.config/omniwm/settings.toml; open -ga OmniWM'
```

For a full rollback to the first snapshot taken before this migration pass,
use `settings.toml.pre-codex-omniwm-hiro-20260601-104452` instead of the
supported-test backup.

## Continuation Recheck 2026-06-01

Current SSH recheck still confirms the migrated config is loaded:

- `hostname`: `omens`
- `sw_vers`: macOS `26.4.1` build `25E253`
- `/Users/ben/.config/omniwm/settings.toml` parses with `tomllib`
- `defaultLayoutType = "dwindle"`
- `ipcEnabled = true`
- `omniwmctl ping`: `pong`
- `omniwmctl version`: `0.4.9.6 (protocol 5)`
- live workspace query: 10 workspaces, all Dwindle
- active workspace restored to `1`

The remaining live window verification is blocked by the macOS console lock,
not by repo scope or missing SSH access:

- `ioreg -n Root -d1` reports `IOConsoleLocked = Yes`
- `IOConsoleUsers` reports `CGSessionScreenIsLocked = Yes`
- `omniwmctl query focused-window-decision` still reports
  `com.apple.loginwindow` with `attribute-fetch-failed`
- `omniwmctl query windows` still returns an empty managed-window list

After unlocking the Mac mini GUI, run this focused verification pass:

```sh
ssh mini 'omniwmctl ping && omniwmctl query workspaces --format json --fields number,raw-name,layout && omniwmctl query windows --format json --fields id,app,title,workspace,mode,is-focused,is-visible,is-scratchpad'
```

Then test manually with real windows:

- `Cmd+Return` quake terminal
- `Cmd+Space` command palette
- `Cmd+1..9` workspace switching
- `Cmd+Ctrl+1..9` moving the focused window
- `Cmd+Tab` and `Cmd+Shift+Tab` workspace cycling
- `Cmd+Arrow` focus
- `Cmd+Shift+Arrow` move/swap
- `Cmd+Option+Arrow` resize
- `Cmd+F` fullscreen
- `Cmd+T` floating
- `Cmd+Option+S` assign scratchpad
- `Cmd+S` toggle scratchpad
- `Cmd+K` menu/key-reference equivalent
