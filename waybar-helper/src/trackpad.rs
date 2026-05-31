//! Trackpad gestures for Hyprland, ported from `scripts/hypr-trackpad-gestures.py`.
//!
//! Reads raw multitouch events straight from the kernel evdev nodes
//! (`/dev/input/event*`, any device whose name contains "trackpad"/"touchpad")
//! and recognises two-finger edge swipes:
//!
//!   * right-edge two-finger swipe LEFT  → open the notification tray
//!   * two-finger swipe RIGHT to the edge → close it
//!
//! Event-driven (blocking `poll(2)`), no polling loop, ~0 CPU at rest. Uses only
//! `libc` (already a dependency) — the evdev protocol is a fixed-layout struct
//! plus a couple of ioctls, so there's no need for an external evdev crate and
//! the binary still builds offline with nothing extra to audit.
//!
//! Requires read access to the trackpad evdev node (user in the `input` group,
//! same as the Python version).

use std::collections::HashMap;
use std::ffi::CString;
use std::fs;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::io::RawFd;
use std::process::{Command, ExitCode};
use std::time::Instant;

// ── evdev constants (verified against this machine's <linux/input.h>) ──────────
const EV_SYN: u16 = 0;
const EV_ABS: u16 = 3;
const SYN_REPORT: u16 = 0;
const ABS_MT_SLOT: u16 = 47;
const ABS_MT_POSITION_X: u16 = 53;
const ABS_MT_POSITION_Y: u16 = 54;
const ABS_MT_TRACKING_ID: u16 = 57;

const TOUCH_NAMES: [&str; 2] = ["trackpad", "touchpad"];
const NOTIF_PANEL: &str = "/home/ben/dotfiles/scripts/waybar-notifications.sh";

// ── ioctl request numbers (asm-generic _IOC encoding; dir=READ=2, type='E') ────
fn eviocgname(len: u64) -> libc::c_ulong {
    ((2u64 << 30) | (len << 16) | (0x45 << 8) | 0x06) as libc::c_ulong
}
fn eviocgabs(abs: u16) -> libc::c_ulong {
    // sizeof(struct input_absinfo) == 24
    ((2u64 << 30) | (24u64 << 16) | (0x45 << 8) | (0x40 + abs as u64)) as libc::c_ulong
}

/// One kernel `struct input_event` is 24 bytes here: 16-byte timeval, then
/// u16 type, u16 code, i32 value. We only need the trailing three fields.
const EVENT_SIZE: usize = 24;

struct Event {
    kind: u16,
    code: u16,
    value: i32,
}

fn parse_event(buf: &[u8]) -> Event {
    Event {
        kind: u16::from_ne_bytes([buf[16], buf[17]]),
        code: u16::from_ne_bytes([buf[18], buf[19]]),
        value: i32::from_ne_bytes([buf[20], buf[21], buf[22], buf[23]]),
    }
}

// ── touch + pad state ──────────────────────────────────────────────────────────
#[derive(Default, Clone)]
struct Touch {
    tracking_id: i32,
    // None until the first ABS_MT_POSITION_* after the tracking id flips
    // positive — avoids confusing a fresh touch with a stale slot.
    start_x: Option<i32>,
    start_y: Option<i32>,
    cur_x: Option<i32>,
    cur_y: Option<i32>,
    started_at: Option<Instant>,
}

#[derive(Clone, Copy)]
struct Pad {
    x_max: i32,
    x_min: i32,
    x_res: i32,
}

impl Pad {
    fn x_range(&self) -> i32 {
        self.x_max - self.x_min
    }
    fn x_mm(&self, dx_units: f64) -> f64 {
        dx_units / self.x_res.max(1) as f64
    }
    fn edge_x(&self, frac: f64) -> i32 {
        (self.x_max as f64 - frac * self.x_range() as f64) as i32
    }
}

// ── detectors (same thresholds as the Python version) ──────────────────────────
const OPEN_EDGE_FRAC: f64 = 0.18;
const CLOSE_END_EDGE_FRAC: f64 = 0.14;
const TRIGGER_MM: f64 = 18.0;
const MAX_TIME_S: f64 = 0.9;

fn within_time(touches: &[&Touch]) -> bool {
    touches
        .iter()
        .filter_map(|t| t.started_at)
        .map(|s| s.elapsed().as_secs_f64())
        .fold(0.0_f64, f64::max)
        <= MAX_TIME_S
}

