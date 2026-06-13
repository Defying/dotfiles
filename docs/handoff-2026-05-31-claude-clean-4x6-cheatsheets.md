# Claude Handoff: Clean 4x6 Hotkey Cheatsheets

Ben wants simple, readable 4x6 shipping-label cheatsheets for tmux and Hyprland.

Do not overcomplicate this. The output should be a clean cheatsheet, not a dense dashboard, poster, infographic, or decorative design. Prioritize legibility at arm's length.

## Goal

Create and print vertical 4x6 label PDFs:

- tmux hotkeys
- Hyprland hotkeys

Use portrait orientation: 4 inches wide by 6 inches tall.

## Design Direction

- Clean black/white or very restrained color.
- Large title, short section headers, short key/action rows.
- Few sections, no tiny text, no crowded cards.
- Keep each row obvious: `key` on the left, `action` on the right.
- Use actual-size 4x6 layout, not scaled letter/A4.
- Avoid decorative gradients, heavy shadows, nested cards, icons, or visual noise.

This is a practical physical cheatsheet.

## Source Of Truth

Verify hotkeys from current files before rendering:

- `config/tmux/tmux.conf`
- `config/hypr/hyprland.conf`

Existing renderers are landscape and can be reused only as source material:

- `scripts/render-tmux-label-cheat-sheet.py`
- `scripts/render-hyprland-label-cheat-sheet.py`

Prefer adding new portrait scripts or clearly updating the existing scripts. Keep the implementation small.

## Suggested Content

tmux:

- Prefix: `Ctrl Space`
- New window, detach, reload config
- Split right/down
- Pane focus and resize
- Window switching
- Vi copy mode and paste

Hyprland:

- Launcher, terminal, close, lock, power
- Window focus/move/fullscreen/float
- Workspaces switch/move
- Screenshots
- Brightness/audio/network quick controls
- Rescue keys

Trim anything that makes the label crowded.

## Print Target

Printer:

```sh
Shipping_Labels
```

Known device:

```sh
ipp://omens-2.local:631/printers/_PL70e_BT
```

Known media option includes:

```sh
4x6.Borderless
```

After rendering, print actual size. Likely command shape:

```sh
lp -d Shipping_Labels -o media=4x6.Borderless -o fit-to-page=false /path/to/file.pdf
```

If CUPS rejects an option, inspect with:

```sh
lpoptions -p Shipping_Labels -l
lpstat -p -d
```

## Acceptance

- PDFs are portrait 4x6.
- Text is readable and not cramped.
- Hotkeys match the current config files.
- Both PDFs are printed to `Shipping_Labels`.
- Leave the generated PDF paths and the exact print commands in the final note.
