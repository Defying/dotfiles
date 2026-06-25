#!/usr/bin/env python3
"""Layer-shell audio and Bluetooth device panel for Waybar."""

import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, Pango

from runtime_dirs import private_runtime_dir

PID_FILE = private_runtime_dir("audio-menu-panel") / "audio-menu-panel.pid"
PANEL_WIDTH = 360

CSS = b"""
#audio-menu-panel {
  background: transparent;
}
.panel {
  font-family: "SF Pro Text", "Symbols Nerd Font", "Font Awesome 6 Free", sans-serif;
  background:
    linear-gradient(155deg, rgba(255, 255, 255, 0.10), rgba(255, 255, 255, 0.035) 46%),
    rgba(8, 11, 17, 0.96);
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: 14px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    inset 0 -1px 0 rgba(0, 0, 0, 0.20),
    0 18px 46px rgba(0, 0, 0, 0.50);
  padding: 12px;
}
label {
  color: #ffffff;
  font-size: 13px;
}
.title {
  font-size: 13px;
  font-weight: 800;
}
.muted {
  color: rgba(255, 255, 255, 0.70);
  font-size: 12px;
}
.device,
.control {
  background: rgba(255, 255, 255, 0.075);
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 12px;
  padding: 8px;
}
.device.connected {
  background: rgba(158, 234, 240, 0.14);
  border-color: rgba(158, 234, 240, 0.52);
}
.icon {
  min-width: 24px;
  font-size: 18px;
  font-weight: 800;
}
button {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.22);
  border-radius: 9px;
  padding: 7px 9px;
  font-size: 12px;
  font-weight: 700;
}
button:hover {
  background: rgba(255, 255, 255, 0.18);
  border-color: rgba(255, 255, 255, 0.38);
}
button.close {
  min-width: 34px;
  min-height: 34px;
  padding: 0;
  border-radius: 10px;
  font-family: "Font Awesome 6 Free", "Symbols Nerd Font", sans-serif;
}
switch slider {
  background: #f4f7fb;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.24);
}
switch trough {
  background: rgba(255, 255, 255, 0.10);
  border: 1px solid rgba(255, 255, 255, 0.10);
}
switch:checked trough {
  background: rgba(72, 187, 120, 0.52);
}
"""


def run(*args, capture=False, timeout=3.5):
    try:
        if capture:
            return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except Exception:
        return "" if capture else None
    return ""


def spawn(*args):
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


def notify(title, body=""):
    run(
        "notify-send",
        "-a",
        "audio-menu",
        "-i",
        "audio-headphones",
        "-t",
        "2200",
        "-h",
        "string:x-canonical-private-synchronous:audio-menu",
        title,
        body,
    )


def bluetooth_powered():
    return "powered: yes" in run("bluetoothctl", "show", capture=True).lower()


def set_bluetooth_power(enabled):
    run("bluetoothctl", "power", "on" if enabled else "off", timeout=5)
    notify("bluetooth", "on" if enabled else "off")


def parse_info(text):
    data = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip().lower()] = value.strip()
    return data


def paired_devices():
    devices = []
    for line in run("bluetoothctl", "devices", "Paired", capture=True).splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            continue
        mac = parts[1]
        fallback_name = parts[2]
        info = parse_info(run("bluetoothctl", "info", mac, capture=True))
        name = info.get("alias") or info.get("name") or fallback_name
        icon = info.get("icon") or ""
        connected = info.get("connected", "").lower() == "yes"
        devices.append({"mac": mac, "name": name, "icon": icon, "connected": connected})
    devices.sort(key=lambda d: (not d["connected"], d["name"].lower()))
    return devices


def output_muted():
    return "[MUTED]" in run("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@", capture=True)


def output_volume_text():
    out = run("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@", capture=True)
    try:
        pct = int(float(out.split()[1]) * 100 + 0.5)
        return f"{pct}%"
    except Exception:
        return "unknown"


