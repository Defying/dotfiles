//! Bambu printer progress bubble for Waybar.
//!
//! Local Bambu status is exposed as MQTT over TLS. Rust's standard library has
//! no TLS, so this module speaks the tiny MQTT subset we need through the
//! already-installed `openssl s_client` binary instead of adding a large client
//! stack for one Waybar poll.

use std::env;
use std::fs;
use std::io::{self, Read, Write};
use std::path::PathBuf;
use std::process::{Child, Command, ExitCode, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::usage::{cache_dir, emit, emit_classes, now};

const DEFAULT_PORT: u16 = 8883;
const PRINTER_GLYPH: &str = "\u{f02f}";
const REQUEST_TIMEOUT: Duration = Duration::from_secs(7);
const MQTT_KEEP_ALIVE_SECONDS: u16 = 30;
const NOTIFICATION_SYNC_HINT: &str = "string:x-canonical-private-synchronous:bambu-printer";
const CACHE_STALE_SECONDS: i64 = 120;
const DAEMON_RETRY_DELAY: Duration = Duration::from_secs(5);
const DAEMON_POLL_INTERVAL: Duration = Duration::from_secs(30);
const DAEMON_PING_INTERVAL: Duration = Duration::from_secs(15);

#[derive(Debug, Deserialize)]
struct Config {
    host: String,
    serial: String,
    access_code: String,
    #[serde(default = "default_port")]
    port: u16,
    #[serde(default)]
    label: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct PrintStatus {
    percent: Option<i64>,
    state: String,
    task: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    job_id: Option<String>,
    remaining_minutes: Option<i64>,
    nozzle_temp: Option<f64>,
    bed_temp: Option<f64>,
}

#[derive(Clone, Debug)]
struct CacheEntry {
    updated_at: i64,
    status: PrintStatus,
    finished_at: Option<i64>,
    finished_before_tracking: bool,
}

#[derive(Debug)]
struct MqttPacket {
    kind: u8,
    flags: u8,
    payload: Vec<u8>,
}

#[derive(Debug)]
enum FetchError {
    Config(String),
    MissingOpenSsl,
    Mqtt(String),
    Auth(String),
    Timeout,
    Io(String),
    Json(String),
}

impl FetchError {
    fn class(&self) -> &'static str {
        match self {
            FetchError::Config(_) => "missing",
            FetchError::Auth(_) => "auth",
            FetchError::Timeout => "stale",
            FetchError::MissingOpenSsl
            | FetchError::Mqtt(_)
            | FetchError::Io(_)
            | FetchError::Json(_) => "error",
        }
    }

    fn message(&self) -> String {
        match self {
            FetchError::Config(msg) => msg.clone(),
            FetchError::MissingOpenSsl => "openssl not found".into(),
            FetchError::Mqtt(msg) => msg.clone(),
            FetchError::Auth(msg) => msg.clone(),
            FetchError::Timeout => "printer did not return status before timeout".into(),
            FetchError::Io(msg) => msg.clone(),
            FetchError::Json(msg) => msg.clone(),
        }
    }
}

pub fn run(args: &[String]) -> ExitCode {
    match args.first().map(String::as_str) {
        Some("--daemon" | "daemon") => return daemon(),
        Some("--refresh" | "refresh") => return refresh_once(),
        Some("--cache" | "cache") | None => {}
        Some(other) => {
            eprintln!("usage: waybar-helper bambu [--cache|--refresh|--daemon]; got {other:?}");
            return ExitCode::from(2);
        }
    }

    emit_cached()
}

fn refresh_once() -> ExitCode {
    match read_config().and_then(|config| fetch_status(&config)) {
        Ok(status) => {
            let entry = write_cache(status);
            emit_status(&entry, false, "");
        }
        Err(err) => emit_error(err),
    }
    ExitCode::SUCCESS
}

fn emit_cached() -> ExitCode {
    match read_cache() {
        Some(entry) => {
            let age = (now() - entry.updated_at).max(0);
            let stale = age > CACHE_STALE_SECONDS;
            let note = if stale {
                format!("daemon stale: {}", format_elapsed(age))
            } else {
                String::new()
            };
            emit_status(&entry, stale, &note);
        }
        None => emit(
            &format!("{PRINTER_GLYPH} --%"),
            "bambu: no cached status; daemon not running yet",
            "missing",
        ),
    }
    ExitCode::SUCCESS
}

fn daemon() -> ExitCode {
    loop {
        match read_config().and_then(|config| daemon_session(&config)) {
            Ok(()) => {}
            Err(err) => eprintln!("bambu daemon: {}", err.message()),
        }
        thread::sleep(DAEMON_RETRY_DELAY);
    }
}

fn default_port() -> u16 {
    DEFAULT_PORT
}

fn config_path() -> PathBuf {
    if let Ok(path) = env::var("BAMBU_WAYBAR_CONFIG") {
        if !path.trim().is_empty() {
            return PathBuf::from(path);
        }
    }
    let base = env::var("XDG_CONFIG_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env::var("HOME").unwrap_or_else(|_| "/tmp".into())).join(".config")
        });
    base.join("bambu-waybar/config.json")
}

