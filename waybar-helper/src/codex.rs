//! Codex subscription usage bubble, ported from `scripts/waybar-openai-tokens.py`.
//!
//! Display path reads the cache and renders instantly; if the cache is stale it
//! kicks off a detached background `--refresh`. Refresh asks the installed Codex
//! CLI for the ChatGPT rate-limit snapshot via the app-server JSON-RPC
//! (`account/rateLimits/read`) — the supported path that auto-refreshes the
//! token — then caches it, notifies on a worse level, and arms/cancels the
//! reset reminder. No OpenAI API keys, no Codex logs.

use std::env;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use serde_json::{json, Value};

use crate::accounts::{self, Account};
use crate::reset;
use crate::usage::{
    asset, cache_dir, compact_countdown, emit, fmt_reset, maybe_notify, now,
};

const CODEX_USAGE_URL: &str = "https://chatgpt.com/codex/settings/usage";
const CACHE_MAX_AGE_SECONDS: i64 = 30;
const REQUEST_TIMEOUT_SECS: u64 = 10;
const REFRESH_LOCK_MAX_AGE_SECONDS: i64 = 120;

fn codex_bin() -> String {
    let bun = PathBuf::from(env::var("HOME").unwrap_or_default())
        .join(".bun/bin/codex");
    if bun.exists() {
        bun.to_string_lossy().into_owned()
    } else {
        "codex".into()
    }
}

fn usage_cache() -> PathBuf {
    cache_dir().join("codex-usage.json")
}
fn refresh_lock() -> PathBuf {
    cache_dir().join("codex-usage.refresh.lock")
}

fn read_cache() -> Value {
    fs::read_to_string(usage_cache())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null)
}

fn cache_age_seconds(cache: &Value) -> i64 {
    let updated = cache.get("updated_at").and_then(|v| v.as_f64()).unwrap_or(0.0);
    (now() as f64 - updated).max(0.0) as i64
}

// ── numeric/value helpers ─────────────────────────────────────────────────────

fn get_num(window: &Value, key: &str) -> Option<f64> {
    window.get(key).and_then(|v| v.as_f64())
}

/// round(float(window[key] or 0)) like the Python `int(round(...))`.
fn used_percent(window: &Value, key: &str) -> i64 {
    get_num(window, key).unwrap_or(0.0).round() as i64
}

fn reached_of(limits: &Value) -> Option<String> {
    match limits.get("rateLimitReachedType") {
        Some(Value::String(s)) if !s.is_empty() => Some(s.clone()),
        _ => None,
    }
}

// ── display formatting ────────────────────────────────────────────────────────

fn window_line(name: &str, window: &Value) -> String {
    if window.is_null() || !window.is_object() || window.as_object().map(|o| o.is_empty()).unwrap_or(true)
    {
        return format!("{name}: unavailable");
    }
    let used = used_percent(window, "usedPercent");
    let remaining = (100 - used).max(0);
    let label = match get_num(window, "windowDurationMins") {
        Some(d) => {
            let mins = d as i64;
            if mins >= 10080 {
                "weekly".to_string()
            } else {
                let hours = mins as f64 / 60.0;
                // "{:g}h" — drop trailing zeros (5.0 -> "5", 1.5 -> "1.5").
                format!("{}h", fmt_g(hours))
            }
        }
        None => name.to_string(),
    };
    let resets = fmt_reset(window.get("resetsAt").and_then(|v| v.as_i64()));
    format!("{label}: {remaining}% remaining ({used}% used), resets {resets}")
}

/// Mimic Python's "{:g}" formatting for the hour label.
fn fmt_g(v: f64) -> String {
    let s = format!("{v}");
    s
}

fn css_class(remaining: i64, reached: bool) -> &'static str {
    if reached || remaining <= 10 {
        "danger"
    } else if remaining <= 30 {
        "warn"
    } else {
        "subscription"
    }
}

fn limit_level(remaining: i64, reached: bool) -> &'static str {
    if reached || remaining <= 10 {
        "critical"
    } else {
        ""
    }
}

