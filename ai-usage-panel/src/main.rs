use chrono::{DateTime, Local, TimeZone};
use gtk::gio;
use gtk::glib::{self, ControlFlow, Propagation};
use gtk::pango;
use gtk::prelude::*;
use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
use serde_json::Value;
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::env;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::rc::Rc;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

const PANEL_TOP_MARGIN: i32 = 6;
const PANEL_WIDTH: i32 = 340;
const CSS: &str = r#"
#ai-usage-panel {
  background: transparent;
}
.panel {
  font-family: "SF Pro Text", "Symbols Nerd Font", "Font Awesome 6 Free", sans-serif;
  background:
    linear-gradient(150deg, rgba(255,255,255,0.18), rgba(255,255,255,0.05) 38%,
      rgba(51,204,255,0.08) 66%, rgba(192,132,245,0.10)),
    rgba(8, 11, 20, 0.74);
  border: 1px solid rgba(255,255,255,0.30);
  border-radius: 16px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.34);
  padding: 12px;
}
.title  { color: #f4f7fb; font-size: 14px; font-weight: 700; }
.sub    { color: rgba(244,247,251,0.58); font-size: 11px; }
.win    { color: rgba(244,247,251,0.82); font-size: 12px; font-weight: 700; }
.pct    { color: #f4f7fb; font-size: 12px; font-weight: 700; }
.reset  { color: rgba(244,247,251,0.58); font-size: 11px; }
.extra  { color: rgba(244,247,251,0.68); font-size: 11px; }
.account { color: #f4f7fb; font-size: 12px; font-weight: 700; }
.account-section {
  background: rgba(255,255,255,0.045);
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 10px;
  padding: 8px;
}
.active-account-section {
  background: rgba(125,211,252,0.12);
  border-color: rgba(125,211,252,0.58);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.16);
}
.account-row { padding: 0; }
.active-badge {
  color: #06121a;
  background: #7dd3fc;
  border-radius: 8px;
  padding: 2px 7px;
  font-size: 10px;
  font-weight: 800;
}
label { color: #f4f7fb; font-size: 12px; }
.usage-window {
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.13);
  border-radius: 9px;
  padding: 7px 8px;
}
button {
  color: #f4f7fb;
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.11);
  border-radius: 8px;
  padding: 6px 8px;
  font-size: 12px;
}
button:hover { background: rgba(255,255,255,0.12); }
button.close {
  min-width: 24px;
  min-height: 24px;
  padding: 0;
  border-radius: 12px;
  font-size: 13px;
}
button.icon {
  min-width: 30px;
  min-height: 28px;
  padding: 0;
  border-radius: 9px;
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
  background: #7dd3fc;
}
progressbar.warn progress { background: #f8df9b; }
progressbar.danger progress { background: #ff6b6b; }
"#;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Service {
    Codex,
    Claude,
}

impl Service {
    fn parse(value: &str) -> Option<Self> {
        match value {
            "codex" => Some(Self::Codex),
            "claude" => Some(Self::Claude),
            _ => None,
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::Codex => "codex",
            Self::Claude => "claude",
        }
    }

    fn other(self) -> Self {
        match self {
            Self::Codex => Self::Claude,
            Self::Claude => Self::Codex,
        }
    }

    fn title(self) -> &'static str {
        self.as_str()
    }

    fn left_margin(self) -> i32 {
        match self {
            Self::Codex => 142,
            Self::Claude => 266,
        }
    }

    fn url(self) -> &'static str {
        match self {
            Self::Codex => "https://chatgpt.com/codex/settings/usage",
            Self::Claude => "https://claude.ai/settings/usage",
        }
    }

    fn refresh_signal(self) -> &'static str {
        match self {
            Self::Codex => "8",
            Self::Claude => "9",
        }
    }

    fn icon(self) -> PathBuf {
        assets_dir().join(match self {
            Self::Codex => "openai.png",
            Self::Claude => "claude.png",
        })
    }

    fn cache(self) -> PathBuf {
        cache_dir().join(match self {
            Self::Codex => "codex-usage.json",
            Self::Claude => "claude-usage.json",
        })
    }
}

#[derive(Clone, Debug)]
struct UsageWindow {
    label: String,
    remaining: i32,
    reset_epoch: Option<i64>,
}

