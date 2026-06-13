# Hypr Emoji Picker Backend Handoff - 2026-05-30

Repo: `/home/ben/dotfiles`

This report covers the Rust backend for a macOS-style Wayland emoji picker. The polished UI is intentionally left for Claude; this backend provides the stable CLI, JSON contract, favorites, recents, and insertion behavior.

## Current Status

Backend crate added:

```text
/home/ben/dotfiles/hypr-emoji-picker
```

Installed binary:

```text
/home/ben/.local/bin/hypr-emoji-picker
```

The installed binary was built through the Mac mini offload helper:

```sh
scripts/build-on-mini hypr-emoji-picker --install
```

No Globe/Fn key binding has been added yet. The backend is usable now through CLI commands and the temporary `pick-fuzzel` proof UI.

## Backend Contract

Main commands:

```sh
hypr-emoji-picker search <query> [--json] [--limit N]
hypr-emoji-picker recent [--json]
hypr-emoji-picker favorite list [--json]
hypr-emoji-picker favorite add <emoji>
hypr-emoji-picker favorite remove <emoji>
hypr-emoji-picker favorite toggle <emoji>
hypr-emoji-picker insert <emoji>
hypr-emoji-picker insert <emoji> --copy-only
hypr-emoji-picker pick-fuzzel
```

`pick-fuzzel` is only a temporary proof UI. The final UI should call the backend directly.

JSON row shape:

```json
{
  "emoji": "😂",
  "name": "face with tears of joy",
  "group": "Smileys & Emotion",
  "shortcodes": ["joy"],
  "keywords": ["cry", "face", "joy", "laugh", "lmao", "lol"],
  "favorite": true,
  "recent_rank": 0,
  "recent_count": 3
}
```

Fields:

- `emoji`: literal emoji string to display and pass back to `insert`.
- `name`: Unicode CLDR name from the Rust `emojis` crate.
- `group`: UI category.
- `shortcodes`: GitHub/gemoji shortcodes when available.
- `keywords`: search tokens from names, shortcodes, groups, plus a small local alias layer.
- `favorite`: user-starred state.
- `recent_rank`: zero-based recency rank, or `null`.
- `recent_count`: insertion count.

## State Files

State directory:

```text
~/.local/state/hypr-emoji-picker/
```

Files:

```text
favorites.json
recents.json
```

Override for tests:

```sh
HYPR_EMOJI_PICKER_STATE=/tmp/some-dir hypr-emoji-picker ...
```

Favorites are explicit starred emoji and are separate from recents. Inserting or copy-inserting an emoji updates recents. `favorite toggle <emoji>` is safe for a UI star button.

## Insertion Behavior

Normal insertion:

```sh
hypr-emoji-picker insert "🚀"
```

Behavior:

- Validate that the emoji is known to the `emojis` crate.
- Save the current text clipboard with `wl-paste --no-newline`.
- Copy the selected emoji with `wl-copy`.
- Paste into the active window with:

```sh
hyprctl dispatch sendshortcut CTRL,V,activewindow
```

- Fall back to `wtype <emoji>` if Hyprland paste dispatch fails.
- Restore the previous text clipboard after a short delay.
- Record the emoji in recents.

Safe copy-only insertion:

```sh
hypr-emoji-picker insert "🚀" --copy-only
```

This copies the emoji and records it as recent, but does not paste into the focused app. Use this for non-invasive tests or as a UI fallback.

## Verification Already Run

Build and tests:

```text
cargo test --release
3 passed; 0 failed
```

Install:

```text
scripts/build-on-mini hypr-emoji-picker --install
```

Smoke checks with isolated state:

```sh
STATE=/tmp/hypr-emoji-picker-test-state
rm -rf "$STATE"
export HYPR_EMOJI_PICKER_STATE="$STATE"

hypr-emoji-picker search lmao --json --limit 3
hypr-emoji-picker favorite add 😂
hypr-emoji-picker favorite list --json
hypr-emoji-picker insert 🚀 --copy-only
wl-paste --no-newline
hypr-emoji-picker recent --json
hypr-emoji-picker search rocket --json --limit 2
hypr-emoji-picker favorite toggle 😂
hypr-emoji-picker favorite list --json
```

Observed results:

- `search lmao` returns `😂`.
- `favorite add 😂` persists `😂` with `"favorite": true`.
- `insert 🚀 --copy-only` copies `🚀` to the clipboard.
- `recent --json` shows `🚀` with `recent_rank: 0` and `recent_count: 1`.
- `favorite toggle 😂` removes it from favorites.

## UI Work For Claude

Build the polished UI as a separate layer over this backend. Recommended UI behavior:

- Small macOS-style floating popup on the focused monitor.
- Search field focused immediately.
- Sections for Favorites, Recents, and matching results.
- Favorite/star control per emoji.
- Keyboard navigation and mouse selection.
- `Enter` calls `hypr-emoji-picker insert <emoji>`.
- Star toggle calls `hypr-emoji-picker favorite toggle <emoji>`.
- Search calls `hypr-emoji-picker search "$query" --json --limit 80`.
- Initial render should load:

```sh
hypr-emoji-picker favorite list --json
hypr-emoji-picker recent --json
```

Do not reimplement emoji search, favorites, recents, or insertion in the UI. Keep those delegated to the Rust backend.

## Open Item: Trigger Key

The Globe/Fn key has still not been captured. Do not guess it.

Before adding a permanent Hyprland bind, capture live keyboard events:

```sh
hyprctl -j devices | jq '.keyboards[] | {name,main,active_keymap}'
sudo libinput debug-events --show-keycodes
```

If Globe/Fn is visible to Hyprland, bind the actual discovered key. If it is not visible, use `Super+Period` as the fallback.
