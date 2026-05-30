//! AI-usage "limit reset" reminders, ported from `scripts/ai_reset.py`.
//!
//! When a usage window is exhausted (0%), arm a one-shot `systemd --user` timer
//! that fires a desktop notification at the reset moment (dormant until then),
//! and — best-effort, fully detached — drop a companion Apple Reminder on a Mac
//! over SSH so it shows up on the phone too. Everything is deduped by service,
//! window, reset epoch, host and list so the every-few-minutes waybar refresh
//! never churns the timer or spams reminders. Only the dedicated AI-reset list
//! is ever touched. `cancel()` is a no-op when nothing is scheduled.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn state_dir() -> PathBuf {
    let base = env::var("XDG_CACHE_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env::var("HOME").unwrap_or_else(|_| "/tmp".into())).join(".cache")
        });
    base.join("waybar")
}

// Optional companion Apple Reminder host (ssh alias) + list. Set
// AI_RESET_MINI_HOST="" to disable the Mac reminder entirely.
fn mini_host() -> String {
    env::var("AI_RESET_MINI_HOST").unwrap_or_else(|_| "mini".into())
}
fn mac_list() -> String {
    env::var("AI_RESET_MAC_LIST").unwrap_or_else(|_| "AI Resets".into())
}

fn unit(service: &str) -> String {
    format!("ai-reset-{}", service.to_lowercase())
}
fn state_file(service: &str) -> PathBuf {
    state_dir().join(format!("{}.reset-timer", service.to_lowercase()))
}

fn read_json(path: &Path) -> Value {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null)
}

/// Run a command with a hard timeout (via `timeout(1)`), discarding output.
/// Returns true if it ran (regardless of exit status), false if it couldn't be
/// spawned or timed out — mirroring Python `_run` returning a result vs None.
fn run_timeout(secs: u32, args: &[&str]) -> bool {
    Command::new("timeout")
        .arg(secs.to_string())
        .args(args)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success() || s.code().is_some()) // ran; non-zero is still "ran"
        .unwrap_or(false)
}

fn timer_active(service: &str) -> bool {
    let t = format!("{}.timer", unit(service));
    Command::new("timeout")
        .args(["4", "systemctl", "--user", "is-active", &t])
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim() == "active")
        .unwrap_or(false)
}

fn clear(service: &str) {
    let u = unit(service);
    let timer = format!("{u}.timer");
    let svc = format!("{u}.service");
    run_timeout(5, &["systemctl", "--user", "stop", &timer, &svc]);
    run_timeout(5, &["systemctl", "--user", "reset-failed", &timer, &svc]);
}

fn same_reset(prev: &Value, window: &str, epoch: i64) -> bool {
    prev.get("reset_epoch").and_then(|v| v.as_i64()) == Some(epoch)
        && prev.get("window").and_then(|v| v.as_str()) == Some(window)
}

/// Has the Mac reminder already been requested for this exact reset? Dedup is
/// separate from the Linux timer so repairing a missing timer can't re-fire the
/// reminder. The else-branch migrates pre-`mac_reminder_*` state files.
fn mac_already_requested(prev: &Value, window: &str, epoch: i64) -> bool {
    let me = mini_host();
    let list = mac_list();
    if prev.get("mac_reminder_epoch").and_then(|v| v.as_i64()) == Some(epoch)
        && prev.get("mac_reminder_window").and_then(|v| v.as_str()) == Some(window)
    {
        prev.get("mac_reminder_host").and_then(|v| v.as_str()) == Some(me.as_str())
            && prev.get("mac_reminder_list").and_then(|v| v.as_str()) == Some(list.as_str())
    } else {
        same_reset(prev, window, epoch) && prev.get("mac_reminder_epoch").is_none()
    }
}

fn sh_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

