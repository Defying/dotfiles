# Hyprland Setup Audit and Claude Code Handoff - 2026-05-30

## Scope

Audit of the live Fedora Asahi Hyprland setup after these changes:

- global dark theme and SF font defaults for Hyprland-launched apps
- Waybar AI menu/bar refresh fixes
- aerial wallpaper transitions and Hyprland hotkeys
- permanent project rule that newly built Hyprland tooling should be Rust-based

## Findings

### High - readiness gates are still blocking

`hypr-validate` and `hypr-readiness` both fail with seven blocking checks. The desktop itself is running, but the recovery/readiness contract is not green.

Evidence:

- `hypr-validate`: `Plasma login manager active` failed.
- `hypr-validate`: `dotfiles repo clean` and `dotfiles pushed` failed.
- `hypr-validate`: all four Btrfs snapshot marker checks failed.
- `hypr-readiness`: current login service is `greetd`; remembered greeter session is `hyprland.desktop`, not `plasma.desktop`.
- `hypr-readiness`: real login proof exists, but it was recorded for dotfiles commit `8e48d0a`; current commit is `f1d9c9b`.

Impact:

- The actual Hyprland session is usable, but the safety gates no longer prove the setup has the expected fallback path.
- Some failures may be stale policy rather than broken runtime: if `greetd` is now the intended login manager, the validator needs to be updated instead of blindly restoring the old Plasma-login assumption.

Recommended next step:

- Decide whether `greetd` is the intended login manager.
- Then either update the validation/readiness scripts to treat `greetd` plus the installed Plasma rollback sessions as valid, or restore the prior Plasma login-manager path.
- Recreate or intentionally retire the missing Btrfs snapshot checks; do not leave them failing silently.
- Commit and push the dotfiles once the current local changes are reviewed.

### Medium - new Hyprland aerial tooling conflicts with the Rust rule

The repository now records the permanent rule in `AGENTS.md:5`: newly built Hyprland desktop tooling should be Rust-based by default. The aerial implementation currently uses Bash/Python/Lua.

Evidence:

- `/home/ben/src/hypr-aerial/bin/aerial:1` is Bash.
- `/home/ben/src/hypr-aerial/bin/aerial-daemon:1` is Bash.
- `/home/ben/src/hypr-aerial/bin/aerial-daemon:17` shells out to Python to parse JSON config.
- `/home/ben/src/hypr-aerial/bin/aerial-daemon:58` builds a long `mpvpaper` option string in shell.
- `/home/ben/src/hypr-aerial/share/aerial-transition.lua:124` implements the live mpv fade.

Impact:

- This is not a runtime blocker, but it is now architectural debt.
- The Lua script is a reasonable exception because it runs embedded inside mpv; the CLI, daemon, fetcher, and settings entrypoints should be rewritten in Rust or explicitly documented as temporary exceptions.

Recommended next step:

- Create a Rust `hypr-aerial` binary with subcommands: `start`, `stop`, `toggle`, `status`, `next`, `prev`, `fetch`, and `settings`.
- Keep the current single persistent `mpvpaper` process and mpv IPC transition model.
- Keep the mpv Lua transition script unless replacing it with a native mpv-supported mechanism that does not restart `mpvpaper`.

### Medium - `plasma-kwallet-pam.service` is failed

`systemctl --user --failed` reports one failed unit:

- `plasma-kwallet-pam.service`

Relevant config:

- `config/hypr/hyprland.conf:31` starts `plasma-kwallet-pam.service` on Hyprland login.

Impact:

- KWallet D-Bus services are running, so the visible impact may be limited.
- The failed unit should still be resolved or removed from startup because it leaves the session in a degraded state.

Recommended next step:

- Check whether this PAM unlock service is useful under the current `greetd` session.
- If not, remove or guard the autostart and rely on the running KWallet D-Bus services.
- If yes, fix the credentials/session environment that makes it fail.

### Medium - wallpaper ownership is split between hyprpaper and aerial

`hypr-doctor` passes, but warns that `hyprpaper` is not running. That is expected when aerial/mpvpaper owns the wallpaper, but the config still autostarts `hyprpaper`.

Evidence:

- `config/hypr/hyprland.conf:34` autostarts `hyprpaper`.
- `/home/ben/src/hypr-aerial/bin/aerial:34` kills `hyprpaper` when aerial starts.
- `/home/ben/src/hypr-aerial/bin/aerial:49` kills `mpvpaper` when aerial stops.
- `hypr-doctor`: `failures=0 warnings=2`; one warning is `process not found: hyprpaper`.

Impact:

- The warning is probably harmless while aerial is active, but the checker and autostart policy disagree about the intended wallpaper provider.

Recommended next step:

- Either make aerial the explicit wallpaper provider and update doctor/readiness checks accordingly, or keep hyprpaper as the default and document aerial as an on-demand override.

### Medium - NetworkManager applet expectation is unmet

`hypr-doctor` also warns that the NetworkManager applet process is missing.

Impact:

- If network status is handled elsewhere, this is just a stale checker expectation.
- If the applet is still expected, add or restore autostart.

Recommended next step:

