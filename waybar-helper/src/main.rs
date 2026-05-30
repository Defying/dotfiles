//! waybar-helper: small, fast-starting replacements for waybar custom modules
//! that otherwise re-spawn a Python interpreter every poll tick.
//!
//! Subcommands:
//!   sysmon   CPU + memory + network bubble (replaces waybar-sysmon.py)
//!
//! Output matches the Python module's JSON byte-for-byte (modulo live values),
//! so it's a drop-in `exec` swap in waybar's config.

use std::collections::HashMap;
use std::env;
use std::fs;
use std::io::{ErrorKind, Read, Write};
use std::os::unix::net::UnixStream;
use std::process::{Command, ExitCode};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

mod accounts;
mod reset;

fn main() -> ExitCode {
    match env::args().nth(1).as_deref() {
        Some("sysmon") => sysmon(),
        Some("clock24") => clock24(),
        Some("clock12") => clock12(),
        Some("date") => date_module(),
        Some("weather") => weather(),
        Some("autohide") => autohide(),
        Some("autobright") => autobright(),
        Some("codex-account-status") => ExitCode::from(accounts::status_json() as u8),
        other => {
            eprintln!(
                "usage: waybar-helper \
                 <sysmon|clock24|clock12|date|weather|autohide|autobright>; got {:?}",
                other
            );
            ExitCode::from(2)
        }
    }
}

// ── shared helpers ────────────────────────────────────────────────────────────

/// Run a command, return trimmed stdout (empty string on failure).
fn cmd(prog: &str, args: &[&str]) -> String {
    Command::new(prog)
        .args(args)
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim_end().to_string())
        .unwrap_or_default()
}

/// Minimal JSON string escaping for the values we emit (newlines in the date
/// calendar tooltip, plus the usual quote/backslash/control chars).
fn esc(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 8);
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            _ => out.push(c),
        }
    }
    out
}

fn emit_text_tooltip(text: &str, tooltip: &str) -> ExitCode {
    println!(
        "{{\"text\": \"{}\", \"tooltip\": \"{}\"}}",
        esc(text),
        esc(tooltip)
    );
    ExitCode::SUCCESS
}

// ── clock / date (drop the per-tick Python spawn; just date(1)/cal(1)) ─────────

fn clock24() -> ExitCode {
    emit_text_tooltip(&cmd("date", &["+%H:%M"]), &cmd("date", &["+%H:%M:%S"]))
}

fn clock12() -> ExitCode {
    let text = cmd("date", &["+%-I:%M %p"]).to_lowercase();
    let tooltip = cmd("date", &["+%-I:%M:%S %p"]).to_lowercase();
    emit_text_tooltip(&text, &tooltip)
}

fn date_module() -> ExitCode {
    let text = cmd("date", &["+%a %b %d"]).to_lowercase();
    let agenda = cmd("date", &["+%A, %B %-d, %Y"]);
    let calendar = {
        let c = cmd("cal", &["-3"]);
        if c.is_empty() {
            cmd("cal", &[])
        } else {
            c
        }
    };
    let tooltip = if calendar.is_empty() {
        agenda.clone()
    } else {
        format!("{agenda}\n\n{calendar}")
    };
    emit_text_tooltip(&text, &tooltip)
}

// ── weather (wttr.in → JSON; drop the python-just-to-JSON-escape spawn) ───────
//
// Port of waybar-weather.sh. curl(1) still does the network (no TLS in std),
// but parsing, the icon map, tooltip assembly and JSON building are all in
// Rust now — the shell version shelled out to python3 purely to json.dumps the
// tooltip. Caches the last good JSON so a transient curl failure doesn't blank
// the bar until the next interval tick.

fn weather_cache_path() -> String {
    let base = env::var("XDG_CACHE_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            let home = env::var("HOME").unwrap_or_else(|_| "/tmp".into());
            format!("{home}/.cache")
        });
    let dir = format!("{base}/waybar-weather");
    let _ = fs::create_dir_all(&dir);
    format!("{dir}/last.json")
}

const WEATHER_BLANK: &str =
    "{\"text\":\" --°\",\"tooltip\":\"weather unavailable\",\"class\":\"unavailable\"}";

