# macOS-Parity Initiative — Research & Build Plan

**Date:** 2026-05-28
**Goal:** Make the Asahi/Hyprland setup feel as clean, smooth, and native as macOS — without
sacrificing system efficiency (this is an M1 Air on battery; CPU-only client rendering due to the
Aquamarine GPU bug, so every always-on poller and animation has a real power cost).

This document is **research + ranking only**. Implementation happens via the `/goal` prompt at the
bottom, which reads this file. Each task below records: what the user asked, what I found in the
code, the root cause, the proposed approach, the efficiency impact, and an effort estimate.

Ranking is **mine**, by (importance × ease). Tiers run from "do first" to "biggest builds." The
user's own "(urgent)" tags are preserved in each entry but did not solely drive the order — a few
"urgent" items are genuinely trivial and a few unmarked ones are high-impact.

---

## Efficiency ground rules (apply to every task)

- **No new busy-poll loops.** Anything that must watch state should be event-driven (evdev,
  Hyprland IPC socket `socket2`, inotify) or piggyback on an existing waybar interval. The one
  place a short poll is unavoidable (waybar-autohide cursor tracking) must gate itself behind
  "only poll while fullscreen."
- **Prefer transient systemd timers over daemons** for one-shot future events (AI reset
  notifications). They cost nothing until they fire.
- **brightnessctl/sysfs writes are cheap; `sleep`-loops in shell are not free** but are acceptable
  for sub-second one-shot fades. Keep fade step counts bounded (≤ ~12) and durations short.
- **Shaders run every frame.** OSD/blur tweaks must not add passes; only adjust constants.
- Keep the existing "frosted glass = Hyprland layer blur + translucent GTK fill" model. Never
  substitute `layerrule blur` for content the GTK side is supposed to draw (established rule).

---

## Tier 0 — Trivial (minutes each, do first)

### 0.1 Network indicator: drop "bps", show `0.0kbps/mbps` *(user: urgent)*
- **Found:** `scripts/waybar-sysmon.py` `fmt_rate()` emits `123bps`, `12Kbps`, `1.2Mbps`. User
  wants no raw bps and a consistent decimal like `0.0kbps`.
- **Approach:** Rewrite `fmt_rate()` to always use one decimal and lowercase units, flooring at
  kbps: `<1 kbps → "0.0kbps"`, else `kbps`/`mbps`/`gbps` with `%.1f`. Adjust the `:>9` field width
  to match the new longest string so the bubble stays fixed-width (it currently relies on width to
  avoid reflowing the centered group).
- **Efficiency:** Zero — pure formatting.
- **Effort:** ~5 min.

### 0.2 Ghostty text a bit smaller *(user: urgent)*
- **Found:** `config/ghostty/config` sets no `font-size`, so it uses Ghostty's default (13).
- **Approach:** Add `font-size = 11` (one line). Tune to 10–12 to taste.
- **Efficiency:** Zero.
- **Effort:** ~1 min.

### 0.3 Re-add volume tick sound
- **Found:** `scripts/volume-osd.sh` `play_tick()` is a deliberate no-op (the freedesktop
  `audio-volume-change` sample was called "rough"). `/usr/share/sounds/freedesktop/stereo/
  audio-volume-change.oga` exists.
