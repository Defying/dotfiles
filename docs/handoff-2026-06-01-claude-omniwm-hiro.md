# Claude Handoff: OmniWM/Hiro Windowing

Date: 2026-06-01
Repo: `/home/ben/dotfiles`

## Status

The macOS OmniWM/Hiro migration was applied on SSH host `mini` (`omens`).
Scope stayed limited to window-manager feel. No Linux Waybar, Mako, panel,
OSD, wallpaper, clipboard, or power-menu stack was ported.

Remote file changed:

- `/Users/ben/.config/omniwm/settings.toml`

Current verified state:

- OmniWM `0.4.9.6 (protocol 5)` launches.
- `omniwmctl ping` returns `pong`.
- `defaultLayoutType = "dwindle"`.
- `ipcEnabled = true`.
- Workspaces `1..10` exist and query as Dwindle.
- `Cmd+1..9`, `Cmd+Ctrl+1..9`, `Cmd+Tab`, `Cmd+Shift+Tab`,
  `Cmd+Arrow`, `Cmd+Shift+Arrow`, `Cmd+Option+Arrow`, `Cmd+F`, `Cmd+T`,
  `Cmd+S`, `Cmd+Option+S`, `Cmd+K`, `Cmd+Space`, and `Cmd+Return` are mapped
  to OmniWM built-ins.

Known limits:

- `Cmd+0` and `Cmd+Ctrl+0` are not mapped. OmniWM rejected added
  `switchWorkspace.9` and `moveToWorkspace.9` IDs and reverted the settings
  file. Workspace `10` itself exists, and `omniwmctl command switch-workspace
  10` works.
- There is no persisted generic "launch Ghostty" hotkey in OmniWM settings or
  CLI. `Cmd+Return` is mapped to `toggleQuakeTerminal` as the closest built-in
  terminal workflow.
- Final window-level testing needs the macOS GUI unlocked. During verification
  the active focus was `loginwindow`, `focused-window-decision` reported
  `attribute-fetch-failed`, and `query windows` returned no managed windows.
- A later recheck confirmed the same blocker from the system side:
  `IOConsoleLocked = Yes` and `CGSessionScreenIsLocked = Yes`.

Rollback:

```sh
ssh mini 'pkill -x OmniWM >/dev/null 2>&1 || true; cp -p /Users/ben/.config/omniwm/settings.toml.pre-codex-omniwm-hiro-supported-test-20260601-105426 /Users/ben/.config/omniwm/settings.toml; open -ga OmniWM'
```

Full initial snapshot:

```sh
/Users/ben/.config/omniwm/settings.toml.pre-codex-omniwm-hiro-20260601-104452
```

## Goal Prompt

Continue the OmniWM/Hiro windowing migration on `mini` only after the macOS GUI
session is unlocked. Keep scope to bare windowing feel: do not port Waybar,
Mako, panels, OSDs, wallpaper, clipboard UI, or power menus. Verify the existing
OmniWM config live with real windows: `Cmd+Return` quake terminal, workspace
switching, moving windows to workspaces, arrow focus, arrow move/swap, resize,
fullscreen, floating, scratchpad assign/toggle, command palette, and menu/help.
If a key fails, prefer OmniWM built-ins and update
`docs/omniwm-hiro-windowing-migration.md` with exact evidence, final key map,
unsupported behavior, and rollback notes. Do not add custom glue unless a
built-in cannot cover an essential behavior.