/// Reprint the last good JSON, or a blank "unavailable" bubble. Always exits 0:
/// a weather hiccup must never make waybar drop the module.
fn emit_cached_or_blank() -> ExitCode {
    match fs::read_to_string(weather_cache_path()) {
        Ok(s) if !s.is_empty() => print!("{s}"),
        _ => println!("{WEATHER_BLANK}"),
    }
    ExitCode::SUCCESS
}

/// curl one wttr.in URL; trimmed stdout, empty on any failure.
fn curl(url: &str) -> String {
    cmd("curl", &["-s", "--max-time", "8", url])
}

fn weather_icon(emoji: &str) -> &'static str {
    // Glyphs match the shell version's Nerd Font weather icons.
    const SUN: &str = "\u{f185}";
    const CLOUD_SUN: &str = "\u{f6c4}";
    const CLOUD: &str = "\u{f0c2}";
    const RAIN: &str = "\u{f73d}";
    const SNOW: &str = "\u{f2dc}";
    const BOLT: &str = "\u{f0e7}";
    const SMOG: &str = "\u{f75f}";
    let has = |c: &str| emoji.contains(c);
    if has("☀") {
        SUN
    } else if has("⛅") || has("🌤") || has("🌥") {
        CLOUD_SUN
    } else if has("☁") {
        CLOUD
    } else if has("🌧") || has("🌦") {
        RAIN
    } else if has("🌨") || has("❄") {
        SNOW
    } else if has("⛈") {
        BOLT
    } else if has("🌫") {
        SMOG
    } else {
        CLOUD_SUN
    }
}

fn weather() -> ExitCode {
    let out = curl("https://wttr.in/?format=%c+%t");
    if out.is_empty() || out.contains("Unknown location") {
        return emit_cached_or_blank();
    }

    // out is "<emoji> +<temp>°F"; split on the first '+' like ${out%%+*}/${out#*+}.
    let (emoji, temp_raw) = out.split_once('+').unwrap_or((out.as_str(), out.as_str()));
    let temp: String = temp_raw
        .replace("°F", "")
        .replace("°C", "")
        .replace(['+', ' '], "");
    if temp.is_empty() {
        return emit_cached_or_blank();
    }

    let icon = weather_icon(emoji);

    let tip = curl(
        "https://wttr.in/?format=%l:+%C\\nfeels+%f++humidity+%h\\nwind+%w++%p+precip\\nsun+%S+→+%s",
    );
    let forecast: String = curl("https://wttr.in/?T&0")
        .lines()
        .take(7)
        .collect::<Vec<_>>()
        .join("\n");

    let tooltip = if !tip.is_empty() || !forecast.is_empty() {
        if forecast.is_empty() {
            tip
        } else {
            format!("{tip}\n\n{forecast}")
        }
    } else {
        "weather details unavailable".to_string()
    };

    let json = format!(
        "{{\"text\": \"{} {}°\", \"tooltip\": \"{}\"}}",
        esc(icon),
        esc(&temp),
        esc(&tooltip)
    );
    // Cache + emit the same bytes (trailing newline, as the shell's tee did).
    let _ = fs::write(weather_cache_path(), format!("{json}\n"));
    println!("{json}");
    ExitCode::SUCCESS
}

// ── sysmon ──────────────────────────────────────────────────────────────────

fn state_path() -> String {
    let base = env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/tmp".into());
    format!("{base}/waybar-helper-sysmon.state")
}

/// Previous-tick sample persisted between invocations.
#[derive(Default)]
struct Prev {
    t: f64,
    idle: u64,
    total: u64,
    iface: String,
    rx: u64,
    tx: u64,
    have_net: bool,
}

fn read_prev() -> Prev {
    // Single space-separated line: t idle total iface rx tx
    let mut p = Prev::default();
    if let Ok(s) = fs::read_to_string(state_path()) {
        let f: Vec<&str> = s.split_whitespace().collect();
        if f.len() == 6 {
            p.t = f[0].parse().unwrap_or(0.0);
            p.idle = f[1].parse().unwrap_or(0);
            p.total = f[2].parse().unwrap_or(0);
            p.iface = if f[3] == "-" {
                String::new()
            } else {
                f[3].to_string()
            };
            p.rx = f[4].parse().unwrap_or(0);
            p.tx = f[5].parse().unwrap_or(0);
            p.have_net = true;
        }
    }
    p
}