fn read_config() -> Result<Config, FetchError> {
    let path = config_path();
    let raw = fs::read_to_string(&path).map_err(|err| {
        FetchError::Config(format!(
            "bambu config missing: {} ({err})",
            path.to_string_lossy()
        ))
    })?;
    let config: Config = serde_json::from_str(&raw)
        .map_err(|err| FetchError::Config(format!("bambu config invalid: {err}")))?;
    if config.host.trim().is_empty()
        || config.serial.trim().is_empty()
        || config.access_code.trim().is_empty()
    {
        return Err(FetchError::Config(
            "bambu config needs host, serial, and access_code".into(),
        ));
    }
    Ok(config)
}

fn cache_path() -> PathBuf {
    cache_dir().join("bambu-status.json")
}

fn write_cache(status: PrintStatus) -> CacheEntry {
    let _ = fs::create_dir_all(cache_dir());
    let previous = read_cache();
    let updated_at = now();
    let (finished_at, finished_before_tracking) =
        finish_tracking(&status, previous.as_ref(), updated_at);
    let entry = CacheEntry {
        updated_at,
        status,
        finished_at,
        finished_before_tracking,
    };
    let payload = json!({
        "updated_at": entry.updated_at,
        "status": entry.status,
        "finished_at": entry.finished_at,
        "finished_before_tracking": entry.finished_before_tracking,
    });
    let _ = fs::write(cache_path(), payload.to_string());
    maybe_notify_printer(&entry, previous.as_ref());
    entry
}

fn read_cache() -> Option<CacheEntry> {
    let data: Value = fs::read_to_string(cache_path())
        .ok()
        .and_then(|raw| serde_json::from_str(&raw).ok())?;
    let updated_at = data.get("updated_at").and_then(|v| v.as_i64()).unwrap_or(0);
    let status = serde_json::from_value(data.get("status")?.clone()).ok()?;
    let finished_at = data.get("finished_at").and_then(|v| v.as_i64());
    let finished_before_tracking = data
        .get("finished_before_tracking")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    Some(CacheEntry {
        updated_at,
        status,
        finished_at,
        finished_before_tracking,
    })
}