#[derive(Clone, Debug)]
struct CodexCard {
    label: String,
    plan: String,
    slot: String,
    active: bool,
    windows: Vec<UsageWindow>,
    extra: String,
    age: Option<i64>,
    error: String,
}

struct PanelState {
    app: gtk::Application,
    window: gtk::ApplicationWindow,
    panel: gtk::Box,
    service: Service,
    codex_cards: RefCell<Vec<CodexCard>>,
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
        let header = gtk::Box::new(gtk::Orientation::Horizontal, 8);
        if self.service.icon().exists() {
            let image = gtk::Image::from_file(self.service.icon());
            image.set_pixel_size(20);
            header.append(&image);
        }

        let title = label(&format!("{} usage", self.service.title()), 0.0, "title");
        title.set_hexpand(true);
        header.append(&title);
        header.append(&icon_button("x", "close", "close", {
            let state = Rc::clone(self);
            move || state.close()
        }));
        self.panel.append(&header);

        if self.service == Service::Codex {
            self.build_codex_profiles();
        } else {
            match load_usage(self.service) {
                Ok((windows, extra, age)) => {
                    for usage in windows {
                        self.panel.append(&window_row(&usage));
                    }
                    if !extra.is_empty() {
                        let extra_label = wrapping_label(&extra, "extra");
                        self.panel.append(&extra_label);
                    }
                    if let Some(age) = age {
                        self.panel.append(&label(&updated_label(age), 0.0, "reset"));
                    }
                }
                Err(message) => {
                    self.panel.append(&wrapping_label(&message, ""));
                }
            }
        }

        let actions = gtk::Box::new(gtk::Orientation::Horizontal, 6);
        actions.set_halign(gtk::Align::End);
        if self.service == Service::Codex {
            if let Some(next_slot) = self.next_codex_slot() {
                actions.append(&icon_button("swap", "swap profile", "icon", {
                    let state = Rc::clone(self);
                    move || state.activate_codex(next_slot.clone())
                }));
            }
        }
        actions.append(&icon_button("refresh", "refresh", "icon", {
            let state = Rc::clone(self);
            move || state.refresh()
        }));
        actions.append(&icon_button("open", "open usage", "icon", {
            let state = Rc::clone(self);
            move || state.open_url()
        }));
        if self.service == Service::Codex {
            actions.append(&icon_button("acct", "account", "icon", {
                let state = Rc::clone(self);
                move || state.account_menu()
            }));
        }
        self.panel.append(&actions);
    }

    fn build_codex_profiles(self: &Rc<Self>) {
        let cards = load_codex_accounts();
        self.codex_cards.replace(cards.clone());
        if cards.is_empty() {
            self.panel.append(&wrapping_label(
                "no cached usage yet; open the bar, then refresh.",
                "",
            ));
            return;
        }

        for card in cards {
            let section = gtk::Box::new(gtk::Orientation::Vertical, 7);
            section.add_css_class("account-section");
            if card.active {
                section.add_css_class("active-account-section");
            }
            section.append(&self.account_row(&card));
            if !card.error.is_empty() {
                section.append(&wrapping_label(
                    &format!("refresh failed: {}", card.error),
                    "extra",
                ));
                self.panel.append(&section);
                continue;
            }
            for usage in &card.windows {
                section.append(&window_row(usage));
            }

            let mut extra = card.extra.clone();
            if let Some(age) = card.age {
                if extra.is_empty() {
                    extra = updated_label(age);
                } else {
                    extra.push_str("  -  ");
                    extra.push_str(&updated_label(age));
                }
            }
            if !extra.is_empty() {
                section.append(&wrapping_label(&extra, "extra"));
            }
            self.panel.append(&section);
        }
    }

    fn account_row(self: &Rc<Self>, card: &CodexCard) -> gtk::Box {
        let row = gtk::Box::new(gtk::Orientation::Horizontal, 8);
        row.add_css_class("account-row");

        let mut text = card.label.clone();
        if !card.plan.is_empty() {
            text.push_str(&format!(" ({})", card.plan));
        }
        let account_label = label(&text, 0.0, "account");
        account_label.set_ellipsize(pango::EllipsizeMode::End);
        account_label.set_hexpand(true);
        row.append(&account_label);

        if card.active {
            row.append(&label("ACTIVE", 1.0, "active-badge"));
        } else if !card.slot.is_empty() {
            let slot = card.slot.clone();
            row.append(&icon_button(">", "use this profile", "icon", {
                let state = Rc::clone(self);
                move || state.activate_codex(slot.clone())
            }));
        }
        row
    }

    fn next_codex_slot(&self) -> Option<String> {
        self.codex_cards
            .borrow()
            .iter()
            .find(|card| !card.active && !card.slot.is_empty())
            .map(|card| card.slot.clone())
    }

    fn refresh(self: &Rc<Self>) {
        let service = self.service;
        self.run_then_repopulate(move || {
            run_refresh(service);
        });
    }

    fn activate_codex(self: &Rc<Self>, slot: String) {
        let service = self.service;
        self.run_then_repopulate(move || {
            let _ = Command::new(scripts_dir().join("ai_accounts.py"))
                .arg("codex-activate")
                .arg(slot)
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            run_refresh(service);
        });
    }

    fn run_then_repopulate<F>(self: &Rc<Self>, work: F)
    where
        F: FnOnce() + Send + 'static,
    {
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            work();
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

    fn open_url(&self) {
        let _ = Command::new("xdg-open")
            .arg(self.service.url())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
        self.close();
    }

    fn account_menu(&self) {
        let _ = Command::new(scripts_dir().join("ai_accounts.py"))
            .arg("codex-menu")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
        self.close();
    }
}

