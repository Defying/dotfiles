# Ghostty Glass Backdrop Prototype

Status: disabled on 2026-06-05.

Prototype file:

- `scripts/ghostty-glass-backdrop.py`

What it tried:

- Create a transparent GTK/Cairo floating backdrop window behind each floating Ghostty window.
- Let Hyprland blur the backdrop instead of using `decoration:screen_shader`.
- Keep terminal text out of the shader/refraction path.

Why it was disabled:

- The backdrop is a separate Hyprland window.
- During floating-window drag, Ghostty moves immediately and the backdrop follows on the next daemon poll.
- Even with one daemon, a runtime lock, duplicate cleanup, and a 33 ms poll, the blur visibly lags behind the terminal.

Config that was reverted:

- Removed `exec-once = /home/ben/dotfiles/scripts/ghostty-glass-backdrop.py --daemon`.
- Removed the `ghostty-glass-backdrop:*` Hyprland window rules.
- Restored Ghostty launch commands without `--gtk-single-instance=false`.
- Restored Ghostty `background-opacity = 0.05`.

Potential future direction:

- Do not use a separate normal window for a draggable terminal.
- Either accept non-refractive compositor blur on Ghostty itself, switch terminal/toolkit, or find a compositor/client-side path where the background effect is attached to the Ghostty surface rather than chased by another window.
