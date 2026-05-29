#!/usr/bin/env python3
"""macOS-style waybar autohide while a window is fullscreen.

Event-driven: blocks on Hyprland's socket2 and does NOTHING until a window
goes fullscreen. Only then does it poll the cursor (10 Hz) to reveal the bar
when the pointer hits the top edge and hide it again when it leaves. When
nothing is fullscreen the poll is stopped entirely — this is the single
sanctioned poll in the setup, strictly gated behind fullscreen state.

waybar sits on the "top" layer, which renders above fullscreen windows, so a
plain SIGUSR1 visibility toggle is enough to slide it over the video.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402  (GLib main loop for the timer)

RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
HIS = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
SOCK2 = f"{RUNTIME}/hypr/{HIS}/.socket2.sock"
CMD_SOCK = f"{RUNTIME}/hypr/{HIS}/.socket.sock"

POLL_MS = 100      # 10 Hz, only while fullscreen
REVEAL_PX = 6      # cursor at/above this Y reveals the bar
HIDE_PX = 50       # cursor below this Y (past the bar) hides it; gap = hysteresis


class AutoHide:
    def __init__(self):
        self.fullscreen = False
        self.bar_visible = True     # waybar starts shown
        self.poll_id = 0

    # ── waybar visibility (SIGUSR1 toggles; we track state so we only flip
    #    when the desired state differs from the current one) ────────────────
    def _toggle_bar(self):
        subprocess.run(["pkill", "-SIGUSR1", "-x", "waybar"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def show_bar(self):
        if not self.bar_visible:
            self._toggle_bar()
            self.bar_visible = True

    def hide_bar(self):
        if self.bar_visible:
            self._toggle_bar()
            self.bar_visible = False

    # ── cursor Y via the Hyprland command socket (no hyprctl process spawn) ──
    def cursor_y(self):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.1)
            s.connect(CMD_SOCK)
            s.sendall(b"cursorpos")
            data = s.recv(64).decode("utf-8", "replace")
            s.close()
            return int(data.split(",")[1])
        except Exception:
            return None

    def _poll(self):
        if not self.fullscreen:
            self.poll_id = 0
            return False
        y = self.cursor_y()
        if y is not None:
            if y <= REVEAL_PX:
                self.show_bar()
            elif y >= HIDE_PX:
                self.hide_bar()
        return True  # keep polling while fullscreen

    def enter_fullscreen(self):
        if self.fullscreen:
            return
        self.fullscreen = True
        self.hide_bar()
        if not self.poll_id:
            self.poll_id = GLib.timeout_add(POLL_MS, self._poll)

    def exit_fullscreen(self):
        if not self.fullscreen:
            return
        self.fullscreen = False
        if self.poll_id:
            GLib.source_remove(self.poll_id)
            self.poll_id = 0
        self.show_bar()   # always restore the bar when leaving fullscreen

    def on_event(self, event, payload):
        if event == "fullscreen":
            # payload is "1" (a window entered fullscreen) or "0" (none left)
            if payload.strip() == "1":
                GLib.idle_add(self.enter_fullscreen)
            else:
                GLib.idle_add(self.exit_fullscreen)


def reader_loop(ah: AutoHide):
    if not HIS:
        print("waybar-autohide: not inside a Hyprland session", file=sys.stderr)
        return
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(SOCK2)
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            event, _, payload = line.decode("utf-8", "replace").partition(">>")
            ah.on_event(event, payload)


def main():
    def shutdown(*_):
        os._exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    ah = AutoHide()
    threading.Thread(target=reader_loop, args=(ah,), daemon=True).start()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
