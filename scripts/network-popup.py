#!/usr/bin/env python3
"""Click-to-open network popup for the waybar network bubble.

Shows today / this-month / all-time traffic from vnstat (queried once on open),
the active interface + SSID, and a short live ↓/↑ sample. Toggles closed on a
second click. Reuses the glass popup look from ai-usage-popup.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell  # noqa: E402

RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
PID_FILE = RUNTIME / "network-popup.pid"


def run(*args, timeout=4):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()
    except Exception:
        return ""


def default_iface():
    for line in run("ip", "route").splitlines():
        parts = line.split()
        if parts[:1] == ["default"] and "dev" in parts:
            return parts[parts.index("dev") + 1]
    return None


def human_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f}{unit}" if unit in ("B", "KiB") else f"{n:.2f}{unit}"
        n /= 1024


def fmt_rate(bits):
    bits = max(0.0, bits)
    if bits >= 1e9:
        return f"{bits / 1e9:.1f}gbps"
    if bits >= 1e6:
        return f"{bits / 1e6:.1f}mbps"
    return f"{bits / 1e3:.1f}kbps"


def stat_bytes(iface):
    base = f"/sys/class/net/{iface}/statistics"
    try:
        return (int(Path(f"{base}/rx_bytes").read_text()),
                int(Path(f"{base}/tx_bytes").read_text()))
    except OSError:
        return None


def sample_rate(iface, dt=0.4):
    a = stat_bytes(iface)
    if not a:
        return 0.0, 0.0
    time.sleep(dt)
    b = stat_bytes(iface)
    if not b:
        return 0.0, 0.0
    return (b[0] - a[0]) * 8 / dt, (b[1] - a[1]) * 8 / dt


def vnstat_for(iface):
    """Return dict with today/month/total rx+tx bytes, or None."""
    try:
        data = json.loads(run("vnstat", "--json", "-i", iface, timeout=5) or "{}")
        ifaces = data.get("interfaces") or []
        if not ifaces:
            return None
        t = ifaces[0]["traffic"]
        day = (t.get("day") or [{}])[-1]
        month = (t.get("month") or [{}])[-1]
        total = t.get("total") or {}
        return {
            "today": (day.get("rx", 0), day.get("tx", 0)),
            "month": (month.get("rx", 0), month.get("tx", 0)),
            "total": (total.get("rx", 0), total.get("tx", 0)),
        }
    except Exception:
        return None


CSS = b"""
#network-popup { background: transparent; }
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
.title  { color: #f4f7fb; font-weight: 800; font-size: 15px; text-shadow: 0 1px 1px rgba(0,0,0,0.55); }
.sub    { color: rgba(244,247,251,0.62); font-size: 11px; }
.rowlbl { color: rgba(244,247,251,0.82); font-weight: 800; font-size: 12px; }
.rowval { color: #f4f7fb; font-size: 12px; }
.dn     { color: #7ee787; font-weight: 800; }
.up     { color: #33ccff; font-weight: 800; }
label   { color: #f4f7fb; text-shadow: 0 1px 1px rgba(0,0,0,0.45); }
button {
  color: #f4f7fb; font-size: 12px;
  background: linear-gradient(145deg, rgba(255,255,255,0.16), rgba(255,255,255,0.06)), rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.18); border-radius: 12px; padding: 7px 10px;
  box-shadow: inset 0 1px rgba(255,255,255,0.16);
}
button:hover { background: linear-gradient(145deg, rgba(255,255,255,0.24), rgba(255,255,255,0.10)), rgba(255,255,255,0.08); }
separator { background: rgba(255,255,255,0.12); }
"""


class Popup(Gtk.Window):
    def __init__(self):
        super().__init__(title="network")
        self.set_name("network-popup")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_size_request(320, -1)
        self.iface = default_iface()

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "network-popup")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, 62)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, 28)
        self.connect("key-press-event", self._on_key)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.root.get_style_context().add_class("panel")
        self.add(self.root)
        self._build()

    def _pair_row(self, label, rx, tx):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label=label, xalign=0)
        lbl.get_style_context().add_class("rowlbl")
        val = Gtk.Label(label="", xalign=1, use_markup=True)
        val.set_markup(
            f'<span foreground="#7ee787">↓ {rx}</span>   '
            f'<span foreground="#33ccff">↑ {tx}</span>'
        )
        val.get_style_context().add_class("rowval")
        row.pack_start(lbl, True, True, 0)
        row.pack_end(val, False, False, 0)
        return row

    def _build(self):
        for c in self.root.get_children():
            self.root.remove(c)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(label="Network", xalign=0)
        title.get_style_context().add_class("title")
        header.pack_start(title, False, False, 0)
        essid = run("iwgetid", "-r")
        sub = f"{essid}  ·  {self.iface}" if essid else (self.iface or "disconnected")
        subl = Gtk.Label(label=sub, xalign=0)
        subl.get_style_context().add_class("sub")
        header.pack_start(subl, False, False, 0)
        self.root.pack_start(header, False, False, 0)

        # Live sample (placeholder until the worker fills it in).
        self.rate_row = self._pair_row("Now", "…", "…")
        self.root.pack_start(self.rate_row, False, False, 0)
        self.root.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)

        stats = vnstat_for(self.iface) if self.iface else None
        if stats:
            for key, label in (("today", "Today"), ("month", "This month"), ("total", "All time")):
                rx, tx = stats[key]
                self.root.pack_start(self._pair_row(label, human_bytes(rx), human_bytes(tx)), False, False, 0)
        else:
            self.root.pack_start(Gtk.Label(label="vnstat has no data for this interface yet.", xalign=0), False, False, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        conn = Gtk.Button(label="Connections")
        conn.connect("clicked", lambda *_: self._spawn("nm-connection-editor"))
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", lambda *_: self._build())
        grid.attach(conn, 0, 0, 1, 1)
        grid.attach(refresh, 1, 0, 1, 1)
        self.root.pack_start(grid, False, False, 0)
        self.root.show_all()

        # Sample the live rate off the main thread so the popup paints instantly.
        if self.iface:
            threading.Thread(target=self._update_rate, daemon=True).start()

    def _update_rate(self):
        down, up = sample_rate(self.iface)

        def apply():
            new = self._pair_row("Now", fmt_rate(down), fmt_rate(up))
            idx = self.root.get_children().index(self.rate_row)
            self.root.remove(self.rate_row)
            self.root.add(new)
            self.root.reorder_child(new, idx)
            self.rate_row = new
            new.show_all()
            return False
        GLib.idle_add(apply)

    def _spawn(self, *args):
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        Gtk.main_quit()

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
            return True
        return False


def toggle_existing():
    try:
        pid = int(PID_FILE.read_text())
        os.kill(pid, 0)
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        PID_FILE.unlink(missing_ok=True)
        return False


def main():
    if toggle_existing():
        return 0
    PID_FILE.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())
    win = Popup()
    win.show_all()
    try:
        Gtk.main()
    finally:
        try:
            if PID_FILE.read_text().strip() == str(os.getpid()):
                PID_FILE.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
