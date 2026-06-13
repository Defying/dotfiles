# Wayland Emoji Picker Handoff - 2026-05-30

## Goal

Build a macOS-style emoji picker for this Fedora Asahi Hyprland Wayland setup. It should open when Ben presses the Globe/Fn key if that key is exposed to Hyprland; if it is not exposed as a bindable key, document the evidence and use a fallback binding such as `Super+Period`.

This is a Hyprland desktop feature, so new code should be Rust-based by default per `AGENTS.md`.

## Current Local Evidence

- Repo: `/home/ben/dotfiles`.
- Session/compositor: Hyprland on Wayland.
- Keyboard from `hyprctl -j devices`:
  - main keyboard: `apple-spi-keyboard`
  - active keymap: `English (US)`
  - XKB model in `config/hypr/hyprland.conf`: `applealu_ansi`
- Existing modifier style:
  - `$mainMod = SUPER`
  - launcher: `bind = $mainMod, SPACE, exec, $menu`
  - Mac-like shortcuts already exist around `Super+C`, `Super+V`, `Super+X`, etc.
- Installed useful tools:
  - `wtype`: `/usr/bin/wtype`
  - `ydotool`: `/usr/bin/ydotool`
  - `wl-copy`: `/usr/bin/wl-copy`
  - `wl-paste`: `/usr/bin/wl-paste`
  - `fuzzel`: `/usr/bin/fuzzel`
  - `libinput`: `/usr/bin/libinput`
  - `hyprctl`: `/usr/bin/hyprctl`
  - `cargo`: `/usr/bin/cargo`
  - `rustc`: `/usr/bin/rustc`
- Not installed during this check:
  - `wev`
  - `evtest`

## Hard Unknown

Do not guess the Globe/Fn key binding.

The next implementation pass needs to capture what, if anything, the key emits on this machine. Use live evidence before editing the permanent Hyprland config.

Suggested capture path:

```sh
hyprctl -j devices | jq '.keyboards[] | {name,main,active_keymap}'
sudo libinput debug-events --show-keycodes
```

Press the Globe key while `libinput debug-events` is running. If it emits a normal key code or keysym that Hyprland can bind, use that exact key name. If it emits only an internal Fn event, no event, or something Hyprland cannot bind directly, write that down and use the fallback binding.

Fallback binding recommendation:

```conf
bind = $mainMod, PERIOD, exec, /home/ben/.local/bin/hypr-emoji-picker
```

## Target UX

The picker should feel close to the macOS emoji popup:

- Small floating window, not a full launcher.
- Opens quickly over the focused monitor.
- Search field focused immediately.
- Recent emojis first.
- Categories such as Smileys, People, Nature, Food, Activity, Travel, Objects, Symbols, Flags.
- Arrow keys navigate the grid/list.
- Typing filters by Unicode name and aliases.
- `Enter` inserts the highlighted emoji.
- Clicking an emoji inserts it.
- `Esc` closes without changing focus more than necessary.
- After insertion, the focused app receives the emoji and the picker closes.

Wayland generally does not expose text-caret geometry globally, so start by centering the picker on the focused monitor. Only attempt caret-relative placement if there is a reliable app/toolkit-specific route.

## Implementation Direction

Preferred durable implementation:

1. Add a dedicated Rust binary, probably as a new crate such as `hypr-emoji-picker/`.
2. Build the UI with GTK4/libadwaita or another Rust-friendly native toolkit that behaves well on Wayland.
3. Use layer-shell or Hyprland window rules for popup-like placement if normal GTK window placement is not enough.
4. Store recents under a stable user-state path, for example:

```text
~/.local/state/hypr-emoji-picker/recents.json
```

5. Source emoji data from a real Unicode dataset or a vendored generated table, not from ad hoc hard-coded search strings. Keep the generated artifact small enough to review, or document its generator.

Pragmatic first slice if native UI dependencies slow the work:

- Build a Rust CLI that feeds emoji rows into `fuzzel --dmenu`, inserts the selected emoji, and persists recents.
- Keep that as a fallback path, not the final macOS-like UI, unless it proves sufficient in daily use.

## Text Insertion Strategy

Test these in real apps before choosing the default:

1. Direct typing:

```sh
wtype "emoji_goes_here"
```

2. Clipboard-paste path:

```sh
printf '%s' "emoji_goes_here" | wl-copy
wtype -M ctrl v -m ctrl
```

or Hyprland:

```sh
hyprctl dispatch sendshortcut CTRL,V,activewindow
```

The clipboard-paste path is often more reliable for multi-byte Unicode text. If using it, preserve and restore the previous clipboard where practical:

- Save the current text clipboard with `wl-paste --no-newline`.
- Put the selected emoji on the clipboard.
- Send paste.
- Restore the old clipboard after a short delay.

Be careful not to corrupt non-text clipboard contents. If restoration support is uncertain, document the limitation instead of pretending it is lossless.

Avoid making `ydotool` the default unless there is already a working daemon/permission setup. It is installed, but it is more invasive than `wtype`/Hyprland dispatch.

## Hyprland Config Plan

Before editing:

```sh
git status --short
```

Preserve unrelated dirty work. Add the permanent bind only after the key capture step.

If Globe is bindable, add the exact bind discovered from live evidence to `config/hypr/hyprland.conf`.

If Globe is not bindable, add:

```conf
bind = $mainMod, PERIOD, exec, /home/ben/.local/bin/hypr-emoji-picker
```

Then verify:

```sh
Hyprland --verify-config
hyprctl reload
hyprctl binds -j | jq '.[] | select(.dispatcher=="exec" and (.arg|contains("hypr-emoji-picker")))'
```

## Verification Checklist

- `cargo build --release` succeeds for the new Rust crate.
- Binary is installed to `/home/ben/.local/bin/hypr-emoji-picker`.
- Hyprland config verifies cleanly.
- Hyprland bind is visible in `hyprctl binds -j`.
- Picker opens from the chosen keybinding.
- Search works.
- Arrow navigation works.
- `Enter` inserts the selected emoji into:
  - Ghostty
  - a browser or Electron text field
  - a GTK text field
- `Esc` closes without insertion.
- Clipboard behavior is tested and documented.
- Recents update after successful insertion and survive app restart.
