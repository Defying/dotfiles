#!/usr/bin/env python3
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


OUT = Path.home() / "Documents" / "hyprland-macbook-cheat-sheet-4x6-legible.pdf"
PAGE_W = 6 * inch
PAGE_H = 4 * inch
MARGIN = 12

FONTS = {
    "SFPro": Path("/usr/local/share/fonts/apple/SF-Pro/SF-Pro.ttf"),
    "SansSemi": Path("/usr/share/fonts/google-noto/NotoSans-SemiBold.ttf"),
    "SansBold": Path("/usr/share/fonts/google-noto/NotoSans-Bold.ttf"),
    "SansBlack": Path("/usr/share/fonts/google-noto/NotoSans-ExtraBold.ttf"),
    "Mono": Path("/usr/share/fonts/adwaita-mono-fonts/AdwaitaMono-Regular.ttf"),
    "MonoBold": Path("/usr/share/fonts/adwaita-mono-fonts/AdwaitaMono-Bold.ttf"),
}


def register_fonts():
    for name, path in FONTS.items():
        if path.exists():
            pdfmetrics.registerFont(TTFont(name, str(path)))


def fit_size(c, text, font, size, max_width, min_size=6.8):
    while size > min_size and c.stringWidth(text, font, size) > max_width:
        size -= 0.15
    return size


def keycap(c, x, y, text, max_width):
    font = "MonoBold"
    size = fit_size(c, text, font, 7.8, max_width - 6, 6.5)
    w = min(max_width, c.stringWidth(text, font, size) + 8)
    h = 13.5
    c.setFillColor(colors.HexColor("#f0e7ff"))
    c.setStrokeColor(colors.HexColor("#7b3fc8"))
    c.setLineWidth(0.55)
    c.roundRect(x, y - 3.2, w, h, 3.2, fill=1, stroke=1)
    c.setFillColor(colors.HexColor("#17111f"))
    c.setFont(font, size)
    c.drawCentredString(x + w / 2, y + 0.9, text)
    return w


def row(c, x, y, key, desc, width):
    key_w = width * 0.50
    used = keycap(c, x, y, key, key_w)
    desc_x = x + max(used + 5, key_w + 2)
    desc_w = width - (desc_x - x)
    size = fit_size(c, desc, "SFPro", 8.7, desc_w, 7.2)
    c.setFillColor(colors.HexColor("#171827"))
    c.setFont("SFPro", size)
    c.drawString(desc_x, y + 0.4, desc)


def card(c, x, y, w, h, title, items):
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.HexColor("#cfc5dc"))
    c.setLineWidth(0.8)
    c.roundRect(x, y, w, h, 8, fill=1, stroke=1)

    c.setFillColor(colors.HexColor("#28163d"))
    c.roundRect(x + 4, y + h - 18, w - 8, 14, 5, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("SansBold", 8.0)
    c.drawString(x + 9, y + h - 14.2, title.upper())

    available = h - 27
    step = min(16.2, available / max(len(items), 1))
    line_y = y + h - 31.5
    for key, desc in items:
        row(c, x + 7, line_y, key, desc, w - 14)
        line_y -= step


def main():
    register_fonts()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUT), pagesize=(PAGE_W, PAGE_H))
    c.setTitle("Hyprland MacBook 4x6 Legible Cheat Sheet")
    c.setAuthor("Codex")

    c.setFillColor(colors.HexColor("#fbf9ff"))
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setStrokeColor(colors.HexColor("#7b3fc8"))
    c.setLineWidth(1.2)
    c.roundRect(5, 5, PAGE_W - 10, PAGE_H - 10, 10, fill=0, stroke=1)

    header_h = 34
    c.setFillColor(colors.HexColor("#15101f"))
    c.roundRect(MARGIN, PAGE_H - MARGIN - header_h, PAGE_W - 2 * MARGIN, header_h, 9, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("SansBlack", 17)
    c.drawString(MARGIN + 11, PAGE_H - MARGIN - 18.5, "HYPRLAND KEYS")
    c.setFont("SFPro", 8.1)
    c.setFillColor(colors.HexColor("#e8d7ff"))
    c.drawRightString(PAGE_W - MARGIN - 11, PAGE_H - MARGIN - 18, "Cmd = Super")
    c.setFont("SFPro", 7.0)
    c.drawString(MARGIN + 12, PAGE_H - MARGIN - 29.2, "13in MacBook • 4x6 label • print actual size")

    gap = 6
    col_w = (PAGE_W - 2 * MARGIN - 2 * gap) / 3
    card_h = 102
    top_y = PAGE_H - MARGIN - header_h - 8 - card_h
    bottom_y = MARGIN + 4

    cards = [
        (
            "Essential",
            [
                ("Cmd Space", "launcher"),
                ("Cmd Return", "terminal"),
                ("Cmd W", "close"),
                ("Cmd L", "lock"),
                ("Cmd Esc", "power menu"),
            ],
        ),
        (
            "Windows",
            [
                ("Cmd F", "fullscreen"),
                ("Cmd T", "float/tile"),
                ("Cmd Arrows", "focus"),
                ("Cmd Shift Arrows", "swap"),
                ("Alt Tab", "next window"),
            ],
        ),
        (
            "Workspaces",
            [
                ("Cmd 1..0", "switch"),
                ("Cmd Ctrl 1..0", "move"),
                ("Cmd Tab", "next"),
                ("Cmd Shift Tab", "previous"),
                ("Cmd S", "scratchpad"),
            ],
        ),
        (
            "Rescue",
            [
                ("Ctrl Alt T", "terminal"),
                ("Alt Return", "terminal"),
                ("F12", "terminal"),
                ("Ctrl Alt Space", "menu"),
                ("Ctrl Alt Q", "exit"),
            ],
        ),
        (
            "Screens",
            [
                ("Cmd Shift 3", "full shot"),
                ("Cmd Shift 4", "region"),
                ("Cmd Shift 5", "edit shot"),
                ("Cmd Ctrl V", "clipboard"),
                ("Cmd ,", "dismiss notif"),
            ],
        ),
        (
            "System",
            [
                ("Vol keys", "audio"),
                ("Bright keys", "display"),
                ("Play keys", "media"),
                ("Cmd Ctrl A", "audio panel"),
                ("Cmd Ctrl W", "network"),
            ],
        ),
    ]

    for idx, (title, items) in enumerate(cards):
        x = MARGIN + (idx % 3) * (col_w + gap)
        y = top_y if idx < 3 else bottom_y
        card(c, x, y, col_w, card_h, title, items)

    c.setFillColor(colors.HexColor("#17111f"))
    c.roundRect(MARGIN, MARGIN - 4, PAGE_W - 2 * MARGIN, 13, 4, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Mono", 6.8)
    c.drawCentredString(PAGE_W / 2, MARGIN + 0.2, "Terminal: hypr-doctor  hypr-proof  hypr-logs  hypr-emergency")

    c.showPage()
    c.save()
    print(OUT)


if __name__ == "__main__":
    main()