/// Two fingers, both starting in the rightmost OPEN_EDGE_FRAC, drifting LEFT at
/// least TRIGGER_MM before lift → open the tray.
fn detect_open(touches: &[&Touch], pad: &Pad) -> bool {
    if touches.len() != 2 {
        return false;
    }
    let edge_x = pad.edge_x(OPEN_EDGE_FRAC);
    if !touches.iter().all(|t| t.start_x.unwrap_or(i32::MIN) >= edge_x) {
        return false;
    }
    if !within_time(touches) {
        return false;
    }
    let dx_avg: f64 = touches
        .iter()
        .map(|t| (t.cur_x.unwrap_or(0) - t.start_x.unwrap_or(0)) as f64)
        .sum::<f64>()
        / 2.0;
    pad.x_mm(-dx_avg) >= TRIGGER_MM
}

/// Two fingers pushed RIGHT by at least TRIGGER_MM and ending in the rightmost
/// CLOSE_END_EDGE_FRAC → close the tray.
fn detect_close(touches: &[&Touch], pad: &Pad) -> bool {
    if touches.len() != 2 {
        return false;
    }
    if !within_time(touches) {
        return false;
    }
    let edge_x = pad.edge_x(CLOSE_END_EDGE_FRAC);
    if !touches.iter().all(|t| t.cur_x.unwrap_or(i32::MIN) >= edge_x) {
        return false;
    }
    let dx_avg: f64 = touches
        .iter()
        .map(|t| (t.cur_x.unwrap_or(0) - t.start_x.unwrap_or(0)) as f64)
        .sum::<f64>()
        / 2.0;
    pad.x_mm(dx_avg) >= TRIGGER_MM
}

fn fire_panel(verb: &str) {
    if !std::path::Path::new(NOTIF_PANEL).is_file() {
        return;
    }
    // Detached so the daemon never blocks or collects zombies (mirrors the
    // Python start_new_session spawn).
    let _ = Command::new("setsid")
        .arg("-f")
        .args(["bash", NOTIF_PANEL, verb])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
}

struct Tracker {
    pad: Pad,
    slots: HashMap<i32, Touch>,
    cur_slot: i32,
    gesture_locked: bool,
}

impl Tracker {
    fn new(pad: Pad) -> Self {
        Tracker {
            pad,
            slots: HashMap::new(),
            cur_slot: 0,
            gesture_locked: false,
        }
    }

    fn active(&self) -> Vec<&Touch> {
        self.slots
            .values()
            .filter(|t| t.tracking_id >= 0 && t.start_x.is_some() && t.cur_x.is_some())
            .collect()
    }

    /// Apply one event; returns true on SYN_REPORT (frame boundary).
    fn handle(&mut self, ev: &Event) -> bool {
        if ev.kind == EV_ABS {
            self.handle_abs(ev);
        }
        ev.kind == EV_SYN && ev.code == SYN_REPORT
    }

    fn handle_abs(&mut self, ev: &Event) {
        if ev.code == ABS_MT_SLOT {
            self.cur_slot = ev.value;
            return;
        }
        let slot = self.slots.entry(self.cur_slot).or_default();
        match ev.code {
            ABS_MT_TRACKING_ID => {
                if ev.value < 0 {
                    slot.tracking_id = -1;
                    slot.start_x = None;
                    slot.start_y = None;
                    slot.cur_x = None;
                    slot.cur_y = None;
                } else {
                    slot.tracking_id = ev.value;
                    slot.start_x = None;
                    slot.start_y = None;
                    slot.cur_x = None;
                    slot.cur_y = None;
                    slot.started_at = Some(Instant::now());
                }
            }
            ABS_MT_POSITION_X => {
                slot.cur_x = Some(ev.value);
                if slot.tracking_id >= 0 && slot.start_x.is_none() {
                    slot.start_x = Some(ev.value);
                }
            }
            ABS_MT_POSITION_Y => {
                slot.cur_y = Some(ev.value);
                if slot.tracking_id >= 0 && slot.start_y.is_none() {
                    slot.start_y = Some(ev.value);
                }
            }
            _ => {}
        }
    }

    fn frame(&mut self) {
        let active = self.active();
        if active.is_empty() {
            if self.gesture_locked {
                self.gesture_locked = false;
            }
            return;
        }
        if self.gesture_locked {
            return;
        }
        // Open is checked before close, matching the Python DETECTORS order.
        if detect_open(&active, &self.pad) {
            fire_panel("open");
            self.gesture_locked = true;
        } else if detect_close(&active, &self.pad) {
            fire_panel("close");
            self.gesture_locked = true;
        }
    }
}

// ── device discovery ───────────────────────────────────────────────────────────
struct Device {
    fd: RawFd,
    tracker: Tracker,
}

