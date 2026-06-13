//! Claude subscription usage bubble, ported from `scripts/waybar-claude-usage.py`.
//!
//! Calls Anthropic's OAuth usage endpoint with the Claude Code OAuth token (no
//! API keys, no transcript logs). std has no TLS, so the GET goes through
//! curl(1). Fetches inline when the cache is stale (matching the Python, which
//! blocks the waybar tick up to the request timeout), with the same rate-limit
//! backoff, stale-window handling, notifications and reset reminders.

use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::{Command, ExitCode, Stdio};

use serde_json::{json, Value};

use crate::reset;
use crate::usage::{
    asset, cache_dir, compact_countdown, compact_duration, emit, fmt_reset, iso_to_epoch,
    maybe_notify, now, AUTH_BUBBLE_TEXT,
};

const CLAUDE_USAGE_URL: &str = "https://claude.ai/settings/usage";
const OAUTH_USAGE_ENDPOINT: &str = "https://api.anthropic.com/api/oauth/usage";
const REQUEST_TIMEOUT: u32 = 10;
const CACHE_TTL_SECONDS: i64 = 300;
const RESET_PAST_GRACE_SECONDS: i64 = 120;

fn creds_path() -> PathBuf {
    PathBuf::from(env::var("HOME").unwrap_or_else(|_| "/tmp".into()))
        .join(".claude/.credentials.json")
}
fn cache_path() -> PathBuf {
    cache_dir().join("claude-usage.json")
}

fn read_json(path: &PathBuf) -> Value {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null)
}

fn read_oauth() -> Value {
    read_json(&creds_path())
        .get("claudeAiOauth")
        .cloned()
        .filter(|v| v.is_object())
        .unwrap_or(Value::Object(Default::default()))
}

// ── fetch ─────────────────────────────────────────────────────────────────────

enum FetchErr {
    Http { code: u16, retry_after: Option<i64> },
    Other(String),
}

fn token_needs_refresh(oauth: &Value) -> bool {
    match oauth.get("expiresAt").and_then(|v| v.as_f64()) {
        Some(ms) if ms != 0.0 => ms / 1000.0 < (now() as f64 + 90.0),
        _ => false,
    }
}

