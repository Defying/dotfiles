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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_reset  # noqa: E402

CREDS_PATH = pathlib.Path.home() / ".claude" / ".credentials.json"
CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"
OAUTH_USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
REQUEST_TIMEOUT = 10
CACHE_TTL_SECONDS = 300
NOTIFY_COOLDOWN_SECONDS = 45 * 60
RESET_PAST_GRACE_SECONDS = 120
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


def signal_waybar(signal):
    if signal:
        subprocess.run(
            ["pkill", f"-RTMIN+{signal}", "waybar"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


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


def update_cache_retry(cached, exc):
    retry_after = retry_after_seconds(exc)
    retry_at = time.time() + retry_after
    payload = dict(cached)
    payload["retry_at"] = retry_at
    payload["refresh_error_text"] = describe_refresh_error(exc)
    try:
        CACHE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
    return retry_at


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


def retry_after_seconds(exc):
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        try:
            seconds = int(exc.headers.get("Retry-After", "300"))
        except (TypeError, ValueError):
            seconds = 300
        return min(max(seconds, 60), 3600)
    return 60


def describe_refresh_error(exc):
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return "Anthropic rate-limited the refresh"
    return f"refresh failed: {exc}"


def compact_duration(seconds):
    seconds = max(0, int(seconds))
    minutes = (seconds + 59) // 60
    hours, mins = divmod(minutes, 60)
    if hours and mins == 0:
        return f"{hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def iso_to_epoch(value):
    if not value:
        return None
    try:
        return int(
            dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        )
    except ValueError:
        return None


def compact_countdown(epoch):
    if not epoch:
        return ""
    seconds = max(0, int(epoch - time.time()))
    if seconds <= 0:
        return ""
    minutes = (seconds + 59) // 60
    days, rem_minutes = divmod(minutes, 1440)
    hours, mins = divmod(rem_minutes, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


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


def reset_has_passed(epoch):
    if not epoch:
        return False
    return int(epoch) <= int(time.time()) - RESET_PAST_GRACE_SECONDS


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


def main(force_refresh=False, force_network=False):
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

    cached = None if force_refresh else read_cache(CACHE_TTL_SECONDS)
    refresh_error_text = None
    retry_at = None
    stale = False
    if cached:
        usage = cached["usage"]
    else:
        cached = read_cache()
        if not force_network and cached and float(cached.get("retry_at") or 0) > time.time():
            usage = cached["usage"]
            stale = True
            refresh_error_text = cached.get("refresh_error_text") or "waiting before retry"
            retry_at = float(cached.get("retry_at") or 0)
        else:
            try:
                usage = read_usage(oauth)
                write_cache(usage)
            except Exception as exc:
                if cached:
                    retry_at = update_cache_retry(cached, exc)
                    refresh_error_text = describe_refresh_error(exc)
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

    five_hour_reset_epoch = iso_to_epoch(five_hour.get("resets_at"))
    seven_day_reset_epoch = iso_to_epoch(seven_day.get("resets_at"))
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
        tooltip_lines.append(f"weekly window: {weekly_remaining}% remaining")
    if extra:
        enabled = "enabled" if extra.get("is_enabled") else "disabled"
        tooltip_lines.append(f"extra usage: {enabled}")
        if extra.get("utilization") is not None:
            tooltip_lines.append(f"extra usage utilization: {int(round(float(extra['utilization'])))}%")
    if stale:
        reason = refresh_error_text or "refresh failed"
        if retry_at and retry_at > time.time():
            reason = f"{reason}; retry in {compact_duration(retry_at - time.time())}"
        tooltip_lines.append(f"showing cached usage from {cache_age}m ago; {reason}")

    display_remaining = 0 if weekly_remaining == 0 else primary_remaining
    level = limit_level(display_remaining)
    text = f"{primary_remaining}%"
    text_window = "5h window remaining %"
    stale_expired_reset = False
    retry_limited = bool(retry_at and retry_at > time.time())
    if weekly_remaining == 0:
        countdown = compact_countdown(seven_day_reset_epoch)
        if countdown:
            text = countdown
            text_window = "weekly reset countdown"
        elif stale and reset_has_passed(seven_day_reset_epoch):
            # Weekly reset passed but unconfirmed; no more-meaningful number to
            # show, so a neutral dash beats a cryptic word.
            text = "–"
            text_window = "stale weekly reset time"
            stale_expired_reset = True
    elif primary_remaining == 0:
        countdown = compact_countdown(five_hour_reset_epoch)
        if countdown:
            text = countdown
            text_window = "5h reset countdown"
        elif stale and reset_has_passed(five_hour_reset_epoch):
            # The cached 5h reset has passed but we can't confirm the fresh
            # value (refresh failed / rate-limited). The 5h window has almost
            # certainly reset — so rather than a cryptic stuck word, show the
            # still-meaningful weekly remaining if we have it.
            if weekly_remaining is not None:
                text = f"{weekly_remaining}%"
                text_window = "weekly remaining (5h reset, awaiting refresh)"
            else:
                text = "–"
                text_window = "stale 5h reset time"
            stale_expired_reset = True
    tooltip_lines.append(f"bar text shows {text_window}")
    if stale_expired_reset:
        tooltip_lines.append("cached reset time has passed; waiting for a fresh Claude usage response")
    tooltip_lines.extend(["", CLAUDE_USAGE_URL])
    if stale_expired_reset:
        level = ""
    if level:
        reset_source = seven_day if weekly_remaining == 0 else five_hour
        maybe_notify("Claude", level, display_remaining, fmt_reset(reset_source.get("resets_at")), icon=ASSET_DIR / "claude.svg")

    # At 0% (a window is exhausted), arm a dormant timer to ping when it resets;
    # prefer the weekly window when it's the one exhausted. Otherwise cancel.
    if stale_expired_reset:
        ai_reset.cancel("Claude")
    elif weekly_remaining == 0:
        ai_reset.schedule("Claude", "weekly", seven_day_reset_epoch, icon=ASSET_DIR / "claude.png")
    elif primary_remaining == 0:
        ai_reset.schedule("Claude", "5h", five_hour_reset_epoch, icon=ASSET_DIR / "claude.png")
    else:
        ai_reset.cancel("Claude")

    stale_class = "rate" if retry_limited else "warn"
    waybar(text, "\n".join(tooltip_lines), stale_class if stale_expired_reset else css_class(display_remaining))
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    signal = None
    if "--signal" in argv:
        try:
            signal = int(argv[argv.index("--signal") + 1])
        except (ValueError, IndexError):
            signal = None
    rc = main(force_refresh="--refresh" in argv, force_network="--force-network" in argv)
    signal_waybar(signal)
    sys.exit(rc)
