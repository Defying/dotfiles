#!/usr/bin/env python3
"""Small lease arbiter for Hyprland's single global screen_shader option."""

import fcntl
import json
import os
import subprocess
import time
from pathlib import Path


DOTFILES_DIR = Path.home() / "dotfiles"
ROUNDED_SHADER = DOTFILES_DIR / "config/hypr/shaders/rounded-corners.frag"


def _runtime_dir():
    base = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/glass-shader-{os.getuid()}"))
    path = base / "glass-shader"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


RUNTIME_DIR = _runtime_dir()
LEASE_DIR = RUNTIME_DIR / "leases"
LOCK_FILE = RUNTIME_DIR / "lock"
LEASE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)


def hyprctl(args, *, capture=False):
    cmd = ["hyprctl", *args]
    try:
        if capture:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        completed = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return "" if capture else False


def _owner_path(owner):
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in owner)
    return LEASE_DIR / f"{safe}.json"


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _read_leases():
    leases = []
    for path in LEASE_DIR.glob("*.json"):
        try:
            lease = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            continue
        if not _pid_alive(lease.get("pid")):
            path.unlink(missing_ok=True)
            continue
        if not lease.get("shader"):
            path.unlink(missing_ok=True)
            continue
        leases.append(lease)
    return leases


def _active_lease(leases):
    if not leases:
        return None
    return max(leases, key=lambda item: (int(item.get("priority", 0)), float(item.get("updated", 0.0))))


def _apply_active_locked():
    lease = _active_lease(_read_leases())
    target = str(ROUNDED_SHADER if lease is None else lease["shader"])
    hyprctl(["keyword", "decoration:screen_shader", target])
    return target


class ShaderLease:
    def __init__(self, owner, shader, priority):
        self.owner = owner
        self.shader = str(shader)
        self.priority = int(priority)
        self.path = _owner_path(owner)
        self.active = False

    def acquire(self):
        payload = {
            "owner": self.owner,
            "pid": os.getpid(),
            "shader": self.shader,
            "priority": self.priority,
            "updated": time.monotonic(),
        }
        with LOCK_FILE.open("w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self.path.write_text(json.dumps(payload, sort_keys=True))
            _apply_active_locked()
        self.active = True
        return True

    def release(self):
        if not self.active and not self.path.exists():
            return
        with LOCK_FILE.open("w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self.path.unlink(missing_ok=True)
            _apply_active_locked()
        self.active = False


def acquire(owner, shader, priority):
    lease = ShaderLease(owner, shader, priority)
    lease.acquire()
    return lease


def cleanup(owner):
    with LOCK_FILE.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        _owner_path(owner).unlink(missing_ok=True)
        _apply_active_locked()


def reconcile():
    with LOCK_FILE.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _apply_active_locked()
