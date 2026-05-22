# Hyprland quickstart

This machine keeps KDE Plasma as the fallback. Hyprland is an additional login session, not a replacement.

Login-screen session choices:

```text
Plasma                       normal KDE fallback
Plasma (Rollback Hyprland)   disable ~/.config/hypr, then start KDE
Hyprland                     normal configured Hyprland
Hyprland (Logged)            normal Hyprland, with startup logs
Hyprland (Safe)              minimal Hyprland, no Waybar/Mako/Hypridle/autostart
```

## Start Hyprland

1. Save work in Plasma.
2. Log out.
3. In the Plasma login screen, choose the `Hyprland` session.
4. Log in as `ben`.

If you want to test the compositor before trusting the normal config, choose `Hyprland (Safe)` first. It uses a root-owned config at:

```text
/usr/local/share/asahi-hyprland/hyprland-safe.conf
```

It has the same core MacBook navigation bindings but skips all user autostart services.

If the normal session fails before you can open a terminal, use `Hyprland (Logged)` on the next try. It runs the normal config and writes startup output under:

```text
~/hyprland-first-login/session-*.log
```

First keys to remember:

```text
Cmd + Return             open Ghostty
Cmd + Space              open app launcher
Cmd + Alt + Space        open Hyprland menu
Cmd + K                  show keybindings
Cmd + W                  close window
Cmd + Shift + Q          exit Hyprland session
Cmd + Shift + /          run Hyprland doctor
Cmd + Escape             power/logout menu
Cmd + L                  lock
```

From a terminal, `hypr-help` reopens this quickstart, `hypr-recovery-card` prints the rollback card, `hypr-validate` reruns the static safety checks, and `hypr-doctor` checks the live Hyprland session.

The menu at `Cmd + Alt + Space` is the Fedora-safe equivalent of Omarchy's menu workflow. It opens apps, keybindings, quickstart, recovery card, doctor, validator, clipboard history, audio/network tools, the Hyprland config, and the power menu.

## First Login Check

After the first normal Hyprland login:

1. A one-time `Hyprland ready` notification should appear after Mako starts.
2. A `Hyprland doctor passed` or `Hyprland doctor failed` notification should appear shortly after startup.
3. Press `Cmd + Return`.
4. Run:

```bash
hypr-doctor
```

It writes a timestamped report under:

```text
~/hyprland-first-login/
```

If the session is unstable, exit with `Cmd + Shift + Q` and pick `Plasma` or `Plasma (Rollback Hyprland)` at the login screen.

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
~/.config/hypr/xdph.conf
~/.config/waybar/config.jsonc
~/.config/waybar/style.css
~/.config/fuzzel/fuzzel.ini
~/.config/mako/config
~/.config/swappy/config
~/.local/bin/hypr-power-menu
~/.local/bin/hypr-screenshot
~/.local/bin/hypr-clipboard-menu
~/.local/bin/hypr-doctor
~/.local/bin/hypr-keybindings
~/.local/bin/hypr-menu
~/.local/bin/hypr-recovery-card
~/.local/bin/hypr-first-login-notice
~/.local/bin/hypr-session-autocheck
```

## Fast fallback

If Hyprland is annoying but the login screen works:

1. `Cmd + Shift + Q` to exit Hyprland.
2. Pick the `Plasma` session at login.

If normal Hyprland fails but you still want a minimal compositor test:

1. Pick `Hyprland (Safe)` at login.
2. Use `Cmd + Return` for Ghostty or `Cmd + Space` for the app launcher.
3. Use `Cmd + Shift + Q` to exit.

If normal Hyprland reaches a black screen or returns to login, try `Hyprland (Logged)` once, then inspect:

```text
~/hyprland-first-login/session-*.log
~/hyprland-first-login/autocheck-*.log
~/hyprland-first-login/doctor-*.log
```

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

To re-enable the Git-tracked Hyprland config later from Plasma:

```bash
hypr-enable
```

To reinstall or verify the root-owned login/recovery files from this repo:

```bash
cd ~/dotfiles
scripts/install-hyprland-system-files.sh --install
scripts/install-hyprland-system-files.sh --check
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

Configured-state checkpoint before the first real Hyprland login:

```text
/home/ben/system-backups/pre-first-hyprland-login-20260522-093108
/.snapshots/pre-first-hyprland-login-20260522-093108/root
/.snapshots/pre-first-hyprland-login-20260522-093108/home
```

Use the Btrfs snapshots for a full rollback only from a rescue environment or with a deliberate rollback plan. Plasma fallback should be the first recovery path.

## Portals

Fedora keeps KDE and Hyprland portals installed side by side. Hyprland sessions use:

```text
/usr/share/xdg-desktop-portal/hyprland-portals.conf
~/.config/hypr/xdph.conf
```

The tracked XDPH config points screen-share picking at Fedora's `hyprland-share-picker`.