fn finish_tracking(
    status: &PrintStatus,
    previous: Option<&CacheEntry>,
    observed_at: i64,
) -> (Option<i64>, bool) {
    if !status.is_finished() {
        return (None, false);
    }

    let Some(previous) = previous.filter(|entry| status.same_job_as(&entry.status)) else {
        return (None, true);
    };
    if !previous.status.is_finished() {
        return (Some(observed_at), false);
    }
    match previous.finished_at {
        Some(finished_at) => (Some(finished_at), false),
        None => (None, true),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum NotificationUrgency {
    Normal,
    Critical,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PrinterNotification {
    title: String,
    body: String,
    urgency: NotificationUrgency,
    timeout_ms: i64,
}

fn maybe_notify_printer(entry: &CacheEntry, previous: Option<&CacheEntry>) {
    if env::var("BAMBU_WAYBAR_NOTIFY")
        .map(|value| value == "0" || value.eq_ignore_ascii_case("false"))
        .unwrap_or(false)
    {
        return;
    }
    let Some(notification) = notification_event(entry, previous) else {
        return;
    };
    send_notification(&notification);
}

fn notification_event(
    entry: &CacheEntry,
    previous: Option<&CacheEntry>,
) -> Option<PrinterNotification> {
    let previous = previous?;
    let status = &entry.status;
    let previous_status = &previous.status;
    let same_job = status.same_job_as(previous_status);

    if status.is_error() && (!same_job || !previous_status.is_error()) {
        return Some(PrinterNotification {
            title: "Bambu printer error".into(),
            body: notification_body(status, "needs attention"),
            urgency: NotificationUrgency::Critical,
            timeout_ms: 0,
        });
    }

    if status.is_paused() && (!same_job || !previous_status.is_paused()) {
        return Some(PrinterNotification {
            title: "Bambu print paused".into(),
            body: notification_body(status, "paused"),
            urgency: NotificationUrgency::Critical,
            timeout_ms: 0,
        });
    }

    if status.is_finished() && same_job && !previous_status.is_finished() {
        return Some(PrinterNotification {
            title: "Bambu print finished".into(),
            body: notification_body(status, "finished"),
            urgency: NotificationUrgency::Normal,
            timeout_ms: 10000,
        });
    }

    if status.is_printing() && (!same_job || !previous_status.is_printing()) {
        return Some(PrinterNotification {
            title: "Bambu print started".into(),
            body: notification_body(status, "started"),
            urgency: NotificationUrgency::Normal,
            timeout_ms: 7000,
        });
    }

    None
}

fn notification_body(status: &PrintStatus, fallback: &str) -> String {
    let mut parts = Vec::new();
    if let Some(task) = status.task.as_deref().filter(|task| !task.is_empty()) {
        parts.push(task.to_string());
    } else {
        parts.push(format!("print {fallback}"));
    }
    if let Some(percent) = status.percent {
        parts.push(format!("{percent}%"));
    }
    if let Some(minutes) = status.remaining_minutes {
        if minutes > 0 {
            parts.push(format!("{} remaining", format_minutes(minutes)));
        }
    }
    parts.join(" · ")
}

fn send_notification(notification: &PrinterNotification) {
    let urgency = match notification.urgency {
        NotificationUrgency::Normal => "normal",
        NotificationUrgency::Critical => "critical",
    };
    let timeout = notification.timeout_ms.to_string();
    let _ = Command::new("setsid")
        .arg("-f")
        .args([
            "notify-send",
            "-a",
            "Bambu",
            "-i",
            "printer-symbolic",
            "-u",
            urgency,
            "-t",
            &timeout,
            "-h",
            NOTIFICATION_SYNC_HINT,
            &notification.title,
            &notification.body,
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

fn emit_error(err: FetchError) {
    if let Some(entry) = read_cache() {
        let age = (now() - entry.updated_at).max(0);
        let note = format!("stale {age}s: {}", err.message());
        emit_status(&entry, true, &note);
    } else {
        emit(
            &format!("{PRINTER_GLYPH} --%"),
            &format!("bambu: {}", err.message()),
            err.class(),
        );
    }
}

fn emit_status(entry: &CacheEntry, stale: bool, extra: &str) {
    let status = &entry.status;
    let text = match status.percent {
        Some(percent) => format!("{PRINTER_GLYPH} {percent}%"),
        None => format!("{PRINTER_GLYPH} --%"),
    };
    let state = if status.state.is_empty() {
        "unknown"
    } else {
        status.state.as_str()
    };
    let mut tooltip = vec![format!("bambu: {}", state.to_ascii_lowercase())];
    match status.percent {
        Some(percent) => tooltip.push(format!("progress: {percent}%")),
        None => tooltip.push("progress: unavailable".into()),
    }
    if let Some(task) = &status.task {
        if !task.is_empty() {
            tooltip.push(format!("job: {task}"));
        }
    }
    if let Some(minutes) = status.remaining_minutes {
        tooltip.push(format!("remaining: {}", format_minutes(minutes)));
    }
    if status.is_finished() {
        if let Some(finished_at) = entry.finished_at {
            let age = format_elapsed((now() - finished_at).max(0));
            if age == "just now" {
                tooltip.push("finished: just now".into());
            } else {
                tooltip.push(format!("finished: {age} ago"));
            }
        } else if entry.finished_before_tracking {
            tooltip.push("finished: before tracking".into());
        }
    }
    if status.nozzle_temp.is_some() || status.bed_temp.is_some() {
        let nozzle = status
            .nozzle_temp
            .map(|v| format!("{v:.0}°C"))
            .unwrap_or_else(|| "--".into());
        let bed = status
            .bed_temp
            .map(|v| format!("{v:.0}°C"))
            .unwrap_or_else(|| "--".into());
        tooltip.push(format!("temps: nozzle {nozzle}, bed {bed}"));
    }
    if !extra.is_empty() {
        tooltip.push(extra.to_string());
    }
    let class = status_class(status);
    if stale {
        emit_classes(&text, &tooltip.join("\n"), &[class, "stale"]);
    } else {
        emit(&text, &tooltip.join("\n"), class);
    }
}

fn status_class(status: &PrintStatus) -> &'static str {
    let state = status.state.to_ascii_uppercase();
    if state.contains("PAUSE") {
        "paused"
    } else if state.contains("FAILED") || state.contains("ERROR") {
        "error"
    } else if state.contains("RUNNING") || state.contains("PREPARE") {
        "printing"
    } else if state.contains("FINISH") || state.contains("IDLE") {
        "idle"
    } else if status.percent.is_some() {
        "printing"
    } else {
        "unknown"
    }
}

fn format_minutes(minutes: i64) -> String {
    if minutes <= 0 {
        return "done".into();
    }
    let hours = minutes / 60;
    let mins = minutes % 60;
    if hours > 0 {
        format!("{hours}h {mins}m")
    } else {
        format!("{mins}m")
    }
}

fn fetch_status(config: &Config) -> Result<PrintStatus, FetchError> {
    let mut child = start_openssl(config)?;
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| FetchError::Io("openssl stdin unavailable".into()))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| FetchError::Io("openssl stdout unavailable".into()))?;
    let (tx, rx) = mpsc::channel();
    let reader = thread::spawn(move || {
        let mut stdout = stdout;
        while let Ok(packet) = read_packet(&mut stdout) {
            if tx.send(packet).is_err() {
                break;
            }
        }
    });

    let result = (|| {
        stdin
            .write_all(&connect_packet(config))
            .map_err(|err| FetchError::Io(format!("connect write failed: {err}")))?;
        stdin
            .flush()
            .map_err(|err| FetchError::Io(format!("connect flush failed: {err}")))?;
        let connack = recv_matching(&rx, REQUEST_TIMEOUT, |packet| packet.kind == 2)?;
        validate_connack(&connack)?;

        let report_topic = format!("device/{}/report", config.serial.trim());
        stdin
            .write_all(&subscribe_packet(1, &report_topic))
            .map_err(|err| FetchError::Io(format!("subscribe write failed: {err}")))?;
        stdin
            .flush()
            .map_err(|err| FetchError::Io(format!("subscribe flush failed: {err}")))?;
        let suback = recv_matching(&rx, REQUEST_TIMEOUT, |packet| packet.kind == 9)?;
        validate_suback(&suback)?;

        let request_topic = format!("device/{}/request", config.serial.trim());
        let sequence_id = format!("waybar-{}", std::process::id());
        let request = json!({
            "pushing": {
                "sequence_id": sequence_id,
                "command": "pushall"
            }
        })
        .to_string();
        stdin
            .write_all(&publish_packet(&request_topic, request.as_bytes()))
            .map_err(|err| FetchError::Io(format!("request write failed: {err}")))?;
        stdin
            .flush()
            .map_err(|err| FetchError::Io(format!("request flush failed: {err}")))?;

        wait_for_status(&rx, &report_topic, REQUEST_TIMEOUT)
    })();

    let _ = child.kill();
    let _ = child.wait();
    let _ = reader.join();
    result
}

fn daemon_session(config: &Config) -> Result<(), FetchError> {
    let mut child = start_openssl(config)?;
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| FetchError::Io("openssl stdin unavailable".into()))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| FetchError::Io("openssl stdout unavailable".into()))?;
    let (tx, rx) = mpsc::channel();
    let reader = thread::spawn(move || {
        let mut stdout = stdout;
        while let Ok(packet) = read_packet(&mut stdout) {
            if tx.send(packet).is_err() {
                break;
            }
        }
    });

    let result = (|| {
        stdin
            .write_all(&connect_packet(config))
            .map_err(|err| FetchError::Io(format!("connect write failed: {err}")))?;
        stdin
            .flush()
            .map_err(|err| FetchError::Io(format!("connect flush failed: {err}")))?;
        let connack = recv_matching(&rx, REQUEST_TIMEOUT, |packet| packet.kind == 2)?;
        validate_connack(&connack)?;

        let report_topic = format!("device/{}/report", config.serial.trim());
        let request_topic = format!("device/{}/request", config.serial.trim());
        stdin
            .write_all(&subscribe_packet(1, &report_topic))
            .map_err(|err| FetchError::Io(format!("subscribe write failed: {err}")))?;
        stdin
            .flush()
            .map_err(|err| FetchError::Io(format!("subscribe flush failed: {err}")))?;
        let suback = recv_matching(&rx, REQUEST_TIMEOUT, |packet| packet.kind == 9)?;
        validate_suback(&suback)?;

        send_pushall(&mut stdin, &request_topic, "daemon")?;
        let mut last_poll = Instant::now();
        let mut last_ping = Instant::now();

        loop {
            match rx.recv_timeout(Duration::from_secs(1)) {
                Ok(packet) if packet.kind == 3 => {
                    if let Some(status) = status_from_publish(&packet, &report_topic)? {
                        write_cache(status);
                    }
                }
                Ok(_) => {}
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => {
                    return Err(FetchError::Mqtt("mqtt connection closed".into()));
                }
            }

            if last_ping.elapsed() >= DAEMON_PING_INTERVAL {
                stdin
                    .write_all(&packet(0xC0, Vec::new()))
                    .map_err(|err| FetchError::Io(format!("ping write failed: {err}")))?;
                stdin
                    .flush()
                    .map_err(|err| FetchError::Io(format!("ping flush failed: {err}")))?;
                last_ping = Instant::now();
            }

            if last_poll.elapsed() >= DAEMON_POLL_INTERVAL {
                send_pushall(&mut stdin, &request_topic, "daemon")?;
                last_poll = Instant::now();
            }
        }
    })();

    let _ = child.kill();
    let _ = child.wait();
    let _ = reader.join();
    result
}

fn start_openssl(config: &Config) -> Result<Child, FetchError> {
    let target = format!("{}:{}", config.host.trim(), config.port);
    Command::new("openssl")
        .args(["s_client", "-quiet", "-connect", &target])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| {
            if err.kind() == io::ErrorKind::NotFound {
                FetchError::MissingOpenSsl
            } else {
                FetchError::Io(format!("openssl failed: {err}"))
            }
        })
}

