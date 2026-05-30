//! Shared helpers for the codex/claude usage bubbles (ported from the common
//! bits of waybar-openai-tokens.py and waybar-claude-usage.py).

use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use chrono::{Datelike, Local, TimeZone};
use serde_json::json;

pub fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

pub fn cache_dir() -> PathBuf {
    let base = env::var("XDG_CACHE_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env::var("HOME").unwrap_or_else(|_| "/tmp".into())).join(".cache")
        });
    base.join("waybar")
}

pub fn asset(name: &str) -> String {
    let home = env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    format!("{home}/dotfiles/assets/{name}")
}

/// Print a Waybar JSON line. Key order differs from the Python (serde sorts),
/// but Waybar parses JSON so order is irrelevant.
pub fn emit(text: &str, tooltip: &str, class: &str) {
    let text = pango_escape(text);
    let tooltip = pango_escape(tooltip);
    println!(
        "{}",
        json!({ "text": text, "tooltip": tooltip, "class": class })
    );
}

fn pango_escape(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for c in value.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            _ => out.push(c),
        }
    }
    out
}

/// Local-time reset label: "%H:%M" if today, else "%a %H:%M" lowercased.
/// `None`/0 → "unknown". Mirrors both scripts' `fmt_reset`.
pub fn fmt_reset(epoch: Option<i64>) -> String {
    let e = match epoch {
        Some(e) if e != 0 => e,
        _ => return "unknown".into(),
    };
    let when = match Local.timestamp_opt(e, 0).single() {
        Some(w) => w,
        None => return "unknown".into(),
    };
    let now = Local::now();
    if when.year() == now.year() && when.ordinal() == now.ordinal() {
        when.format("%H:%M").to_string()
    } else {
        when.format("%a %H:%M").to_string().to_lowercase()
    }
}

/// Compact "Nd Nh" / "Nh Nm" / "Nm" countdown to `epoch`. Empty if past/missing.
pub fn compact_countdown(epoch: Option<i64>) -> String {
    let e = match epoch {
        Some(e) if e != 0 => e,
        _ => return String::new(),
    };
    let seconds = (e - now()).max(0);
    if seconds <= 0 {
        return String::new();
    }
    let minutes = (seconds + 59) / 60;
    let (days, rem) = (minutes / 1440, minutes % 1440);
    let (hours, mins) = (rem / 60, rem % 60);
    if days > 0 {
        format!("{days}d {hours}h")
    } else if hours > 0 {
        format!("{hours}h {mins}m")
    } else {
        format!("{mins}m")
    }
}

/// Compact "Nh" / "Nh Nm" / "Nm" duration (used for retry hints). Mirrors the
/// claude script's `compact_duration` (rounds seconds up to whole minutes).
// Consumed by the claude usage subcommand (next migration stage).
#[allow(dead_code)]
pub fn compact_duration(seconds: i64) -> String {
    let seconds = seconds.max(0);
    let minutes = (seconds + 59) / 60;
    let (hours, mins) = (minutes / 60, minutes % 60);
    if hours > 0 && mins == 0 {
        format!("{hours}h")
    } else if hours > 0 {
        format!("{hours}h {mins}m")
    } else {
        format!("{mins}m")
    }
}

/// Parse an ISO-8601 timestamp (with Z or offset) to a UTC epoch.
// Consumed by the claude usage subcommand (next migration stage).
#[allow(dead_code)]
pub fn iso_to_epoch(value: &str) -> Option<i64> {
    if value.is_empty() {
        return None;
    }
    chrono::DateTime::parse_from_rfc3339(value)
        .ok()
        .map(|dt| dt.timestamp())
}

/// Fire a desktop notification only on a transition into a worse level; suppress
/// while staying at the same level. State in `<service>.notify`. Mirrors the
/// identical `maybe_notify` in both scripts.
pub fn maybe_notify(service: &str, level: &str, remaining: i64, reset_label: &str, icon: &str) {
    let dir = cache_dir();
    let _ = fs::create_dir_all(&dir);
    let state_file = dir.join(format!("{}.notify", service.to_lowercase()));
    let last_level = fs::read_to_string(&state_file)
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.get("level").and_then(|l| l.as_str()).map(String::from))
        .unwrap_or_default();
    if level == last_level {
        return;
    }
    if !level.is_empty() {
        let mut args: Vec<String> = [
            "notify-send",
            "-a",
            "AI usage",
            "-u",
            "normal",
            "-t",
            "10000",
            "-h",
            "string:x-canonical-private-synchronous:ai-usage",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        if std::path::Path::new(icon).exists() {
            args.push("-i".into());
            args.push(icon.into());
        }
        args.push(format!("{service} usage low"));
        args.push(format!("{remaining}% remaining · resets {reset_label}"));
        let _ = Command::new("setsid")
            .arg("-f")
            .args(&args)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    }
    let _ = fs::write(&state_file, json!({ "level": level }).to_string());
}
