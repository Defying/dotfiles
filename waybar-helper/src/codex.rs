//! Codex subscription usage bubble, ported from `scripts/waybar-openai-tokens.py`.
//!
//! Display path reads the cache and renders instantly; if the cache is stale it
//! kicks off a detached background `--refresh`. Refresh asks the installed Codex
//! CLI for the ChatGPT rate-limit snapshot via the app-server JSON-RPC
//! (`account/rateLimits/read`) — the supported path that auto-refreshes the
//! token — then caches it, notifies on a worse level, and arms/cancels the
//! reset reminder. No OpenAI API keys, no Codex logs.

use std::cmp::Ordering;
use std::env;
use std::fs;
use std::io::{self, BufRead, BufReader, Write};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitCode, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use serde_json::{json, Value};

use crate::accounts::{self, Account};
use crate::reset;
use crate::usage::{
    asset, cache_dir, emit, emit_classes, fmt_reset, maybe_notify, now, AUTH_BUBBLE_TEXT,
};

const CODEX_USAGE_URL: &str = "https://chatgpt.com/codex/settings/usage";
const CACHE_MAX_AGE_SECONDS: i64 = 30;
const REQUEST_TIMEOUT_SECS: u64 = 10;
const REFRESH_LOCK_MAX_AGE_SECONDS: i64 = 120;

fn codex_bin() -> String {
    let bun = PathBuf::from(env::var("HOME").unwrap_or_default()).join(".bun/bin/codex");
    if bun.exists() {
        bun.to_string_lossy().into_owned()
    } else {
        "codex".into()
    }
}

fn usage_cache() -> PathBuf {
    cache_dir().join("codex-usage.json")
}
fn slot_key(slot: &str) -> String {
    let mut out = String::with_capacity(slot.len());
    let mut prev_dash = false;
    for c in slot.trim().to_lowercase().chars() {
        if c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-') {
            out.push(c);
            prev_dash = false;
        } else if !prev_dash {
            out.push('-');
            prev_dash = true;
        }
    }
    let trimmed = out.trim_matches(['-', '.']).to_string();
    if trimmed.is_empty() {
        "active".to_string()
    } else {
        trimmed
    }
}
fn slot_usage_cache(slot: &str) -> PathBuf {
    cache_dir().join(format!("codex-usage-{}.json", slot_key(slot)))
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

fn read_slot_cache(slot: &str) -> Value {
    fs::read_to_string(slot_usage_cache(slot))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null)
}

fn cache_age_seconds(cache: &Value) -> i64 {
    let updated = cache
        .get("updated_at")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
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
    if window.is_null()
        || !window.is_object()
        || window.as_object().map(|o| o.is_empty()).unwrap_or(true)
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

#[derive(Clone)]
struct LimitStatus {
    primary_remaining: i64,
    weekly_remaining: Option<i64>,
    reached: Option<String>,
    blocked_window: Option<&'static str>,
    blocked_reset: Option<i64>,
    weekly_blocked: bool,
    primary_blocked: bool,
}

impl LimitStatus {
    fn blocked(&self) -> bool {
        self.blocked_window.is_some()
    }

    fn display_remaining(&self) -> i64 {
        if self.blocked() {
            0
        } else {
            self.primary_remaining
        }
    }

    fn window_label(&self) -> &'static str {
        self.blocked_window.unwrap_or("5h")
    }
}

fn bar_text_for_status(status: &LimitStatus) -> String {
    format!("{}%", status.display_remaining())
}

fn limit_status(limits: &Value) -> LimitStatus {
    let primary = limits.get("primary").cloned().unwrap_or(Value::Null);
    let secondary = limits.get("secondary").cloned().unwrap_or(Value::Null);
    let reached = reached_of(limits);
    let reached_b = reached.is_some();

    let primary_used = used_percent(&primary, "usedPercent");
    let primary_remaining = (100 - primary_used).max(0);
    let primary_reset = primary.get("resetsAt").and_then(|v| v.as_i64());
    let has_secondary =
        secondary.is_object() && !secondary.as_object().map(|o| o.is_empty()).unwrap_or(true);
    let weekly_remaining = if has_secondary {
        Some((100 - used_percent(&secondary, "usedPercent")).max(0))
    } else {
        None
    };
    let weekly_reset = secondary.get("resetsAt").and_then(|v| v.as_i64());

    let weekly_blocked = matches!(weekly_remaining, Some(w) if w <= 0);
    let primary_blocked = primary_remaining <= 0 || (reached_b && !weekly_blocked);
    let (blocked_window, blocked_reset) = if weekly_blocked {
        (Some("weekly"), weekly_reset)
    } else if primary_blocked {
        (Some("5h"), primary_reset)
    } else {
        (None, None)
    };

    LimitStatus {
        primary_remaining,
        weekly_remaining,
        reached,
        blocked_window,
        blocked_reset,
        weekly_blocked,
        primary_blocked,
    }
}

fn css_class_for_status(status: &LimitStatus) -> &'static str {
    if status.weekly_blocked {
        "subscription"
    } else if status.primary_blocked || status.primary_remaining <= 10 {
        "danger"
    } else if status.primary_remaining <= 30 {
        "warn"
    } else {
        "subscription"
    }
}

fn limit_level_for_status(status: &LimitStatus) -> &'static str {
    if status.weekly_blocked {
        ""
    } else if status.primary_blocked || status.primary_remaining <= 10 {
        "danger"
    } else if status.primary_remaining <= 30 {
        "warn"
    } else {
        ""
    }
}