fn write_state(t: f64, idle: u64, total: u64, iface: &str, rx: u64, tx: u64) {
    let ifc = if iface.is_empty() { "-" } else { iface };
    let _ = fs::write(state_path(), format!("{t} {idle} {total} {ifc} {rx} {tx}"));
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// (idle, total) jiffies from the aggregate `cpu` line of /proc/stat.
fn read_cpu_totals() -> (u64, u64) {
    let s = match fs::read_to_string("/proc/stat") {
        Ok(s) => s,
        Err(_) => return (0, 0),
    };
    let head = s.lines().next().unwrap_or("");
    let parts: Vec<&str> = head.split_whitespace().collect();
    if parts.first() != Some(&"cpu") {
        return (0, 0);
    }
    let f: Vec<u64> = parts[1..]
        .iter()
        .take(10)
        .map(|x| x.parse().unwrap_or(0))
        .collect();
    if f.len() < 5 {
        return (0, 0);
    }
    let idle = f[3] + f[4]; // idle + iowait
    let total: u64 = f.iter().sum();
    (idle, total)
}

fn cpu_percent(prev: &Prev, cur_idle: u64, cur_total: u64) -> i64 {
    let di = cur_idle as i64 - prev.idle as i64;
    let dt = cur_total as i64 - prev.total as i64;
    if dt <= 0 {
        return 0;
    }
    let busy = dt - di;
    (busy as f64 * 100.0 / dt as f64).round().clamp(0.0, 100.0) as i64
}

/// (percent, "used/total GiB")
fn mem_percent_and_human() -> (i64, String) {
    let s = match fs::read_to_string("/proc/meminfo") {
        Ok(s) => s,
        Err(_) => return (0, "0/0 GiB".into()),
    };
    let mut total = 0u64;
    let mut avail = 0u64;
    let mut free = 0u64;
    for line in s.lines() {
        let (key, rest) = match line.split_once(':') {
            Some(kv) => kv,
            None => continue,
        };
        let val: u64 = rest
            .split_whitespace()
            .next()
            .and_then(|v| v.parse().ok())
            .unwrap_or(0);
        match key {
            "MemTotal" => total = val,
            "MemAvailable" => avail = val,
            "MemFree" => free = val,
            _ => {}
        }
    }
    if total == 0 {
        return (0, "0/0 GiB".into());
    }
    let avail = if avail > 0 { avail } else { free };
    let used = total.saturating_sub(avail);
    let pct = (used as f64 * 100.0 / total as f64)
        .round()
        .clamp(0.0, 100.0) as i64;
    let used_gi = used as f64 / (1024.0 * 1024.0);
    let total_gi = total as f64 / (1024.0 * 1024.0);
    (pct, format!("{used_gi:.1}/{total_gi:.0} GiB"))
}

/// Interface carrying the default route (e.g. wld0).
fn default_iface() -> String {
    let s = match fs::read_to_string("/proc/net/route") {
        Ok(s) => s,
        Err(_) => return String::new(),
    };
    for line in s.lines().skip(1) {
        let p: Vec<&str> = line.split_whitespace().collect();
        if p.len() > 3 && p[1] == "00000000" {
            if let Ok(flags) = u64::from_str_radix(p[3], 16) {
                if flags & 2 != 0 {
                    return p[0].to_string();
                }
            }
        }
    }
    String::new()
}

fn net_bytes(iface: &str) -> (u64, u64) {
    let base = format!("/sys/class/net/{iface}/statistics");
    let rd = |f: &str| -> u64 {
        fs::read_to_string(format!("{base}/{f}"))
            .ok()
            .and_then(|s| s.trim().parse().ok())
            .unwrap_or(0)
    };
    (rd("rx_bytes"), rd("tx_bytes"))
}

fn fmt_rate(bits_per_sec: f64) -> String {
    let bps = bits_per_sec.max(0.0);
    if bps >= 1e9 {
        format!("{:.1}gbps", bps / 1e9)
    } else if bps >= 1e6 {
        format!("{:.1}mbps", bps / 1e6)
    } else {
        format!("{:.1}kbps", bps / 1e3)
    }
}

fn classify(cpu: i64, mem: i64) -> &'static str {
    if cpu >= 90 || mem >= 90 {
        "hot"
    } else if cpu >= 60 || mem >= 70 {
        "busy"
    } else {
        "ok"
    }
}