struct EmitOpts {
    stale_age: Option<i64>,
    refresh_error: Option<String>,
    notify: bool,
}

fn emit_usage(limits: &Value, account: &Account, opts: EmitOpts) {
    let account_label = if account.is_empty() {
        "active account".to_string()
    } else {
        accounts::display_label(account)
    };
    let primary = limits.get("primary").cloned().unwrap_or(Value::Null);
    let secondary = limits.get("secondary").cloned().unwrap_or(Value::Null);
    let credits = limits.get("credits").cloned().unwrap_or(Value::Null);
    let reached = reached_of(limits);
    let reached_b = reached.is_some();

    let primary_used = used_percent(&primary, "usedPercent");
    let primary_remaining = (100 - primary_used).max(0);
    let has_secondary = secondary.is_object()
        && !secondary.as_object().map(|o| o.is_empty()).unwrap_or(true);
    let weekly_remaining = if has_secondary {
        Some((100 - used_percent(&secondary, "usedPercent")).max(0))
    } else {
        None
    };

    let weekly_blocked = reached_b || matches!(weekly_remaining, Some(w) if w <= 0);
    let display = if weekly_blocked { 0 } else { primary_remaining };
    let level = limit_level(display, reached_b);
    let block_window = if weekly_blocked { &secondary } else { &primary };

    let mut text = format!("{display}%");
    if display == 0 {
        let countdown = compact_countdown(block_window.get("resetsAt").and_then(|v| v.as_i64()));
        if !countdown.is_empty() {
            text = countdown;
        }
    }

    let credit_line = if credits.get("unlimited").and_then(|v| v.as_bool()).unwrap_or(false) {
        "credits: unlimited".to_string()
    } else {
        let bal = match credits.get("balance") {
            Some(Value::String(s)) => s.clone(),
            Some(Value::Number(n)) => n.to_string(),
            _ => "0".to_string(),
        };
        format!("credits: {bal}")
    };
    let reset_label = fmt_reset(block_window.get("resetsAt").and_then(|v| v.as_i64()));

    let plan = limits
        .get("planType")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(String::from)
        .or_else(|| (!account.plan.is_empty()).then(|| account.plan.clone()))
        .unwrap_or_else(|| "plan".to_string());

    let mut lines = vec![
        format!("Codex subscription usage ({plan})"),
        format!("account: {account_label}"),
        window_line("primary", &primary),
        window_line("secondary", &secondary),
        credit_line,
    ];
    lines.push(
        if weekly_blocked && display == 0 {
            "bar shows weekly reset countdown"
        } else if display == 0 {
            "bar shows 5h reset countdown"
        } else {
            "bar shows 5h window remaining %"
        }
        .to_string(),
    );
    if let Some(age) = opts.stale_age {
        lines.push(format!("cached {age}s ago; refreshing in background"));
    }
    if let Some(err) = &opts.refresh_error {
        lines.push(format!("last refresh failed: {err}"));
    }
    if let Some(r) = &reached {
        lines.push(format!("limit state: {r}"));
    }
    lines.push(String::new());
    lines.push(CODEX_USAGE_URL.to_string());

    if opts.notify {
        maybe_notify("Codex", level, display, &reset_label, &asset("openai.svg"));
        if display == 0 {
            reset::schedule(
                "Codex",
                if weekly_blocked { "weekly" } else { "5h" },
                block_window.get("resetsAt").and_then(|v| v.as_i64()),
                Some(&asset("openai.png")),
            );
        } else {
            reset::cancel("Codex");
        }
    }

    emit(&text, &lines.join("\n"), css_class(display, reached_b));
}

// ── display entrypoint ────────────────────────────────────────────────────────

fn account_from_cache(cache: &Value) -> Account {
    cache
        .get("account")
        .cloned()
        .and_then(|v| serde_json::from_value(v).ok())
        .unwrap_or_default()
}

