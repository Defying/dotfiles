//! Codex account read/sync side, ported from `scripts/ai_accounts.py`.
//!
//! Codex stores one active ChatGPT login at `~/.codex/auth.json`. This keeps
//! named copies under `~/.codex/accounts/<slot>/` and the active slot's auth in
//! sync with the live one. Only the read/sync half the Waybar usage bubble needs
//! is here; the interactive switcher (fuzzel menu, terminal login, `activate`)
//! stays in the Python script for now (it's quick-settings / Phase 4 UI).
//!
//! Credential files are written 0600 via a same-dir temp + atomic rename, in
//! 0700 dirs — matching the Python `_write_private` / `_ensure_private_dir`.

use std::env;
use std::fs;
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::Value;

fn home() -> PathBuf {
    PathBuf::from(env::var("HOME").unwrap_or_else(|_| "/tmp".into()))
}
fn codex_home() -> PathBuf {
    home().join(".codex")
}
fn auth_path() -> PathBuf {
    codex_home().join("auth.json")
}
fn accounts_dir() -> PathBuf {
    codex_home().join("accounts")
}
fn active_path() -> PathBuf {
    accounts_dir().join("active")
}
fn cache_dir() -> PathBuf {
    let base = env::var("XDG_CACHE_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".cache"));
    base.join("waybar")
}
fn account_cache() -> PathBuf {
    cache_dir().join("codex-account.json")
}

/// Account metadata. Field order is alphabetical so compact serialization
/// matches the Python `json.dumps(..., sort_keys=True)` shape. Empty strings are
/// kept (as Python does); `slot` is only present once a slot is known.
#[derive(Default, Clone, Serialize, Deserialize)]
pub struct Account {
    #[serde(default)]
    pub account_id: String,
    #[serde(default)]
    pub auth_mode: String,
    #[serde(default)]
    pub email: String,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub plan: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub slot: Option<String>,
    #[serde(default)]
    pub updated_at: String,
}

impl Account {
    pub fn is_empty(&self) -> bool {
        self.account_id.is_empty()
            && self.email.is_empty()
            && self.label.is_empty()
            && self.slot.is_none()
    }
}

fn now_iso() -> String {
    // UTC ISO-8601 with offset, e.g. 2026-05-30T01:57:32.741050+00:00.
    Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Micros, false)
}

fn read_json(path: &Path) -> Value {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null)
}

fn ensure_private_dir(dir: &Path) {
    let _ = fs::DirBuilder::new().recursive(true).mode(0o700).create(dir);
    let _ = fs::set_permissions(dir, fs::Permissions::from_mode(0o700));
}

/// Atomic private write: temp file in the same dir at 0600, then rename over.
fn write_private(path: &Path, data: &[u8]) -> std::io::Result<()> {
    let parent = path.parent().unwrap_or(Path::new("."));
    ensure_private_dir(parent);
    let tmp = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name().and_then(|s| s.to_str()).unwrap_or("f"),
        std::process::id()
    ));
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&tmp)?;
    f.write_all(data)?;
    f.sync_all().ok();
    drop(f);
    match fs::rename(&tmp, path) {
        Ok(()) => {
            let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));
            Ok(())
        }
        Err(e) => {
            let _ = fs::remove_file(&tmp);
            Err(e)
        }
    }
}

fn str_field(v: &Value, key: &str) -> String {
    v.get(key).and_then(|x| x.as_str()).unwrap_or("").to_string()
}

/// base64url-decode, ignoring padding (`=`), like Python's urlsafe_b64decode
/// after re-padding. Returns None on any invalid character.
fn b64url_decode(s: &str) -> Option<Vec<u8>> {
    fn val(c: u8) -> Option<u32> {
        match c {
            b'A'..=b'Z' => Some((c - b'A') as u32),
            b'a'..=b'z' => Some((c - b'a' + 26) as u32),
            b'0'..=b'9' => Some((c - b'0' + 52) as u32),
            b'-' => Some(62),
            b'_' => Some(63),
            _ => None,
        }
    }
    let mut out = Vec::new();
    let mut acc = 0u32;
    let mut bits = 0;
    for &c in s.as_bytes() {
        if c == b'=' {
            continue;
        }
        let v = val(c)?;
        acc = (acc << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
        }
    }
    Some(out)
}

