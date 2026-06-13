#!/usr/bin/env python3
"""Click-to-open AI usage panel for the waybar Codex/Claude bubbles.

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
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell, Pango  # noqa: E402

from runtime_dirs import private_runtime_dir  # noqa: E402

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "waybar"
SCRIPTS = Path(__file__).resolve().parent
ASSETS = Path.home() / "dotfiles" / "assets"
CODEX_ACCOUNTS = Path.home() / ".codex" / "accounts"
RUNTIME = private_runtime_dir("ai-usage-panel")
SCRIPT = str(Path(__file__).resolve())
PANEL_TOP_MARGIN = 6
PANEL_LEFT_MARGIN = {
    "codex": 142,
    "claude": 266,
}

SERVICES = {
    "codex": {
        "title": "codex",
        "icon": ASSETS / "openai.png",
        "cache": CACHE / "codex-usage.json",
        "url": "https://chatgpt.com/codex/settings/usage",
        "refresh": [str(SCRIPTS / "waybar-ai-refresh.sh"), "codex", "8"],
        "account": [str(SCRIPTS / "ai_accounts.py"), "codex-menu"],
    },
    "claude": {
        "title": "claude",
        "icon": ASSETS / "claude.png",
        "cache": CACHE / "claude-usage.json",
        "url": "https://claude.ai/settings/usage",
        "refresh": [str(SCRIPTS / "waybar-ai-refresh.sh"), "claude", "9"],
    },
}


def reset_label(epoch):
    left, right = reset_parts(epoch)
    return f"{left}  ·  {right}" if right else left


def reset_parts(epoch):
    if not epoch:
        return "reset time unknown", ""
    now = dt.datetime.now()
    when = dt.datetime.fromtimestamp(int(epoch))
    delta = int(epoch) - now.timestamp()
    if delta <= 0:
        return "resets now", ""
    if when.date() == now.date():
        stamp = when.strftime("%H:%M")
    else:
        stamp = when.strftime("%a %H:%M").lower()
    return f"resets {stamp}", compact_duration(delta)


def compact_duration(seconds):
    minutes = max(1, int(round(seconds / 60)))
    days, rem_minutes = divmod(minutes, 24 * 60)
    hours, mins = divmod(rem_minutes, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    return f"{mins}m"


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
        return None, "no cached usage yet; open the bar, then refresh.", None

    updated = data.get("updated_at", 0)
    age = int((dt.datetime.now().timestamp() - float(updated)) / 60) if updated else None
    if data.get("error"):
        return None, f"refresh failed: {data.get('error')}", age

    if service == "codex":
        lim = data.get("limits") or {}
        prim, sec = lim.get("primary") or {}, lim.get("secondary") or {}
        windows = [
            ("5-hour", 100 - int(round(float(prim.get("usedPercent") or 0))), prim.get("resetsAt")),
            ("weekly", 100 - int(round(float(sec.get("usedPercent") or 0))), sec.get("resetsAt")) if sec else None,
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
            ("weekly", 100 - int(round(float(sd.get("utilization") or 0))), _iso_epoch(sd.get("resets_at"))) if sd else None,
        ]
        ex = u.get("extra_usage") or {}
        extra = f"extra usage: {'enabled' if ex.get('is_enabled') else 'disabled'}" if ex else ""

    return [w for w in windows if w], extra, age


def _safe_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _age_min(data):
    updated = data.get("updated_at", 0)
    try:
        return int((dt.datetime.now().timestamp() - float(updated)) / 60) if updated else None
    except (TypeError, ValueError):
        return None


def updated_label(age):
    if age <= 0:
        return "updated just now"
    return f"updated {age}m ago"


def _active_codex_slot():
    try:
        return (CODEX_ACCOUNTS / "active").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _slot_from_cache_path(path):
    name = path.name
    if name.startswith("codex-usage-") and name.endswith(".json"):
        return name[len("codex-usage-") : -len(".json")]
    return ""


def _account_label(account):
    return account.get("label") or account.get("email") or account.get("slot") or "codex account"


def _account_identity(account, path):
    return (
        account.get("account_id")
        or account.get("email")
        or account.get("slot")
        or _slot_from_cache_path(path)
        or path.stem
    )


def _is_auto_slot(slot, account_id):
    return bool(account_id and slot.lower().endswith(f"-{account_id[:8].lower()}"))


def _cache_rank(data, path, active_slot):
    account = data.get("account") or {}
    slot = account.get("slot") or _slot_from_cache_path(path)
    account_id = account.get("account_id") or ""
    try:
        updated = float(data.get("updated_at") or 0)
    except (TypeError, ValueError):
        updated = 0
    return (
        0 if slot == active_slot else 1,
        0 if not _is_auto_slot(slot, account_id) else 1,
        0 if path.name != "codex-usage.json" else 1,
        -updated,
    )


def _remaining_from_used(window):
    return 100 - int(round(float((window or {}).get("usedPercent") or 0)))


def _codex_windows(data):
    limits = data.get("limits") or {}
    primary = limits.get("primary") or {}
    secondary = limits.get("secondary") or {}
    windows = [
        ("5-hour", _remaining_from_used(primary), primary.get("resetsAt")),
        ("weekly", _remaining_from_used(secondary), secondary.get("resetsAt")) if secondary else None,
    ]
    return [w for w in windows if w]


def _codex_extra(data):
    credits = (data.get("limits") or {}).get("credits") or {}
    if credits.get("unlimited"):
        return "credits: unlimited"
    return f"credits: {credits.get('balance', '0')}"


def load_codex_accounts():
    active_slot = _active_codex_slot()
    paths = [SERVICES["codex"]["cache"]]
    paths.extend(sorted(CACHE.glob("codex-usage-*.json")))

    chosen = {}
    for path in paths:
        data = _safe_json(path)
        if not data:
            continue
        account = data.get("account") or {}
        identity = _account_identity(account, path)
        if not identity:
            continue
        existing = chosen.get(identity)
        if existing is None or _cache_rank(data, path, active_slot) < _cache_rank(existing[0], existing[1], active_slot):
            chosen[identity] = (data, path)

    cards = []
    for data, path in chosen.values():
        account = data.get("account") or {}
        slot = account.get("slot") or _slot_from_cache_path(path)
        plan = account.get("plan") or (data.get("limits") or {}).get("planType") or ""
        cards.append(
            {
                "label": _account_label(account),
                "plan": plan,
                "slot": slot,
                "active": bool(active_slot and slot == active_slot),
                "windows": _codex_windows(data),
                "extra": _codex_extra(data),
                "age": _age_min(data),
                "error": data.get("error") or "",
            }
        )

    cards.sort(key=lambda c: (0 if c["active"] else 1, c["label"].lower(), c["slot"]))
    return cards


CSS = b"""
#ai-usage-panel {
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
  padding: 12px;
}
.title  { color: #f4f7fb; font-size: 14px; font-weight: 700; }
.sub    { color: rgba(244,247,251,0.58); font-size: 11px; }
.win    { color: rgba(244,247,251,0.82); font-size: 12px; font-weight: 700; }
.pct    { color: #f4f7fb; font-size: 12px; font-weight: 700; }
.reset  { color: rgba(244,247,251,0.58); font-size: 11px; }
.extra  { color: rgba(244,247,251,0.68); font-size: 11px; }
.account { color: #f4f7fb; font-size: 12px; font-weight: 700; }
.account-section {
  background: rgba(255,255,255,0.045);
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 10px;
  padding: 8px;
}
.account-section.active-account-section {
  background: rgba(125,211,252,0.12);
  border-color: rgba(125,211,252,0.58);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.16);
}
.account-row {
  padding: 0;
}
.active-badge {
  color: #06121a;
  background: #7dd3fc;
  border-radius: 8px;
  padding: 2px 7px;
  font-size: 10px;
  font-weight: 800;
}
label   { color: #f4f7fb; font-size: 12px; }
.usage-window {
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.13);
  border-radius: 9px;
  padding: 7px 8px;
}
button {
  color: #f4f7fb;
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.11);
  border-radius: 8px;
  padding: 6px 8px;
  font-size: 12px;
}
button:hover {
  background: rgba(255,255,255,0.12);
}
button.close {
  min-width: 24px;
  min-height: 24px;
  padding: 0;
  border-radius: 12px;
  font-size: 13px;
}
button.icon {
  min-width: 30px;
  min-height: 28px;
  padding: 0;
  border-radius: 9px;
  font-size: 13px;
}
progressbar trough {
  min-height: 10px;
  border-radius: 10px;
  background: rgba(255,255,255,0.11);
  border: 1px solid rgba(255,255,255,0.08);
}
progressbar progress {
  min-height: 10px;
  border-radius: 10px;
  background: #7dd3fc;
}
progressbar.warn progress {
  background: #f8df9b;
}
progressbar.danger progress {
  background: #ff6b6b;
}
"""


def pid_file(service):
    return RUNTIME / f"ai-usage-{service}.pid"


def close_from_pidfile(path):
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        os.kill(pid, signal.SIGTERM)
        path.unlink(missing_ok=True)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        path.unlink(missing_ok=True)
        return False


def running_pids(service):
    try:
        out = subprocess.check_output(
            ["pgrep", "-u", str(os.getuid()), "-f", f"{SCRIPT} {service}"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
    except Exception:
        return []
    own = os.getpid()
    pids = []
    for line in out.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid != own:
            pids.append(pid)
    return pids


def close_existing(service):
    closed = close_from_pidfile(pid_file(service))
    for pid in running_pids(service):
        try:
            os.kill(pid, signal.SIGTERM)
            closed = True
        except OSError:
            pass
    return closed


class Panel(Gtk.Window):
    def __init__(self, service):
        self.service = service
        self.cfg = SERVICES[service]
        self._codex_cards = []
        super().__init__(title=f"ai-usage-{service}")
        self._focus_seen = False
        self._seat = None
        self.set_name("ai-usage-panel")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_app_paintable(True)
        self.set_size_request(340, -1)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "ai-usage")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, PANEL_TOP_MARGIN)
        GtkLayerShell.set_margin(
            self,
            GtkLayerShell.Edge.LEFT,
            PANEL_LEFT_MARGIN.get(service, 142),
        )

        self.connect("key-press-event", self._on_key)
        self.connect("focus-in-event", self._on_focus_in)
        self.connect("focus-out-event", self._on_focus_out)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.panel.get_style_context().add_class("panel")
        self.add(self.panel)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._on_button_press)
        self.populate()

    def show_all(self):
        super().show_all()
        self.present()
        GLib.idle_add(self._grab_pointer)

    def populate(self):
        for child in self.panel.get_children():
            self.panel.remove(child)
        self.build()
        self.panel.show_all()

    def build(self):
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = self.cfg["icon"]
        if icon.exists():
            img = Gtk.Image.new_from_pixbuf(
                GdkPixbuf.Pixbuf.new_from_file_at_size(str(icon), 20, 20)
            )
            header.pack_start(img, False, False, 0)
        title = Gtk.Label(label=f"{self.cfg['title']} usage", xalign=0)
        title.get_style_context().add_class("title")
        header.pack_start(title, True, True, 0)
        header.pack_end(self._icon_button("", "close", self.close, "close"), False, False, 0)
        self.panel.pack_start(header, False, False, 0)

        if self.service == "codex":
            self._build_codex_profiles()
        else:
            windows, extra, age = load_usage(self.service)
            if windows is None:
                msg = Gtk.Label(label=extra, xalign=0)
                msg.set_line_wrap(True)
                msg.set_max_width_chars(42)
                self.panel.pack_start(msg, False, False, 0)
            else:
                for label, remaining, epoch in windows:
                    self.panel.pack_start(self._window_row(label, remaining, epoch), False, False, 0)
                if extra:
                    el = Gtk.Label(label=extra, xalign=0)
                    el.set_line_wrap(True)
                    el.set_max_width_chars(42)
                    el.get_style_context().add_class("extra")
                    self.panel.pack_start(el, False, False, 0)
                if age is not None:
                    al = Gtk.Label(label=updated_label(age), xalign=0)
                    al.get_style_context().add_class("reset")
                    self.panel.pack_start(al, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_halign(Gtk.Align.END)
        buttons = [
            ("", "refresh", self._refresh),
            ("", "open usage", self._open_url),
        ]
        if self.service == "codex":
            next_slot = self._next_codex_slot()
            if next_slot:
                buttons.insert(0, ("⇄", "swap profile", lambda slot=next_slot: self._activate_codex(slot)))
        if self.cfg.get("account"):
            buttons.append(("", "account", self._account_menu))
        for glyph, tooltip, callback in buttons:
            actions.pack_start(self._icon_button(glyph, tooltip, callback), False, False, 0)
        self.panel.pack_start(actions, False, False, 0)

    def _build_codex_profiles(self):
        cards = load_codex_accounts()
        self._codex_cards = cards
        if not cards:
            msg = Gtk.Label(label="no cached usage yet; open the bar, then refresh.", xalign=0)
            msg.set_line_wrap(True)
            msg.set_max_width_chars(42)
            self.panel.pack_start(msg, False, False, 0)
            return

        for card in cards:
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            section.get_style_context().add_class("account-section")
            if card["active"]:
                section.get_style_context().add_class("active-account-section")
            section.pack_start(self._account_row(card), False, False, 0)
            if card["error"]:
                msg = Gtk.Label(label=f"refresh failed: {card['error']}", xalign=0)
                msg.set_line_wrap(True)
                msg.set_max_width_chars(42)
                msg.get_style_context().add_class("extra")
                section.pack_start(msg, False, False, 0)
                self.panel.pack_start(section, False, False, 0)
                continue
            for label, remaining, epoch in card["windows"]:
                section.pack_start(self._window_row(label, remaining, epoch), False, False, 0)
            extra = card["extra"]
            if card["age"] is not None:
                extra = f"{extra}  ·  {updated_label(card['age'])}"
            if extra:
                el = Gtk.Label(label=extra, xalign=0)
                el.set_line_wrap(True)
                el.set_max_width_chars(42)
                el.get_style_context().add_class("extra")
                section.pack_start(el, False, False, 0)
            self.panel.pack_start(section, False, False, 0)

    def _account_row(self, card):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class("account-row")
        label = card["label"]
        if card["plan"]:
            label = f"{label} ({card['plan']})"
        text = Gtk.Label(label=label, xalign=0)
        text.set_ellipsize(Pango.EllipsizeMode.END)
        text.get_style_context().add_class("account")
        row.pack_start(text, True, True, 0)
        if card["active"]:
            badge = Gtk.Label(label="ACTIVE")
            badge.get_style_context().add_class("active-badge")
            row.pack_end(badge, False, False, 0)
        elif card["slot"]:
            button = self._icon_button("", "use this profile", lambda slot=card["slot"]: self._activate_codex(slot))
            row.pack_end(button, False, False, 0)
        return row

    def _next_codex_slot(self):
        for card in self._codex_cards:
            if card["slot"] and not card["active"]:
                return card["slot"]
        return ""

    def _icon_button(self, glyph, tooltip, callback, class_name="icon"):
        button = Gtk.Button(label=glyph)
        button.get_style_context().add_class(class_name)
        button.set_tooltip_text(tooltip)
        button.connect("clicked", lambda *_: callback())
        return button

    def _window_row(self, label, remaining, epoch):
        remaining = max(0, min(100, int(remaining)))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.get_style_context().add_class("usage-window")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=label, xalign=0)
        name.get_style_context().add_class("win")
        pct = Gtk.Label(label=f"{remaining}% left", xalign=1)
        pct.set_ellipsize(Pango.EllipsizeMode.END)
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

        reset_left, reset_right = reset_parts(epoch)
        reset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        reset = Gtk.Label(label=reset_left, xalign=0)
        reset.set_ellipsize(Pango.EllipsizeMode.END)
        reset.get_style_context().add_class("reset")
        reset_row.pack_start(reset, True, True, 0)
        if reset_right:
            countdown = Gtk.Label(label=reset_right, xalign=1)
            countdown.get_style_context().add_class("reset")
            reset_row.pack_end(countdown, False, False, 0)
        box.pack_start(reset_row, False, False, 0)
        return box

    def _refresh(self):
        def worker():
            try:
                subprocess.run(
                    self.cfg["refresh"],
                    timeout=20,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass
            GLib.idle_add(self.populate)
        threading.Thread(target=worker, daemon=True).start()

    def _open_url(self):
        subprocess.Popen(
            ["xdg-open", self.cfg["url"]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.close()

    def _account_menu(self):
        command = self.cfg.get("account")
        if command:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        self.close()

    def _activate_codex(self, slot):
        def worker():
            try:
                subprocess.run(
                    [str(SCRIPTS / "ai_accounts.py"), "codex-activate", slot],
                    timeout=10,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.run(
                    self.cfg["refresh"],
                    timeout=20,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass
            GLib.idle_add(self.populate)

        threading.Thread(target=worker, daemon=True).start()

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _on_focus_in(self, *_):
        self._focus_seen = True
        return False

    def _on_focus_out(self, *_):
        if not self._focus_seen:
            return False
        GLib.timeout_add(80, self._close_if_unfocused)
        return False

    def _close_if_unfocused(self):
        if not self.has_toplevel_focus():
            self.close()
        return False

    def _grab_pointer(self):
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

    def _on_button_press(self, _widget, event):
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


def launch(service):
    if close_existing(service):
        return 0
    for other in SERVICES:
        if other != service:
            close_existing(other)
    pf = pid_file(service)
    pf.write_text(str(os.getpid()), encoding="utf-8")
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())
    signal.signal(signal.SIGINT, lambda *_: Gtk.main_quit())
    window = Panel(service)
    window.show_all()
    try:
        Gtk.main()
    finally:
        try:
            if pf.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pf.unlink()
        except OSError:
            pass
    return 0


def main():
    service = sys.argv[1] if len(sys.argv) > 1 else "codex"
    if service not in SERVICES:
        print(f"usage: {sys.argv[0]} codex|claude", file=sys.stderr)
        return 2
    return launch(service)


if __name__ == "__main__":
    sys.exit(main())
