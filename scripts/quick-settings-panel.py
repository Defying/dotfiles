#!/usr/bin/env python3
"""Layer-shell quick settings panel for Waybar."""

import os
import json
import signal
import shutil
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, Gtk, GtkLayerShell

from runtime_dirs import private_runtime_dir

PID_FILE = private_runtime_dir("quick-settings-panel") / "quick-settings-panel.pid"
AWAKE_PID_FILE = private_runtime_dir("quick-settings-awake") / "awake.pid"
AWAKE_STATE_FILE = private_runtime_dir("quick-settings-awake") / "awake.json"
POWER_STATE_FILE = private_runtime_dir("quick-settings-power") / "power.json"
LOW_POWER_PROFILE = "powersave"
DEFAULT_POWER_PROFILE = "balanced"
BATTERY_DEVICE = "/org/freedesktop/UPower/devices/battery_macsmc_battery"
BATTERY_SYSFS = Path("/sys/class/power_supply/macsmc-battery")
BATTERY_TMPFILES = Path("/etc/tmpfiles.d/battery-charge-limit.conf")
BATTERY_UDEV_CONF = Path("/etc/udev/macsmc-battery.conf")
AUTOBRIGHT_CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
AUTOBRIGHT_OFF_FILE = AUTOBRIGHT_CACHE_HOME / "hypr" / "auto-brightness.off"
AUTOBRIGHT_CMD = "/home/ben/.local/bin/waybar-helper"


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


def dnd_enabled():
    return "do-not-disturb" in run("makoctl", "mode", capture=True)


def set_dnd(enabled):
    run("makoctl", "mode", "-a" if enabled else "-r", "do-not-disturb")
    run("pkill", "-RTMIN+10", "-x", "waybar")
    notify("Do Not Disturb", "on" if enabled else "off")


def autobrightness_enabled():
    return not AUTOBRIGHT_OFF_FILE.exists()


def autobrightness_daemon_running():
    return bool(run("pgrep", "-fx", f"{AUTOBRIGHT_CMD} autobright", capture=True))


def set_autobrightness(enabled):
    AUTOBRIGHT_OFF_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if enabled:
        try:
            AUTOBRIGHT_OFF_FILE.unlink()
        except OSError:
            pass
        if not autobrightness_daemon_running() and shutil.which(AUTOBRIGHT_CMD):
            spawn(AUTOBRIGHT_CMD, "autobright")
    else:
        AUTOBRIGHT_OFF_FILE.touch()
    notify("Adaptive brightness", "on" if enabled else "off")


