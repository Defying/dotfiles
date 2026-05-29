#!/usr/bin/env python3
"""liquid-launcher: liquid glass app launcher for Hyprland.

Runs as a resident daemon so opening is instant: the window (rows + icons) is
built once and kept hidden, then shown on demand. The Hyprland keybind invokes
this same script with no args — that path is a ~14ms stdlib-only client that
just sends "toggle" over a Unix socket and exits *before* the heavy GTK stack
(~90ms) is ever imported. `--daemon` (exec-once) pre-warms the resident window.
"""

import math
import os
import re
import signal
import socket
import sys
import threading
from pathlib import Path

SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "liquid-launcher.sock")


def log(*a):
    """Stderr log — shows up in the journal when run as a user service."""
    print("liquid-launcher:", *a, file=sys.stderr, flush=True)


class DaemonExists(Exception):
    """Raised when another live daemon already owns the socket."""


def _connect():
    """Connect to the daemon socket, or None if nothing is listening
    (covers both 'no socket file' and 'stale socket from a crash')."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.3)
        s.connect(SOCK)
        return s
    except OSError:
        return None


def _send(cmd):
    """Send one command to a running daemon. True if one answered."""
    s = _connect()
    if s is None:
        return False
    try:
        s.sendall(cmd.encode() + b"\n")
        return True
    except OSError:
        return False
    finally:
        s.close()


def _daemon_alive():
    s = _connect()
    if s is not None:
        s.close()
        return True
    return False


# Fast path: if a daemon is already up and we weren't asked to *be* the daemon,
# toggle it and exit now — before importing cairo/gi (the expensive part).
if __name__ == "__main__" and "--daemon" not in sys.argv:
    if _send("toggle"):
        sys.exit(0)

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, GtkLayerShell  # noqa: E402

APP_DIRS = [
    Path("/usr/share/applications"),
    Path.home() / ".local/share/applications",
    Path("/usr/local/share/applications"),
]

LAUNCHER_W = 580
LAUNCHER_R = 24
MAX_ROWS   = 8     # visible rows (window height); the full list scrolls beyond this
HEADER_H   = 64
ROW_H      = 50
ICON_PX    = 28   # every row's icon occupies this fixed square so names align
HEIGHT     = HEADER_H + 1 + MAX_ROWS * ROW_H + 12   # sep + rows + bottom pad



# ── App loading ───────────────────────────────────────────────────────────────

def load_apps():
    seen, apps = set(), []
    for d in APP_DIRS:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.desktop")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            kv, in_entry = {}, False
            for line in text.splitlines():
                line = line.strip()
                if line == "[Desktop Entry]":
                    in_entry = True
                elif line.startswith("[") and in_entry:
                    break
                elif in_entry and "=" in line:
                    k, _, v = line.partition("=")
                    kv[k.strip()] = v.strip()
            if not in_entry:
                continue
            if kv.get("NoDisplay", "").lower() == "true":
                continue
            if kv.get("Type") != "Application":
                continue
            name = kv.get("Name", "").strip()
            exec_ = kv.get("Exec", "").strip()
            if not name or not exec_ or name in seen:
                continue
            seen.add(name)
            apps.append({
                "name":    name,
                "exec":    exec_,
                "icon":    kv.get("Icon", ""),
                "generic": kv.get("GenericName", ""),
                "keywords": kv.get("Keywords", "").lower(),
            })
    return sorted(apps, key=lambda a: a["name"].lower())


def clean_exec(s):
    return re.sub(r"\s*%[a-zA-Z]\s*", " ", s).strip()


def score_app(app, q):
    if not q:
        return 1
    name = app["name"].lower()
    if name.startswith(q):
        return 4
    if q in name:
        return 3
    gen = (app.get("generic") or "").lower()
    if gen.startswith(q) or q in gen:
        return 2
    if q in app.get("keywords", ""):
        return 1
    return 0


def filter_apps(apps, query):
    q = query.lower().strip()
    if not q:
        return apps
    scored = [(score_app(a, q), a) for a in apps]
    scored = [(s, a) for s, a in scored if s > 0]
    scored.sort(key=lambda x: (-x[0], x[1]["name"].lower()))
    return [a for _, a in scored]


# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = b"""
* { transition: none; }
window { background: transparent; }
#search-entry {
    background: transparent;
    border: none;
    box-shadow: none;
    color: rgba(244,247,251,0.96);
    font-family: "SF Pro Text", sans-serif;
    font-size: 17px;
    padding: 0 4px;
    caret-color: rgba(192,132,245,0.9);
    min-height: 0;
}
#search-entry:focus { outline: none; box-shadow: none; }
.prompt { color: rgba(192,132,245,0.85); font-size: 20px; }
#result-list { background: transparent; border: none; }
#result-list row {
    background: transparent;
    border-radius: 12px;
    padding: 0 4px;
    min-height: 0;
}
#result-list row:hover { background: rgba(255,255,255,0.08); outline: none; }
#result-list row:selected { background: rgba(255,255,255,0.13); outline: none; }
.app-name {
    color: rgba(244,247,251,0.96);
    font-family: "SF Pro Text", sans-serif;
    font-size: 14px;
    font-weight: 500;
}
.app-sub {
    color: rgba(244,247,251,0.48);
    font-family: "SF Pro Text", sans-serif;
    font-size: 12px;
}
scrolledwindow, viewport { background: transparent; }
"""


# ── Cairo helpers ─────────────────────────────────────────────────────────────

def rrect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x+w-r, y+r,   r, -math.pi/2, 0)
    cr.arc(x+w-r, y+h-r, r,  0,          math.pi/2)
    cr.arc(x+r,   y+h-r, r,  math.pi/2,  math.pi)
    cr.arc(x+r,   y+r,   r,  math.pi,    math.pi*1.5)
    cr.close_path()


# ── Window ────────────────────────────────────────────────────────────────────

class LauncherWindow(Gtk.Window):
    def __init__(self, apps):
        super().__init__(title="liquid-launcher")
        self.apps = apps
        self.results = list(apps)
        self._icon_theme = Gtk.IconTheme.get_default()

        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_resizable(False)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "launcher")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_exclusive_zone(self, -1)
        # No anchors = centered

        self.set_default_size(LAUNCHER_W, HEIGHT)

        # Background glass panel (Cairo)
        self.bg = Gtk.DrawingArea()
        self.bg.connect("draw", self._draw_bg)

        # Search bar
        prompt = Gtk.Label(label="›")
        prompt.get_style_context().add_class("prompt")

        self.entry = Gtk.Entry()
        self.entry.set_name("search-entry")
        self.entry.set_placeholder_text("search apps…")
        self.entry.connect("changed", self._on_changed)
        self.entry.connect("activate", self._on_activate)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        search_row.set_size_request(-1, HEADER_H)
        search_row.pack_start(prompt, False, False, 20)
        search_row.pack_start(self.entry, True, True, 0)
        search_row.pack_end(Gtk.Label(), False, False, 16)

        # Separator
        sep = Gtk.DrawingArea()
        sep.set_size_request(-1, 1)
        sep.connect("draw", lambda w, cr: (
            cr.set_source_rgba(1, 1, 1, 0.10),
            cr.rectangle(16, 0, w.get_allocated_width() - 32, 1),
            cr.fill(),
        ))

        # Results
        self.listbox = Gtk.ListBox()
        self.listbox.set_name("result-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", lambda _lb, row: self._launch(row.app))
        # Display is driven by filter/sort funcs over rows built ONCE, so a
        # keystroke never rebuilds widgets or reloads icons — it just toggles
        # visibility and re-sorts. _match_ids / _order are recomputed per query.
        self._match_ids = {id(a) for a in self.apps}
        self._order = {id(a): i for i, a in enumerate(self.apps)}
        self.listbox.set_filter_func(lambda row: id(row.app) in self._match_ids)
        self.listbox.set_sort_func(
            lambda r1, r2: self._order.get(id(r1.app), 1 << 30)
                          - self._order.get(id(r2.app), 1 << 30))

        scroll = Gtk.ScrolledWindow()
        # Horizontal never; vertical scrolls (wheel + keyboard) past the 8
        # visible rows. EXTERNAL keeps the macOS-style overlay look (no
        # scrollbar chrome) while still allowing wheel/keyboard scrolling.
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        scroll.set_size_request(-1, MAX_ROWS * ROW_H)
        scroll.add(self.listbox)
        self.scroll = scroll

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.pack_start(search_row, False, False, 0)
        content.pack_start(sep,        False, False, 0)
        content.pack_start(scroll,     False, False, 0)

        overlay = Gtk.Overlay()
        overlay.add(self.bg)
        overlay.add_overlay(content)
        self.add(overlay)

        self.connect("key-press-event", self._on_key)

        self._populate()
        GLib.idle_add(self._load_next_icons)   # decode icons off the open path
        self._select_first()

        # Live freshness: rebuild when apps are installed/removed (debounced),
        # event-driven via Gio.FileMonitor — no polling.
        self._monitors = []
        self._rebuild_pending = 0
        self._watch_app_dirs()

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw_bg(self, widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Dark glass card — Hyprland's blur shows through the partial alpha
        rrect(cr, 0, 0, w, h, LAUNCHER_R)
        cr.set_source_rgba(0.039, 0.055, 0.094, 0.72)
        cr.fill_preserve()

        # Top-left highlight gradient
        pat = cairo.LinearGradient(0, 0, w, h)
        pat.add_color_stop_rgba(0.00, 1.0, 1.0, 1.0, 0.18)
        pat.add_color_stop_rgba(0.35, 1.0, 1.0, 1.0, 0.05)
        pat.add_color_stop_rgba(0.65, 0.20, 0.80, 1.0, 0.07)
        pat.add_color_stop_rgba(1.00, 0.75, 0.52, 0.96, 0.10)
        rrect(cr, 0, 0, w, h, LAUNCHER_R)
        cr.set_source(pat)
        cr.fill()

        # Border
        cr.set_source_rgba(1, 1, 1, 0.30)
        cr.set_line_width(1.0)
        rrect(cr, 0.5, 0.5, w - 1, h - 1, LAUNCHER_R)
        cr.stroke()

    # ── Results ───────────────────────────────────────────────────────────────

    def _populate(self):
        # Built ONCE at startup for every app. Filtering/sorting afterwards is
        # pure visibility + reorder via the listbox funcs — no rebuilds. Icon
        # pixbuf decode (~the whole startup cost) is deferred to idle time via
        # _icon_queue so the window paints immediately on open.
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        self._row_for = {}
        self._icon_queue = []
        for app in self.apps:
            row = Gtk.ListBoxRow()
            row.app = app
            self._row_for[id(app)] = row

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.set_size_request(-1, ROW_H)
            box.set_margin_start(16)
            box.set_margin_end(16)

            # Icon slot — empty placeholder now, decoded lazily after show.
            img = Gtk.Image()
            img.set_size_request(ICON_PX, ICON_PX)
            img.set_halign(Gtk.Align.CENTER)
            img.set_valign(Gtk.Align.CENTER)
            box.pack_start(img, False, False, 0)
            icon_name = app.get("icon", "")
            if icon_name:
                self._icon_queue.append((img, icon_name))

            # Name only, vertically centered in the row.
            name_lbl = Gtk.Label(label=app["name"])
            name_lbl.set_halign(Gtk.Align.START)
            name_lbl.set_valign(Gtk.Align.CENTER)
            name_lbl.get_style_context().add_class("app-name")
            box.pack_start(name_lbl, True, True, 0)

            row.add(box)
            self.listbox.add(row)

        self.listbox.show_all()

    def _load_next_icons(self):
        # Decode a small batch per idle tick so the main loop stays responsive.
        for _ in range(8):
            if not self._icon_queue:
                return False
            img, icon_name = self._icon_queue.pop(0)
            try:
                if os.path.isabs(icon_name) and os.path.exists(icon_name):
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(
                        icon_name, ICON_PX, ICON_PX)
                else:
                    # FORCE_SIZE guarantees the themed icon comes back at exactly
                    # ICON_PX (otherwise the theme may hand back 16/24/32px).
                    pb = self._icon_theme.load_icon(
                        icon_name, ICON_PX, Gtk.IconLookupFlags.FORCE_SIZE)
                img.set_from_pixbuf(pb)
            except Exception:
                pass
        return bool(self._icon_queue)

    def _select_first(self):
        if self.results:
            self.listbox.select_row(self._row_for[id(self.results[0])])
        self.scroll.get_vadjustment().set_value(0)

    def _scroll_to(self, row):
        adj = self.scroll.get_vadjustment()
        alloc = row.get_allocation()
        top, bottom = alloc.y, alloc.y + alloc.height
        page, val = adj.get_page_size(), adj.get_value()
        if top < val:
            adj.set_value(top)
        elif bottom > val + page:
            adj.set_value(bottom - page)

    def _move(self, delta):
        if not self.results:
            return
        cur = self.listbox.get_selected_row()
        idx = self._order.get(id(cur.app), 0) if cur else 0
        idx = max(0, min(len(self.results) - 1, idx + delta))
        row = self._row_for[id(self.results[idx])]
        self.listbox.select_row(row)
        self._scroll_to(row)

    # ── Events ────────────────────────────────────────────────────────────────

    def _on_changed(self, entry):
        self.results = filter_apps(self.apps, entry.get_text())
        self._match_ids = {id(a) for a in self.results}
        self._order = {id(a): i for i, a in enumerate(self.results)}
        self.listbox.invalidate_filter()
        self.listbox.invalidate_sort()
        self._select_first()

    def _on_activate(self, _entry):
        row = self.listbox.get_selected_row()
        if row:
            self._launch(row.app)

    def _on_key(self, _widget, event):
        k = event.keyval
        if k == Gdk.KEY_Escape:
            self.hide_launcher()
            return True
        if k in (Gdk.KEY_Down, Gdk.KEY_Tab):
            self._move(1)
            return True
        if k in (Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab):
            self._move(-1)
            return True
        if k == Gdk.KEY_Page_Down:
            self._move(MAX_ROWS - 1)
            return True
        if k == Gdk.KEY_Page_Up:
            self._move(-(MAX_ROWS - 1))
            return True
        return False

    def _launch(self, app):
        cmd = clean_exec(app["exec"])
        try:
            GLib.spawn_command_line_async(cmd)
        except Exception as e:
            log(f"launch failed: {e}")
        self.hide_launcher()

    # ── Show / hide (the daemon keeps the process alive between opens) ──────────

    def show_launcher(self):
        self.entry.set_text("")
        self._on_changed(self.entry)   # reset to full list even if text was ""
        self.show_all()
        self.present()
        self.entry.grab_focus()

    def hide_launcher(self):
        self.hide()

    def toggle(self):
        if self.get_visible():
            self.hide_launcher()
        else:
            self.show_launcher()

    # ── Live app-list refresh (Gio.FileMonitor, debounced) ──────────────────────

    def _watch_app_dirs(self):
        for d in APP_DIRS:
            try:
                gf = Gio.File.new_for_path(str(d))
                mon = gf.monitor_directory(Gio.FileMonitorFlags.NONE, None)
                mon.connect("changed", self._on_appdir_changed)
                self._monitors.append(mon)
            except Exception as e:
                log(f"monitor {d} failed: {e}")

    def _on_appdir_changed(self, *_):
        # Debounce bursts of .desktop writes into a single rebuild ~1s later.
        if self._rebuild_pending:
            GLib.source_remove(self._rebuild_pending)
        self._rebuild_pending = GLib.timeout_add_seconds(1, self.rebuild_apps)

    def rebuild_apps(self):
        self._rebuild_pending = 0
        try:
            self.apps = load_apps()
            self._populate()
            self._on_changed(self.entry)        # re-apply current query
            GLib.idle_add(self._load_next_icons)
            log(f"rebuilt app list ({len(self.apps)} apps)")
        except Exception as e:
            log(f"rebuild failed: {e}")
        return False   # one-shot


# ── Daemon ──────────────────────────────────────────────────────────────────

class Daemon:
    """Resident launcher: builds the window once, serves toggle/show/hide/
    reload/quit over a Unix socket. Adding a verb = one entry in _HANDLERS."""

    def __init__(self, initial_show):
        self._srv = self._bind()        # claim single-instance before building
        self.win = LauncherWindow(load_apps())
        self._HANDLERS = {
            "toggle": self.win.toggle,
            "show":   self.win.show_launcher,
            "hide":   self.win.hide_launcher,
            "reload": self.win.rebuild_apps,
            "ping":   lambda: None,     # liveness probe, no-op
            "quit":   self._quit,
        }
        threading.Thread(target=self._serve, daemon=True).start()
        if initial_show:
            self.win.show_launcher()

    def _bind(self):
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(SOCK)
        except OSError:
            # Path is taken. If a daemon answers, we're redundant; otherwise
            # it's a stale socket from a crash — remove it and claim the path.
            if _daemon_alive():
                srv.close()
                raise DaemonExists()
            try:
                os.unlink(SOCK)
            except FileNotFoundError:
                pass
            srv.bind(SOCK)
        srv.listen(8)
        return srv

    def _serve(self):
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return                      # socket closed on shutdown
            try:
                cmd = conn.recv(64).decode("utf-8", "replace").strip()
            except OSError:
                cmd = ""
            finally:
                conn.close()
            if cmd:
                GLib.idle_add(self._dispatch, cmd)

    def _dispatch(self, cmd):
        handler = self._HANDLERS.get(cmd)
        if handler:
            try:
                handler()
            except Exception as e:
                log(f"command '{cmd}' failed: {e}")
        else:
            log(f"unknown command: {cmd!r}")
        return False                        # one-shot idle callback

    def _quit(self):
        Gtk.main_quit()

    def cleanup(self):
        try:
            self._srv.close()
        except Exception:
            pass
        try:
            os.unlink(SOCK)
        except FileNotFoundError:
            pass


def main():
    initial_show = "--daemon" not in sys.argv   # exec-once prewarms hidden
    try:
        daemon = Daemon(initial_show)
    except DaemonExists:
        # Lost a startup race against another daemon. If we meant to open,
        # poke the winner so the user still gets a launcher.
        if initial_show:
            _send("toggle")
        return 0

    def _sig(*_):
        GLib.idle_add(Gtk.main_quit)
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    try:
        Gtk.main()
    finally:
        daemon.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
