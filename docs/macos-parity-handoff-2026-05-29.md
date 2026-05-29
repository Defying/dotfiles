# macOS-Parity Initiative — Handoff for Codex

**Date:** 2026-05-29
**Predecessor:** built from `docs/macos-parity-2026-05-28.md` (the research/ranking report).
**Author of this session:** Claude (Opus 4.8). This file hands off remaining work.

This session implemented most of the macOS-parity report on Ben's Asahi-Fedora-44 +
Hyprland M1 laptop (CPU-only client rendering due to the Aquamarine GPU bug). **Honor the
efficiency ground rules from the 2026-05-28 report on everything:** no new busy-poll loops
(event-driven via evdev / Hyprland `socket2` / inotify, or piggyback existing waybar
intervals), dormant transient systemd timers for one-shot future events, bound any shell
fade to ≤12 steps, never add shader passes. The only sanctioned poll is the fullscreen
waybar-autohide (gated to fullscreen only).

Dotfiles live in `~/dotfiles`, symlinked into `~/.config`. Commit per task/coherent group
with clear messages; **batch pushes** (push at session end or when Ben asks). Use
passwordless sudo directly for system files. Ben switches between macOS and Linux.

---

## ⚠️ Read first — incidents & system changes this session

1. **hyprlock crash / lockout (resolved).** A restyled hyprlock (task 3.5) crashed the
   locker on a real lock, leaving the session locked with **no locker process** and denying
   new ones ("got yeeten"). Recovery required enabling `misc:allow_session_lock_restore` and
   relaunching hyprlock. **Lessons that are now permanent rules:**
   - `misc:allow_session_lock_restore = true` is set in `hyprland.conf` — a crashed locker is
     now recoverable by just re-running `hyprlock`.
   - **NEVER test a lockscreen with `timeout N hyprlock`** — SIGTERM can leave an orphaned
     session-lock. To validate hyprlock config, do a real lock/unlock cycle, or rely on
     `allow_session_lock_restore` as the safety net.
   - hyprlock is currently the **proven-simple** config (3.5 restyle was reverted). Do not
     re-attempt a styled lock without validating every option against a real lock cycle.
2. **System changes made (sudo):**
   - `ben` added to groups **`input`** (trackpad evdev for gestures) and **`video`**
     (direct backlight sysfs). *Both require a re-login to take effect for the session.*
   - **udev rule** `/etc/udev/rules.d/90-backlight-perms.rules` (tracked at
     `system/etc/udev/rules.d/…`, registered in `scripts/install-hyprland-system-files.sh`):
     makes `apple-panel-bl` + `kbd_backlight` brightness nodes `video`-group-writable so
     brightness daemons write sysfs in-process (no spawns). Until re-login the auto-brightness
     daemon falls back to logind D-Bus (also spawn-free).
3. **23 commits are unpushed** (`main` is ahead of `origin/main` by 23). Push when Ben says.

---

## ✅ Done & verified this session

