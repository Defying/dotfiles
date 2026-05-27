#!/usr/bin/env python3
"""Daemon that holds a low-priority screen-shader lease painting every
waybar bubble with the liquid glass effect.

It emits a fragment shader that contains one SDF region per visible bubble,
positioned from the bar layout in `dotfiles/config/waybar/config.jsonc` and
estimated widths for each module type. The bubble row's y-span and rounded
radius match the CSS so the shader-rendered shape lines up with the GTK
border that waybar still draws on top.

When a higher-priority popup (notification panel, weather popup) acquires
the screen_shader, the waybar shader steps aside; releasing the popup's
lease lets the reconciler reapply this one.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import glass_shader  # noqa: E402

OWNER       = "waybar-glass"
PRIORITY    = 20
BAR_TOP     = 10
BAR_HEIGHT  = 36
BAR_RADIUS  = 16
SIDE_MARGIN = 28
BUBBLE_GAP  = 12

WAYBAR_CONFIG = Path.home() / "dotfiles" / "config" / "waybar" / "config.jsonc"

# Approximate widths in waybar's logical pixels. The values err on the wider
# side so the SDF safely encloses the GTK widget; a small pad doesn't show
# because the bubble's CSS border + content draw on top of the glass.
MODULE_WIDTH = {
    "hyprland/workspaces":   170,
    "custom/codex-tokens":    86,
    "custom/claude-tokens":   86,
    "custom/date":           120,
    "custom/clock-24":        80,
    "custom/clock-12":        96,
    "custom/launcher":        38,
    "custom/weather":         88,
    "custom/notifications":   60,
    "custom/quick-settings":  50,
    "tray":                   58,
    "group/status":          152,
}
DEFAULT_WIDTH = 80
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/wbg-{os.getuid()}"))
SHADER_FILE = RUNTIME_DIR / "waybar-glass.frag"


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
        reserved_top = int((m.get("reserved") or [0, 0])[1])
        return int(m["width"]), int(m["height"]), float(m.get("scale", 2.0)), reserved_top
    except Exception:
        return 2560, 1600, 2.0, 0


def load_waybar_layout() -> dict:
    """Read the (JSON-with-comments) waybar config and return the three module lists."""
    text = WAYBAR_CONFIG.read_text()
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    cfg = json.loads(text)
    return {
        "left":   list(cfg.get("modules-left",   [])),
        "center": list(cfg.get("modules-center", [])),
        "right":  list(cfg.get("modules-right",  [])),
    }


def module_widths(modules: list[str]) -> list[int]:
    return [MODULE_WIDTH.get(m, DEFAULT_WIDTH) for m in modules]


def bubble_bounds(layout: dict, screen_logical_w: int) -> list[tuple[int, int]]:
    """Return (left_x, width) pairs for each visible bubble across the bar."""
    out: list[tuple[int, int]] = []

    # Left section: starts at SIDE_MARGIN, items separated by BUBBLE_GAP.
    x = SIDE_MARGIN
    for w in module_widths(layout["left"]):
        out.append((x, w))
        x += w + BUBBLE_GAP

    # Center section: total width including gaps, centered around screen midpoint.
    cw = module_widths(layout["center"])
    if cw:
        total = sum(cw) + BUBBLE_GAP * (len(cw) - 1)
        x = screen_logical_w // 2 - total // 2
        for w in cw:
            out.append((x, w))
            x += w + BUBBLE_GAP

    # Right section: anchored at screen_w - SIDE_MARGIN, packed right-to-left.
    rw = module_widths(layout["right"])
    if rw:
        x = screen_logical_w - SIDE_MARGIN
        for w in reversed(rw):
            x -= w
            out.append((x, w))
            x -= BUBBLE_GAP
    return out


# ── Shader emission ──────────────────────────────────────────────────────────

SHADER_HEAD = """#version 300 es
precision highp float;
in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;

const vec2  SCR   = vec2({sw:.1f},{sh:.1f});
const float SCRNR = 28.0;
const float RAD   = {rad:.1f};
const float BL    = {bl:.2f};
const float DIST_D = 0.18;
const float DIST_S = 0.08;
const float CA     = 1.8;
const float TINT   = 0.92;
const float FROST  = 0.30;
const float VEIL   = 0.18;
const float EHL    = 0.18;

float sdf(vec2 p,vec2 hs,float r){{
    vec2 d=abs(p)-hs+vec2(r);
    return min(max(d.x,d.y),0.0)+length(max(d,0.0))-r;
}}
vec3 smp(vec2 c){{return texture(tex,clamp(c/SCR,vec2(0),vec2(1))).rgb;}}

