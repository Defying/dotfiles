# Repository Instructions

## Hyprland Work

Anything newly built for the Hyprland desktop should be Rust-based by default.
Use shell/Python/Lua only for glue around existing tools, one-off migrations, or
cases where the runtime must be embedded in an existing program.

## Waybar Click Panels

Do not build new clicked Waybar panels on top of `scripts/glass_popup.py`,
`GlassPopup`, or a full-screen transparent click-away overlay. Treat that path
as legacy unless the user explicitly asks to repair it.

Clicked Waybar items that open UI should be their own dedicated, opaque
layer-shell panels/windows, matching the quick settings and launcher approach:
feature-local lifecycle, feature-local layout, and readable panel backgrounds.

## Hyprland Glass Menus

When the user says the launcher or menus look right and asks for other menu bar
surfaces to match them, treat that as an implementation instruction, not an
open-ended research project. Inspect enough current source and live state to
avoid breaking things, then make the narrow material/rule changes needed for
the affected surfaces to match the reference.

The reference material is a hard-edged translucent card around alpha 0.72-0.74
over Hyprland namespace blur. Do not use broad outer CSS shadows on blurred
layer-shell menus; they expand the alpha mask and create blur halos. Keep only
inset highlights or shadows that render inside the card.

Use exact namespace rules for menu blur and `ignore_alpha`. Do not add broad
prefix rules that accidentally affect unrelated full-screen overlays. Before
editing a Hyprland rule block, re-open the exact current block and patch against
that text instead of assuming stale handoff snippets still match.

If source inspection already proves a menu surface has the same high-opacity
or shadow-mask problem as the reference bug, fix that known mismatch directly.
Do not leave it for a later audit unless live verification contradicts the
source evidence.
