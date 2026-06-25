use chrono::{Local, TimeZone};
use gtk::gio;
use gtk::glib::{self, ControlFlow, Propagation};
use gtk::pango;
use gtk::prelude::*;
use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
use serde_json::Value;
use std::cell::Cell;
use std::env;
use std::fs;
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::rc::Rc;
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const PANEL_TOP_MARGIN: i32 = 6;
const PANEL_LEFT_MARGIN: i32 = 292;
const PANEL_WIDTH: i32 = 320;
const PRINTER_GLYPH: &str = "\u{f02f}";
const REFRESH_GLYPH: &str = "\u{f021}";
const CLOSE_GLYPH: &str = "\u{f00d}";

const CSS: &str = r#"
#bambu-printer-panel {
  background: transparent;
}
.panel {
  font-family: "SF Pro Text", "Symbols Nerd Font", "Font Awesome 6 Free", sans-serif;
  background:
    linear-gradient(150deg, rgba(255,255,255,0.18), rgba(255,255,255,0.05) 42%,
      rgba(126,231,135,0.10)),
    rgba(8, 11, 20, 0.74);
  border: 1px solid rgba(255,255,255,0.30);
  border-radius: 16px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.34);
  padding: 12px;
}
.title { color: #f4f7fb; font-size: 14px; font-weight: 800; }
.sub { color: rgba(244,247,251,0.60); font-size: 11px; }
.status { color: #f4f7fb; font-size: 12px; font-weight: 800; }
.value { color: rgba(244,247,251,0.84); font-size: 12px; font-weight: 700; }
.muted { color: rgba(244,247,251,0.58); font-size: 11px; }
.section {
  background: rgba(255,255,255,0.055);
  border: 1px solid rgba(255,255,255,0.13);
  border-radius: 10px;
  padding: 8px;
}
.row { padding: 0; }
label { color: #f4f7fb; font-size: 12px; }
button {
  color: #f4f7fb;
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.11);
  border-radius: 9px;
  padding: 0;
  min-width: 30px;
  min-height: 28px;
  font-size: 12px;
}
button:hover { background: rgba(255,255,255,0.12); }
button.close {
  min-width: 24px;
  min-height: 24px;
  border-radius: 12px;
  font-size: 13px;
}
progressbar trough {
  min-height: 10px;
  border-radius: 10px;
  background: rgba(255,255,255,0.11);
  border: 1px solid rgba(255,255,255,0.08);
}
progressbar progress {
  min-height: 10px;
  border-radius: 10px;
  background: #7ee787;
}
progressbar.warn progress { background: #ffe08a; }
progressbar.error progress { background: #ff6b6b; }
"#;

#[derive(Clone, Debug)]
struct PrinterConfig {
    host: String,
    port: i64,
    serial: String,
}

#[derive(Clone, Debug)]
struct PrinterStatus {
    updated_at: Option<i64>,
    state: String,
    percent: Option<i64>,
    task: String,
    remaining_minutes: Option<i64>,
    finished_at: Option<i64>,
    finished_before_tracking: bool,
    nozzle_temp: Option<f64>,
    bed_temp: Option<f64>,
}

#[derive(Clone, Debug)]
struct PrinterInfo {
    config: Option<PrinterConfig>,
    status: Option<PrinterStatus>,
    error: String,
}

struct PanelState {
    app: gtk::Application,
    window: gtk::ApplicationWindow,
    panel: gtk::Box,
    focus_seen: Cell<bool>,
}

impl PanelState {
    fn close(&self) {
        self.app.quit();
    }

    fn populate(self: &Rc<Self>) {
        while let Some(child) = self.panel.first_child() {
            self.panel.remove(&child);
        }
        self.build();
    }

    fn build(self: &Rc<Self>) {
        let info = load_info();

        let header = gtk::Box::new(gtk::Orientation::Horizontal, 8);
        let icon = label(PRINTER_GLYPH, 0.0, "title");
        header.append(&icon);
        let title = label("bambu printer", 0.0, "title");
        title.set_hexpand(true);
        header.append(&title);
        header.append(&icon_button(REFRESH_GLYPH, "refresh", {
            let state = Rc::clone(self);
            move || state.refresh()
        }));
        header.append(&icon_button_with_class(CLOSE_GLYPH, "close", "close", {
            let state = Rc::clone(self);
            move || state.close()
        }));
        self.panel.append(&header);

        if !info.error.is_empty() {
            self.panel.append(&wrapping_label(&info.error, "muted"));
        }

        if let Some(status) = &info.status {
            self.panel.append(&status_section(status));
            self.panel.append(&temp_section(status));
        } else {
            self.panel.append(&wrapping_label(
                "no cached printer status yet; refresh once.",
                "muted",
            ));
        }

        if let Some(config) = &info.config {
            self.panel.append(&config_section(config));
        }
    }

    fn refresh(self: &Rc<Self>) {
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            let _ = Command::new(helper_bin())
                .arg("bambu")
                .arg("--refresh")
                .env("BAMBU_WAYBAR_NOTIFY", "0")
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            let _ = Command::new("pkill")
                .args(["-RTMIN+12", "-x", "waybar"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            let _ = tx.send(());
        });

        let state = Rc::clone(self);
        glib::timeout_add_local(Duration::from_millis(100), move || match rx.try_recv() {
            Ok(()) => {
                state.populate();
                ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => ControlFlow::Break,
        });
    }
}

fn main() {
    std::process::exit(launch());
}

fn launch() -> i32 {
    if close_existing() {
        return 0;
    }

    let pid_path = pid_file();
    if let Some(parent) = pid_path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let pid = std::process::id();
    let _ = fs::write(&pid_path, pid.to_string());

    let app = gtk::Application::builder()
        .application_id("dev.ben.bambu_printer_panel")
        .flags(gio::ApplicationFlags::NON_UNIQUE)
        .build();

    let cleanup_path = pid_path.clone();
    app.connect_shutdown(move |_| cleanup_pidfile(&cleanup_path, pid));
    app.connect_activate(build_panel);

    let _ = app.run_with_args(&["bambu-printer-panel"]);
    0
}

fn build_panel(app: &gtk::Application) {
    install_css();

    let window = gtk::ApplicationWindow::builder()
        .application(app)
        .title("bambu-printer")
        .decorated(false)
        .resizable(false)
        .default_width(PANEL_WIDTH)
        .build();
    window.set_widget_name("bambu-printer-panel");
    window.set_focusable(true);
    window.init_layer_shell();
    window.set_namespace(Some("bambu-printer"));
    window.set_layer(Layer::Overlay);
    window.set_keyboard_mode(KeyboardMode::OnDemand);
    window.set_anchor(Edge::Top, true);
    window.set_anchor(Edge::Left, true);
    window.set_margin(Edge::Top, PANEL_TOP_MARGIN);
    window.set_margin(Edge::Left, PANEL_LEFT_MARGIN);

    let panel = gtk::Box::new(gtk::Orientation::Vertical, 8);
    panel.add_css_class("panel");
    window.set_child(Some(&panel));

    let state = Rc::new(PanelState {
        app: app.clone(),
        window: window.clone(),
        panel,
        focus_seen: Cell::new(false),
    });
    state.populate();

    let key = gtk::EventControllerKey::new();
    {
        let state = Rc::clone(&state);
        key.connect_key_pressed(move |_, key, _, _| {
            if key == gtk::gdk::Key::Escape {
                state.close();
                Propagation::Stop
            } else {
                Propagation::Proceed
            }
        });
    }
    window.add_controller(key);

    window.connect_close_request({
        let state = Rc::clone(&state);
        move |_| {
            state.close();
            Propagation::Proceed
        }
    });

    window.connect_is_active_notify({
        let state = Rc::clone(&state);
        move |window| {
            if window.is_active() {
                state.focus_seen.set(true);
            } else if state.focus_seen.get() {
                let state = Rc::clone(&state);
                glib::timeout_add_local_once(Duration::from_millis(80), move || {
                    if !state.window.is_active() {
                        state.close();
                    }
                });
            }
        }
    });

    window.present();
}

fn status_section(status: &PrinterStatus) -> gtk::Box {
    let section = gtk::Box::new(gtk::Orientation::Vertical, 7);
    section.add_css_class("section");

    let top = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    let state = label(&status_label(status), 0.0, "status");
    state.set_hexpand(true);
    top.append(&state);
    top.append(&label(&percent_label(status.percent), 1.0, "status"));
    section.append(&top);

    let progress = gtk::ProgressBar::new();
    let percent = status.percent.unwrap_or(0).clamp(0, 100);
    progress.set_fraction(percent as f64 / 100.0);
    if status.is_attention() {
        progress.add_css_class("error");
    } else if status.is_paused() {
        progress.add_css_class("warn");
    }
    section.append(&progress);

    if !status.task.is_empty() {
        section.append(&wrapping_label(&status.task, "value"));
    }
    let mut detail = Vec::new();
    if let Some(minutes) = status.remaining_minutes {
        detail.push(format!("remaining: {}", format_minutes(minutes)));
    }
    if let Some(finished) = finished_label(status) {
        detail.push(finished);
    }
    if let Some(updated_at) = status.updated_at {
        detail.push(format!("updated {}", format_age(updated_at)));
    }
    if !detail.is_empty() {
        section.append(&wrapping_label(&detail.join("  -  "), "muted"));
    }
    section
}

fn temp_section(status: &PrinterStatus) -> gtk::Box {
    let section = gtk::Box::new(gtk::Orientation::Vertical, 6);
    section.add_css_class("section");
    section.append(&key_value(
        "nozzle",
        &status
            .nozzle_temp
            .map(format_temp)
            .unwrap_or_else(|| "--".into()),
    ));
    section.append(&key_value(
        "bed",
        &status
            .bed_temp
            .map(format_temp)
            .unwrap_or_else(|| "--".into()),
    ));
    section
}

fn config_section(config: &PrinterConfig) -> gtk::Box {
    let section = gtk::Box::new(gtk::Orientation::Vertical, 6);
    section.add_css_class("section");
    section.append(&key_value(
        "host",
        &format!("{}:{}", config.host, config.port),
    ));
    section.append(&key_value("serial", &config.serial));
    section.append(&key_value("notifications", "mako on"));
    section
}

fn key_value(key: &str, value: &str) -> gtk::Box {
    let row = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    row.add_css_class("row");
    let key_label = label(key, 0.0, "muted");
    key_label.set_hexpand(true);
    let value_label = label(value, 1.0, "value");
    value_label.set_ellipsize(pango::EllipsizeMode::End);
    row.append(&key_label);
    row.append(&value_label);
    row
}

fn load_info() -> PrinterInfo {
    let (config, config_error) = load_config();
    let (status, status_error) = load_status();
    let error = [config_error, status_error]
        .into_iter()
        .flatten()
        .collect::<Vec<_>>()
        .join("\n");
    PrinterInfo {
        config,
        status,
        error,
    }
}

fn load_config() -> (Option<PrinterConfig>, Option<String>) {
    let path = config_path();
    let Ok(data) = read_json(&path) else {
        return (None, Some(format!("could not read {}", path.display())));
    };
    let config = PrinterConfig {
        host: data
            .get("host")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        port: data.get("port").and_then(Value::as_i64).unwrap_or(8883),
        serial: data
            .get("serial")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
    };
    (Some(config), None)
}

fn load_status() -> (Option<PrinterStatus>, Option<String>) {
    let path = cache_path();
    let Ok(data) = read_json(&path) else {
        return (None, Some(format!("could not read {}", path.display())));
    };
    let status = data.get("status").unwrap_or(&Value::Null);
    let printer_status = PrinterStatus {
        updated_at: data.get("updated_at").and_then(Value::as_i64),
        state: status
            .get("state")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        percent: status.get("percent").and_then(Value::as_i64),
        task: status
            .get("task")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        remaining_minutes: status.get("remaining_minutes").and_then(Value::as_i64),
        finished_at: data.get("finished_at").and_then(Value::as_i64),
        finished_before_tracking: data
            .get("finished_before_tracking")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        nozzle_temp: status.get("nozzle_temp").and_then(Value::as_f64),
        bed_temp: status.get("bed_temp").and_then(Value::as_f64),
    };
    (Some(printer_status), None)
}

impl PrinterStatus {
    fn state_upper(&self) -> String {
        self.state.to_ascii_uppercase()
    }

    fn is_finished(&self) -> bool {
        let state = self.state_upper();
        state.contains("FINISH") || state.contains("COMPLETE")
    }

    fn is_paused(&self) -> bool {
        self.state_upper().contains("PAUSE")
    }

    fn is_attention(&self) -> bool {
        let state = self.state_upper();
        state.contains("FAILED") || state.contains("ERROR")
    }
}

fn status_label(status: &PrinterStatus) -> String {
    let state = status.state.to_ascii_lowercase();
    if state.is_empty() {
        "unknown".into()
    } else {
        state
    }
}

fn percent_label(percent: Option<i64>) -> String {
    percent
        .map(|value| format!("{}%", value.clamp(0, 100)))
        .unwrap_or_else(|| "--%".into())
}

fn finished_label(status: &PrinterStatus) -> Option<String> {
    if !status.is_finished() {
        return None;
    }
    if let Some(finished_at) = status.finished_at {
        return Some(format!("finished {}", format_age(finished_at)));
    }
    if status.finished_before_tracking {
        return Some("finished before tracking".into());
    }
    None
}

fn format_temp(value: f64) -> String {
    format!("{value:.0}C")
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

fn format_age(epoch: i64) -> String {
    let seconds = (now() - epoch).max(0);
    if seconds < 60 {
        return "just now".into();
    }
    let minutes = (seconds + 59) / 60;
    let hours = minutes / 60;
    let mins = minutes % 60;
    let label = if hours == 0 {
        format!("{minutes}m")
    } else if hours < 24 {
        format!("{hours}h {mins}m")
    } else {
        format!("{}d {}h", hours / 24, hours % 24)
    };

    let absolute = Local
        .timestamp_opt(epoch, 0)
        .single()
        .map(|time| time.format("%H:%M").to_string())
        .unwrap_or_default();
    if absolute.is_empty() {
        format!("{label} ago")
    } else {
        format!("{label} ago ({absolute})")
    }
}

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or(0)
}

fn read_json(path: &Path) -> Result<Value, String> {
    fs::read_to_string(path)
        .map_err(|err| err.to_string())
        .and_then(|text| serde_json::from_str(&text).map_err(|err| err.to_string()))
}

fn config_path() -> PathBuf {
    if let Ok(path) = env::var("BAMBU_WAYBAR_CONFIG") {
        if !path.trim().is_empty() {
            return PathBuf::from(path);
        }
    }
    let base = env::var("XDG_CONFIG_HOME")
        .ok()
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env::var("HOME").unwrap_or_else(|_| "/tmp".into())).join(".config")
        });
    base.join("bambu-waybar/config.json")
}

fn cache_path() -> PathBuf {
    cache_dir().join("bambu-status.json")
}

fn cache_dir() -> PathBuf {
    let base = env::var("XDG_CACHE_HOME")
        .ok()
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env::var("HOME").unwrap_or_else(|_| "/tmp".into())).join(".cache")
        });
    base.join("waybar")
}