- **Approach:** Re-enable `play_tick` with a quiet `paplay` (e.g. `--volume=20000`) backgrounded +
  `disown`, fired only on `up`/`down` (not on every repeat key — or it'll machine-gun). Consider
  rate-limiting to the discrete step, or accept the canberra sample. If the sample is still
  disliked, ship a short click `.ogg` into `assets/` and point at it.
- **Efficiency:** One detached `paplay` per volume keypress — negligible.
- **Effort:** ~10 min.

---

## Tier 1 — Urgent, high daily impact, low–medium effort

### 1.1 macOS-feeling hotkeys (cmd vs ctrl confusion in helium) *(user: urgent)*
- **Found:** Hyprland already maps `Super+C/V/X/A/Z/Shift+C/Shift+V` to their Ctrl equivalents via
  `sendshortcut`/`hypr-mac-shortcut` (hyprland.conf:263-269). **Not** mapped: browser/editor
  chords the user actually trips on — `Cmd+T` (new tab), `Cmd+W` (close tab), `Cmd+L` (address
  bar), `Cmd+R` (reload), `Cmd+Shift+T` (reopen tab), `Cmd+F` (find), `Cmd+1..9` (tab switch).
- **Conflict (the hard part):** `Super+T` is currently `togglefloating`, `Super+W`/`Super+Q` are
  `killactive`, `Super+F` is `fullscreen`, `Super+1..9` are workspace switches. Blindly sending
  Ctrl+T to every window would clobber these WM bindings everywhere.
- **Approach (recommended):** Window-class–scoped behavior. Two options:
  1. **Submap-free dispatch script:** a thin `hypr-cmd-key <key>` helper bound to the Super
     chords that checks `hyprctl activewindow` class; if it's a browser/Electron/editor class,
     `hyprctl dispatch sendshortcut CTRL,<key>,activewindow`; otherwise run the WM action
     (togglefloating, killactive, etc.). Centralizes the cmd→ctrl translation. Adds one cheap
     `hyprctl` call per press.
  2. **Relocate the conflicting WM binds** (move floating to `Super+Shift+F`, etc.) and make
     `Super+T/W/L/R/F` pure `sendshortcut` globally. Simpler but changes existing muscle memory.
  - Recommend **option 1** — it preserves WM keys on the desktop and only "becomes mac" inside
    apps. Confirm the helium/browser window class first (`hyprctl activewindow`), likely
    `net.imptools.Helium` / `helium` / something Chromium-ish; XWayland apps report differently.
- **Efficiency:** One `hyprctl` query per mapped keypress — trivial.
- **Effort:** medium (design + class detection + don't-break-existing-binds testing).

### 1.2 New windows should focus + move cursor to them *(user: urgent)*
- **Found:** `misc { focus_on_activate = true }` and `cursor { warp_on_change_workspace = 1 }` are
  set. New tiled windows *do* get keyboard focus in Hyprland by default, but the **cursor does not
  warp to them** — so `follow_mouse = 1` can immediately yank focus back to whatever is under the
  stationary pointer, which feels like "the new window didn't focus."
- **Approach:** Enable cursor warping to new/focused windows. Hyprland exposes
  `cursor { no_warps = false }` (default) but the relevant knob is per-focus warping — use the
  `bind`-side `movecursor`/`focuswindow` or set `general:no_focus_fallback` and, most directly,
  add a windowrule-free global: there is no single "warp to new window" toggle in 0.55, so the
  reliable path is a tiny `exec`-on-open hook. Two routes:
  1. Bind the window-spawning keys (`Super+Return`, launcher launch) to also dispatch
     `hyprctl dispatch movecursortocorner` / `cursorpos` onto the new window after it maps.
  2. Simpler: set `cursor { warp_on_toggle_special = 1 }` won't help; instead listen on the
     Hyprland `openwindow` event (socket2) in an existing helper and warp the cursor to the new
     window's center via `hyprctl dispatch movecursor`. The `waybar-hover-refresh.py` /
     `workspace-osd.py` daemons already tail socket2 — piggyback there, no new process.
  - Verify whether simply setting `follow_mouse = 2` (focus follows but click required to refocus)
    or `focus_on_activate` already suffices once cursor warps. Test the launcher path specifically
    (GTK layer-shell may return focus oddly on close).
- **Efficiency:** Reuse an existing socket2 listener — no new daemon.
- **Effort:** medium (needs live testing of focus/warp interaction with `follow_mouse`).

### 1.3 Schedule a mako notification for AI reset after hitting 0% *(user: urgent)*
- **Found:** `waybar-openai-tokens.py` (and the Claude twin) have `maybe_notify()` which fires once
  on transition into the critical level — but nothing tells the user **when the limit comes back**.
  Both scripts already parse `resetsAt` epochs for the blocking window.
- **Approach:** When a window hits 0%/blocked, schedule a **one-shot transient systemd user timer**
  to fire a mako notification at `resetsAt` ("Codex weekly limit reset — you're back to 100%").
  - `systemd-run --user --on-calendar="<ISO of resetsAt>" --timer-property=AccuracySec=30s
    notify-send ...` (or `--on-active=<seconds-until>`).
  - **Dedupe:** the script polls every 300s; store the scheduled `resetsAt` epoch + a stable unit
    name (`ai-reset-codex.timer`) in the existing `STATE_DIR` notify state. Only (re)create the
    timer if the stored epoch differs. Cancel/replace if `resetsAt` moves.
  - Distinguish session (5h/primary) vs weekly (secondary) in the message, matching whichever
    window is the one at 0%.
- **Efficiency:** Best-case design — the timer is dormant (zero cost) until it fires once. No
  polling added beyond the existing 300s waybar tick.
- **Effort:** medium (shared helper used by both codex + claude scripts).

### 1.4 Helium vertical scroll broken inside the page *(user: urgent)*
- **Found:** Touchpad config: `natural_scroll = true`, `scroll_factor = 0.35`,
  `clickfinger_behavior = true`. `~/.config/helium/config.json` is essentially empty; there's a
  README. Helium is Chromium-based.
- **Hypotheses to test (in order):**
  1. Helium running under **XWayland** with broken high-resolution/axis scroll — check
     `hyprctl clients` for `xwayland: 1`. If so, force Wayland via flags
     (`--ozone-platform=wayland --enable-features=UseOzonePlatform`) in its launcher/desktop file
     (the global env already sets `OZONE_PLATFORM=wayland`, but Helium may ignore it).
  2. `scroll_factor = 0.35` makes vertical deltas so small that Chromium's smooth-scroll
     threshold eats them — test by temporarily raising to `1.0` in a Helium-only context, or
     enable `#enable-smooth-scrolling` / disable it.
  3. Chromium "overlay scrollbars"/touchpad pixel-scroll feature mismatch — try
     `--disable-features=...` toggles.
- **Approach:** Reproduce live, identify which of the above, then pin the fix in Helium's launch
  flags or a small wrapper. This is **investigate-first**; the fix itself is likely one flag.
- **Efficiency:** N/A (config/flag).
- **Effort:** medium (debugging-bound, not code-bound).

### 1.5 Launcher icon alignment *(unmarked, but cheap + very visible)*
- **Found:** `scripts/liquid-launcher.py` builds each row as an HBox with `spacing=12`; icons load
  via `_load_icon()` at 28px, but the **fallback** path and themed-icon path don't all enforce a
  fixed widget size, and `Gtk.Image` from a pixbuf sizes to the pixbuf — themed icons that resolve
  at non-28px or missing icons leave the text labels starting at different x offsets, so names
  don't line up between rows.
- **Approach:** Force every icon widget to a fixed `set_size_request(28, 28)` **and**
  `set_halign(CENTER)/set_valign(CENTER)`, and always return a sized container (even for the
  fallback), so the label column starts at a constant x for every row. Optionally wrap the icon in
  a fixed-width `Gtk.Box`. Pixbufs should be scaled to fit 28×28 preserving aspect.
- **Efficiency:** Zero (one-time layout at launcher open).
- **Effort:** low–medium (~30 min, GTK fiddling).

---

## Tier 2 — Polish, medium effort

### 2.1 OSDs not transparent enough + purple flash on fade *(applies to most panes)*
- **Found:** `scripts/liquid-osd.py` draws its background with cairo
  `set_source_rgba(0.04, 0.06, 0.10, 0.55)` and its embedded shader bakes a purple tint
  (`mix(glassColor, vec3(0.74,0.52,0.95), 0.035)`); the standalone `shaders/liquid-osd.frag` does
  the same. The **purple flash on fade** is most likely Hyprland's `layersIn/layersOut` fade
  animation revealing the tinted glass before/after content settles, combined with the relatively
  opaque 0.55 background.
- **Approach:**
  1. Lower the OSD background alpha (0.55 → ~0.40) and the white veil for more transparency.
  2. Cut or remove the purple tint mix (0.035 → ~0.01 or 0) to kill the purple cast.
  3. Address the flash: soften/shorten the `layersOut` fade for the `liquid-osd` namespace, or
     ensure the GTK window is painted (RGBA visual already requested at line 296) before the layer
     maps — possibly add a brief opacity ramp in the cairo draw rather than relying on Hyprland's
     layer fade. Test which surface the purple comes from (shader tint vs layer animation).
  - Apply the same alpha/tint reduction consistently to the other panes that share the look
    (quick-settings, weather-popup, notification-panel) since the user said "most panes."
- **Efficiency:** No new passes; only constant changes. Neutral-to-better.
- **Effort:** medium (visual iteration).

### 2.2 Brightness smoother (but stay efficient) *(macOS-like)*
- **Found:** `scripts/volume-osd.sh` `fade_brightness()` uses 3 steps over 45 ms (linear). The
  idle dim uses `hypr-brightness-fade.sh` with 28 steps. Stepwise on a quick tap looks chunky.
- **Approach:** Increase the keypress fade to ~8–10 steps over ~120–160 ms with a perceptual
  curve. Brightness perception is logarithmic, so a linear sysfs ramp looks fast-then-slow; use an
  ease (e.g. cubic) or step in perceptual space. Keep total steps bounded (≤12) so it stays cheap.
  Hold-to-repeat already re-reads live brightness each press, so overlapping fades stay correct —
  just make sure background fades from a prior press are superseded (kill the previous fade PID, or
  keep steps short enough they finish before the next repeat at `repeat_rate=40`).
- **Efficiency:** sysfs writes are cheap; bounded step count keeps a tap fade well under a few ms
  of CPU. The explicit "do not make it inefficient" constraint is respected by capping steps.
- **Effort:** low–medium.

### 2.3 Screen fades to idle slowly like macOS
- **Found:** `hypridle.conf`: 300s → dim to 10% (smooth, 28-step fade, battery-only); 600s → lock;
  900s → `dpms off` (instant black). The instant dpms is the un-macOS part.
- **Approach:** Before the 900s `dpms off`, run a short brightness fade-to-near-zero (reuse
  `hypr-brightness-fade.sh dim 1 <secs>`) so the panel eases down, *then* dpms off. On resume,
  dpms on + restore. macOS fades the whole display; brightness ramp is the cheapest faithful
  approximation (no compositor gamma animation needed). Keep it battery-aware via the existing
  `hypr-on-battery` wrapper.
- **Efficiency:** One bounded shell fade at idle transition — negligible, and it's already the
  pattern used at 300s.
- **Effort:** low–medium.

### 2.4 Accurate Claude & OpenAI logos in AI bubbles
- **Found:** `assets/claude.png` (604 B) and `assets/openai.png` (732 B) are tiny/inaccurate;
  referenced from `waybar/style.css` as 15×15 `background-image`. SVG versions exist but are also
  hand-rough.
- **Approach:** Source the official marks — Anthropic's Claude "sunburst/asterisk" glyph and
  OpenAI's blossom — as clean SVGs, render to crisp PNGs at ~2× the display size (30×30 for a
  15px slot) for retina sharpness, drop into `assets/`, keep the same filenames so CSS is
  unchanged. Use monochrome/white-friendly versions so they read on the dark bubble. (These are
  widely published brand assets; recreate the paths rather than copying trademarked files verbatim
  if precise originals aren't to hand.)
- **Efficiency:** Zero (static images).
- **Effort:** low–medium (asset sourcing/rendering).

### 2.5 Update + print both 4×6" PDF shipping labels (tmux + hyprland) *(user: urgent)*
- **Found:** `docs/tmux-cheatsheet.svg`, `docs/hyprland-cheatsheet.svg`, and
  `scripts/render-hyprland-label-cheat-sheet.py` exist. The bindings in these must be reconciled
  with the **current** `tmux.conf` (prefix `C-Space`, `|`/`-` splits, `M-1..5` windows, vi copy)
  and `hyprland.conf` (Super-based, the new mac-key changes from 1.1 once landed).
- **Approach:** Update both SVG/source cheatsheets to match current configs (do this **after** the
  hotkey work in 1.1 so the label reflects final keys), render each to a 4×6" PDF (102×152 mm) via
  the existing render script / `rsvg-convert` / `cairosvg`, then print with `lp -o
  media=Custom.4x6in` (confirm printer + `lpstat -p`). If no printer is configured, produce the
  PDFs and tell the user the exact `lp` command to run.
- **Efficiency:** N/A (one-shot).
- **Effort:** medium — **sequence after 1.1** so keys are final.

### 2.6 Useful zsh MOTD
- **Found:** `.zshrc` has a custom vcs_info prompt but no MOTD/login banner.
- **Approach:** A lightweight, **interactive-login-only** MOTD (guard on `[[ -o interactive ]]` and
  a once-per-login sentinel so it doesn't run on every subshell/tmux pane). Show genuinely useful,
  cheap-to-compute lines: hostname/uptime, battery % + charging state (read the sysfs the waybar
  battery uses), AI usage one-liner (read the cached waybar AI state files — do **not** spawn the
  codex app-server from the prompt), pending dnf updates count (cached, not a live refresh), git
  repos with uncommitted dotfiles. Keep total cost to a few file reads; absolutely no network or
  subprocess-heavy calls in the hot path.
- **Efficiency:** Must be near-instant — read cached files only, gate to login shells. This is the
  main efficiency risk in the list if done naively (e.g. calling `codex`/`dnf` live); avoid that.
- **Effort:** medium.

---

## Tier 3 — Bigger builds

### 3.1 Clicking a bubble opens a menu (AI bubbles first)
- **Found:** AI bubbles use `on-click` → refresh script; detail lives only in the hover tooltip.
  The repo already has the **pattern** for popups: `quick-settings-panel.py`,
  `notification-panel.py`, `weather-popup.py` (GTK layer-shell windows with the glass look).
- **Approach:** Build an AI-usage popup (GTK layer-shell, reuse the quick-settings panel
  scaffolding + CSS) that reads the same rate-limit data the bubble already fetches (read from a
  cached state file the bubble writes, so the click doesn't re-spawn the codex app-server) and
  shows 5h + weekly bars, reset times, credits, and a refresh button. Rebind the bubble `on-click`
  to open it; keep right-click → quick settings. Apply the same menu pattern to other bubbles over
  time.
- **Efficiency:** Read cached data on click; have the bubble's normal 300s poll persist its last
  result to a file for the popup to consume — avoids a second live query.
- **Effort:** medium–high (new GTK window, but pattern exists).

### 3.2 Integrate vnstat into the network bubble dropdown/menu
- **Found:** `vnstat` 2.13 installed, daemon **active + enabled** (DB is being populated). Network
  bubble currently only has a wifi tooltip + `on-click` → `nm-connection-editor`.
- **Approach:** Build (or extend 3.1's menu framework into) a network popup showing: current
  ↓/↑ rates (reuse sysmon's computation), today/month/total from `vnstat --json` (one cheap
  subprocess on open), and top interfaces. Parse `vnstat --json d`/`m`. Bind network `on-click` to
  open it; keep a button to launch `nm-connection-editor`.
- **Efficiency:** `vnstat --json` runs only on menu open — fine.
- **Effort:** medium–high (shares scaffolding with 3.1; build the popup framework once).

### 3.3 Fullscreen → mouse-to-top slides waybar down (YouTube case)
- **Found:** Waybar is `layer: top`; under a fullscreen window it's covered/hidden. No autohide
  logic exists. macOS reveals the menu bar when the cursor hits the top edge.
- **Approach:** A small daemon that, **only while a window is fullscreen** (subscribe to Hyprland
  `fullscreen` events on socket2 — reuse an existing listener), polls cursor Y at a modest rate
  (e.g. 10 Hz) and toggles waybar visibility (`pkill -SIGUSR1 waybar` to hide/show, or move its
  layer) when the cursor is within the top few px; hide again after it leaves + a short delay.
  When no window is fullscreen, the poller is idle (no cursor polling at all).
- **Efficiency:** **The one place with a poll** — strictly gated behind fullscreen state so it's
  dormant during normal use. 10 Hz cursor reads only during fullscreen video is acceptable.
  Document this gate clearly.
- **Effort:** medium–high.

### 3.4 Notification swipe: fix + make bidirectional
- **Found:** `scripts/hypr-trackpad-gestures.py` is a clean evdev daemon but only implements
  **right-edge 2-finger swipe-LEFT → toggle** (it calls `waybar-notifications.sh` which *toggles*).
  Two problems: (a) user says it's "not working" — likely the daemon isn't running or the user
  isn't in the `input` group (the script's own docstring flags this prerequisite); (b) it toggles,
  so to close you must swipe the *same* direction — user wants swipe-left = open, swipe-right =
  close.
- **Approach:**
  1. **Fix "not working":** verify `groups | grep input`, verify the daemon is alive
     (`pgrep -af hypr-trackpad-gestures`), check it found the pad (stderr). Fix group membership
     (`usermod -aG input ben`, needs re-login) if missing.
  2. **Bidirectional:** add a `RightEdgeSwipeRight` detector that explicitly **closes** the tray,
     and split the action so swipe-left = open-only, swipe-right = close-only (call the panel with
     explicit open/close args instead of toggle). The panel script (`waybar-notifications.sh` /
     `notification-panel.py`) needs explicit open/close verbs.
- **Efficiency:** Already event-driven, ~zero at rest. No regression.
- **Effort:** medium.

### 3.5 Idle lock (hyprlock) rebuilt to match the custom login screen
- **Found:** `config/hypr/hyprlock.conf` is plain (blurred wallpaper, time, one hint, input field).
  The **greetd greeter** (`/usr/local/bin/hypr-greeter-app`) is the polished glass GTK card the
  user likes. They should match.
- **Approach:** Restyle `hyprlock.conf` to mirror the greeter's aesthetic — same glass card feel
  (hyprlock supports `shape`/`background`/multiple `label`s; it can't run arbitrary GTK CSS, so
  approximate the card with a rounded translucent `shape` behind the input + matching fonts/colors
  `SF Pro`, the cyan focus accent `#33ccff`, purple `#c084f5`). Pull avatar/user, clock, and a
  hint line consistent with the greeter. It won't be pixel-identical (different toolkits) but can
  read as the same family.
- **Efficiency:** Lockscreen only; irrelevant to steady-state power.
- **Effort:** medium.

### 3.6 Greeter: add a DE switcher (e.g. fall back to Plasma) *(safety feature)*
- **Found:** `/etc/greetd/config.toml` → `hypr-greeter` → `hypr-greeter-app` (GTK, **username
  hardcoded to ben**, `START_CMD = ['/usr/bin/start-hyprland']`). `/etc/greetd/environments`
  already lists the candidate sessions: `start-hyprland`,
  `plasma-dbus-run-session-if-needed startplasma-wayland`, plus logged/recovery/rollback variants.
  `/usr/share/wayland-sessions/` has `hyprland`, `plasma`, and rollback `.desktop` files.
- **Approach:** Add a **session dropdown** to `hypr-greeter-app` (a `Gtk.ComboBox`/`ListBox`)
  populated from the `environments` file (or the wayland-sessions desktop entries), defaulting to
  Hyprland, so if Hyprland breaks the user can pick Plasma from the login screen. Wire the chosen
  command into the greetd `start_session` request instead of the hardcoded `START_CMD`.
  - **Deployment note:** the greeter lives in `/usr/local/bin` (a **system file**), installed via
    `scripts/install-hyprland-system-files.sh`. Edit the source in the repo (find/track the source
    of `hypr-greeter-app`), then reinstall with sudo. Test on a VT you can recover from; keep the
    rollback sessions intact.
- **Efficiency:** Greeter only; irrelevant to steady-state.
- **Effort:** medium–high (touches system files; test carefully).

---

## Suggested build order (dependency-aware)

1. **Tier 0** (0.1–0.3) — quick wins, immediate feel improvement.
2. **1.1 hotkeys** → then **2.5 labels** depend on it (labels must show final keys).
3. **1.2 focus/warp**, **1.3 AI reset notify**, **1.5 launcher icons** — independent, high impact.
4. **1.4 helium scroll** — investigate early (blocks comfortable browser use).
5. **Tier 2 polish** (2.1–2.4, 2.6) — independent; 2.5 after 1.1.
6. **Tier 3 builds** — do **3.1 first** to establish the bubble-menu/popup framework, then **3.2**
   reuses it. **3.4** (swipe) independent. **3.3** (autohide) independent but mind the efficiency
   gate. **3.5/3.6** (lock + greeter) last; 3.6 touches system files.

## Efficiency scorecard (what to watch)

| Task | New steady-state cost | Mitigation |
|------|----------------------|------------|
| 3.3 waybar autohide | cursor poll **only while fullscreen** | gate behind fullscreen event; idle otherwise |
| 2.6 zsh MOTD | risk if it calls codex/dnf live | read cached files only; login-shell guard |
| 1.3 AI reset notify | none (dormant timer) | transient systemd timer, dedupe by epoch |
| 1.1 hotkeys | one `hyprctl` per mapped press | negligible |
| 2.2/2.3 brightness | bounded sub-second shell fades | cap steps ≤12 |
| everything else | none / one-shot | — |

Nothing here adds a persistent busy-loop except the explicitly-gated autohide poller. Net effect on
idle battery should be ~neutral.
