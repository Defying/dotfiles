# Claude Code Rust Rewrite Handoff - 2026-05-29

This is a handoff for a staged Rust migration of the Fedora Asahi Hyprland /
Waybar helper stack in `/home/ben/dotfiles`.

The user wants this optimized and rewritten toward Rust where it makes sense.
Do not do a blind big-bang rewrite. Preserve working behavior, keep the current
UI design direction, and migrate by risk/impact.

## Ground Rules

- Keep the machine usable while working. Do not break Waybar, Hyprland startup,
  notifications, quick settings, or login/session recovery.
- Do not touch Apple Reminders except the dedicated AI reset reminder behavior.
  The user explicitly does not want any other list modified.
- Keep remote SSH behavior for AI reset reminders. Make it idempotent and
  efficient; do not spam reminders.
- Prefer cache-only reads on UI open paths. Heavy network/CLI refreshes should
  happen only on explicit refresh or scheduled background ticks.
- Do not replace working Python GTK UI with an incomplete Rust UI. Port in
  slices and keep fallbacks until the Rust version is proven.
- Use exact process matching for Waybar signals. Loose `pkill ... waybar` also
  matches `waybar-helper` and can kill the Rust helper.
- Keep the user-facing visual style compact, glyph-forward, and not bulky.

## Current Repo State

At handoff time, these files were dirty:

```text
M config/hypr/hyprland.conf
M scripts/ai_accounts.py
M scripts/waybar-ai-refresh.sh
M scripts/waybar-claude-usage.py
M scripts/waybar-hover-refresh.py
M scripts/waybar-openai-tokens.py
M waybar-helper/README.md
M waybar-helper/src/main.rs
```

What those dirty changes mean:

- `waybar-helper/src/main.rs` has an in-progress Rust port of
  `scripts/hypr-waybar-autohide.py` as `waybar-helper autohide`.
- `config/hypr/hyprland.conf` has been changed to launch
  `/home/ben/.local/bin/waybar-helper autohide` instead of the Python autohide
  daemon.
- `waybar-helper/README.md` documents the new `autohide` subcommand.
- The Waybar signal call sites were patched toward exact matching:
  `pkill -RTMIN+N -x waybar`.

Important: review these dirty changes before continuing. They were not
committed yet.

## Toolchain State

Rust is installed both through Fedora packages and rustup shims.

Fedora tools on PATH:

```text
/usr/bin/cargo
/usr/bin/rustfmt
/usr/bin/cargo-fmt
/usr/bin/clippy-driver
/usr/bin/rust-analyzer
cargo 1.95.0 (Fedora 1.95.0-5.fc44)
rustfmt 1.9.0
clippy 0.1.95
```

Installed Fedora packages include:

```text
rust
cargo
rustfmt
clippy
rust-analyzer
rust-src
rust-std-static
```

The rustup stable toolchain also had these components added:

```text
rustfmt
rust-src
rust-analyzer
clippy already present
```

## Current Rust Helper

Rust crate:

```text
waybar-helper/
  Cargo.toml
  Cargo.lock
  src/main.rs
```

Current subcommands:

```text
waybar-helper sysmon
waybar-helper clock24
waybar-helper clock12
waybar-helper date
waybar-helper autohide   # in progress
```

`sysmon`, `clock24`, `clock12`, and `date` are already intended as fast Waybar
custom module replacements. Waybar currently uses:

```text
/home/ben/.local/bin/waybar-helper sysmon
/home/ben/.local/bin/waybar-helper clock24
/home/ben/.local/bin/waybar-helper date
```

`clock12` exists but `config/waybar/config.jsonc` may still point at the old
shell wrapper.

## Immediate Highest Priority

### 1. Finish and validate `waybar-helper autohide`

The Python original is:

```text
scripts/hypr-waybar-autohide.py
```

Behavior to preserve:

- Event-driven on Hyprland socket2.
- No cursor polling unless active workspace has fullscreen content.
- While fullscreen, poll cursor at 10 Hz:
  - cursor near top reveals Waybar
  - cursor below bar hides Waybar
- Always restore Waybar when leaving fullscreen.
- Reconcile against actual `j/activeworkspace.hasfullscreen`, not only event
  payloads, because Hyprland does not always emit a clean exit event.

Known issue found during handoff:

- A loose signal such as `pkill -RTMIN+8 waybar` can kill `waybar-helper` because
  it matches process names containing `waybar`.
- Existing call sites should use `pkill -RTMIN+8 -x waybar`.
- Confirm every signal path is exact-matched, including hypridle resume hooks.

Validate with:

```sh
cargo fmt --manifest-path waybar-helper/Cargo.toml --check
cargo clippy --manifest-path waybar-helper/Cargo.toml -- -D warnings
cargo build --release --manifest-path waybar-helper/Cargo.toml
install -m755 waybar-helper/target/release/waybar-helper ~/.local/bin/waybar-helper
timeout 2s ~/.local/bin/waybar-helper autohide
```

`timeout` should exit with `124`, meaning the daemon stayed alive until killed.

Then run it live through a transient user unit or reload Hyprland:

```sh
systemd-run --user --unit=waybar-helper-autohide --collect ~/.local/bin/waybar-helper autohide
systemctl --user status --no-pager waybar-helper-autohide.service
```

Test fullscreen enter/exit manually and verify Waybar always comes back.

### 2. Fix Codex countdown display if it still shows weekly incorrectly

Observed state during handoff:

- Codex Waybar bubble showed a weekly countdown like `6d 16h`.
- Running `scripts/waybar-openai-tokens.py` directly emitted a healthy 5h
  percentage, e.g. `84%`, and tooltip said `bar shows 5h window remaining %`.
- Cache showed:
  - primary/5h usage not exhausted
  - weekly usage not exhausted
  - `rateLimitReachedType: null`

So the Python indicator logic looked correct at that moment. If Waybar still
shows a weekly countdown, suspect stale Waybar process/module state, signal
delivery, or a refresh race, not necessarily the calculation itself.

Useful commands:

```sh
/home/ben/dotfiles/scripts/waybar-openai-tokens.py
cat ~/.cache/waybar/codex-usage.json
pkill -RTMIN+8 -x waybar
```

Do not make Codex show weekly countdown unless the weekly window is actually at
0% remaining or the service reports a real weekly block.

## Rust Migration Priority Order

### Phase 1: Low-risk Waybar emitters

Port tiny modules first. These are easiest to verify because they print one JSON
object and exit.

Candidates:

- `scripts/waybar-weather.sh`
- `scripts/waybar-notifications-count.sh`
- old clock/date wrappers still referenced anywhere
- small JSON status wrappers

Guidelines:

- Preserve Waybar JSON shape exactly: `text`, `tooltip`, and optional `class`.
- Keep caching behavior. Weather must not call network on every paint if cache
  is still valid.
- Use filesystem and process APIs directly where possible.
- Avoid spawning Python just to JSON-escape strings.

### Phase 2: Daemons and Hyprland socket listeners

These are good Rust targets because Python/GTK/GLib overhead is unnecessary for
event loops that mostly listen to sockets:

- `scripts/hypr-waybar-autohide.py` (already in progress)
- parts of `scripts/workspace-osd.py` that listen to socket2
- `scripts/waybar-hover-refresh.py`
- maybe `scripts/hypr-auto-brightness.py`

Guidelines:

- Use Hyprland sockets directly when practical.
- Avoid `hyprctl` loops. If polling is unavoidable, gate it strictly.
- Keep current behavior before adding new behavior.
- Add logging only where it helps debug state transitions.

### Phase 3: CLI account/cache/service helpers

These are medium risk:

- `scripts/ai_accounts.py`
- `scripts/ai_reset.py`
- `scripts/waybar-openai-tokens.py`
- `scripts/waybar-claude-usage.py`
- `scripts/waybar-ai-refresh.sh`

Why medium risk:

- They involve auth files, cache files, external CLIs, remote SSH, systemd
  timers, and Apple Reminders.
- The user has already been burned by reminder/list bugs. Be conservative.

Guidelines:

- Keep cache schema compatible until all consumers are migrated.
- Preserve account switching semantics for Codex:
  - active auth at `~/.codex/auth.json`
  - account slots under `~/.codex/accounts/<slot>/`
  - cache under `~/.cache/waybar/`
- Keep AI usage refreshes explicit and throttled. Do not hit network or provider
  CLIs on every UI open.
- Apple Reminder behavior must dedupe by service, window, reset epoch, and host.
- Only target the AI reset reminders list. Do not touch grocery or other lists.