fn emit_cached_or_placeholder() -> ExitCode {
    let cache = read_cache();
    // Python `if not cache:` — missing OR an empty object both mean "no cache yet".
    let empty = cache.as_object().map(|o| o.is_empty()).unwrap_or(true);
    if empty {
        spawn_background_refresh();
        emit("...", "Codex usage refresh started", "refreshing");
        return ExitCode::SUCCESS;
    }

    let age = cache_age_seconds(&cache);
    if age >= CACHE_MAX_AGE_SECONDS {
        spawn_background_refresh();
    }
    let stale_suffix = if age >= CACHE_MAX_AGE_SECONDS {
        format!("cached {age}s ago; refreshing in background")
    } else {
        String::new()
    };

    let error = cache.get("error").and_then(|v| v.as_str());
    let limits = cache.get("limits").cloned().unwrap_or(Value::Null);
    let has_limits = limits.is_object()
        && !limits.as_object().map(|o| o.is_empty()).unwrap_or(true);

    if error == Some("auth") {
        let account = {
            let a = account_from_cache(&cache);
            if a.is_empty() {
                accounts::active_account()
            } else {
                a
            }
        };
        let label = if account.is_empty() {
            "active account".to_string()
        } else {
            accounts::display_label(&account)
        };
        let status = cache
            .get("status")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("Codex login status unavailable");
        // Python always includes the stale line (empty when fresh), then a blank
        // separator, then the hint — so keep the empty element to match spacing.
        let lines = [
            format!("Codex account: {label}"),
            status.to_string(),
            stale_suffix.clone(),
            String::new(),
            "Open quick settings, then use Account or Login.".to_string(),
        ];
        emit("login", lines.join("\n").trim(), "auth");
        return ExitCode::SUCCESS;
    }

    if let Some(err) = error {
        if has_limits {
            emit_usage(
                &limits,
                &account_from_cache(&cache),
                EmitOpts {
                    stale_age: Some(age),
                    refresh_error: Some(err.to_string()),
                    notify: false,
                },
            );
            return ExitCode::SUCCESS;
        }
        let status = cache.get("status").and_then(|v| v.as_str()).unwrap_or("Codex status unavailable");
        let lines = [
            status.to_string(),
            String::new(),
            format!("Could not read Codex subscription usage: {err}"),
            stale_suffix, // always present (empty when fresh), matching Python
            CODEX_USAGE_URL.to_string(),
        ];
        emit("err", lines.join("\n").trim(), "error");
        return ExitCode::SUCCESS;
    }

    emit_usage(
        &limits,
        &account_from_cache(&cache),
        EmitOpts {
            stale_age: if age >= CACHE_MAX_AGE_SECONDS { Some(age) } else { None },
            refresh_error: cache
                .get("refresh_error")
                .and_then(|v| v.as_str())
                .map(String::from),
            notify: false,
        },
    );
    ExitCode::SUCCESS
}

// ── refresh path ──────────────────────────────────────────────────────────────

fn codex_login_status() -> String {
    Command::new("timeout")
        .args(["4", &codex_bin(), "login", "status"])
        .output()
        .ok()
        .map(|o| {
            let mut s = String::from_utf8_lossy(&o.stdout).to_string();
            s.push_str(&String::from_utf8_lossy(&o.stderr)); // Python merged stderr→stdout
            s.trim().to_string()
        })
        .unwrap_or_default()
}

