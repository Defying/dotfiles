#!/usr/bin/env python3
"""Shared "liquid glass" popup for waybar dropdowns (the unified menu module).

Every click-to-open waybar dropdown (AI usage, network, quick settings,
notifications, weather, …) subclasses GlassPopup so they all share one look and
one set of behaviours:

  * the frosted-glass panel design lifted from the AI usage popup,
  * Esc closes it (the window takes keyboard focus, so this Just Works),
  * clicking anywhere outside the panel closes it (click-away),
  * a second click on the same bar item toggles it shut (single-instance via a
    per-name pidfile).

The trick that makes Esc + click-away reliable: the window is a *full-screen*
transparent layer-shell overlay with the glass panel positioned inside it via
alignment + margins. The overlay catches the stray click (or Esc) and closes,
exactly like a native menu. Subclasses only implement build(): populate
self.panel with widgets and call self.populate() to render.

Usage in a subclass:

    class MyPopup(GlassPopup):
        def build(self):
            self.panel.pack_start(Gtk.Label(label="hi"), False, False, 0)

    if __name__ == "__main__":
        sys.exit(GlassPopup.launch("mything", MyPopup, corner="top-right"))
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell  # noqa: E402

from runtime_dirs import private_runtime_dir

RUNTIME = private_runtime_dir("glass-popup")

# Distance from the screen edge to the panel — matches the waybar bubble margin
# (28px side gap) and clears the 44px bar + a little breathing room.
BAR_GAP_TOP = 60
EDGE_GAP = 24

# The one shared stylesheet. Anything popup-specific (progress bars, accent
# colours) can be appended by a subclass via EXTRA_CSS, but the panel/button/
# label base lives here so every dropdown is visually identical.
BASE_CSS = b"""
.glass-root { background: transparent; }
.panel {
  /* Base fill is fairly opaque (0.62) so the panel stays readable over a busy
     desktop; the Hyprland glass-popup blur rule frosts whatever shows through. */
  background:
    linear-gradient(145deg, rgba(255,255,255,0.22), rgba(255,255,255,0.07) 42%,
      rgba(51,204,255,0.12) 68%, rgba(192,132,245,0.16)),
    rgba(10, 14, 24, 0.62);
  border: 1px solid rgba(255,255,255,0.42);
  border-radius: 22px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.55), 0 30px 90px rgba(2,6,23,0.48);
  padding: 16px;
}
.title  { color: #f4f7fb; font-weight: 800; font-size: 15px; text-shadow: 0 1px 1px rgba(0,0,0,0.55); }
.sub    { color: rgba(244,247,251,0.62); font-size: 11px; }
.rowlbl { color: rgba(244,247,251,0.82); font-weight: 800; font-size: 12px; }
.rowval { color: #f4f7fb; font-size: 12px; }
.dim    { color: rgba(244,247,251,0.60); font-size: 11px; }
label   { color: #f4f7fb; text-shadow: 0 1px 1px rgba(0,0,0,0.45); }
button {
  color: #f4f7fb; font-size: 12px;
  background: linear-gradient(145deg, rgba(255,255,255,0.16), rgba(255,255,255,0.06)), rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.18); border-radius: 12px; padding: 7px 10px;
  box-shadow: inset 0 1px rgba(255,255,255,0.16);
}
button:hover { background: linear-gradient(145deg, rgba(255,255,255,0.24), rgba(255,255,255,0.10)), rgba(255,255,255,0.08); }
button:active { background: linear-gradient(145deg, rgba(255,255,255,0.30), rgba(255,255,255,0.12)), rgba(255,255,255,0.10); }
separator { background: rgba(255,255,255,0.12); }
progressbar trough { min-height: 9px; border-radius: 6px; background: rgba(255,255,255,0.14);
  border: 1px solid rgba(255,255,255,0.08); }
progressbar progress { min-height: 9px; border-radius: 6px; background: #c084f5; }
progressbar.warn   progress { background: #f8df9b; }
progressbar.danger progress { background: #ff6b6b; }
"""

_CORNERS = {
    "top-left":  (GtkLayerShell.Edge.LEFT,  Gtk.Align.START),
    "top-right": (GtkLayerShell.Edge.RIGHT, Gtk.Align.END),
    "top-center": (None, Gtk.Align.CENTER),
}


class GlassPopup(Gtk.Window):
    """Base class for a single glass dropdown. Override build()."""

    #: appended to BASE_CSS by subclasses that need extra rules
    EXTRA_CSS: bytes = b""

    #: default panel width; subclasses may override
    WIDTH = 300

    def __init__(self, name: str, corner: str = "top-right", width: int | None = None):
        super().__init__(title=f"glass-popup-{name}")
        self.name = name
        self.set_decorated(False)
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        # Full-screen transparent overlay → reliable Esc + click-away.
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, f"glass-popup-{name}")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_exclusive_zone(self, -1)

        self._install_css()

        # Scrim event box fills the screen and catches click-away.
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("glass-root")
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.connect("button-press-event", self._on_click)

        # The glass panel, positioned in the requested corner.
        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.panel.get_style_context().add_class("panel")
        self.panel.set_size_request(width or self.WIDTH, -1)
        self.panel.set_valign(Gtk.Align.START)
        edge, halign = _CORNERS.get(corner, _CORNERS["top-right"])
        self.panel.set_halign(halign)
        self.panel.set_margin_top(BAR_GAP_TOP)
        if halign == Gtk.Align.START:
            self.panel.set_margin_start(EDGE_GAP)
        elif halign == Gtk.Align.END:
            self.panel.set_margin_end(EDGE_GAP)

        scrim.add(self.panel)
        self.add(scrim)
        self.connect("key-press-event", self._on_key)

    # ── public API for subclasses ───────────────────────────────────────────
    def build(self):
        """Override: pack widgets into self.panel."""
        raise NotImplementedError

    def populate(self):
        """(Re)build the panel contents — call after data changes."""
        for child in self.panel.get_children():
            self.panel.remove(child)
        self.build()
        self.panel.show_all()

    def close(self):
        Gtk.main_quit()

    # ── internals ─────────────────────────────────────────────────────────────
    def _install_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(BASE_CSS + self.EXTRA_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _on_click(self, _w, ev):
        a = self.panel.get_allocation()
        inside = (a.x <= ev.x <= a.x + a.width and a.y <= ev.y <= a.y + a.height)
        if not inside:
            self.close()
        return False

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    # ── lifecycle: single-instance toggle + main loop ──────────────────────────
    @staticmethod
    def _pidfile(name: str) -> Path:
        return RUNTIME / f"glass-popup-{name}.pid"

    @classmethod
    def toggle_off(cls, name: str) -> bool:
        """If a popup of this name is open, close it; return True if it was."""
        pf = cls._pidfile(name)
        try:
            pid = int(pf.read_text())
            os.kill(pid, 0)
            os.kill(pid, signal.SIGTERM)
            pf.unlink(missing_ok=True)
            return True
        except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
            pf.unlink(missing_ok=True)
            return False

    @classmethod
    def launch(cls, name: str, popup_cls, **kwargs) -> int:
        """Toggle: second invocation closes an open popup. Otherwise show it.

        popup_cls(name=..., **kwargs) is instantiated; it should call
        self.populate() at the end of its own __init__.
        """
        if cls.toggle_off(name):
            return 0
        pf = cls._pidfile(name)
        pf.write_text(str(os.getpid()))
        signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())
        signal.signal(signal.SIGINT, lambda *_: Gtk.main_quit())
        win = popup_cls(name=name, **kwargs)
        win.show_all()
        try:
            Gtk.main()
        finally:
            try:
                if pf.read_text().strip() == str(os.getpid()):
                    pf.unlink()
            except OSError:
                pass
        return 0
