#!/usr/bin/env python3
"""weather-popup: layer-shell weather card for Hyprland.

Hyprland's `layerrule = blur, namespace ^weather-popup$` paints the blur
behind us; we draw a translucent rounded fill + content on top via cairo.
No screen-shader, no per-frame GLSL — cheap enough for CPU rendering.
"""

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
WHITE_DIM = (1, 1, 1, 0.70)
WHITE_SUB = (1, 1, 1, 0.46)
PAD = 20


class WeatherWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="weather-popup")
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

        # Translucent rounded fill + top sheen — Hyprland's layerrule blur
        # behind the window provides the frosted-glass background.
        rrect(cr, 0, 0, w, h, POPUP_R)
        cr.set_source_rgba(0.04, 0.06, 0.10, 0.48)
        cr.fill_preserve()
        g = cairo.LinearGradient(0, 0, 0, h)
        g.add_color_stop_rgba(0.0, 1, 1, 1, 0.22)
        g.add_color_stop_rgba(0.5, 1, 1, 1, 0.04)
        g.add_color_stop_rgba(1.0, 0, 0, 0, 0.12)
        cr.set_source(g)
        cr.fill()

        cr.set_source_rgba(1, 1, 1, 0.28)
        cr.set_line_width(1.0)
        rrect(cr, 0.5, 0.5, w - 1, h - 1, POPUP_R)
        cr.stroke()

        y = PAD
        if self.weather is None:
            text(cr, "fetching weather…", "SF Pro Text 13", WHITE_SUB, PAD, y + 24)
            return

        wth = self.weather

        text(cr, wth["location"], "SF Pro Text 12", WHITE_SUB,
             PAD, y, max_w=w - PAD * 2 - 50)
        y += 20

        _, th = text(cr, f"{wth['temp_c']}°", "SF Pro Display 44 Bold", WHITE, PAD, y)
        text(cr, weather_symbol(wth["code"]), "Noto Color Emoji 34",
             (1, 1, 1, 1), w - PAD - 46, y + 4)
        y += max(th, 42) + 2

        feels_str = f"Feels {wth['feels_c']}°"
        text(cr, wth["desc"], "SF Pro Text 13", WHITE_DIM, PAD, y)
        text(cr, feels_str, "SF Pro Text 13", WHITE_SUB,
             w - PAD - 80, y, max_w=80)
        y += 20

        y += 14
        sep(cr, PAD, y, w - PAD * 2)
        y += 14

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
            text(cr, val,   "SF Pro Text 14", WHITE,       dx, y + 16, max_w=col_w)
        y += 42

        y += 12
        sep(cr, PAD, y, w - PAD * 2)
        y += 14

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

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
        return False

    def _on_click(self, *_):
        Gtk.main_quit()


def main():
    def _sig(*_):
        GLib.idle_add(Gtk.main_quit)

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)

    WeatherWindow()
    Gtk.main()


if __name__ == "__main__":
    main()