fn main() {
    let arg = env::args().nth(1).unwrap_or_else(|| "codex".to_string());
    let Some(service) = Service::parse(&arg) else {
        eprintln!("usage: ai-usage-panel codex|claude");
        std::process::exit(2);
    };
    std::process::exit(launch(service));
}

fn launch(service: Service) -> i32 {
    if close_existing(service) {
        return 0;
    }
    close_existing(service.other());

    let pid_path = pid_file(service);
    if let Some(parent) = pid_path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let pid = std::process::id();
    let _ = fs::write(&pid_path, pid.to_string());

    let app = gtk::Application::builder()
        .application_id("dev.ben.ai_usage_panel")
        .flags(gio::ApplicationFlags::NON_UNIQUE)
        .build();

    let cleanup_path = pid_path.clone();
    app.connect_shutdown(move |_| cleanup_pidfile(&cleanup_path, pid));
    app.connect_activate(move |app| build_panel(app, service));

    let _ = app.run_with_args(&["ai-usage-panel"]);
    0
}

fn build_panel(app: &gtk::Application, service: Service) {
    install_css();

    let window = gtk::ApplicationWindow::builder()
        .application(app)
        .title(format!("ai-usage-{}", service.as_str()))
        .decorated(false)
        .resizable(false)
        .default_width(PANEL_WIDTH)
        .build();
    window.set_widget_name("ai-usage-panel");
    window.set_focusable(true);
    window.init_layer_shell();
    window.set_namespace(Some("ai-usage"));
    window.set_layer(Layer::Overlay);
    window.set_keyboard_mode(KeyboardMode::OnDemand);
    window.set_anchor(Edge::Top, true);
    window.set_anchor(Edge::Left, true);
    window.set_margin(Edge::Top, PANEL_TOP_MARGIN);
    window.set_margin(Edge::Left, service.left_margin());

    let panel = gtk::Box::new(gtk::Orientation::Vertical, 8);
    panel.add_css_class("panel");
    window.set_child(Some(&panel));

    let state = Rc::new(PanelState {
        app: app.clone(),
        window: window.clone(),
        panel,
        service,
        codex_cards: RefCell::new(Vec::new()),
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
    widget.set_max_width_chars(42);
    widget
}

fn icon_button<F>(text: &str, tooltip: &str, class_name: &str, callback: F) -> gtk::Button
where
    F: Fn() + 'static,
{
    let button = gtk::Button::with_label(text);
    button.add_css_class(class_name);
    button.set_tooltip_text(Some(tooltip));
    button.connect_clicked(move |_| callback());
    button
}

fn window_row(window: &UsageWindow) -> gtk::Box {
    let remaining = window.remaining.clamp(0, 100);
    let box_ = gtk::Box::new(gtk::Orientation::Vertical, 4);
    box_.add_css_class("usage-window");

    let top = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    let name = label(&window.label, 0.0, "win");
    name.set_hexpand(true);
    let pct = label(&format!("{remaining}% left"), 1.0, "pct");
    pct.set_ellipsize(pango::EllipsizeMode::End);
    top.append(&name);
    top.append(&pct);
    box_.append(&top);

    let progress = gtk::ProgressBar::new();
    progress.set_fraction(remaining as f64 / 100.0);
    if remaining <= 10 {
        progress.add_css_class("danger");
    } else if remaining <= 30 {
        progress.add_css_class("warn");
    }
    box_.append(&progress);

    let (left, right) = reset_parts(window.reset_epoch);
    let reset_row = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    let reset = label(&left, 0.0, "reset");
    reset.set_ellipsize(pango::EllipsizeMode::End);
    reset.set_hexpand(true);
    reset_row.append(&reset);
    if !right.is_empty() {
        reset_row.append(&label(&right, 1.0, "reset"));
    }
    box_.append(&reset_row);
    box_
}

fn load_usage(service: Service) -> Result<(Vec<UsageWindow>, String, Option<i64>), String> {
    let data = read_json(&service.cache())
        .ok_or_else(|| "no cached usage yet; open the bar, then refresh.".to_string())?;
    let age = age_min(&data);
    if let Some(error) = data.get("error").and_then(Value::as_str) {
        return Err(format!("refresh failed: {error}"));
    }

    match service {
        Service::Codex => {
            let limits = data.get("limits").unwrap_or(&Value::Null);
            let primary = limits.get("primary").unwrap_or(&Value::Null);
            let secondary = limits.get("secondary").unwrap_or(&Value::Null);
            let mut windows = vec![UsageWindow {
                label: "5-hour".to_string(),
                remaining: remaining_from_used(primary),
                reset_epoch: primary.get("resetsAt").and_then(Value::as_i64),
            }];
            if secondary.is_object() {
                windows.push(UsageWindow {
                    label: "weekly".to_string(),
                    remaining: remaining_from_used(secondary),
                    reset_epoch: secondary.get("resetsAt").and_then(Value::as_i64),
                });
            }

            let account = data.get("account").unwrap_or(&Value::Null);
            let mut extras = Vec::new();
            if let Some(label) = account
                .get("label")
                .or_else(|| account.get("email"))
                .and_then(Value::as_str)
            {
                extras.push(format!("account: {label}"));
            }
            extras.push(codex_extra(&data));
            Ok((windows, extras.join("  -  "), age))
        }
        Service::Claude => {
            let usage = data.get("usage").unwrap_or(&Value::Null);
            let five_hour = usage.get("five_hour").unwrap_or(&Value::Null);
            let seven_day = usage.get("seven_day").unwrap_or(&Value::Null);
            let mut windows = vec![UsageWindow {
                label: "5-hour".to_string(),
                remaining: 100 - round_percent(five_hour.get("utilization")),
                reset_epoch: five_hour.get("resets_at").and_then(iso_epoch),
            }];
            if seven_day.is_object() {
                windows.push(UsageWindow {
                    label: "weekly".to_string(),
                    remaining: 100 - round_percent(seven_day.get("utilization")),
                    reset_epoch: seven_day.get("resets_at").and_then(iso_epoch),
                });
            }
            let extra = usage
                .get("extra_usage")
                .filter(|value| value.is_object())
                .map(|extra| {
                    if extra
                        .get("is_enabled")
                        .and_then(Value::as_bool)
                        .unwrap_or(false)
                    {
                        "extra usage: enabled".to_string()
                    } else {
                        "extra usage: disabled".to_string()
                    }
                })
                .unwrap_or_default();
            Ok((windows, extra, age))
        }
    }
}

fn load_codex_accounts() -> Vec<CodexCard> {
    let active_slot = active_codex_slot();
    let mut paths = vec![Service::Codex.cache()];
    if let Ok(entries) = fs::read_dir(cache_dir()) {
        let mut extra: Vec<PathBuf> = entries
            .flatten()
            .map(|entry| entry.path())
            .filter(|path| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.starts_with("codex-usage-") && name.ends_with(".json"))
            })
            .collect();
        extra.sort();
        paths.extend(extra);
    }

    let mut chosen: HashMap<String, (Value, PathBuf, (i32, i32, i32, i64))> = HashMap::new();
    for path in paths {
        let Some(data) = read_json(&path) else {
            continue;
        };
        let account = data.get("account").unwrap_or(&Value::Null);
        let identity = account_identity(account, &path);
        if identity.is_empty() {
            continue;
        }
        let rank = cache_rank(&data, &path, &active_slot);
        match chosen.get(&identity) {
            Some((_, _, existing_rank)) if *existing_rank <= rank => {}
            _ => {
                chosen.insert(identity, (data, path, rank));
            }
        }
    }

    let mut cards = Vec::new();
    for (data, path, _) in chosen.into_values() {
        let account = data.get("account").unwrap_or(&Value::Null);
        let slot = account
            .get("slot")
            .and_then(Value::as_str)
            .map(str::to_string)
            .filter(|slot| !slot.is_empty())
            .unwrap_or_else(|| slot_from_cache_path(&path));
        let plan = account
            .get("plan")
            .or_else(|| data.get("limits").and_then(|limits| limits.get("planType")))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();

        cards.push(CodexCard {
            label: account_label(account, &slot),
            plan,
            active: !active_slot.is_empty() && slot == active_slot,
            slot,
            windows: codex_windows(&data),
            extra: codex_extra(&data),
            age: age_min(&data),
            error: data
                .get("error")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        });
    }

    cards.sort_by(|left, right| {
        (
            if left.active { 0 } else { 1 },
            left.label.to_lowercase(),
            left.slot.clone(),
        )
            .cmp(&(
                if right.active { 0 } else { 1 },
                right.label.to_lowercase(),
                right.slot.clone(),
            ))
    });
    cards
}

