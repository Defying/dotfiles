#!/usr/bin/env python3
"""Click-to-open network popup for the waybar network bubble.

Shows today / this-month / all-time traffic from vnstat (queried once on open),
the active interface + SSID, and a short live ↓/↑ sample. Toggles closed on a
second click. Reuses the glass popup look from ai-usage-popup.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from glass_popup import GlassPopup  # noqa: E402


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


class Popup(GlassPopup):
    WIDTH = 320

    def __init__(self, name, corner="top-right"):
        self.iface = default_iface()
        super().__init__(name, corner=corner)
        self.populate()

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

    def build(self):
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(label="Network", xalign=0)
        title.get_style_context().add_class("title")
        header.pack_start(title, False, False, 0)
        essid = run("iwgetid", "-r")
        sub = f"{essid}  ·  {self.iface}" if essid else (self.iface or "disconnected")
        subl = Gtk.Label(label=sub, xalign=0)
        subl.get_style_context().add_class("sub")
        header.pack_start(subl, False, False, 0)
        self.panel.pack_start(header, False, False, 0)

        # Live sample (placeholder until the worker fills it in).
        self.rate_row = self._pair_row("Now", "…", "…")
        self.panel.pack_start(self.rate_row, False, False, 0)
        self.panel.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)

        stats = vnstat_for(self.iface) if self.iface else None
        if stats:
            for key, label in (("today", "Today"), ("month", "This month"), ("total", "All time")):
                rx, tx = stats[key]
                self.panel.pack_start(self._pair_row(label, human_bytes(rx), human_bytes(tx)), False, False, 0)
        else:
            self.panel.pack_start(Gtk.Label(label="vnstat has no data for this interface yet.", xalign=0), False, False, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        conn = Gtk.Button(label="Connections")
        conn.connect("clicked", lambda *_: self._spawn("nm-connection-editor"))
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", lambda *_: self.populate())
        grid.attach(conn, 0, 0, 1, 1)
        grid.attach(refresh, 1, 0, 1, 1)
        self.panel.pack_start(grid, False, False, 0)

        # Sample the live rate off the main thread so the popup paints instantly.
        if self.iface:
            threading.Thread(target=self._update_rate, daemon=True).start()

    def _update_rate(self):
        down, up = sample_rate(self.iface)

        def apply():
            new = self._pair_row("Now", fmt_rate(down), fmt_rate(up))
            idx = self.panel.get_children().index(self.rate_row)
            self.panel.remove(self.rate_row)
            self.panel.add(new)
            self.panel.reorder_child(new, idx)
            self.rate_row = new
            new.show_all()
            return False
        GLib.idle_add(apply)

    def _spawn(self, *args):
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.close()


def main():
    return GlassPopup.launch("network", Popup, corner="top-right")


if __name__ == "__main__":
    sys.exit(main())
