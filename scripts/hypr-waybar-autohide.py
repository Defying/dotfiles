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

import json
import os
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402  (GLib main loop for the timer)

from runtime_dirs import private_runtime_dir

RUNTIME = Path(os.environ["XDG_RUNTIME_DIR"]) if os.environ.get("XDG_RUNTIME_DIR") else private_runtime_dir("hypr-runtime")
HIS = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
SOCK2 = str(RUNTIME / "hypr" / HIS / ".socket2.sock")
CMD_SOCK = str(RUNTIME / "hypr" / HIS / ".socket.sock")

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

    # ── Hyprland command socket helpers (no hyprctl process spawn) ───────────
    def _query(self, cmd: bytes) -> str:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.1)
            s.connect(CMD_SOCK)
            s.sendall(cmd)
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            s.close()
            return data.decode("utf-8", "replace")
        except Exception:
            return ""

    def cursor_y(self):
        out = self._query(b"cursorpos")
        try:
            return int(out.split(",")[1])
        except (IndexError, ValueError):
            return None

    def workspace_has_fullscreen(self):
        """Ground truth from Hyprland, not the (unreliable) event payload.

        Returns True/False, or None if the query failed (then keep current
        state). `fullscreen>>0` is NOT always emitted — e.g. closing the
        fullscreen window or some workspace switches leave the bar stuck in
        autohide. Reconciling against this on every relevant event fixes that.
        """
        out = self._query(b"j/activeworkspace")
        if not out:
            return None
        try:
            return bool(json.loads(out).get("hasfullscreen"))
        except (json.JSONDecodeError, AttributeError):
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

    def reconcile(self):
        """Drive enter/exit from Hyprland's real fullscreen state."""
        fs = self.workspace_has_fullscreen()
        if fs is None:
            return False          # query failed; leave state untouched
        if fs:
            self.enter_fullscreen()
        else:
            self.exit_fullscreen()
        return False              # one-shot for GLib.idle_add

    def on_event(self, event, payload):
        # Don't trust the `fullscreen` payload alone — it isn't emitted on
        # every transition (closing the fullscreen window, some workspace
        # switches). Reconcile against the real state on anything that can
        # change whether the focused workspace has a fullscreen window.
        if event in ("fullscreen", "workspace", "workspacev2", "focusedmon",
                     "closewindow", "openwindow", "movewindow", "movewindowv2",
                     "activewindow", "activewindowv2"):
            GLib.idle_add(self.reconcile)


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
    GLib.idle_add(ah.reconcile)   # sync to current state on launch
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