- Decide whether `nm-applet` is part of the intended Hyprland session.
- Update the autostart/checker pair so they agree.

### Low - current shell lacks imported dark-theme vars

Hyprland and systemd user environments have the intended dark-theme variables, but an older terminal shell may not.

Evidence:

- `config/hypr/hyprland.conf:17` sets `QT_QPA_PLATFORMTHEME,kde`.
- `config/hypr/hyprland.conf:18` sets `QT_STYLE_OVERRIDE,Breeze`.
- `config/hypr/hyprland.conf:19` sets `KDE_SESSION_VERSION,6`.
- `config/hypr/hyprland.conf:20` sets `GTK_THEME,Breeze-Dark`.
- `config/hypr/hyprland.conf:28` imports those variables into systemd user env.
- `config/hypr/hyprland.conf:29` imports them into D-Bus activation env.
- `systemctl --user show-environment` reports `GTK_THEME=Breeze-Dark`, `KDE_SESSION_VERSION=6`, `QT_QPA_PLATFORMTHEME=kde`, and `QT_STYLE_OVERRIDE=Breeze`.

Impact:

- Apps launched from Hyprland should be dark.
- Apps launched from an old terminal that predates the import may miss these vars until the terminal is restarted.

### Low - dead code remains in `waybar-ai-refresh.sh`

`scripts/waybar-ai-refresh.sh:46` through `scripts/waybar-ai-refresh.sh:48` are unreachable because every `case` arm exits before reaching them.

Impact:

- No current runtime impact.
- It should be removed during cleanup so future signal behavior is easier to reason about.

### Low - aerial library is large

The aerial media library currently has:

- `63` `.mov` files
- `0` `.part` files
- `15G` total size under `/home/ben/Videos/aerials`

Impact:

- Downloads completed cleanly.
- Disk usage is significant; monitor free space before expanding the catalog.

## Current Working State

### Runtime

- Current session is Hyprland under Wayland.
- `hyprctl configerrors` reports no config errors.
- `hypr-doctor` passes with `failures=0 warnings=2`.
- Warnings are `hyprpaper` not running and NetworkManager applet not running.
- `waybar.service` is active and running.
- `systemctl --user --failed` reports only `plasma-kwallet-pam.service`.

### Theme

- KDE color scheme is `BreezeDark`.
- KDE look-and-feel package is `org.fedoraproject.fedoradark.desktop`.
- GTK theme is `Breeze-Dark`.
- GNOME color scheme is `prefer-dark`.
- `fc-match sans` resolves to `SF Pro Text`.
- `fc-match monospace` resolves to `SF Mono`.

### Waybar AI Helpers

- `config/waybar/config.jsonc:50` uses `/home/ben/.local/bin/waybar-helper claude`.
- `cargo test` in `waybar-helper` passes: `4 passed; 0 failed; 1 ignored`.
- `/home/ben/.local/bin/waybar-helper codex` returns valid Waybar JSON.
- `/home/ben/.local/bin/waybar-helper claude` returns valid Waybar JSON, currently from cached Claude usage because Anthropic rate-limited refresh.

### Aerial Wallpapers

- `/home/ben/.local/bin/aerial status` reports aerial is running.
- Current daemon PID file reports PID `1073019`.
- The design uses one persistent `mpvpaper` process with mpv IPC at `/run/user/1000/aerial-mpv.sock`.
- `/home/ben/src/hypr-aerial/share/aerial-transition.lua:162` transitions by fading out, loading the next file, then fading in.
- `/home/ben/src/hypr-aerial/share/aerial-transition.lua:222` and `:226` expose `aerial-next` and `aerial-prev` mpv script messages.
- Hyprland binds are installed:
  - `Super+Shift+A`: `/home/ben/.local/bin/aerial toggle`
  - `Super+Shift+]`: `/home/ben/.local/bin/aerial next`
  - `Super+Shift+[`: `/home/ben/.local/bin/aerial prev`

## Dirty Worktrees

Dotfiles:

```text
 M README.md
 M config/hypr/hypridle.conf
 M config/hypr/hyprland.conf
 M config/waybar/config.jsonc
 M scripts/waybar-ai-refresh.sh
 M scripts/waybar-claude-usage.py
 M scripts/waybar-openai-tokens.py
 M waybar-helper/src/codex.rs
 M waybar-helper/src/main.rs
 M waybar-helper/src/usage.rs
?? AGENTS.md
?? config/fontconfig/
?? config/gtk-3.0/
?? config/gtk-4.0/
?? config/kdedefaults/
?? config/kdeglobals
?? config/mpv/
?? scripts/hypr-lock-and-refresh.sh
?? waybar-helper/src/claude.rs
```

Hypr Aerial:

```text
 M README.md
 M bin/aerial
 M bin/aerial-daemon
 M bin/aerial-fetch
 M bin/aerial-settings
?? share/
```

## Verification Commands Run

