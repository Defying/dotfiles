#!/usr/bin/env python3
"""emoji-picker: a macOS-style emoji picker on the shared glass dropdown.

This is purely the UI layer. All emoji data, search, favorites, recents, and
insertion live in the Rust backend (`hypr-emoji-picker`); this script only
renders results and calls back into it:

    initial   → hypr-emoji-picker favorite list --json
                hypr-emoji-picker recent --json
    search    → hypr-emoji-picker search "<query>" --json --limit 80
    pick      → hypr-emoji-picker insert "<emoji>"
    star/unstar → hypr-emoji-picker favorite toggle "<emoji>"

Layout: a frosted search field on top, then a scrollable grid. With no query
the grid shows Favorites and Recents as first-class sections; once you type it
becomes a single results grid. Click or Enter inserts (delegated to the
backend's paste-into-active-window); right-click toggles a favorite star.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from glass_popup import GlassPopup  # noqa: E402

BIN = "/home/ben/.local/bin/hypr-emoji-picker"
SEARCH_LIMIT = 80
# Debounce keystrokes so we don't spawn a backend process on every letter.
SEARCH_DEBOUNCE_MS = 90
COLUMNS = 9


# ── backend glue (the only place we talk to hypr-emoji-picker) ─────────────────

def _backend(*args: str) -> str:
    try:
        out = subprocess.run(
            [BIN, *args], capture_output=True, text=True, timeout=6,
        )
        return out.stdout
    except Exception:
        return ""


def _backend_json(*args: str) -> list[dict]:
    raw = _backend(*args)
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


class Popup(GlassPopup):
    WIDTH = 430
    EXTRA_CSS = b"""
    /* Keep the scrim fully transparent (inherited from GlassPopup) so only the
       panel frosts the desktop behind it, never the whole screen. */
    /* Richer dark glass for the card itself: the shared 0.62 fill goes muddy
       over Hyprland's bright frosted backdrop, so match the launcher's crisp
       0.74 dark card with the same accent gradient. */
    .panel {
      background:
        linear-gradient(150deg, rgba(255,255,255,0.20), rgba(255,255,255,0.05) 38%,
          rgba(51,204,255,0.09) 66%, rgba(192,132,245,0.13)),
        rgba(8, 11, 20, 0.74);
      border: 1px solid rgba(255,255,255,0.34);
      border-radius: 22px;
      /* NO outer drop-shadow. A CSS box-shadow feathers translucent pixels far
         beyond the card; at this namespace's ignore_alpha 0.01 the compositor
         blurs the desktop under that whole feathered region, giving a ~1in halo
         around the card. The launcher avoids this by painting a hard-edged Cairo
         card. Keep only the inset highlight, which paints inside the card. */
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.45);
    }
    .emoji-search {
      font-size: 15px; color: #f4f7fb;
      background: rgba(255,255,255,0.08);
      border: 1px solid rgba(255,255,255,0.20);
      border-radius: 12px; padding: 9px 12px; margin-bottom: 4px;
    }
    .emoji-search:focus { border-color: rgba(192,132,245,0.75);
      background: rgba(255,255,255,0.12); }
    .emoji-section { color: rgba(244,247,251,0.52); font-size: 11px;
      font-weight: 800; letter-spacing: 1px; margin: 6px 2px 2px 4px; }
    flowboxchild {
      border-radius: 12px; padding: 2px; min-width: 40px; min-height: 40px;
    }
    flowboxchild:selected, flowboxchild:hover {
      background: linear-gradient(145deg, rgba(255,255,255,0.22),
        rgba(192,132,245,0.20));
    }
    .emoji-glyph { font-size: 24px; }
    .emoji-empty { color: rgba(244,247,251,0.55); font-size: 13px;
      margin: 18px 6px; }
    .emoji-hint  { color: rgba(244,247,251,0.40); font-size: 10px;
      margin: 4px 4px 0 4px; }
    scrolledwindow { background: transparent; }
    """

    def __init__(self, name, corner="top-center"):
        self.favorites: list[dict] = []
        self.recents: list[dict] = []
        self.results: list[dict] = []
        self.loaded = False          # initial favorites/recents fetch done
        self.query = ""
        self._search_timer = 0
        self._primary_flow: Gtk.FlowBox | None = None

        super().__init__(name, corner=corner)
        # Float it nearer the vertical centre for the macOS feel rather than
        # hugging the bar like the status dropdowns.
        self.panel.set_valign(Gtk.Align.CENTER)
        self.panel.set_margin_top(0)

        self._build_chrome()
        self.populate()
        threading.Thread(target=self._load_initial, daemon=True).start()

    # ── data loading ──────────────────────────────────────────────────────────
    def _load_initial(self):
        favs = _backend_json("favorite", "list", "--json")
        recents = _backend_json("recent", "--json")
        def apply():
            self.favorites = favs
            self.recents = recents
            self.loaded = True
            self.populate()
            return False
        GLib.idle_add(apply)

    def _run_search(self, query: str):
        rows = _backend_json("search", query, "--json", "--limit", str(SEARCH_LIMIT))
        def apply():
            # Ignore stale results if the query moved on while we were fetching.
            if query == self.query:
                self.results = rows
                self.populate()
            return False
        GLib.idle_add(apply)

    # ── persistent chrome (search entry lives outside the repopulated body) ─────
    def _build_chrome(self):
        self.entry = Gtk.SearchEntry()
        self.entry.get_style_context().add_class("emoji-search")
        self.entry.set_placeholder_text("Search emoji…")
        self.entry.connect("search-changed", self._on_search_changed)
        self.entry.connect("activate", self._on_entry_activate)
        self.entry.connect("key-press-event", self._on_entry_key)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_min_content_height(340)
        self.scroller.set_max_content_height(340)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.scroller.add(self.body)

        # Pack once; populate() only rebuilds self.body's children.
        self.panel.pack_start(self.entry, False, False, 0)
        self.panel.pack_start(self.scroller, True, True, 0)
        hint = Gtk.Label(label="Enter to insert · right-click to ★ · Esc to close",
                         xalign=0)
        hint.get_style_context().add_class("emoji-hint")
        self.panel.pack_start(hint, False, False, 0)

    # GlassPopup.populate() clears self.panel; we override to keep the chrome and
    # only rebuild the scrollable body.
    def populate(self):
        for child in self.body.get_children():
            self.body.remove(child)
        self.build()
        self.body.show_all()
        GLib.idle_add(self.entry.grab_focus)

    def build(self):
        self._primary_flow = None
        if self.query:
            if not self.results:
                self._add_empty("No emoji match “%s”" % self.query)
            else:
                self._primary_flow = self._add_section(None, self.results)
        else:
            if not self.loaded:
                self._add_empty("Loading…")
                return
            if self.favorites:
                self._primary_flow = self._add_section("FAVORITES", self.favorites)
            if self.recents:
                flow = self._add_section("RECENTS", self.recents)
                if self._primary_flow is None:
                    self._primary_flow = flow
            if not self.favorites and not self.recents:
                self._add_empty("Star emoji to keep them here.\nType to search.")
        # Pre-select the first cell so Enter-from-search works immediately.
        if self._primary_flow is not None:
            first = self._primary_flow.get_child_at_index(0)
            if first is not None:
                self._primary_flow.select_child(first)

    # ── widget builders ─────────────────────────────────────────────────────────
    def _add_empty(self, text: str):
        lbl = Gtk.Label(label=text, xalign=0.5, justify=Gtk.Justification.CENTER)
        lbl.get_style_context().add_class("emoji-empty")
        self.body.pack_start(lbl, True, True, 0)

    def _add_section(self, title: str | None, rows: list[dict]) -> Gtk.FlowBox:
        if title:
            lbl = Gtk.Label(label=title, xalign=0)
            lbl.get_style_context().add_class("emoji-section")
            self.body.pack_start(lbl, False, False, 0)

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        flow.set_max_children_per_line(COLUMNS)
        flow.set_min_children_per_line(COLUMNS)
        flow.set_homogeneous(True)
        flow.set_row_spacing(2)
        flow.set_column_spacing(2)
        flow.connect("child-activated", self._on_child_activated)
        for row in rows:
            flow.add(self._emoji_cell(row))
        self.body.pack_start(flow, False, False, 0)
        return flow

    def _emoji_cell(self, row: dict) -> Gtk.FlowBoxChild:
        emoji = row.get("emoji", "")
        child = Gtk.FlowBoxChild()
        child.emoji = emoji
        name = row.get("name", "")
        star = " ★" if row.get("favorite") else ""
        child.set_tooltip_text(f"{name}{star}" if name else emoji)

        lbl = Gtk.Label(label=emoji)
        lbl.get_style_context().add_class("emoji-glyph")

        # EventBox so right-click can toggle the favorite without inserting.
        ev = Gtk.EventBox()
        ev.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        ev.connect("button-press-event", self._on_cell_button, emoji)
        ev.add(lbl)
        child.add(ev)
        return child

    # ── interaction ─────────────────────────────────────────────────────────────
    def _on_search_changed(self, entry):
        self.query = entry.get_text().strip()
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = 0
        if not self.query:
            self.results = []
            self.populate()
            return
        q = self.query
        def fire_search():
            self._search_timer = 0
            threading.Thread(target=self._run_search, args=(q,), daemon=True).start()
            return False
        self._search_timer = GLib.timeout_add(
            SEARCH_DEBOUNCE_MS,
            fire_search,
        )

    def _on_entry_activate(self, _entry):
        emoji = self._selected_emoji()
        if emoji:
            self._insert(emoji)

    def _on_entry_key(self, _entry, ev):
        # Down / Tab drops focus from the search field into the grid.
        if ev.keyval in (Gdk.KEY_Down, Gdk.KEY_Tab) and self._primary_flow:
            first = (self._primary_flow.get_selected_children() or
                     [self._primary_flow.get_child_at_index(0)])[0]
            if first is not None:
                self._primary_flow.select_child(first)
                first.grab_focus()
                return True
        return False

    def _on_child_activated(self, _flow, child):
        emoji = getattr(child, "emoji", "")
        if emoji:
            self._insert(emoji)

    def _on_cell_button(self, _widget, ev, emoji):
        if ev.button == 3:  # right-click → toggle favorite
            self._toggle_favorite(emoji)
            return True
        if ev.button == 1 and ev.type == Gdk.EventType._2BUTTON_PRESS:
            self._insert(emoji)
            return True
        return False

    def _selected_emoji(self) -> str:
        if not self._primary_flow:
            return ""
        sel = self._primary_flow.get_selected_children()
        if sel:
            return getattr(sel[0], "emoji", "")
        first = self._primary_flow.get_child_at_index(0)
        return getattr(first, "emoji", "") if first else ""

    def _toggle_favorite(self, emoji: str):
        _backend("favorite", "toggle", emoji)
        # Refresh favorites (and recents, since stars can affect their display)
        # off-thread, then repopulate the browse view.
        threading.Thread(target=self._load_initial, daemon=True).start()

    def _insert(self, emoji: str):
        # Dismiss the overlay *before* the backend pastes, so keyboard focus is
        # back on the real window when `sendshortcut CTRL,V,activewindow` fires.
        self.hide()
        while Gtk.events_pending():
            Gtk.main_iteration()
        try:
            subprocess.Popen([BIN, "insert", emoji])
        except Exception:
            pass
        GLib.timeout_add(60, Gtk.main_quit)


def main():
    return GlassPopup.launch("emoji", Popup, corner="top-center")


if __name__ == "__main__":
    sys.exit(main())
