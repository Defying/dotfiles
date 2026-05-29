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

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, Pango

from runtime_dirs import private_runtime_dir

PID_FILE = private_runtime_dir("quick-settings-panel") / "quick-settings-panel.pid"
CODEX_INDICATOR = "/home/ben/dotfiles/scripts/waybar-openai-tokens.py"
CODEX_ACCOUNT = "/home/ben/dotfiles/scripts/ai_accounts.py"
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


def keyboard_brightness_pct():
    out = run("brightnessctl", "-m", "-d", "kbd_backlight", capture=True)
    try:
        return int(out.split(",")[3].replace("%", ""))
    except Exception:
        return 0


def set_keyboard_brightness(value):
    run("brightnessctl", "-q", "-d", "kbd_backlight", "set", f"{int(value)}%")


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
        self.set_size_request(390, -1)

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
          font-family: "SF Pro Text", "Symbols Nerd Font", "Font Awesome 6 Free", sans-serif;
          background:
            linear-gradient(180deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.03)),
            rgba(12, 16, 24, 0.88);
          border: 1px solid rgba(255, 255, 255, 0.18);
          border-radius: 16px;
          box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.16),
            0 18px 56px rgba(2, 6, 23, 0.42);
          padding: 12px;
        }
        .title {
          color: #f4f7fb;
          font-weight: 800;
          font-size: 14px;
        }
        .section {
          color: rgba(244, 247, 251, 0.64);
          font-weight: 800;
          font-size: 11px;
          margin-top: 2px;
        }
        .status {
          color: rgba(244, 247, 251, 0.88);
          font-size: 12px;
          background: rgba(255, 255, 255, 0.055);
          border: 1px solid rgba(255, 255, 255, 0.10);
          border-radius: 8px;
          padding: 4px 7px;
        }
        label {
          color: #f4f7fb;
          font-size: 12px;
        }
        .muted {
          color: rgba(244, 247, 251, 0.60);
          font-size: 11px;
        }
        .control {
          background: rgba(255, 255, 255, 0.045);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 9px;
          padding: 6px 8px;
        }
        .status-row {
          padding: 6px 8px;
        }
        button {
          color: #f4f7fb;
          background: rgba(255, 255, 255, 0.07);
          border: 1px solid rgba(255, 255, 255, 0.11);
          border-radius: 8px;
          padding: 6px 8px;
          font-size: 12px;
        }
        button:hover {
          background: rgba(255, 255, 255, 0.12);
        }
        button.close {
          min-width: 24px;
          min-height: 24px;
          padding: 0;
          border-radius: 12px;
        }
        button.menu {
          min-width: 38px;
          font-size: 13px;
        }
        button.tile {
          min-height: 66px;
          padding: 8px 6px;
          border-radius: 12px;
          background: rgba(255, 255, 255, 0.075);
        }
        button.tile:checked {
          color: #071018;
          background: #7dd3fc;
          border-color: rgba(255, 255, 255, 0.32);
        }
        .tile-icon {
          font-size: 22px;
          font-weight: 800;
        }
        .tile-label {
          font-size: 11px;
          font-weight: 700;
        }
        .slider-icon {
          min-width: 22px;
          font-size: 16px;
        }
        switch {
          margin: 1px 0;
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
        scale trough {
          min-height: 6px;
          border-radius: 9px;
          background: rgba(255, 255, 255, 0.11);
          border: 1px solid rgba(255, 255, 255, 0.08);
        }
        scale highlight {
          border-radius: 9px;
          background: #7dd3fc;
        }
        scale slider {
          min-width: 14px;
          min-height: 14px;
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

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        root.get_style_context().add_class("panel")
        self.add(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.pack_start(header, False, False, 0)
        title = Gtk.Label(label="Quick Settings", xalign=0)
        title.get_style_context().add_class("title")
        header.pack_start(title, True, True, 0)
        close = Gtk.Button(label="x")
        close.get_style_context().add_class("close")
        close.connect("clicked", lambda *_: Gtk.main_quit())
        header.pack_end(close, False, False, 0)

        self.add_toggle_tiles(root)
        self.add_scale(root, "󰃠", "Display", brightness_pct(), set_brightness)
        self.add_scale(root, "", "Keyboard", keyboard_brightness_pct(), set_keyboard_brightness)
        self.add_scale(root, "", "Volume", volume_pct(), set_volume)

        self.add_codex_usage(root)
        self.add_claude_usage(root)
        self.add_action_menus(root)
        self.update_codex_status(silent=True)
        self.update_claude_status(silent=True)

    def add_codex_usage(self, parent):
        parent.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
        self.codex_status_label = self.add_status_row(parent, "Codex", "codex ...", [
            ("Usage", lambda: self.open_url(CODEX_USAGE_URL, "Codex usage")),
            ("Codex", lambda: self.open_url(CODEX_URL, "Codex")),
            ("Pricing", lambda: self.open_url(CODEX_PRICING_URL, "Codex pricing")),
            ("Account", self.open_codex_account),
            ("Login", self.open_codex_login),
            ("Refresh", self.update_codex_status),
        ])
        self.codex_status_label.set_tooltip_text("Loading Codex subscription usage.")

    def add_claude_usage(self, parent):
        self.claude_status_label = self.add_status_row(parent, "Claude", "claude ...", [
            ("Usage", lambda: self.open_url(CLAUDE_USAGE_URL, "Claude usage")),
            ("Claude", lambda: self.open_url(CLAUDE_URL, "Claude")),
            ("Login", self.open_claude_login),
            ("Refresh", self.update_claude_status),
        ])
        self.claude_status_label.set_tooltip_text("Loading Claude subscription usage.")

    def add_status_row(self, parent, title, initial, actions):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class("control")
        row.get_style_context().add_class("status-row")
        parent.pack_start(row, False, False, 0)

        name = Gtk.Label(label=title, xalign=0)
        name.get_style_context().add_class("section")
        row.pack_start(name, False, False, 0)

        status = Gtk.Label(label=initial, xalign=1)
        status.set_line_wrap(False)
        status.set_ellipsize(Pango.EllipsizeMode.END)
        status.get_style_context().add_class("status")
        row.pack_start(status, True, True, 0)

        self.add_menu_button(row, "⋯", actions)
        return status

    def add_action_menus(self, parent):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        parent.pack_start(row, False, False, 0)
        self.add_menu_button(row, "󰓃", [
            ("Audio devices", lambda: spawn("/home/ben/dotfiles/scripts/audio-menu.sh")),
            ("Network settings", lambda: spawn("nm-connection-editor")),
            ("Sound settings", lambda: spawn("pavucontrol")),
        ])
        self.add_menu_button(row, "", [
            ("Reload Waybar", self.reload_waybar),
            ("Reload Hyprland", self.reload_hyprland),
            ("Lock", lambda: run("loginctl", "lock-session")),
            ("Power", self.open_power_menu),
        ])

    def add_menu_button(self, parent, label, actions):
        menu = Gtk.MenuButton(label=label)
        menu.get_style_context().add_class("menu")
        popover = Gtk.Popover(relative_to=menu)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        for action_label, callback in actions:
            button = Gtk.Button(label=action_label)
            button.connect(
                "clicked",
                lambda _button, cb=callback, pop=popover: (cb(), pop.popdown()),
            )
            box.pack_start(button, False, False, 0)
        popover.add(box)
        box.show_all()
        menu.set_popover(popover)
        parent.pack_end(menu, False, False, 0)
        return menu

    def add_toggle_tiles(self, parent):
        grid = Gtk.Grid(column_spacing=7, row_spacing=7, column_homogeneous=True)
        parent.pack_start(grid, False, False, 0)
        tiles = [
            ("", "Wi-Fi", wifi_enabled, set_wifi),
            ("", "Bluetooth", bluetooth_enabled, set_bluetooth),
            ("󰝟", "Mute", muted, set_muted),
        ]
        for index, (icon, label, getter, setter) in enumerate(tiles):
            button = Gtk.ToggleButton()
            button.get_style_context().add_class("tile")
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            content.set_halign(Gtk.Align.CENTER)
            content.set_valign(Gtk.Align.CENTER)
            icon_label = Gtk.Label(label=icon)
            icon_label.get_style_context().add_class("tile-icon")
            text_label = Gtk.Label(label=label)
            text_label.get_style_context().add_class("tile-label")
            content.pack_start(icon_label, False, False, 0)
            content.pack_start(text_label, False, False, 0)
            button.add(content)
            button.set_active(bool(getter()))
            button.connect("toggled", lambda b, cb=setter: cb(b.get_active()))
            grid.attach(button, index, 0, 1, 1)

    def add_scale(self, parent, icon, label, value, setter):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.get_style_context().add_class("control")
        parent.pack_start(box, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.pack_start(row, False, False, 0)
        icon_label = Gtk.Label(label=icon)
        icon_label.get_style_context().add_class("slider-icon")
        row.pack_start(icon_label, False, False, 0)
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
        spawn(CODEX_ACCOUNT, "codex-login-new")
        notify("Codex login", "New logins are saved as switchable accounts.")

    def open_codex_account(self):
        spawn(CODEX_ACCOUNT, "codex-menu")
        notify("Codex account", "Choose or save a Codex account.")

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