fn refresh_auth() {
    let _ = Command::new("timeout")
        .args(["6", "claude", "auth", "status"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

/// GET the usage endpoint via curl. Ok(json) on 200, else a typed error.
fn fetch_usage(token: &str) -> Result<Value, FetchErr> {
    let body = env::temp_dir().join(format!("claude-usage.body.{}", std::process::id()));
    let out = Command::new("curl")
        .args([
            "-s",
            "-o",
            &body.to_string_lossy(),
            "-w",
            "%{http_code}\t%header{retry-after}",
            "--max-time",
            &REQUEST_TIMEOUT.to_string(),
            "-H",
            &format!("Authorization: Bearer {token}"),
            "-H",
            "Accept: application/json",
            "-H",
            "User-Agent: claude-code-waybar/1",
            OAUTH_USAGE_ENDPOINT,
        ])
        .output();

    let result = (|| {
        let out = out.map_err(|e| FetchErr::Other(format!("refresh failed: {e}")))?;
        let meta = String::from_utf8_lossy(&out.stdout);
        let mut parts = meta.split('\t');
        let code: u16 = parts.next().unwrap_or("0").trim().parse().unwrap_or(0);
        let retry_after = parts
            .next()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .and_then(|s| s.parse::<i64>().ok());
        if code == 200 {
            let v: Value = serde_json::from_str(&fs::read_to_string(&body).unwrap_or_default())
                .map_err(|e| FetchErr::Other(format!("refresh failed: {e}")))?;
            Ok(v)
        } else if code == 0 {
            Err(FetchErr::Other("refresh failed: connection error".into()))
        } else {
            Err(FetchErr::Http { code, retry_after })
        }
    })();
    let _ = fs::remove_file(&body);
    result
}

/// Fetch with token-refresh and a single 401/403 retry. Mirrors `read_usage`.
fn read_usage(oauth: &Value) -> Result<Value, FetchErr> {
    let mut token = oauth
        .get("accessToken")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if token.is_empty() {
        return Err(FetchErr::Other("Claude OAuth token not found".into()));
    }
    if token_needs_refresh(oauth) {
        refresh_auth();
        token = read_oauth()
            .get("accessToken")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if token.is_empty() {
            return Err(FetchErr::Other(
                "Claude OAuth token not found after refresh".into(),
            ));
        }
    }
    match fetch_usage(&token) {
        Err(FetchErr::Http { code, retry_after }) if code == 401 || code == 403 => {
            refresh_auth();
            let t = read_oauth()
                .get("accessToken")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            if !t.is_empty() {
                fetch_usage(&t)
            } else {
                Err(FetchErr::Http { code, retry_after })
            }
        }
        other => other,
    }
}

fn retry_after_seconds(err: &FetchErr) -> i64 {
    match err {
        FetchErr::Http {
            code: 429,
            retry_after,
        } => retry_after.unwrap_or(300).clamp(60, 3600),
        _ => 60,
    }
}

fn describe_refresh_error(err: &FetchErr) -> String {
    match err {
        FetchErr::Http { code: 429, .. } => "Anthropic rate-limited the refresh".into(),
        FetchErr::Http { code, .. } => format!("refresh failed: HTTP {code}"),
        FetchErr::Other(m) => format!("refresh failed: {m}"),
    }
}

// ── cache ─────────────────────────────────────────────────────────────────────

fn read_cache(max_age: Option<i64>) -> Option<Value> {
    let cached = read_json(&cache_path());
    if !cached.is_object() {
        return None;
    }
    if let Some(age) = max_age {
        let updated = cached
            .get("updated_at")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        if now() as f64 - updated > age as f64 {
            return None;
        }
    }
    Some(cached)
}

fn write_cache(usage: &Value) {
    let _ = fs::create_dir_all(cache_dir());
    let payload = json!({ "updated_at": now() as f64, "usage": usage });
    let _ = fs::write(cache_path(), payload.to_string());
}

/// Store a retry deadline + error text on the existing cache. Returns retry_at.
fn update_cache_retry(cached: &Value, err: &FetchErr) -> f64 {
    let retry_at = now() as f64 + retry_after_seconds(err) as f64;
    if let Value::Object(mut m) = cached.clone() {
        m.insert("retry_at".into(), json!(retry_at));
        m.insert(
            "refresh_error_text".into(),
            json!(describe_refresh_error(err)),
        );
        let _ = fs::create_dir_all(cache_dir());
        let _ = fs::write(cache_path(), Value::Object(m).to_string());
    }
    retry_at
}

// ── display helpers ───────────────────────────────────────────────────────────

fn round_util(window: &Value) -> i64 {
    window
        .get("utilization")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0)
        .round() as i64
}

fn is_empty_obj(v: &Value) -> bool {
    !v.is_object() || v.as_object().map(|o| o.is_empty()).unwrap_or(true)
}

fn fmt_reset_iso(window: &Value) -> String {
    let iso = window
        .get("resets_at")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    fmt_reset(iso_to_epoch(iso))
}

fn window_line(label: &str, window: &Value) -> String {
    if is_empty_obj(window) {
        return format!("{label}: unavailable");
    }
    let used = round_util(window);
    let remaining = (100 - used).max(0);
    format!(
        "{label}: {remaining}% remaining ({used}% used), resets {}",
        fmt_reset_iso(window)
    )
}

fn css_class(remaining: i64) -> &'static str {
    if remaining <= 10 {
        "danger"
    } else if remaining <= 30 {
        "warn"
    } else {
        "subscription"
    }
}

fn limit_level(remaining: i64) -> &'static str {
    if remaining <= 10 {
        "danger"
    } else if remaining <= 30 {
        "warn"
    } else {
        ""
    }
}

#[cfg(test)]
mod tests {
    use super::limit_level;

    #[test]
    fn notification_level_tracks_warning_and_danger_thresholds() {
        assert_eq!(limit_level(31), "");
        assert_eq!(limit_level(30), "warn");
        assert_eq!(limit_level(10), "danger");
        assert_eq!(limit_level(0), "danger");
    }
}

fn reset_has_passed(epoch: Option<i64>) -> bool {
    matches!(epoch, Some(e) if e != 0 && e <= now() - RESET_PAST_GRACE_SECONDS)
}

fn signal_waybar(signal: Option<i64>) {
    if let Some(s) = signal {
        let _ = Command::new("pkill")
            .args([
                "-RTMIN+".to_string() + &s.to_string(),
                "-x".into(),
                "waybar".into(),
            ])
            .status();
    }
}

