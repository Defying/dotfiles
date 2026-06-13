#!/usr/bin/env python3
"""Render dense portrait 4x6 hotkey cheat-sheet labels for tmux and Hyprland.

Packed deliberately: small text, tight margins, two columns, as many real
bindings as fit. Keys on the left (mono, with ⌘ ⇧ ⌃ ⌥ symbols), action on the
right. Lists are kept in sync by hand with config/tmux/tmux.conf and
config/hypr/hyprland.conf — verify there before editing.

Usage: render-portrait-label-cheatsheets.py [tmux|hyprland|both]
"""
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

OUT_DIR = Path.home() / "Documents"
PAGE_W = 4 * inch   # portrait: 4 wide
PAGE_H = 6 * inch   #          6 tall
MARGIN = 9
GUTTER = 12

FONTS = {
    "Title": Path("/usr/share/fonts/google-noto/NotoSans-ExtraBold.ttf"),
    "Head": Path("/usr/share/fonts/google-noto/NotoSans-Bold.ttf"),
    "Action": Path("/usr/local/share/fonts/apple/SF-Pro/SF-Pro.ttf"),
    "Key": Path("/usr/share/fonts/adwaita-mono-fonts/AdwaitaMono-Bold.ttf"),
}

INK = colors.HexColor("#16191f")     # near-black body text
SUB = colors.HexColor("#3a4150")     # action text / subtitle
RULE = colors.HexColor("#e2e6ec")    # hairlines

# Tight vertical metrics — the whole point is to cram. ROW_STEP is computed
# per sheet so the busier column stretches to fill the page (clamped tight).
HEAD_STEP = 13.5
SECTION_GAP = 4
KEY_FS = 7.4
ACT_FS = 7.4
HEAD_FS = 8.0


def register_fonts():
    for name, path in FONTS.items():
        pdfmetrics.registerFont(TTFont(name, str(path)))


def fit(c, text, font, size, max_w, floor=5.6):
    while size > floor and c.stringWidth(text, font, size) > max_w:
        size -= 0.1
    return size


def draw_column(c, col_x, col_w, top, sections, row_step):
    key_x = col_x + 2
    act_x = col_x + col_w * 0.46
    key_w = act_x - key_x - 3
    act_w = col_x + col_w - act_x - 1
    y = top
    for label, items in sections:
        c.setFillColor(c._accent)
        c.setFont("Head", HEAD_FS)
        c.drawString(col_x, y, label.upper())
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(col_x, y - 3.5, col_x + col_w, y - 3.5)
        y -= HEAD_STEP
        for key, action in items:
            c.setFillColor(INK)
            ks = fit(c, key, "Key", KEY_FS, key_w)
            c.setFont("Key", ks)
            c.drawString(key_x, y, key)
            c.setFillColor(SUB)
            as_ = fit(c, action, "Action", ACT_FS, act_w)
            c.setFont("Action", as_)
            c.drawString(act_x, y, action)
            y -= row_step
        y -= SECTION_GAP
    return y


