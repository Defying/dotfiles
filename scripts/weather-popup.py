#!/usr/bin/env python3
"""weather-popup: liquid glass weather card for Hyprland."""

import json
import math
import os
import signal
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

import cairo
import glass_shader
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell, Pango, PangoCairo

POPUP_W      = 400
POPUP_H      = 292
POPUP_R      = 22
TOP_MARGIN   = 12   # logical px gap below waybar
RIGHT_MARGIN = 28   # matches waybar right margin

DOTFILES_DIR   = Path.home() / "dotfiles"
ROUNDED_SHADER = DOTFILES_DIR / "config/hypr/shaders/rounded-corners.frag"
RUNTIME_DIR    = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/wpopup-{os.getuid()}"))
SHADER_FILE    = RUNTIME_DIR / "weather-popup.frag"

WTTR_URL = "https://wttr.in/?format=j1"


# ── Weather helpers ───────────────────────────────────────────────────────────

def weather_symbol(code):
    c = int(code)
    if c == 113: return "☀"
    if c == 116: return "⛅"
    if c in (119, 122): return "☁"
    if c in (143, 248, 260): return "🌫"
    if c in (200, 386, 389, 392, 395): return "⛈"
    if c >= 320: return "❄"
    if c >= 263: return "🌧"
    if c >= 176: return "🌦"
    return "⛅"