void apply_glass(inout vec4 col, vec2 pix, vec2 cen, vec2 sz){{
    vec2 g=pix-cen;
    vec2 hs=sz*0.5;
    float inside=-sdf(g,hs,RAD)/max(min(sz.x,sz.y),1.0);
    float mask=smoothstep(-0.005,0.008,inside);
    if(mask<=0.0) return;
    float cl=length(g);
    vec2 n=cl>0.0001?g/cl:vec2(0);
    float df=1.0-clamp(inside/DIST_D,0.0,1.0);
    float dis=1.0-sqrt(max(1.0-df*df,0.0));
    vec2 c=pix-dis*n*hs*DIST_S;
    float rim=1.0-smoothstep(0.0,0.030,inside);
    vec2 sh=n*rim*CA;
    vec3 ref=vec3(smp(c-sh).r,smp(c).g,smp(c+sh).b);
    vec3 b=ref*0.42
        +smp(c+vec2(BL,0))*0.11+smp(c-vec2(BL,0))*0.11
        +smp(c+vec2(0,BL))*0.11+smp(c-vec2(0,BL))*0.11
        +smp(c+vec2(BL,BL))*0.07+smp(c-vec2(BL,BL))*0.07;
    float tl=1.0-smoothstep(-hs.y,-hs.y*0.10,g.y);
    float dia=1.0-smoothstep(-0.6,0.3,g.x/hs.x+g.y/hs.y);
    float hl=clamp(rim*EHL+tl*dia*0.08,0.0,0.22);
    float lm=dot(b,vec3(0.299,0.587,0.114));
    vec3 fr=mix(mix(b,vec3(lm),FROST),vec3(1),VEIL);
    vec3 gc=mix(fr,vec3(1),hl)*TINT;
    gc=mix(gc,vec3(0.62,0.58,0.92),0.025);
    float pixLum=dot(col.rgb,vec3(0.299,0.587,0.114));
    float pixMax=max(max(col.r,col.g),col.b);
    float pixMin=min(min(col.r,col.g),col.b);
    float pixSat=(pixMax-pixMin)/max(pixMax,0.001);
    /* Two preservation tracks: bright-desaturated (text/borders) AND
       highly-saturated (state-tinted backgrounds like danger / warn). */
    float textLum=smoothstep(0.50,0.80,pixLum);
    float textDesat=1.0-smoothstep(0.30,0.60,pixSat);
    float stateColor=smoothstep(0.55,0.85,pixSat);
    float textiness=max(textLum*textDesat, stateColor*0.85);
    vec3 glassed=mix(col.rgb,gc,mask);
    vec3 brightened=mix(col.rgb,vec3(1.0),0.40);
    col.rgb=mix(glassed,brightened,mask*textiness);
}}

void main(){{
    vec2 pix=v_texcoord*SCR;
    vec4 col=texture(tex,v_texcoord);
"""

SHADER_TAIL = """
    vec2 corner=min(pix,SCR-pix);
    if(corner.x<SCRNR&&corner.y<SCRNR){
        vec2 d=vec2(SCRNR)-corner;
        float aa=smoothstep(SCRNR-1.0,SCRNR+0.5,length(d));
        col.rgb=mix(col.rgb,vec3(0),aa);
        col.a=mix(col.a,1.0,aa);
    }
    fragColor=col;
}
"""


def build_shader(sw_phys: int, sh: int, scale: float, sw_logical: int,
                 bubbles: list[tuple[int, int]]) -> str:
    bh = BAR_HEIGHT * scale
    cy = (BAR_TOP * scale) + bh * 0.5
    head = SHADER_HEAD.format(
        sw=sw_phys, sh=sh, rad=BAR_RADIUS * scale, bl=max(1.4, 1.15 * scale)
    )
    body = []
    for left, width in bubbles:
        cx = (left + width / 2.0) * scale
        bw = width * scale
        body.append(f"    apply_glass(col, pix, vec2({cx:.1f},{cy:.1f}), vec2({bw:.1f},{bh:.1f}));")
    return head + "\n".join(body) + SHADER_TAIL


def write_shader_for_current_monitor() -> None:
    sw_phys, sh, scale, _ = focused_monitor()
    sw_logical = int(round(sw_phys / scale))
    layout = load_waybar_layout()
    bubbles = bubble_bounds(layout, sw_logical)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SHADER_FILE.write_text(build_shader(sw_phys, sh, scale, sw_logical, bubbles))


def main() -> int:
    write_shader_for_current_monitor()
    lease = glass_shader.acquire(OWNER, SHADER_FILE, PRIORITY)

    stop = False
    def shutdown(*_):
        nonlocal stop
        stop = True
        lease.release()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT,  shutdown)

    try:
        while not stop:
            time.sleep(15)
            if not stop:
                try:
                    lease.acquire()
                except Exception:
                    pass
    finally:
        lease.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