/// Decode the claims (middle segment) of a JWT. Empty object on any failure.
fn jwt_claims(token: &str) -> Value {
    if token.split('.').count() < 3 {
        return Value::Object(Default::default());
    }
    let payload = token.split('.').nth(1).unwrap_or("");
    match b64url_decode(payload).and_then(|b| serde_json::from_slice::<Value>(&b).ok()) {
        Some(v) if v.is_object() => v,
        _ => Value::Object(Default::default()),
    }
}

/// Slugify a label into a slot-name component (mirrors Python `_slug`).
fn slug(value: &str, fallback: &str) -> String {
    let raw = if value.trim().is_empty() {
        fallback
    } else {
        value
    };
    let mut s = raw.trim().to_lowercase();
    if let Some(at) = s.find('@') {
        s.truncate(at); // strip @domain
    }
    // collapse runs of non [a-z0-9_.-] into a single '-'
    let mut out = String::with_capacity(s.len());
    let mut prev_dash = false;
    for c in s.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '_' | '.' | '-') {
            out.push(c);
            prev_dash = false;
        } else if !prev_dash {
            out.push('-');
            prev_dash = true;
        }
    }
    let trimmed = out.trim_matches(['.', '-']).to_string();
    if trimmed.is_empty() {
        fallback.to_string()
    } else {
        trimmed
    }
}

/// Build account metadata from an auth.json (decoding its id_token JWT).
pub fn account_from_auth(path: &Path, name: Option<&str>) -> Account {
    let data = read_json(path);
    let tokens = data.get("tokens").cloned().unwrap_or(Value::Null);
    let tokens = if tokens.is_object() {
        tokens
    } else {
        Value::Object(Default::default())
    };
    let claims = jwt_claims(tokens.get("id_token").and_then(|x| x.as_str()).unwrap_or(""));
    let openai = claims
        .get("https://api.openai.com/auth")
        .cloned()
        .filter(|v| v.is_object())
        .unwrap_or(Value::Object(Default::default()));

    let email = str_field(&claims, "email");
    let claim_name = str_field(&claims, "name");
    let account_id = {
        let a = str_field(&tokens, "account_id");
        if !a.is_empty() {
            a
        } else {
            str_field(&openai, "chatgpt_account_id")
        }
    };
    let plan = str_field(&openai, "chatgpt_plan_type");
    let label = name
        .map(|s| s.to_string())
        .filter(|s| !s.is_empty())
        .or_else(|| (!email.is_empty()).then(|| email.clone()))
        .or_else(|| (!claim_name.is_empty()).then(|| claim_name.clone()))
        .unwrap_or_else(|| {
            if !account_id.is_empty() {
                format!("codex-{}", &account_id[..account_id.len().min(8)])
            } else {
                "Codex account".to_string()
            }
        });

    Account {
        label,
        email,
        name: claim_name,
        account_id,
        plan,
        auth_mode: str_field(&data, "auth_mode"),
        updated_at: now_iso(),
        slot: None,
    }
}

fn slot_dir(slot: &str) -> PathBuf {
    accounts_dir().join(slot)
}
fn slot_auth(slot: &str) -> PathBuf {
    slot_dir(slot).join("auth.json")
}
fn slot_meta(slot: &str) -> PathBuf {
    slot_dir(slot).join("meta.json")
}

pub fn read_active_slot() -> String {
    fs::read_to_string(active_path())
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}

fn write_active_slot(slot: &str) {
    ensure_private_dir(&accounts_dir());
    let _ = write_private(&active_path(), format!("{slot}\n").as_bytes());
}

fn write_meta(slot: &str, meta: &Account) {
    if let Ok(s) = serde_json::to_string_pretty(meta) {
        let _ = write_private(&slot_meta(slot), s.as_bytes());
    }
}

pub fn write_account_cache(meta: &Account) {
    if let Ok(s) = serde_json::to_string(meta) {
        let _ = write_private(&account_cache(), s.as_bytes());
    }
}