fn sysmon() -> ExitCode {
    let prev = read_prev();
    let now = now_secs();

    let (cur_idle, cur_total) = read_cpu_totals();
    let cpu = cpu_percent(&prev, cur_idle, cur_total);

    let (mem, mem_human) = mem_percent_and_human();

    let iface = default_iface();
    let (mut down, mut up) = (0.0f64, 0.0f64);
    let (mut rx, mut tx) = (0u64, 0u64);
    if !iface.is_empty() {
        let (r, t) = net_bytes(&iface);
        rx = r;
        tx = t;
        let dt = now - prev.t;
        // Only trust a delta if the same interface was sampled last tick.
        if prev.have_net && prev.iface == iface && dt > 0.0 {
            down = (rx.saturating_sub(prev.rx)) as f64 * 8.0 / dt;
            up = (tx.saturating_sub(prev.tx)) as f64 * 8.0 / dt;
        }
    }

    write_state(now, cur_idle, cur_total, &iface, rx, tx);

    // Fixed-width fields so the bubble never reflows the bar (SF Mono).
    let dr = fmt_rate(down);
    let ur = fmt_rate(up);
    let text = format!("cpu {cpu:>3}%  mem {mem:>3}%  ↓ {dr:>9} ↑ {ur:>9}");
    let iface_disp = if iface.is_empty() { "—" } else { &iface };
    let tooltip =
        format!("cpu {cpu}% · mem {mem}% ({mem_human})\\nnet {iface_disp}  ↓ {dr}  ↑ {ur}");
    let class = classify(cpu, mem);
    // Hand-rolled JSON: every field is plain numbers/words/arrows — no quotes
    // or backslashes to escape (the \\n above is literal, as in the Python).
    println!("{{\"text\": \"{text}\", \"tooltip\": \"{tooltip}\", \"class\": \"{class}\"}}");
    ExitCode::SUCCESS
}

// ── autohide: macOS-style waybar hide while a window is fullscreen ─────────────
//
// Port of hypr-waybar-autohide.py. Single-threaded, std-only: it blocks on
// Hyprland's socket2 and does nothing until a window is fullscreen, then uses
// the socket *read timeout* itself as the 10 Hz poll clock (no GLib, no GTK, no
// extra threads). When nothing is fullscreen the read is fully blocking — the
// poll only runs while fullscreen, the single sanctioned poll in the setup.

const POLL: Duration = Duration::from_millis(100); // 10 Hz, only while fullscreen
const REVEAL_PX: i32 = 6; // cursor at/above this Y reveals the bar
const HIDE_PX: i32 = 50; // cursor below this Y hides it; gap = hysteresis

// Any event that can change whether the focused workspace has a fullscreen
// window. We reconcile against Hyprland's real state on each — the `fullscreen`
// payload alone isn't emitted on every transition (closing the fullscreen
// window, some workspace switches), which is what left the bar stuck before.
const RELEVANT: &[&str] = &[
    "fullscreen",
    "workspace",
    "workspacev2",
    "focusedmon",
    "closewindow",
    "openwindow",
    "movewindow",
    "movewindowv2",
    "activewindow",
    "activewindowv2",
];

struct AutoHide {
    cmd_sock: String,
    fullscreen: bool,
    bar_visible: bool,
}

impl AutoHide {
    fn toggle_bar(&self) {
        let _ = Command::new("pkill")
            .args(["-SIGUSR1", "-x", "waybar"])
            .status();
    }
    fn show_bar(&mut self) {
        if !self.bar_visible {
            self.toggle_bar();
            self.bar_visible = true;
        }
    }
    fn hide_bar(&mut self) {
        if self.bar_visible {
            self.toggle_bar();
            self.bar_visible = false;
        }
    }

