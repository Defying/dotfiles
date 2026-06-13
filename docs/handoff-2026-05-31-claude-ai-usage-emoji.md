# Claude Handoff - AI Usage, Mako, Emoji Blur

Date: 2026-05-31  
Repo: `/home/ben/dotfiles`

## User Request

Ben stopped Codex and asked for this handoff. Continue the active desktop task:

- Codex Waybar/menu-bar usage should understand multiple saved Codex accounts.
- If the active Codex account hits a limit, tell Ben whether another saved account is available now or resets sooner.
- Consider both 5h and weekly windows.
- If a Codex account hits the weekly limit, do not make the Waybar bubble red.
- Mako notifications look wrong: little/no blur and text is too large.
- Claude's emoji picker has a major blur bug compared with the launcher.
- Apple Reminders reset notifications need to work for multiple Codex accounts independently.

## Current Git State

`main` is ahead of `origin/main` by one commit:

- `b19e393 emoji: macOS-style glass picker UI over hypr-emoji-picker backend`

Current dirty files from `git status -sb --untracked-files=all`:

```text
 M config/hypr/hyprland.conf
 M config/mako/config
 M docs/hyprland-brightness-race-2026-05-30.md
 M scripts/emoji-picker.py
 M waybar-helper/src/accounts.rs
 M waybar-helper/src/codex.rs
 M waybar-helper/src/main.rs
 M waybar-helper/src/reset.rs
 M waybar-helper/src/usage.rs
?? docs/handoff-2026-05-30-claude.md
?? docs/handoff-2026-05-30-codex.md
?? docs/hypr-emoji-picker-backend-2026-05-30.md
?? docs/wayland-emoji-picker-globe-key-2026-05-30.md
?? hypr-emoji-picker/.gitignore
?? hypr-emoji-picker/Cargo.lock
?? hypr-emoji-picker/Cargo.toml
?? hypr-emoji-picker/src/main.rs
```

Important: preserve unrelated dirty docs and the untracked `hypr-emoji-picker/` backend unless the task explicitly needs them. Do not revert Ben/Codex/Claude changes casually.

## What Codex Changed This Turn

### Codex Usage Logic

Edited:

- `waybar-helper/src/accounts.rs`
- `waybar-helper/src/codex.rs`
- `waybar-helper/src/reset.rs`
- formatting-only churn in `waybar-helper/src/main.rs` and `waybar-helper/src/usage.rs` from `cargo fmt`

Implemented in the Rust helper:

- `accounts::list_accounts()` for saved Codex slots under `~/.codex/accounts/`.
- Per-slot Codex usage caches:
  - `~/.cache/waybar/codex-usage.json` remains the active Waybar cache.
  - `~/.cache/waybar/codex-usage-defying.json`
  - `~/.cache/waybar/codex-usage-ben-7b0d6a0e.json`
- Weekly exhaustion now maps to normal/subscription styling, not red/danger styling.
- 5h exhaustion or very low 5h usage can still warn/danger.
- Reset timer service names are now account-specific through the service label, for example:
  - `Codex defying`
  - `Codex ben@carveworkshop.com`
- Timer units/state are slugged, so multiple account reset reminders do not overwrite each other.
- New tests cover:
  - weekly exhaustion stays visually calm
  - reset service names keep account timers independent

### Mako / Hyprland Visuals

Edited:

- `config/mako/config`
- `config/hypr/hyprland.conf`

Changes:

- Mako font reduced from `SF Pro Text 13` to `SF Pro Text 11`.
- Notification width/height/padding/icon size reduced.
- AI usage critical notifications no longer use the giant solid red style.
- Added a Hyprland blur rule for namespace `mako`, while existing live namespace `notifications` remains covered.
- Confirmed with a probe that live Mako notifications currently show as namespace `notifications`.

### Emoji Picker Blur

Claude had built `scripts/emoji-picker.py` as a `GlassPopup` subclass over the Rust backend. Codex inspected it and verified it live.

Live verification screenshot:

```text
/tmp/dotfiles-ai-emoji-check.png
```

The screenshot shows the bug clearly: the centered picker panel is fine, but a huge blurred oval/field appears behind it. This is not launcher parity.

Likely cause:

- `scripts/emoji-picker.py` uses `GlassPopup`.
- `GlassPopup` is a fullscreen transparent overlay with a card inside it.
- Hyprland blur is applied to namespace `glass-popup-emoji`.
- Even with a transparent scrim, the fullscreen layer-shell overlay is causing blur outside the intended card area.

Codex had started toward a fix by adjusting Hyprland rules:

```text
layerrule = blur true, match:namespace ^glass-popup-emoji$
layerrule = match:namespace ^glass-popup-emoji$, ignore_alpha 0.01
```