#[cfg(test)]
mod tests {
    use super::{
        bar_text_for_account, bar_text_for_status, block_notify_state, compare_ranked_accounts,
        css_class_for_status, legacy_block_notify_file_matches, limit_level_for_status,
        limit_status, AccountSnapshot, RankedAccount,
    };
    use crate::accounts::Account;
    use serde_json::json;

    #[test]
    fn notification_level_tracks_warning_and_danger_thresholds() {
        let healthy = limit_status(&json!({"primary": {"usedPercent": 52}}));
        assert_eq!(limit_level_for_status(&healthy), "");
        let warn = limit_status(&json!({"primary": {"usedPercent": 70}}));
        assert_eq!(limit_level_for_status(&warn), "warn");
        let danger = limit_status(&json!({"primary": {"usedPercent": 90}}));
        assert_eq!(limit_level_for_status(&danger), "danger");
    }

    #[test]
    fn weekly_exhaustion_stays_visually_calm() {
        let weekly = limit_status(&json!({
            "rateLimitReachedType": "rate_limit_reached",
            "primary": {"usedPercent": 42, "resetsAt": 2000},
            "secondary": {"usedPercent": 100, "resetsAt": 9000, "windowDurationMins": 10080}
        }));
        assert_eq!(weekly.blocked_window, Some("weekly"));
        assert_eq!(limit_level_for_status(&weekly), "");
        assert_eq!(css_class_for_status(&weekly), "subscription");
    }

    #[test]
    fn blocked_bar_text_stays_percent_not_countdown() {
        let blocked = limit_status(&json!({
            "rateLimitReachedType": "rate_limit_reached",
            "primary": {"usedPercent": 100, "resetsAt": 1787331600, "windowDurationMins": 300}
        }));
        assert_eq!(bar_text_for_status(&blocked), "0%");
    }

    #[test]
    fn blocked_notification_state_tolerates_reset_jitter() {
        let blocked = limit_status(&json!({
            "rateLimitReachedType": "rate_limit_reached",
            "primary": {"usedPercent": 100, "resetsAt": 1787331600, "windowDurationMins": 300}
        }));
        let jittered = limit_status(&json!({
            "rateLimitReachedType": "rate_limit_reached",
            "primary": {"usedPercent": 100, "resetsAt": 1787331601, "windowDurationMins": 300}
        }));
        assert_eq!(block_notify_state(&blocked), block_notify_state(&jittered));
    }

    #[test]
    fn legacy_block_notify_file_matches_same_reset_bucket() {
        let prefix = "codex-block-account-5h-";
        assert!(legacy_block_notify_file_matches(
            prefix,
            1787331600 / 300,
            "codex-block-account-5h-1787331601.notify"
        ));
        assert!(!legacy_block_notify_file_matches(
            prefix,
            1787331600 / 300,
            "codex-block-account-5h-1787331901.notify"
        ));
    }

    #[test]
    fn active_bar_text_hides_account_label() {
        let account = Account {
            account_id: "7b0d6a0e-0000-0000-0000-000000000000".to_string(),
            label: "ben@carveworkshop.com".to_string(),
            slot: Some("ben-7b0d6a0e".to_string()),
            ..Default::default()
        };
        let status = limit_status(&json!({"primary": {"usedPercent": 5}}));

        assert_eq!(bar_text_for_account(&status, &account, true), "95%");
        assert_eq!(bar_text_for_account(&status, &account, false), "95%");
    }

    #[test]
    fn usable_account_sorts_before_blocked_active_account() {
        let blocked_account = Account {
            label: "other".to_string(),
            slot: Some("other".to_string()),
            ..Default::default()
        };
        let usable_account = Account {
            label: "ben".to_string(),
            slot: Some("ben-7b0d6a0e".to_string()),
            ..Default::default()
        };
        let mut accounts = [
            RankedAccount {
                snapshot: Some(AccountSnapshot {
                    status: limit_status(&json!({
                        "rateLimitReachedType": "rate_limit_reached",
                        "primary": {"usedPercent": 100, "resetsAt": 2000}
                    })),
                    account: blocked_account.clone(),
                }),
                account: blocked_account,
                active: true,
            },
            RankedAccount {
                snapshot: Some(AccountSnapshot {
                    status: limit_status(&json!({"primary": {"usedPercent": 20}})),
                    account: usable_account.clone(),
                }),
                account: usable_account,
                active: false,
            },
        ];

        accounts.sort_by(compare_ranked_accounts);

        assert_eq!(accounts[0].account.slot.as_deref(), Some("ben-7b0d6a0e"));
    }

    #[test]
    fn defying_sorts_first_even_when_auth_is_unusable() {
        let defying = Account {
            label: "defying".to_string(),
            slot: Some("defying".to_string()),
            ..Default::default()
        };
        let usable_account = Account {
            label: "ben".to_string(),
            slot: Some("ben-7b0d6a0e".to_string()),
            ..Default::default()
        };
        let mut accounts = [
            RankedAccount {
                snapshot: Some(AccountSnapshot {
                    status: limit_status(&json!({"primary": {"usedPercent": 20}})),
                    account: usable_account.clone(),
                }),
                account: usable_account,
                active: true,
            },
            RankedAccount {
                snapshot: None,
                account: defying,
                active: false,
            },
        ];

        accounts.sort_by(compare_ranked_accounts);

        assert_eq!(accounts[0].account.slot.as_deref(), Some("defying"));
    }
}

struct EmitOpts {
    stale_age: Option<i64>,
    refresh_error: Option<String>,
    notify: bool,
    active: bool,
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
    let status = limit_status(limits);
    let display = status.display_remaining();
    let level = limit_level_for_status(&status);
    let block_window = if status.weekly_blocked {
        &secondary
    } else {
        &primary
    };