    /// One request/response on the Hyprland command socket (no hyprctl spawn).
    fn query(&self, request: &[u8]) -> String {
        let s = UnixStream::connect(&self.cmd_sock);
        let mut s = match s {
            Ok(s) => s,
            Err(_) => return String::new(),
        };
        let _ = s.set_read_timeout(Some(Duration::from_millis(100)));
        if s.write_all(request).is_err() {
            return String::new();
        }
        let mut out = String::new();
        let _ = s.read_to_string(&mut out);
        out
    }

    fn cursor_y(&self) -> Option<i32> {
        // cursorpos → "x, y"
        self.query(b"cursorpos")
            .split(',')
            .nth(1)?
            .trim()
            .parse()
            .ok()
    }

    /// Ground truth from Hyprland; None if the query failed (keep current state).
    fn has_fullscreen(&self) -> Option<bool> {
        let out = self.query(b"j/activeworkspace");
        let idx = out.find("\"hasfullscreen\"")?;
        // Only inspect the value token (up to the next comma), so a later field
        // or a window title containing "true"/"false" can't fool us.
        let tail = &out[idx + "\"hasfullscreen\"".len()..];
        let token = tail.split(',').next().unwrap_or("");
        if token.contains("true") {
            Some(true)
        } else if token.contains("false") {
            Some(false)
        } else {
            None
        }
    }

    fn reconcile(&mut self) {
        match self.has_fullscreen() {
            Some(true) if !self.fullscreen => {
                self.fullscreen = true;
                self.hide_bar();
                eprintln!("autohide: enter fullscreen → hide bar");
            }
            Some(false) if self.fullscreen => {
                self.fullscreen = false;
                self.show_bar(); // always restore the bar when leaving fullscreen
                eprintln!("autohide: exit fullscreen → show bar");
            }
            _ => {}
        }
    }

    fn poll_cursor(&mut self) {
        if let Some(y) = self.cursor_y() {
            if y <= REVEAL_PX {
                self.show_bar();
            } else if y >= HIDE_PX {
                self.hide_bar();
            }
        }
    }
}

fn autohide() -> ExitCode {
    let his = env::var("HYPRLAND_INSTANCE_SIGNATURE").unwrap_or_default();
    if his.is_empty() {
        eprintln!("waybar-helper autohide: not inside a Hyprland session");
        return ExitCode::from(1);
    }
    let runtime = env::var("XDG_RUNTIME_DIR").unwrap_or_else(|_| "/tmp".into());
    let sock2 = format!("{runtime}/hypr/{his}/.socket2.sock");
    let cmd_sock = format!("{runtime}/hypr/{his}/.socket.sock");

    let mut stream = match UnixStream::connect(&sock2) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("waybar-helper autohide: cannot connect socket2: {e}");
            return ExitCode::from(1);
        }
    };

    let mut ah = AutoHide {
        cmd_sock,
        fullscreen: false,
        bar_visible: true,
    };
    ah.reconcile(); // sync to current state on launch

    // Read timeout doubles as the 10Hz poll clock — only set while fullscreen.
    let apply_timeout = |s: &UnixStream, fs: bool| {
        let _ = s.set_read_timeout(if fs { Some(POLL) } else { None });
    };
    apply_timeout(&stream, ah.fullscreen);

    let mut buf: Vec<u8> = Vec::new();
    let mut chunk = [0u8; 4096];
    loop {
        match stream.read(&mut chunk) {
            Ok(0) => return ExitCode::SUCCESS, // socket closed (compositor gone)
            Ok(n) => {
                buf.extend_from_slice(&chunk[..n]);
                let mut changed = false;
                while let Some(pos) = buf.iter().position(|&b| b == b'\n') {
                    let line: Vec<u8> = buf.drain(..=pos).collect();
                    let line = String::from_utf8_lossy(&line[..line.len() - 1]);
                    let event = line.split(">>").next().unwrap_or("");
                    if RELEVANT.contains(&event) {
                        ah.reconcile();
                        changed = true;
                    }
                }
                if changed {
                    apply_timeout(&stream, ah.fullscreen);
                }
            }
            // Read timeout while fullscreen → a poll tick.
            Err(ref e) if e.kind() == ErrorKind::WouldBlock || e.kind() == ErrorKind::TimedOut => {
                if ah.fullscreen {
                    ah.poll_cursor();
                }
            }
            Err(ref e) if e.kind() == ErrorKind::Interrupted => continue,
            Err(_) => return ExitCode::from(1),
        }
    }
}

