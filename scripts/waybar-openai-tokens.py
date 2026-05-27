#!/usr/bin/env python3
"""Codex subscription usage bubble for Waybar.

This asks the installed Codex CLI for the same ChatGPT subscription rate-limit
snapshot exposed to clients via `account/rateLimits/read`. It does not use
OpenAI API keys and does not read Codex logs.
"""

import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time

CODEX_USAGE_URL = "https://chatgpt.com/codex/settings/usage"
REQUEST_TIMEOUT = 10
NOTIFY_COOLDOWN_SECONDS = 45 * 60
STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "waybar"


def waybar(text, tooltip, css_class="subscription"):
    payload = {"text": text, "tooltip": tooltip, "class": css_class}
    print(json.dumps(payload))


ASSET_DIR = Path.home() / "dotfiles" / "assets"


def maybe_notify(service, level, remaining, reset_label, icon=None):
    """Fire only on transitions into a worse level. Suppress while staying at level."""
    try:
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        state_file = STATE_DIR / f"{service.lower()}.notify"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            state = {}
        last_level = state.get("level", "")
        if level == last_level:
            return
        if level:
            args = [
                "notify-send",
                "-a", "AI usage",
                "-u", "normal",
                "-t", "10000",
                "-h", "string:x-canonical-private-synchronous:ai-usage",
            ]
            if icon and Path(icon).exists():
                args.extend(["-i", str(icon)])
            args.extend([
                f"{service} usage low",
                f"{remaining}% remaining · resets {reset_label}",
            ])
            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        state_file.write_text(json.dumps({"level": level}), encoding="utf-8")
    except Exception:
        pass


def codex_login_status():
    try:
        return subprocess.check_output(
            ["codex", "login", "status"],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=4,
        ).strip()
    except Exception:
        return ""


def read_rate_limits():
    initialize = {
        "id": 1,
        "method": "initialize",
        "params": {
            "clientInfo": {"name": "waybar", "title": "Waybar", "version": "1"},
            "capabilities": {
                "experimentalApi": True,
                "requestAttestation": False,
                "optOutNotificationMethods": [],
            },
        },
    }
    initialized = {"method": "initialized"}
    request = {"id": 2, "method": "account/rateLimits/read"}

    proc = subprocess.Popen(
        ["codex", "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None

    for message in (initialize, initialized, request):
        proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    proc.stdin.flush()

    deadline = time.monotonic() + REQUEST_TIMEOUT
    try:
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == 2:
                if "error" in message:
                    raise RuntimeError(message["error"].get("message", "rate limit request failed"))
                return message.get("result") or {}
        raise TimeoutError("Codex rate-limit request timed out")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            proc.kill()


def pick_codex_limits(data):
    by_id = data.get("rateLimitsByLimitId") or {}
    return by_id.get("codex") or data.get("rateLimits") or {}


def fmt_reset(epoch):
    if not epoch:
        return "unknown"
    when = dt.datetime.fromtimestamp(int(epoch))
    now = dt.datetime.now()
    if when.date() == now.date():
        return when.strftime("%-I:%M %p").lower()
    return when.strftime("%a %-I:%M %p").lower()


def window_line(name, window):
    if not window:
        return f"{name}: unavailable"
    used = int(round(float(window.get("usedPercent") or 0)))
    remaining = max(0, 100 - used)
    duration = window.get("windowDurationMins")
    if duration:
        hours = int(duration) / 60
        label = f"{hours:g}h"
        if int(duration) >= 10080:
            label = "weekly"
    else:
        label = name
    return f"{label}: {remaining}% remaining ({used}% used), resets {fmt_reset(window.get('resetsAt'))}"


def css_class(primary_remaining, reached):
    if reached:
        return "danger"
    if primary_remaining <= 10:
        return "danger"
    if primary_remaining <= 30:
        return "warn"
    return "subscription"


def limit_level(primary_remaining, reached):
    if reached or primary_remaining <= 10:
        return "critical"
    return ""


def main():
    status = codex_login_status()
    if "Logged in" not in status:
        waybar(
            "login",
            "\n".join(
                [
                    status or "Codex login status unavailable",
                    "",
                    "Open quick settings, then use Login.",
                ]
            ),
            "auth",
        )
        return 0

    try:
        data = read_rate_limits()
    except Exception as exc:
        waybar(
            "?",
            "\n".join(
                [
                    status,
                    "",
                    f"Could not read Codex subscription usage: {exc}",
                    CODEX_USAGE_URL,
                ]
            ),
            "error",
        )
        return 0

    limits = pick_codex_limits(data)
    primary = limits.get("primary") or {}
    secondary = limits.get("secondary") or {}
    credits = limits.get("credits") or {}
    reached = limits.get("rateLimitReachedType")
    primary_used = int(round(float(primary.get("usedPercent") or 0)))
    primary_remaining = max(0, 100 - primary_used)
    secondary_used = int(round(float(secondary.get("usedPercent") or 0))) if secondary else None
    secondary_remaining = max(0, 100 - secondary_used) if secondary_used is not None else None

    text = f"{primary_remaining}%"
    if reached:
        text = "0%"
    level = limit_level(primary_remaining, reached)

    credit_line = "credits: unlimited" if credits.get("unlimited") else f"credits: {credits.get('balance', '0')}"
    reset_label = fmt_reset(primary.get("resetsAt"))
    tooltip_lines = [
        f"Codex subscription usage ({limits.get('planType') or 'plan'})",
        window_line("primary", primary),
        window_line("secondary", secondary),
        credit_line,
    ]
    if secondary_remaining is not None:
        tooltip_lines.append(f"bar text shows remaining current window; weekly is {secondary_remaining}% remaining")
    if reached:
        tooltip_lines.append(f"limit state: {reached}")
    tooltip_lines.extend(["", CODEX_USAGE_URL])

    maybe_notify("Codex", level, primary_remaining, reset_label, icon=ASSET_DIR / "openai.svg")
    waybar(text, "\n".join(tooltip_lines), css_class(primary_remaining, reached))
    return 0


if __name__ == "__main__":
    sys.exit(main())
