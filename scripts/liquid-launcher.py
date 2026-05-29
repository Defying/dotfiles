#!/usr/bin/env python3
"""liquid-launcher: liquid glass app launcher for Hyprland."""

import json
import math
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell  # noqa: E402

LAUNCHER_W = 580
LAUNCHER_R = 24
MAX_ROWS   = 8     # visible rows (window height); the full list scrolls beyond this
HEADER_H   = 64
ROW_H      = 50
ICON_PX    = 28   # every row's icon occupies this fixed square so names align
HEIGHT     = HEADER_H + 1 + MAX_ROWS * ROW_H + 12   # sep + rows + bottom pad



# ── App loading ───────────────────────────────────────────────────────────────

def load_apps():
    dirs = [
        Path("/usr/share/applications"),
        Path.home() / ".local/share/applications",
        Path("/usr/local/share/applications"),
    ]
    seen, apps = set(), []
    for d in dirs:
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


# ── Hyprland ──────────────────────────────────────────────────────────────────

def hyprctl(args, *, capture=False):
    try:
        if capture:
            return subprocess.check_output(["hyprctl", *args], text=True,
                                           stderr=subprocess.DEVNULL)
        completed = subprocess.run(
            ["hyprctl", *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return "" if capture else False




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
        self.connect("destroy", self._on_destroy)

        self._populate()
        self.show_all()
        self.entry.grab_focus()
        self._select_first()

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
        # pure visibility + reorder via the listbox funcs — no rebuilds.
        self._row_for = {}
        for app in self.apps:
            row = Gtk.ListBoxRow()
            row.app = app
            self._row_for[id(app)] = row

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.set_size_request(-1, ROW_H)
            box.set_margin_start(16)
            box.set_margin_end(16)

            # Icon
            icon_w = self._load_icon(app.get("icon", ""))
            box.pack_start(icon_w, False, False, 0)

            # Labels
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            name_lbl = Gtk.Label(label=app["name"])
            name_lbl.set_halign(Gtk.Align.START)
            name_lbl.get_style_context().add_class("app-name")
            labels.pack_start(name_lbl, False, False, 0)
            generic = app.get("generic", "")
            if generic and generic != app["name"]:
                sub = Gtk.Label(label=generic)
                sub.set_halign(Gtk.Align.START)
                sub.get_style_context().add_class("app-sub")
                labels.pack_start(sub, False, False, 0)
            box.pack_start(labels, True, True, 0)

            row.add(box)
            self.listbox.add(row)

        self.listbox.show_all()

    def _load_icon(self, icon_name):
        img = None
        if icon_name:
            try:
                if os.path.isabs(icon_name) and os.path.exists(icon_name):
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_name, ICON_PX, ICON_PX)
                    img = Gtk.Image.new_from_pixbuf(pb)
                else:
                    # FORCE_SIZE guarantees the themed icon comes back at exactly
                    # ICON_PX (otherwise the theme may hand back 16/24/32px).
                    pb = self._icon_theme.load_icon(
                        icon_name, ICON_PX, Gtk.IconLookupFlags.FORCE_SIZE)
                    img = Gtk.Image.new_from_pixbuf(pb)
            except Exception:
                img = None
        if img is None:
            img = Gtk.Image()  # empty, but still reserves the icon slot below
        # Pin every icon to an identical centered square so the label column
        # starts at the same x on every row, regardless of the icon's real
        # aspect ratio (file icons keep aspect) or whether it resolved at all.
        img.set_size_request(ICON_PX, ICON_PX)
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        return img

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
            self._quit()
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
            print(f"liquid-launcher: launch failed: {e}", file=sys.stderr)
        self._quit()

    def _quit(self):
        Gtk.main_quit()

    def _on_destroy(self, _widget):
        pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    apps = load_apps()

    def _sig(*_):
        GLib.idle_add(Gtk.main_quit)

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    LauncherWindow(apps)
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
