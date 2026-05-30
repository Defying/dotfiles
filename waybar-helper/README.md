# waybar-helper

Small, fast-starting Rust replacements for waybar custom modules that otherwise
re-spawn a Python interpreter every poll tick. std-only (no crates).

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
- `waybar-helper autohide` — fullscreen Waybar autohide daemon. Drop-in for
  `hypr-waybar-autohide.py`; listens on Hyprland socket2 and only polls the
  cursor while the active workspace has a fullscreen window.

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