| Task | What changed | Verify status |
|------|--------------|---------------|
| 0.1 net rate | `waybar-sysmon.py` `fmt_rate()` → `0.0kbps/mbps`, no raw bps | live ✓ |
| 0.2 ghostty | `config/ghostty/config` `font-size = 11` | applies on new windows |
| 0.3 volume tick | `volume-osd.sh` `play_tick` re-enabled (quiet paplay) | ✓ |
| 1.2 focus warp | `workspace-osd.py` socket2 reader also warps cursor to new windows | live ✓ (lands dead-center) |
| 1.3 AI reset notify | `ai_reset.py` + wired into codex/claude bubbles; transient systemd timer at reset | codex weekly timer armed; mako path proven. **Ben: "working, audit later."** |
| 1.4 helium scroll | `hyprland.conf` touchpad `scroll_factor 0.35→1.0` (Chromium high-res scroll) | **needs Ben's eyes** — applied live |
| 1.5 launcher icons | `liquid-launcher.py` fixed 28px centered icon slots | launches clean |
| 2.1 OSD/flash | OSD bg alphas 0.55–0.62→0.42–0.48; blur `vibrancy 0.50→0.25` | **needs Ben's eyes** on the purple flash |
| 2.2 brightness | `volume-osd.sh` fade → 10 ease-out steps/120ms, supersedes prior fade | ✓ up/down nets to start |
| 2.3 idle fade | `hypridle.conf` 900s: fade to ~1% then dpms; new no-save `fade` mode in fade script | ✓ dim→fade→restore correct |
| 2.4 logos | real OpenAI/Claude marks (simple-icons) recolored, 32px PNGs in `assets/` | ✓ rendered, waybar reloaded |
| 2.5 labels | hyprland label keys updated + new `render-tmux-label-cheat-sheet.py`; both 4x6 PDFs printed to `Shipping_Labels` | **needs Ben's eyes** on print size/orientation |
| 2.6 zsh MOTD | `zsh-motd.sh` (host/battery/Claude-cache/git); login + non-tmux gated | ✓ ~instant |
| 3.1 AI popup | `ai-usage-popup.py` glass popup; bubble on-click; codex bubble now caches `codex-usage.json` | ✓ launches; **needs a real click test** |
| 3.2 vnstat menu | `network-popup.py` (today/month/total + live sample); network on-click | ✓ launches |
| 3.3 waybar autohide | `hypr-waybar-autohide.py`: socket2-gated, polls cursor only while fullscreen; SIGUSR1 flips top↔bottom | ✓ all transitions verified |
| 3.4 notif swipe | input group fix + `SwipeRightToEdge` close detector; `waybar-notifications.sh` open/close/toggle verbs | verbs ✓; **gestures need re-login** (input group) |
| 3.8 kbd backlight | `volume-osd.sh` `kbd-up/down` verbs; bound to `Super`+brightness keys | ✓ steps up/down |
| 3.9 auto-brightness | `hypr-auto-brightness.py`: ALS lux→log curve, EMA-smoothed, in-process set (sysfs-or-D-Bus, no spawns), idle/manual-aware; toggle `Super+Shift+B` | ✓ smooth fade, no spawns |
| scale | `hyprland.conf` monitor scale `1.666667→1.6` (1600×1000 logical) | ✓ clean |

New always-on daemons (added to `hyprland.conf` `exec-once`): `hypr-waybar-autohide.py`,
`hypr-auto-brightness.py`. Both idle at rest (event-driven / gated poll).

---

## 🔧 Remaining / blocked work

### 1.1 — macOS-feeling hotkeys (cmd→ctrl in apps)  — **DEFERRED by Ben** ("ignore the keymapping shit for now")
Class-aware Super-key routing so browsers/editors get Ctrl chords without clobbering WM
binds. helium runs native Wayland, class `helium`; `hypr-mac-shortcut` already does
class-aware copy/paste — extend that pattern. **Needs Ben's decision** on which keys
(T/W/R, Shift+T, L, F) and app scope. Do NOT remap `Super+1..9` (workspaces). If/when this
lands, **re-render the 4x6 labels (2.5) so they show final keys.**

### 3.6 — Greeter DE switcher  — **STAGED, NOT INSTALLED (do carefully)**
`system/usr/local/bin/hypr-greeter-app` is edited in the repo (session dropdown: Hyprland
default + Plasma + safe/recovery, filtered by binary presence; `do_auth(pw, cmd, env)`) but
**was never installed to `/usr/local/bin`** after the lock scare — the live greeter/login is
untouched and safe. The repo file has an uncommitted diff. To finish:
- Finish wiring the dropdown widget into the GTK card UI + `_on_login` reading the selection
  (the data/`do_auth` plumbing is done; the widget may need adding/verifying).
- Add combobox CSS to match the glass card.
- **Test on a recoverable path**: keep the greetd rollback/recovery sessions intact; verify
  on a VT you can recover from before relying on it. Install via
  `scripts/install-hyprland-system-files.sh --install` (sudo).