fn recv_matching<F>(
    rx: &Receiver<MqttPacket>,
    timeout: Duration,
    mut matches: F,
) -> Result<MqttPacket, FetchError>
where
    F: FnMut(&MqttPacket) -> bool,
{
    let deadline = Instant::now() + timeout;
    loop {
        let now = Instant::now();
        if now >= deadline {
            return Err(FetchError::Timeout);
        }
        match rx.recv_timeout(deadline.saturating_duration_since(now)) {
            Ok(packet) if matches(&packet) => return Ok(packet),
            Ok(_) => continue,
            Err(RecvTimeoutError::Timeout) => return Err(FetchError::Timeout),
            Err(RecvTimeoutError::Disconnected) => {
                return Err(FetchError::Mqtt("mqtt connection closed".into()));
            }
        }
    }
}

fn wait_for_status(
    rx: &Receiver<MqttPacket>,
    report_topic: &str,
    timeout: Duration,
) -> Result<PrintStatus, FetchError> {
    let deadline = Instant::now() + timeout;
    loop {
        let now = Instant::now();
        if now >= deadline {
            return Err(FetchError::Timeout);
        }
        let packet = match rx.recv_timeout(deadline.saturating_duration_since(now)) {
            Ok(packet) => packet,
            Err(RecvTimeoutError::Timeout) => return Err(FetchError::Timeout),
            Err(RecvTimeoutError::Disconnected) => {
                return Err(FetchError::Mqtt("mqtt connection closed".into()));
            }
        };
        if packet.kind != 3 {
            continue;
        }
        if let Some(status) = status_from_publish(&packet, report_topic)? {
            return Ok(status);
        }
    }
}

