#!/usr/bin/env python3
"""liquid-launcher: liquid glass app launcher for Hyprland."""

import json
import math
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import cairo
import glass_shader
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell  # noqa: E402

LAUNCHER_W = 580
LAUNCHER_R = 24
MAX_ROWS   = 8
HEADER_H   = 64
ROW_H      = 50
HEIGHT     = HEADER_H + 1 + MAX_ROWS * ROW_H + 12   # sep + rows + bottom pad

DOTFILES_DIR   = Path.home() / "dotfiles"
ROUNDED_SHADER = DOTFILES_DIR / "config/hypr/shaders/rounded-corners.frag"
RUNTIME_DIR    = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/llauncher-{os.getuid()}"))
SHADER_FILE    = RUNTIME_DIR / "liquid-launcher.frag"


# ── App loading ───────────────────────────────────────────────────────────────

def load_apps():
    dirs = [
        Path("/usr/share/applications"),
        Path.home() / ".local/share/applications",
        Path("/usr/local/share/applications"),
    ]
    seen, apps = set(), []
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.desktop")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            kv, in_entry = {}, False
            for line in text.splitlines():
                line = line.strip()
                if line == "[Desktop Entry]":
                    in_entry = True
                elif line.startswith("[") and in_entry:
                    break
                elif in_entry and "=" in line:
                    k, _, v = line.partition("=")
                    kv[k.strip()] = v.strip()
            if not in_entry:
                continue
            if kv.get("NoDisplay", "").lower() == "true":
                continue
            if kv.get("Type") != "Application":
                continue
            name = kv.get("Name", "").strip()
            exec_ = kv.get("Exec", "").strip()
            if not name or not exec_ or name in seen:
                continue
            seen.add(name)
            apps.append({
                "name":    name,
                "exec":    exec_,
                "icon":    kv.get("Icon", ""),
                "generic": kv.get("GenericName", ""),
                "keywords": kv.get("Keywords", "").lower(),
            })
    return sorted(apps, key=lambda a: a["name"].lower())


def clean_exec(s):
    return re.sub(r"\s*%[a-zA-Z]\s*", " ", s).strip()


def score_app(app, q):
    if not q:
        return 1
    name = app["name"].lower()
    if name.startswith(q):
        return 4
    if q in name:
        return 3
    gen = (app.get("generic") or "").lower()
    if gen.startswith(q) or q in gen:
        return 2
    if q in app.get("keywords", ""):
        return 1
    return 0


def filter_apps(apps, query):
    q = query.lower().strip()
    if not q:
        return apps[:MAX_ROWS]
    scored = [(score_app(a, q), a) for a in apps]
    scored = [(s, a) for s, a in scored if s > 0]
    scored.sort(key=lambda x: (-x[0], x[1]["name"].lower()))
    return [a for _, a in scored[:MAX_ROWS]]


# ── Hyprland ──────────────────────────────────────────────────────────────────

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


# ── Shader ────────────────────────────────────────────────────────────────────