fn codex_windows(data: &Value) -> Vec<UsageWindow> {
    let limits = data.get("limits").unwrap_or(&Value::Null);
    let primary = limits.get("primary").unwrap_or(&Value::Null);
    let secondary = limits.get("secondary").unwrap_or(&Value::Null);
    let mut windows = vec![UsageWindow {
        label: "5-hour".to_string(),
        remaining: remaining_from_used(primary),
        reset_epoch: primary.get("resetsAt").and_then(Value::as_i64),
    }];
    if secondary.is_object() {
        windows.push(UsageWindow {
            label: "weekly".to_string(),
            remaining: remaining_from_used(secondary),
            reset_epoch: secondary.get("resetsAt").and_then(Value::as_i64),
        });
    }
    windows
}

fn remaining_from_used(window: &Value) -> i32 {
    100 - round_percent(window.get("usedPercent"))
}

fn round_percent(value: Option<&Value>) -> i32 {
    value.and_then(Value::as_f64).unwrap_or(0.0).round() as i32
}

fn codex_extra(data: &Value) -> String {
    let credits = data
        .get("limits")
        .and_then(|limits| limits.get("credits"))
        .unwrap_or(&Value::Null);
    if credits
        .get("unlimited")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        "credits: unlimited".to_string()
    } else {
        format!(
            "credits: {}",
            credits
                .get("balance")
                .and_then(Value::as_str)
                .unwrap_or("0")
        )
    }
}