    let text = bar_text_for_account(&status, account, opts.active);

    let credit_line = if credits
        .get("unlimited")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        "credits: unlimited".to_string()
    } else {
        let bal = match credits.get("balance") {
            Some(Value::String(s)) => s.clone(),
            Some(Value::Number(n)) => n.to_string(),
            _ => "0".to_string(),
        };
        format!("credits: {bal}")
    };
    let reset_label = fmt_reset(if status.blocked() {
        status.blocked_reset
    } else {
        block_window.get("resetsAt").and_then(|v| v.as_i64())
    });

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
        if status.weekly_blocked && display == 0 {
            "bar shows weekly window remaining %"
        } else if display == 0 {
            "bar shows 5h window remaining %"
        } else {
            "bar shows 5h window remaining %"
        }
        .to_string(),
    );
    if let Some(line) = comparison_line(account, &status) {
        lines.push(line);
    }
    if let Some(age) = opts.stale_age {
        if opts.refresh_error.is_some() {
            lines.push(format!("cached {age}s ago; refresh failed recently"));
        } else {
            lines.push(format!("cached {age}s ago; refreshing in background"));
        }
    }
    if let Some(err) = &opts.refresh_error {
        lines.push(format!("last refresh failed: {err}"));
    }
    if let Some(r) = &status.reached {
        lines.push(format!("limit state: {r}"));
    }
    lines.push(String::new());
    lines.push(CODEX_USAGE_URL.to_string());

    if opts.notify {
        // Per-account notify state so each saved Codex account is tracked
        // independently (email is the stable key; fall back to label/slot).
        let acct_name = if !account.email.is_empty() {
            account.email.clone()
        } else if !account.label.is_empty() {
            account.label.clone()
        } else {
            account.slot.clone().unwrap_or_default()
        };
        if status.blocked() {
            notify_blocked(account, &status, comparison_line(account, &status));
            sync_reset_for_account(account, &status);
        } else {
            maybe_notify(
                "Codex",
                &acct_name,
                level,
                display,
                &reset_label,
                &asset("openai.svg"),
            );
            reset::cancel(&account_service_name(account));
        }
    }

    let class = css_class_for_status(&status);
    if opts.active {
        emit_classes(&text, &lines.join("\n"), &[class, "active"]);
    } else {
        emit(&text, &lines.join("\n"), class);
    }
}

// ── display entrypoint ────────────────────────────────────────────────────────

fn account_from_cache(cache: &Value) -> Account {
    cache
        .get("account")
        .cloned()
        .and_then(|v| serde_json::from_value(v).ok())
        .unwrap_or_default()
}

#[derive(Clone)]
struct AccountSnapshot {
    account: Account,
    status: LimitStatus,
}

struct RankedAccount {
    account: Account,
    snapshot: Option<AccountSnapshot>,
    active: bool,
}

fn account_identity(account: &Account) -> String {
    if !account.account_id.is_empty() {
        return account.account_id.clone();
    }
    if let Some(slot) = &account.slot {
        if !slot.is_empty() {
            return slot.clone();
        }
    }
    if !account.email.is_empty() {
        return account.email.clone();
    }
    account.label.clone()
}

fn account_short_label(account: &Account) -> String {
    if !account.label.is_empty() {
        account.label.clone()
    } else if !account.email.is_empty() {
        account.email.clone()
    } else if let Some(slot) = &account.slot {
        slot.clone()
    } else {
        "Codex account".to_string()
    }
}

fn bar_text_for_account(status: &LimitStatus, _account: &Account, _active: bool) -> String {
    bar_text_for_status(status)
}

fn account_service_name(account: &Account) -> String {
    format!("Codex {}", account_short_label(account))
}

fn slot_is_active(slot: Option<&str>) -> bool {
    let active_slot = accounts::read_active_slot();
    !active_slot.is_empty() && slot == Some(active_slot.as_str())
}

fn account_is_active(account: &Account, slot_hint: Option<&str>) -> bool {
    if slot_is_active(slot_hint) {
        return true;
    }
    slot_is_active(account.slot.as_deref())
}

fn is_auth_failure_message(message: &str) -> bool {
    let lower = message.to_lowercase();
    lower.contains("401 unauthorized")
        || lower.contains("token_revoked")
        || lower.contains("token_invalidated")
        || lower.contains("invalidated oauth token")
        || lower.contains("authentication token has been invalidated")
}

fn cache_has_auth_failure(cache: &Value) -> bool {
    cache.get("error").and_then(|v| v.as_str()) == Some("auth")
        || cache
            .get("refresh_error")
            .and_then(|v| v.as_str())
            .is_some_and(is_auth_failure_message)
}

fn snapshot_from_cache(cache: &Value) -> Option<AccountSnapshot> {
    if cache_has_auth_failure(cache) {
        return None;
    }
    let limits = cache.get("limits").cloned().unwrap_or(Value::Null);
    if !limits.is_object() || limits.as_object().map(|o| o.is_empty()).unwrap_or(true) {
        return None;
    }
    let account = account_from_cache(cache);
    if account.is_empty() {
        return None;
    }
    Some(AccountSnapshot {
        status: limit_status(&limits),
        account,
    })
}