def build_shader(sw, sh, scale, reserved_top):
    lw = LAUNCHER_W * scale
    lh = HEIGHT * scale
    cx = sw * 0.5
    cy = sh * 0.5
    r  = LAUNCHER_R * scale
    bl = max(1.4, 1.15 * scale)

    return f"""#version 300 es
precision highp float;
in vec2 v_texcoord;
out vec4 fragColor;
uniform sampler2D tex;

const vec2  SCR    = vec2({sw:.1f},{sh:.1f});
const float SCRNR  = 28.0;
const vec2  CEN    = vec2({cx:.1f},{cy:.1f});
const vec2  SZ     = vec2({lw:.1f},{lh:.1f});
const float RAD    = {r:.1f};
const float BL     = {bl:.2f};
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
        col.rgb=mix(col.rgb,gc,mask);
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
        if SHADER_FILE.exists() and SHADER_FILE.read_text() == src:
            return True
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
        self.lease = glass_shader.acquire("liquid-launcher", SHADER_FILE, 80)
        return True

    def restore(self):
        if self.lease is None:
            return
        self.lease.release()
        self.lease = None


# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = b"""
* { transition: none; }
window { background: transparent; }
#search-entry {
    background: transparent;
    border: none;
    box-shadow: none;
    color: rgba(244,247,251,0.96);
    font-family: "SF Pro Text", sans-serif;
    font-size: 17px;
    padding: 0 4px;
    caret-color: rgba(192,132,245,0.9);
    min-height: 0;
}
#search-entry:focus { outline: none; box-shadow: none; }
.prompt { color: rgba(192,132,245,0.85); font-size: 20px; }
#result-list { background: transparent; border: none; }
#result-list row {
    background: transparent;
    border-radius: 12px;
    padding: 0 4px;
    min-height: 0;
}
#result-list row:hover { background: rgba(255,255,255,0.08); outline: none; }
#result-list row:selected { background: rgba(255,255,255,0.13); outline: none; }
.app-name {
    color: rgba(244,247,251,0.96);
    font-family: "SF Pro Text", sans-serif;
    font-size: 14px;
    font-weight: 500;
}
.app-sub {
    color: rgba(244,247,251,0.48);
    font-family: "SF Pro Text", sans-serif;
    font-size: 12px;
}
scrolledwindow, viewport { background: transparent; }
"""


# ── Cairo helpers ─────────────────────────────────────────────────────────────

def rrect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x+w-r, y+r,   r, -math.pi/2, 0)
    cr.arc(x+w-r, y+h-r, r,  0,          math.pi/2)
    cr.arc(x+r,   y+h-r, r,  math.pi/2,  math.pi)
    cr.arc(x+r,   y+r,   r,  math.pi,    math.pi*1.5)
    cr.close_path()


# ── Window ────────────────────────────────────────────────────────────────────

class LauncherWindow(Gtk.Window):
    def __init__(self, apps, shader):
        super().__init__(title="liquid-launcher")
        self.apps = apps
        self.shader = shader
        self.results = apps[:MAX_ROWS]
        self._icon_theme = Gtk.IconTheme.get_default()

        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_resizable(False)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "launcher")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_exclusive_zone(self, -1)
        # No anchors = centered

        self.set_default_size(LAUNCHER_W, HEIGHT)

        # Background glass panel (Cairo)
        self.bg = Gtk.DrawingArea()
        self.bg.connect("draw", self._draw_bg)

        # Search bar
        prompt = Gtk.Label(label="›")
        prompt.get_style_context().add_class("prompt")

        self.entry = Gtk.Entry()
        self.entry.set_name("search-entry")
        self.entry.set_placeholder_text("search apps…")
        self.entry.connect("changed", self._on_changed)
        self.entry.connect("activate", self._on_activate)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        search_row.set_size_request(-1, HEADER_H)
        search_row.pack_start(prompt, False, False, 20)
        search_row.pack_start(self.entry, True, True, 0)
        search_row.pack_end(Gtk.Label(), False, False, 16)

        # Separator
        sep = Gtk.DrawingArea()
        sep.set_size_request(-1, 1)
        sep.connect("draw", lambda w, cr: (
            cr.set_source_rgba(1, 1, 1, 0.10),
            cr.rectangle(16, 0, w.get_allocated_width() - 32, 1),
            cr.fill(),
        ))

        # Results
        self.listbox = Gtk.ListBox()
        self.listbox.set_name("result-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", lambda _lb, row: self._launch(row.app))

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        scroll.set_size_request(-1, MAX_ROWS * ROW_H)
        scroll.add(self.listbox)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.pack_start(search_row, False, False, 0)
        content.pack_start(sep,        False, False, 0)
        content.pack_start(scroll,     False, False, 0)

        overlay = Gtk.Overlay()
        overlay.add(self.bg)
        overlay.add_overlay(content)
        self.add(overlay)

        self.connect("key-press-event", self._on_key)
        self.connect("destroy", self._on_destroy)

        self._populate()
        self.show_all()
        self.entry.grab_focus()
        self._select_first()

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw_bg(self, widget, cr):
        # The shader provides the entire glass background.
        # We only clear to transparent here; the border is a secondary stroke.
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        # Subtle border stroke to supplement the shader rim highlight.
        cr.set_source_rgba(1, 1, 1, 0.18)
        cr.set_line_width(1.0)
        rrect(cr, 0.5, 0.5, w - 1, h - 1, LAUNCHER_R)
        cr.stroke()

    # ── Results ───────────────────────────────────────────────────────────────

    def _populate(self):
        for child in self.listbox.get_children():
            self.listbox.remove(child)

        for app in self.results:
            row = Gtk.ListBoxRow()
            row.app = app

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.set_size_request(-1, ROW_H)
            box.set_margin_start(16)
            box.set_margin_end(16)

            # Icon
            icon_w = self._load_icon(app.get("icon", ""))
            box.pack_start(icon_w, False, False, 0)

            # Labels
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            name_lbl = Gtk.Label(label=app["name"])
            name_lbl.set_halign(Gtk.Align.START)
            name_lbl.get_style_context().add_class("app-name")
            labels.pack_start(name_lbl, False, False, 0)
            generic = app.get("generic", "")
            if generic and generic != app["name"]:
                sub = Gtk.Label(label=generic)
                sub.set_halign(Gtk.Align.START)
                sub.get_style_context().add_class("app-sub")
                labels.pack_start(sub, False, False, 0)
            box.pack_start(labels, True, True, 0)

            row.add(box)
            self.listbox.add(row)

        self.listbox.show_all()

    def _load_icon(self, icon_name):
        if icon_name:
            try:
                if os.path.isabs(icon_name) and os.path.exists(icon_name):
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_name, 28, 28)
                    return Gtk.Image.new_from_pixbuf(pb)
                pb = self._icon_theme.load_icon(icon_name, 28, 0)
                return Gtk.Image.new_from_pixbuf(pb)
            except Exception:
                pass
        fallback = Gtk.Label(label=" ")
        fallback.set_size_request(28, 28)
        return fallback

    def _select_first(self):
        row = self.listbox.get_row_at_index(0)
        if row:
            self.listbox.select_row(row)

    # ── Events ────────────────────────────────────────────────────────────────

    def _on_changed(self, entry):
        self.results = filter_apps(self.apps, entry.get_text())
        self._populate()
        self._select_first()

    def _on_activate(self, _entry):
        row = self.listbox.get_selected_row()
        if row:
            self._launch(row.app)

    def _on_key(self, _widget, event):
        k = event.keyval
        if k == Gdk.KEY_Escape:
            self._quit()
            return True
        if k in (Gdk.KEY_Down, Gdk.KEY_Tab):
            cur = self.listbox.get_selected_row()
            nxt = self.listbox.get_row_at_index((cur.get_index() if cur else -1) + 1)
            if nxt:
                self.listbox.select_row(nxt)
                nxt.grab_focus()
                self.entry.grab_focus()
            return True
        if k in (Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab):
            cur = self.listbox.get_selected_row()
            idx = cur.get_index() if cur else 1
            prev = self.listbox.get_row_at_index(max(0, idx - 1))
            if prev:
                self.listbox.select_row(prev)
            return True
        return False

    def _launch(self, app):
        cmd = clean_exec(app["exec"])
        try:
            GLib.spawn_command_line_async(cmd)
        except Exception as e:
            print(f"liquid-launcher: launch failed: {e}", file=sys.stderr)
        self._quit()

    def _quit(self):
        self.shader.restore()
        Gtk.main_quit()

    def _on_destroy(self, _widget):
        self.shader.restore()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    apps = load_apps()
    shader = ShaderController()
    if not shader.enable():
        print("liquid-launcher: failed to enable Hyprland screen shader", file=sys.stderr)

    def _sig(*_):
        def restore_and_quit():
            shader.restore()
            Gtk.main_quit()
            return False

        GLib.idle_add(restore_and_quit)

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    try:
        win = LauncherWindow(apps, shader)
        Gtk.main()
    finally:
        shader.restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
