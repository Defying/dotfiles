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

A second read-only checkpoint was created after Hyprland was installed/configured and before any real login-screen Hyprland test:

- Backup ID: `pre-first-hyprland-login-20260522-093108`
- Device: `/dev/nvme0n1p6`
- Root snapshot: `/.snapshots/pre-first-hyprland-login-20260522-093108/root`
- Home snapshot: `/.snapshots/pre-first-hyprland-login-20260522-093108/home`
- Inspectable manifest bundle: `/home/ben/system-backups/pre-first-hyprland-login-20260522-093108`
- Dotfiles commit at creation: `011e6a1647ed5a47a5ad4740ffe0f9362f24fd9b`

The second bundle records package state, Btrfs subvolumes, session files, the Hyprland system-file check, dotfiles status/log, and a passing `hypr-validate` run.

## Initial safety state

- Plasma is still installed and remains the working desktop.
- `plasmalogin.service` is the active display manager.
- Hyprland packages were not installed yet when this note was first committed.
- The Hyprland COPR repo file exists but is disabled by default: `/etc/yum.repos.d/_copr_sdegler-hyprland.repo`.
- The repo is only meant to be enabled for explicit Hyprland package installation.

Do not run Omarchy's Arch installer on Fedora Asahi. Use Omarchy as a configuration reference only.

## Omarchy reference check

Current Omarchy upstream was inspected from `basecamp/omarchy` dev commit:

```text
b911a6f8ba51ac36b7bef9cd07b96207a2c59a18
```

Relevant upstream behavior:

- Hyprland defaults are currently organized as Lua modules under `config/hypr` and `default/hypr`.
- Omarchy exposes keybindings through `Super + K`.
- Omarchy exposes its main menu through `Super + Alt + Space`.
- Omarchy's current look-and-feel values match the already staged gaps, border gradient, shadow, blur, animation, dwindle, cursor, and XWayland choices.

Fedora Asahi keeps the validated classic Hyprland config instead of switching to Omarchy's Lua stack, because this machine is on Fedora packages and does not have Omarchy's Arch-specific helper commands. Fedora-safe equivalents were added:

```text
Cmd + K             hypr-keybindings
Cmd + Alt + Space   hypr-menu
```

Omarchy also configures XDPH screencopy behavior. Fedora Asahi now tracks the local equivalent at:

```text
~/.config/hypr/xdph.conf
```

It keeps `allow_token_by_default = true` and uses Fedora's installed `hyprland-share-picker` binary for screen-share source selection.

## Recovery card

A compact recovery reference command was added:

```bash
hypr-recovery-card
```

It prints the login-screen session choices, first-test order, emergency keybindings, TTY fallback, rollback/re-enable commands, log locations, backup bundles, and Btrfs snapshot anchors. It is also available from the `hypr-menu`.

## Login status helper

The Plasma Login Manager can reuse a still-running session for the same user. If the current Plasma session is only locked or switched away from, selecting Hyprland at the greeter can return to the existing Plasma session instead of starting a new Hyprland session.

The helper below prints the current session, the session remembered by Plasma Login Manager, and the effective `ReuseSession` setting:

```bash
hypr-login-status
```

The preflight helper runs the current safe-before-retry checklist from Plasma:

```bash
hypr-preflight
```

It refreshes the Plasma/user service environment, keeps Plasma as the remembered greeter default, clears first-login notice state so the next normal Hyprland attempt is noisy again, runs `hypr-validate`, prints `hypr-login-status`, and prints the fallback keys.

The nested smoke-test helper starts the real configured Hyprland under the current Plasma Wayland session:

```bash
hypr-smoke-test
```

It checks Hyprland IPC, launches Ghostty, runs `hypr-doctor` against the nested compositor, writes `~/hyprland-first-login/nested-smoke-*.report`, exits the nested compositor, and refreshes the Plasma session environment. Nested smoke sessions set `HYPR_NESTED_SMOKE=1` so the real-login autocheck does not write misleading proof logs. This is stronger than static validation but still not a replacement for real login-screen proof.

Use a real Plasma logout before the first Hyprland test:

```text
Leave -> Log Out
```

Do not use Lock or Switch User for the first test.

The remembered greeter session can be reset to Plasma with:

```bash
hypr-login-default plasma
```

That keeps Hyprland as an explicit login-screen choice rather than an accidental default.

If Plasma's logout button stops responding after a desktop-switching test, refresh the user service and DBus activation environment from the current session:

```bash
hypr-refresh-session-env
```