def read_awake_pid():
    state = read_json(AWAKE_STATE_FILE)
    if state.get("pid"):
        try:
            return int(state["pid"])
        except Exception:
            pass
    try:
        return int(AWAKE_PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def process_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def stop_awake():
    pid = read_awake_pid()
    if pid:
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    for path in (AWAKE_STATE_FILE, AWAKE_PID_FILE):
        try:
            path.unlink()
        except OSError:
            pass


def awake_mode():
    state = read_json(AWAKE_STATE_FILE)
    pid = state.get("pid")
    mode = state.get("mode") or ""
    if pid and mode and process_alive(pid):
        return mode
    legacy_pid = read_awake_pid()
    if process_alive(legacy_pid):
        return "display"
    stop_awake()
    return ""


def awake_enabled():
    return bool(awake_mode())


def start_awake(mode):
    if not shutil.which("systemd-inhibit"):
        notify("Awake", "systemd-inhibit not found")
        return
    stop_awake()
    if mode == "system":
        what = "sleep"
        label = "system awake"
    else:
        what = "idle:sleep"
        label = "display awake"
    try:
        AWAKE_STATE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [
                "systemd-inhibit",
                f"--what={what}",
                "--who=quick-settings",
                f"--why=Awake mode: {label}",
                "--mode=block",
                "sleep",
                "infinity",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        AWAKE_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
        write_json(AWAKE_STATE_FILE, {"pid": proc.pid, "mode": mode, "what": what})
        notify("Awake", label)
    except Exception as exc:
        notify("Awake", str(exc))


def set_awake_mode(mode):
    if mode:
        start_awake(mode)
    else:
        stop_awake()
        notify("Awake", "off")


def set_awake(enabled):
    set_awake_mode("display" if enabled else "")


def display_sleep():
    run("hyprctl", "dispatch", "dpms", "off")
    notify("Display", "sleep")


def awake_blockers():
    output = run("systemd-inhibit", "--list", "--no-pager", "--no-legend", capture=True, timeout=4)
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        notify("Awake blockers", "none")
        return
    summary = []
    for line in lines[:5]:
        parts = line.split()
        who = parts[0] if parts else "unknown"
        what = parts[5] if len(parts) > 5 else ""
        why = " ".join(parts[6:-1]) if len(parts) > 7 else ""
        summary.append(f"{who}: {what} {why}".strip())
    notify("Awake blockers", "\n".join(summary))


def tuned_profiles():
    out = run("tuned-adm", "list", capture=True, timeout=4)
    profiles = set()
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        profiles.add(line[2:].split(None, 1)[0])
    return profiles


def current_power_profile():
    out = run("tuned-adm", "active", capture=True, timeout=4)
    for line in out.splitlines():
        marker = "Current active profile:"
        if marker in line:
            return line.split(marker, 1)[1].strip()
    return ""


def low_power_supported():
    return bool(shutil.which("tuned-adm")) and LOW_POWER_PROFILE in tuned_profiles()


def low_power_enabled():
    return current_power_profile() == LOW_POWER_PROFILE


def read_power_restore_profile():
    restore = read_json(POWER_STATE_FILE).get("restore_profile") or DEFAULT_POWER_PROFILE
    profiles = tuned_profiles()
    if restore == LOW_POWER_PROFILE or restore not in profiles:
        return DEFAULT_POWER_PROFILE
    return restore


def set_power_profile(profile):
    try:
        return subprocess.run(
            ["sudo", "-n", "tuned-adm", "profile", profile],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=6,
            check=False,
        ).returncode == 0
    except Exception:
        return False


def set_low_power(enabled):
    if not low_power_supported():
        notify("Low Power", "not supported")
        return
    current = current_power_profile()
    if enabled:
        if current != LOW_POWER_PROFILE:
            write_json(POWER_STATE_FILE, {"restore_profile": current or DEFAULT_POWER_PROFILE})
        if set_power_profile(LOW_POWER_PROFILE):
            notify("Low Power", "on")
        else:
            notify("Low Power", "sudo unavailable")
    else:
        restore = read_power_restore_profile()
        if set_power_profile(restore):
            try:
                POWER_STATE_FILE.unlink()
            except OSError:
                pass
            notify("Low Power", f"off · {restore}")
        else:
            notify("Low Power", "sudo unavailable")


def battery_prop(name):
    out = run(
        "busctl",
        "get-property",
        "org.freedesktop.UPower",
        BATTERY_DEVICE,
        "org.freedesktop.UPower.Device",
        name,
        capture=True,
    )
    parts = out.split(maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


def int_or(value, fallback):
    try:
        return int(str(value).strip())
    except Exception:
        return fallback


def battery_sysfs_prop(name):
    try:
        return (BATTERY_SYSFS / name).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def charge_limit_settings_supported():
    return int_or(battery_prop("ChargeThresholdSettingsSupported"), 0)


def charge_limit_supported():
    return battery_prop("ChargeThresholdSupported") == "true" or (BATTERY_SYSFS / "charge_control_end_threshold").exists()


def charge_limit_enabled():
    sysfs_end = int_or(battery_sysfs_prop("charge_control_end_threshold"), 100)
    return battery_prop("ChargeThresholdEnabled") == "true" or sysfs_end < 100


def charge_limit_thresholds():
    sysfs_start = int_or(battery_sysfs_prop("charge_control_start_threshold"), 100)
    sysfs_end = int_or(battery_sysfs_prop("charge_control_end_threshold"), 100)
    if sysfs_end < 100:
        return sysfs_start, sysfs_end
    start = int_or(battery_prop("ChargeStartThreshold"), 75)
    end = int_or(battery_prop("ChargeEndThreshold"), 80)
    return start, end


def write_root_file(path, content):
    try:
        subprocess.run(
            ["sudo", "-n", "tee", str(path)],
            input=content,
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
        )
        return True
    except Exception:
        return False


def write_root_sysfs(path, value):
    try:
        subprocess.run(
            ["sudo", "-n", "sh", "-c", 'printf "%s\n" "$1" > "$2"', "sh", str(value), str(path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
        )
        return True
    except Exception:
        return False


def set_charge_thresholds(end):
    end = max(60, min(100, int(round(float(end) / 5) * 5)))
    if end >= 100:
        set_charge_limit(False)
        return
    start = max(40, end - 5)
    start_path = BATTERY_SYSFS / "charge_control_start_threshold"
    end_path = BATTERY_SYSFS / "charge_control_end_threshold"
    current_start = int_or(battery_sysfs_prop("charge_control_start_threshold"), 100)
    first, second = ((end_path, end), (start_path, start)) if end >= current_start else ((start_path, start), (end_path, end))
    for path, value in (first, second):
        if not write_root_sysfs(path, value):
            notify("Charge limit", "cannot write threshold")
            return
    tmpfiles = "\n".join(
        [
            "# Managed by quick settings.",
            f"w {start_path} - - - - {start}",
            f"w {end_path} - - - - {end}",
            "",
        ]
    )
    write_root_file(BATTERY_TMPFILES, tmpfiles)
    write_root_file(BATTERY_UDEV_CONF, f"CHARGE_CONTROL_END_THRESHOLD={end}\n")
    run(
        "busctl",
        "call",
        "org.freedesktop.UPower",
        BATTERY_DEVICE,
        "org.freedesktop.UPower.Device",
        "EnableChargeThreshold",
        "b",
        "true",
        timeout=4,
    )
    notify("Charge limit", f"on · {start}-{end}%")


def set_charge_limit(enabled):
    if not charge_limit_supported():
        notify("Charge limit", "not supported")
        return
    run(
        "busctl",
        "call",
        "org.freedesktop.UPower",
        BATTERY_DEVICE,
        "org.freedesktop.UPower.Device",
        "EnableChargeThreshold",
        "b",
        "true" if enabled else "false",
        timeout=4,
    )
    if not enabled:
        write_root_sysfs(BATTERY_SYSFS / "charge_control_start_threshold", 100)
        write_root_sysfs(BATTERY_SYSFS / "charge_control_end_threshold", 100)
    start, end = charge_limit_thresholds()
    notify("Charge limit", f"{'on' if enabled else 'off'} · {start}-{end}%")


class Panel(Gtk.Window):
    def __init__(self):
        super().__init__(title="quick-settings")
        self.set_name("quick-settings-panel")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_app_paintable(True)
        self.set_size_request(386, -1)
        self._awake_buttons = {}
        self._awake_state_label = None
        self._low_power_switch = None
        self._low_power_state_label = None
        self._autobrightness_switch = None
        self._autobrightness_state_label = None
        self._syncing_awake = False
        self._syncing_low_power = False
        self._syncing_autobrightness = False

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

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
          .muted {
            color: rgba(255, 255, 255, 0.74);
            font-size: 12px;
          }
          .control {
            background: rgba(255, 255, 255, 0.10);
            border: 1px solid rgba(255, 255, 255, 0.20);
            border-radius: 10px;
            padding: 9px 10px;
          }
          .tile-row,
          .awake-card {
            background: rgba(255, 255, 255, 0.075);
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 12px;
            padding: 8px;
          }
          .action-row {
            padding: 0;
          }
          button {
            color: #ffffff;
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(255, 255, 255, 0.22);
            border-radius: 9px;
            padding: 7px 9px;
            font-size: 13px;
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
            font-size: 13px;
          }
          button.menu {
            min-width: 48px;
            min-height: 38px;
            font-size: 16px;
          }
          button.tile {
            min-width: 38px;
            min-height: 38px;
            padding: 0;
            border-radius: 10px;
            background: rgba(255, 255, 255, 0.13);
          }
          button.tile:checked {
            color: #061014;
            background: #9eeaf0;
            border-color: rgba(255, 255, 255, 0.64);
            box-shadow:
              inset 0 1px 0 rgba(255, 255, 255, 0.50),
              0 0 0 1px rgba(158, 234, 240, 0.28);
          }
          .tile-icon {
            font-size: 17px;
            font-weight: 800;
          }
          button.tile:checked .tile-icon {
            color: #061014;
          }
          .awake-title {
            font-size: 13px;
            font-weight: 800;
          }
          button.segment {
            min-width: 62px;
            min-height: 32px;
            padding: 0 8px;
            font-size: 12px;
            font-weight: 800;
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.10);
          }
          button.segment:checked {
            color: #061014;
            background: #9eeaf0;
            border-color: rgba(255, 255, 255, 0.62);
          }
          button.segment:checked label {
            color: #061014;
          }
          .slider-icon {
            min-width: 24px;
            font-size: 18px;
          }
          .value-pill {
            color: #ffffff;
            background: rgba(255, 255, 255, 0.16);
          border: 1px solid rgba(255, 255, 255, 0.22);
          border-radius: 8px;
          min-width: 46px;
          padding: 3px 6px;
          font-size: 12px;
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
          min-height: 12px;
          border-radius: 10px;
          background: rgba(255, 255, 255, 0.18);
          border: 1px solid rgba(255, 255, 255, 0.18);
        }
        scale highlight {
          min-height: 12px;
          border-radius: 10px;
          background: #9eeaf0;
        }
        scale slider {
          min-width: 0;
          min-height: 0;
          background: transparent;
          border: 0;
          box-shadow: none;
          margin: 0;
          padding: 0;
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

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.get_style_context().add_class("panel")
        self.add(root)

        self.add_toggle_tiles(root)
        self.add_low_power_control(root)
        self.add_awake_control(root)
        self.add_autobrightness_control(root)
        self.add_scale(root, "󰃠", "Display", brightness_pct(), set_brightness)
        self.add_scale(root, "", "Keyboard", keyboard_brightness_pct(), set_keyboard_brightness)
        self.add_scale(root, "", "Volume", volume_pct(), set_volume)
        self.add_action_menus(root)

    def add_action_menus(self, parent):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class("action-row")
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
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        row.get_style_context().add_class("tile-row")
        parent.pack_start(row, False, False, 0)
        tiles = [
            ("", "Wi-Fi", wifi_enabled, set_wifi),
            ("", "Bluetooth", bluetooth_enabled, set_bluetooth),
            ("", "Do Not Disturb", dnd_enabled, set_dnd),
            ("󰝟", "Mute", muted, set_muted),
            ("󰍺", "Sleep display now", None, display_sleep),
            ("󱊢", "Battery charge limit", charge_limit_enabled, set_charge_limit),
            ("󰒲", "Show awake blockers", None, awake_blockers),
        ]
        for icon, label, getter, callback in tiles:
            if getter is None:
                row.pack_start(self.add_action_tile(icon, label, callback), False, False, 0)
            else:
                row.pack_start(self.add_toggle_tile(icon, label, getter, callback), False, False, 0)
        close = Gtk.Button(label="")
        close.get_style_context().add_class("close")
        close.set_tooltip_text("Close")
        close.connect("clicked", lambda *_: Gtk.main_quit())
        row.pack_end(close, False, False, 0)

    def add_low_power_control(self, parent):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        row.get_style_context().add_class("awake-card")
        parent.pack_start(row, False, False, 0)

        icon_label = Gtk.Label(label="󰌪")
        icon_label.get_style_context().add_class("tile-icon")
        row.pack_start(icon_label, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(label="Low Power")
        title.get_style_context().add_class("awake-title")
        title.set_xalign(0)
        self._low_power_state_label = Gtk.Label()
        self._low_power_state_label.get_style_context().add_class("muted")
        self._low_power_state_label.set_xalign(0)
        text.pack_start(title, False, False, 0)
        text.pack_start(self._low_power_state_label, False, False, 0)
        row.pack_start(text, True, True, 0)

        self._low_power_switch = Gtk.Switch()
        self._low_power_switch.set_tooltip_text("Low Power Mode")
        self._low_power_switch.connect("notify::active", self.on_low_power_switch)
        row.pack_end(self._low_power_switch, False, False, 0)
        self.sync_low_power_control()

    def add_autobrightness_control(self, parent):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        row.get_style_context().add_class("awake-card")
        parent.pack_start(row, False, False, 0)

        icon_label = Gtk.Label(label="󰖨")
        icon_label.get_style_context().add_class("tile-icon")
        row.pack_start(icon_label, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(label="Auto Brightness")
        title.get_style_context().add_class("awake-title")
        title.set_xalign(0)
        self._autobrightness_state_label = Gtk.Label()
        self._autobrightness_state_label.get_style_context().add_class("muted")
        self._autobrightness_state_label.set_xalign(0)
        text.pack_start(title, False, False, 0)
        text.pack_start(self._autobrightness_state_label, False, False, 0)
        row.pack_start(text, True, True, 0)

        self._autobrightness_switch = Gtk.Switch()
        self._autobrightness_switch.set_tooltip_text("Auto Brightness")
        self._autobrightness_switch.connect("notify::active", self.on_autobrightness_switch)
        row.pack_end(self._autobrightness_switch, False, False, 0)
        self.sync_autobrightness_control()

    def add_awake_control(self, parent):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        row.get_style_context().add_class("awake-card")
        parent.pack_start(row, False, False, 0)

        icon_label = Gtk.Label(label="")
        icon_label.get_style_context().add_class("tile-icon")
        row.pack_start(icon_label, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(label="Awake")
        title.get_style_context().add_class("awake-title")
        title.set_xalign(0)
        self._awake_state_label = Gtk.Label()
        self._awake_state_label.get_style_context().add_class("muted")
        self._awake_state_label.set_xalign(0)
        text.pack_start(title, False, False, 0)
        text.pack_start(self._awake_state_label, False, False, 0)
        row.pack_start(text, True, True, 0)

        segments = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        row.pack_end(segments, False, False, 0)

        off = self.add_segment_button("Off", "")
        system = self.add_segment_button("System", "system")
        display = self.add_segment_button("Display", "display")
        for button in (off, system, display):
            segments.pack_start(button, False, False, 0)
        self._awake_buttons = {"": off, "system": system, "display": display}
        self.sync_awake_buttons()

    def add_segment_button(self, label, mode):
        button = Gtk.ToggleButton(label=label)
        button.get_style_context().add_class("segment")
        button.connect("toggled", lambda b, m=mode: self.set_awake_mode_from_button(m, b.get_active()))
        return button

    def add_toggle_tile(self, icon, label, getter, setter):
        button = Gtk.ToggleButton()
        button.get_style_context().add_class("tile")
        icon_label = Gtk.Label(label=icon)
        icon_label.get_style_context().add_class("tile-icon")
        button.add(icon_label)
        button.set_tooltip_text(label)
        button.set_active(bool(getter()))
        button.connect("toggled", lambda b, cb=setter: cb(b.get_active()))
        return button

    def add_action_tile(self, icon, label, callback):
        button = Gtk.Button()
        button.get_style_context().add_class("tile")
        icon_label = Gtk.Label(label=icon)
        icon_label.get_style_context().add_class("tile-icon")
        button.add(icon_label)
        button.set_tooltip_text(label)
        button.connect("clicked", lambda *_: callback())
        return button

    def on_low_power_switch(self, switch, *_):
        if self._syncing_low_power:
            return
        set_low_power(switch.get_active())
        self.sync_low_power_control()

    def sync_low_power_control(self):
        supported = low_power_supported()
        active = low_power_enabled() if supported else False
        profile = current_power_profile() or "unknown"
        if self._low_power_state_label is not None:
            self._low_power_state_label.set_text(
                "Unavailable" if not supported else f"{'On' if active else 'Off'} · {profile}"
            )
        if self._low_power_switch is not None:
            self._syncing_low_power = True
            try:
                self._low_power_switch.set_sensitive(supported)
                self._low_power_switch.set_active(active)
            finally:
                self._syncing_low_power = False

    def on_autobrightness_switch(self, switch, *_):
        if self._syncing_autobrightness:
            return
        set_autobrightness(switch.get_active())
        self.sync_autobrightness_control()

    def sync_autobrightness_control(self):
        active = autobrightness_enabled()
        daemon = autobrightness_daemon_running()
        if self._autobrightness_state_label is not None:
            if active and daemon:
                self._autobrightness_state_label.set_text("On · light sensor")
            elif active:
                self._autobrightness_state_label.set_text("On · starting")
            else:
                self._autobrightness_state_label.set_text("Off · manual")
        if self._autobrightness_switch is not None:
            self._syncing_autobrightness = True
            try:
                self._autobrightness_switch.set_active(active)
            finally:
                self._syncing_autobrightness = False

    def set_awake_mode_from_button(self, mode, active):
        if self._syncing_awake:
            return
        if active:
            set_awake_mode(mode)
        self.sync_awake_buttons()

    def sync_awake_buttons(self):
        current = awake_mode()
        state_text = {
            "": "Off",
            "system": "Display may sleep",
            "display": "Display stays on",
        }.get(current, "Off")
        if self._awake_state_label is not None:
            self._awake_state_label.set_text(state_text)
        self._syncing_awake = True
        try:
            for mode, button in self._awake_buttons.items():
                button.set_active(mode == current)
        finally:
            self._syncing_awake = False

    def add_scale(self, parent, icon, label, value, setter):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class("control")
        row.set_tooltip_text(label)
        parent.pack_start(row, False, False, 0)
        icon_label = Gtk.Label(label=icon)
        icon_label.get_style_context().add_class("slider-icon")
        row.pack_start(icon_label, False, False, 0)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_draw_value(False)
        scale.set_value(value)
        scale.set_tooltip_text(label)
        row.pack_start(scale, True, True, 0)
        value_label = Gtk.Label(label=f"{int(value)}%")
        value_label.get_style_context().add_class("value-pill")
        row.pack_start(value_label, False, False, 0)
        scale.connect("value-changed", lambda s, lbl=value_label: lbl.set_text(f"{int(s.get_value())}%"))
        scale.connect("button-release-event", lambda s, _event: (setter(s.get_value()), False)[1])

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