fn device_name(fd: RawFd) -> Option<String> {
    let mut buf = [0u8; 256];
    let r = unsafe { libc::ioctl(fd, eviocgname(256), buf.as_mut_ptr()) };
    if r < 0 {
        return None;
    }
    let end = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
    Some(String::from_utf8_lossy(&buf[..end]).to_string())
}

/// EVIOCGABS → (min, max, resolution) for one axis. None if unreadable.
fn abs_axis(fd: RawFd, axis: u16) -> Option<(i32, i32, i32)> {
    // struct input_absinfo { i32 value, minimum, maximum, fuzz, flat, resolution }
    let mut info = [0i32; 6];
    let r = unsafe { libc::ioctl(fd, eviocgabs(axis), info.as_mut_ptr()) };
    if r < 0 {
        return None;
    }
    Some((info[1], info[2], info[5]))
}

fn pad_geometry(fd: RawFd) -> Option<Pad> {
    let (x_min, x_max, x_res) = abs_axis(fd, ABS_MT_POSITION_X)?;
    let (_y_min, _y_max, y_res) = abs_axis(fd, ABS_MT_POSITION_Y)?;
    if x_res == 0 || y_res == 0 {
        return None; // no physical resolution → can't convert to mm
    }
    Some(Pad {
        x_min,
        x_max,
        x_res,
    })
}

fn open_trackpads() -> Vec<Device> {
    let mut out = Vec::new();
    let entries = match fs::read_dir("/dev/input") {
        Ok(e) => e,
        Err(_) => return out,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path
            .file_name()
            .and_then(|s| s.to_str())
            .map(|s| s.starts_with("event"))
            .unwrap_or(false)
        {
            continue;
        }
        let cpath = match CString::new(path.as_os_str().as_bytes()) {
            Ok(c) => c,
            Err(_) => continue,
        };
        let fd = unsafe {
            libc::open(
                cpath.as_ptr(),
                libc::O_RDONLY | libc::O_NONBLOCK | libc::O_CLOEXEC,
            )
        };
        if fd < 0 {
            continue;
        }
        let is_pad = device_name(fd)
            .map(|n| {
                let n = n.to_lowercase();
                TOUCH_NAMES.iter().any(|kw| n.contains(kw))
            })
            .unwrap_or(false);
        if !is_pad {
            unsafe { libc::close(fd) };
            continue;
        }
        match pad_geometry(fd) {
            Some(pad) => out.push(Device {
                fd,
                tracker: Tracker::new(pad),
            }),
            None => unsafe {
                libc::close(fd);
            },
        }
    }
    out
}

/// Drain all currently-available events from one fd, dispatching frames.
/// Returns false on a hard read error (fd gone) so the caller can exit.
fn drain(dev: &mut Device) -> bool {
    let mut buf = [0u8; EVENT_SIZE * 64];
    loop {
        let n = unsafe {
            libc::read(
                dev.fd,
                buf.as_mut_ptr() as *mut libc::c_void,
                buf.len(),
            )
        };
        if n > 0 {
            let n = n as usize;
            let mut off = 0;
            while off + EVENT_SIZE <= n {
                let ev = parse_event(&buf[off..off + EVENT_SIZE]);
                if dev.tracker.handle(&ev) {
                    dev.tracker.frame();
                }
                off += EVENT_SIZE;
            }
            // A short read means the buffer's drained; otherwise keep reading.
            if n < buf.len() {
                return true;
            }
        } else if n == 0 {
            return true;
        } else {
            let err = std::io::Error::last_os_error();
            match err.raw_os_error() {
                Some(libc::EAGAIN) => return true, // nothing left this wake
                Some(libc::EINTR) => continue,
                _ => return false, // device disappeared
            }
        }
    }
}

pub fn run() -> ExitCode {
    let mut devices = open_trackpads();
    if devices.is_empty() {
        eprintln!(
            "waybar-helper trackpad: no usable trackpad (need read on \
             /dev/input/event*; user must be in the input group)"
        );
        return ExitCode::from(1);
    }

    let mut pfds: Vec<libc::pollfd> = devices
        .iter()
        .map(|d| libc::pollfd {
            fd: d.fd,
            events: libc::POLLIN,
            revents: 0,
        })
        .collect();

    loop {
        let r = unsafe { libc::poll(pfds.as_mut_ptr(), pfds.len() as libc::nfds_t, -1) };
        if r < 0 {
            let err = std::io::Error::last_os_error();
            if err.raw_os_error() == Some(libc::EINTR) {
                continue;
            }
            return ExitCode::from(1);
        }
        for i in 0..pfds.len() {
            if pfds[i].revents & libc::POLLIN != 0 && !drain(&mut devices[i]) {
                return ExitCode::SUCCESS; // a device went away; exit like the Python did
            }
        }
    }
}