// ── autobright: adaptive backlight from the M1 ambient light sensor ───────────
//
// Port of hypr-auto-brightness.py. Polling daemon (one tiny sysfs read every
// POLL_S, not a busy loop) easing the panel along a log curve. Brightness is set
// IN-PROCESS via a direct sysfs write when the node is group-writable (the
// `video` group + 90-backlight-perms.rules path) — NO process spawns, even
// during a fade. Only if the direct write fails does it fall back to one
// `busctl` logind SetBrightness call (the std crate has no in-process D-Bus; the
// Python used Gio there). It acts only when enabled, the target differs by more
// than a deadband, the session isn't idle-dimmed, and the user hasn't just set
// brightness manually (then it backs off and adopts their level as baseline).

const ALS: &str = "/sys/bus/iio/devices/iio:device0/in_illuminance_input";
const BRIGHT: &str = "/sys/class/backlight/apple-panel-bl/brightness";
const MAXF: &str = "/sys/class/backlight/apple-panel-bl/max_brightness";

const POLL_S: Duration = Duration::from_secs(4);
const DEADBAND_PCT: i64 = 5;
const MANUAL_TOL_FRAC: f64 = 0.05;
const BACKOFF: Duration = Duration::from_secs(300);
const LUX_EMA: f64 = 0.35; // lower = smoother lux
const FADE_SECONDS: f64 = 1.8; // gentle macOS-like ramp per adjustment
const FADE_STEPS: u32 = 110; // fine steps (in-process writes are cheap)

// ── learning: remember the brightness the user prefers at each light level ────
//
// The fixed log curve (`default_pct`) is only a prior. Whenever the user sets
// brightness by hand we record it against the current ambient-light *bucket*
// (log-spaced, BUCKETS_PER_DECADE per 10× lux) and EMA it toward their choice.
// The bucket's influence grows with how many times they've confirmed it
// (confidence = n/CONF_FULL, capped at 1), so one accidental nudge barely moves
// the curve but a consistent preference takes it over. Targets interpolate
// linearly between adjacent buckets so the curve stays smooth as light drifts.
// The model is a tiny TSV under XDG_STATE_HOME so it survives reboots.
const BUCKETS_PER_DECADE: f64 = 4.0;
const LEARN_ALPHA: f64 = 0.35; // weight of the newest manual sample
const SAMPLE_CAP: u32 = 20; // keep adapting; don't freeze a bucket forever
const CONF_FULL: f64 = 6.0; // confirmations for a bucket to fully own its level

/// Continuous bucket coordinate for a lux reading (log-spaced).
fn lux_bucket_pos(lux: f64) -> f64 {
    (lux.max(0.0) + 1.0).log10() * BUCKETS_PER_DECADE
}

/// The fixed log-curve prior at a bucket's center lux.
fn default_pct_at_bucket(b: i64) -> f64 {
    let lux = 10f64.powf(b as f64 / BUCKETS_PER_DECADE) - 1.0;
    lux_to_pct(lux) as f64
}

/// Learned (lux-bucket → preferred percent) model, persisted as TSV.
#[derive(Default)]
struct Model {
    buckets: HashMap<i64, (f64, u32)>, // bucket → (preferred pct, sample count)
}

