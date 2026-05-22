# Hyprland migration notes - Fedora Asahi

Date: 2026-05-22
Host: `misery`
OS: Fedora Linux Asahi Remix 44 KDE Plasma Desktop Edition
Arch: `aarch64`

This note records the pre-Hyprland state and intended MacBook-oriented setup before changing the desktop session. Keep KDE/Plasma installed until Hyprland has been tested from the login screen.

## Rollback anchors

Btrfs snapshots were created before Hyprland installation or config changes:

- Backup ID: `pre-hyprland-20260522-083751`
- Device: `/dev/nvme0n1p6`
- Root snapshot: `/.snapshots/pre-hyprland-20260522-083751/root`
- Home snapshot: `/.snapshots/pre-hyprland-20260522-083751/home`
- Inspectable backup bundle: `/home/ben/system-backups/pre-hyprland-20260522-083751`

The backup bundle contains package manifests, `/etc`, boot config, KDE config, dotfiles, and user config copies. Size when created: about 5.1 GB.

## Initial safety state

- Plasma is still installed and remains the working desktop.
- `plasmalogin.service` is the active display manager.
- Hyprland packages were not installed yet when this note was first committed.
- The Hyprland COPR repo file exists but is disabled by default: `/etc/yum.repos.d/_copr_sdegler-hyprland.repo`.
- The repo is only meant to be enabled for explicit Hyprland package installation.

Do not run Omarchy's Arch installer on Fedora Asahi. Use Omarchy as a configuration reference only.

## Command key fix

Goal: Apple Command acts as Linux Super/Win, Option acts as Alt, Control stays Control.

Persistent kernel module config:

```conf
# /etc/modprobe.d/99-apple-command-super.conf
options hid_apple swap_opt_cmd=0 swap_ctrl_cmd=0
```

Current XKB/KDE intent:

```text
layout: us
model: applealu_ansi
options: ctrl:nocaps,terminate:ctrl_alt_bksp
```

KDE user config also sets:

```text
~/.config/kxkbrc
LayoutList=us
Model=applealu_ansi
Options=ctrl:nocaps
```

## Fonts

Use Apple fonts already installed locally:

- UI font: `SF Pro` / `SF Pro Text`
- Display font: `SF Pro Display`
- Mono font: `SF Mono`

Font paths are under `/usr/local/share/fonts/apple/`.

## Proposed Hyprland bindings

Modifier mapping:

```text
Command = Super
Option = Alt
Control = Control
Caps Lock = Control
```

Core commands:

```text
Cmd + Space              app launcher
Cmd + Return             Ghostty terminal
Cmd + W                  close active window
Cmd + Shift + Q          exit Hyprland session
Cmd + F                  fullscreen
Cmd + T                  toggle floating/tiling
Cmd + J                  toggle split direction
```

Workspaces:

```text
Cmd + 1..9 / 0           switch workspace 1..10
Cmd + Shift + 1..9 / 0   move window to workspace 1..10
Cmd + Tab                next workspace
Cmd + Shift + Tab        previous workspace
```

Window movement:

```text
Cmd + Arrow              focus window in that direction
Cmd + Shift + Arrow      move/swap window in that direction
Cmd + Alt + Arrow        resize window
Cmd + Left Mouse         drag window
Cmd + Right Mouse        resize window
```

Mac-style editing helpers:

```text
Cmd + C                  copy via Ctrl+C
Cmd + V                  paste via Ctrl+V
Cmd + X                  cut via Ctrl+X
Cmd + A                  select all via Ctrl+A
Cmd + Z                  undo via Ctrl+Z
Cmd + Shift + C          terminal copy fallback
Cmd + Shift + V          terminal paste fallback
```

Utilities:

```text
Cmd + Shift + 3          full screenshot
Cmd + Shift + 4          region screenshot
Cmd + Shift + 5          screenshot region and open editor
Volume/Brightness/Media  MacBook function/media keys
Cmd + L                  lock screen
Cmd + Escape             power/logout menu
```

## Intended install direction

Install Hyprland as an additional login session, not as a Plasma replacement:

- `hyprland`
- `hyprpaper`
- `hypridle`
- `hyprlock`
- `hyprpolkitagent`
- `xdg-desktop-portal-hyprland`
- `waybar`
- `fuzzel` or `wofi`
- `mako`
- `grim`
- `slurp`
- `swappy`
- `brightnessctl`
- `playerctl`
- `cliphist`
- `network-manager-applet`
- `pavucontrol`

Keep Plasma and `xdg-desktop-portal-kde` installed for fallback.

## Installation update

Hyprland was later installed as an additional login session using the disabled-by-default COPR only for the explicit transaction. Plasma was not removed.

Installed session:

```text
/usr/share/wayland-sessions/hyprland.desktop
```

Config files are tracked under this dotfiles repo and symlinked into `~/.config`.

## Validation update

Static validation passed after installation:

```text
Hyprland --verify-config -c ~/.config/hypr/hyprland.conf
fuzzel --check-config --config ~/.config/fuzzel/fuzzel.ini
jq empty ~/.config/waybar/config.jsonc
bash -n ~/.local/bin/hypr-power-menu ~/.local/bin/hypr-screenshot ~/.local/bin/hypr-clipboard-menu
```

The login wrapper was also smoke-tested from the active Plasma Wayland session with a throwaway home/config and autostart disabled:

```text
timeout --kill-after=3s 8s env HOME=/tmp/codex-hypr-test-home \
  XDG_CONFIG_HOME=/tmp/codex-hypr-test-home/.config \
  /usr/bin/start-hyprland
```

Result: Hyprland started and ran until the timeout killed it. The visible warnings were XKB/Xwayland warnings, not Hyprland config failures.

An additional nested smoke test used the full autostart config. It reached Hyprland startup with the expected nested-session caveats:

- `hyprpolkitagent` could not register because Plasma already had an auth agent in the current session.
- Killing `/usr/bin/start-hyprland` via `timeout` produced a small coredump from the wrapper shutdown path.
- No autostart child processes were left running afterward.

Remaining manual proof: log out of Plasma, choose the `Hyprland` session, and confirm the real user session starts with autostart enabled.

## Graphical rollback session

A dedicated rollback session was added to the login screen:

```text
/usr/share/wayland-sessions/plasma-rollback-hyprland.desktop
Name=Plasma (Rollback Hyprland)
```

It runs:

```text
/usr/local/bin/asahi-hyprland-rollback-plasma
```

That wrapper calls `/usr/local/bin/asahi-hyprland-disable-config`, which moves `~/.config/hypr` to a timestamped `~/.config/hypr.disabled-*` directory, writes a log to `~/hyprland-rollback/`, then starts Plasma using Fedora Asahi's normal Plasma Wayland command.

TTY/manual rollback command:

```bash
hypr-rollback
```

Re-enable command after rollback:

```bash
hypr-enable
```

## Safe Hyprland session

A minimal Hyprland recovery session was added to the login screen:

```text
/usr/share/wayland-sessions/hyprland-safe.desktop
Name=Hyprland (Safe)
```

It runs Fedora's normal Hyprland watchdog wrapper with a separate root-owned config:

```text
/usr/local/bin/asahi-hyprland-safe
/usr/local/share/asahi-hyprland/hyprland-safe.conf
```

That config keeps the same MacBook-oriented core bindings but intentionally has no `exec-once` autostart entries. It does not start Waybar, Mako, Hypridle, clipboard watchers, wallpaper, network tray, or the polkit agent. Use it from the login screen when the normal Hyprland session is suspect but you still want a minimal compositor session before falling back to Plasma.

Nested smoke test:

```text
timeout --kill-after=3s 8s env HOME=/tmp/codex-hypr-safe-test-home \
  XDG_CONFIG_HOME=/tmp/codex-hypr-safe-test-home/.config \
  /usr/local/bin/asahi-hyprland-safe
```

Result: Hyprland started with `/usr/local/share/asahi-hyprland/hyprland-safe.conf` and ran until the timeout killed it. The visible warnings were XKB/Xwayland warnings. As with the normal wrapper smoke test, killing `start-hyprland` by timeout produced a wrapper coredump during shutdown; no `Hyprland` or `start-hyprland` processes remained afterward.

## Logged Hyprland session

A logged normal Hyprland session was added to the login screen:

```text
/usr/share/wayland-sessions/hyprland-logged.desktop
Name=Hyprland (Logged)
```

It runs:

```text
/usr/local/bin/asahi-hyprland-logged
```

That wrapper preserves the normal Fedora `/usr/bin/start-hyprland` launch path and writes startup output to:

```text
~/hyprland-first-login/session-*.log
```

Use it when the normal `Hyprland` login fails before a terminal can be opened. It is diagnostic only; it does not change the normal Hyprland config.

Nested smoke test used a throwaway home with the safe config symlinked as `hyprland.conf`:

```text
timeout --kill-after=3s 8s env HOME=/tmp/codex-hypr-logged-test-home \
  XDG_CONFIG_HOME=/tmp/codex-hypr-logged-test-home/.config \
  /usr/local/bin/asahi-hyprland-logged
```

Result: the wrapper wrote `session-*.log`, `start-hyprland` reached Hyprland startup, and no `Hyprland` or `start-hyprland` processes remained afterward. Killing the nested wrapper by timeout produced the same shutdown-path coredump caveat as the other nested smoke tests.

## First-login doctor

A post-login session checker was added:

```bash
hypr-doctor
```

It is also bound in the normal Hyprland config:

```text
Cmd + Shift + /
```

The doctor checks the active session environment, Hyprland IPC, monitor/workspace visibility, core autostart processes, portal-related user services, and whether the live Hyprland config still parses. Logs are written under:

```text
~/hyprland-first-login/
```

When run from the current KDE session, it correctly fails the Hyprland session checks while confirming the command/config pieces are present. The meaningful final run is after logging into the real Hyprland session.