fn cached_snapshot_for_account(account: &Account) -> Option<AccountSnapshot> {
    let slot = account.slot.as_deref()?;
    let slot_cache = read_slot_cache(slot);
    if let Some(mut snap) = snapshot_from_cache(&slot_cache) {
        if snap.account.slot.is_none() {
            snap.account.slot = Some(slot.to_string());
        }
        return Some(snap);
    }
    let active_cache = read_cache();
    let snap = snapshot_from_cache(&active_cache)?;
    if account_identity(&snap.account) == account_identity(account) {
        return Some(snap);
    }
    None
}

fn usability_tier(snapshot: Option<&AccountSnapshot>) -> i32 {
    match snapshot {
        Some(snapshot) if !snapshot.status.blocked() => 0,
        Some(_) => 1,
        None => 2,
    }
}

fn account_sort_label(account: &Account) -> String {
    if !account.label.is_empty() {
        account.label.to_lowercase()
    } else if !account.email.is_empty() {
        account.email.to_lowercase()
    } else if let Some(slot) = &account.slot {
        slot.to_lowercase()
    } else {
        String::new()
    }
}

fn compare_ranked_accounts(a: &RankedAccount, b: &RankedAccount) -> Ordering {
    let tier_a = usability_tier(a.snapshot.as_ref());
    let tier_b = usability_tier(b.snapshot.as_ref());
    account_priority(&a.account)
        .cmp(&account_priority(&b.account))
        .then_with(|| {
            tier_a.cmp(&tier_b).then_with(|| match tier_a {
                0 => {
                    let status_a = &a.snapshot.as_ref().unwrap().status;
                    let status_b = &b.snapshot.as_ref().unwrap().status;
                    status_b
                        .primary_remaining
                        .cmp(&status_a.primary_remaining)
                        .then_with(|| {
                            status_b
                                .weekly_remaining
                                .unwrap_or(-1)
                                .cmp(&status_a.weekly_remaining.unwrap_or(-1))
                        })
                }
                1 => {
                    let status_a = &a.snapshot.as_ref().unwrap().status;
                    let status_b = &b.snapshot.as_ref().unwrap().status;
                    status_a
                        .blocked_reset
                        .unwrap_or(i64::MAX)
                        .cmp(&status_b.blocked_reset.unwrap_or(i64::MAX))
                }
                _ => Ordering::Equal,
            })
        })
        .then_with(|| b.active.cmp(&a.active))
        .then_with(|| account_sort_label(&a.account).cmp(&account_sort_label(&b.account)))
}

fn account_priority(account: &Account) -> i32 {
    let slot = account.slot.as_deref().unwrap_or("").to_lowercase();
    let label = account_sort_label(account);
    if slot == "defying" || label == "defying" || label == "defying@me.com" {
        0
    } else {
        1
    }
}

fn ranked_accounts() -> Vec<RankedAccount> {
    let active_slot = accounts::read_active_slot();
    let mut ranked: Vec<RankedAccount> = accounts::list_accounts()
        .into_iter()
        .map(|account| {
            let active = account.slot.as_deref() == Some(active_slot.as_str());
            let snapshot = cached_snapshot_for_account(&account);
            RankedAccount {
                account,
                snapshot,
                active,
            }
        })
        .collect();
    ranked.sort_by(compare_ranked_accounts);
    ranked
}

fn slot_for_rank(rank: usize) -> Option<String> {
    ranked_accounts().get(rank)?.account.slot.clone()
}

fn cached_other_snapshots(active: &Account) -> Vec<AccountSnapshot> {
    let active_id = account_identity(active);
    accounts::list_accounts()
        .into_iter()
        .filter(|a| account_identity(a) != active_id)
        .filter_map(|a| cached_snapshot_for_account(&a))
        .collect()
}

fn comparison_line(active: &Account, status: &LimitStatus) -> Option<String> {
    if !status.blocked() {
        return None;
    }
    let active_reset = status.blocked_reset;
    let mut best: Option<(i32, i64, String)> = None;
    for snap in cached_other_snapshots(active) {
        let label = account_short_label(&snap.account);
        if !snap.status.blocked() {
            let line = format!(
                "other account available now: {label} (5h {}%, weekly {}%)",
                snap.status.primary_remaining,
                snap.status
                    .weekly_remaining
                    .map(|w| w.to_string())
                    .unwrap_or_else(|| "?".to_string())
            );
            let rank = (0, 0, line);
            if best.as_ref().map(|b| rank.0 < b.0).unwrap_or(true) {
                best = Some(rank);
            }
            continue;
        }
        let Some(other_reset) = snap.status.blocked_reset else {
            continue;
        };
        let sooner = active_reset.map(|a| other_reset < a).unwrap_or(true);
        if !sooner {
            continue;
        }
        let line = format!(
            "other account resets sooner: {label} {} at {}",
            snap.status.window_label(),
            fmt_reset(Some(other_reset))
        );
        let rank = (1, other_reset, line);
        if best
            .as_ref()
            .map(|b| rank.0 < b.0 || (rank.0 == b.0 && rank.1 < b.1))
            .unwrap_or(true)
        {
            best = Some(rank);
        }
    }
    best.map(|(_, _, line)| line)
}