def draw_sheet(out_path, title, subtitle, accent, left_col, right_col):
    c = canvas.Canvas(str(out_path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(title)
    c._accent = accent

    c.setFillColor(colors.white)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#d4d9e0"))
    c.setLineWidth(1)
    c.roundRect(4, 4, PAGE_W - 8, PAGE_H - 8, 8, fill=0, stroke=1)

    top = PAGE_H - MARGIN
    left = MARGIN
    full_w = PAGE_W - 2 * MARGIN

    # Compact header: title left, optional note right, accent rule under both.
    c.setFillColor(INK)
    c.setFont("Title", 17)
    c.drawString(left, top - 14, title)
    if subtitle:
        c.setFillColor(SUB)
        c.setFont("Action", 8.5)
        c.drawRightString(left + full_w, top - 13, subtitle)
    rule_y = top - 20
    c.setStrokeColor(accent)
    c.setLineWidth(1.8)
    c.line(left, rule_y, left + full_w, rule_y)

    content_top = rule_y - 11
    col_w = (full_w - GUTTER) / 2
    left_x = left
    right_x = left + col_w + GUTTER

    # Stretch row spacing so the busier column just fills the page. The column
    # needing the most height per row sets the step; clamp it tight either way.
    avail = content_top - MARGIN
    def fill_step(sections):
        nh = len(sections)
        nr = sum(len(items) for _, items in sections) or 1
        return (avail - nh * HEAD_STEP - nh * SECTION_GAP) / nr
    row_step = min(fill_step(left_col), fill_step(right_col))
    row_step = max(11, min(17, row_step))

    # Faint divider down the gutter.
    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.line(left + col_w + GUTTER / 2, MARGIN, left + col_w + GUTTER / 2, rule_y - 4)

    draw_column(c, left_x, col_w, content_top, left_col, row_step)
    draw_column(c, right_x, col_w, content_top, right_col, row_step)

    c.showPage()
    c.save()
    return out_path


# ── tmux (prefix = Ctrl Space, per config/tmux/tmux.conf) ──────────────────────
TMUX = dict(
    out=OUT_DIR / "tmux-cheatsheet-4x6.pdf",
    title="tmux",
    subtitle="prefix = Ctrl Space  (= Pfx)",
    accent=colors.HexColor("#1f7fb0"),
    left=[
        ("Session", [
            ("Pfx r", "reload config"),
            ("Pfx c", "new window"),
            ("Pfx d", "detach"),
            ("Pfx $", "rename session"),
            ("Pfx s", "session list"),
            ("Pfx :", "command prompt"),
            ("Pfx ?", "list keys"),
        ]),
        ("Splits", [
            ("Pfx |", "split right"),
            ("Pfx -", "split down"),
            ("Pfx x", "kill pane"),
            ("Pfx &", "kill window"),
            ("Pfx z", "zoom pane"),
            ("Pfx Space", "next layout"),
        ]),
        ("Panes (vim)", [
            ("Pfx h j k l", "focus L D U R"),
            ("Pfx H J K L", "resize (hold)"),
            ("Pfx o", "cycle panes"),
            ("Pfx q", "show numbers"),
            ("Pfx { }", "swap pane"),
            ("Pfx !", "pane → window"),
            ("Mouse", "select / resize"),
        ]),
    ],
    right=[
        ("Windows", [
            ("Alt 1-5", "window N"),
            ("Alt h / l", "prev / next"),
            ("Pfx n / p", "next / prev"),
            ("Pfx w", "window list"),
            ("Pfx ,", "rename window"),
        ]),
        ("Copy (vi)", [
            ("Pfx Enter", "copy mode"),
            ("v", "start select"),
            ("V", "select line"),
            ("y", "copy → wl-copy"),
            ("Esc", "cancel"),
            ("Pfx ]", "paste"),
        ]),
        ("Defaults", [
            ("mouse on", "scroll / select"),
            ("set-clip", "OSC52 copy"),
            ("base-index", "1-indexed"),
            ("history", "50000 lines"),
            ("Pfx t", "clock"),
        ]),
    ],
)

# ── Hyprland (mainMod = Super = ⌘, per config/hypr/hyprland.conf) ───────────────
HYPR = dict(
    out=OUT_DIR / "hyprland-cheatsheet-4x6.pdf",
    title="Hyprland",
    subtitle="",
    accent=colors.HexColor("#7a4fb8"),
    left=[
        ("Apps & Session", [
            ("⌘ Space", "launcher"),
            ("⌘⌥ Space", "command menu"),
            ("⌘ Return", "terminal"),
            ("⌘ E", "files"),
            ("⌘ W", "close window"),
            ("⌘⇧ Q", "exit Hyprland"),
            ("⌘ Esc", "power menu"),
            ("⌘ K", "keybinds list"),
            ("⌘⇧ /", "doctor"),
        ]),
        ("Edit (mac-style)", [
            ("⌘ C / V", "copy / paste"),
            ("⌘ X / A", "cut / all"),
            ("⌘ Z", "undo"),
            ("⌘⇧ C / V", "term copy/paste"),
            ("⌘⌃ V", "clipboard hist"),
        ]),
        ("Window", [
            ("⌘ ←↑↓→", "focus"),
            ("⌘⇧ ←↑↓→", "move / swap"),
            ("⌘⌥ ←↑↓→", "resize"),
            ("⌘ F", "fullscreen"),
            ("⌘⌥ F", "maximize"),
            ("⌘ T", "float"),
            ("⌘ J", "toggle split"),
            ("⌘ P", "pseudo"),
        ]),
    ],
    right=[
        ("Workspaces", [
            ("⌘ 1-0", "switch"),
            ("⌘⌃ 1-0", "move window"),
            ("⌘⌃⌥ 1-0", "move silent"),
            ("⌘ Tab", "next ws"),
            ("⌘⇧ Tab", "prev ws"),
            ("⌥ Tab", "cycle windows"),
            ("⌘ S", "scratchpad"),
            ("⌘⌥ S", "→ scratchpad"),
        ]),
        ("Screenshot", [
            ("⌘⇧ 3", "full"),
            ("⌘⇧ 4", "region"),
            ("⌘⇧ 5", "edit"),
        ]),
        ("Media & System", [
            ("⌘ L", "lock"),
            ("⌘ , / ⇧,", "dismiss / all"),
            ("⌘⌃ A", "audio mixer"),
            ("⌘⌃ W", "network"),
            ("⌘⇧ B", "auto-bright"),
            ("⌘ . / Globe", "emoji picker"),
        ]),
        ("Wallpaper", [
            ("⌘⇧ A", "aerial toggle"),
            ("⌘⇧ [ ]", "prev / next"),
            ("⌘⇧ F", "favorite"),
            ("⌘⇧ M", "favs only"),
            ("⌘⇧ N", "black bg"),
            ("⌘⇧ R", "reset bg"),
        ]),
    ],
)


def main():
    register_fonts()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    targets = {"tmux": [TMUX], "hyprland": [HYPR], "both": [TMUX, HYPR]}[which]
    for cfg in targets:
        out = draw_sheet(cfg["out"], cfg["title"], cfg["subtitle"],
                         cfg["accent"], cfg["left"], cfg["right"])
        print(out)


if __name__ == "__main__":
    main()