fn account_label(account: &Value, slot: &str) -> String {
    account
        .get("label")
        .or_else(|| account.get("email"))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| {
            if slot.is_empty() {
                "codex account"
            } else {
                slot
            }
        })
        .to_string()
}

fn account_identity(account: &Value, path: &Path) -> String {
    account
        .get("account_id")
        .or_else(|| account.get("email"))
        .or_else(|| account.get("slot"))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| {
            let slot = slot_from_cache_path(path);
            if !slot.is_empty() {
                slot
            } else {
                path.file_stem()
                    .and_then(|stem| stem.to_str())
                    .unwrap_or("")
                    .to_string()
            }
        })
}

fn cache_rank(data: &Value, path: &Path, active_slot: &str) -> (i32, i32, i32, i64) {
    let account = data.get("account").unwrap_or(&Value::Null);
    let slot = account
        .get("slot")
        .and_then(Value::as_str)
        .map(str::to_string)
        .filter(|slot| !slot.is_empty())
        .unwrap_or_else(|| slot_from_cache_path(path));
    let account_id = account
        .get("account_id")
        .and_then(Value::as_str)
        .unwrap_or("");
    let updated = data
        .get("updated_at")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    (
        if slot == active_slot { 0 } else { 1 },
        if is_auto_slot(&slot, account_id) {
            1
        } else {
            0
        },
        if path.file_name().and_then(|name| name.to_str()) == Some("codex-usage.json") {
            1
        } else {
            0
        },
        -(updated * 1000.0) as i64,
    )
}

fn is_auto_slot(slot: &str, account_id: &str) -> bool {
    if account_id.len() < 8 {
        return false;
    }
    slot.to_lowercase()
        .ends_with(&format!("-{}", account_id[..8].to_lowercase()))
}

