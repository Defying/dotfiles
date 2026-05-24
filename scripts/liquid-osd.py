#!/usr/bin/env python3
import argparse
import fcntl
import json
import math
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import cairo
import gi
import glass_shader

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import GLib, Gtk, GtkLayerShell


WIDTH = 320
HEIGHT = 72
TOP_MARGIN = 64
RADIUS = 22
TIMEOUT_MS = 1400
TIMEOUT_MS = int(os.environ.get("LIQUID_OSD_TIMEOUT_MS", TIMEOUT_MS))

DOTFILES_DIR = Path.home() / "dotfiles"
ROUNDED_SHADER = DOTFILES_DIR / "config/hypr/shaders/rounded-corners.frag"


def choose_runtime_dir():
    candidates = []
    if os.environ.get("XDG_RUNTIME_DIR"):
        candidates.append(Path(os.environ["XDG_RUNTIME_DIR"]))
    candidates.append(Path("/tmp") / f"liquid-osd-{os.getuid()}")

    for path in candidates:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("")
            probe.unlink()
            return path
        except OSError:
            continue

    return Path("/tmp")


RUNTIME_DIR = choose_runtime_dir()
SHADER_FILE = RUNTIME_DIR / "liquid-osd.frag"
SOCKET_FILE = RUNTIME_DIR / "liquid-osd.sock"
PID_FILE = RUNTIME_DIR / "liquid-osd.pid"
LOCK_FILE = RUNTIME_DIR / "liquid-osd.lock"
DEBUG = os.environ.get("LIQUID_OSD_DEBUG") == "1"


def debug(message):
    if DEBUG:
        print(f"liquid-osd: {message}", file=sys.stderr, flush=True)


def hyprctl(args, *, capture=False):
    cmd = ["hyprctl", *args]
    try:
        if capture:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        completed = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return "" if capture else False


def focused_monitor():
    fallback = 2560, 1600, 1.0, 0
    out = hyprctl(["monitors", "-j"], capture=True)
    if not out:
        return fallback

    try:
        monitors = json.loads(out)
    except json.JSONDecodeError:
        return fallback

    if not monitors:
        return fallback

    monitor = next((item for item in monitors if item.get("focused")), monitors[0])
    try:
        reserved = monitor.get("reserved") or [0, 0, 0, 0]
        reserved_top = int(reserved[1]) if len(reserved) > 1 else 0
        return (
            int(monitor["width"]),
            int(monitor["height"]),
            float(monitor.get("scale") or 1.0),
            reserved_top,
        )
    except (KeyError, TypeError, ValueError):
        return fallback


def current_screen_shader():
    out = hyprctl(["getoption", "decoration:screen_shader", "-j"], capture=True)
    if not out:
        return str(ROUNDED_SHADER)

    try:
        option = json.loads(out)
    except json.JSONDecodeError:
        return str(ROUNDED_SHADER)

    shader = option.get("str") if option.get("set") else ""
    if not shader or shader == str(SHADER_FILE):
        return str(ROUNDED_SHADER)
    return str(shader)