/// One `account/rateLimits/read` over the app-server JSON-RPC (stdio).
fn read_rate_limits() -> Result<Value, String> {
    let initialize = json!({
        "id": 1, "method": "initialize",
        "params": {
            "clientInfo": {"name": "waybar", "title": "Waybar", "version": "1"},
            "capabilities": {
                "experimentalApi": true, "requestAttestation": false,
                "optOutNotificationMethods": []
            }
        }
    });
    let initialized = json!({"method": "initialized"});
    let request = json!({"id": 2, "method": "account/rateLimits/read"});

    let mut child = Command::new(codex_bin())
        .args(["app-server", "--listen", "stdio://"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("could not start codex app-server: {e}"))?;

    let mut stdin = child.stdin.take().ok_or("no stdin")?;
    let stdout = child.stdout.take().ok_or("no stdout")?;
    for msg in [&initialize, &initialized, &request] {
        let _ = writeln!(stdin, "{}", serde_json::to_string(msg).unwrap_or_default());
    }
    let _ = stdin.flush();

    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            let line = match line {
                Ok(l) => l,
                Err(_) => break,
            };
            if let Ok(msg) = serde_json::from_str::<Value>(&line) {
                if msg.get("id").and_then(|v| v.as_i64()) == Some(2) {
                    let _ = tx.send(msg);
                    return;
                }
            }
        }
        let _ = tx.send(Value::Null); // stream ended without our reply
    });

    let outcome = match rx.recv_timeout(Duration::from_secs(REQUEST_TIMEOUT_SECS)) {
        Ok(Value::Null) => Err("Codex rate-limit request timed out".to_string()),
        Ok(msg) => {
            if let Some(err) = msg.get("error") {
                Err(err
                    .get("message")
                    .and_then(|m| m.as_str())
                    .unwrap_or("rate limit request failed")
                    .to_string())
            } else {
                Ok(msg.get("result").cloned().unwrap_or(json!({})))
            }
        }
        Err(_) => Err("Codex rate-limit request timed out".to_string()),
    };
    let _ = child.kill();
    let _ = child.wait();
    drop(stdin);
    outcome
}

fn pick_codex_limits(data: &Value) -> Value {
    if let Some(c) = data.get("rateLimitsByLimitId").and_then(|b| b.get("codex")) {
        return c.clone();
    }
    data.get("rateLimits").cloned().unwrap_or(json!({}))
}

fn write_usage_cache(payload: Value) {
    // {"updated_at": now, ...payload}
    let mut obj = serde_json::Map::new();
    obj.insert("updated_at".into(), json!(now() as f64));
    if let Value::Object(m) = payload {
        for (k, v) in m {
            obj.insert(k, v);
        }
    }
    let _ = fs::create_dir_all(cache_dir());
    let _ = fs::write(usage_cache(), Value::Object(obj).to_string());
}

fn write_cache_raw(payload: &Value) {
    let _ = fs::create_dir_all(cache_dir());
    let _ = fs::write(usage_cache(), payload.to_string());
}

fn signal_waybar(signal: Option<i64>) {
    if let Some(s) = signal {
        let _ = Command::new("pkill")
            .args(["-RTMIN+".to_string() + &s.to_string(), "-x".into(), "waybar".into()])
            .status();
    }
}

fn refresh_usage() {
    let account = accounts::sync_active_slot().unwrap_or_else(|_| accounts::active_account());
    let account_label = if account.is_empty() {
        "active account".to_string()
    } else {
        accounts::display_label(&account)
    };

    let status = codex_login_status();
    if !status.contains("Logged in") {
        let lines = [
            format!("Codex account: {account_label}"),
            if status.is_empty() {
                "Codex login status unavailable".to_string()
            } else {
                status.clone()
            },
            String::new(),
            "Open quick settings, then use Account or Login.".to_string(),
        ];
        emit("login", &lines.join("\n"), "auth");
        write_usage_cache(json!({
            "error": "auth", "status": status,
            "account": serde_json::to_value(&account).unwrap_or(Value::Null)
        }));
        return;
    }

    let data = match read_rate_limits() {
        Ok(d) => d,
        Err(exc) => {
            let previous = read_cache();
            let prev_limits = previous.get("limits").cloned().unwrap_or(Value::Null);
            let has_limits = prev_limits.is_object()
                && !prev_limits.as_object().map(|o| o.is_empty()).unwrap_or(true);
            if has_limits {
                if let Value::Object(mut m) = previous.clone() {
                    m.remove("error");
                    m.insert("refresh_error".into(), json!(exc));
                    m.insert("last_refresh_attempt_at".into(), json!(now() as f64));
                    m.insert("status".into(), json!(status));
                    if !account.is_empty() {
                        m.insert("account".into(), serde_json::to_value(&account).unwrap_or(Value::Null));
                    }
                    write_cache_raw(&Value::Object(m));
                }
                emit_usage(
                    &prev_limits,
                    &account_from_cache(&previous),
                    EmitOpts {
                        stale_age: Some(cache_age_seconds(&previous)),
                        refresh_error: Some(exc),
                        notify: false,
                    },
                );
                return;
            }
            let lines = [
                status.clone(),
                String::new(),
                format!("Could not read Codex subscription usage: {exc}"),
                CODEX_USAGE_URL.to_string(),
            ];
            emit("err", &lines.join("\n"), "error");
            write_usage_cache(json!({
                "error": exc, "status": status,
                "account": serde_json::to_value(&account).unwrap_or(Value::Null)
            }));
            return;
        }
    };

    let limits = pick_codex_limits(&data);
    write_usage_cache(json!({
        "limits": limits,
        "account": serde_json::to_value(&account).unwrap_or(Value::Null),
        "status": status
    }));
    emit_usage(
        &limits,
        &account,
        EmitOpts { stale_age: None, refresh_error: None, notify: true },
    );
}

