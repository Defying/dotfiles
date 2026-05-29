#!/usr/bin/env python3
"""Tiny workspace OSD for Hyprland.

Listens on Hyprland's socket2 event stream and pops a small layer-shell
overlay showing the active workspace name (e.g. "1") whenever it changes.
Re-uses the existing "liquid-osd" namespace blur, so the panel gets the
frosted background for free.

The same socket2 reader also does double duty: on `openwindow` it warps the
pointer to the centre of the freshly-opened window (only if that window
actually became focused), so a new terminal / any launcher-spawned window
grabs the cursor — with follow_mouse=1, focus follows wherever the pointer
lands. Piggybacking here avoids a second always-on daemon.

Single process, event-driven: the reader blocks on the socket and does no
work between events.
"""

from __future__ import annotations

import json
import math
import os
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, Pango, PangoCairo

POPUP_W   = 160
POPUP_H   = 160
POPUP_R   = 28
BOTTOM_MARGIN = 96
FADE_MS   = 700
PAD       = 18

RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/wsosd-{os.getuid()}"))
INSTANCE = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
SOCK_PATH = RUNTIME / "hypr" / INSTANCE / ".socket2.sock" if INSTANCE else None


# ── Cairo helpers ──────────────────────────────────────────────────────────

def rrect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r,     r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r,  0,            math.pi / 2)
    cr.arc(x + r,     y + h - r, r,  math.pi / 2,  math.pi)
    cr.arc(x + r,     y + r,     r,  math.pi,      math.pi * 1.5)
    cr.close_path()


# ── Window ─────────────────────────────────────────────────────────────────

class WorkspaceOsd(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="workspace-osd")
        self.label = ""
        self.hide_handle: int | None = None

        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_resizable(False)
        screen = self.get_screen()
        if (visual := screen.get_rgba_visual()):
            self.set_visual(visual)

        GtkLayerShell.init_for_window(self)
        # Re-use the existing liquid-osd namespace so the layerrule blur in
        # hyprland.conf applies without adding a new rule.
        GtkLayerShell.set_namespace(self, "liquid-osd")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, BOTTOM_MARGIN)
        GtkLayerShell.set_exclusive_zone(self, 0)

        self.set_default_size(POPUP_W, POPUP_H)
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(POPUP_W, POPUP_H)
        self.area.connect("draw", self._draw)
        self.add(self.area)

    def show_workspace(self, label: str) -> None:
        self.label = label
        self.area.queue_draw()
        if not self.get_visible():
            self.show_all()
        if self.hide_handle is not None:
            GLib.source_remove(self.hide_handle)
        self.hide_handle = GLib.timeout_add(FADE_MS, self._hide)

    def _hide(self) -> bool:
        self.hide_handle = None
        self.hide()
        return False

    def _draw(self, widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()

        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Translucent rounded fill + sheen — layerrule blur paints behind.
        rrect(cr, 0, 0, w, h, POPUP_R)
        cr.set_source_rgba(0.04, 0.06, 0.10, 0.48)
        cr.fill_preserve()
        g = cairo.LinearGradient(0, 0, 0, h)
        g.add_color_stop_rgba(0.0, 1, 1, 1, 0.22)
        g.add_color_stop_rgba(0.5, 1, 1, 1, 0.04)
        g.add_color_stop_rgba(1.0, 0, 0, 0, 0.16)
        cr.set_source(g)
        cr.fill()

        cr.set_source_rgba(1, 1, 1, 0.28)
        cr.set_line_width(1.0)
        rrect(cr, 0.5, 0.5, w - 1, h - 1, POPUP_R)
        cr.stroke()

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription("SF Pro Display 72 Bold"))
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_text(self.label, -1)
        tw, th = layout.get_pixel_size()
        cr.set_source_rgba(1, 1, 1, 0.96)
        cr.move_to((w - tw) / 2, (h - th) / 2 - 4)
        PangoCairo.show_layout(cr, layout)


# ── Hyprland socket2 reader ────────────────────────────────────────────────

def parse_label(payload: str) -> str | None:
    """Return the workspace name to show, or None to skip."""
    # workspacev2>>ID,NAME
    parts = payload.split(",", 1)
    if len(parts) != 2:
        return None
    name = parts[1].strip()
    if name.startswith("special"):
        return None
    return name


WARP_DELAY_MS = 55  # let Hyprland finish placing/animating the new window first


def warp_cursor_to_window(addr: str) -> bool:
    """Warp the pointer to the centre of the just-opened window, but only if
    that window is the one Hyprland actually focused — so windows that open in
    the background or on another workspace don't steal the cursor. One-shot
    (returns False so GLib doesn't repeat it)."""
    try:
        proc = subprocess.run(
            ["hyprctl", "activewindow", "-j"],
            capture_output=True, text=True, timeout=1,
        )
        aw = json.loads(proc.stdout or "{}")
        if aw.get("address", "")[2:] != addr:
            return False
        at, size = aw.get("at") or [0, 0], aw.get("size") or [0, 0]
        if size[0] <= 0 or size[1] <= 0:
            return False
        cx, cy = at[0] + size[0] // 2, at[1] + size[1] // 2
        subprocess.run(
            ["hyprctl", "dispatch", "movecursor", str(cx), str(cy)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1,
        )
    except Exception:
        pass
    return False


def reader_loop(window: WorkspaceOsd) -> None:
    if not SOCK_PATH or not SOCK_PATH.exists():
        print(f"workspace-osd: hyprland socket2 not found at {SOCK_PATH}",
              file=sys.stderr)
        return
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(SOCK_PATH))
    sock.setblocking(True)

    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                event, _, payload = line.decode("utf-8", "replace").partition(">>")
            except Exception:
                continue
            if event == "workspacev2":
                label = parse_label(payload)
                if label is None:
                    continue
                GLib.idle_add(window.show_workspace, label)
            elif event == "openwindow":
                # openwindow>>ADDRESS,WORKSPACE,CLASS,TITLE  (address has no 0x)
                addr = payload.split(",", 1)[0].strip()
                if addr:
                    GLib.timeout_add(WARP_DELAY_MS, warp_cursor_to_window, addr)


def main() -> int:
    def shutdown(*_):
        os._exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT,  shutdown)

    window = WorkspaceOsd()
    threading.Thread(target=reader_loop, args=(window,), daemon=True).start()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