def shader_source(screen_width, screen_height, scale, reserved_top):
    osd_width = WIDTH * scale
    osd_height = HEIGHT * scale
    osd_center_x = screen_width * 0.5
    osd_center_y = (reserved_top + TOP_MARGIN + HEIGHT * 0.5) * scale
    osd_radius = RADIUS * scale
    blur_px = max(1.5, 1.35 * scale)

    return f"""#version 300 es
precision highp float;

in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;

const vec2 SCREEN = vec2({screen_width:.3f}, {screen_height:.3f});
const float SCREEN_RADIUS = 28.0;

const vec2 OSD_CENTER = vec2({osd_center_x:.3f}, {osd_center_y:.3f});
const vec2 OSD_SIZE = vec2({osd_width:.3f}, {osd_height:.3f});
const float OSD_RADIUS = {osd_radius:.3f};

const float DISTORTION_DEPTH = 0.22;
const float DISTORTION_STRENGTH = 0.16;
const float CHROMATIC_SHIFT_PX = 3.0;
const float GLASS_TINT = 0.94;
const float EDGE_HIGHLIGHT = 0.20;
const float BLUR_PX = {blur_px:.3f};
const float FROST_AMOUNT = 0.28;
const float FROST_VEIL = 0.16;

float roundedSdf(vec2 p, vec2 halfSize, float radius) {{
    vec2 d = abs(p) - halfSize + vec2(radius);
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0)) - radius;
}}

vec3 sampleScreen(vec2 coord) {{
    vec2 uv = clamp(coord / SCREEN, vec2(0.0), vec2(1.0));
    return texture(tex, uv).rgb;
}}

vec3 liquidGlass(vec2 pix, vec3 baseColor) {{
    vec2 glassCoord = pix - OSD_CENTER;
    vec2 halfSize = OSD_SIZE * 0.5;

    if (abs(glassCoord.x) > halfSize.x + 2.0 || abs(glassCoord.y) > halfSize.y + 2.0) {{
        return baseColor;
    }}

    float size = max(min(OSD_SIZE.x, OSD_SIZE.y), 1.0);
    float inside = -roundedSdf(glassCoord, halfSize, OSD_RADIUS) / size;
    float mask = smoothstep(-0.006, 0.006, inside);
    if (mask <= 0.0) {{
        return baseColor;
    }}

    float coordLen = length(glassCoord);
    vec2 normal = coordLen > 0.0001 ? glassCoord / coordLen : vec2(0.0);
    float distFromCenter = 1.0 - clamp(inside / DISTORTION_DEPTH, 0.0, 1.0);
    float distortion = 1.0 - sqrt(max(1.0 - distFromCenter * distFromCenter, 0.0));
    vec2 offset = distortion * normal * OSD_SIZE * 0.5 * DISTORTION_STRENGTH;
    vec2 coord = pix - offset;

    float rim = 1.0 - smoothstep(0.0, 0.035, inside);
    vec2 shift = normal * rim * CHROMATIC_SHIFT_PX;
    vec3 refracted = vec3(
        sampleScreen(coord - shift).r,
        sampleScreen(coord).g,
        sampleScreen(coord + shift).b
    );

    vec3 blurred = refracted * 0.40;
    blurred += sampleScreen(coord + vec2(BLUR_PX, 0.0)) * 0.11;
    blurred += sampleScreen(coord - vec2(BLUR_PX, 0.0)) * 0.11;
    blurred += sampleScreen(coord + vec2(0.0, BLUR_PX)) * 0.11;
    blurred += sampleScreen(coord - vec2(0.0, BLUR_PX)) * 0.11;
    blurred += sampleScreen(coord + vec2(BLUR_PX, BLUR_PX)) * 0.08;
    blurred += sampleScreen(coord + vec2(-BLUR_PX, BLUR_PX)) * 0.08;

    float topLight = 1.0 - smoothstep(-halfSize.y, -halfSize.y * 0.15, glassCoord.y);
    float diagonal = 1.0 - smoothstep(-0.65, 0.35, glassCoord.x / halfSize.x + glassCoord.y / halfSize.y);
    float highlight = clamp(rim * EDGE_HIGHLIGHT + topLight * diagonal * 0.07, 0.0, 0.28);

    float luma = dot(blurred, vec3(0.299, 0.587, 0.114));
    vec3 frosted = mix(blurred, vec3(luma), FROST_AMOUNT);
    frosted = mix(frosted, vec3(1.0), FROST_VEIL);

    vec3 glassColor = mix(frosted, vec3(1.0), highlight);
    glassColor *= vec3(GLASS_TINT);
    glassColor = mix(glassColor, vec3(0.74, 0.52, 0.95), 0.035);

    return mix(baseColor, glassColor, mask);
}}

vec4 roundedScreenCorners(vec2 pix, vec4 color) {{
    vec2 corner = min(pix, SCREEN - pix);

    if (corner.x < SCREEN_RADIUS && corner.y < SCREEN_RADIUS) {{
        vec2 d = vec2(SCREEN_RADIUS) - corner;
        float dist = length(d);
        float aa = smoothstep(SCREEN_RADIUS - 1.0, SCREEN_RADIUS + 0.5, dist);
        color.rgb = mix(color.rgb, vec3(0.0), aa);
        color.a = mix(color.a, 1.0, aa);
    }}

    return color;
}}

void main() {{
    vec2 pix = v_texcoord * SCREEN;
    vec4 color = texture(tex, v_texcoord);
    color.rgb = liquidGlass(pix, color.rgb);
    fragColor = roundedScreenCorners(pix, color);
}}
"""


