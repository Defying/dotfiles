#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import signal
import subprocess
import sys
from dataclasses import dataclass

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk


GHOSTTY_CLASS = "com.mitchellh.ghostty"
TITLE_PREFIX = "ghostty-glass-backdrop:"
LOCK_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "ghostty-glass-backdrop.lock")
POLL_MS = 33
RADIUS = 28


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Target:
    key: str
    ghostty_address: str
    rect: Rect


def hyprctl(args: list[str], *, capture: bool = False) -> str | bool:
    cmd = ["hyprctl", *args]
    try:
        if capture:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        return subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
    except Exception:
        return "" if capture else False


def hypr_json(args: list[str]) -> object:
    out = hyprctl([*args, "-j"], capture=True)
    if not isinstance(out, str) or not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def rounded_rectangle(cr, x, y, width, height, radius):
    right = x + width
    bottom = y + height
    cr.new_sub_path()
    cr.arc(right - radius, y + radius, radius, -math.pi / 2.0, 0.0)
    cr.arc(right - radius, bottom - radius, radius, 0.0, math.pi / 2.0)
    cr.arc(x + radius, bottom - radius, radius, math.pi / 2.0, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, math.pi * 1.5)
    cr.close_path()


class BackdropWindow(Gtk.Window):
    def __init__(self, key: str) -> None:
        super().__init__(title=f"{TITLE_PREFIX}{key}")
        self.key = key
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self.draw)
        self.add(self.area)

    def draw(self, area, cr):
        width = area.get_allocated_width()
        height = area.get_allocated_height()

        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        rounded_rectangle(cr, 0, 0, width, height, RADIUS)
        cr.clip_preserve()
        cr.set_source_rgba(0.039, 0.055, 0.094, 0.46)
        cr.fill_preserve()

        sheen = cairo.LinearGradient(0, 0, width, height)
        sheen.add_color_stop_rgba(0.00, 1.0, 1.0, 1.0, 0.20)
        sheen.add_color_stop_rgba(0.42, 1.0, 1.0, 1.0, 0.05)
        sheen.add_color_stop_rgba(0.68, 0.20, 0.80, 1.0, 0.08)
        sheen.add_color_stop_rgba(1.00, 0.75, 0.52, 0.96, 0.10)
        cr.set_source(sheen)
        cr.fill()

        rounded_rectangle(cr, 0.5, 0.5, width - 1, height - 1, RADIUS - 0.5)
        cr.set_line_width(1.0)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.28)
        cr.stroke()


class BackdropDaemon:
    def __init__(self) -> None:
        self.windows: dict[str, BackdropWindow] = {}
        self.stopping = False

    def targets(self) -> dict[str, Target]:
        monitors = hypr_json(["monitors"])
        clients = hypr_json(["clients"])
        if not isinstance(monitors, list) or not isinstance(clients, list):
            return {}

        focused_monitor = next((m for m in monitors if isinstance(m, dict) and m.get("focused")), None)
        if focused_monitor is None:
            return {}

        active_workspace = focused_monitor.get("activeWorkspace")
        if not isinstance(active_workspace, dict):
            return {}
        active_id = active_workspace.get("id")
        monitor_id = focused_monitor.get("id")

        result: dict[str, Target] = {}
        for client in clients:
            if not isinstance(client, dict):
                continue
            if client.get("class") != GHOSTTY_CLASS:
                continue
            if not client.get("floating"):
                continue
            if client.get("hidden") or not client.get("mapped", True):
                continue
            if client.get("monitor") != monitor_id:
                continue
            workspace = client.get("workspace")
            if not isinstance(workspace, dict) or workspace.get("id") != active_id:
                continue

            at = client.get("at") or []
            size = client.get("size") or []
            if len(at) != 2 or len(size) != 2:
                continue
            try:
                rect = Rect(int(at[0]), int(at[1]), int(size[0]), int(size[1]))
            except (TypeError, ValueError):
                continue
            if rect.width <= 2 or rect.height <= 2:
                continue

            address = str(client.get("address") or "")
            key = str(client.get("stableId") or address).replace(":", "-")
            if not address:
                continue
            result[key] = Target(key=key, ghostty_address=address, rect=rect)

        return result

    def backdrop_addresses(self) -> dict[str, list[str]]:
        clients = hypr_json(["clients"])
        if not isinstance(clients, list):
            return {}

        result: dict[str, list[str]] = {}
        for client in clients:
            if not isinstance(client, dict):
                continue
            title = str(client.get("title") or "")
            if not title.startswith(TITLE_PREFIX):
                continue
            address = str(client.get("address") or "")
            if address:
                result.setdefault(title.removeprefix(TITLE_PREFIX), []).append(address)
        return result

    def close_orphan_backdrops(self) -> None:
        for addresses in self.backdrop_addresses().values():
            for address in addresses:
                hyprctl(["dispatch", "closewindow", f"address:{address}"])

    def ensure_window(self, target: Target) -> None:
        if target.key not in self.windows:
            window = BackdropWindow(target.key)
            window.set_default_size(target.rect.width, target.rect.height)
            window.area.set_size_request(target.rect.width, target.rect.height)
            window.show_all()
            self.windows[target.key] = window

    def sync_geometry(self, targets: dict[str, Target]) -> None:
        addresses = self.backdrop_addresses()
        for key, target in targets.items():
            existing = addresses.get(key) or []
            if not existing:
                continue
            address = existing[0]
            for duplicate in existing[1:]:
                hyprctl(["dispatch", "closewindow", f"address:{duplicate}"])
            rect = target.rect
            hyprctl(["dispatch", "resizewindowpixel", f"exact {rect.width} {rect.height},address:{address}"])
            hyprctl(["dispatch", "movewindowpixel", f"exact {rect.x} {rect.y},address:{address}"])
            hyprctl(["dispatch", "alterzorder", f"bottom,address:{address}"])
            hyprctl(["dispatch", "alterzorder", f"top,address:{target.ghostty_address}"])

    def tick(self) -> bool:
        if self.stopping:
            self.cleanup()
            Gtk.main_quit()
            return False

        targets = self.targets()
        for key in list(self.windows):
            if key not in targets:
                self.windows[key].destroy()
                del self.windows[key]

        for target in targets.values():
            self.ensure_window(target)

        self.sync_geometry(targets)
        return True

    def cleanup(self) -> None:
        for window in list(self.windows.values()):
            window.destroy()
        self.windows.clear()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()
    if not args.daemon:
        parser.print_help()
        return 2

    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    lock_file.write(f"{os.getpid()}\n")
    lock_file.flush()

    daemon = BackdropDaemon()
    daemon.close_orphan_backdrops()

    def request_stop(*_args):
        daemon.stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    GLib.timeout_add(POLL_MS, daemon.tick)
    daemon.tick()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
