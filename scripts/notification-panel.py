#!/usr/bin/env python3
"""Layer-shell notification panel backed by mako."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

sys.path.insert(0, str(Path(__file__).resolve().parent))
import glass_shader  # noqa: E402

PID_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "notification-panel.pid"
ASSETS = Path.home() / "dotfiles" / "assets"
ICON_MAP = {
    "ai usage": ASSETS / "openai.png",
}

POPUP_W      = 380
POPUP_H      = 600
POPUP_R      = 24
TOP_MARGIN   = 62
RIGHT_MARGIN = 28
RUNTIME_DIR  = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/np-{os.getuid()}"))
SHADER_FILE  = RUNTIME_DIR / "notification-panel.frag"


def run(*args, capture=False, timeout=2.0):
    try:
        if capture:
            return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except Exception:
        return "" if capture else None
    return ""


def makoctl_json(command):
    out = run("makoctl", command, "-j", capture=True)
    if not out:
        return []
    try:
        return json.loads(out)
    except Exception:
        return []


def dnd_active():
    modes = run("makoctl", "mode", capture=True).splitlines()
    return any(line.strip().lstrip("* ").strip() == "do-not-disturb" for line in modes)


def set_dnd(on):
    if on:
        run("makoctl", "mode", "-a", "do-not-disturb")
    else:
        run("makoctl", "mode", "-r", "do-not-disturb")


def pick_icon(app_name, app_icon):
    if app_icon and Path(app_icon).expanduser().exists():
        return Path(app_icon).expanduser()
    key = (app_name or "").strip().lower()
    if key in ICON_MAP and ICON_MAP[key].exists():
        return ICON_MAP[key]
    if "claude" in key and (ASSETS / "claude.png").exists():
        return ASSETS / "claude.png"
    if "openai" in key or "codex" in key or "chatgpt" in key:
        png = ASSETS / "openai.png"
        if png.exists():
            return png
    return None


# ── Glass shader: provides the popup background (per UI rule). ────────────────

def hyprctl(args, *, capture=False):
    cmd = ["hyprctl", *args]
    try:
        if capture:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        completed = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, check=False)
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
const float DIST_D = 0.18;
const float DIST_S = 0.10;
const float CA     = 2.0;
const float TINT   = 0.93;
const float FROST  = 0.28;
const float VEIL   = 0.16;
const float EHL    = 0.20;

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
        float hl=clamp(rim*EHL+tl*dia*0.08,0.0,0.24);
        float lm=dot(b,vec3(0.299,0.587,0.114));
        vec3 fr=mix(mix(b,vec3(lm),FROST),vec3(1),VEIL);
        vec3 gc=mix(fr,vec3(1),hl)*TINT;
        gc=mix(gc,vec3(0.55,0.62,0.95),0.04);
        /* Preserve GTK-drawn text/icons so the panel content sits on top of
           the glass instead of being painted over, and pull bright pixels
           toward pure white so text stays crisp against the glass. */
        float pixLum=dot(col.rgb,vec3(0.299,0.587,0.114));
        float pixMax=max(max(col.r,col.g),col.b);
        float pixMin=min(min(col.r,col.g),col.b);
        float pixSat=(pixMax-pixMin)/max(pixMax,0.001);
        float textLum=smoothstep(0.50,0.80,pixLum);
        float textDesat=1.0-smoothstep(0.30,0.60,pixSat);
        float textiness=textLum*textDesat;
        vec3 glassed=mix(col.rgb,gc,mask);
        vec3 brightened=mix(col.rgb,vec3(1.0),0.45);
        col.rgb=mix(glassed,brightened,mask*textiness);
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
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        SHADER_FILE.write_text(build_shader(sw, sh, scale, rt))
        return True
    except OSError:
        return False


class ShaderController:
    def __init__(self):
        self.lease = None

    def enable(self):
        if not write_shader():
            return False
        self.lease = glass_shader.acquire("notification-panel", SHADER_FILE, 75)
        return True

    def restore(self):
        if self.lease is None:
            return
        self.lease.release()
        self.lease = None


CSS = b"""
#notification-panel { background: transparent; }
.panel {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.30);
  border-radius: 24px;
  padding: 16px;
}
.title { color: #f4f7fb; font-weight: 800; font-size: 15px; text-shadow: 0 1px 1px rgba(0,0,0,0.55); }
.section { color: rgba(244, 247, 251, 0.78); font-weight: 700; font-size: 11px; text-shadow: 0 1px 1px rgba(0,0,0,0.45); margin-top: 4px; }
.empty { color: rgba(244, 247, 251, 0.55); font-size: 12px; padding: 18px 8px; }
.notif {
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 14px;
  padding: 10px 12px;
}
.notif.history { background: rgba(255, 255, 255, 0.04); }
.notif .app { color: rgba(244, 247, 251, 0.62); font-size: 10px; font-weight: 700; letter-spacing: 0.04em; }
.notif .summary { color: #f4f7fb; font-size: 13px; font-weight: 700; }
.notif .body { color: rgba(244, 247, 251, 0.86); font-size: 12px; }
.notif .urgent .summary { color: #ffd6d6; }
button {
  color: #f4f7fb;
  background:
    linear-gradient(145deg, rgba(255,255,255,0.16), rgba(255,255,255,0.06)),
    rgba(255,255,255,0.05);
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 12px;
  padding: 6px 10px;
  box-shadow: inset 0 1px rgba(255,255,255,0.16);
}
button:hover {
  background:
    linear-gradient(145deg, rgba(255,255,255,0.24), rgba(255,255,255,0.10)),
    rgba(255,255,255,0.08);
}
.icon-btn { padding: 4px 8px; min-width: 28px; }
switch slider { background: #f4f7fb; box-shadow: 0 2px 8px rgba(0,0,0,0.24); }
switch trough { background: rgba(255,255,255,0.14); border: 1px solid rgba(255,255,255,0.16); }
switch:checked trough { background: rgba(192, 132, 245, 0.55); }
separator { background: rgba(255,255,255,0.12); }
scrolledwindow { background: transparent; }
"""


class NotificationPanel(Gtk.Window):
    def __init__(self):
        super().__init__(title="notifications")
        self.set_name("notification-panel")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_size_request(POPUP_W, POPUP_H)
        self.set_app_paintable(True)
        screen = self.get_screen()
        if (visual := screen.get_rgba_visual()):
            self.set_visual(visual)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "notifications")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, TOP_MARGIN)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, RIGHT_MARGIN)

        self.connect("key-press-event", self.on_key)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.get_style_context().add_class("panel")
        self.add(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.pack_start(header, False, False, 0)
        title = Gtk.Label(label="Notifications", xalign=0)
        title.get_style_context().add_class("title")
        header.pack_start(title, True, True, 0)

        dnd_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.pack_end(dnd_row, False, False, 0)
        dnd_row.pack_start(Gtk.Label(label="DND"), False, False, 0)
        self.dnd_switch = Gtk.Switch()
        self.dnd_switch.set_active(dnd_active())
        self.dnd_switch.connect("notify::active", self.on_dnd_toggle)
        dnd_row.pack_start(self.dnd_switch, False, False, 0)

        self.list_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(120)
        scroller.set_max_content_height(520)
        scroller.set_propagate_natural_height(True)
        scroller.add(self.list_container)
        root.pack_start(scroller, True, True, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.pack_start(footer, False, False, 0)
        clear = Gtk.Button(label="Clear all")
        clear.connect("clicked", self.on_clear)
        footer.pack_start(clear, True, True, 0)
        restore = Gtk.Button(label="Restore last")
        restore.connect("clicked", self.on_restore)
        footer.pack_start(restore, True, True, 0)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: Gtk.main_quit())
        footer.pack_start(close, True, True, 0)

        self.refresh()
        GLib.timeout_add_seconds(2, self._tick)

    def _tick(self):
        self.refresh()
        return True

    def refresh(self):
        for child in self.list_container.get_children():
            self.list_container.remove(child)

        active = makoctl_json("list")
        history = makoctl_json("history")
        # mako's `list` shape is [{notifications: [...]}, ...] grouped by mode; flatten.
        flat_active = []
        if isinstance(active, list):
            for entry in active:
                if isinstance(entry, dict) and "notifications" in entry:
                    flat_active.extend(entry.get("notifications") or [])
                else:
                    flat_active.append(entry)

        if flat_active:
            label = Gtk.Label(label=f"ACTIVE  ·  {len(flat_active)}", xalign=0)
            label.get_style_context().add_class("section")
            self.list_container.pack_start(label, False, False, 0)
            for notif in flat_active:
                self.list_container.pack_start(self.make_row(notif, history_row=False), False, False, 0)

        if history:
            label = Gtk.Label(label=f"HISTORY  ·  {len(history)}", xalign=0)
            label.get_style_context().add_class("section")
            self.list_container.pack_start(label, False, False, 0)
            for notif in history[:25]:
                self.list_container.pack_start(self.make_row(notif, history_row=True), False, False, 0)

        if not flat_active and not history:
            empty = Gtk.Label(label="No notifications", xalign=0.5)
            empty.get_style_context().add_class("empty")
            self.list_container.pack_start(empty, False, False, 0)

        self.list_container.show_all()

    def make_row(self, notif, history_row):
        def get(name, default=""):
            value = notif.get(name)
            if isinstance(value, dict):
                return value.get("data", default)
            return value if value is not None else default

        app = str(get("app_name") or "notification")
        summary = str(get("summary") or "")
        body = str(get("body") or "")
        urgency = str(get("urgency") or "normal")
        notif_id = get("id")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        ctx = row.get_style_context()
        ctx.add_class("notif")
        if history_row:
            ctx.add_class("history")
        if urgency == "critical":
            ctx.add_class("urgent")

        icon_path = pick_icon(app, get("app_icon"))
        if icon_path:
            image = Gtk.Image.new_from_file(str(icon_path))
            image.set_pixel_size(28)
            row.pack_start(image, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        row.pack_start(content, True, True, 0)
        app_label = Gtk.Label(label=app.upper(), xalign=0)
        app_label.get_style_context().add_class("app")
        content.pack_start(app_label, False, False, 0)
        if summary:
            sl = Gtk.Label(label=summary, xalign=0)
            sl.set_line_wrap(True)
            sl.set_max_width_chars(40)
            sl.get_style_context().add_class("summary")
            content.pack_start(sl, False, False, 0)
        if body:
            bl = Gtk.Label(label=body, xalign=0)
            bl.set_line_wrap(True)
            bl.set_max_width_chars(46)
            bl.get_style_context().add_class("body")
            content.pack_start(bl, False, False, 0)

        action = Gtk.Button(label="×")
        action.get_style_context().add_class("icon-btn")
        action.set_valign(Gtk.Align.CENTER)
        if history_row:
            action.set_tooltip_text("remove from history")
            action.connect("clicked", lambda *_: self.on_forget(notif_id))
        else:
            action.set_tooltip_text("dismiss")
            action.connect("clicked", lambda *_: self.on_dismiss(notif_id))
        row.pack_end(action, False, False, 0)

        return row

    def on_dismiss(self, notif_id):
        if notif_id is not None:
            run("makoctl", "dismiss", "-n", str(notif_id))
        else:
            run("makoctl", "dismiss")
        self.refresh()

    def on_forget(self, notif_id):
        # mako has no per-id history removal; restoring then dismissing without
        # adding back to history is the canonical workaround for the most-recent
        # entry. For arbitrary IDs, just clear all history.
        run("makoctl", "restore")
        run("makoctl", "dismiss", "--no-history")
        self.refresh()

    def on_clear(self, *_):
        run("makoctl", "dismiss", "--all")
        self.refresh()

    def on_restore(self, *_):
        run("makoctl", "restore")
        self.refresh()

    def on_dnd_toggle(self, switch, *_):
        set_dnd(switch.get_active())

    def on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()


def main():
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception:
        pass

    shader = ShaderController()
    if not shader.enable():
        print("notification-panel: failed to enable Hyprland screen shader", file=sys.stderr)

    def _sig(*_):
        def restore_and_quit():
            shader.restore()
            Gtk.main_quit()
            return False
        GLib.idle_add(restore_and_quit)

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT,  _sig)

    panel = NotificationPanel()
    panel.connect("destroy", lambda *_: Gtk.main_quit())
    panel.show_all()
    try:
        Gtk.main()
    finally:
        shader.restore()
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
