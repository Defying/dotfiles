#!/usr/bin/env python3
"""Click-to-open AI usage popup for the waybar Codex/Claude bubbles.

Renders 5-hour + weekly usage as bars with reset times, reading the cached
usage the bubbles already write (~/.cache/waybar/{codex,claude}-usage.json) so
opening it never re-spawns the Codex app-server or hits the network. A Refresh
button re-runs the indicator on demand.

Usage:  ai-usage-popup.py codex|claude
Clicking the bubble again (same service) toggles the popup closed.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell  # noqa: E402

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "waybar"
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
SCRIPTS = Path(__file__).resolve().parent
ASSETS = Path.home() / "dotfiles" / "assets"

SERVICES = {
    "codex": {
        "title": "Codex",
        "icon": ASSETS / "openai.png",
        "cache": CACHE / "codex-usage.json",
        "url": "https://chatgpt.com/codex/settings/usage",
        "refresh": [str(SCRIPTS / "waybar-ai-refresh.sh"), "codex", "8"],
    },
    "claude": {
        "title": "Claude",
        "icon": ASSETS / "claude.png",
        "cache": CACHE / "claude-usage.json",
        "url": "https://claude.ai/settings/usage",
        "refresh": [str(SCRIPTS / "waybar-ai-refresh.sh"), "claude", "9"],
    },
}


def reset_label(epoch):
    if not epoch:
        return "reset time unknown"
    now = dt.datetime.now()
    when = dt.datetime.fromtimestamp(int(epoch))
    delta = int(epoch) - now.timestamp()
    if delta <= 0:
        return "resets now"
    if when.date() == now.date():
        stamp = when.strftime("%H:%M")
    else:
        stamp = when.strftime("%a %H:%M").lower()
    hrs = delta / 3600
    rel = f"{hrs:.0f}h" if hrs >= 1 else f"{delta / 60:.0f}m"
    return f"resets {stamp}  ·  in {rel}"


def _iso_epoch(value):
    if not value:
        return None
    try:
        return int(dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def load_usage(service):
    """Return (windows, extra_line, age_min) or (None, msg, None) on failure.
    windows: list of (label, remaining_pct, reset_epoch)."""
    cfg = SERVICES[service]
    try:
        data = json.loads(cfg["cache"].read_text(encoding="utf-8"))
    except Exception:
        return None, "No cached usage yet — open the bar, then Refresh.", None

    updated = data.get("updated_at", 0)
    age = int((dt.datetime.now().timestamp() - float(updated)) / 60) if updated else None

    if service == "codex":
        lim = data.get("limits") or {}
        prim, sec = lim.get("primary") or {}, lim.get("secondary") or {}
        windows = [
            ("5-hour", 100 - int(round(float(prim.get("usedPercent") or 0))), prim.get("resetsAt")),
            ("Weekly", 100 - int(round(float(sec.get("usedPercent") or 0))), sec.get("resetsAt")) if sec else None,
        ]
        credits = lim.get("credits") or {}
        extra = "credits: unlimited" if credits.get("unlimited") else f"credits: {credits.get('balance', '0')}"
    else:
        u = data.get("usage") or {}
        fh, sd = u.get("five_hour") or {}, u.get("seven_day") or {}
        windows = [
            ("5-hour", 100 - int(round(float(fh.get("utilization") or 0))), _iso_epoch(fh.get("resets_at"))),
            ("Weekly", 100 - int(round(float(sd.get("utilization") or 0))), _iso_epoch(sd.get("resets_at"))) if sd else None,
        ]
        ex = u.get("extra_usage") or {}
        extra = f"extra usage: {'enabled' if ex.get('is_enabled') else 'disabled'}" if ex else ""

    return [w for w in windows if w], extra, age


CSS = b"""
#ai-usage-popup { background: transparent; }
.panel {
  background:
    linear-gradient(145deg, rgba(255,255,255,0.30), rgba(255,255,255,0.09) 42%,
      rgba(51,204,255,0.14) 68%, rgba(192,132,245,0.18)),
    rgba(10, 14, 24, 0.34);
  border: 1px solid rgba(255,255,255,0.42);
  border-radius: 22px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.55), 0 30px 90px rgba(2,6,23,0.48);
  padding: 16px;
}
.title { color: #f4f7fb; font-weight: 800; font-size: 15px; text-shadow: 0 1px 1px rgba(0,0,0,0.55); }
.win   { color: rgba(244,247,251,0.82); font-weight: 800; font-size: 12px; }
.pct   { color: #f4f7fb; font-weight: 800; font-size: 12px; }
.reset { color: rgba(244,247,251,0.60); font-size: 11px; }
.extra { color: rgba(244,247,251,0.70); font-size: 11px; }
label  { color: #f4f7fb; text-shadow: 0 1px 1px rgba(0,0,0,0.45); }
progressbar trough { min-height: 9px; border-radius: 6px; background: rgba(255,255,255,0.14);
  border: 1px solid rgba(255,255,255,0.08); }
progressbar progress { min-height: 9px; border-radius: 6px; background: #c084f5; }
progressbar.warn   progress { background: #f8df9b; }
progressbar.danger progress { background: #ff6b6b; }
button {
  color: #f4f7fb; font-size: 12px;
  background: linear-gradient(145deg, rgba(255,255,255,0.16), rgba(255,255,255,0.06)), rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.18); border-radius: 12px; padding: 7px 10px;
  box-shadow: inset 0 1px rgba(255,255,255,0.16);
}
button:hover { background: linear-gradient(145deg, rgba(255,255,255,0.24), rgba(255,255,255,0.10)), rgba(255,255,255,0.08); }
"""


class Popup(Gtk.Window):
    def __init__(self, service):
        super().__init__(title="ai-usage")
        self.service = service
        self.cfg = SERVICES[service]
        self.set_name("ai-usage-popup")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_size_request(300, -1)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "ai-usage")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, 62)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, 28)
        self.connect("key-press-event", self._on_key)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.root.get_style_context().add_class("panel")
        self.add(self.root)
        self._build()

    def _build(self):
        for child in self.root.get_children():
            self.root.remove(child)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = self.cfg["icon"]
        if icon.exists():
            from gi.repository import GdkPixbuf
            img = Gtk.Image.new_from_pixbuf(
                GdkPixbuf.Pixbuf.new_from_file_at_size(str(icon), 18, 18))
            header.pack_start(img, False, False, 0)
        title = Gtk.Label(label=f"{self.cfg['title']} usage", xalign=0)
        title.get_style_context().add_class("title")
        header.pack_start(title, True, True, 0)
        self.root.pack_start(header, False, False, 0)

        windows, extra, age = load_usage(self.service)
        if windows is None:
            msg = Gtk.Label(label=extra, xalign=0)
            msg.set_line_wrap(True)
            self.root.pack_start(msg, False, False, 0)
        else:
            for label, remaining, epoch in windows:
                self.root.pack_start(self._window_row(label, remaining, epoch), False, False, 0)
            if extra:
                el = Gtk.Label(label=extra, xalign=0)
                el.get_style_context().add_class("extra")
                self.root.pack_start(el, False, False, 0)
            if age is not None:
                al = Gtk.Label(label=f"updated {age}m ago", xalign=0)
                al.get_style_context().add_class("reset")
                self.root.pack_start(al, False, False, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", lambda *_: self._refresh())
        usage = Gtk.Button(label="Open usage")
        usage.connect("clicked", lambda *_: self._open_url())
        grid.attach(refresh, 0, 0, 1, 1)
        grid.attach(usage, 1, 0, 1, 1)
        self.root.pack_start(grid, False, False, 0)
        self.root.show_all()

    def _window_row(self, label, remaining, epoch):
        remaining = max(0, min(100, int(remaining)))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=label, xalign=0)
        name.get_style_context().add_class("win")
        pct = Gtk.Label(label=f"{remaining}% left", xalign=1)
        pct.get_style_context().add_class("pct")
        top.pack_start(name, True, True, 0)
        top.pack_end(pct, False, False, 0)
        box.pack_start(top, False, False, 0)

        bar = Gtk.ProgressBar()
        bar.set_fraction(remaining / 100.0)
        if remaining <= 10:
            bar.get_style_context().add_class("danger")
        elif remaining <= 30:
            bar.get_style_context().add_class("warn")
        box.pack_start(bar, False, False, 0)

        reset = Gtk.Label(label=reset_label(epoch), xalign=0)
        reset.get_style_context().add_class("reset")
        box.pack_start(reset, False, False, 0)
        return box

    def _refresh(self):
        def worker():
            try:
                subprocess.run(self.cfg["refresh"], timeout=20,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            GLib.idle_add(self._build)
        threading.Thread(target=worker, daemon=True).start()

    def _open_url(self):
        subprocess.Popen(["xdg-open", self.cfg["url"]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        Gtk.main_quit()

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
            return True
        return False


def toggle_existing(service):
    """If a popup for this service is already up, close it and return True."""
    pid_file = RUNTIME / f"ai-usage-{service}.pid"
    try:
        pid = int(pid_file.read_text())
        os.kill(pid, 0)            # alive?
        os.kill(pid, signal.SIGTERM)
        pid_file.unlink(missing_ok=True)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pid_file.unlink(missing_ok=True)
        return False


def main():
    service = sys.argv[1] if len(sys.argv) > 1 else "codex"
    if service not in SERVICES:
        print(f"usage: {sys.argv[0]} codex|claude", file=sys.stderr)
        return 2
    if toggle_existing(service):   # second click closes the open popup
        return 0

    pid_file = RUNTIME / f"ai-usage-{service}.pid"
    pid_file.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())
    win = Popup(service)
    win.show_all()
    try:
        Gtk.main()
    finally:
        try:
            if pid_file.read_text().strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
