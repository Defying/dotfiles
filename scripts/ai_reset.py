#!/usr/bin/env python3
"""Schedule a one-shot "limit reset" desktop notification for an AI usage window.

Backed by a transient ``systemd --user`` timer, so nothing runs or polls until
the reset moment arrives — the timer is dormant (zero cost) in between. Arming
is idempotent: the timer is only (re)created when the target reset epoch
changes, so the every-5-minute waybar refresh doesn't churn it.

Imported by waybar-openai-tokens.py and waybar-claude-usage.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "waybar"


def _unit(service: str) -> str:
    return f"ai-reset-{service.lower()}"


def _state_file(service: str) -> Path:
    return STATE_DIR / f"{service.lower()}.reset-timer"


def _run(args: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
        )
    except Exception:
        return None


def _timer_active(service: str) -> bool:
    try:
        out = subprocess.run(
            ["systemctl", "--user", "is-active", f"{_unit(service)}.timer"],
            capture_output=True, text=True, timeout=4,
        ).stdout.strip()
        return out == "active"
    except Exception:
        return False


def _clear(service: str) -> None:
    unit = _unit(service)
    _run(["systemctl", "--user", "stop", f"{unit}.timer", f"{unit}.service"])
    _run(["systemctl", "--user", "reset-failed", f"{unit}.timer", f"{unit}.service"])


def schedule(service: str, window_label: str, reset_epoch, icon=None) -> None:
    """(Re)arm a one-shot notification at ``reset_epoch`` for ``service``.

    ``window_label`` (e.g. "weekly" / "5h") is shown in the message. No-op if
    the reset is in the past or a matching timer is already armed.
    """
    try:
        reset_epoch = int(reset_epoch)
    except (TypeError, ValueError):
        return
    delay = reset_epoch - int(time.time())
    if delay <= 0:
        return

    try:
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        sf = _state_file(service)
        try:
            prev = json.loads(sf.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            prev = {}
        if prev.get("reset_epoch") == reset_epoch and _timer_active(service):
            return  # already armed for this exact reset

        _clear(service)

        args = [
            "systemd-run", "--user", "--quiet",
            f"--unit={_unit(service)}",
            f"--on-active={delay}s",
            "--timer-property=AccuracySec=30s",
            "--",
            "notify-send", "-a", "AI usage", "-u", "normal", "-t", "0",
            "-h", "string:x-canonical-private-synchronous:ai-reset",
        ]
        if icon and Path(str(icon)).exists():
            args.extend(["-i", str(icon)])
        args.extend([
            f"{service} limit reset",
            f"{window_label} window reset — usage is back to 100%",
        ])
        if _run(args) is not None:
            sf.write_text(
                json.dumps({"reset_epoch": reset_epoch, "window": window_label}),
                encoding="utf-8",
            )
    except Exception:
        pass


def cancel(service: str) -> None:
    """Drop a pending reset timer (call when the service is no longer at 0%)."""
    try:
        sf = _state_file(service)
        if not sf.exists():
            return
        _clear(service)
        sf.unlink(missing_ok=True)
    except Exception:
        pass