def write_shader():
    screen_width, screen_height, scale, reserved_top = focused_monitor()
    source = shader_source(screen_width, screen_height, scale, reserved_top)
    debug(
        f"write shader {SHADER_FILE} for {screen_width}x{screen_height} "
        f"scale={scale} reserved_top={reserved_top}"
    )
    try:
        if SHADER_FILE.exists() and SHADER_FILE.read_text() == source:
            return True
        SHADER_FILE.write_text(source)
        return True
    except OSError:
        return False


class ShaderController:
    def __init__(self):
        self.lease = None

    def enable(self):
        if not write_shader():
            return False
        self.lease = glass_shader.acquire("liquid-osd", SHADER_FILE, 60)
        debug(f"enable shader lease={SHADER_FILE}")
        return True

    def restore(self):
        if self.lease is None:
            return
        self.lease.release()
        debug("released shader lease")
        self.lease = None


def rounded_rectangle(cr, x, y, width, height, radius):
    right = x + width
    bottom = y + height
    cr.new_sub_path()
    cr.arc(right - radius, y + radius, radius, -math.pi / 2.0, 0.0)
    cr.arc(right - radius, bottom - radius, radius, 0.0, math.pi / 2.0)
    cr.arc(x + radius, bottom - radius, radius, math.pi / 2.0, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, math.pi * 1.5)
    cr.close_path()


class OsdWindow(Gtk.Window):
    def __init__(self, payload):
        super().__init__(title="liquid-osd")
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(WIDTH, HEIGHT)

        visual = self.get_screen().get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "liquid-osd")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, TOP_MARGIN)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self, 0)

        self.title = ""
        self.value = 0
        self.icon_name = ""
        self.timeout_id = 0

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(WIDTH, HEIGHT)
        self.area.connect("draw", self.draw)
        self.add(self.area)

        self.apply_payload(payload)
        self.show_all()

    def apply_payload(self, payload):
        self.title = str(payload.get("title", ""))
        self.value = max(0, min(100, int(payload.get("value", 0))))
        self.icon_name = str(payload.get("icon", ""))
        self.area.queue_draw()
        self.arm_timeout()
        return False

    def arm_timeout(self):
        if self.timeout_id:
            GLib.source_remove(self.timeout_id)
        self.timeout_id = GLib.timeout_add(TIMEOUT_MS, self.finish)

    def finish(self):
        self.timeout_id = 0
        Gtk.main_quit()
        return False

    def draw(self, area, cr):
        width = area.get_allocated_width()
        height = area.get_allocated_height()

        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        cr.select_font_face("SF Pro Text", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(13)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.34)
        cr.move_to(57, 29)
        cr.show_text(self.title)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.93)
        cr.move_to(56, 28)
        cr.show_text(self.title)

        cr.select_font_face("SF Pro Text", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(13)
        text = f"{self.value}%"
        ext = cr.text_extents(text)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.32)
        cr.move_to(width - 15 - ext.width, 29)
        cr.show_text(text)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
        cr.move_to(width - 16 - ext.width, 28)
        cr.show_text(text)

        cr.set_source_rgba(0.0, 0.0, 0.0, 0.20)
        rounded_rectangle(cr, 56, 44, width - 78, 7, 3.5)
        cr.fill()
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.24)
        rounded_rectangle(cr, 56, 44, width - 78, 7, 3.5)
        cr.fill()
        cr.set_source_rgba(0.82, 0.62, 1.0, 0.92)
        rounded_rectangle(cr, 56, 44, (width - 78) * self.value / 100.0, 7, 3.5)
        cr.fill()

        cr.set_source_rgba(0.0, 0.0, 0.0, 0.28)
        cr.set_line_width(2.5)
        cr.arc(30, 36, 14, 0, math.tau)
        cr.stroke()
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.88)
        cr.set_line_width(1.5)
        cr.arc(30, 36, 14, 0, math.tau)
        cr.stroke()
        cr.set_font_size(17)
        title_lower = self.title.lower()
        glyph = "B" if title_lower.startswith("bright") else ("M" if "mic" in title_lower else "V")
        ext = cr.text_extents(glyph)
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.34)
        cr.move_to(31 - ext.width / 2 - ext.x_bearing, 37 - ext.height / 2 - ext.y_bearing)
        cr.show_text(glyph)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.94)
        cr.move_to(30 - ext.width / 2 - ext.x_bearing, 36 - ext.height / 2 - ext.y_bearing)
        cr.show_text(glyph)


