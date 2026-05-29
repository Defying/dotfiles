#!/usr/bin/env python3
"""Adaptive display brightness from the M1 ambient light sensor.

The Asahi AOP ALS exposes lux at
/sys/.../als.0.auto/iio:device0/in_illuminance_input. This daemon samples it
at a low rate (one tiny sysfs read every POLL_S — not a busy loop) and eases
the panel brightness along a log curve.

Efficiency: brightness is set IN-PROCESS — a direct sysfs write when the udev
rule has made the node group-writable (90-backlight-perms.rules / video group),
otherwise one cached logind D-Bus SetBrightness call. Either way there are NO
process spawns, even during a fade. It only acts when:

  * enabled (toggle file absent), and
  * the smoothed target differs from current by more than a deadband, and
  * the session isn't idle-dimmed (the idle fade's state file is absent), and
  * the user hasn't just changed brightness manually (then we back off and
    adopt their level as the new baseline, so auto never fights a manual set).

Toggle: touch/rm ~/.cache/hypr/auto-brightness.off  (bound to Super+Shift+B).
"""

from __future__ import annotations

import math
import os
import signal
import sys
import time
from pathlib import Path

ALS = Path("/sys/bus/iio/devices/iio:device0/in_illuminance_input")
BL = Path("/sys/class/backlight/apple-panel-bl")
BRIGHT = BL / "brightness"
MAXF = BL / "max_brightness"
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
IDLE_STATE = RUNTIME / "hypr-brightness-fade" / "saved-brightness"
TOGGLE_OFF = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "hypr" / "auto-brightness.off"

POLL_S = 4.0
DEADBAND_PCT = 5
MANUAL_TOL_FRAC = 0.05
BACKOFF_S = 300
LUX_EMA = 0.35           # lower = smoother lux
FADE_SECONDS = 1.8       # gentle macOS-like ramp per adjustment
FADE_STEPS = 110         # fine steps (in-process writes are cheap) → no visible stepping


def read_int(path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


class Brightness:
    """In-process brightness setter: direct sysfs write, else logind D-Bus.
    No subprocess spawns on either path."""

    def __init__(self):
        self._bus = None
        self._direct = os.access(BRIGHT, os.W_OK)

    def _logind(self, raw):
        if self._bus is None:
            from gi.repository import Gio
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        from gi.repository import Gio, GLib
        self._bus.call_sync(
            "org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
            "org.freedesktop.login1.Session", "SetBrightness",
            GLib.Variant("(ssu)", ("backlight", "apple-panel-bl", int(raw))),
            None, Gio.DBusCallFlags.NONE, 1000, None,
        )

    def set_raw(self, raw):
        raw = int(raw)
        if self._direct:
            try:
                with open(BRIGHT, "w") as f:
                    f.write(str(raw))
                return
            except OSError:
                self._direct = False   # perms changed under us; fall back
        try:
            self._logind(raw)
        except Exception:
            pass

    def fade(self, start, target, seconds=FADE_SECONDS, steps=FADE_STEPS):
        if start == target:
            return
        delay = seconds / steps
        for i in range(1, steps + 1):
            t = i / steps
            e = 1 - (1 - t) ** 3            # ease-out cubic
            self.set_raw(round(start + (target - start) * e))
            time.sleep(delay)


def lux_to_pct(lux):
    # ~10% in the dark, ~50% in a normal room (~75 lux), 100% in daylight.
    return max(10, min(100, int(round(12 + 22 * math.log10(max(0.0, lux) + 1)))))


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    if not ALS.exists():
        print("auto-brightness: no ALS at", ALS, file=sys.stderr)
        return 1
    max_raw = read_int(MAXF) or 0
    if max_raw <= 0:
        print("auto-brightness: no max brightness", file=sys.stderr)
        return 1
    tol = max(1, int(max_raw * MANUAL_TOL_FRAC))
    setter = Brightness()

    last_set_raw = None
    backoff_until = 0.0
    ema_lux = None

    while True:
        time.sleep(POLL_S)

        if TOGGLE_OFF.exists():
            last_set_raw = None
            ema_lux = None
            continue
        if IDLE_STATE.exists():
            continue

        raw_lux = read_int(ALS)
        if raw_lux is None:
            continue
        ema_lux = raw_lux if ema_lux is None else (LUX_EMA * raw_lux + (1 - LUX_EMA) * ema_lux)

        cur_raw = read_int(BRIGHT)
        if cur_raw is None:
            continue

        # Manual-change detection: brightness drifted from what we set.
        if last_set_raw is not None and abs(cur_raw - last_set_raw) > tol:
            last_set_raw = cur_raw
            backoff_until = time.time() + BACKOFF_S
            continue
        if time.time() < backoff_until:
            continue

        target_pct = lux_to_pct(ema_lux)
        cur_pct = round(cur_raw * 100 / max_raw)
        if abs(target_pct - cur_pct) < DEADBAND_PCT:
            continue

        target_raw = int(max_raw * target_pct / 100)
        setter.fade(cur_raw, target_raw)
        last_set_raw = target_raw


if __name__ == "__main__":
    sys.exit(main())
