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

Use a real logout. Do not use Lock or Switch User for the first Hyprland attempt. Plasma Login Manager can reuse an existing Plasma session for the same user, which looks like the Hyprland selection was ignored.

Automated real-login testing is intentionally not enabled. Do not restart Plasma Login Manager, force logout, or enable autologin unless you are at the keyboard and have a confirmed way back into Plasma. The helpers can validate config, run a nested smoke test with explicit `--run`, and prepare rollback paths, but they do not type the login password or recover an unattended greeter.

Before logging out, this command shows the current desktop, the session remembered by the greeter, and whether Plasma Login Manager is likely to reuse the still-running Plasma session:

```bash
hypr-login-status
```

Before a real retry, this command runs the whole safe preflight: refreshes the Plasma/user session environment, keeps Plasma as the remembered default, resets first-login notice state, runs static validation, and prints the retry keys:

```bash
hypr-preflight
```

To test the actual Hyprland config from inside Plasma without logging out, run:

```bash
hypr-smoke-test --run
```

It starts a nested Hyprland compositor, checks Hyprland IPC, launches Ghostty, runs `hypr-doctor` against that nested compositor, writes `~/hyprland-first-login/nested-smoke-*.report`, then exits Hyprland and refreshes the Plasma session environment. This is not a replacement for real login proof, but it catches most config/autostart/terminal failures before you leave KDE.

Keep Plasma as the remembered default so Hyprland is only entered when you explicitly choose it:

```bash
hypr-login-default plasma
```

If Plasma's logout button does nothing after a desktop-switching test, refresh the user service and DBus activation environment, then try logout again:

```bash
hypr-refresh-session-env
```

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
Ctrl + Alt + T           open Ghostty fallback
Ctrl + Alt + Return      open Ghostty fallback
Alt + Return             open Ghostty fallback
F12                      open Ghostty fallback
Cmd + Space              open app launcher
Cmd + Alt + Space        open Hyprland menu
Ctrl + Alt + Space       open Hyprland menu fallback
Cmd + K                  show keybindings
Ctrl + Alt + K           show keybindings fallback
Cmd + W                  close window
Cmd + Shift + Q          exit Hyprland session
Ctrl + Alt + Q           exit Hyprland fallback
Ctrl + Alt + Backspace   exit Hyprland fallback
Cmd + Shift + /          run Hyprland doctor
Cmd + Escape             power/logout menu
Cmd + L                  lock
```

The normal Hyprland session opens Ghostty automatically until `hypr-proof` has recorded a real-login proof marker. The terminal prints the recovery card and leaves a shell open so the first login is not dependent on remembering a shortcut.

From a terminal, `hypr-help` reopens this quickstart, `hypr-recovery-card` prints the rollback card, `hypr-login-status` shows login-manager state, `hypr-logs` shows the latest attempt logs, `hypr-validate` reruns the static safety checks, `hypr-doctor` checks the live Hyprland session, and `hypr-proof` records proof after a real Hyprland login.

The menu at `Cmd + Alt + Space` is the Fedora-safe equivalent of Omarchy's menu workflow. It opens apps, keybindings, quickstart, recovery card, login status, logs, preflight, doctor, proof, validator, clipboard history, audio/network tools, the Hyprland config, and the power menu.

## First Login Check

After the first normal Hyprland login:

1. A one-time `Hyprland ready` notification should appear after Mako starts.
2. Ghostty should open automatically with the recovery card and a shell.
3. A `Hyprland doctor passed` or `Hyprland doctor failed` notification should appear shortly after startup.
4. If Ghostty did not open, press `Ctrl + Alt + T`, `Ctrl + Alt + Return`, `Alt + Return`, or `F12`.
5. In Ghostty, run:

```bash
hypr-doctor
hypr-proof
```

The normal session autocheck also runs these after startup and retries a few times before reporting failure. `hypr-proof` writes a timestamped proof log and, only when the session is a real normal-config Hyprland login through Plasma Login Manager, updates:

```text
~/.local/state/hyprland/real-login-proof
```

Reports are written under:

```text
~/hyprland-first-login/
```

If the session is unstable, exit with `Ctrl + Alt + Q` or `Cmd + Shift + Q` and pick `Plasma` or `Plasma (Rollback Hyprland)` at the login screen.

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
~/.local/bin/hypr-proof
~/.local/bin/hypr-keybindings
~/.local/bin/hypr-menu
~/.local/bin/hypr-recovery-card
~/.local/bin/hypr-login-status
~/.local/bin/hypr-login-default
~/.local/bin/hypr-refresh-session-env
~/.local/bin/hypr-preflight
~/.local/bin/hypr-smoke-test
~/.local/bin/hypr-logs
~/.local/bin/hypr-first-login-notice
~/.local/bin/hypr-first-login-terminal
~/.local/bin/hypr-session-autocheck
```

## Fast fallback

If Hyprland is annoying but the login screen works:

1. `Ctrl + Alt + Q` or `Cmd + Shift + Q` to exit Hyprland.
2. Pick the `Plasma` session at login.

If normal Hyprland fails but you still want a minimal compositor test:

1. Pick `Hyprland (Safe)` at login.
2. Use `Ctrl + Alt + T`, `Ctrl + Alt + Return`, `Alt + Return`, `F12`, or `Cmd + Return` for Ghostty.
3. Use `Ctrl + Alt + Space` or `Cmd + Space` for the app launcher/menu.
4. Use `Ctrl + Alt + Q` or `Cmd + Shift + Q` to exit.

If normal Hyprland reaches a black screen or returns to login, try `Hyprland (Logged)` once, then inspect:

```bash
hypr-logs
```

```text
~/hyprland-first-login/session-*.log
~/hyprland-first-login/nested-smoke-*.report
~/hyprland-first-login/autocheck-*.log
~/hyprland-first-login/proof-*.log
~/hyprland-first-login/doctor-*.log
~/.local/state/hyprland/real-login-proof
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