// AppleScript run via `osascript -e <script> -- <name> <delaySeconds> <list>`.
// Creates the reminder in the named list (making the list if absent), deduped
// on the Mac side by name + due-date within 120s. Verbatim from ai_reset.py.
const OSASCRIPT: &str = r#"
on run argv
  set reminderName to item 1 of argv
  set delaySeconds to (item 2 of argv) as integer
  set reminderListName to item 3 of argv
  set resetDate to (current date) + delaySeconds
  tell application "Reminders"
    set reminderList to missing value
    repeat with candidateList in lists
      try
        if (name of candidateList as text) is reminderListName then
          set reminderList to candidateList
          exit repeat
        end if
      end try
    end repeat
    if reminderList is missing value then
      set reminderList to make new list with properties {name:reminderListName}
    end if
    set alreadyExists to false
    repeat with existingReminder in reminders of reminderList
      try
        if (name of existingReminder as text) is reminderName and completed of existingReminder is false then
          set existingDueDate to due date of existingReminder
          if existingDueDate is not missing value then
            set deltaSeconds to existingDueDate - resetDate
            if deltaSeconds < 0 then set deltaSeconds to -deltaSeconds
            if deltaSeconds < 120 then set alreadyExists to true
          end if
        end if
      end try
    end repeat
    if alreadyExists is false then
      make new reminder at reminderList with properties {name:reminderName, body:"Usage back to 100%", due date:resetDate}
    end if
  end tell
end run
"#;

/// Fire-and-forget Apple Reminder over SSH. Fully detached (`setsid -f`) with a
/// 2s connect timeout, so it returns immediately and dies quietly if the Mac is
/// unreachable. Uses the Mac's own clock for the relative offset.
fn set_mac_reminder(service: &str, window_label: &str, reset_epoch: i64) {
    let host = mini_host();
    if host.is_empty() {
        return;
    }
    let delay = reset_epoch - now();
    if delay <= 0 {
        return;
    }
    let title = format!("{service} {window_label} limit reset");
    let remote = format!(
        "osascript -e {} -- {} {} {}",
        sh_quote(OSASCRIPT),
        sh_quote(&title),
        sh_quote(&delay.to_string()),
        sh_quote(&mac_list()),
    );
    let _ = Command::new("setsid")
        .args([
            "-f",
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=2",
            "-o",
            "StrictHostKeyChecking=accept-new",
            &host,
            &remote,
        ])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
}

