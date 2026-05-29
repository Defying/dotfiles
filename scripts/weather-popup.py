#!/usr/bin/env python3
"""weather-popup: weather card on the shared glass dropdown module.

Was a bespoke cairo-drawn card; now it's GTK widgets inside GlassPopup so it
shares the one liquid-glass design, Esc, and click-away with every other
dropdown. Data still comes from wttr.in, fetched off-thread on open.
"""

import json
import sys
import threading
import urllib.request
from datetime import datetime

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from glass_popup import GlassPopup  # noqa: E402

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
        diff = (dt - datetime.now().date()).days
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


class Popup(GlassPopup):
    WIDTH = 380
    EXTRA_CSS = b"""
    .bigtemp { font-size: 38px; font-weight: 800; }
    .wsym    { font-size: 30px; }
    .wdesc   { color: rgba(244,247,251,0.80); font-size: 13px; }
    .dlabel  { color: rgba(244,247,251,0.46); font-size: 11px; }
    .dval    { color: #f4f7fb; font-size: 14px; }
    .fday    { color: rgba(244,247,251,0.78); font-size: 11px; font-weight: 700; }
    .fsym    { font-size: 20px; }
    .ftemp   { color: rgba(244,247,251,0.56); font-size: 12px; }
    """

    def __init__(self, name, corner="top-right"):
        self.weather = None
        super().__init__(name, corner=corner)
        self.populate()
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        self.weather = parse_weather(fetch_weather())
        GLib.idle_add(self.populate)

    @staticmethod
    def _styled(text, cls, xalign=0):
        lbl = Gtk.Label(label=text, xalign=xalign)
        lbl.get_style_context().add_class(cls)
        return lbl

    def build(self):
        if self.weather is None:
            self.panel.pack_start(self._styled("fetching weather…", "dim"), False, False, 0)
            return
        w = self.weather

        self.panel.pack_start(self._styled(w["location"], "sub"), False, False, 0)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.pack_start(self._styled(f"{w['temp_c']}°", "bigtemp"), True, True, 0)
        top.pack_end(self._styled(weather_symbol(w["code"]), "wsym"), False, False, 0)
        self.panel.pack_start(top, False, False, 0)

        desc = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        desc.pack_start(self._styled(w["desc"], "wdesc"), True, True, 0)
        desc.pack_end(self._styled(f"Feels {w['feels_c']}°", "sub", xalign=1), False, False, 0)
        self.panel.pack_start(desc, False, False, 0)

        self.panel.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        details = [("Humidity", f"{w['humidity']}%"), ("Wind", w["wind"]),
                   ("UV Index", w["uv"]), ("Precip", f"{w['precip']}mm")]
        dgrid = Gtk.Grid(column_homogeneous=True, column_spacing=8)
        for i, (label, val) in enumerate(details):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.pack_start(self._styled(label, "dlabel"), False, False, 0)
            col.pack_start(self._styled(val, "dval"), False, False, 0)
            dgrid.attach(col, i, 0, 1, 1)
        self.panel.pack_start(dgrid, False, False, 0)

        forecast = w.get("forecast", [])[:3]
        if forecast:
            self.panel.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)
            fgrid = Gtk.Grid(column_homogeneous=True, column_spacing=8, row_spacing=2)
            for i, day in enumerate(forecast):
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                col.set_halign(Gtk.Align.CENTER)
                col.pack_start(self._styled(day_label(day["date"]), "fday", xalign=0.5), False, False, 0)
                col.pack_start(self._styled(weather_symbol(day["code"]), "fsym", xalign=0.5), False, False, 0)
                col.pack_start(self._styled(f"{day['max_c']}° / {day['min_c']}°", "ftemp", xalign=0.5), False, False, 0)
                fgrid.attach(col, i, 0, 1, 1)
            self.panel.pack_start(fgrid, False, False, 0)


def main():
    return GlassPopup.launch("weather", Popup, corner="top-right")


if __name__ == "__main__":
    sys.exit(main())