fn send_pushall<W: Write>(
    writer: &mut W,
    request_topic: &str,
    prefix: &str,
) -> Result<(), FetchError> {
    let sequence_id = format!("{prefix}-{}", now());
    let request = json!({
        "pushing": {
            "sequence_id": sequence_id,
            "command": "pushall"
        }
    })
    .to_string();
    writer
        .write_all(&publish_packet(request_topic, request.as_bytes()))
        .map_err(|err| FetchError::Io(format!("request write failed: {err}")))?;
    writer
        .flush()
        .map_err(|err| FetchError::Io(format!("request flush failed: {err}")))?;
    Ok(())
}

fn status_from_publish(
    packet: &MqttPacket,
    report_topic: &str,
) -> Result<Option<PrintStatus>, FetchError> {
    let Some((topic, payload)) = parse_publish(packet) else {
        return Ok(None);
    };
    if topic != report_topic {
        return Ok(None);
    }
    let root: Value = serde_json::from_slice(payload)
        .map_err(|err| FetchError::Json(format!("printer sent invalid json: {err}")))?;
    Ok(PrintStatus::from_report(&root))
}

fn validate_connack(packet: &MqttPacket) -> Result<(), FetchError> {
    if packet.payload.len() < 2 {
        return Err(FetchError::Mqtt("short connack".into()));
    }
    match packet.payload[1] {
        0 => Ok(()),
        4 | 5 => Err(FetchError::Auth("printer rejected access code".into())),
        code => Err(FetchError::Mqtt(format!(
            "printer rejected mqtt connect ({code})"
        ))),
    }
}