/// (Re)arm a one-shot reset notification at `reset_epoch` for `service`.
/// No-op if the reset is in the past or a matching timer is already armed.
// Consumed by the codex/claude usage subcommands (next migration stage).
#[allow(dead_code)]
pub fn schedule(service: &str, window_label: &str, reset_epoch: Option<i64>, icon: Option<&str>) {
    let epoch = match reset_epoch {
        Some(e) => e,
        None => return,
    };
    let delay = epoch - now();
    if delay <= 0 {
        cancel(service);
        return;
    }

    let sf = state_file(service);
    let prev = read_json(&sf);
    let same = same_reset(&prev, window_label, epoch);
    let mac_requested = mac_already_requested(&prev, window_label, epoch);
    if same && timer_active(service) {
        return; // already armed for this exact reset
    }

    clear(service);

    let unit_arg = format!("--unit={}", unit(service));
    let on_active = format!("--on-active={delay}s");
    let title = format!("{service} limit reset");
    let body = format!("{window_label} window reset — usage is back to 100%");
    let mut args: Vec<String> = [
        "systemd-run",
        "--user",
        "--quiet",
        &unit_arg,
        &on_active,
        "--timer-property=AccuracySec=30s",
        "--",
        "notify-send",
        "-a",
        "AI usage",
        "-u",
        "normal",
        "-t",
        "0",
        "-h",
        "string:x-canonical-private-synchronous:ai-reset",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    if let Some(ic) = icon {
        if Path::new(ic).exists() {
            args.push("-i".into());
            args.push(ic.into());
        }
    }
    args.push(title);
    args.push(body);

    let argv: Vec<&str> = args.iter().map(|s| s.as_str()).collect();
    if !run_timeout(5, &argv) {
        return; // systemd-run couldn't be spawned; leave state untouched
    }

    let host = mini_host();
    let list = mac_list();
    let mut state = json!({ "reset_epoch": epoch, "window": window_label });
    if mac_requested {
        // Already requested for this reset — record it without re-firing SSH.
        state["mac_reminder_epoch"] = json!(epoch);
        state["mac_reminder_window"] = json!(window_label);
        state["mac_reminder_host"] = json!(host);
        state["mac_reminder_list"] = json!(list);
    } else {
        set_mac_reminder(service, window_label, epoch);
        state["mac_reminder_epoch"] = json!(epoch);
        state["mac_reminder_window"] = json!(window_label);
        state["mac_reminder_host"] = json!(host);
        state["mac_reminder_list"] = json!(list);
    }
    let _ = fs::create_dir_all(state_dir());
    if let Ok(s) = serde_json::to_string(&state) {
        let _ = fs::write(&sf, s);
    }
}

/// Drop a pending reset timer. No-op when nothing is scheduled (no state file).
#[allow(dead_code)]
pub fn cancel(service: &str) {
    let sf = state_file(service);
    if !sf.exists() {
        return;
    }
    clear(service);
    let _ = fs::remove_file(&sf);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn same_reset_matches_epoch_and_window() {
        let prev = json!({"reset_epoch": 1000, "window": "5h"});
        assert!(same_reset(&prev, "5h", 1000));
        assert!(!same_reset(&prev, "weekly", 1000)); // window differs
        assert!(!same_reset(&prev, "5h", 1001)); // epoch differs
        assert!(!same_reset(&Value::Null, "5h", 1000)); // no state
    }

    #[test]
    fn mac_dedup_suppresses_exact_repeat() {
        // Same epoch+window+host+list already requested → suppressed (no re-fire).
        let prev = json!({
            "reset_epoch": 1000, "window": "weekly",
            "mac_reminder_epoch": 1000, "mac_reminder_window": "weekly",
            "mac_reminder_host": "mini", "mac_reminder_list": "AI Resets",
        });
        assert!(mac_already_requested(&prev, "weekly", 1000));
        // Different epoch → not yet requested → will fire.
        assert!(!mac_already_requested(&prev, "weekly", 2000));
        // Different window → will fire.
        assert!(!mac_already_requested(&prev, "5h", 1000));
    }

    #[test]
    fn mac_dedup_migrates_legacy_state() {
        // Pre-mac_reminder_* state matching the reset is treated as already
        // requested, so upgrading doesn't flood a reminder for an armed reset.
        let legacy = json!({"reset_epoch": 1000, "window": "5h"});
        assert!(mac_already_requested(&legacy, "5h", 1000));
        // But a legacy state for a *different* reset is not.
        assert!(!mac_already_requested(&legacy, "5h", 1001));
    }

    #[test]
    #[ignore = "spawns a real systemd --user timer; run with --ignored --test-threads=1"]
    fn schedule_then_cancel_arms_and_clears_timer() {
        // Sandbox the state dir and disable the Mac reminder (no SSH fires).
        let tmp = std::env::temp_dir().join(format!("airesettest-{}", std::process::id()));
        let _ = fs::create_dir_all(&tmp);
        std::env::set_var("XDG_CACHE_HOME", &tmp);
        std::env::set_var("AI_RESET_MINI_HOST", "");
        let svc = "TestSvc";
        cancel(svc); // clean slate

        let epoch = now() + 3600;
        schedule(svc, "5h", Some(epoch), None);
        assert!(timer_active(svc), "timer should be armed after schedule");
        let sf = state_file(svc);
        assert!(sf.exists(), "state file written");

        // Idempotent: same reset must not churn the timer.
        schedule(svc, "5h", Some(epoch), None);
        assert!(timer_active(svc), "timer still armed after idempotent re-schedule");

        cancel(svc);
        assert!(!timer_active(svc), "timer cleared after cancel");
        assert!(!sf.exists(), "state file removed after cancel");
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn mac_dedup_refires_when_host_or_list_changes() {
        // Same reset but the configured host/list changed → re-request.
        let prev = json!({
            "reset_epoch": 1000, "window": "weekly",
            "mac_reminder_epoch": 1000, "mac_reminder_window": "weekly",
            "mac_reminder_host": "oldmac", "mac_reminder_list": "AI Resets",
        });
        // Default host is "mini" (env unset in test), which differs from "oldmac".
        assert!(!mac_already_requested(&prev, "weekly", 1000));
    }
}