fn helper_bin() -> PathBuf {
    let installed = PathBuf::from("/home/ben/.local/bin/waybar-helper");
    if installed.exists() {
        installed
    } else {
        PathBuf::from("/home/ben/dotfiles/waybar-helper/target/release/waybar-helper")
    }
}

fn install_css() {
    let provider = gtk::CssProvider::new();
    provider.load_from_string(CSS);
    if let Some(display) = gtk::gdk::Display::default() {
        gtk::style_context_add_provider_for_display(
            &display,
            &provider,
            gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
    }
}

fn label(text: &str, xalign: f32, class_name: &str) -> gtk::Label {
    let widget = gtk::Label::new(Some(text));
    widget.set_xalign(xalign);
    if !class_name.is_empty() {
        widget.add_css_class(class_name);
    }
    widget
}

fn wrapping_label(text: &str, class_name: &str) -> gtk::Label {
    let widget = label(text, 0.0, class_name);
    widget.set_wrap(true);
    widget.set_max_width_chars(38);
    widget
}

fn icon_button<F>(text: &str, tooltip: &str, callback: F) -> gtk::Button
where
    F: Fn() + 'static,
{
    icon_button_with_class(text, tooltip, "", callback)
}

fn icon_button_with_class<F>(
    text: &str,
    tooltip: &str,
    class_name: &str,
    callback: F,
) -> gtk::Button
where
    F: Fn() + 'static,
{
    let button = gtk::Button::with_label(text);
    if !class_name.is_empty() {
        button.add_css_class(class_name);
    }
    button.set_tooltip_text(Some(tooltip));
    button.connect_clicked(move |_| callback());
    button
}

fn pid_file() -> PathBuf {
    private_runtime_dir("bambu-printer-panel").join("bambu-printer-panel.pid")
}

fn private_runtime_dir(name: &str) -> PathBuf {
    if let Ok(xdg) = env::var("XDG_RUNTIME_DIR") {
        let path = PathBuf::from(xdg);
        if path.is_dir() {
            return path;
        }
    }

    let uid = unsafe { libc::getuid() };
    let candidates = [
        PathBuf::from(env::var("TMPDIR").unwrap_or_else(|_| "/tmp".into()))
            .join(format!("{name}-{uid}")),
        PathBuf::from(env::var("HOME").unwrap_or_else(|_| "/tmp".into()))
            .join(".cache")
            .join(name),
    ];
    for path in candidates {
        if fs::create_dir_all(&path).is_ok()
            && fs::metadata(&path)
                .map(|meta| meta.uid() == uid && meta.permissions().mode() & 0o077 == 0)
                .unwrap_or(false)
        {
            return path;
        }
        let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o700));
    }
    PathBuf::from(env::var("HOME").unwrap_or_else(|_| "/tmp".into())).join(".cache")
}

fn close_existing() -> bool {
    let path = pid_file();
    let Ok(text) = fs::read_to_string(&path) else {
        return false;
    };
    let Ok(pid) = text.trim().parse::<i32>() else {
        let _ = fs::remove_file(path);
        return false;
    };
    if process_exists(pid) {
        unsafe {
            libc::kill(pid, libc::SIGTERM);
        }
        true
    } else {
        let _ = fs::remove_file(path);
        false
    }
}

fn process_exists(pid: i32) -> bool {
    unsafe { libc::kill(pid, 0) == 0 }
}

fn cleanup_pidfile(path: &Path, pid: u32) {
    let Ok(text) = fs::read_to_string(path) else {
        return;
    };
    if text.trim() == pid.to_string() {
        let _ = fs::remove_file(path);
    }
}
