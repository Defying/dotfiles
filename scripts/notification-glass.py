#!/usr/bin/env python3
"""Apply liquid glass shader regions to live mako notification layers."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


DOTFILES_DIR = Path.home() / "dotfiles"
ROUNDED_SHADER = DOTFILES_DIR / "config/hypr/shaders/rounded-corners.frag"


def choose_runtime_dir():
    candidates = []
    if os.environ.get("XDG_RUNTIME_DIR"):
        candidates.append(Path(os.environ["XDG_RUNTIME_DIR"]))
    candidates.append(Path("/tmp") / f"notification-glass-{os.getuid()}")

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
SHADER_FILE = RUNTIME_DIR / "notification-glass.frag"
LOCK_FILE = RUNTIME_DIR / "notification-glass.lock"
DEBUG = os.environ.get("NOTIFICATION_GLASS_DEBUG") == "1"
IDLE_POLL_SECONDS = 0.45
ACTIVE_POLL_SECONDS = 0.12
RADIUS = 18.0


def debug(message):
    if DEBUG:
        print(f"notification-glass: {message}", file=sys.stderr, flush=True)


def hyprctl(args, *, capture=False):
    try:
        if capture:
            return subprocess.check_output(["hyprctl", *args], text=True, stderr=subprocess.DEVNULL)
        completed = subprocess.run(
            ["hyprctl", *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return "" if capture else False


def current_screen_shader():
    out = hyprctl(["getoption", "decoration:screen_shader", "-j"], capture=True)
    if not out:
        return ""

    try:
        option = json.loads(out)
    except json.JSONDecodeError:
        return ""

    return str(option.get("str") or "") if option.get("set") else ""


def focused_monitor_size():
    fallback = 2560, 1600
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
        return int(monitor["width"]), int(monitor["height"])
    except (KeyError, TypeError, ValueError):
        return fallback


def notification_regions():
    layers_out = hyprctl(["layers", "-j"], capture=True)
    monitors_out = hyprctl(["monitors", "-j"], capture=True)
    screen_width, screen_height = focused_monitor_size()
    if not layers_out or not monitors_out:
        return screen_width, screen_height, []

    try:
        layers = json.loads(layers_out)
        monitors = {item.get("name"): item for item in json.loads(monitors_out)}
    except (TypeError, json.JSONDecodeError):
        return screen_width, screen_height, []

    regions = []
    for monitor_name, monitor_layers in layers.items():
        monitor = monitors.get(monitor_name) or {}
        try:
            scale = float(monitor.get("scale") or 1.0)
            screen_width = int(monitor.get("width") or screen_width)
            screen_height = int(monitor.get("height") or screen_height)
        except (TypeError, ValueError):
            scale = 1.0

        for level in (monitor_layers.get("levels") or {}).values():
            for layer in level:
                if layer.get("namespace") != "notifications":
                    continue
                try:
                    x = float(layer["x"])
                    y = float(layer["y"])
                    width = float(layer["w"])
                    height = float(layer["h"])
                except (KeyError, TypeError, ValueError):
                    continue
                if width <= 0.0 or height <= 0.0:
                    continue
                regions.append((
                    (x + width * 0.5) * scale,
                    (y + height * 0.5) * scale,
                    width * scale,
                    height * scale,
                    RADIUS * scale,
                ))

    return screen_width, screen_height, regions


def shader_source(screen_width, screen_height, regions):
    calls = "\n".join(
        "    color.rgb = liquidGlass(pix, color.rgb, "
        f"vec2({cx:.3f}, {cy:.3f}), vec2({width:.3f}, {height:.3f}), {radius:.3f});"
        for cx, cy, width, height, radius in regions
    )

    return f"""#version 300 es
precision highp float;

in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;

const vec2 SCREEN = vec2({screen_width:.3f}, {screen_height:.3f});
const float SCREEN_RADIUS = 28.0;
const float DISTORTION_DEPTH = 0.20;
const float DISTORTION_STRENGTH = 0.13;
const float CHROMATIC_SHIFT_PX = 2.4;
const float GLASS_TINT = 0.93;
const float EDGE_HIGHLIGHT = 0.22;
const float BLUR_PX = 1.9;
const float FROST_AMOUNT = 0.25;
const float FROST_VEIL = 0.13;

float roundedSdf(vec2 p, vec2 halfSize, float radius) {{
    vec2 d = abs(p) - halfSize + vec2(radius);
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0)) - radius;
}}

vec3 sampleScreen(vec2 coord) {{
    vec2 uv = clamp(coord / SCREEN, vec2(0.0), vec2(1.0));
    return texture(tex, uv).rgb;
}}