fn slot_from_cache_path(path: &Path) -> String {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return String::new();
    };
    name.strip_prefix("codex-usage-")
        .and_then(|name| name.strip_suffix(".json"))
        .unwrap_or("")
        .to_string()
}

fn active_codex_slot() -> String {
    fs::read_to_string(home_dir().join(".codex/accounts/active"))
        .map(|slot| slot.trim().to_string())
        .unwrap_or_default()
}

fn read_json(path: &Path) -> Option<Value> {
    fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
}

fn age_min(data: &Value) -> Option<i64> {
    let updated = data.get("updated_at").and_then(Value::as_f64)?;
    if updated <= 0.0 {
        return None;
    }
    let age = (Local::now().timestamp() as f64 - updated) / 60.0;
    Some(age.floor().max(0.0) as i64)
}

fn updated_label(age: i64) -> String {
    if age <= 0 {
        "updated just now".to_string()
    } else {
        format!("updated {age}m ago")
    }
}

fn reset_parts(epoch: Option<i64>) -> (String, String) {
    let Some(epoch) = epoch else {
        return ("reset time unknown".to_string(), String::new());
    };
    let now = Local::now();
    let Some(when) = Local.timestamp_opt(epoch, 0).single() else {
        return ("reset time unknown".to_string(), String::new());
    };
    let delta = epoch - now.timestamp();
    if delta <= 0 {
        return ("resets now".to_string(), String::new());
    }
    let stamp = if when.date_naive() == now.date_naive() {
        when.format("%H:%M").to_string()
    } else {
        when.format("%a %H:%M").to_string().to_lowercase()
    };
    (format!("resets {stamp}"), compact_duration(delta))
}

fn compact_duration(seconds: i64) -> String {
    let minutes = ((seconds as f64) / 60.0).round().max(1.0) as i64;
    let days = minutes / (24 * 60);
    let rem_minutes = minutes % (24 * 60);
    let hours = rem_minutes / 60;
    let mins = rem_minutes % 60;
    if days > 0 {
        if hours > 0 {
            format!("{days}d {hours}h")
        } else {
            format!("{days}d")
        }
    } else if hours > 0 {
        if mins > 0 {
            format!("{hours}h {mins}m")
        } else {
            format!("{hours}h")
        }
    } else {
        format!("{mins}m")
    }
}

fn iso_epoch(value: &Value) -> Option<i64> {
    DateTime::parse_from_rfc3339(value.as_str()?)
        .ok()
        .map(|timestamp| timestamp.timestamp())
}

fn run_refresh(service: Service) {
    let _ = Command::new(scripts_dir().join("waybar-ai-refresh.sh"))
        .arg(service.as_str())
        .arg(service.refresh_signal())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

fn close_existing(service: Service) -> bool {
    let path = pid_file(service);
    let Ok(text) = fs::read_to_string(&path) else {
        return false;
    };
    let Ok(pid) = text.trim().parse::<i32>() else {
        let _ = fs::remove_file(&path);
        return false;
    };

    if process_exists(pid) {
        let _ = fs::remove_file(&path);
        unsafe {
            libc::kill(pid, libc::SIGTERM);
        }
        true
    } else {
        let _ = fs::remove_file(&path);
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

fn pid_file(service: Service) -> PathBuf {
    private_runtime_dir("ai-usage-panel").join(format!("ai-usage-{}.pid", service.as_str()))
}

fn private_runtime_dir(name: &str) -> PathBuf {
    if let Some(path) = env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from) {
        if path.is_dir() {
            return path;
        }
    }

    let uid = unsafe { libc::getuid() };
    let candidates = [
        env::var_os("TMPDIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/tmp"))
            .join(format!("{name}-{uid}")),
        home_dir().join(".cache").join(name),
    ];

    for path in candidates {
        if fs::create_dir_all(&path).is_err() {
            continue;
        }
        let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o700));
        if path.is_dir() {
            return path;
        }
    }

    home_dir().join(".cache")
}

fn home_dir() -> PathBuf {
    env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/home/ben"))
}

fn cache_dir() -> PathBuf {
    env::var_os("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home_dir().join(".cache"))
        .join("waybar")
}

fn scripts_dir() -> PathBuf {
    home_dir().join("dotfiles/scripts")
}

fn assets_dir() -> PathBuf {
    home_dir().join("dotfiles/assets")
}