def set_output_muted(enabled):
    run("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if enabled else "0")


def device_glyph(device):
    icon = device.get("icon", "")
    name = device.get("name", "").lower()
    if "keyboard" in icon or "keyboard" in name or "keychron" in name:
        return ""
    if "airpods" in name or "earbud" in icon or "earbud" in name:
        return "󱡏"
    if "head" in icon:
        return "󰋋"
    return ""


class AudioPanel(Gtk.Window):
    def __init__(self):
        super().__init__(title="audio")
        self.set_name("audio-menu-panel")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_app_paintable(True)
        self.set_size_request(PANEL_WIDTH, -1)
        self._syncing_bluetooth = False
        self._syncing_mute = False

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "audio-menu")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, 62)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, 84)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.root.get_style_context().add_class("panel")
        self.root.set_sensitive(False)
        self.add(self.root)
        self.connect("key-press-event", self.on_key)
        self.populate()
        GLib.timeout_add(350, self.arm_controls)

    def arm_controls(self):
        self.root.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def populate(self):
        for child in self.root.get_children():
            self.root.remove(child)
        self.add_header()
        self.add_devices()
        self.add_audio_controls()
        self.root.show_all()

    def add_header(self):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.root.pack_start(row, False, False, 0)
        title = Gtk.Label(label="audio", xalign=0)
        title.get_style_context().add_class("title")
        row.pack_start(title, True, True, 0)

        row.pack_start(Gtk.Label(label=""), False, False, 0)
        self.bt_switch = Gtk.Switch()
        self.bt_switch.set_active(bluetooth_powered())
        self.bt_switch.connect("notify::active", self.on_bluetooth_toggle)
        row.pack_start(self.bt_switch, False, False, 0)

        close = Gtk.Button(label="")
        close.get_style_context().add_class("close")
        close.set_tooltip_text("close")
        close.connect("clicked", lambda *_: Gtk.main_quit())
        row.pack_end(close, False, False, 0)

    def add_devices(self):
        devices = paired_devices()
        if not devices:
            empty = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            empty.get_style_context().add_class("device")
            empty.pack_start(Gtk.Label(label=""), False, False, 0)
            label = Gtk.Label(label="no paired bluetooth devices", xalign=0)
            label.get_style_context().add_class("muted")
            empty.pack_start(label, True, True, 0)
            self.root.pack_start(empty, False, False, 0)
            return

        for device in devices:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
            ctx = row.get_style_context()
            ctx.add_class("device")
            if device["connected"]:
                ctx.add_class("connected")
            self.root.pack_start(row, False, False, 0)

            icon = Gtk.Label(label=device_glyph(device))
            icon.get_style_context().add_class("icon")
            row.pack_start(icon, False, False, 0)

            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            name = Gtk.Label(label=device["name"], xalign=0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            state = Gtk.Label(label="connected" if device["connected"] else "available", xalign=0)
            state.get_style_context().add_class("muted")
            text.pack_start(name, False, False, 0)
            text.pack_start(state, False, False, 0)
            row.pack_start(text, True, True, 0)

            label = "disconnect" if device["connected"] else "connect"
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, d=device: self.toggle_device(d))
            row.pack_end(button, False, False, 0)

    def add_audio_controls(self):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        row.get_style_context().add_class("control")
        self.root.pack_start(row, False, False, 0)

        icon = Gtk.Label(label="")
        icon.get_style_context().add_class("icon")
        row.pack_start(icon, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(label="output", xalign=0)
        subtitle = Gtk.Label(label=f"default sink · {output_volume_text()}", xalign=0)
        subtitle.get_style_context().add_class("muted")
        text.pack_start(title, False, False, 0)
        text.pack_start(subtitle, False, False, 0)
        row.pack_start(text, True, True, 0)

        self.mute_switch = Gtk.Switch()
        self.mute_switch.set_active(output_muted())
        self.mute_switch.set_tooltip_text("mute")
        self.mute_switch.connect("notify::active", self.on_mute_toggle)
        row.pack_end(self.mute_switch, False, False, 0)

        settings = Gtk.Button(label="settings")
        settings.connect("clicked", lambda *_: spawn("pavucontrol"))
        row.pack_end(settings, False, False, 0)

        pair = Gtk.Button(label="pair")
        pair.connect("clicked", lambda *_: spawn("bluedevil-wizard"))
        row.pack_end(pair, False, False, 0)

    def toggle_device(self, device):
        verb = "disconnect" if device["connected"] else "connect"
        notify("bluetooth", f"{verb} {device['name']}")
        out = run("bluetoothctl", verb, device["mac"], capture=True, timeout=12)
        if out:
            notify("bluetooth", out.splitlines()[-1])
        self.populate()

    def on_bluetooth_toggle(self, switch, *_):
        if self._syncing_bluetooth:
            return
        set_bluetooth_power(switch.get_active())
        self.populate()

    def on_mute_toggle(self, switch, *_):
        if self._syncing_mute:
            return
        set_output_muted(switch.get_active())
        self.populate()

    def on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
            return True
        return False


def main():
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    window = AudioPanel()
    window.show_all()
    try:
        Gtk.main()
    finally:
        try:
            if PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                PID_FILE.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
