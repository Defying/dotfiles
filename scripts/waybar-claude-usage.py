#!/usr/bin/env python3
"""Claude subscription usage bubble for Waybar.

This calls Anthropic's OAuth usage endpoint with the Claude Code OAuth token.
It does not use API keys and does not read Claude Code transcript logs.
"""

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

CREDS_PATH = pathlib.Path.home() / ".claude" / ".credentials.json"
CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"
OAUTH_USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
REQUEST_TIMEOUT = 10
CACHE_TTL_SECONDS = 300
NOTIFY_COOLDOWN_SECONDS = 45 * 60
CACHE_PATH = (
    pathlib.Path(
        os.environ.get("XDG_CACHE_HOME", pathlib.Path.home() / ".cache")
    )
    / "waybar"
    / "claude-usage.json"
)
STATE_DIR = CACHE_PATH.parent


def waybar(text, tooltip, css_class="subscription"):
    print(json.dumps({"text": text, "tooltip": tooltip, "class": css_class}))


ASSET_DIR = pathlib.Path.home() / "dotfiles" / "assets"


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
            if icon and pathlib.Path(icon).exists():
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


def read_cache(max_age=None):
    try:
        cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if max_age is not None and time.time() - float(cached.get("updated_at", 0)) > max_age:
        return None
    return cached


def write_cache(usage):
    try:
        CACHE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps({"updated_at": time.time(), "usage": usage}, sort_keys=True),
            encoding="utf-8",
        )
    except OSError:
        pass


def read_oauth():
    try:
        data = json.loads(CREDS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    return data.get("claudeAiOauth") or {}


def refresh_auth():
    try:
        subprocess.run(
            ["claude", "auth", "status"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=6,
        )
    except Exception:
        pass


def token_needs_refresh(oauth):
    expires_at = oauth.get("expiresAt")
    if not expires_at:
        return False
    return float(expires_at) / 1000 < time.time() + 90


def fetch_usage(token):
    request = urllib.request.Request(
        OAUTH_USAGE_ENDPOINT,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "claude-code-waybar/1",
        },
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def read_usage(oauth):
    token = oauth.get("accessToken")
    if not token:
        raise RuntimeError("Claude OAuth token not found")
    if token_needs_refresh(oauth):
        refresh_auth()
        oauth = read_oauth()
        token = oauth.get("accessToken")
        if not token:
            raise RuntimeError("Claude OAuth token not found after refresh")
    try:
        return fetch_usage(token)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            refresh_auth()
            token = read_oauth().get("accessToken")
            if token:
                return fetch_usage(token)
        raise


def fmt_reset(value):
    if not value:
        return "unknown"
    try:
        when = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return "unknown"
    now = dt.datetime.now().astimezone()
    if when.date() == now.date():
        return when.strftime("%H:%M")
    return when.strftime("%a %H:%M").lower()


def window_line(label, window):
    if not window:
        return f"{label}: unavailable"
    used = int(round(float(window.get("utilization") or 0)))
    remaining = max(0, 100 - used)
    return f"{label}: {remaining}% remaining ({used}% used), resets {fmt_reset(window.get('resets_at'))}"


def css_class(primary_remaining):
    if primary_remaining <= 10:
        return "danger"
    if primary_remaining <= 30:
        return "warn"
    return "subscription"


def limit_level(primary_remaining):
    if primary_remaining <= 10:
        return "critical"
    return ""


def main():
    oauth = read_oauth()
    subscription = oauth.get("subscriptionType") or "plan"
    if not oauth.get("accessToken"):
        waybar(
            "login",
            "\n".join(
                [
                    "Claude Code OAuth token not found.",
                    "Open quick settings, then use Claude Login.",
                ]
            ),
            "auth",
        )
        return 0

    cached = read_cache(CACHE_TTL_SECONDS)
    stale = False
    if cached:
        usage = cached["usage"]
    else:
        try:
            usage = read_usage(oauth)
            write_cache(usage)
        except Exception as exc:
            cached = read_cache()
            if cached:
                usage = cached["usage"]
                stale = True
            else:
                css = "rate" if isinstance(exc, urllib.error.HTTPError) and exc.code == 429 else "error"
                text = "rate" if css == "rate" else "error"
                waybar(
                    text,
                    "\n".join(
                        [
                            f"Could not read Claude subscription usage: {exc}",
                            "No cached usage is available yet.",
                            "",
                            CLAUDE_USAGE_URL,
                        ]
                    ),
                    css,
                )
                return 0

    if stale:
        cache_age = int((time.time() - float(cached.get("updated_at", 0))) / 60)
    else:
        cache_age = 0

    try:
        five_hour = usage.get("five_hour") or {}
        seven_day = usage.get("seven_day") or {}
    except AttributeError:
        waybar(
            "error",
            "\n".join(
                [
                    "Could not read Claude subscription usage: invalid cached response",
                    "",
                    CLAUDE_USAGE_URL,
                ]
            ),
            "error",
        )
        return 0

    primary_used = int(round(float(five_hour.get("utilization") or 0)))
    primary_remaining = max(0, 100 - primary_used)
    weekly_used = int(round(float(seven_day.get("utilization") or 0))) if seven_day else None
    weekly_remaining = max(0, 100 - weekly_used) if weekly_used is not None else None
    extra = usage.get("extra_usage") or {}

    tooltip_lines = [
        f"Claude subscription usage ({subscription})",
        window_line("5h", five_hour),
        window_line("weekly", seven_day),
    ]
    if weekly_remaining is not None:
        tooltip_lines.append(f"bar text shows remaining current window; weekly is {weekly_remaining}% remaining")
    if extra:
        enabled = "enabled" if extra.get("is_enabled") else "disabled"
        tooltip_lines.append(f"extra usage: {enabled}")
        if extra.get("utilization") is not None:
            tooltip_lines.append(f"extra usage utilization: {int(round(float(extra['utilization'])))}%")
    if stale:
        tooltip_lines.append(f"showing cached usage from {cache_age}m ago; Anthropic rate-limited the refresh")
    tooltip_lines.extend(["", CLAUDE_USAGE_URL])

    level = limit_level(primary_remaining)
    text = f"{primary_remaining}%"
    if level:
        maybe_notify("Claude", level, primary_remaining, fmt_reset(five_hour.get("resets_at")), icon=ASSET_DIR / "claude.svg")
    waybar(text, "\n".join(tooltip_lines), css_class(primary_remaining))
    return 0


if __name__ == "__main__":
    sys.exit(main())