That did not fully fix it. The next likely fix is to stop using the fullscreen `GlassPopup` overlay for the emoji picker and instead use a centered layer-shell window shaped like `scripts/liquid-launcher.py`:

- no fullscreen click-away scrim
- namespace can be `emoji-picker` or `glass-popup-emoji`
- draw/compose only the card surface
- anchor/center like `liquid-launcher`
- keep keyboard exclusive and Esc closes
- accept that click-away may need a different mechanism, or skip click-away for now to get blur correctness

## Live Account State

Saved Codex slots:

- `defying`
  - email: `defying@me.com`
  - active slot
  - usage cache refreshed successfully
- `ben-7b0d6a0e`
  - email: `ben@carveworkshop.com`
  - isolated `CODEX_HOME=/home/ben/.codex/accounts/ben-7b0d6a0e` can report login status, but rate-limit fetch fails with 401 token invalidated

Observed after installing the new helper:

```text
/home/ben/.cache/waybar/codex-usage-defying.json
  5h: 14% remaining
  weekly: 86% remaining

/home/ben/.cache/waybar/codex-usage-ben-7b0d6a0e.json
  error: token_invalidated / authentication token has been invalidated
```

Current Waybar helper output:

```json
{"class":"warn","text":"14%","tooltip":"Codex subscription usage (plus)\naccount: defying (plus)\n5h: 14% remaining (86% used), resets 06:06\nweekly: 86% remaining (14% used), resets sun 01:06\ncredits: 0\nbar shows 5h window remaining %\n\nhttps://chatgpt.com/codex/settings/usage"}
```

There are currently no `ai-reset-*` timers active because the active `defying` account is low but not exhausted.

## Commands Already Run

Passed:

```sh
cargo fmt --manifest-path waybar-helper/Cargo.toml --check
cargo test --release --manifest-path waybar-helper/Cargo.toml
cargo clippy --manifest-path waybar-helper/Cargo.toml -- -D warnings
python3 -m py_compile scripts/emoji-picker.py scripts/glass_popup.py scripts/ai_accounts.py scripts/ai_reset.py
mako --config config/mako/config --help
Hyprland --verify-config
scripts/build-on-mini waybar-helper --install
/home/ben/.local/bin/waybar-helper codex --refresh
hyprctl reload
makoctl reload
pkill -RTMIN+8 -x waybar
```

Rust test result after the account-reminder changes:

```text
8 passed; 0 failed; 1 ignored
```

Live layer probe while emoji picker and a Mako notification were open:

```text
glass-popup-emoji  0  0     1600 1000
notifications      1228 108 356  66
wallpaper          0  0     1600 1000
waybar             0  0     1600 44
```

This proves the emoji picker is currently a fullscreen layer, which matches the blur bug.

## Remaining Work

1. Fix emoji picker blur.
   - Prefer rewriting `scripts/emoji-picker.py` to use a centered, launcher-style layer-shell window instead of `GlassPopup`.
   - Keep the Rust backend contract intact: all data/search/favorites/recents/insertion stay delegated to `/home/ben/.local/bin/hypr-emoji-picker`.
   - Verify with `hyprctl layers -j` and screenshot. The emoji layer should not be a fullscreen blurred surface.

2. Finish Codex multi-account UX.
   - Current inactive `ben@carveworkshop.com` slot needs re-login before it can be a useful fallback.
   - Once multiple slots have valid usage, verify:
     - active account exhausted, other account available now -> notification/tooltip says so
     - active account exhausted, other account blocked but sooner reset -> notification/tooltip says sooner reset
     - weekly exhausted -> standard/subscription bubble color, not red
     - 5h exhausted -> still urgent enough
   - Add tests for comparison text if time permits.

3. Verify account-specific Apple Reminders.
   - The Rust `reset::schedule()` now keys systemd state/unit names by service string, so `Codex defying` and `Codex ben@carveworkshop.com` should produce separate timers/state.
   - Need a controlled test without spamming the real Mac reminders list if possible.
   - If testing real reminders, use the configured list only (`AI Resets`) and dedupe by service/window/epoch/host/list.

4. Verify Mako appearance.
   - The screenshot showed Mako notification size is much better after config changes.
   - Need user-facing visual confirmation if Ben still thinks blur is absent.
   - Live namespace is `notifications`; Hyprland rules cover both `notifications` and `mako`.

5. Commit only once the user-visible behavior is verified.
   - Do not include unrelated dirty docs unless Ben explicitly wants them committed.

## Caution

- Ben explicitly asked not to embed continuation prompts in Markdown reports. Keep continuation prompts in chat only.
- Do not revert `scripts/emoji-picker.py`; it is Claude's committed UI work plus local edits.
- Do not assume `ben@carveworkshop.com` is usable until its Codex slot is re-logged-in. Its saved token is invalidated.
