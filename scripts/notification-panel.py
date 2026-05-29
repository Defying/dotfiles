#!/usr/bin/env python3
"""Notification panel (mako) on the shared glass dropdown module.

Subclasses GlassPopup for the unified look + Esc/click-away, but keeps its own
main()/pidfile because the waybar-notifications.sh wrapper drives explicit
open/close/toggle (swipe gestures) — it must not self-toggle on launch.
"""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from glass_popup import GlassPopup  # noqa: E402
from runtime_dirs import private_runtime_dir  # noqa: E402

PID_FILE = private_runtime_dir("notification-panel") / "notification-panel.pid"
ASSETS = Path.home() / "dotfiles" / "assets"
ICON_MAP = {
    "ai usage": ASSETS / "openai.png",
}


def run(*args, capture=False, timeout=2.0):
    try:
        if capture:
            return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except Exception:
        return "" if capture else None
    return ""


def makoctl_json(command):
    out = run("makoctl", command, "-j", capture=True)
    if not out:
        return []
    try:
        return json.loads(out)
    except Exception:
        return []


def dnd_active():
    modes = run("makoctl", "mode", capture=True).splitlines()
    return any(line.strip().lstrip("* ").strip() == "do-not-disturb" for line in modes)


def set_dnd(on):
    if on:
        run("makoctl", "mode", "-a", "do-not-disturb")
    else:
        run("makoctl", "mode", "-r", "do-not-disturb")


def pick_icon(app_name, app_icon):
    if app_icon and Path(app_icon).expanduser().exists():
        return Path(app_icon).expanduser()
    key = (app_name or "").strip().lower()
    if key in ICON_MAP and ICON_MAP[key].exists():
        return ICON_MAP[key]
    if "claude" in key and (ASSETS / "claude.png").exists():
        return ASSETS / "claude.png"
    if "openai" in key or "codex" in key or "chatgpt" in key:
        png = ASSETS / "openai.png"
        if png.exists():
            return png
    return None


