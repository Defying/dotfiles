#!/usr/bin/env python3
"""Refresh the AI usage bubbles when the cursor lingers on them.

Adaptive polling: read hyprctl cursorpos at 2 s while the cursor is anywhere
below the bar (no work to be done), and only escalate to 250 ms when the
cursor enters the bar's vertical band. Bubble rects come from AT-SPI on
startup and refresh every BOUNDS_REFRESH_S seconds. Re-fires only after
the cursor leaves and re-enters.

Why polling instead of AT-SPI mouse events: AT-SPI's mouse:abs callback
fires on every pixel of motion, which is much more expensive than a 2 s
hyprctl tick when the cursor is just doing normal navigation. The
adaptive approach lands at <0.2%% CPU idle.
"""

from __future__ import annotations

import os
import signal as posix_signal
import subprocess
import sys
import time

import pyatspi

POLL_NEAR_S = 0.25
POLL_FAR_S  = 2.00
BAR_BAND_PX = 80
HOVER_S     = 1.2
BOUNDS_REFRESH_S = 10.0

TARGETS = [
    {"index": 1, "signal": 8, "name": "codex"},
    {"index": 2, "signal": 9, "name": "claude"},
]


def hyprctl(args: list[str]) -> str:
    try:
        return subprocess.check_output(["hyprctl", *args], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def cursor_pos() -> tuple[int, int] | None:
    out = hyprctl(["cursorpos"])
    if not out:
        return None
    try:
        x, _, y = out.partition(",")
        return int(x.strip()), int(y.strip())
    except ValueError:
        return None


def find_waybar_frame():
    for app in pyatspi.Registry.getDesktop(0):
        if (app.name or "").lower() == "waybar":
            for i in range(app.childCount):
                child = app[i]
                if child.getRoleName() == "frame":
                    return child
    return None


def read_left_panel_rects() -> list[tuple[int, int, int, int]]:
    frame = find_waybar_frame()
    if frame is None or frame.childCount == 0:
        return []
    root = frame[0]
    if root.childCount == 0:
        return []
    left = root[0]
    rects: list[tuple[int, int, int, int]] = []
    for i in range(left.childCount):
        panel = left[i]
        try:
            ext = panel.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        except Exception:
            rects.append((-1, -1, 0, 0))
            continue
        rects.append((ext.x, ext.y, ext.width, ext.height))
    return rects


def in_rect(pt: tuple[int, int], rect: tuple[int, int, int, int]) -> bool:
    x, y = pt
    rx, ry, rw, rh = rect
    return rw > 0 and rh > 0 and rx <= x < rx + rw and ry <= y < ry + rh


def fire(sig: int) -> None:
    subprocess.run(["pkill", f"-RTMIN+{sig}", "-x", "waybar"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    def shutdown(*_):
        os._exit(0)
    posix_signal.signal(posix_signal.SIGTERM, shutdown)
    posix_signal.signal(posix_signal.SIGINT,  shutdown)

    rects = read_left_panel_rects()
    last_bounds_t = time.monotonic()
    hover_started: dict[int, float] = {}
    fired: set[int] = set()

    while True:
        now = time.monotonic()
        if now - last_bounds_t > BOUNDS_REFRESH_S or not rects:
            rects = read_left_panel_rects()
            last_bounds_t = now

        pt = cursor_pos()
        if pt is None:
            time.sleep(POLL_FAR_S)
            continue

        near_bar = pt[1] < BAR_BAND_PX
        if near_bar:
            for t in TARGETS:
                idx = t["index"]
                if idx >= len(rects):
                    continue
                if in_rect(pt, rects[idx]):
                    started = hover_started.get(idx)
                    if started is None:
                        hover_started[idx] = now
                    elif idx not in fired and (now - started) >= HOVER_S:
                        fire(t["signal"])
                        fired.add(idx)
                else:
                    hover_started.pop(idx, None)
                    fired.discard(idx)
        elif hover_started or fired:
            hover_started.clear()
            fired.clear()

        time.sleep(POLL_NEAR_S if near_bar else POLL_FAR_S)


if __name__ == "__main__":
    sys.exit(main())
