#!/usr/bin/env python3
"""Apply liquid glass shader regions to visible Ghostty windows."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import glass_shader


DOTFILES_DIR = Path.home() / "dotfiles"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/ghostty-glass-{os.getuid()}"))
SHADER_FILE = RUNTIME_DIR / "ghostty-glass.frag"
LOCK_FILE = RUNTIME_DIR / "ghostty-glass.lock"
DEBUG = os.environ.get("GHOSTTY_GLASS_DEBUG") == "1"
POLL_SECONDS = 0.25
RADIUS = 28.0


def debug(message):
    if DEBUG:
        print(f"ghostty-glass: {message}", file=sys.stderr, flush=True)


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


def monitor_map():
    fallback = 2560, 1600, {0: {"scale": 1.0, "x": 0.0, "y": 0.0}}
    out = hyprctl(["monitors", "-j"], capture=True)
    if not out:
        return fallback
    try:
        monitors = json.loads(out)
    except json.JSONDecodeError:
        return fallback
    if not monitors:
        return fallback

    focused = next((item for item in monitors if item.get("focused")), monitors[0])
    by_id = {}
    for item in monitors:
        try:
            by_id[int(item["id"])] = {
                "scale": float(item.get("scale") or 1.0),
                "x": float(item.get("x") or 0.0),
                "y": float(item.get("y") or 0.0),
            }
        except (KeyError, TypeError, ValueError):
            continue
    try:
        return int(focused["width"]), int(focused["height"]), by_id
    except (KeyError, TypeError, ValueError):
        return fallback


def ghostty_regions():
    screen_width, screen_height, monitors = monitor_map()
    out = hyprctl(["clients", "-j"], capture=True)
    if not out:
        return screen_width, screen_height, []
    try:
        clients = json.loads(out)
    except json.JSONDecodeError:
        return screen_width, screen_height, []

    regions = []
    for client in clients:
        klass = client.get("class") or client.get("initialClass") or ""
        if klass != "com.mitchellh.ghostty":
            continue
        if client.get("hidden") or not client.get("mapped", True):
            continue
        try:
            x, y = client["at"]
            width, height = client["size"]
            monitor_id = int(client.get("monitor", 0))
        except (KeyError, TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        monitor = monitors.get(monitor_id, {"scale": 1.0, "x": 0.0, "y": 0.0})
        scale = monitor["scale"]
        mx = monitor["x"]
        my = monitor["y"]
        regions.append((
            (float(x) - mx + float(width) * 0.5) * scale,
            (float(y) - my + float(height) * 0.5) * scale,
            float(width) * scale,
            float(height) * scale,
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
const float DISTORTION_DEPTH = 0.16;
const float DISTORTION_STRENGTH = 0.02;
const float CHROMATIC_SHIFT_PX = 0.3;
const float GLASS_TINT = 0.98;
const float EDGE_HIGHLIGHT = 0.05;
const float BLUR_PX = 1.2;
const float FROST_AMOUNT = 0.18;
const float FROST_VEIL = 0.04;
const float TEXT_CONTRAST_LO = 0.03;
const float TEXT_CONTRAST_HI = 0.14;

float roundedSdf(vec2 p, vec2 halfSize, float radius) {{
    vec2 d = abs(p) - halfSize + vec2(radius);
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0)) - radius;
}}

vec3 sampleScreen(vec2 coord) {{
    return texture(tex, clamp(coord / SCREEN, vec2(0.0), vec2(1.0))).rgb;
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
    vec3 refracted = vec3(sampleScreen(coord - shift).r, sampleScreen(coord).g, sampleScreen(coord + shift).b);
    vec3 blurred = refracted * 0.36;
    blurred += sampleScreen(coord + vec2(BLUR_PX, 0.0)) * 0.12;
    blurred += sampleScreen(coord - vec2(BLUR_PX, 0.0)) * 0.12;
    blurred += sampleScreen(coord + vec2(0.0, BLUR_PX)) * 0.12;
    blurred += sampleScreen(coord - vec2(0.0, BLUR_PX)) * 0.12;
    blurred += sampleScreen(coord + vec2(BLUR_PX, BLUR_PX)) * 0.08;
    blurred += sampleScreen(coord - vec2(BLUR_PX, BLUR_PX)) * 0.08;
    float luma = dot(blurred, vec3(0.299, 0.587, 0.114));
    vec3 frosted = mix(mix(blurred, vec3(luma), FROST_AMOUNT), vec3(1.0), FROST_VEIL);
    vec3 glassColor = mix(frosted, vec3(1.0), clamp(rim * EDGE_HIGHLIGHT, 0.0, 0.2)) * GLASS_TINT;

    vec2 stride = vec2(1.4) / SCREEN;
    vec2 tc = pix / SCREEN;
    float lumC = dot(baseColor, vec3(0.299, 0.587, 0.114));
    float lumN = 0.0;
    lumN += dot(texture(tex, tc + vec2( stride.x, 0.0)).rgb, vec3(0.299, 0.587, 0.114));
    lumN += dot(texture(tex, tc + vec2(-stride.x, 0.0)).rgb, vec3(0.299, 0.587, 0.114));
    lumN += dot(texture(tex, tc + vec2(0.0,  stride.y)).rgb, vec3(0.299, 0.587, 0.114));
    lumN += dot(texture(tex, tc + vec2(0.0, -stride.y)).rgb, vec3(0.299, 0.587, 0.114));
    lumN *= 0.25;
    float contentMask = smoothstep(TEXT_CONTRAST_LO, TEXT_CONTRAST_HI, abs(lumC - lumN));

    vec3 glassResult = mix(baseColor, glassColor, mask);
    return mix(glassResult, baseColor, contentMask);
}}

vec4 roundedScreenCorners(vec2 pix, vec4 color) {{
    vec2 corner = min(pix, SCREEN - pix);
    if (corner.x < SCREEN_RADIUS && corner.y < SCREEN_RADIUS) {{
        vec2 d = vec2(SCREEN_RADIUS) - corner;
        float aa = smoothstep(SCREEN_RADIUS - 1.0, SCREEN_RADIUS + 0.5, length(d));
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
        RUNTIME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        if SHADER_FILE.exists() and SHADER_FILE.read_text() == source:
            return True
        SHADER_FILE.write_text(source)
        return True
    except OSError:
        return False


def acquire_lock():
    try:
        RUNTIME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = LOCK_FILE.open("w")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock
    except OSError:
        return None


def main():
    lock = acquire_lock()
    if lock is None:
        return 0

    stop = False
    last_signature = None
    lease = None

    def request_stop(*_args):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    try:
        glass_shader.reconcile()
        while not stop:
            screen_width, screen_height, regions = ghostty_regions()
            signature = (
                screen_width,
                screen_height,
                tuple(tuple(round(value, 3) for value in region) for region in regions),
            )
            if regions:
                if signature != last_signature and write_shader(screen_width, screen_height, regions):
                    if lease is None:
                        lease = glass_shader.acquire("ghostty-glass", SHADER_FILE, 10)
                    else:
                        lease.acquire()
                    debug(f"enabled {len(regions)} region(s)")
                    last_signature = signature
            else:
                if lease is not None:
                    lease.release()
                    lease = None
                    debug("released shader lease")
                last_signature = None
                glass_shader.reconcile()
            time.sleep(POLL_SECONDS)
    finally:
        if lease is not None:
            lease.release()
        glass_shader.cleanup("ghostty-glass")
        lock.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