- Commit the repo file once validated.

### 3.7 — SSH → mac mini Apple Reminder  — **BLOCKED on Ben's SSH details**
On hitting 0%, additionally `ssh <mini>` and create an Apple Reminder for the reset time via
`osascript` (Reminders.app `make new reminder with properties {name:…, due date:…}`). Wire it
into `ai_reset.py` (alongside the existing systemd-timer mako notification) **gated behind the
mini being reachable** (short `ssh -o ConnectTimeout=2 -o BatchMode=yes` probe) so it never
blocks the waybar tick. **Needs:** the mini's SSH host/alias + confirmation key auth works.

---

## 🔎 To audit (Ben said "audit later")

- **1.3 AI reset notify** — codex weekly timer armed for the real reset; confirm the mako
  notification actually fires at reset and that codex flips back to showing the 5h % after the
  weekly window resets.
- **3.9 auto-brightness** — the ALS read **1 lux** repeatedly during testing (very dark /
  possibly occluded sensor); confirm the lux→brightness curve feels right in real daylight and
  that it doesn't sit too dim. Tunables at top of `hypr-auto-brightness.py`
  (`LUX_EMA`, `FADE_SECONDS`, `FADE_STEPS`, `DEADBAND_PCT`, curve in `lux_to_pct`).
- **1.4 helium scroll**, **2.1 OSD purple flash**, **2.5 label print** — all need Ben's eyes.
- After Ben's **next re-login**: confirm trackpad gestures (3.4) and direct-sysfs brightness
  (video group) are active.

---

## /goal prompt for Codex (paste into `/goal`)

```
/goal Read /home/ben/dotfiles/docs/macos-parity-handoff-2026-05-29.md in full (and its
predecessor docs/macos-parity-2026-05-28.md for context). This is Ben's Asahi-Fedora-44 +
Hyprland M1 laptop, CPU-only client rendering (Aquamarine bug). Honor the efficiency ground
rules: no new busy-poll loops (event-driven via evdev/socket2/inotify or piggyback existing
waybar intervals), dormant transient systemd timers for one-shot events, shell fades ≤12
steps, no extra shader passes; the only sanctioned poll is the fullscreen waybar-autohide.

FIRST, honor the incident rules in the handoff: keep misc:allow_session_lock_restore=true,
and NEVER validate a lockscreen with `timeout hyprlock` (do a real lock/unlock cycle). The
greeter/login changes are STAGED, not installed — treat login/lock as high-risk and test only
on a recoverable path with the greetd rollback sessions intact.

Then tackle the remaining work, pausing for Ben's input where the handoff marks it:
  1. Task 3.6 (greeter DE switcher): finish the GTK dropdown wiring in the staged
     system/usr/local/bin/hypr-greeter-app, add matching CSS, test on a recoverable VT, then
     install via scripts/install-hyprland-system-files.sh and commit.
  2. Task 1.1 (mac hotkeys) — ASK Ben for the key set + app scope first (he deferred it);
     extend the class-aware hypr-mac-shortcut pattern; never remap Super+1..9. After it lands,
     re-render BOTH 4x6 labels (render-{hyprland,tmux}-label-cheat-sheet.py) so they show final
     keys, and reprint to the Shipping_Labels printer.
  3. Task 3.7 (mac-mini Apple Reminder) — ASK Ben for the mini's SSH host/alias; add a
     reachability-gated osascript Reminders call into ai_reset.py alongside the mako timer.
  4. Work the "To audit" list with Ben: confirm 1.3 notify fires, tune 3.9 auto-brightness
     against real daylight, and get Ben's eyes on 1.4 / 2.1 / 2.5.

Configs are symlinked from ~/dotfiles into ~/.config; reload/restart only the affected
component to verify and report what changed vs. what was already there (don't overclaim).
Commit per task with clear messages; batch pushes (push at the end or when Ben asks). There
are already 23 unpushed commits from the prior session — fold your pushes in with Ben's
go-ahead. Use passwordless sudo directly for system files.
```