impl Model {
    fn path() -> String {
        let base = env::var("XDG_STATE_HOME")
            .ok()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| {
                let home = env::var("HOME").unwrap_or_else(|_| "/tmp".into());
                format!("{home}/.local/state")
            });
        let dir = format!("{base}/hypr");
        let _ = fs::create_dir_all(&dir);
        format!("{dir}/auto-brightness-model.tsv")
    }

    fn load() -> Self {
        let mut m = Model::default();
        if let Ok(s) = fs::read_to_string(Model::path()) {
            for line in s.lines() {
                let f: Vec<&str> = line.split('\t').collect();
                if f.len() == 3 {
                    if let (Ok(b), Ok(pct), Ok(n)) =
                        (f[0].parse(), f[1].parse::<f64>(), f[2].parse())
                    {
                        m.buckets.insert(b, (pct.clamp(10.0, 100.0), n));
                    }
                }
            }
        }
        m
    }

    fn save(&self) {
        let mut buckets: Vec<_> = self.buckets.iter().collect();
        buckets.sort_by_key(|(b, _)| **b);
        let mut out = String::new();
        for (b, (pct, n)) in buckets {
            out.push_str(&format!("{b}\t{pct:.1}\t{n}\n"));
        }
        let _ = fs::write(Model::path(), out);
    }

    /// Fold a manual preference into a bucket. Returns the new (pct, n) so the
    /// caller can log what was learned.
    fn learn(&mut self, b: i64, pref_pct: f64) -> (f64, u32) {
        let e = self.buckets.entry(b).or_insert((pref_pct, 0));
        e.0 = LEARN_ALPHA * pref_pct + (1.0 - LEARN_ALPHA) * e.0;
        e.1 = (e.1 + 1).min(SAMPLE_CAP);
        *e
    }

    /// A bucket's effective percent: learned value blended over the prior by
    /// confidence (no samples → pure prior; many → pure learned).
    fn effective(&self, b: i64) -> f64 {
        let default = default_pct_at_bucket(b);
        match self.buckets.get(&b) {
            Some(&(pct, n)) => {
                let conf = (n as f64 / CONF_FULL).min(1.0);
                conf * pct + (1.0 - conf) * default
            }
            None => default,
        }
    }

    /// Target percent for a lux reading: linear interp between the two buckets
    /// it falls between, so the curve has no steps as light drifts.
    fn target_pct(&self, lux: f64) -> i64 {
        let pos = lux_bucket_pos(lux);
        let b0 = pos.floor() as i64;
        let frac = pos - b0 as f64;
        let v = self.effective(b0) * (1.0 - frac) + self.effective(b0 + 1) * frac;
        (v.round() as i64).clamp(10, 100)
    }
}

fn read_int(path: &str) -> Option<i64> {
    fs::read_to_string(path).ok()?.trim().parse().ok()
}

/// `$XDG_RUNTIME_DIR` (matches private_runtime_dir / hypr-brightness-fade.sh).
fn runtime_dir() -> String {
    env::var("XDG_RUNTIME_DIR")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            let home = env::var("HOME").unwrap_or_else(|_| "/tmp".into());
            format!("{home}/.cache/hypr-runtime")
        })
}

/// True while the idle fade has dimmed the panel (its saved-brightness state
/// file is present) — auto-brightness must not fight the idle dim.
fn idle_dimmed() -> bool {
    let p = format!("{}/hypr-brightness-fade/saved-brightness", runtime_dir());
    fs::metadata(p).is_ok()
}

/// Toggle file: present = auto-brightness off (Super+Shift+B touches/removes it).
fn autobright_off() -> bool {
    let base = env::var("XDG_CACHE_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            let home = env::var("HOME").unwrap_or_else(|_| "/tmp".into());
            format!("{home}/.cache")
        });
    fs::metadata(format!("{base}/hypr/auto-brightness.off")).is_ok()
}

/// In-process direct sysfs write; falls back to one busctl logind call. Tracks
/// whether the direct path is usable so the fallback is only tried once it's
/// actually needed (perms can change under us).
struct Brightness {
    direct: bool,
}

impl Brightness {
    fn new() -> Self {
        // Probe writability without a spawn; refined on first real write.
        Brightness {
            direct: fs::OpenOptions::new().write(true).open(BRIGHT).is_ok(),
        }
    }