vec3 liquidGlass(vec2 pix, vec3 baseColor, vec2 center, vec2 size, float radius) {{
    vec2 glassCoord = pix - center;
    vec2 halfSize = size * 0.5;

    if (abs(glassCoord.x) > halfSize.x + 2.0 || abs(glassCoord.y) > halfSize.y + 2.0) {{
        return baseColor;
    }}

    float inside = -roundedSdf(glassCoord, halfSize, radius) / max(min(size.x, size.y), 1.0);
    float mask = smoothstep(-0.005, 0.008, inside);
    if (mask <= 0.0) {{
        return baseColor;
    }}

    float coordLen = length(glassCoord);
    vec2 normal = coordLen > 0.0001 ? glassCoord / coordLen : vec2(0.0);
    float distFromCenter = 1.0 - clamp(inside / DISTORTION_DEPTH, 0.0, 1.0);
    float distortion = 1.0 - sqrt(max(1.0 - distFromCenter * distFromCenter, 0.0));
    vec2 coord = pix - distortion * normal * halfSize * DISTORTION_STRENGTH;

    float rim = 1.0 - smoothstep(0.0, 0.030, inside);
    vec2 shift = normal * rim * CHROMATIC_SHIFT_PX;
    vec3 refracted = vec3(
        sampleScreen(coord - shift).r,
        sampleScreen(coord).g,
        sampleScreen(coord + shift).b
    );

    vec3 blurred = refracted * 0.38;
    blurred += sampleScreen(coord + vec2(BLUR_PX, 0.0)) * 0.12;
    blurred += sampleScreen(coord - vec2(BLUR_PX, 0.0)) * 0.12;
    blurred += sampleScreen(coord + vec2(0.0, BLUR_PX)) * 0.12;
    blurred += sampleScreen(coord - vec2(0.0, BLUR_PX)) * 0.12;
    blurred += sampleScreen(coord + vec2(BLUR_PX, BLUR_PX)) * 0.07;
    blurred += sampleScreen(coord - vec2(BLUR_PX, BLUR_PX)) * 0.07;

    float topLight = 1.0 - smoothstep(-halfSize.y, -halfSize.y * 0.10, glassCoord.y);
    float diagonal = 1.0 - smoothstep(-0.6, 0.3, glassCoord.x / halfSize.x + glassCoord.y / halfSize.y);
    float highlight = clamp(rim * EDGE_HIGHLIGHT + topLight * diagonal * 0.08, 0.0, 0.28);

    float luma = dot(blurred, vec3(0.299, 0.587, 0.114));
    vec3 frosted = mix(mix(blurred, vec3(luma), FROST_AMOUNT), vec3(1.0), FROST_VEIL);
    vec3 glassColor = mix(frosted, vec3(1.0), highlight) * GLASS_TINT;
    glassColor = mix(glassColor, vec3(0.75, 0.52, 0.95), 0.03);

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
{calls}
    fragColor = roundedScreenCorners(pix, color);
}}
"""


def write_shader(screen_width, screen_height, regions):
    source = shader_source(screen_width, screen_height, regions)
    try:
        if SHADER_FILE.exists() and SHADER_FILE.read_text() == source:
            return True
        SHADER_FILE.write_text(source)
        return True
    except OSError:
        return False


def acquire_lock():
    try:
        lock = LOCK_FILE.open("w")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock
    except OSError:
        return None


def restore_if_owned():
    if current_screen_shader() == str(SHADER_FILE):
        hyprctl(["keyword", "decoration:screen_shader", str(ROUNDED_SHADER)])


def main():
    lock = acquire_lock()
    if lock is None:
        return 0

    stop = False
    last_signature = None

    def request_stop(*_args):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    try:
        while not stop:
            screen_width, screen_height, regions = notification_regions()
            signature = (
                screen_width,
                screen_height,
                tuple(tuple(round(value, 3) for value in region) for region in regions),
            )
            current_shader = current_screen_shader()

            if regions:
                if current_shader in ("", str(ROUNDED_SHADER), str(SHADER_FILE)):
                    if signature != last_signature or current_shader != str(SHADER_FILE):
                        if write_shader(screen_width, screen_height, regions):
                            hyprctl(["keyword", "decoration:screen_shader", str(SHADER_FILE)])
                            debug(f"enabled {len(regions)} region(s)")
                            last_signature = signature
                else:
                    debug(f"shader busy: {current_shader}")
            else:
                if current_shader == str(SHADER_FILE):
                    hyprctl(["keyword", "decoration:screen_shader", str(ROUNDED_SHADER)])
                    debug("restored rounded shader")
                last_signature = None

            time.sleep(ACTIVE_POLL_SECONDS if regions else IDLE_POLL_SECONDS)
    finally:
        restore_if_owned()
        lock.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
