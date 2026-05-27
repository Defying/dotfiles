#!/usr/bin/env python3
"""Daemon that paints every visible waybar bubble with the liquid glass
shader, using AT-SPI to read each bubble's real on-screen bounds (no
hardcoded width estimates).

How it locates bubbles:
    waybar (application)
      └─ frame[waybar]
           └─ filler [the bar root container]
                └─ filler [one per modules-left / -center / -right section]
                     └─ panel [one per module — workspaces, custom bubble, etc]

We iterate the section fillers' direct panel children, drop anything that
reports an off-screen x (waybar's signal for "this widget is hidden right
now", e.g. the bluetooth glyph when BT is off), and emit one SDF region per
remaining panel. AT-SPI gives geometry in waybar's logical pixels; we
multiply by the monitor's Hyprland scale to get physical pixels for the
shader.

Updates are event-driven: we subscribe to object:bounds-changed, debounce
to 80 ms, then rebuild the shader and re-acquire the lease.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pyatspi

sys.path.insert(0, str(Path(__file__).resolve().parent))
import glass_shader  # noqa: E402

OWNER       = "waybar-glass"
PRIORITY    = 20
BAR_RADIUS  = 16   # px, logical — must match the .modules CSS border-radius
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/wbg-{os.getuid()}"))
SHADER_FILE = RUNTIME_DIR / "waybar-glass.frag"
DEBOUNCE_S  = 0.08


# ── Hyprland helpers ────────────────────────────────────────────────────────

def hyprctl(args, *, capture=False):
    cmd = ["hyprctl", *args]
    try:
        if capture:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        return subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, check=False).returncode == 0
    except Exception:
        return "" if capture else False


def focused_monitor():
    try:
        monitors = json.loads(hyprctl(["monitors", "-j"], capture=True))
        m = next((x for x in monitors if x.get("focused")), monitors[0])
        return int(m["width"]), int(m["height"]), float(m.get("scale", 2.0))
    except Exception:
        return 2560, 1600, 2.0


# ── AT-SPI walk ─────────────────────────────────────────────────────────────

def find_waybar_frame():
    desktop = pyatspi.Registry.getDesktop(0)
    for app in desktop:
        if (app.name or "").lower() == "waybar":
            for i in range(app.childCount):
                child = app[i]
                if child.getRoleName() == "frame":
                    return child
    return None


def bubble_rects(frame) -> list[tuple[int, int, int, int]]:
    """Return [(x, y, w, h), …] in waybar's logical pixels for every visible
    bubble (i.e. each section filler's direct panel children)."""
    rects: list[tuple[int, int, int, int]] = []
    if frame is None or frame.childCount == 0:
        return rects
    root = frame[0]                     # the outer filler
    for s in range(root.childCount):
        section = root[s]               # filler for left / center / right
        for p in range(section.childCount):
            panel = section[p]
            try:
                ext = panel.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
            except Exception:
                continue
            if ext.x < 0 or ext.width <= 0 or ext.height <= 0:
                continue
            rects.append((ext.x, ext.y, ext.width, ext.height))
    return rects


# ── Shader emission ─────────────────────────────────────────────────────────

SHADER_HEAD = """#version 300 es
precision highp float;
in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;

const vec2  SCR    = vec2({sw:.1f},{sh:.1f});
const float SCRNR  = 28.0;
const float RAD    = {rad:.1f};
const float BL     = {bl:.2f};
const float DIST_D = 0.18;
const float DIST_S = 0.08;
const float CA     = 1.8;
const float TINT   = 0.92;
const float FROST  = 0.30;
const float VEIL   = 0.18;
const float EHL    = 0.18;

float sdf(vec2 p, vec2 hs, float r) {{
    vec2 d = abs(p) - hs + vec2(r);
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0)) - r;
}}
vec3 smp(vec2 c) {{ return texture(tex, clamp(c / SCR, vec2(0), vec2(1))).rgb; }}

void apply_glass(inout vec4 col, vec2 pix, vec2 cen, vec2 sz) {{
    vec2 g = pix - cen;
    vec2 hs = sz * 0.5;
    float inside = -sdf(g, hs, RAD) / max(min(sz.x, sz.y), 1.0);
    float mask = smoothstep(-0.005, 0.008, inside);
    if (mask <= 0.0) return;
    float cl = length(g);
    vec2 n = cl > 0.0001 ? g / cl : vec2(0);
    float df = 1.0 - clamp(inside / DIST_D, 0.0, 1.0);
    float dis = 1.0 - sqrt(max(1.0 - df * df, 0.0));
    vec2 c = pix - dis * n * hs * DIST_S;
    float rim = 1.0 - smoothstep(0.0, 0.030, inside);
    vec2 sh = n * rim * CA;
    vec3 ref = vec3(smp(c - sh).r, smp(c).g, smp(c + sh).b);
    vec3 b = ref * 0.42
        + smp(c + vec2(BL, 0)) * 0.11 + smp(c - vec2(BL, 0)) * 0.11
        + smp(c + vec2(0, BL)) * 0.11 + smp(c - vec2(0, BL)) * 0.11
        + smp(c + vec2(BL, BL)) * 0.07 + smp(c - vec2(BL, BL)) * 0.07;
    float tl = 1.0 - smoothstep(-hs.y, -hs.y * 0.10, g.y);
    float dia = 1.0 - smoothstep(-0.6, 0.3, g.x / hs.x + g.y / hs.y);
    float hl = clamp(rim * EHL + tl * dia * 0.08, 0.0, 0.22);
    float lm = dot(b, vec3(0.299, 0.587, 0.114));
    vec3 fr = mix(mix(b, vec3(lm), FROST), vec3(1), VEIL);
    vec3 gc = mix(fr, vec3(1), hl) * TINT;
    gc = mix(gc, vec3(0.62, 0.58, 0.92), 0.025);
    float pixLum = dot(col.rgb, vec3(0.299, 0.587, 0.114));
    float pixMax = max(max(col.r, col.g), col.b);
    float pixMin = min(min(col.r, col.g), col.b);
    float pixSat = (pixMax - pixMin) / max(pixMax, 0.001);
    float textLum = smoothstep(0.50, 0.80, pixLum);
    float textDesat = 1.0 - smoothstep(0.30, 0.60, pixSat);
    float stateColor = smoothstep(0.55, 0.85, pixSat);
    float textiness = max(textLum * textDesat, stateColor * 0.85);
    vec3 glassed = mix(col.rgb, gc, mask);
    vec3 brightened = mix(col.rgb, vec3(1.0), 0.40);
    col.rgb = mix(glassed, brightened, mask * textiness);
}}

void main() {{
    vec2 pix = v_texcoord * SCR;
    vec4 col = texture(tex, v_texcoord);
"""

SHADER_TAIL = """
    vec2 corner = min(pix, SCR - pix);
    if (corner.x < SCRNR && corner.y < SCRNR) {
        vec2 d = vec2(SCRNR) - corner;
        float aa = smoothstep(SCRNR - 1.0, SCRNR + 0.5, length(d));
        col.rgb = mix(col.rgb, vec3(0), aa);
        col.a = mix(col.a, 1.0, aa);
    }
    fragColor = col;
}
"""


def build_shader(rects_logical, sw, sh, scale) -> str:
    head = SHADER_HEAD.format(
        sw=sw, sh=sh, rad=BAR_RADIUS * scale, bl=max(1.4, 1.15 * scale)
    )
    body: list[str] = []
    for x, y, w, h in rects_logical:
        cx = (x + w / 2.0) * scale
        cy = (y + h / 2.0) * scale
        bw = w * scale
        bh = h * scale
        body.append(
            f"    apply_glass(col, pix, vec2({cx:.1f},{cy:.1f}), vec2({bw:.1f},{bh:.1f}));"
        )
    return head + "\n".join(body) + SHADER_TAIL


# ── State ───────────────────────────────────────────────────────────────────

class GlassState:
    def __init__(self) -> None:
        self.lease = None
        self.last_rects: list[tuple[int, int, int, int]] = []
        self.pending = False
        self.lock = threading.Lock()
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    def regenerate(self) -> bool:
        frame = find_waybar_frame()
        if frame is None:
            return False
        rects = bubble_rects(frame)
        if not rects:
            return False
        if rects == self.last_rects:
            return False
        sw, sh, scale = focused_monitor()
        SHADER_FILE.write_text(build_shader(rects, sw, sh, scale))
        self.last_rects = rects
        return True

    def reapply(self) -> None:
        if self.regenerate():
            if self.lease is None:
                self.lease = glass_shader.acquire(OWNER, SHADER_FILE, PRIORITY)
            else:
                self.lease.acquire()


# ── Main loop with event-driven debounce ────────────────────────────────────

def main() -> int:
    state = GlassState()
    state.reapply()

    def schedule_update():
        with state.lock:
            if state.pending:
                return
            state.pending = True

        def fire():
            time.sleep(DEBOUNCE_S)
            with state.lock:
                state.pending = False
            try:
                state.reapply()
            except Exception as exc:
                print(f"waybar-glass: update failed: {exc}", file=sys.stderr)

        threading.Thread(target=fire, daemon=True).start()

    def on_event(event):
        # bounds-changed fires whenever any tracked widget moves or resizes.
        # We just trigger a debounced rebuild instead of inspecting the event.
        schedule_update()

    pyatspi.Registry.registerEventListener(on_event, "object:bounds-changed")
    pyatspi.Registry.registerEventListener(on_event, "object:children-changed")

    def shutdown(*_):
        try:
            pyatspi.Registry.stop()
        except Exception:
            pass
        if state.lease is not None:
            state.lease.release()
        os._exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT,  shutdown)

    # Periodic safety reapply so monitor scale changes or compositor restarts
    # don't strand us on a stale shader.
    def heartbeat():
        while True:
            time.sleep(30)
            schedule_update()
    threading.Thread(target=heartbeat, daemon=True).start()

    pyatspi.Registry.start()  # blocks until stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