`hypr-login-status` reports the user service environment alongside the current session and warns when `XDG_CURRENT_DESKTOP` does not match.

## Log reader

The latest Hyprland attempt logs can be read with:

```bash
hypr-logs
```

It prints the newest logged-session startup log, session autocheck log, real-login proof log, doctor log, and rollback log. It is also available from the `hypr-menu`.

## Real-login proof helper

The final proof for this migration is a real normal-config Hyprland session started by Plasma Login Manager, not a nested smoke test from Plasma. The helper below checks for a Hyprland desktop identity, a Hyprland instance signature, Hyprland IPC, a running Hyprland process for the user, normal-config autostart processes, the dotfiles-linked config, and `loginctl Service=plasmalogin`:

```bash
hypr-proof
```

It writes proof logs under:

```text
~/hyprland-first-login/proof-*.log
```

When all checks pass, it also writes:

```text
~/.local/state/hyprland/real-login-proof
```

The normal Hyprland session autocheck runs this automatically after `hypr-doctor` passes. Running `hypr-proof` from Plasma, a nested smoke test, or the safe no-autostart session should fail, which prevents accidental proof from the wrong session.

## First-login notice

The normal Hyprland session autostarts a one-time notification after Mako starts:

```text
~/.local/bin/hypr-first-login-notice
```

It points at `Cmd + Alt + Space`, `Cmd + K`, `Cmd + Return`, `Cmd + Shift + /`, and `Cmd + Shift + Q`, plus non-Super fallbacks: `Ctrl + Alt + T`, `F12`, `Ctrl + Alt + Space`, and `Ctrl + Alt + Q`. The safe Hyprland session intentionally does not run this or any other user autostart helper.

## First-login terminal

The normal Hyprland session also autostarts Ghostty until `hypr-proof` records a real-login proof marker:

```text
~/.local/bin/hypr-first-login-terminal
```

It prints `hypr-recovery-card`, lists the non-Super fallback keys, and leaves a login shell open. This makes the first normal Hyprland login recoverable even if Command/Super is confusing or not yet trusted.

## Session autocheck

The normal Hyprland session also autostarts:

```text
~/.local/bin/hypr-session-autocheck
```

It waits for the session to settle, retries the `hypr-doctor`/`hypr-proof` sequence up to three times, writes `~/hyprland-first-login/autocheck-*.log`, and sends a pass/fail notification. The safe Hyprland session intentionally does not run this helper.

After `hypr-doctor` passes, it runs `hypr-proof` and sends a verified notification only if the real-login proof marker was written.

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
Ctrl + Alt + T           Ghostty terminal fallback
Ctrl + Alt + Return      Ghostty terminal fallback
Alt + Return             Ghostty terminal fallback
F12                      Ghostty terminal fallback
Ctrl + Alt + Space       menu fallback
Ctrl + Alt + K           keybindings fallback
Cmd + W                  close active window
Cmd + Shift + Q          exit Hyprland session
Ctrl + Alt + Q           exit Hyprland fallback
Ctrl + Alt + Backspace   exit Hyprland fallback
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

Remaining manual proof: fully log out of Plasma, choose the `Hyprland` session, and confirm the real user session starts with autostart enabled.

Observed 2026-05-22: selecting a Hyprland session from the greeter while the original Plasma session was still active returned to Plasma. `loginctl` showed the original KDE session from 01:05 was still active, and `/var/lib/plasmalogin/.local/state/plasma-login-greeterstaterc` recorded `LastLoggedInSession=hyprland-safe.desktop`. This was Plasma Login Manager session reuse, not proof that Hyprland failed to start.

Observed 2026-05-22 during the first real logged Hyprland attempt: Hyprland started from Plasma Login Manager, set `XDG_CURRENT_DESKTOP=Hyprland`, exposed Hyprland IPC, and launched the normal autostarts. The session was still not usable enough because the live login PATH did not include `rg`, causing `hypr-doctor` portal checks to fail, and the terminal/menu bindings were not discoverable under stress. The runtime helpers were changed to use `grep` instead of `rg`, Ghostty now opens automatically on first normal login, and non-Super fallbacks were added for terminal, menu, keybindings, and exit.

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

## System-file reinstall command

The root-owned login and recovery files are tracked under `system/`. They can be reinstalled from a fresh clone with:

```bash
cd ~/dotfiles
scripts/install-hyprland-system-files.sh --install
```

Verification:

```bash
scripts/install-hyprland-system-files.sh --check
```

The script installs only the tracked Hyprland recovery/session files. It does not modify Fedora's package-owned `hyprland.desktop` or `plasma.desktop`.

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
