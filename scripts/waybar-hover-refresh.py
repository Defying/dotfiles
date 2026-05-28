#!/usr/bin/env python3
"""Refresh the AI usage bubbles when the cursor lingers on them.

waybar's config exposes no on-enter event, so the trick is:
  1. Find each AI bubble's on-screen rect via AT-SPI (same source the
     liquid-glass daemon used) — accurate even after the bubble width
     shifts because of new content.
  2. Poll hyprctl cursorpos at 5 Hz.
  3. When the cursor has stayed inside a bubble for HOVER_S seconds,
     pkill -RTMIN+SIG waybar to fire that module's refresh signal.
  4. Don't fire again until the cursor leaves and re-enters, so a
     long park doesn't spam signals.

The AT-SPI bubble names are matched by the label text inside the panel
(e.g. "12%" / "login" / "rate") — we look up the panels under the bar's
left section and key them by index.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import pyatspi

POLL_S       = 0.2
HOVER_S      = 1.2
REBOUNDS_S   = 5.0  # how often to re-read AT-SPI bubble bounds

# Order matches modules-left: workspaces, codex-tokens, claude-tokens.
# We refresh the second + third panels; their signal numbers come from
# config.jsonc.
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


def read_bubble_rects(frame) -> list[tuple[int, int, int, int]]:
    """Return rects for each panel in the LEFT section (modules-left order)."""
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
    subprocess.run(["pkill", f"-RTMIN+{sig}", "waybar"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    def shutdown(*_):
        os._exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT,  shutdown)

    rects: list[tuple[int, int, int, int]] = []
    last_bounds_t = 0.0
    hover_started: dict[int, float] = {}  # index -> monotonic start time
    fired: set[int]                   = set()

    while True:
        now = time.monotonic()
        if now - last_bounds_t > REBOUNDS_S or not rects:
            frame = find_waybar_frame()
            rects = read_bubble_rects(frame)
            last_bounds_t = now

        pt = cursor_pos()
        if pt is None:
            time.sleep(POLL_S)
            continue

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

        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
