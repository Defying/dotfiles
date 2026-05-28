#!/usr/bin/env python3
"""Trackpad gestures for Hyprland on the Apple SPI trackpad.

Reads raw multitouch events from /dev/input/event* (any device whose name
contains "trackpad" or "touchpad") and recognises:

    * RIGHT-EDGE 2-finger swipe-left
        → macOS-style "open the notification tray". Both fingers must start
          in the rightmost EDGE_FRAC of the trackpad and travel at least
          TRIGGER_MM millimetres to the left before lift / before another
          touch joins.

The daemon is single-purpose and event-driven: no polling, no per-frame
work. CPU at rest is essentially zero. Add new gestures by adding another
Detector subclass and registering it in `DETECTORS`.

Requirements:
    * `python3-evdev` installed.
    * User must be in the `input` group (or have read access to the
      trackpad evdev node via udev/uaccess). After running
      `sudo usermod -aG input <user>` you have to log out + back in for
      the new group to apply to the Hyprland session.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import evdev
from evdev import ecodes as ec

TOUCH_NAMES   = ("trackpad", "touchpad")
NOTIF_TOGGLE  = "/home/ben/dotfiles/scripts/waybar-notifications.sh"


# ── Touch state ────────────────────────────────────────────────────────────

@dataclass
class Touch:
    tracking_id: int = -1
    # start_x/start_y are None until the first ABS_MT_POSITION_* after the
    # tracking id flips positive — this avoids confusing a fresh touch with
    # a stale slot whose cur_x was inherited from the previous finger.
    start_x: int | None = None
    start_y: int | None = None
    cur_x: int | None   = None
    cur_y: int | None   = None
    started_at: float = 0.0


@dataclass
class Pad:
    x_min: int
    x_max: int
    y_min: int
    y_max: int
    x_res: int  # units per mm
    y_res: int

    @property
    def x_range(self) -> int:
        return self.x_max - self.x_min

    @property
    def y_range(self) -> int:
        return self.y_max - self.y_min

    def x_mm(self, dx_units: int) -> float:
        return dx_units / max(self.x_res, 1)

    def edge_x(self, frac: float) -> int:
        return int(self.x_max - frac * self.x_range)


# ── Detectors ──────────────────────────────────────────────────────────────

class Detector:
    """Override `evaluate(active_touches, pad)` to test the gesture; return
    True if it fired (so the dispatcher locks the gesture state until all
    fingers lift). Override `reset()` if you carry per-gesture flags."""

    name = "detector"

    def evaluate(self, touches: list[Touch], pad: Pad) -> bool:
        return False

    def reset(self) -> None:
        pass


class RightEdgeSwipeLeft(Detector):
    """Two fingers, both starting in the rightmost EDGE_FRAC of the pad,
    drifting LEFT at least TRIGGER_MM before lift."""

    name      = "right-edge-swipe-left"
    EDGE_FRAC = 0.18    # rightmost 18% of the pad counts as the edge
    TRIGGER_MM = 18.0   # ~18 mm of leftward travel commits the gesture
    MAX_TIME_S = 0.9

    def __init__(self, action) -> None:
        self.action = action

    def evaluate(self, touches: list[Touch], pad: Pad) -> bool:
        if len(touches) != 2:
            return False
        edge_x = pad.edge_x(self.EDGE_FRAC)
        if not all(t.start_x >= edge_x for t in touches):
            return False
        if (time.monotonic() - min(t.started_at for t in touches)) > self.MAX_TIME_S:
            return False
        dx_avg_units = sum(t.cur_x - t.start_x for t in touches) / 2
        if pad.x_mm(-dx_avg_units) < self.TRIGGER_MM:
            return False
        self.action()
        return True


# ── Action implementations ─────────────────────────────────────────────────

def toggle_notifications() -> None:
    if not os.path.isfile(NOTIF_TOGGLE):
        return
    subprocess.Popen(
        ["bash", NOTIF_TOGGLE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


DETECTORS: list[Detector] = [
    RightEdgeSwipeLeft(toggle_notifications),
]


# ── Device discovery / parsing ─────────────────────────────────────────────

def find_trackpads() -> list[evdev.InputDevice]:
    out = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except (OSError, PermissionError):
            continue
        if any(kw in dev.name.lower() for kw in TOUCH_NAMES):
            out.append(dev)
    return out


def pad_geometry(dev: evdev.InputDevice) -> Pad | None:
    abs_caps = dict(dev.capabilities().get(ec.EV_ABS, []))
    x = abs_caps.get(ec.ABS_MT_POSITION_X)
    y = abs_caps.get(ec.ABS_MT_POSITION_Y)
    if not x or not y or not x.resolution or not y.resolution:
        return None
    return Pad(
        x_min=x.min, x_max=x.max, x_res=x.resolution,
        y_min=y.min, y_max=y.max, y_res=y.resolution,
    )


class TouchTracker:
    def __init__(self, pad: Pad) -> None:
        self.pad = pad
        self.slots: dict[int, Touch] = {}
        self.cur_slot = 0
        self.gesture_locked = False  # true after a gesture fires until reset

    def active(self) -> list[Touch]:
        # A touch only counts once its start position has been captured —
        # before the first ABS_MT_POSITION_* event we have no edge to test.
        return [
            t for t in self.slots.values()
            if t.tracking_id >= 0 and t.start_x is not None and t.cur_x is not None
        ]

    def reset_locks(self) -> None:
        if self.gesture_locked and not self.active():
            self.gesture_locked = False
            for d in DETECTORS:
                d.reset()

    def handle(self, event) -> bool:
        """Returns True iff this event ended a frame (SYN_REPORT)."""
        if event.type == ec.EV_ABS:
            self._handle_abs(event)
        return event.type == ec.EV_SYN and event.code == ec.SYN_REPORT

    def _handle_abs(self, event) -> None:
        code = event.code
        if code == ec.ABS_MT_SLOT:
            self.cur_slot = event.value
            return
        slot = self.slots.setdefault(self.cur_slot, Touch())
        if code == ec.ABS_MT_TRACKING_ID:
            if event.value < 0:
                slot.tracking_id = -1
                slot.start_x = slot.start_y = None
                slot.cur_x = slot.cur_y = None
            else:
                slot.tracking_id = event.value
                slot.start_x = slot.start_y = None
                slot.cur_x = slot.cur_y = None
                slot.started_at = time.monotonic()
        elif code == ec.ABS_MT_POSITION_X:
            slot.cur_x = event.value
            if slot.tracking_id >= 0 and slot.start_x is None:
                slot.start_x = event.value
        elif code == ec.ABS_MT_POSITION_Y:
            slot.cur_y = event.value
            if slot.tracking_id >= 0 and slot.start_y is None:
                slot.start_y = event.value

    def frame(self) -> None:
        active = self.active()
        if not active:
            self.reset_locks()
            return
        if self.gesture_locked:
            return
        for det in DETECTORS:
            if det.evaluate(active, self.pad):
                self.gesture_locked = True
                break


# ── Main loop ──────────────────────────────────────────────────────────────

def loop(devices: list[evdev.InputDevice]) -> None:
    trackers: dict[int, TouchTracker] = {}
    fd_to_dev: dict[int, evdev.InputDevice] = {}
    for dev in devices:
        pad = pad_geometry(dev)
        if pad is None:
            continue
        trackers[dev.fd] = TouchTracker(pad)
        fd_to_dev[dev.fd] = dev

    if not trackers:
        print("hypr-trackpad-gestures: no usable trackpad", file=sys.stderr)
        return

    poller = select.poll()
    for fd in fd_to_dev:
        poller.register(fd, select.POLLIN)

    while True:
        for fd, _ in poller.poll():
            dev = fd_to_dev[fd]
            tracker = trackers[fd]
            try:
                for event in dev.read():
                    if tracker.handle(event):
                        tracker.frame()
            except OSError:
                return


def main() -> int:
    def shutdown(*_):
        os._exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT,  shutdown)

    devices = find_trackpads()
    if not devices:
        print("hypr-trackpad-gestures: no trackpad found (need read on "
              "/dev/input/event*; user must be in the input group)",
              file=sys.stderr)
        return 1
    loop(devices)
    return 0


if __name__ == "__main__":
    sys.exit(main())