fn validate_suback(packet: &MqttPacket) -> Result<(), FetchError> {
    if packet.payload.len() < 3 {
        return Err(FetchError::Mqtt("short suback".into()));
    }
    let code = packet.payload[2];
    if code == 0 || code == 1 || code == 2 {
        Ok(())
    } else {
        Err(FetchError::Mqtt(format!(
            "printer rejected report subscribe ({code})"
        )))
    }
}

fn read_packet<R: Read>(reader: &mut R) -> io::Result<MqttPacket> {
    let mut first = [0u8; 1];
    reader.read_exact(&mut first)?;
    let remaining = read_remaining_length(reader)?;
    let mut payload = vec![0u8; remaining];
    reader.read_exact(&mut payload)?;
    Ok(MqttPacket {
        kind: first[0] >> 4,
        flags: first[0] & 0x0f,
        payload,
    })
}

fn read_remaining_length<R: Read>(reader: &mut R) -> io::Result<usize> {
    let mut multiplier = 1usize;
    let mut value = 0usize;
    for _ in 0..4 {
        let mut encoded = [0u8; 1];
        reader.read_exact(&mut encoded)?;
        value += ((encoded[0] & 127) as usize) * multiplier;
        if encoded[0] & 128 == 0 {
            return Ok(value);
        }
        multiplier *= 128;
    }
    Err(io::Error::new(
        io::ErrorKind::InvalidData,
        "malformed mqtt remaining length",
    ))
}

fn connect_packet(config: &Config) -> Vec<u8> {
    let client_id = format!(
        "waybar-{}-{}",
        config.label.as_deref().unwrap_or("bambu"),
        std::process::id()
    );
    let mut variable = Vec::new();
    push_utf8(&mut variable, "MQTT");
    variable.push(4);
    variable.push(0xC2); // username + password + clean session
    variable.extend_from_slice(&MQTT_KEEP_ALIVE_SECONDS.to_be_bytes());

    let mut payload = Vec::new();
    push_utf8(&mut payload, &client_id);
    push_utf8(&mut payload, "bblp");
    push_utf8(&mut payload, config.access_code.trim());

    packet(0x10, [variable, payload].concat())
}

fn subscribe_packet(packet_id: u16, topic: &str) -> Vec<u8> {
    let mut body = Vec::new();
    body.extend_from_slice(&packet_id.to_be_bytes());
    push_utf8(&mut body, topic);
    body.push(0); // QoS 0
    packet(0x82, body)
}