// ── main display ──────────────────────────────────────────────────────────────

fn main_display(force_refresh: bool, force_network: bool) -> i32 {
    let oauth = read_oauth();
    let subscription = oauth
        .get("subscriptionType")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .unwrap_or("plan")
        .to_string();

    if oauth
        .get("accessToken")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .is_empty()
    {
        emit(
            AUTH_BUBBLE_TEXT,
            "Claude Code OAuth token not found.\nOpen quick settings, then use Claude Login.",
            "auth",
        );
        return 0;
    }

    let cached = if force_refresh {
        None
    } else {
        read_cache(Some(CACHE_TTL_SECONDS))
    };
    let mut refresh_error_text: Option<String> = None;
    let mut retry_at: Option<f64> = None;
    let mut stale = false;
    let mut cache_for_age: Option<Value> = cached.clone();

    let usage: Value = if let Some(c) = &cached {
        c.get("usage").cloned().unwrap_or(Value::Null)
    } else {
        let any = read_cache(None);
        cache_for_age = any.clone();
        let retry_pending = any
            .as_ref()
            .and_then(|c| c.get("retry_at").and_then(|v| v.as_f64()))
            .map(|r| r > now() as f64)
            .unwrap_or(false);
        if let (false, true, Some(c)) = (force_network, retry_pending, &any) {
            stale = true;
            refresh_error_text = Some(
                c.get("refresh_error_text")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
                    .unwrap_or("waiting before retry")
                    .to_string(),
            );
            retry_at = c.get("retry_at").and_then(|v| v.as_f64());
            c.get("usage").cloned().unwrap_or(Value::Null)
        } else {
            match read_usage(&oauth) {
                Ok(u) => {
                    write_cache(&u);
                    u
                }
                Err(e) => {
                    if let Some(c) = &any {
                        retry_at = Some(update_cache_retry(c, &e));
                        refresh_error_text = Some(describe_refresh_error(&e));
                        stale = true;
                        cache_for_age = any.clone();
                        c.get("usage").cloned().unwrap_or(Value::Null)
                    } else {
                        let (css, text) = match e {
                            FetchErr::Http { code: 429, .. } => ("rate", "rate"),
                            _ => ("error", "error"),
                        };
                        // No-cache + fetch failure is a rare first-run path; the
                        // exact error text can't mirror Python's raw exception.
                        emit(
                            text,
                            &format!(
                                "Could not read Claude subscription usage: {}\nNo cached usage is available yet.\n\n{CLAUDE_USAGE_URL}",
                                describe_refresh_error(&e)
                            ),
                            css,
                        );
                        return 0;
                    }
                }
            }
        }
    };

    let cache_age = if stale {
        let updated = cache_for_age
            .as_ref()
            .and_then(|c| c.get("updated_at").and_then(|v| v.as_f64()))
            .unwrap_or(0.0);
        ((now() as f64 - updated) / 60.0) as i64
    } else {
        0
    };

    if !usage.is_object() {
        emit(
            "error",
            &format!("Could not read Claude subscription usage: invalid cached response\n\n{CLAUDE_USAGE_URL}"),
            "error",
        );
        return 0;
    }

    let five_hour = usage
        .get("five_hour")
        .cloned()
        .filter(|v| v.is_object())
        .unwrap_or(json!({}));
    let seven_day = usage
        .get("seven_day")
        .cloned()
        .filter(|v| v.is_object())
        .unwrap_or(json!({}));

    let five_reset = iso_to_epoch(
        five_hour
            .get("resets_at")
            .and_then(|v| v.as_str())
            .unwrap_or(""),
    );
    let seven_reset = iso_to_epoch(
        seven_day
            .get("resets_at")
            .and_then(|v| v.as_str())
            .unwrap_or(""),
    );
    let primary_used = round_util(&five_hour);
    let primary_remaining = (100 - primary_used).max(0);
    let has_seven = !is_empty_obj(&seven_day);
    let weekly_remaining: Option<i64> = if has_seven {
        Some((100 - round_util(&seven_day)).max(0))
    } else {
        None
    };
    let extra = usage
        .get("extra_usage")
        .cloned()
        .filter(|v| v.is_object())
        .unwrap_or(json!({}));

    let mut lines = vec![
        format!("Claude subscription usage ({subscription})"),
        window_line("5h", &five_hour),
        window_line("weekly", &seven_day),
    ];
    if let Some(w) = weekly_remaining {
        lines.push(format!("weekly window: {w}% remaining"));
    }
    if !is_empty_obj(&extra) {
        let enabled = if extra
            .get("is_enabled")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        {
            "enabled"
        } else {
            "disabled"
        };
        lines.push(format!("extra usage: {enabled}"));
        if let Some(u) = extra.get("utilization").and_then(|v| v.as_f64()) {
            lines.push(format!("extra usage utilization: {}%", u.round() as i64));
        }
    }
    if stale {
        let mut reason = refresh_error_text
            .clone()
            .unwrap_or_else(|| "refresh failed".into());
        if let Some(r) = retry_at {
            if r > now() as f64 {
                reason = format!(
                    "{reason}; retry in {}",
                    compact_duration((r - now() as f64) as i64)
                );
            }
        }
        lines.push(format!(
            "showing cached usage from {cache_age}m ago; {reason}"
        ));
    }

    let display_remaining = if weekly_remaining == Some(0) {
        0
    } else {
        primary_remaining
    };
    let mut level = limit_level(display_remaining);
    let mut text = format!("{primary_remaining}%");
    let mut text_window = "5h window remaining %";
    let mut stale_expired_reset = false;
    let retry_limited = retry_at.map(|r| r > now() as f64).unwrap_or(false);

    if weekly_remaining == Some(0) {
        let countdown = compact_countdown(seven_reset);
        if !countdown.is_empty() {
            text = countdown;
            text_window = "weekly reset countdown";
        } else if stale && reset_has_passed(seven_reset) {
            text = "–".into();
            text_window = "stale weekly reset time";
            stale_expired_reset = true;
        }
    } else if primary_remaining == 0 {
        let countdown = compact_countdown(five_reset);
        if !countdown.is_empty() {
            text = countdown;
            text_window = "5h reset countdown";
        } else if stale && reset_has_passed(five_reset) {
            if let Some(w) = weekly_remaining {
                text = format!("{w}%");
                text_window = "weekly remaining (5h reset, awaiting refresh)";
            } else {
                text = "–".into();
                text_window = "stale 5h reset time";
            }
            stale_expired_reset = true;
        }
    }
    lines.push(format!("bar text shows {text_window}"));
    if stale_expired_reset {
        lines
            .push("cached reset time has passed; waiting for a fresh Claude usage response".into());
    }
    lines.push(String::new());
    lines.push(CLAUDE_USAGE_URL.to_string());
    if stale_expired_reset {
        level = "";
    }

    if !level.is_empty() {
        let reset_source = if weekly_remaining == Some(0) {
            &seven_day
        } else {
            &five_hour
        };
        // Claude uses a single OAuth account here (no slot system like Codex),
        // so the account key is empty; the per-account machinery is ready if
        // Claude multi-account is added later.
        maybe_notify(
            "Claude",
            "",
            level,
            display_remaining,
            &fmt_reset_iso(reset_source),
            &asset("claude.svg"),
        );
    }

    if stale_expired_reset {
        reset::cancel("Claude");
    } else if weekly_remaining == Some(0) {
        reset::schedule("Claude", "weekly", seven_reset, Some(&asset("claude.png")));
    } else if primary_remaining == 0 {
        reset::schedule("Claude", "5h", five_reset, Some(&asset("claude.png")));
    } else {
        reset::cancel("Claude");
    }

    let stale_class = if retry_limited { "rate" } else { "warn" };
    let class = if stale_expired_reset {
        stale_class
    } else {
        css_class(display_remaining)
    };
    emit(&text, &lines.join("\n"), class);
    0
}

/// `waybar-helper claude [--refresh] [--force-network] [--signal N]`
pub fn run(args: &[String]) -> ExitCode {
    let force_refresh = args.iter().any(|a| a == "--refresh");
    let force_network = args.iter().any(|a| a == "--force-network");
    let rc = main_display(force_refresh, force_network);
    let signal = args
        .iter()
        .position(|a| a == "--signal")
        .and_then(|i| args.get(i + 1))
        .and_then(|s| s.parse().ok());
    signal_waybar(signal);
    ExitCode::from(rc as u8)
}
