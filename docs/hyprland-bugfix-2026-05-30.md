# Hyprland Bugfix Report - 2026-05-30

## Objective

Find and fix the unknown Hyprland setup bug from live evidence.

## Findings

### Fixed: `hypr-proof` was checking the wrong wallpaper provider

The current Hyprland session starts `swaybg` for the static wallpaper. This is
intentional because `hyprpaper` is not the active provider on this Fedora Asahi
setup. `hypr-doctor` already accepted `swaybg` or `mpvpaper`, but `hypr-proof`
still hard-failed unless `hyprpaper` was running.

Evidence before the fix:

```text
hypr-proof: FAIL process not found: hyprpaper
hypr-doctor: OK wallpaper provider running: swaybg
```

Patch:

- `home/.local/bin/hypr-proof` now accepts `swaybg` for the normal static
  wallpaper and `mpvpaper` when aerial owns the wallpaper.

### Fixed: global full-screen shader was still active

The live compositor had `decoration:screen_shader` set to
`config/hypr/shaders/rounded-corners.frag`. That shader runs over the full
output and hard-codes the internal `2560x1600` panel size. Window rounding is
already handled natively by Hyprland with `rounding = 28`, so the global shader
was unnecessary and fragile.

The system journal also had Asahi GPU timeout entries at `2026-05-30 09:55:59`.
I did not reproduce the timeout on a later reload, but removing an unnecessary
global compositor shader is the conservative fix for this machine.

Patch:

- `config/hypr/hyprland.conf` no longer sets `decoration:screen_shader`.
- Live reload applied the change; `hyprctl` now reports the screen shader unset.

## Current Verification

Commands run after the fixes:

```text
bash -n home/.local/bin/hypr-proof home/.local/bin/hypr-doctor
Hyprland --verify-config -c /home/ben/.config/hypr/hyprland.conf
hyprctl reload
hyprctl getoption decoration:screen_shader -j
/home/ben/.local/bin/hypr-proof
/home/ben/.local/bin/hypr-doctor
systemctl --user --failed --no-pager --plain
journalctl -b --no-pager --since '2026-05-30 14:48:49' | grep -Ei 'asahi|gpu|timeout|fault'
```

Results:

```text
Hyprland config ok
decoration:screen_shader set=false
hypr-proof failures=0
hypr-doctor failures=0 warnings=0
systemctl --user failed units=0
no new GPU timeout/fault logs since the shader was disabled
```

`hypr-validate` still fails because the dotfiles worktree is dirty. That is not
a live Hyprland failure; it is the validator's repo-clean gate. Current unrelated
dirty files include `README.md`, `config/ghostty/config`, `zsh/.zshrc`, and the
new agent handoff helper files.

## Changed Files

```text
config/hypr/hyprland.conf
home/.local/bin/hypr-proof
```

## Handoff Prompt

```text
/goal Read /home/ben/dotfiles/docs/hyprland-bugfix-2026-05-30.md in full before doing anything. Verify the current live Hyprland state first, then continue only if there are new runtime failures beyond the fixed hypr-proof wallpaper-provider check and disabled global screen_shader. Preserve unrelated dirty work.
```