fn notify_once_with_state(
    key: &str,
    state: Value,
    title: &str,
    body: &str,
    urgency: &str,
    timeout: &str,
    icon: &str,
) {
    let dir = cache_dir();
    let _ = fs::create_dir_all(&dir);
    let state_file = dir.join(format!("{}.notify", slot_key(key)));
    let same = fs::read_to_string(&state_file)
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .map(|v| v == state)
        .unwrap_or(false);
    if same {
        return;
    }
    let sync = format!(
        "string:x-canonical-private-synchronous:ai-usage-{}",
        slot_key(key)
    );
    let mut args: Vec<String> = [
        "notify-send",
        "-a",
        "AI usage",
        "-u",
        urgency,
        "-t",
        timeout,
        "-h",
        &sync,
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    let notify_icon = if Path::new(icon).exists() {
        icon.to_string()
    } else {
        asset("openai.svg")
    };
    args.push("-i".into());
    args.push(notify_icon);
    args.push(title.to_string());
    args.push(body.to_string());
    let _ = Command::new("setsid")
        .arg("-f")
        .args(&args)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    let _ = fs::write(&state_file, state.to_string());
}

fn notify_once(key: &str, title: &str, body: &str, urgency: &str, timeout: &str, icon: &str) {
    notify_once_with_state(
        key,
        json!({ "title": title, "body": body, "urgency": urgency }),
        title,
        body,
        urgency,
        timeout,
        icon,
    );
}

fn block_notify_key(account: &Account, window: &str) -> String {
    format!("codex-block-{}-{window}", account_identity(account))
}

fn legacy_block_notify_file_matches(prefix: &str, reset_bucket: i64, name: &str) -> bool {
    let Some(rest) = name.strip_prefix(prefix) else {
        return false;
    };
    let Some(raw_reset) = rest.strip_suffix(".notify") else {
        return false;
    };
    raw_reset
        .parse::<i64>()
        .map(|reset| reset / 300 == reset_bucket)
        .unwrap_or(false)
}

fn migrate_legacy_block_notify_state(account: &Account, status: &LimitStatus) {
    let Some(reset) = status.blocked_reset else {
        return;
    };
    let window = status.window_label();
    let stable_file = cache_dir().join(format!(
        "{}.notify",
        slot_key(&block_notify_key(account, window))
    ));
    if stable_file.exists() {
        return;
    }
    let prefix = format!(
        "{}-",
        slot_key(&format!(
            "codex-block-{}-{window}",
            account_identity(account)
        ))
    );
    let reset_bucket = reset / 300;
    let seen = fs::read_dir(cache_dir())
        .ok()
        .into_iter()
        .flat_map(|entries| entries.filter_map(Result::ok))
        .filter_map(|entry| entry.file_name().into_string().ok())
        .any(|name| legacy_block_notify_file_matches(&prefix, reset_bucket, &name));
    if seen {
        let _ = fs::create_dir_all(cache_dir());
        let _ = fs::write(stable_file, block_notify_state(status).to_string());
    }
}

fn clear_notify_state(key: &str) {
    let _ = fs::remove_file(cache_dir().join(format!("{}.notify", slot_key(key))));
}

fn block_notify_state(status: &LimitStatus) -> Value {
    json!({
        "state": "blocked",
        "window": status.window_label(),
        "reset_bucket": status.blocked_reset.unwrap_or_default() / 300,
    })
}

fn notify_blocked(account: &Account, status: &LimitStatus, comparison: Option<String>) {
    if !status.blocked() {
        return;
    }
    let label = account_short_label(account);
    let window = status.window_label();
    let reset = fmt_reset(status.blocked_reset);
    let mut body = format!("{label}: {window} limit resets {reset}");
    if let Some(line) = comparison {
        body.push('\n');
        body.push_str(&line);
    }
    let title = if status.weekly_blocked {
        "Codex weekly limit"
    } else {
        "Codex usage limit"
    };
    let urgency = if status.weekly_blocked {
        "normal"
    } else {
        "critical"
    };
    let timeout = if status.weekly_blocked { "12000" } else { "0" };
    let key = block_notify_key(account, window);
    migrate_legacy_block_notify_state(account, status);
    notify_once_with_state(
        &key,
        block_notify_state(status),
        title,
        &body,
        urgency,
        timeout,
        &asset("openai.svg"),
    );
}

fn notify_lifted(account: &Account, previous: &LimitStatus, current: &LimitStatus) {
    if !previous.blocked() || current.blocked() {
        return;
    }
    let window = previous.window_label();
    let label = account_short_label(account);
    clear_notify_state(&block_notify_key(account, window));
    let title = "Codex limit reset";
    let body = format!("{label}: {window} window is available again");
    let key = format!(
        "codex-lifted-{}-{}-{}",
        account_identity(account),
        window,
        previous.blocked_reset.unwrap_or_default()
    );
    notify_once(&key, title, &body, "normal", "12000", &asset("openai.svg"));
}

fn sync_reset_for_account(account: &Account, status: &LimitStatus) {
    let service = account_service_name(account);
    if let Some(window) = status.blocked_window {
        reset::schedule(
            &service,
            window,
            status.blocked_reset,
            Some(&asset("openai.png")),
        );
    } else {
        reset::cancel(&service);
    }
}

fn account_for_slot(slot: &str) -> Account {
    accounts::list_accounts()
        .into_iter()
        .find(|a| a.slot.as_deref() == Some(slot))
        .unwrap_or_default()
}

fn spawn_refresh_for_stale_cache() {
    spawn_background_refresh();
}

fn should_spawn_refresh(cache: &Value, age: i64) -> bool {
    if age < CACHE_MAX_AGE_SECONDS {
        return false;
    }
    let last_attempt = cache
        .get("last_refresh_attempt_at")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0) as i64;
    last_attempt == 0 || now().saturating_sub(last_attempt) >= REFRESH_LOCK_MAX_AGE_SECONDS
}

