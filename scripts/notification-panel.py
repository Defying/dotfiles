#!/usr/bin/env python3
"""Dedicated opaque notification panel for the Waybar notification bubble."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, Pango  # noqa: E402

from runtime_dirs import private_runtime_dir  # noqa: E402

PID_FILE = private_runtime_dir("notification-panel") / "notification-panel.pid"
ASSETS = Path.home() / "dotfiles" / "assets"
ICON_MAP = {
    "ai usage": ASSETS / "openai.png",
}
PANEL_TOP_MARGIN = 6
PANEL_RIGHT_MARGIN = 72
PANEL_WIDTH = 360

CSS = b"""
#notification-panel {
  background: transparent;
}
.panel {
  font-family: "SF Pro Text", "Symbols Nerd Font", "Font Awesome 6 Free", sans-serif;
  background:
    linear-gradient(150deg, rgba(255,255,255,0.18), rgba(255,255,255,0.05) 38%,
      rgba(51,204,255,0.08) 66%, rgba(192,132,245,0.10)),
    rgba(8, 11, 20, 0.74);
  border: 1px solid rgba(255,255,255,0.30);
  border-radius: 16px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.34);
  padding: 10px;
}
.title { color: #f4f7fb; font-size: 13px; font-weight: 700; }
.section { color: rgba(244,247,251,0.60); font-size: 10px; font-weight: 700; }
.empty { color: rgba(244,247,251,0.56); font-size: 11px; padding: 12px 6px; }
.notif {
  background: rgba(255,255,255,0.055);
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 9px;
  padding: 7px 8px;
}
.notif.history { background: rgba(255,255,255,0.035); }
.notif .app { color: rgba(244,247,251,0.55); font-size: 9px; font-weight: 700; }
.notif .summary { color: #f4f7fb; font-size: 11px; font-weight: 700; }
.notif .body { color: rgba(244,247,251,0.78); font-size: 11px; }
.notif.urgent .summary { color: #ffd6d6; }
label { color: #f4f7fb; font-size: 11px; }
button.icon {
  color: #f4f7fb;
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.11);
  border-radius: 9px;
  min-width: 28px;
  min-height: 26px;
  padding: 0;
  font-size: 12px;
}
button.icon:hover {
  background: rgba(255,255,255,0.12);
}
switch slider {
  background: #f4f7fb;
  box-shadow: 0 2px 8px rgba(0,0,0,0.24);
}
switch trough {
  background: rgba(255,255,255,0.10);
  border: 1px solid rgba(255,255,255,0.10);
}
switch:checked trough {
  background: rgba(192,132,245,0.55);
}
scrolledwindow, viewport {
  background: transparent;
}
"""


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


class NotificationPanel(Gtk.Window):
    def __init__(self):
        super().__init__(title="notifications")
        self._focus_seen = False
        self._seat = None
        self.set_name("notification-panel")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_app_paintable(True)
        self.set_size_request(PANEL_WIDTH, -1)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "notification-panel")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, PANEL_TOP_MARGIN)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, PANEL_RIGHT_MARGIN)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        self.panel.get_style_context().add_class("panel")
        self.add(self.panel)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("key-press-event", self.on_key)
        self.connect("button-press-event", self.on_button_press)
        self.connect("focus-in-event", self.on_focus_in)
        self.connect("focus-out-event", self.on_focus_out)
        self.populate()
        GLib.timeout_add_seconds(2, self._tick)

    def show_all(self):
        super().show_all()
        self.present()
        GLib.idle_add(self.grab_pointer)

    def populate(self):
        for child in self.panel.get_children():
            self.panel.remove(child)
        self.build()
        self.panel.show_all()

    def build(self):
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.panel.pack_start(header, False, False, 0)
        title = Gtk.Label(label="notifications", xalign=0)
        title.get_style_context().add_class("title")
        header.pack_start(title, True, True, 0)

        dnd_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.pack_end(dnd_row, False, False, 0)
        dnd_row.pack_start(Gtk.Label(label=""), False, False, 0)
        self.dnd_switch = Gtk.Switch()
        self.dnd_switch.set_tooltip_text("do not disturb")
        self.dnd_switch.set_active(dnd_active())
        self.dnd_switch.connect("notify::active", self.on_dnd_toggle)
        dnd_row.pack_start(self.dnd_switch, False, False, 0)
        header.pack_end(self.icon_button("", "close", self.close), False, False, 0)

        self.list_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(96)
        scroller.set_max_content_height(420)
        scroller.set_propagate_natural_height(True)
        scroller.add(self.list_container)
        self.panel.pack_start(scroller, True, True, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_halign(Gtk.Align.END)
        self.panel.pack_start(footer, False, False, 0)
        footer.pack_start(self.icon_button("", "clear all", self.on_clear), False, False, 0)
        footer.pack_start(self.icon_button("", "restore last", self.on_restore), False, False, 0)

        self.refresh()

    def icon_button(self, glyph, tooltip, callback):
        button = Gtk.Button(label=glyph)
        button.get_style_context().add_class("icon")
        button.set_tooltip_text(tooltip)
        button.connect("clicked", lambda *_: callback())
        return button

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
            image.set_pixel_size(22)
            row.pack_start(image, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        row.pack_start(content, True, True, 0)
        app_label = Gtk.Label(label=app.lower(), xalign=0)
        app_label.get_style_context().add_class("app")
        content.pack_start(app_label, False, False, 0)
        if summary:
            sl = Gtk.Label(label=summary, xalign=0)
            sl.set_line_wrap(True)
            sl.set_max_width_chars(38)
            sl.set_ellipsize(Pango.EllipsizeMode.END)
            sl.get_style_context().add_class("summary")
            content.pack_start(sl, False, False, 0)
        if body:
            bl = Gtk.Label(label=body, xalign=0)
            bl.set_line_wrap(True)
            bl.set_max_width_chars(42)
            bl.set_ellipsize(Pango.EllipsizeMode.END)
            bl.get_style_context().add_class("body")
            content.pack_start(bl, False, False, 0)

        action = Gtk.Button(label="")
        action.get_style_context().add_class("icon")
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

    def on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def on_focus_in(self, *_):
        self._focus_seen = True
        return False

    def on_focus_out(self, *_):
        if not self._focus_seen:
            return False
        GLib.timeout_add(80, self.close_if_unfocused)
        return False

    def close_if_unfocused(self):
        if not self.has_toplevel_focus():
            self.close()
        return False

    def grab_pointer(self):
        window = self.get_window()
        display = Gdk.Display.get_default()
        if window is None or display is None:
            return False
        seat = display.get_default_seat()
        if seat is None:
            return False
        result = seat.grab(
            window,
            Gdk.SeatCapabilities.POINTER,
            True,
            None,
            None,
            None,
        )
        if result == Gdk.GrabStatus.SUCCESS:
            self._seat = seat
        return False

    def on_button_press(self, _widget, event):
        alloc = self.get_allocation()
        if event.x < 0 or event.y < 0 or event.x > alloc.width or event.y > alloc.height:
            self.close()
            return True
        return False

    def close(self):
        if self._seat is not None:
            self._seat.ungrab()
            self._seat = None
        Gtk.main_quit()


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
            if PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                PID_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