### Phase 4: UI popups and quick settings

These are highest risk and should not be the first rewrite:

- `scripts/glass_popup.py`
- `scripts/quick-settings-panel.py`
- `scripts/ai-usage-popup.py`
- `scripts/network-popup.py`
- `scripts/weather-popup.py`
- `scripts/notification-panel.py`
- `scripts/liquid-launcher.py`
- `scripts/liquid-osd.py`

Reason:

- These use GTK/layer-shell, custom visuals, and current design details the user
  has been tuning interactively.
- A direct Rust rewrite likely needs GTK4/gtk-layer-shell or another Wayland UI
  stack, and visual regressions will be obvious.

Suggested approach:

- First extract shared non-UI logic into Rust helpers if useful.
- Keep the Python UI as the shell until the Rust UI can match visuals and
  interactions.
- Screenshot-test every change on the actual desktop.
- Quick settings must remain compact:
  - no `Quick Settings` label
  - glyph close button in the top toggle row
  - glyph-first toggles
  - thick no-knob sliders
  - AI rows with logos and useful details

## Current Important Behavior to Preserve

Waybar:

- Separate bubbles for workspaces, Codex, Claude, date/time/weather, sysmon,
  tray, status, notifications, quick settings.
- Codex and Claude bubbles read cached usage quickly and refresh in background.
- Middle-click refresh exists for AI bubbles.
- AI bubble click opens a popup reading cached data.
- Quick settings right-click remains available from AI bubbles.

AI usage:

- Codex uses ChatGPT/Codex subscription limits via Codex CLI app-server, not
  OpenAI platform API usage.
- Claude uses Claude Code OAuth usage endpoint, not transcript logs.
- Display percent remaining, not percent used.
- When a real exhausted window exists, show reset countdown for that exact
  exhausted window.
- If a cached reset timestamp has passed but refresh is rate-limited, avoid
  cryptic stale labels like `now`; show meaningful stale/remaining context.

System:

- Keep `misc:allow_session_lock_restore=true` in Hyprland config.
- Keep Plasma installed and do not damage fallback session behavior.
- Keep Hyprland startup reliable.

## Validation Checklist

Run after each meaningful migration step:

```sh
git diff --check
cargo fmt --manifest-path waybar-helper/Cargo.toml --check
cargo clippy --manifest-path waybar-helper/Cargo.toml -- -D warnings
cargo build --release --manifest-path waybar-helper/Cargo.toml
python3 -m py_compile scripts/waybar-openai-tokens.py scripts/waybar-claude-usage.py scripts/ai_accounts.py scripts/ai_reset.py
```

For UI changes, also:

```sh
pkill -x waybar
setsid -f waybar
grim /tmp/waybar-check.png
```

For quick settings:

```sh
pkill -f '^python3 /home/ben/dotfiles/scripts/quick-settings-panel.py$' || true
setsid -f /home/ben/dotfiles/scripts/quick-settings-panel.py
grim /tmp/quick-settings-check.png
```

Inspect screenshots before committing.

For AI:

```sh
/home/ben/dotfiles/scripts/waybar-openai-tokens.py
/home/ben/dotfiles/scripts/waybar-claude-usage.py
cat ~/.cache/waybar/codex-usage.json
cat ~/.cache/waybar/claude-usage.json
```

## Suggested Commit Strategy

Commit small, reversible units:

1. Finish existing `waybar-helper autohide` plus exact Waybar signal matching.
2. Wire and validate `clock12` if desired.
3. Port one simple Waybar JSON module at a time.
4. Port daemon/socket listeners.
5. Only then tackle AI helpers.
6. Leave GTK popup rewrites until there is a clear Rust UI strategy.

Each commit should include the validation actually run.

## Do Not Forget

- The user values efficiency, but not at the cost of breaking the UI or account
  behavior.
- Do not disable Apple Reminders.
- Do not send reminders to any list other than the AI reset list.
- Do not assume a stale Waybar label means the provider limit is actually stale
  or blocked. Check cache and direct script output.
- Prefer Rust where it removes repeated Python startup, long-running Python
  daemons, shell JSON hacks, or fragile process matching.
- Leave Python in place where it is currently the safest way to preserve a
  tuned GTK/layer-shell UI.