fn publish_packet(topic: &str, payload: &[u8]) -> Vec<u8> {
    let mut body = Vec::new();
    push_utf8(&mut body, topic);
    body.extend_from_slice(payload);
    packet(0x30, body)
}

fn packet(header: u8, body: Vec<u8>) -> Vec<u8> {
    let mut out = vec![header];
    out.extend_from_slice(&encode_remaining_length(body.len()));
    out.extend_from_slice(&body);
    out
}

fn encode_remaining_length(mut value: usize) -> Vec<u8> {
    let mut out = Vec::new();
    loop {
        let mut encoded = (value % 128) as u8;
        value /= 128;
        if value > 0 {
            encoded |= 128;
        }
        out.push(encoded);
        if value == 0 {
            return out;
        }
    }
}

fn push_utf8(out: &mut Vec<u8>, value: &str) {
    let bytes = value.as_bytes();
    let len = bytes.len().min(u16::MAX as usize) as u16;
    out.extend_from_slice(&len.to_be_bytes());
    out.extend_from_slice(&bytes[..len as usize]);
}

fn parse_publish(packet: &MqttPacket) -> Option<(String, &[u8])> {
    let payload = packet.payload.as_slice();
    if payload.len() < 2 {
        return None;
    }
    let topic_len = u16::from_be_bytes([payload[0], payload[1]]) as usize;
    if payload.len() < 2 + topic_len {
        return None;
    }
    let topic = String::from_utf8_lossy(&payload[2..2 + topic_len]).to_string();
    let mut index = 2 + topic_len;
    let qos = (packet.flags & 0b0110) >> 1;
    if qos > 0 {
        if payload.len() < index + 2 {
            return None;
        }
        index += 2;
    }
    Some((topic, &payload[index..]))
}

impl PrintStatus {
    fn is_finished(&self) -> bool {
        let state = self.state.to_ascii_uppercase();
        state.contains("FINISH") || state.contains("COMPLETE")
    }

    fn is_paused(&self) -> bool {
        self.state.to_ascii_uppercase().contains("PAUSE")
    }

    fn is_error(&self) -> bool {
        let state = self.state.to_ascii_uppercase();
        state.contains("FAILED") || state.contains("ERROR")
    }

    fn is_printing(&self) -> bool {
        let state = self.state.to_ascii_uppercase();
        state.contains("RUNNING")
            || state.contains("PREPARE")
            || matches!(self.percent, Some(percent) if percent > 0 && percent < 100)
    }

    fn same_job_as(&self, other: &Self) -> bool {
        match (self.job_key(), other.job_key()) {
            (Some(a), Some(b)) => a == b,
            _ => true,
        }
    }

    fn job_key(&self) -> Option<&str> {
        self.job_id
            .as_deref()
            .filter(|s| !s.is_empty())
            .or_else(|| self.task.as_deref().filter(|s| !s.is_empty()))
    }

    fn from_report(root: &Value) -> Option<Self> {
        let print = root.get("print")?;
        let percent = first_i64(
            print,
            &[
                "mc_percent",
                "print_percent",
                "printPercent",
                "progress",
                "percent",
            ],
        )
        .map(|v| v.clamp(0, 100));
        let state = first_string(
            print,
            &["gcode_state", "print_status", "printStatus", "state"],
        )
        .unwrap_or_default();
        let task = first_string(
            print,
            &[
                "subtask_name",
                "task_name",
                "gcode_file",
                "file",
                "filename",
            ],
        );
        let status = PrintStatus {
            percent,
            state,
            task,
            job_id: first_string(
                print,
                &["task_id", "subtask_id", "project_id", "profile_id"],
            ),
            remaining_minutes: first_i64(
                print,
                &[
                    "mc_remaining_time",
                    "remaining_time",
                    "remainingTime",
                    "time_remaining",
                ],
            ),
            nozzle_temp: first_f64(
                print,
                &["nozzle_temper", "nozzle_temp", "nozzleTemperature"],
            ),
            bed_temp: first_f64(print, &["bed_temper", "bed_temp", "bedTemperature"]),
        };
        if status.percent.is_some() || !status.state.is_empty() {
            Some(status)
        } else {
            None
        }
    }
}

