# Codex Handoff: AI Usage Profiles and Glass Audit

Date: 2026-06-01
Repo: `/home/ben/dotfiles`

## Current State

The `ben` Codex account is fixed. `codex-ben` was re-logged into `ben@carveworkshop.com`, GitHub connector was linked, the stale empty `codex_apps` cache was removed, and `codex-ben` was restarted. The regenerated tool cache has 90 GitHub tools.

The Codex Waybar click panel was updated in `scripts/ai-usage-popup.py`:

- It is now a dedicated opaque layer-shell panel, not `GlassPopup`.
- It reads all `~/.cache/waybar/codex-usage*.json` files and dedupes stale duplicate slots.
- It shows profile cards for `ben-7b0d6a0e` and `defying`.
- The active profile is first and the entire active card is highlighted.
- There is a bottom-row `⇄` button for one-shot swapping to the other profile.
- Inactive profile rows still have a direct arrow button.
- Profile activation runs `scripts/ai_accounts.py codex-activate SLOT`, then refreshes via `scripts/waybar-ai-refresh.sh codex 8`.
- Reset countdown labels are split into left/right labels: left `resets sun 12:35`, right-aligned `6d 12h`; no `in` text.

Apple Reminders/reset scheduling is tied in through the existing Rust refresh path. `waybar-helper codex --refresh` calls account-specific reset scheduling using service names like `Codex defying` and `Codex ben@carveworkshop.com`, so systemd timers and Mac reminders stay separate per profile. The Mac list is `AI Resets`; dedupe keys include service/window/epoch/host/list.

## Verification Already Run

```sh
python3 -m py_compile scripts/ai-usage-popup.py
cargo test
cargo test schedule_then_cancel_arms_and_clears_timer -- --ignored --test-threads=1
hyprctl reload
hyprctl configerrors
```

Results:

- Python compile passed.
- Rust tests passed: 8 passed, 1 ignored.
- Controlled timer test passed with the Mac reminder disabled by the test.
- `hyprctl configerrors` returned `ok`.
- Live `hyprctl layers` showed `namespace: ai-usage` when the panel was open.

## Blur Investigation

The user said the launcher blur is perfect but the AI usage panel looked bad. The compositor rule was not missing: `ai-usage` had a live layer and a blur rule.

Actual difference found:

- `scripts/liquid-launcher.py` paints a hard-edged Cairo card at about `rgba(..., 0.72)`.
- The AI panel was a GTK CSS card at `rgba(..., 0.92)` with an outer shadow, so Hyprland blur was active but visually buried and the shadow could muddy the alpha mask.

Changed:

- `scripts/ai-usage-popup.py`: panel material now uses launcher-like lower alpha (`rgba(8, 11, 20, 0.74)`), stronger glass border, accent gradient, and no outer CSS shadow.
- `config/hypr/hyprland.conf`: tightened `ai-usage` ignore-alpha to exact namespace:

```conf
layerrule = blur true, match:namespace ^ai-usage$
layerrule = match:namespace ^ai-usage$, ignore_alpha 0.01
```

Screenshots captured during investigation:

- `/tmp/ai-usage-before.png`
- `/tmp/launcher-reference.png`
- `/tmp/ai-usage-after-glass.png`

These are temporary but useful if still present.

## Likely Next Work

The user asked what other menu bar/UI items have the same issue. Continue the audit from live surfaces and source, not guesses.

Known likely offenders from source scan:

- `scripts/quick-settings-panel.py`: dedicated layer-shell `quick-settings`, but CSS uses `rgba(12, 16, 24, 0.88)` plus an outer shadow. This is similar to the old AI panel and probably hides blur.
- `scripts/notification-panel.py`: dedicated layer-shell `notification-panel`, CSS uses `rgba(12, 16, 24, 0.94)` plus an outer shadow. Very likely too opaque.
- `scripts/glass_popup.py`: legacy full-screen transparent overlay for `network`, `weather`, and some older popups. Base panel is `rgba(10, 14, 24, 0.62)` but has a large outer shadow. Existing comments say that shadow can expand the blur mask/halo.
- `scripts/emoji-picker.py`: already compensates for that by overriding GlassPopup with launcher-like material and no outer shadow. Probably ok.
- `scripts/liquid-launcher.py`: good reference implementation. Cairo hard-edged card.
- `scripts/liquid-osd.py` and `scripts/workspace-osd.py`: Cairo-drawn, likely ok.
- Waybar itself has its own blur rule and lower `ignore_alpha`; do not change Waybar-wide blur casually.

When continuing, open one surface at a time, run `hyprctl layers`, inspect the namespace, capture a screenshot with `grim`, and compare material alpha/shadow against the launcher.

## Dirty Worktree Warning

The worktree has many unrelated dirty files from prior work. Do not revert them casually. Relevant files for this task are mainly:

- `scripts/ai-usage-popup.py`
- `config/hypr/hyprland.conf`
- `waybar-helper/src/codex.rs`
- `waybar-helper/src/reset.rs`
- `waybar-helper/src/accounts.rs`
- `scripts/ai_accounts.py`
- `home/.local/bin/codex-tmux`