```text
/home/ben/.local/bin/hypr-validate
/home/ben/.local/bin/hypr-readiness
/home/ben/.local/bin/hypr-doctor
hyprctl configerrors
hyprctl binds -j | jq -r '.[] | select(.arg|test("aerial")) | [.modmask,.key,.arg] | @tsv'
systemctl --user --failed --no-pager
systemctl --user status waybar.service --no-pager
systemctl --user show-environment | rg '^(GTK_THEME|KDE_SESSION_VERSION|QT_QPA_PLATFORMTHEME|QT_STYLE_OVERRIDE)='
kreadconfig6 --file kdeglobals --group General --key ColorScheme
kreadconfig6 --file kdeglobals --group KDE --key LookAndFeelPackage
kreadconfig6 --file kdeglobals --group General --key font
gsettings get org.gnome.desktop.interface gtk-theme
gsettings get org.gnome.desktop.interface color-scheme
fc-match sans
fc-match monospace
cargo test
/home/ben/.local/bin/waybar-helper codex
/home/ben/.local/bin/waybar-helper claude
/home/ben/.local/bin/aerial status
bash -n bin/aerial bin/aerial-daemon bin/aerial-fetch
python3 -m py_compile bin/aerial-settings
python3 -m py_compile scripts/waybar-claude-usage.py scripts/waybar-openai-tokens.py scripts/waybar-hover-refresh.py scripts/hypr-trackpad-gestures.py scripts/workspace-osd.py scripts/liquid-launcher.py
bash -n scripts/waybar-ai-refresh.sh scripts/hypr-lock-and-refresh.sh
```

## Resolution - 2026-05-30

Findings addressed in order.

### High - readiness gates (resolved)

`greetd` is the intended login manager: `display-manager.service` points at
`greetd.service` and the greeter (`/usr/local/bin/hypr-greeter-app`) offers
`Plasma (Wayland)`, plus Hyprland safe/recovery, as explicit fallback sessions —
the recovery contract moved from plasmalogin's remembered-session state into the
greetd greeter. `hypr-validate` and `hypr-readiness` were updated to:

- assert `greetd` is the active default display manager (replacing the
  `plasmalogin.service` active check),
- assert the greeter still offers the Plasma fallback (`grep startplasma-wayland`
  in `hypr-greeter-app`) and that the tracked greetd config/greeter are installed
  (replacing the plasmalogin remembered-session readability check),
- retire the four pre-migration Btrfs snapshot checks: `/.snapshots` has been
  reclaimed and recreating the snapshots now would capture the current Hyprland
  state, not a pre-Hyprland rollback point. The on-disk backup bundles plus the
  greeter's Plasma fallback remain the recovery artifacts.

After committing + pushing this branch, both gates are green.

### Medium - aerial tooling now Rust (resolved)

`~/src/hypr-aerial` was rewritten as a single Rust `aerial` binary
(start/stop/toggle/status/next/prev/fetch/settings + internal `__daemon`),
preserving the single persistent `mpvpaper` process and the mpv IPC transition
model. `next`/`prev` now use a std `UnixStream` (no `socat`). The mpv Lua
transition script is kept (it runs embedded in mpv); the GTK dialog is retained
as the optional `bin/aerial-settings-gui`, with `aerial settings` as the
canonical CLI. (Committed/pushed in the hypr-aerial repo.)

### Medium - kwallet (resolved)

Removed the `plasma-kwallet-pam.service` autostart from `hyprland.conf`: under
greetd the PAM stack already unlocks kwallet at login (it exports
`PAM_KWALLET5_LOGIN`) and the kwallet D-Bus service activates on demand, so the
unit only re-ran `pam_kwallet_init` with no credentials and failed. The live
failed unit was `reset-failed`; `systemctl --user --failed` is now empty.

### Medium - wallpaper provider (resolved)

`hyprpaper` stays the default boot wallpaper; aerial is an on-demand override.
`hypr-doctor` now treats either `hyprpaper` or `mpvpaper` (aerial) as a valid
wallpaper provider instead of warning when hyprpaper is absent.

### Medium - nm-applet (resolved)

Network status is handled by Waybar's native `network` module plus
`scripts/network-popup.py` and `nm-connection-editor`, so `nm-applet` is not part
of the session. Removed the stale `hypr-doctor` check. `hypr-doctor` now reports
`failures=0 warnings=0`.

### Low - dark-theme vars (no action)

Hyprland/systemd/D-Bus envs already carry the dark-theme vars; only pre-existing
terminals miss them until restarted. No change needed.

### Low - dead code (resolved)

Removed the unreachable post-`case` block in `scripts/waybar-ai-refresh.sh`.

### Low - aerial library size (monitor)

63 `.mov` files, ~15G under `~/Videos/aerials`. Informational; keep an eye on
free space before expanding the catalog. No code change.

### Follow-up feature - wallpaper favourites (added)

Added `aerial favorite` / `unfavorite` and `aerial favorites [on|off|toggle|list]`
plus Hyprland binds `Super+Shift+F` (favourite the clip on screen) and
`Super+Shift+M` (favourites-only mode). Favourites live in
`~/.config/hypr-aerial/favorites`; the mpv Lua script filters rotation to them in
favourites mode. Verified live end-to-end, and the GTK settings GUI was launched
and confirmed to render against live config.