class NotificationPanel(GlassPopup):
    WIDTH = 380
    EXTRA_CSS = b"""
    .section { color: rgba(244,247,251,0.78); font-weight: 700; font-size: 11px; margin-top: 4px; }
    .empty   { color: rgba(244,247,251,0.55); font-size: 12px; padding: 18px 8px; }
    .notif {
      background: rgba(255,255,255,0.07);
      border: 1px solid rgba(255,255,255,0.12);
      border-radius: 14px;
      padding: 10px 12px;
    }
    .notif.history { background: rgba(255,255,255,0.04); }
    .notif .app { color: rgba(244,247,251,0.62); font-size: 10px; font-weight: 700; letter-spacing: 0.04em; }
    .notif .summary { color: #f4f7fb; font-size: 13px; font-weight: 700; }
    .notif .body { color: rgba(244,247,251,0.86); font-size: 12px; }
    .notif.urgent .summary { color: #ffd6d6; }
    .icon-btn { padding: 4px 8px; min-width: 28px; }
    switch slider { background: #f4f7fb; box-shadow: 0 2px 8px rgba(0,0,0,0.24); }
    switch trough { background: rgba(255,255,255,0.14); border: 1px solid rgba(255,255,255,0.16); }
    switch:checked trough { background: rgba(192,132,245,0.55); }
    scrolledwindow { background: transparent; }
    """

    def __init__(self, name="notifications", corner="top-right"):
        super().__init__(name, corner=corner)
        self.populate()
        GLib.timeout_add_seconds(2, self._tick)

    def build(self):
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.panel.pack_start(header, False, False, 0)
        title = Gtk.Label(label="notifications", xalign=0)
        title.get_style_context().add_class("title")
        header.pack_start(title, True, True, 0)

        dnd_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.pack_end(dnd_row, False, False, 0)
        dnd_row.pack_start(Gtk.Label(label="dnd"), False, False, 0)
        self.dnd_switch = Gtk.Switch()
        self.dnd_switch.set_active(dnd_active())
        self.dnd_switch.connect("notify::active", self.on_dnd_toggle)
        dnd_row.pack_start(self.dnd_switch, False, False, 0)

        self.list_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(120)
        scroller.set_max_content_height(520)
        scroller.set_propagate_natural_height(True)
        scroller.add(self.list_container)
        self.panel.pack_start(scroller, True, True, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.panel.pack_start(footer, False, False, 0)
        clear = Gtk.Button(label="clear all")
        clear.connect("clicked", self.on_clear)
        footer.pack_start(clear, True, True, 0)
        restore = Gtk.Button(label="restore last")
        restore.connect("clicked", self.on_restore)
        footer.pack_start(restore, True, True, 0)
        close = Gtk.Button(label="close")
        close.connect("clicked", lambda *_: self.close())
        footer.pack_start(close, True, True, 0)

        self.refresh()

    def _tick(self):
        self.refresh()
        return True

    def refresh(self):
        for child in self.list_container.get_children():
            self.list_container.remove(child)

        active = makoctl_json("list")
        history = makoctl_json("history")
        # mako's `list` shape is [{notifications: [...]}, ...] grouped by mode; flatten.
        flat_active = []
        if isinstance(active, list):
            for entry in active:
                if isinstance(entry, dict) and "notifications" in entry:
                    flat_active.extend(entry.get("notifications") or [])
                else:
                    flat_active.append(entry)

        if flat_active:
            label = Gtk.Label(label=f"active  ·  {len(flat_active)}", xalign=0)
            label.get_style_context().add_class("section")
            self.list_container.pack_start(label, False, False, 0)
            for notif in flat_active:
                self.list_container.pack_start(self.make_row(notif, history_row=False), False, False, 0)

        if history:
            label = Gtk.Label(label=f"history  ·  {len(history)}", xalign=0)
            label.get_style_context().add_class("section")
            self.list_container.pack_start(label, False, False, 0)
            for notif in history[:25]:
                self.list_container.pack_start(self.make_row(notif, history_row=True), False, False, 0)

        if not flat_active and not history:
            empty = Gtk.Label(label="no notifications", xalign=0.5)
            empty.get_style_context().add_class("empty")
            self.list_container.pack_start(empty, False, False, 0)

        self.list_container.show_all()

    def make_row(self, notif, history_row):
        def get(name, default=""):
            value = notif.get(name)
            if isinstance(value, dict):
                return value.get("data", default)
            return value if value is not None else default

        app = str(get("app_name") or "notification")
        summary = str(get("summary") or "")
        body = str(get("body") or "")
        urgency = str(get("urgency") or "normal")
        notif_id = get("id")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        ctx = row.get_style_context()
        ctx.add_class("notif")
        if history_row:
            ctx.add_class("history")
        if urgency == "critical":
            ctx.add_class("urgent")

        icon_path = pick_icon(app, get("app_icon"))
        if icon_path:
            image = Gtk.Image.new_from_file(str(icon_path))
            image.set_pixel_size(28)
            row.pack_start(image, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        row.pack_start(content, True, True, 0)
        app_label = Gtk.Label(label=app.lower(), xalign=0)
        app_label.get_style_context().add_class("app")
        content.pack_start(app_label, False, False, 0)
        if summary:
            sl = Gtk.Label(label=summary, xalign=0)
            sl.set_line_wrap(True)
            sl.set_max_width_chars(40)
            sl.get_style_context().add_class("summary")
            content.pack_start(sl, False, False, 0)
        if body:
            bl = Gtk.Label(label=body, xalign=0)
            bl.set_line_wrap(True)
            bl.set_max_width_chars(46)
            bl.get_style_context().add_class("body")
            content.pack_start(bl, False, False, 0)

        action = Gtk.Button(label="×")
        action.get_style_context().add_class("icon-btn")
        action.set_valign(Gtk.Align.CENTER)
        if history_row:
            action.set_tooltip_text("remove from history")
            action.connect("clicked", lambda *_: self.on_forget(notif_id))
        else:
            action.set_tooltip_text("dismiss")
            action.connect("clicked", lambda *_: self.on_dismiss(notif_id))
        row.pack_end(action, False, False, 0)

        return row

    def on_dismiss(self, notif_id):
        if notif_id is not None:
            run("makoctl", "dismiss", "-n", str(notif_id))
        else:
            run("makoctl", "dismiss")
        self.refresh()

    def on_forget(self, notif_id):
        # mako has no per-id history removal; restoring then dismissing without
        # adding back to history is the canonical workaround for the most-recent
        # entry. For arbitrary IDs, just clear all history.
        run("makoctl", "restore")
        run("makoctl", "dismiss", "--no-history")
        self.refresh()

    def on_clear(self, *_):
        # Dismiss currently visible notifications, then drain mako's history
        # (mako has no built-in "clear history" — restore + dismiss --no-history
        # is the documented workaround). Hard cap on iterations to avoid spin.
        run("makoctl", "dismiss", "--all")
        for _ in range(50):
            history = makoctl_json("history")
            if not history:
                break
            run("makoctl", "restore")
            run("makoctl", "dismiss", "--no-history")
        self.refresh()

    def on_restore(self, *_):
        run("makoctl", "restore")
        self.refresh()

    def on_dnd_toggle(self, switch, *_):
        set_dnd(switch.get_active())


def main():
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception:
        pass

    def _sig(*_):
        GLib.idle_add(Gtk.main_quit)

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)

    panel = NotificationPanel()
    panel.connect("destroy", lambda *_: Gtk.main_quit())
    panel.show_all()
    try:
        Gtk.main()
    finally:
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
