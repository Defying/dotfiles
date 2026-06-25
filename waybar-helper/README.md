# waybar-helper

Small, fast-starting Rust replacements for waybar custom modules that otherwise
re-spawn a Python interpreter every poll tick.

Measured on this machine (M1 / Asahi, CPU-only rendering):

| module | per-run cold start |
|--------|--------------------|
| `waybar-sysmon.py` (Python) | ~29 ms |
| `waybar-helper sysmon` (Rust) | ~1 ms |

At the 2 s `sysmon` interval that's ~0.84 s/min of CPU reclaimed (~1.4% of one
core, continuous) and no 12 MB Python RSS per spawn.

## Subcommands

- `waybar-helper sysmon` — CPU + memory + network bubble. Drop-in for
  `waybar-sysmon.py`; emits the same JSON.
- `waybar-helper clock24` — 24h clock (drop-in for `waybar-clock-24.sh`).
- `waybar-helper clock12` — 12h clock (drop-in for `waybar-clock-12.sh`).
- `waybar-helper date` — date + `cal -3` tooltip (drop-in for `waybar-date.sh`).
- `waybar-helper weather` — wttr.in glyph + temp, with the fuller forecast in
  the tooltip (drop-in for `waybar-weather.sh`). `curl(1)` still does the
  network, but parsing/icon-map/JSON are all in Rust now — the shell version
  shelled out to `python3` purely to JSON-escape the tooltip. Caches the last
  good JSON so a transient curl failure doesn't blank the bar.
- `waybar-helper tailscale` — compact `ts` status for Waybar. Emits `active`,
  `warn`, `auth`, `off`, `missing`, or `error` classes and includes tailnet,
  node IP, peer count, and health details in the tooltip.
- `waybar-helper bambu` — compact Bambu printer progress bubble. Reads local
  cache written by `waybar-helper bambu --daemon` and emits print percentage
  plus `printing`, `paused`, `idle`, `stale`, `missing`, `auth`, or `error`
  classes. The daemon reads local credentials from
  `~/.config/bambu-waybar/config.json`, keeps one secure MQTT session open,
  updates `~/.cache/waybar/bambu-status.json`, and sends Mako notifications
  through `notify-send` for observed print start/finish transitions and sticky
  critical notifications for pause/error states. Use `waybar-helper bambu
  --refresh` for a one-shot manual fetch.
- `waybar-helper autohide` — fullscreen Waybar autohide daemon. Drop-in for
  `hypr-waybar-autohide.py`; listens on Hyprland socket2 and only polls
  compositor state plus cursor position while the active workspace has a
  fullscreen window.
- `waybar-helper autobright` — adaptive backlight from the M1 ALS. Drop-in for
  `hypr-auto-brightness.py`. Sets brightness IN-PROCESS via a direct sysfs write
  (needs the `video` group + 90-backlight-perms.rules) — no spawns, even during
  a fade — falling back to one `busctl` logind `SetBrightness` call only if the
  direct write fails. **Learns your preference:** the log curve is only a prior;
  whenever you set brightness by hand it records that against the current
  ambient-light bucket and EMAs toward it. A bucket's influence grows with how
  many times you've confirmed it (confidence `n/6`, capped), so one nudge barely
  moves the curve but a consistent preference takes it over. The learned model
  is a tiny TSV under `$XDG_STATE_HOME/hypr/auto-brightness-model.tsv` so it
  survives reboots. Toggle off with `Super+Shift+B` (touches
  `~/.cache/hypr/auto-brightness.off`); delete the TSV to reset what it learned.

The clock/date subcommands still shell out to `date(1)`/`cal(1)` (tiny C
programs) for locale/timezone formatting, but drop the per-tick Python that
the old `*.sh` wrappers spawned just to emit JSON.

Not ported: `notifications-count` (now uses `jq` instead of Python — its cost
is the `makoctl` calls, not the wrapper) and the `codex`/`claude` usage
fetchers (network-bound, with retry/notify logic that changes often).

## Build & install

```sh
cargo build --release
install -m755 target/release/waybar-helper ~/.local/bin/waybar-helper
```

Waybar config points at `~/.local/bin/waybar-helper sysmon`; Hyprland launches
`~/.local/bin/waybar-helper autohide`. `/target` is git-ignored; rebuild +
reinstall after changes.