// ── refresh lock + background spawn ───────────────────────────────────────────

fn refresh_in_progress() -> bool {
    let lock = refresh_lock();
    fs::metadata(&lock)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.elapsed().ok())
        .map(|d| d.as_secs() as i64 <= REFRESH_LOCK_MAX_AGE_SECONDS)
        .unwrap_or(false)
        && refresh_lock_holder_alive(&lock)
}

fn refresh_lock_holder_alive(lock: &Path) -> bool {
    let pid = match fs::read_to_string(lock)
        .ok()
        .and_then(|s| s.trim().parse::<u32>().ok())
    {
        Some(pid) => pid,
        None => return true,
    };
    PathBuf::from(format!("/proc/{pid}")).exists()
}

/// O_CREAT|O_EXCL lock; clears a stale (>max-age) lock first. None if held.
fn acquire_refresh_lock() -> Option<fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    let _ = fs::create_dir_all(cache_dir());
    let lock = refresh_lock();
    if let Ok(m) = fs::metadata(&lock) {
        let stale = m
            .modified()
            .ok()
            .and_then(|t| t.elapsed().ok())
            .map(|d| d.as_secs() as i64 > REFRESH_LOCK_MAX_AGE_SECONDS)
            .unwrap_or(true)
            || !refresh_lock_holder_alive(&lock);
        if stale {
            let _ = fs::remove_file(&lock);
        }
    }
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&lock)
        .ok()?;
    let _ = write!(f, "{}", std::process::id());
    Some(f)
}

fn release_refresh_lock(f: fs::File) {
    drop(f);
    let _ = fs::remove_file(refresh_lock());
}

fn spawn_background_refresh() {
    if refresh_in_progress() {
        return;
    }
    if let Ok(exe) = env::current_exe() {
        let _ = Command::new("setsid")
            .arg("-f")
            .arg(exe)
            .args(["codex", "--refresh", "--signal", "8"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
}

fn refresh_with_lock(signal: Option<i64>) -> ExitCode {
    let lock = match acquire_refresh_lock() {
        Some(f) => f,
        None => return ExitCode::SUCCESS,
    };
    refresh_usage();
    signal_waybar(signal);
    release_refresh_lock(lock);
    ExitCode::SUCCESS
}

/// `waybar-helper codex [--refresh] [--signal N]`
pub fn run(args: &[String]) -> ExitCode {
    if args.first().map(|s| s.as_str()) == Some("--refresh") {
        let signal = parse_signal(args);
        return refresh_with_lock(signal);
    }
    emit_cached_or_placeholder()
}

fn parse_signal(args: &[String]) -> Option<i64> {
    let i = args.iter().position(|a| a == "--signal")?;
    args.get(i + 1)?.parse().ok()
}