def payload_from_args(args):
    return {
        "title": args.title,
        "value": max(0, min(100, int(args.value))),
        "icon": args.icon,
    }


def send_update(payload):
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(0.08)
        client.connect(str(SOCKET_FILE))
        client.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        client.close()
        debug(f"sent update to {SOCKET_FILE}")
        return True
    except OSError:
        return False


def bind_socket():
    try:
        SOCKET_FILE.unlink()
    except FileNotFoundError:
        pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_FILE))
    server.listen(8)
    server.settimeout(0.2)
    debug(f"listening on {SOCKET_FILE}")
    return server


def acquire_lock():
    try:
        lock = LOCK_FILE.open("w")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock
    except OSError:
        return None


def wait_for_update_socket(payload):
    deadline = time.monotonic() + 0.45
    while time.monotonic() < deadline:
        if send_update(payload):
            return True
        time.sleep(0.03)
    return False


def socket_loop(server, window, stop_event):
    while not stop_event.is_set():
        try:
            conn, _ = server.accept()
        except (TimeoutError, socket.timeout):
            continue
        except OSError:
            break

        with conn:
            try:
                data = conn.recv(4096)
                payload = json.loads(data.decode("utf-8").strip())
            except Exception:
                continue
        GLib.idle_add(window.apply_payload, payload)


def write_pid():
    try:
        PID_FILE.write_text(str(os.getpid()))
    except OSError:
        pass


def cleanup_socket():
    for path in (SOCKET_FILE, PID_FILE):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--value", required=True, type=int)
    parser.add_argument("--icon", default="")
    args = parser.parse_args()
    payload = payload_from_args(args)

    if send_update(payload):
        return 0

    lock = acquire_lock()
    if lock is None:
        if wait_for_update_socket(payload):
            return 0
        debug("another instance is starting; dropping stale update")
        return 0

    shader = ShaderController()
    server = bind_socket()
    stop_event = threading.Event()

    if not shader.enable():
        print("liquid-osd: failed to enable Hyprland screen shader", file=sys.stderr)

    write_pid()
    window = OsdWindow(payload)
    debug("window created")
    thread = threading.Thread(
        target=socket_loop,
        args=(server, window, stop_event),
        daemon=True,
    )
    thread.start()

    def request_quit(*_args):
        GLib.idle_add(Gtk.main_quit)

    signal.signal(signal.SIGTERM, request_quit)
    signal.signal(signal.SIGINT, request_quit)
    window.connect("destroy", lambda *_args: Gtk.main_quit())

    try:
        Gtk.main()
    finally:
        stop_event.set()
        try:
            server.close()
        except OSError:
            pass
        cleanup_socket()
        shader.restore()
        lock.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