/// Save the live auth.json into a (new) slot derived from its account, mark it
/// active, and cache the metadata. Mirrors Python `save_current`.
pub fn save_current(name: Option<&str>) -> std::io::Result<Account> {
    if !auth_path().exists() {
        return Err(std::io::Error::other(
            "Codex auth.json does not exist; run Codex login first",
        ));
    }
    let mut meta = account_from_auth(&auth_path(), name);
    let account_id = meta.account_id.clone();
    let base = name
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .or_else(|| (!meta.email.is_empty()).then(|| meta.email.clone()))
        .or_else(|| (!account_id.is_empty()).then(|| account_id.clone()))
        .unwrap_or_else(|| meta.label.clone());
    let mut slot = slug(&base, "codex");
    if !account_id.is_empty() {
        slot = format!("{slot}-{}", &account_id[..account_id.len().min(8)]);
    }
    meta.slot = Some(slot.clone());
    let auth_bytes = fs::read(auth_path())?;
    write_private(&slot_auth(&slot), &auth_bytes)?;
    write_meta(&slot, &meta);
    write_active_slot(&slot);
    write_account_cache(&meta);
    Ok(meta)
}

/// Keep the active slot's stored auth current with the live auth.json (creating
/// a slot if none is active). Mirrors Python `sync_active_slot`.
pub fn sync_active_slot() -> std::io::Result<Account> {
    ensure_private_dir(&accounts_dir());
    let slot = read_active_slot();
    if slot.is_empty() {
        return save_current(None);
    }
    if auth_path().exists() {
        let mut meta = account_from_auth(&auth_path(), None);
        let existing = read_json(&slot_meta(&slot));
        let current_id = &meta.account_id;
        let existing_id = existing.get("account_id").and_then(|x| x.as_str()).unwrap_or("");
        if !current_id.is_empty() && !existing_id.is_empty() && current_id != existing_id {
            return save_current(None); // the live login changed account
        }
        let auth_bytes = fs::read(auth_path())?;
        write_private(&slot_auth(&slot), &auth_bytes)?;
        if let Some(lbl) = existing.get("label").and_then(|x| x.as_str()) {
            if !lbl.is_empty() {
                meta.label = lbl.to_string();
            }
        }
        meta.slot = Some(slot.clone());
        write_meta(&slot, &meta);
        write_account_cache(&meta);
        return Ok(meta);
    }
    // No live auth: fall back to the slot's stored meta.
    let mut meta: Account = serde_json::from_value(read_json(&slot_meta(&slot))).unwrap_or_default();
    meta.slot = Some(slot);
    Ok(meta)
}

/// The current account metadata, syncing the active slot when possible.
/// Mirrors Python `active_account`.
pub fn active_account() -> Account {
    let slot = read_active_slot();
    if !slot.is_empty() && slot_auth(&slot).exists() {
        let meta = read_json(&slot_meta(&slot));
        if meta.is_object() && !meta.as_object().map(|m| m.is_empty()).unwrap_or(true) {
            let mut acc: Account = serde_json::from_value(meta).unwrap_or_default();
            acc.slot = Some(slot);
            return acc;
        }
    }
    if auth_path().exists() {
        return sync_active_slot().unwrap_or_else(|_| account_from_auth(&auth_path(), None));
    }
    Account::default()
}

/// "label (plan)" for tooltips. Mirrors Python `display_label`.
// Consumed by the codex usage subcommand (next migration stage).
#[allow(dead_code)]
pub fn display_label(meta: &Account) -> String {
    let label = if !meta.label.is_empty() {
        meta.label.clone()
    } else if !meta.email.is_empty() {
        meta.email.clone()
    } else if let Some(s) = &meta.slot {
        s.clone()
    } else {
        "Codex account".to_string()
    };
    if !meta.plan.is_empty() {
        format!("{label} ({})", meta.plan)
    } else {
        label
    }
}

/// `codex-status-json`: print the active account, refreshing its cache.
pub fn status_json() -> i32 {
    let meta = active_account();
    if !meta.is_empty() {
        write_account_cache(&meta);
    }
    match serde_json::to_string(&meta) {
        Ok(s) => println!("{s}"),
        Err(_) => println!("{{}}"),
    }
    0
}
