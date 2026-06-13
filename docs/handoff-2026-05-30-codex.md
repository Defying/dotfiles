# Handoff — 2026-05-30 (for Codex)

Session got long; handing off. Repos: `~/dotfiles` (main, pushed to
`github.com:Defying/dotfiles`) and `~/src/hypr-aerial` (separate, pushed). Both
build offline; crates are cached in `~/.cargo`.

## Environment facts (important)
- Fedora Asahi, **M1 Air**, Hyprland, **greetd** login (greeter offers Plasma
  fallback). glibc **2.43**.
- **No GPU utilisation sysfs** on Asahi (no devfreq / drm-engine fdinfo) — can't
  show GPU usage. **hyprpaper can't render** here (GPU) → static wallpaper is
  **swaybg**; aerial overlays mpvpaper on top.
- Waybar config is JSON validated by `jq` in `hypr-validate` → **no `//`
  comments** in `config/waybar/config.jsonc` or the gate breaks.
- Memory dir: `~/.claude/projects/-home-ben-dotfiles/memory/` (see MEMORY.md).

## DONE this session (all committed + pushed unless noted)
- Addressed `docs/audit-handoff-2026-05-30.md` findings (greetd validators,
  retired btrfs checks, kwallet autostart removed, hypr-doctor swaybg/mpvpaper +
  no nm-applet, dead code).
- **hypr-aerial rewritten in Rust** (single binary: start/stop/toggle/status/
  next/prev/fetch/settings/__daemon) + **favourites**: `aerial favorite`
  (Super+Shift+F), `aerial favorites toggle` (Super+Shift+M); favourites in
  `~/.config/hypr-aerial/favorites`; mpv Lua filters in favourites mode.
- Static wallpaper → swaybg; image moved into repo `assets/wallpaper-marek-piwnicki.jpg`.
- Masked `drkonqi-coredump-processor@.service` (was hanging 5min/crash).
- **waybar sysmon** rewritten layout (2-row grid, glyphs), added **SSD** +
  **system wattage**, net in **KiB/MiB**, net moved to right column, and
  **continuous 1400ms updates** (`waybar-helper sysmon 1400` loop;
  `restart-interval` in config). Cost ~1ms/sample.
- **AI usage notifications**: `usage.rs::maybe_notify` now per-(service,account),
  urgency by level (danger→critical sticky mako, warn→normal), account label in
  toast, per-account sync tag. Codex passes its account; Claude single-account.
  mako `[app-name="AI usage" urgency=critical]` rule; `.danger` bar item solid red.
- **Glass popup blur** fixed: `ignore_alpha 0.20→0.08`, panel opacity `0.34→0.62`.

## JUST DID — NOT committed yet (DO THIS FIRST)
- `scripts/build-on-mini` (+ `~/.local/bin/build-on-mini` symlink) — NEW, untracked.
  Commit it. Tested working: `build-on-mini waybar-helper --install` builds the
  Linux binary on the mini and installs it. Verified the mini-built binary runs
  on the laptop (GLIBC_2.28 ≤ 2.43).

## Mac mini build server (set up, working)
- SSH alias `mini` → 10.0.0.111, user ben, key-based. macOS arm64, 10 cores.
- One-time setup ALREADY done on mini: rustup (minimal) + `rustup target add
  aarch64-unknown-linux-gnu` + `brew install
  messense/macos-cross-toolchains/aarch64-unknown-linux-gnu` +
  `~/.cargo/config.toml` linker for that target.
- Gotcha: Homebrew rust precedes rustup on PATH; the script force-prepends
  `~/.cargo/bin`.
- Use it to keep the laptop cool: `build-on-mini hypr-aerial`,
  `build-on-mini waybar-helper --install`.

## PENDING / next
- Commit `scripts/build-on-mini`.
- (Optional) make `aerial` use build-on-mini in its README dev notes.
- Verify the urgent AI notification fires on a real <10% transition (only the
  synthetic test was shown).
- User was rapid-firing waybar tweaks; expect more polish requests.

## Build/install reminders
- waybar-helper: `~/.local/bin/waybar-helper` is a real file (cp the built
  binary, or `build-on-mini waybar-helper --install`). Module auto-refreshes.
- aerial: symlinks in `~/.local/bin` → `~/src/hypr-aerial/target/release/aerial`.
- After waybar style/config changes: `systemctl --user restart waybar.service`
  (CSS hot-reloads via reload_style_on_change).
