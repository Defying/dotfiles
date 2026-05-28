#!/usr/bin/env python3
"""Layer-shell quick settings panel for Waybar."""

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

PID_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "quick-settings-panel.pid"
CODEX_INDICATOR = "/home/ben/dotfiles/scripts/waybar-openai-tokens.py"
CLAUDE_INDICATOR = "/home/ben/dotfiles/scripts/waybar-claude-usage.py"
CODEX_URL = "https://chatgpt.com/codex"
CODEX_USAGE_URL = "https://chatgpt.com/codex/settings/usage"
CODEX_PRICING_URL = "https://chatgpt.com/codex/pricing"
CLAUDE_URL = "https://claude.ai"
CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"


def run(*args, check=False, capture=False, timeout=2.5):
    try:
        if capture:
            return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()
        subprocess.run(args, check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        return ""
    except Exception:
        return ""


def spawn(*args):
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


def notify(title, body=""):
    run("notify-send", "-a", "quick-settings", "-t", "2200", title, body)


def wifi_enabled():
    return run("nmcli", "radio", "wifi", capture=True) == "enabled"


def set_wifi(enabled):
    run("nmcli", "radio", "wifi", "on" if enabled else "off")
    notify("Wi-Fi", "on" if enabled else "off")


def bluetooth_enabled():
    out = run("bluetoothctl", "show", capture=True).lower()
    return "powered: yes" in out


def set_bluetooth(enabled):
    run("bluetoothctl", "power", "on" if enabled else "off")
    notify("Bluetooth", "on" if enabled else "off")


def brightness_pct():
    out = run("brightnessctl", "-m", capture=True)
    try:
        return int(out.split(",")[3].replace("%", ""))
    except Exception:
        return 50


def set_brightness(value):
    run("brightnessctl", "-q", "set", f"{int(value)}%")


def volume_pct():
    out = run("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@", capture=True)
    try:
        return int(float(out.split()[1]) * 100 + 0.5)
    except Exception:
        return 50


def set_volume(value):
    run("wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{int(value)}%")


def muted():
    return "[MUTED]" in run("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@", capture=True)


def set_muted(enabled):
    run("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if enabled else "0")


def codex_status():
    out = run(CODEX_INDICATOR, capture=True)
    try:
        data = json.loads(out)
    except Exception:
        return "codex ?", "status unavailable"
    return data.get("text", "codex ?"), data.get("tooltip", "")


def claude_status():
    out = run(CLAUDE_INDICATOR, capture=True)
    try:
        data = json.loads(out)
    except Exception:
        return "claude ?", "status unavailable"
    return data.get("text", "claude ?"), data.get("tooltip", "")


def hypr_json(*args):
    try:
        return json.loads(run("hyprctl", *args, "-j", capture=True))
    except Exception:
        return None


class Panel(Gtk.Window):
    def __init__(self):
        super().__init__(title="quick-settings")
        self.set_name("quick-settings-panel")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_size_request(360, -1)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "quick-settings")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, 62)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, 28)

        self.connect("key-press-event", self.on_key)

        css = b"""
        #quick-settings-panel {
          background: transparent;
        }
        .panel {
          background:
            linear-gradient(145deg,
              rgba(255, 255, 255, 0.30),
              rgba(255, 255, 255, 0.09) 42%,
              rgba(51, 204, 255, 0.14) 68%,
              rgba(192, 132, 245, 0.18)),
            rgba(10, 14, 24, 0.34);
          border: 1px solid rgba(255, 255, 255, 0.42);
          border-radius: 24px;
          box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.55),
            inset 0 -1px 0 rgba(255, 255, 255, 0.18),
            0 30px 90px rgba(2, 6, 23, 0.48);
          padding: 18px;
        }
        .title {
          color: #f4f7fb;
          font-weight: 800;
          font-size: 15px;
          text-shadow: 0 1px 1px rgba(0, 0, 0, 0.55);
        }
        .section {
          color: rgba(244, 247, 251, 0.78);
          font-weight: 800;
          font-size: 12px;
          text-shadow: 0 1px 1px rgba(0, 0, 0, 0.45);
        }
        .status {
          color: #f8df9b;
          font-size: 12px;
          background: rgba(255, 255, 255, 0.07);
          border: 1px solid rgba(255, 255, 255, 0.12);
          border-radius: 10px;
          padding: 7px 9px;
        }
        label {
          color: #f4f7fb;
          font-size: 13px;
          text-shadow: 0 1px 1px rgba(0, 0, 0, 0.45);
        }
        .muted {
          color: rgba(244, 247, 251, 0.70);
        }
        button {
          color: #f4f7fb;
          background:
            linear-gradient(145deg, rgba(255, 255, 255, 0.16), rgba(255, 255, 255, 0.06)),
            rgba(255, 255, 255, 0.05);
          border: 1px solid rgba(255, 255, 255, 0.18);
          border-radius: 12px;
          padding: 8px 10px;
          box-shadow: inset 0 1px rgba(255, 255, 255, 0.16);
        }
        button:hover {
          background:
            linear-gradient(145deg, rgba(255, 255, 255, 0.24), rgba(255, 255, 255, 0.10)),
            rgba(255, 255, 255, 0.08);
        }
        switch {
          margin: 1px 0;
        }
        switch slider {
          background: #f4f7fb;
          box-shadow: 0 2px 8px rgba(0, 0, 0, 0.24);
        }
        switch trough {
          background: rgba(255, 255, 255, 0.14);
          border: 1px solid rgba(255, 255, 255, 0.16);
        }
        switch:checked trough {
          background: rgba(126, 231, 135, 0.42);
        }
        scale trough {
          min-height: 8px;
          border-radius: 9px;
          background: rgba(255, 255, 255, 0.16);
          border: 1px solid rgba(255, 255, 255, 0.08);
        }
        scale highlight {
          border-radius: 9px;
          background: #c084f5;
        }
        scale slider {
          min-width: 16px;
          min-height: 16px;
          background: #f4f7fb;
          border: 0;
          box-shadow: 0 2px 8px rgba(0, 0, 0, 0.24);
        }
        separator {
          background: rgba(255, 255, 255, 0.12);
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.get_style_context().add_class("panel")
        self.add(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.pack_start(header, False, False, 0)
        title = Gtk.Label(label="Quick Settings", xalign=0)
        title.get_style_context().add_class("title")
        header.pack_start(title, True, True, 0)
        close = Gtk.Button(label="x")
        close.connect("clicked", lambda *_: Gtk.main_quit())
        header.pack_end(close, False, False, 0)

        self.add_switch(root, "Wi-Fi", wifi_enabled, set_wifi)
        self.add_switch(root, "Bluetooth", bluetooth_enabled, set_bluetooth)
        self.add_switch(root, "Mute", muted, set_muted)
        self.add_scale(root, "Brightness", brightness_pct(), set_brightness)
        self.add_scale(root, "Volume", volume_pct(), set_volume)

        self.add_codex_usage(root)
        self.add_claude_usage(root)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        root.pack_start(grid, False, False, 0)
        buttons = [
            ("Audio", lambda: spawn("/home/ben/dotfiles/scripts/audio-menu.sh")),
            ("Network", lambda: spawn("nm-connection-editor")),
            ("Sound", lambda: spawn("pavucontrol")),
            ("Reload Bar", self.reload_waybar),
            ("Reload Hypr", self.reload_hyprland),
            ("Lock", lambda: run("loginctl", "lock-session")),
            ("Power", self.open_power_menu),
        ]
        for index, (label, callback) in enumerate(buttons):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, cb=callback: cb())
            grid.attach(button, index % 2, index // 2, 1, 1)
        self.update_codex_status(silent=True)
        self.update_claude_status(silent=True)

    def add_codex_usage(self, parent):
        parent.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
        parent.pack_start(Gtk.Label(label="Codex Usage", xalign=0), False, False, 0)
        parent.get_children()[-1].get_style_context().add_class("section")

        self.codex_status_label = Gtk.Label(label="codex ...", xalign=0)
        self.codex_status_label.set_line_wrap(True)
        self.codex_status_label.set_tooltip_text("Loading Codex subscription usage.")
        self.codex_status_label.get_style_context().add_class("status")
        parent.pack_start(self.codex_status_label, False, False, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        parent.pack_start(grid, False, False, 0)
        buttons = [
            ("Usage", lambda: self.open_url(CODEX_USAGE_URL, "Codex usage")),
            ("Codex", lambda: self.open_url(CODEX_URL, "Codex")),
            ("Pricing", lambda: self.open_url(CODEX_PRICING_URL, "Codex pricing")),
            ("Login", self.open_codex_login),
            ("Status", self.update_codex_status),
        ]
        for index, (label, callback) in enumerate(buttons):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, cb=callback: cb())
            grid.attach(button, index % 2, index // 2, 1, 1)

    def add_claude_usage(self, parent):
        parent.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
        parent.pack_start(Gtk.Label(label="Claude Usage", xalign=0), False, False, 0)
        parent.get_children()[-1].get_style_context().add_class("section")

        self.claude_status_label = Gtk.Label(label="claude ...", xalign=0)
        self.claude_status_label.set_line_wrap(True)
        self.claude_status_label.set_tooltip_text("Loading Claude subscription usage.")
        self.claude_status_label.get_style_context().add_class("status")
        parent.pack_start(self.claude_status_label, False, False, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        parent.pack_start(grid, False, False, 0)
        buttons = [
            ("Usage", lambda: self.open_url(CLAUDE_USAGE_URL, "Claude usage")),
            ("Claude", lambda: self.open_url(CLAUDE_URL, "Claude")),
            ("Login", self.open_claude_login),
            ("Status", self.update_claude_status),
        ]
        for index, (label, callback) in enumerate(buttons):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, cb=callback: cb())
            grid.attach(button, index % 2, index // 2, 1, 1)

    def add_switch(self, parent, label, getter, setter):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        parent.pack_start(row, False, False, 0)
        row.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
        switch = Gtk.Switch()
        switch.set_active(bool(getter()))
        switch.connect("notify::active", lambda sw, _param: setter(sw.get_active()))
        row.pack_end(switch, False, False, 0)

    def add_scale(self, parent, label, value, setter):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        parent.pack_start(box, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.pack_start(row, False, False, 0)
        row.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
        value_label = Gtk.Label(label=f"{int(value)}%")
        value_label.get_style_context().add_class("muted")
        row.pack_end(value_label, False, False, 0)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_draw_value(False)
        scale.set_value(value)
        scale.connect("value-changed", lambda s: value_label.set_text(f"{int(s.get_value())}%"))
        scale.connect("button-release-event", lambda s, _event: (setter(s.get_value()), False)[1])
        box.pack_start(scale, False, False, 0)

    def reload_waybar(self):
        run("pkill", "-x", "waybar")
        spawn("waybar")
        Gtk.main_quit()

    def reload_hyprland(self):
        run("hyprctl", "reload")
        notify("Hyprland", "reloaded")

    def open_power_menu(self):
        spawn("/home/ben/.local/bin/hypr-power-menu")
        Gtk.main_quit()

    def restart_waybar(self):
        run("pkill", "-x", "waybar")
        spawn("waybar")

    def update_status_async(self, label, getter, title, silent=False):
        label.set_text(f"{title.lower()} ...")
        label.set_tooltip_text(f"Loading {title} usage.")

        def worker():
            status, tooltip = getter()

            def apply():
                label.set_text(status)
                label.set_tooltip_text(tooltip)
                if not silent:
                    notify(f"{title} usage", status)
                return False

            GLib.idle_add(apply)

        threading.Thread(target=worker, daemon=True).start()

    def update_codex_status(self, silent=False):
        self.update_status_async(self.codex_status_label, codex_status, "Codex", silent)

    def update_claude_status(self, silent=False):
        self.update_status_async(self.claude_status_label, claude_status, "Claude", silent)

    def open_url(self, url, name):
        spawn("xdg-open", url)
        notify(name, url)

    def open_codex_login(self):
        terminals = [
            ("ghostty", "-e", "codex", "login"),
            ("foot", "codex", "login"),
            ("kitty", "codex", "login"),
            ("alacritty", "-e", "codex", "login"),
        ]
        for command in terminals:
            if shutil.which(command[0]):
                spawn(*command)
                notify("Codex login", "Browser login flow opened from Codex CLI.")
                return
        spawn("xdg-open", CODEX_URL)
        notify("Codex login", "No terminal found; opened Codex web.")

    def open_claude_login(self):
        terminals = [
            ("ghostty", "-e", "claude", "auth", "login", "--claudeai"),
            ("foot", "claude", "auth", "login", "--claudeai"),
            ("kitty", "claude", "auth", "login", "--claudeai"),
            ("alacritty", "-e", "claude", "auth", "login", "--claudeai"),
        ]
        for command in terminals:
            if shutil.which(command[0]):
                spawn(*command)
                notify("Claude login", "Browser login flow opened from Claude CLI.")
                return
        spawn("xdg-open", CLAUDE_URL)
        notify("Claude login", "No terminal found; opened Claude web.")

    def on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
            return True
        return False


def main():
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    window = Panel()
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