fn first_string(value: &Value, keys: &[&str]) -> Option<String> {
    keys.iter()
        .filter_map(|key| value.get(*key).and_then(|v| v.as_str()))
        .find(|s| !s.is_empty())
        .map(ToString::to_string)
}

fn first_i64(value: &Value, keys: &[&str]) -> Option<i64> {
    keys.iter().find_map(|key| {
        let v = value.get(*key)?;
        v.as_i64()
            .or_else(|| v.as_f64().map(|n| n.round() as i64))
            .or_else(|| {
                v.as_str()
                    .and_then(|s| s.parse::<f64>().ok())
                    .map(|n| n.round() as i64)
            })
    })
}

fn first_f64(value: &Value, keys: &[&str]) -> Option<f64> {
    keys.iter().find_map(|key| {
        let v = value.get(*key)?;
        v.as_f64()
            .or_else(|| v.as_i64().map(|n| n as f64))
            .or_else(|| v.as_str().and_then(|s| s.parse::<f64>().ok()))
    })
}

fn format_elapsed(seconds: i64) -> String {
    if seconds < 60 {
        return "just now".into();
    }
    let minutes = (seconds + 59) / 60;
    if minutes == 1 {
        return "1m".into();
    }
    let hours = minutes / 60;
    let mins = minutes % 60;
    if hours == 0 {
        format!("{minutes}m")
    } else if hours < 24 {
        format!("{hours}h {mins}m")
    } else {
        let days = hours / 24;
        let rem_hours = hours % 24;
        format!("{days}d {rem_hours}h")
    }
}

#[cfg(test)]
mod tests {
    use super::{
        finish_tracking, notification_event, CacheEntry, NotificationUrgency, PrintStatus,
    };

    fn status(state: &str) -> PrintStatus {
        PrintStatus {
            percent: Some(if state == "FINISH" { 100 } else { 42 }),
            state: state.to_string(),
            task: Some("benchy".into()),
            job_id: Some("job-1".into()),
            remaining_minutes: None,
            nozzle_temp: None,
            bed_temp: None,
        }
    }

    fn entry(status: PrintStatus, finished_at: Option<i64>, before: bool) -> CacheEntry {
        CacheEntry {
            updated_at: 900,
            status,
            finished_at,
            finished_before_tracking: before,
        }
    }

    #[test]
    fn records_finish_when_state_transitions_to_finish() {
        let previous = entry(status("RUNNING"), None, false);
        assert_eq!(
            finish_tracking(&status("FINISH"), Some(&previous), 1000),
            (Some(1000), false)
        );
    }

    #[test]
    fn preserves_existing_finish_time_for_same_finished_job() {
        let previous = entry(status("FINISH"), Some(950), false);
        assert_eq!(
            finish_tracking(&status("FINISH"), Some(&previous), 1000),
            (Some(950), false)
        );
    }

    #[test]
    fn marks_already_finished_job_as_before_tracking() {
        assert_eq!(finish_tracking(&status("FINISH"), None, 1000), (None, true));
    }

    #[test]
    fn notifies_when_print_finishes_after_being_observed_running() {
        let previous = entry(status("RUNNING"), None, false);
        let current = entry(status("FINISH"), Some(1000), false);
        let notification = notification_event(&current, Some(&previous)).unwrap();
        assert_eq!(notification.title, "Bambu print finished");
        assert_eq!(notification.urgency, NotificationUrgency::Normal);
    }

    #[test]
    fn does_not_notify_for_already_finished_print() {
        let current = entry(status("FINISH"), None, true);
        assert_eq!(notification_event(&current, None), None);
    }

    #[test]
    fn notifies_critically_when_print_pauses() {
        let previous = entry(status("RUNNING"), None, false);
        let current = entry(status("PAUSE"), None, false);
        let notification = notification_event(&current, Some(&previous)).unwrap();
        assert_eq!(notification.title, "Bambu print paused");
        assert_eq!(notification.urgency, NotificationUrgency::Critical);
        assert_eq!(notification.timeout_ms, 0);
    }

    #[test]
    fn does_not_repeat_same_paused_notification() {
        let previous = entry(status("PAUSE"), None, false);
        let current = entry(status("PAUSE"), None, false);
        assert_eq!(notification_event(&current, Some(&previous)), None);
    }
}