fn emit_cached_or_placeholder(slot: Option<&str>) -> ExitCode {
    let cache = slot.map(read_slot_cache).unwrap_or_else(read_cache);
    let active = slot_is_active(slot);
    // Python `if not cache:` — missing OR an empty object both mean "no cache yet".
    let empty = cache.as_object().map(|o| o.is_empty()).unwrap_or(true);
    if empty {
        spawn_refresh_for_stale_cache();
        let tooltip = slot
            .map(|s| format!("Codex usage refresh started\naccount slot: {s}"))
            .unwrap_or_else(|| "Codex usage refresh started".to_string());
        if active {
            emit_classes("...", &tooltip, &["refreshing", "active"]);
        } else {
            emit("...", &tooltip, "refreshing");
        }
        return ExitCode::SUCCESS;
    }

    let age = cache_age_seconds(&cache);
    if should_spawn_refresh(&cache, age) {
        spawn_refresh_for_stale_cache();
    }
    let stale_suffix = if age >= CACHE_MAX_AGE_SECONDS {
        format!("cached {age}s ago; refreshing in background")
    } else {
        String::new()
    };

    let error = cache.get("error").and_then(|v| v.as_str());
    let limits = cache.get("limits").cloned().unwrap_or(Value::Null);
    let has_limits =
        limits.is_object() && !limits.as_object().map(|o| o.is_empty()).unwrap_or(true);

    if error == Some("auth") {
        let account = {
            let a = account_from_cache(&cache);
            if a.is_empty() {
                slot.map(account_for_slot)
                    .unwrap_or_else(accounts::active_account)
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
            cache
                .get("refresh_error")
                .and_then(|v| v.as_str())
                .map(|err| format!("last refresh failed: {err}"))
                .unwrap_or_default(),
            String::new(),
            "Open quick settings, then use Account or Login.".to_string(),
        ];
        if account_is_active(&account, slot) {
            emit_classes(
                AUTH_BUBBLE_TEXT,
                lines.join("\n").trim(),
                &["auth", "active"],
            );
        } else {
            emit(AUTH_BUBBLE_TEXT, lines.join("\n").trim(), "auth");
        }
        return ExitCode::SUCCESS;
    }

    if cache_has_auth_failure(&cache) {
        let account = {
            let a = account_from_cache(&cache);
            if a.is_empty() {
                slot.map(account_for_slot)
                    .unwrap_or_else(accounts::active_account)
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
        let err = cache
            .get("refresh_error")
            .and_then(|v| v.as_str())
            .unwrap_or("Codex authentication failed");
        let lines = [
            format!("Codex account: {label}"),
            status.to_string(),
            stale_suffix.clone(),
            format!("last refresh failed: {err}"),
            String::new(),
            "Open quick settings, then use Account or Login.".to_string(),
        ];
        if account_is_active(&account, slot) {
            emit_classes(
                AUTH_BUBBLE_TEXT,
                lines.join("\n").trim(),
                &["auth", "active"],
            );
        } else {
            emit(AUTH_BUBBLE_TEXT, lines.join("\n").trim(), "auth");
        }
        return ExitCode::SUCCESS;
    }

    if let Some(err) = error {
        if has_limits {
            let mut account = account_from_cache(&cache);
            if account.slot.is_none() {
                if let Some(slot) = slot {
                    account.slot = Some(slot.to_string());
                }
            }
            let active = account_is_active(&account, slot);
            emit_usage(
                &limits,
                &account,
                EmitOpts {
                    stale_age: Some(age),
                    refresh_error: Some(err.to_string()),
                    notify: false,
                    active,
                },
            );
            return ExitCode::SUCCESS;
        }
        let status = cache
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("Codex status unavailable");
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

    let account = {
        let mut account = account_from_cache(&cache);
        if account.is_empty() {
            if let Some(slot) = slot {
                account = account_for_slot(slot);
            }
        } else if account.slot.is_none() {
            if let Some(slot) = slot {
                account.slot = Some(slot.to_string());
            }
        }
        account
    };
    let active = account_is_active(&account, slot);
    emit_usage(
        &limits,
        &account,
        EmitOpts {
            stale_age: if age >= CACHE_MAX_AGE_SECONDS {
                Some(age)
            } else {
                None
            },
            refresh_error: cache
                .get("refresh_error")
                .and_then(|v| v.as_str())
                .map(String::from),
            notify: false,
            active,
        },
    );
    ExitCode::SUCCESS
}

// ── refresh path ──────────────────────────────────────────────────────────────

fn codex_login_status_for_home(codex_home: Option<&Path>) -> String {
    let mut cmd = Command::new("timeout");
    cmd.args(["4", &codex_bin(), "login", "status"]);
    if let Some(home) = codex_home {
        cmd.env("CODEX_HOME", home);
    }
    cmd.output()
        .ok()
        .map(|o| {
            let mut s = String::from_utf8_lossy(&o.stdout).to_string();
            s.push_str(&String::from_utf8_lossy(&o.stderr)); // Python merged stderr→stdout
            s.trim().to_string()
        })
        .unwrap_or_default()
}

/// One `account/rateLimits/read` over the app-server JSON-RPC (stdio).
fn read_rate_limits_for_home(codex_home: Option<&Path>) -> Result<Value, String> {
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

    let mut cmd = Command::new(codex_bin());
    cmd.args(["app-server", "--listen", "stdio://"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    unsafe {
        cmd.pre_exec(|| {
            if libc::setpgid(0, 0) == 0 {
                Ok(())
            } else {
                Err(io::Error::last_os_error())
            }
        });
    }
    if let Some(home) = codex_home {
        cmd.env("CODEX_HOME", home);
    }
    let mut child = cmd
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
    terminate_app_server(&mut child);
    drop(stdin);
    outcome
}

fn terminate_app_server(child: &mut Child) {
    let pid = child.id() as libc::pid_t;
    let pgid = unsafe { libc::getpgid(pid) };
    if pgid == pid {
        unsafe {
            libc::kill(-pid, libc::SIGTERM);
        }
    } else {
        let _ = child.kill();
    }

    for _ in 0..10 {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }

    if pgid == pid {
        unsafe {
            libc::kill(-pid, libc::SIGKILL);
        }
    } else {
        let _ = child.kill();
    }
    let _ = child.wait();
}

fn pick_codex_limits(data: &Value) -> Value {
    if let Some(c) = data.get("rateLimitsByLimitId").and_then(|b| b.get("codex")) {
        return c.clone();
    }
    data.get("rateLimits").cloned().unwrap_or(json!({}))
}

fn timestamped_payload(payload: Value) -> Value {
    // {"updated_at": now, ...payload}
    let mut obj = serde_json::Map::new();
    obj.insert("updated_at".into(), json!(now() as f64));
    if let Value::Object(m) = payload {
        for (k, v) in m {
            obj.insert(k, v);
        }
    }
    Value::Object(obj)
}

fn write_usage_cache(payload: Value) {
    let payload = timestamped_payload(payload);
    let _ = fs::create_dir_all(cache_dir());
    let _ = fs::write(usage_cache(), payload.to_string());
}

fn write_slot_usage_cache(slot: &str, payload: Value) {
    let payload = timestamped_payload(payload);
    let _ = fs::create_dir_all(cache_dir());
    let _ = fs::write(slot_usage_cache(slot), payload.to_string());
}

fn write_cache_raw(payload: &Value) {
    let _ = fs::create_dir_all(cache_dir());
    let _ = fs::write(usage_cache(), payload.to_string());
}

fn write_slot_cache_raw(slot: &str, payload: &Value) {
    let _ = fs::create_dir_all(cache_dir());
    let _ = fs::write(slot_usage_cache(slot), payload.to_string());
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

fn slot_payload(account: &Account, limits: Value, status: &str) -> Value {
    json!({
        "limits": limits,
        "account": serde_json::to_value(account).unwrap_or(Value::Null),
        "status": status
    })
}

fn refresh_one_account(account: &Account, active: bool) -> Option<AccountSnapshot> {
    let slot = account.slot.as_deref()?;
    let home = accounts::slot_dir(slot);
    let codex_home = if active { None } else { Some(home.as_path()) };
    let status_text = codex_login_status_for_home(codex_home);
    if !status_text.contains("Logged in") {
        let payload = json!({
            "error": "auth",
            "status": status_text,
            "account": serde_json::to_value(account).unwrap_or(Value::Null)
        });
        if active {
            write_usage_cache(payload.clone());
        }
        write_slot_usage_cache(slot, payload);
        return None;
    }

    let data = match read_rate_limits_for_home(codex_home) {
        Ok(d) => d,
        Err(exc) => {
            if is_auth_failure_message(&exc) {
                let payload = json!({
                    "error": "auth",
                    "refresh_error": exc,
                    "last_refresh_attempt_at": now() as f64,
                    "status": status_text,
                    "account": serde_json::to_value(account).unwrap_or(Value::Null)
                });
                if active {
                    write_usage_cache(payload.clone());
                }
                write_slot_usage_cache(slot, payload);
                return None;
            }
            let previous = read_slot_cache(slot);
            let prev_limits = previous.get("limits").cloned().unwrap_or(Value::Null);
            let has_limits = prev_limits.is_object()
                && !prev_limits
                    .as_object()
                    .map(|o| o.is_empty())
                    .unwrap_or(true);
            if has_limits {
                let mut patched = previous.clone();
                if let Value::Object(ref mut m) = patched {
                    m.remove("error");
                    m.insert("refresh_error".into(), json!(exc));
                    m.insert("last_refresh_attempt_at".into(), json!(now() as f64));
                    m.insert("status".into(), json!(status_text));
                    m.insert(
                        "account".into(),
                        serde_json::to_value(account).unwrap_or(Value::Null),
                    );
                }
                if active {
                    write_cache_raw(&patched);
                } else {
                    write_slot_cache_raw(slot, &patched);
                }
                return snapshot_from_cache(&patched);
            }
            let payload = json!({
                "error": exc,
                "status": status_text,
                "account": serde_json::to_value(account).unwrap_or(Value::Null)
            });
            if active {
                write_usage_cache(payload.clone());
            }
            write_slot_usage_cache(slot, payload);
            return None;
        }
    };

    let limits = pick_codex_limits(&data);
    if !limits.is_object() || limits.as_object().map(|o| o.is_empty()).unwrap_or(true) {
        let payload = json!({
            "error": "missing_limits",
            "status": status_text,
            "account": serde_json::to_value(account).unwrap_or(Value::Null)
        });
        if active {
            write_usage_cache(payload.clone());
        }
        write_slot_usage_cache(slot, payload);
        return None;
    }
    let previous = read_slot_cache(slot);
    let previous_status = snapshot_from_cache(&previous).map(|s| s.status);
    let current_status = limit_status(&limits);
    let payload = slot_payload(account, limits.clone(), &status_text);
    if active {
        write_usage_cache(payload.clone());
    }
    write_slot_usage_cache(slot, payload);
    if let Some(prev) = previous_status {
        notify_lifted(account, &prev, &current_status);
    }
    sync_reset_for_account(account, &current_status);
    Some(AccountSnapshot {
        account: account.clone(),
        status: current_status,
    })
}

fn refresh_other_accounts(active: &Account, force: bool) {
    let active_id = account_identity(active);
    for account in accounts::list_accounts() {
        if account_identity(&account) == active_id {
            continue;
        }
        let stale = account
            .slot
            .as_deref()
            .map(read_slot_cache)
            .map(|c| {
                c.as_object().map(|o| o.is_empty()).unwrap_or(true)
                    || snapshot_from_cache(&c).is_none()
                    || cache_age_seconds(&c) >= 600
            })
            .unwrap_or(true);
        if force || stale {
            let _ = refresh_one_account(&account, false);
        } else if let Some(snap) = cached_snapshot_for_account(&account) {
            sync_reset_for_account(&snap.account, &snap.status);
        }
    }
}

fn refresh_usage() {
    let account = accounts::sync_active_slot().unwrap_or_else(|_| accounts::active_account());
    let account_label = if account.is_empty() {
        "active account".to_string()
    } else {
        accounts::display_label(&account)
    };

    let status = codex_login_status_for_home(None);
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
        emit(AUTH_BUBBLE_TEXT, &lines.join("\n"), "auth");
        write_usage_cache(json!({
            "error": "auth", "status": status,
            "account": serde_json::to_value(&account).unwrap_or(Value::Null)
        }));
        return;
    }

    let data = match read_rate_limits_for_home(None) {
        Ok(d) => d,
        Err(exc) => {
            if is_auth_failure_message(&exc) {
                let lines = [
                    format!("Codex account: {account_label}"),
                    status.clone(),
                    format!("last refresh failed: {exc}"),
                    String::new(),
                    "Open quick settings, then use Account or Login.".to_string(),
                ];
                emit(AUTH_BUBBLE_TEXT, &lines.join("\n"), "auth");
                write_usage_cache(json!({
                    "error": "auth",
                    "refresh_error": exc,
                    "last_refresh_attempt_at": now() as f64,
                    "status": status,
                    "account": serde_json::to_value(&account).unwrap_or(Value::Null)
                }));
                return;
            }
            let previous = read_cache();
            let prev_limits = previous.get("limits").cloned().unwrap_or(Value::Null);
            let has_limits = prev_limits.is_object()
                && !prev_limits
                    .as_object()
                    .map(|o| o.is_empty())
                    .unwrap_or(true);
            if has_limits {
                if let Value::Object(mut m) = previous.clone() {
                    m.remove("error");
                    m.insert("refresh_error".into(), json!(exc));
                    m.insert("last_refresh_attempt_at".into(), json!(now() as f64));
                    m.insert("status".into(), json!(status));
                    if !account.is_empty() {
                        m.insert(
                            "account".into(),
                            serde_json::to_value(&account).unwrap_or(Value::Null),
                        );
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
                        active: true,
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
    if !limits.is_object() || limits.as_object().map(|o| o.is_empty()).unwrap_or(true) {
        let err = "Codex rate-limit response did not include codex limits";
        let lines = [
            status.clone(),
            String::new(),
            err.to_string(),
            CODEX_USAGE_URL.to_string(),
        ];
        emit("err", &lines.join("\n"), "error");
        write_usage_cache(json!({
            "error": "missing_limits",
            "status": status,
            "account": serde_json::to_value(&account).unwrap_or(Value::Null)
        }));
        return;
    }
    let current_status = limit_status(&limits);
    if let Some(slot) = account.slot.as_deref() {
        let previous = read_slot_cache(slot);
        if let Some(prev) = snapshot_from_cache(&previous).map(|s| s.status) {
            notify_lifted(&account, &prev, &current_status);
        }
    }
    write_usage_cache(json!({
        "limits": limits.clone(),
        "account": serde_json::to_value(&account).unwrap_or(Value::Null),
        "status": status
    }));
    if let Some(slot) = account.slot.as_deref() {
        write_slot_usage_cache(slot, slot_payload(&account, limits.clone(), &status));
    }
    sync_reset_for_account(&account, &current_status);
    reset::cancel("Codex");
    refresh_other_accounts(&account, true);
    emit_usage(
        &limits,
        &account,
        EmitOpts {
            stale_age: None,
            refresh_error: None,
            notify: true,
            active: true,
        },
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

/// `waybar-helper codex [--slot SLOT|--rank N] [--refresh] [--signal N]`
pub fn run(args: &[String]) -> ExitCode {
    if args.first().map(|s| s.as_str()) == Some("--refresh") {
        let signal = parse_signal(args);
        return refresh_with_lock(signal);
    }
    if let Some(slot) = parse_slot(args) {
        return emit_cached_or_placeholder(Some(slot.as_str()));
    }
    if let Some(rank) = parse_rank(args) {
        return match slot_for_rank(rank) {
            Some(slot) => emit_cached_or_placeholder(Some(slot.as_str())),
            None => {
                emit(
                    AUTH_BUBBLE_TEXT,
                    &format!("No Codex account found for rank {rank}"),
                    "missing",
                );
                ExitCode::SUCCESS
            }
        };
    }
    emit_cached_or_placeholder(None)
}

fn parse_signal(args: &[String]) -> Option<i64> {
    let i = args.iter().position(|a| a == "--signal")?;
    args.get(i + 1)?.parse().ok()
}

fn parse_slot(args: &[String]) -> Option<String> {
    let i = args.iter().position(|a| a == "--slot")?;
    args.get(i + 1).cloned()
}

fn parse_rank(args: &[String]) -> Option<usize> {
    let i = args.iter().position(|a| a == "--rank")?;
    args.get(i + 1)?.parse().ok()
}
