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
import shlex
import subprocess
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "waybar"

# Optional companion: also drop an Apple Reminder on a Mac (e.g. the mac mini)
# via osascript over SSH, so the reset shows up in Reminders / on your phone,
# not just the local mako toast. Host is an ssh alias (see ~/.ssh/config "mini");
# set AI_RESET_MINI_HOST="" to disable. Requires a one-time TCC approval on the
# Mac the first time (allow Terminal/ssh to control Reminders). Best-effort:
# fired fully detached with a short connect timeout, so it never blocks the
# waybar tick and silently does nothing when the Mac is unreachable. The local
# state file also records the Apple Reminder request separately from the Linux
# timer, so repairing a missing local timer cannot create duplicate reminders.
MINI_HOST = os.environ.get("AI_RESET_MINI_HOST", "mini")
MAC_REMINDER_LIST = os.environ.get("AI_RESET_MAC_LIST", "AI Resets")


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


def _set_mac_reminder(service: str, window_label: str, reset_epoch: int) -> None:
    """Fire-and-forget: create an Apple Reminder on MINI_HOST at the reset time.

    Detached (start_new_session) with a 2s connect timeout, so it returns
    immediately and never blocks the caller; if the Mac is off/asleep the
    background ssh just dies quietly. Uses the Mac's own clock as the base for
    the relative offset, so it's correct regardless of timezone differences.
    """
    if not MINI_HOST:
        return
    delay = reset_epoch - int(time.time())
    if delay <= 0:
        return
    title = f"{service} {window_label} limit reset"
    script = r'''
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
'''
    remote = (
        "osascript -e " + shlex.quote(script)
        + " -- " + shlex.quote(title)
        + " " + shlex.quote(str(delay))
        + " " + shlex.quote(MAC_REMINDER_LIST)
    )
    cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2",
        "-o", "StrictHostKeyChecking=accept-new", MINI_HOST, remote,
    ]
    try:
        subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def _clear(service: str) -> None:
    unit = _unit(service)
    _run(["systemctl", "--user", "stop", f"{unit}.timer", f"{unit}.service"])
    _run(["systemctl", "--user", "reset-failed", f"{unit}.timer", f"{unit}.service"])


def _same_reset(prev: dict, window_label: str, reset_epoch: int) -> bool:
    return prev.get("reset_epoch") == reset_epoch and prev.get("window") == window_label


def _mac_reminder_already_requested(prev: dict, window_label: str, reset_epoch: int) -> bool:
    if prev.get("mac_reminder_epoch") == reset_epoch and prev.get("mac_reminder_window") == window_label:
        return (
            prev.get("mac_reminder_host") == MINI_HOST
            and prev.get("mac_reminder_list") == MAC_REMINDER_LIST
        )
    # Old state files were written only after the Mac reminder path had been
    # reached. Treat an existing matching state file as already requested so
    # upgrading this helper stops floods immediately.
    return _same_reset(prev, window_label, reset_epoch) and "mac_reminder_epoch" not in prev


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
        cancel(service)
        return

    try:
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        sf = _state_file(service)
        try:
            prev = json.loads(sf.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            prev = {}
        same_reset = _same_reset(prev, window_label, reset_epoch)
        mac_reminder_requested = _mac_reminder_already_requested(prev, window_label, reset_epoch)
        if same_reset and _timer_active(service):
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
            state = {"reset_epoch": reset_epoch, "window": window_label}
            if mac_reminder_requested:
                state.update({
                    "mac_reminder_epoch": reset_epoch,
                    "mac_reminder_window": window_label,
                    "mac_reminder_host": MINI_HOST,
                    "mac_reminder_list": MAC_REMINDER_LIST,
                })
            else:
                # Companion Apple Reminder on the Mac. This is intentionally
                # deduped separately from the Linux timer because the local
                # transient systemd timer may need repair while the reset epoch
                # is unchanged.
                _set_mac_reminder(service, window_label, reset_epoch)
                state.update({
                    "mac_reminder_epoch": reset_epoch,
                    "mac_reminder_window": window_label,
                    "mac_reminder_host": MINI_HOST,
                    "mac_reminder_list": MAC_REMINDER_LIST,
                })
            sf.write_text(json.dumps(state), encoding="utf-8")
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
