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

## Build & install

```sh
cargo build --release
install -m755 target/release/waybar-helper ~/.local/bin/waybar-helper
```

waybar config points at `~/.local/bin/waybar-helper sysmon`. `/target` is
git-ignored; rebuild + reinstall after changes.