    fn set_raw(&mut self, raw: i64) {
        if self.direct {
            if fs::write(BRIGHT, raw.to_string()).is_ok() {
                return;
            }
            self.direct = false; // perms changed under us; fall back from here
        }
        let _ = Command::new("busctl")
            .args([
                "call",
                "org.freedesktop.login1",
                "/org/freedesktop/login1/session/auto",
                "org.freedesktop.login1.Session",
                "SetBrightness",
                "ssu",
                "backlight",
                "apple-panel-bl",
                &raw.to_string(),
            ])
            .status();
    }

    fn fade(&mut self, start: i64, target: i64) {
        if start == target {
            return;
        }
        let delay = Duration::from_secs_f64(FADE_SECONDS / FADE_STEPS as f64);
        for i in 1..=FADE_STEPS {
            let t = i as f64 / FADE_STEPS as f64;
            let e = 1.0 - (1.0 - t).powi(3); // ease-out cubic
            let raw = (start as f64 + (target - start) as f64 * e).round() as i64;
            self.set_raw(raw);
            std::thread::sleep(delay);
        }
    }
}

/// ~10% in the dark, ~50% in a normal room (~75 lux), 100% in daylight.
fn lux_to_pct(lux: f64) -> i64 {
    let v = (12.0 + 22.0 * (lux.max(0.0) + 1.0).log10()).round() as i64;
    v.clamp(10, 100)
}

fn autobright() -> ExitCode {
    if read_int(ALS).is_none() {
        eprintln!("waybar-helper autobright: no ALS at {ALS}");
        return ExitCode::from(1);
    }
    let max_raw = read_int(MAXF).unwrap_or(0);
    if max_raw <= 0 {
        eprintln!("waybar-helper autobright: no max brightness");
        return ExitCode::from(1);
    }
    let tol = ((max_raw as f64 * MANUAL_TOL_FRAC) as i64).max(1);
    let mut setter = Brightness::new();
    let mut model = Model::load();

    let mut last_set_raw: Option<i64> = None;
    let mut backoff_until: Option<std::time::Instant> = None;
    let mut ema_lux: Option<f64> = None;

    loop {
        std::thread::sleep(POLL_S);

        if autobright_off() {
            last_set_raw = None;
            ema_lux = None;
            continue;
        }
        if idle_dimmed() {
            continue;
        }

        let raw_lux = match read_int(ALS) {
            Some(v) => v as f64,
            None => continue,
        };
        let lux = match ema_lux {
            None => raw_lux,
            Some(prev) => LUX_EMA * raw_lux + (1.0 - LUX_EMA) * prev,
        };
        ema_lux = Some(lux);

        let cur_raw = match read_int(BRIGHT) {
            Some(v) => v,
            None => continue,
        };
        let cur_pct = (cur_raw as f64 * 100.0 / max_raw as f64).round() as i64;

        // On first sighting, adopt whatever's set now as our baseline instead of
        // yanking it to the curve. We never override a level the user already
        // chose, and it makes the next manual change detectable (and learnable).
        if last_set_raw.is_none() {
            last_set_raw = Some(cur_raw);
            continue;
        }

        // Manual-change detection: brightness drifted from what we set. Learn
        // the user's choice for this light level, adopt it as the new baseline,
        // and back off so we never immediately fight the adjustment.
        if let Some(last) = last_set_raw {
            if (cur_raw - last).abs() > tol {
                let bucket = lux_bucket_pos(lux).round() as i64;
                let (pref, n) = model.learn(bucket, cur_pct as f64);
                model.save();
                eprintln!(
                    "autobright: learned lux≈{:.0} (bucket {bucket}) → prefer {cur_pct}% \
                     [now {pref:.0}%, n={n}]",
                    lux
                );
                last_set_raw = Some(cur_raw);
                backoff_until = Some(std::time::Instant::now() + BACKOFF);
                continue;
            }
        }
        if backoff_until.is_some_and(|until| std::time::Instant::now() < until) {
            continue;
        }

        let target_pct = model.target_pct(lux);
        if (target_pct - cur_pct).abs() < DEADBAND_PCT {
            continue;
        }

        let target_raw = (max_raw as f64 * target_pct as f64 / 100.0) as i64;
        setter.fade(cur_raw, target_raw);
        last_set_raw = Some(target_raw);
    }
}
