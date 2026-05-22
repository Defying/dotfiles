# Hyprland quickstart

This machine keeps KDE Plasma as the fallback. Hyprland is an additional login session, not a replacement.

## Start Hyprland

1. Save work in Plasma.
2. Log out.
3. In the Plasma login screen, choose the `Hyprland` session.
4. Log in as `ben`.

First keys to remember:

```text
Cmd + Return             open Ghostty
Cmd + Space              open app launcher
Cmd + W                  close window
Cmd + Shift + Q          exit Hyprland session
Cmd + Escape             power/logout menu
Cmd + L                  lock
```

From a terminal, `hypr-help` reopens this quickstart and `hypr-validate` reruns the static safety checks.

## Navigation

```text
Cmd + 1..9 / 0           switch workspace 1..10
Cmd + Shift + 1..9 / 0   move window to workspace 1..10
Cmd + Tab                next workspace
Cmd + Shift + Tab        previous workspace
Cmd + Arrow              focus a nearby window
Cmd + Shift + Arrow      swap window position
Cmd + Alt + Arrow        resize active window
Cmd + Left Mouse         drag window
Cmd + Right Mouse        resize window
```

## Mac-style editing

```text
Cmd + C                  copy
Cmd + V                  paste
Cmd + X                  cut
Cmd + A                  select all
Cmd + Z                  undo
Cmd + Shift + C/V        terminal copy/paste fallback
```

## Screenshots

```text
Cmd + Shift + 3          full screenshot
Cmd + Shift + 4          region screenshot
Cmd + Shift + 5          region screenshot, then edit
```

Screenshots go to `~/Pictures/Screenshots` and are copied to the clipboard.

## Config locations

Live config files are symlinked from this repo:

```text
~/.config/hypr/hyprland.conf
~/.config/hypr/hyprpaper.conf
~/.config/hypr/hypridle.conf
~/.config/hypr/hyprlock.conf
~/.config/waybar/config.jsonc
~/.config/waybar/style.css
~/.config/fuzzel/fuzzel.ini
~/.config/mako/config
~/.config/swappy/config
~/.local/bin/hypr-power-menu
~/.local/bin/hypr-screenshot
~/.local/bin/hypr-clipboard-menu
```

## Fast fallback

If Hyprland is annoying but the login screen works:

1. `Cmd + Shift + Q` to exit Hyprland.
2. Pick the `Plasma` session at login.

## Login-Screen Rollback

If Hyprland is broken and you want the login screen to disable it for you:

1. At the login screen, open the session chooser.
2. Pick `Plasma (Rollback Hyprland)`.
3. Log in as `ben`.

That session moves `~/.config/hypr` to a timestamped disabled directory like:

```text
~/.config/hypr.disabled-20260522-091500
```

Then it starts Plasma. It writes a log under:

```text
~/hyprland-rollback/
```

If Hyprland will not start:

1. Switch to a TTY with `Ctrl + Alt + F3`.
2. Log in.
3. Run the rollback command:

```bash
hypr-rollback
```

4. Return to the login screen with `Ctrl + Alt + F1` or reboot.
5. Choose `Plasma`.

## Backup anchors

Pre-Hyprland backup:

```text
/home/ben/system-backups/pre-hyprland-20260522-083751
/.snapshots/pre-hyprland-20260522-083751/root
/.snapshots/pre-hyprland-20260522-083751/home
```

Use the Btrfs snapshots for a full rollback only from a rescue environment or with a deliberate rollback plan. Plasma fallback should be the first recovery path.
