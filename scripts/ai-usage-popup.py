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
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from glass_popup import GlassPopup  # noqa: E402

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "waybar"
SCRIPTS = Path(__file__).resolve().parent
ASSETS = Path.home() / "dotfiles" / "assets"

SERVICES = {
    "codex": {
        "title": "Codex",
        "icon": ASSETS / "openai.png",
        "cache": CACHE / "codex-usage.json",
        "url": "https://chatgpt.com/codex/settings/usage",
        "refresh": [str(SCRIPTS / "waybar-openai-tokens.py"), "--refresh", "--signal", "8"],
        "account": [str(SCRIPTS / "ai_accounts.py"), "codex-menu"],
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
    if data.get("error"):
        return None, f"Refresh failed: {data.get('error')}", age

    if service == "codex":
        lim = data.get("limits") or {}
        prim, sec = lim.get("primary") or {}, lim.get("secondary") or {}
        windows = [
            ("5-hour", 100 - int(round(float(prim.get("usedPercent") or 0))), prim.get("resetsAt")),
            ("Weekly", 100 - int(round(float(sec.get("usedPercent") or 0))), sec.get("resetsAt")) if sec else None,
        ]
        credits = lim.get("credits") or {}
        account = data.get("account") or {}
        extras = []
        label = account.get("label") or account.get("email")
        if label:
            extras.append(f"account: {label}")
        extras.append("credits: unlimited" if credits.get("unlimited") else f"credits: {credits.get('balance', '0')}")
        extra = "  ·  ".join(extras)
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


class Popup(GlassPopup):
    EXTRA_CSS = b"""
    .win   { color: rgba(244,247,251,0.82); font-weight: 800; font-size: 12px; }
    .pct   { color: #f4f7fb; font-weight: 800; font-size: 12px; }
    .reset { color: rgba(244,247,251,0.60); font-size: 11px; }
    .extra { color: rgba(244,247,251,0.70); font-size: 11px; }
    """

    def __init__(self, name, service, corner="top-left"):
        self.service = service
        self.cfg = SERVICES[service]
        super().__init__(name, corner=corner)
        self.populate()

    def build(self):
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
        self.panel.pack_start(header, False, False, 0)

        windows, extra, age = load_usage(self.service)
        if windows is None:
            msg = Gtk.Label(label=extra, xalign=0)
            msg.set_line_wrap(True)
            self.panel.pack_start(msg, False, False, 0)
        else:
            for label, remaining, epoch in windows:
                self.panel.pack_start(self._window_row(label, remaining, epoch), False, False, 0)
            if extra:
                el = Gtk.Label(label=extra, xalign=0)
                el.set_line_wrap(True)
                el.get_style_context().add_class("extra")
                self.panel.pack_start(el, False, False, 0)
            if age is not None:
                al = Gtk.Label(label=f"updated {age}m ago", xalign=0)
                al.get_style_context().add_class("reset")
                self.panel.pack_start(al, False, False, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        buttons = [
            ("Refresh", self._refresh),
            ("Open usage", self._open_url),
        ]
        if self.cfg.get("account"):
            buttons.append(("Account", self._account_menu))
        for index, (label, callback) in enumerate(buttons):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, cb=callback: cb())
            grid.attach(button, index % 2, index // 2, 1, 1)
        self.panel.pack_start(grid, False, False, 0)

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
            GLib.idle_add(self.populate)
        threading.Thread(target=worker, daemon=True).start()

    def _open_url(self):
        subprocess.Popen(["xdg-open", self.cfg["url"]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.close()

    def _account_menu(self):
        command = self.cfg.get("account")
        if command:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.close()


def main():
    service = sys.argv[1] if len(sys.argv) > 1 else "codex"
    if service not in SERVICES:
        print(f"usage: {sys.argv[0]} codex|claude", file=sys.stderr)
        return 2
    return GlassPopup.launch(f"ai-usage-{service}", Popup,
                             service=service, corner="top-left")


if __name__ == "__main__":
    sys.exit(main())
