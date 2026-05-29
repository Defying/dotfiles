//! waybar-helper: small, fast-starting replacements for waybar custom modules
//! that otherwise re-spawn a Python interpreter every poll tick.
//!
//! Subcommands:
//!   sysmon   CPU + memory + network bubble (replaces waybar-sysmon.py)
//!
//! Output matches the Python module's JSON byte-for-byte (modulo live values),
//! so it's a drop-in `exec` swap in waybar's config.

use std::env;
use std::fs;
use std::process::ExitCode;
use std::time::{SystemTime, UNIX_EPOCH};

fn main() -> ExitCode {
    match env::args().nth(1).as_deref() {
        Some("sysmon") => sysmon(),
        other => {
            eprintln!("usage: waybar-helper <sysmon>; got {:?}", other);
            ExitCode::from(2)
        }
    }
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
            p.iface = if f[3] == "-" { String::new() } else { f[3].to_string() };
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
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
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
    let f: Vec<u64> = parts[1..].iter().take(10).map(|x| x.parse().unwrap_or(0)).collect();
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
        let val: u64 = rest.split_whitespace().next().and_then(|v| v.parse().ok()).unwrap_or(0);
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
    let pct = (used as f64 * 100.0 / total as f64).round().clamp(0.0, 100.0) as i64;
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
    let tooltip = format!(
        "cpu {cpu}% · mem {mem}% ({mem_human})\\nnet {iface_disp}  ↓ {dr}  ↑ {ur}"
    );
    let class = classify(cpu, mem);
    // Hand-rolled JSON: every field is plain numbers/words/arrows — no quotes
    // or backslashes to escape (the \\n above is literal, as in the Python).
    println!("{{\"text\": \"{text}\", \"tooltip\": \"{tooltip}\", \"class\": \"{class}\"}}");
    ExitCode::SUCCESS
}