def day_label(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now().date()
        diff = (dt - today).days
        if diff == 0: return "Today"
        if diff == 1: return "Tomorrow"
        return dt.strftime("%A")[:3]
    except Exception:
        return "---"


def fetch_weather():
    try:
        req = urllib.request.Request(WTTR_URL, headers={"User-Agent": "curl/7.0"})
        with urllib.request.urlopen(req, timeout=7) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def parse_weather(data):
    if not data:
        return None
    try:
        cur  = data["current_condition"][0]
        area = data.get("nearest_area", [{}])[0]
        city    = area.get("areaName",  [{}])[0].get("value", "")
        country = area.get("country",   [{}])[0].get("value", "")
        country = country.replace("United States of America", "US")

        forecast = []
        for day in data.get("weather", [])[:3]:
            hourly = day.get("hourly", [])
            mid = next(
                (h for h in hourly if int(h.get("time", "0")) >= 1200),
                hourly[len(hourly) // 2] if hourly else {},
            )
            forecast.append({
                "date":  day["date"],
                "max_c": day["maxtempC"],
                "min_c": day["mintempC"],
                "code":  mid.get("weatherCode", "116"),
            })

        return {
            "location": f"{city}, {country}" if country else city,
            "temp_c":   cur["temp_C"],
            "feels_c":  cur["FeelsLikeC"],
            "humidity": cur["humidity"],
            "wind":     f"{cur['windspeedKmph']} {cur['winddir16Point']}",
            "desc":     cur["weatherDesc"][0]["value"],
            "code":     cur["weatherCode"],
            "uv":       cur["uvIndex"],
            "precip":   cur["precipMM"],
            "forecast": forecast,
        }
    except Exception:
        return None


# ── Hyprland / shader ─────────────────────────────────────────────────────────

def hyprctl(args, *, capture=False):
    try:
        if capture:
            return subprocess.check_output(["hyprctl", *args], text=True,
                                           stderr=subprocess.DEVNULL)
        completed = subprocess.run(
            ["hyprctl", *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0
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


def current_screen_shader():
    try:
        opt = json.loads(hyprctl(["getoption", "decoration:screen_shader", "-j"], capture=True))
        s = opt.get("str") if opt.get("set") else ""
        if not s or s == str(SHADER_FILE):
            return str(ROUNDED_SHADER)
        return s
    except Exception:
        return str(ROUNDED_SHADER)


def build_shader(sw, sh, scale, reserved_top):
    pw = POPUP_W * scale
    ph = POPUP_H * scale
    cx = sw - (RIGHT_MARGIN + POPUP_W / 2) * scale
    cy = (reserved_top + TOP_MARGIN + POPUP_H / 2) * scale
    r  = POPUP_R * scale
    bl = max(1.4, 1.15 * scale)

    return f"""#version 300 es
precision highp float;
in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;

const vec2  SCR   = vec2({sw:.1f},{sh:.1f});
const float SCRNR = 28.0;
const vec2  CEN   = vec2({cx:.1f},{cy:.1f});
const vec2  SZ    = vec2({pw:.1f},{ph:.1f});
const float RAD   = {r:.1f};
const float BL    = {bl:.2f};
const float DIST_D = 0.20;
const float DIST_S = 0.13;
const float CA     = 2.4;
const float TINT   = 0.93;
const float FROST  = 0.25;
const float VEIL   = 0.13;
const float EHL    = 0.22;

float sdf(vec2 p,vec2 hs,float r){{
    vec2 d=abs(p)-hs+vec2(r);
    return min(max(d.x,d.y),0.0)+length(max(d,0.0))-r;
}}
vec3 smp(vec2 c){{return texture(tex,clamp(c/SCR,vec2(0),vec2(1))).rgb;}}

void main(){{
    vec2 pix=v_texcoord*SCR;
    vec4 col=texture(tex,v_texcoord);
    vec2 g=pix-CEN;
    vec2 hs=SZ*0.5;
    float inside=-sdf(g,hs,RAD)/max(min(SZ.x,SZ.y),1.0);
    float mask=smoothstep(-0.005,0.008,inside);
    if(mask>0.0){{
        float cl=length(g);
        vec2 n=cl>0.0001?g/cl:vec2(0);
        float df=1.0-clamp(inside/DIST_D,0.0,1.0);
        float dis=1.0-sqrt(max(1.0-df*df,0.0));
        vec2 c=pix-dis*n*hs*DIST_S;
        float rim=1.0-smoothstep(0.0,0.030,inside);
        vec2 sh=n*rim*CA;
        vec3 ref=vec3(smp(c-sh).r,smp(c).g,smp(c+sh).b);
        vec3 b=ref*0.38
            +smp(c+vec2(BL,0))*0.12+smp(c-vec2(BL,0))*0.12
            +smp(c+vec2(0,BL))*0.12+smp(c-vec2(0,BL))*0.12
            +smp(c+vec2(BL,BL))*0.07+smp(c-vec2(BL,BL))*0.07;
        float tl=1.0-smoothstep(-hs.y,-hs.y*0.10,g.y);
        float dia=1.0-smoothstep(-0.6,0.3,g.x/hs.x+g.y/hs.y);
        float hl=clamp(rim*EHL+tl*dia*0.08,0.0,0.28);
        float lm=dot(b,vec3(0.299,0.587,0.114));
        vec3 fr=mix(mix(b,vec3(lm),FROST),vec3(1),VEIL);
        vec3 gc=mix(fr,vec3(1),hl)*TINT;
        gc=mix(gc,vec3(0.75,0.52,0.95),0.03);
        /* Preserve bright, desaturated pixels — GTK-drawn text/icons sit
           on top of the glass instead of being overpainted by it. */
        float pixLum=dot(col.rgb,vec3(0.299,0.587,0.114));
        float pixMax=max(max(col.r,col.g),col.b);
        float pixMin=min(min(col.r,col.g),col.b);
        float pixSat=(pixMax-pixMin)/max(pixMax,0.001);
        float textLum=smoothstep(0.55,0.85,pixLum);
        float textDesat=1.0-smoothstep(0.30,0.60,pixSat);
        float textiness=textLum*textDesat;
        col.rgb=mix(col.rgb,gc,mask*(1.0-textiness));
    }}
    vec2 corner=min(pix,SCR-pix);
    if(corner.x<SCRNR&&corner.y<SCRNR){{
        vec2 d=vec2(SCRNR)-corner;
        float aa=smoothstep(SCRNR-1.0,SCRNR+0.5,length(d));
        col.rgb=mix(col.rgb,vec3(0),aa);
        col.a=mix(col.a,1.0,aa);
    }}
    fragColor=col;
}}
"""


def write_shader():
    sw, sh, scale, rt = focused_monitor()
    src = build_shader(sw, sh, scale, rt)
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        SHADER_FILE.write_text(src)
        return True
    except OSError:
        return False


class ShaderController:
    def __init__(self):
        self.lease = None

    def enable(self):
        if not write_shader():
            return False
        self.lease = glass_shader.acquire("weather-popup", SHADER_FILE, 70)
        return True

    def restore(self):
        if self.lease is None:
            return
        self.lease.release()
        self.lease = None


# ── Cairo helpers ─────────────────────────────────────────────────────────────

def rrect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x+w-r, y+r,   r, -math.pi/2, 0)
    cr.arc(x+w-r, y+h-r, r,  0,          math.pi/2)
    cr.arc(x+r,   y+h-r, r,  math.pi/2,  math.pi)
    cr.arc(x+r,   y+r,   r,  math.pi,    math.pi*1.5)
    cr.close_path()


def text(cr, s, font, rgba, x, y, max_w=None, align=Pango.Alignment.LEFT):
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription(font))
    layout.set_text(s, -1)
    layout.set_alignment(align)
    if max_w:
        layout.set_width(int(max_w * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
    cr.set_source_rgba(*rgba)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)
    return layout.get_pixel_size()


def sep(cr, x, y, w):
    cr.set_source_rgba(1, 1, 1, 0.12)
    cr.set_line_width(1)
    cr.move_to(x, y + 0.5)
    cr.line_to(x + w, y + 0.5)
    cr.stroke()


# ── Window ────────────────────────────────────────────────────────────────────

WHITE     = (1, 1, 1, 0.96)
WHITE_DIM = (1, 1, 1, 0.65)
WHITE_SUB = (1, 1, 1, 0.42)
PAD = 20


class WeatherWindow(Gtk.Window):
    def __init__(self, shader):
        super().__init__(title="weather-popup")
        self.shader = shader
        self.weather = None

        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_resizable(False)
        screen = self.get_screen()
        if (visual := screen.get_rgba_visual()):
            self.set_visual(visual)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "weather-popup")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP,   True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP,   TOP_MARGIN)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, RIGHT_MARGIN)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        GtkLayerShell.set_exclusive_zone(self, 0)

        self.set_default_size(POPUP_W, POPUP_H)

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(POPUP_W, POPUP_H)
        self.area.connect("draw", self._draw)
        self.add(self.area)

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        self.connect("key-press-event",    self._on_key)
        self.connect("button-press-event", self._on_click)
        self.connect("destroy",            self._on_destroy)

        threading.Thread(target=self._fetch, daemon=True).start()
        self.show_all()

    def _fetch(self):
        self.weather = parse_weather(fetch_weather())
        GLib.idle_add(self.area.queue_draw)

    def _draw(self, widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()

        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Subtle border — the screen shader provides the glass fill behind us.
        cr.set_source_rgba(1, 1, 1, 0.22)
        cr.set_line_width(1.0)
        rrect(cr, 0.5, 0.5, w - 1, h - 1, POPUP_R)
        cr.stroke()

        y = PAD

        if self.weather is None:
            text(cr, "fetching weather…", "SF Pro Text 13", WHITE_SUB, PAD, y + 24)
            return

        wth = self.weather

        # ── Location ──────────────────────────────────────────────────────────
        text(cr, wth["location"], "SF Pro Text 12", WHITE_SUB,
             PAD, y, max_w=w - PAD * 2 - 50)
        y += 20

        # ── Current: big temp + icon ──────────────────────────────────────────
        _, th = text(cr, f"{wth['temp_c']}°", "SF Pro Display 44 Bold", WHITE, PAD, y)
        text(cr, weather_symbol(wth["code"]), "Noto Color Emoji 34",
             (1, 1, 1, 1), w - PAD - 46, y + 4)
        y += max(th, 42) + 2

        # Condition + feels like on one line
        feels_str = f"Feels {wth['feels_c']}°"
        text(cr, wth["desc"], "SF Pro Text 13", WHITE_DIM, PAD, y)
        tw, _ = text(cr, feels_str, "SF Pro Text 13", WHITE_SUB,
                     w - PAD - 80, y, max_w=80)
        y += 20

        # ── Separator ─────────────────────────────────────────────────────────
        y += 14
        sep(cr, PAD, y, w - PAD * 2)
        y += 14

        # ── Details: humidity / wind / UV / precip ────────────────────────────
        details = [
            ("Humidity",  f"{wth['humidity']}%"),
            ("Wind",      wth["wind"]),
            ("UV Index",  wth["uv"]),
            ("Precip",    f"{wth['precip']}mm"),
        ]
        col_w = (w - PAD * 2) / len(details)
        for i, (label, val) in enumerate(details):
            dx = PAD + i * col_w
            text(cr, label, "SF Pro Text 11", WHITE_SUB,   dx, y,      max_w=col_w)
            text(cr, val,   "SF Pro Text 14", WHITE,        dx, y + 16, max_w=col_w)
        y += 42

        # ── Separator ─────────────────────────────────────────────────────────
        y += 12
        sep(cr, PAD, y, w - PAD * 2)
        y += 14

        # ── 3-day forecast ────────────────────────────────────────────────────
        forecast = wth.get("forecast", [])[:3]
        if forecast:
            day_w = (w - PAD * 2) / 3
            for i, day in enumerate(forecast):
                dx = PAD + i * day_w
                text(cr, day_label(day["date"]), "SF Pro Text 11 SemiBold",
                     WHITE_DIM, dx, y, max_w=day_w)
                text(cr, weather_symbol(day["code"]), "Noto Color Emoji 20",
                     (1, 1, 1, 1), dx, y + 17, max_w=day_w)
                text(cr, f"{day['max_c']}° / {day['min_c']}°", "SF Pro Text 12",
                     WHITE_SUB, dx, y + 42, max_w=day_w)

    # ── Events ────────────────────────────────────────────────────────────────

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self._quit()
        return False

    def _on_click(self, *_):
        self._quit()

    def _quit(self):
        self.shader.restore()
        Gtk.main_quit()

    def _on_destroy(self, _):
        self.shader.restore()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    shader = ShaderController()
    if not shader.enable():
        print("weather-popup: failed to enable Hyprland screen shader", file=sys.stderr)

    def _sig(*_):
        def restore_and_quit():
            shader.restore()
            Gtk.main_quit()
            return False

        GLib.idle_add(restore_and_quit)

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)

    try:
        WeatherWindow(shader)
        Gtk.main()
    finally:
        shader.restore()


if __name__ == "__main__":
    main()
