# Rust Rewrite Handoff — Phase 3 (AI helpers) — 2026-05-29

Continuation of `docs/claude-code-rust-rewrite-handoff-2026-05-29.md`. That doc
is the original plan/ground-rules; this one is the live state of the in-progress
**Phase 3** (AI usage + account + reminder helpers) port and what to do next.

The machine must stay usable after every step. Waybar, Hyprland startup,
notifications, account switching, and **Apple Reminders** must keep working.

## What's done and committed (on `main`, each live-validated)

Earlier this session (Phases 1–2, all committed and live):
- Exact `-x` Waybar signal matching at every `pkill … waybar` site (incl. the two
  hypridle resume hooks) so signals never hit `waybar-helper`.
- `waybar-helper autohide` (port of hypr-waybar-autohide.py) — live unit.
- `clock12` + `weather` wired into waybar config (weather dropped the
  python-just-to-JSON-escape spawn).
- `waybar-helper autobright` (port of hypr-auto-brightness.py) **+ a learning
  model** (learns preferred brightness per ambient-light bucket, confidence-
  weighted over the log-curve prior, persisted at
  `$XDG_STATE_HOME/hypr/auto-brightness-model.tsv`). Live unit. NOTE: `ben` was
  added to the `video` group on 2026-05-29 so the direct sysfs write path works
  **after the next login**; until then the running unit uses the `busctl` logind
  fallback (functionally correct).

Phase 3 so far (all committed):
1. `serde` + `serde_json` + `chrono` added to the crate. The crate's original
   "std-only, no deps" rule was **deliberately relaxed** (user chose serde) for
   the JSON-heavy usage helpers. `cargo` can reach crates.io here.
2. `src/accounts.rs` — Codex account read/sync (account_from_auth w/ base64url
   JWT-claims decode, active_account, sync_active_slot, save_current,
   display_label, account-cache writer). Secure writes: 0600 files via same-dir
   temp + atomic rename, 0700 dirs. Interactive switcher (fuzzel menu, terminal
   login, `activate`) **stays in Python** (quick-settings / Phase 4). Exposed as
   `waybar-helper codex-account-status`. Validated byte-identical to
   `ai_accounts.py codex-status-json` in a sandboxed HOME (output + cache + perms
   + `diff -r` of the .codex tree).
3. `src/reset.rs` — AI limit-reset reminders (port of ai_reset.py). Dormant
   systemd `--user` timer + detached SSH→Apple Reminder, deduped by service /
   window / epoch / host / list, with the legacy-state migration. `cancel()` is a
   no-op when nothing's scheduled. 4 unit tests for the dedup matrix + an
   `#[ignore]`d integration test (run with `AI_RESET_MINI_HOST=""`) proving the
   timer arms / is idempotent / cancels cleanly.
4. `src/usage.rs` — shared helpers (chrono local-time `fmt_reset`,
   `compact_countdown`/`compact_duration`, `iso_to_epoch`, transition-only
   `maybe_notify`, the Waybar `emit`).
5. `src/codex.rs` — `waybar-helper codex` (display) + `codex --refresh [--signal
   N]`. Display reads cache, kicks a detached background `--refresh` when stale;
   refresh does the app-server JSON-RPC (`account/rateLimits/read`), caches,
   notifies, arms/cancels the reminder. **Wired live** in waybar config
   (`custom/codex-tokens` → `waybar-helper codex`). Validated byte-identical to
   the .py across ~13 cache states + a live app-server refresh; reminder dedup
   confirmed (no duplicate Apple reminder on switch).

## In progress — NOT committed (the current dirty state)

```
M waybar-helper/src/main.rs        # added `mod claude;` + `Some("claude") => claude::run(...)`
?? waybar-helper/src/claude.rs     # NEW, written, compiles, NOT yet clippy-clean or validated
```

`src/claude.rs` is a full port of `waybar-claude-usage.py` (`waybar-helper claude
[--refresh] [--force-network] [--signal N]`). Claude fetches **inline** (no
background spawn, no lock) via **curl** (`%{http_code}` + `%header{retry-after}`,
curl 8.18 supports both) to `https://api.anthropic.com/api/oauth/usage`, with the
token-refresh-on-expiry, 401/403 single retry, 429 backoff (`retry_at` in cache),
and the tricky stale/expired-reset display (the `–` dash, weekly-remaining
fallback, `retry in …`).

### Immediate next steps (do these first, in order)

1. **Fix the one clippy warning** in `claude.rs` (~line 303): `called unwrap on
   any after checking is_some`. In the `if !force_network && any.is_some() &&
   retry_pending` branch, replace `any.is_some()` + `let c = any.unwrap();` with
   an `if let Some(c) = &any` (and compute `retry_pending` from `c`). Then:
   `cargo clippy --release -- -D warnings` must be clean and `cargo test` green.
2. **Validate `claude` display vs Python** using the harness below. Current live
   state is handy: the endpoint is returning **429** (rate-limited), so the
   cached/stale + "Anthropic rate-limited the refresh" path is exercised for real
   and is byte-matchable. Test states: healthy, 5h-exhausted (countdown),
   weekly-exhausted, stale-with-retry, stale-expired-reset (the `–`/weekly-
   fallback), auth (no token), extra_usage enabled.
3. **Validate the refresh/fetch** in a sandbox HOME with copied `~/.claude` and
   `AI_RESET_MINI_HOST=""`. Because the endpoint is 429 right now you can confirm
   the backoff writes `retry_at`/`refresh_error_text` and the bubble matches.
4. **Switch waybar config** `custom/claude-tokens` exec →
   `/home/ben/.local/bin/waybar-helper claude`, reload waybar, screenshot, and
   confirm the reset-timer state file is unchanged (no duplicate reminder).
   Commit the module, then commit the config switch (two small commits).

### Known faithful-port caveat
The **no-cache + network-failure** error path can't byte-match Python's raw
exception text (Python interpolates the urllib exception; Rust has a curl/HTTP
string). Structure matches; this only differs on a degenerate first-run-offline
state. Everything with a cache present (the normal world) matches.

## Validation harness (reuse verbatim — don't re-derive)

Gotchas already paid for:
- Run comparisons from a **non-`/tmp` cwd** (e.g. `/home/ben/dotfiles`): there is
  a stray `/tmp/inspect.py` that shadows stdlib and breaks `python3 -m json.tool`
  when cwd is `/tmp`.
- **Never** pipe captured output through `echo "$x"` — the shell interprets the
  literal `\n` in the JSON and corrupts it. Write outputs to files and compare.
- serde **sorts** JSON keys; Python preserves insertion order. Compare **parsed**
  dicts, not bytes. Waybar doesn't care about key order.
- Normalize the volatile `cached Ns ago` integer (sub-second timing skew between
  the two sequential process launches) before comparing tooltips.

Display comparison (files, parsed, age-normalized):
```sh
cd /home/ben/dotfiles
sb=$(mktemp -d); mkdir -p "$sb/waybar"
printf '%s' "$CACHE_JSON" > "$sb/waybar/claude-usage.json"
XDG_CACHE_HOME="$sb" python3 scripts/waybar-claude-usage.py 2>/dev/null > "$sb/py.out"
XDG_CACHE_HOME="$sb" ~/.local/bin/waybar-helper claude 2>/dev/null > "$sb/rs.out"
python3 - "$sb/py.out" "$sb/rs.out" <<'PY'
import json,sys,re
py=json.load(open(sys.argv[1])); rs=json.load(open(sys.argv[2]))
def n(d):
    d=dict(d)
    if 'tooltip' in d: d['tooltip']=re.sub(r'cached \d+m? ago','cached N',d['tooltip'])
    return d
print("MATCH" if n(py)==n(rs) else f"DIFFER\npy={py}\nrs={rs}")
PY
rm -rf "$sb"
```
Refresh/account sandbox (parallel HOMEs, no real SSH):
```sh
base=$(mktemp -d); for w in py rs; do mkdir -p "$base/$w/.cache/waybar"; cp -a ~/.claude "$base/$w/.claude"; done
HOME=$base/rs XDG_CACHE_HOME=$base/rs/.cache AI_RESET_MINI_HOST="" ~/.local/bin/waybar-helper claude --refresh
```

Build/validate loop:
```sh
cargo fmt --manifest-path waybar-helper/Cargo.toml --check
cargo clippy --release --manifest-path waybar-helper/Cargo.toml -- -D warnings
cargo test --manifest-path waybar-helper/Cargo.toml
cargo build --release --manifest-path waybar-helper/Cargo.toml
install -m755 waybar-helper/target/release/waybar-helper ~/.local/bin/waybar-helper
```

## Remaining after claude

- **Stage 5 — refresh wrapper**: `scripts/waybar-ai-refresh.sh` (middle-click)
  still calls the Python `--refresh`. Point its codex/claude branches at
  `waybar-helper codex|claude --refresh --signal N`. Then the only remaining
  Python consumers of the usage scripts are the popups (Phase 4). Once nothing
  calls them, the `.py` usage scripts can be retired (keep until then).
- **Phase 4 — GTK popups / quick settings** (highest risk, last): glass_popup,
  quick-settings-panel, ai-usage-popup, network/weather/notification popups,
  liquid-launcher/osd. These read the same caches (compatible) and call
  `ai_accounts.py codex-menu` etc. (still Python). Keep the Python UI until a
  GTK4/layer-shell Rust UI can match the tuned visuals; screenshot every change.
  See `[[waybar-helper-std-only-constraint]]` — `hover-refresh.py` (AT-SPI) and
  `workspace-osd.py` (GTK) are **not** clean std/Rust ports.

## Reminder safety rules (do not violate)
- Only ever touch the **"AI Resets"** Apple list. Dedup by service/window/epoch/
  host/list. Never spam. Test reminder code with `AI_RESET_MINI_HOST=""`.
- Keep the cache schema compatible until all consumers (incl. Phase 4 popups)
  are migrated.
- The codex 5h window is currently **exhausted**; `ai-reset-codex.timer` is
  legitimately armed for the real reset — leave it.

## Memory
`~/.claude/projects/-home-ben-dotfiles/memory/` has `migration-progress-2026-05`
and `waybar-helper-std-only-constraint`. Update `migration-progress-2026-05.md`
as stages land.
