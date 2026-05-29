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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_reset  # noqa: E402
import ai_accounts  # noqa: E402

CODEX_USAGE_URL = "https://chatgpt.com/codex/settings/usage"
REQUEST_TIMEOUT = 10
NOTIFY_COOLDOWN_SECONDS = 45 * 60
STATE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "waybar"
USAGE_CACHE = STATE_DIR / "codex-usage.json"
REFRESH_LOCK = STATE_DIR / "codex-usage.refresh.lock"
CACHE_MAX_AGE_SECONDS = 30
REFRESH_LOCK_MAX_AGE_SECONDS = 120
BUN_CODEX = Path.home() / ".bun" / "bin" / "codex"
CODEX_BIN = str(BUN_CODEX) if BUN_CODEX.exists() else "codex"


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
            [CODEX_BIN, "login", "status"],
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
        [CODEX_BIN, "app-server", "--listen", "stdio://"],
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


def write_usage_cache(payload):
    """Persist usage so Waybar can render instantly and refresh out-of-band."""
    try:
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        USAGE_CACHE.write_text(json.dumps({"updated_at": time.time(), **payload}), encoding="utf-8")
    except Exception:
        pass


def write_cache_raw(payload):
    try:
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        USAGE_CACHE.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def read_usage_cache():
    try:
        return json.loads(USAGE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def cache_age_seconds(cache):
    try:
        return max(0, int(time.time() - float(cache.get("updated_at") or 0)))
    except Exception:
        return 999999


def signal_waybar(signal):
    if signal:
        subprocess.run(["pkill", f"-RTMIN+{signal}", "waybar"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def acquire_refresh_lock():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        age = time.time() - REFRESH_LOCK.stat().st_mtime
        if age > REFRESH_LOCK_MAX_AGE_SECONDS:
            REFRESH_LOCK.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return None

    try:
        fd = os.open(str(REFRESH_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    except OSError:
        return None
    os.write(fd, str(os.getpid()).encode("ascii"))
    return fd


def release_refresh_lock(fd):
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        REFRESH_LOCK.unlink()
    except OSError:
        pass


def refresh_in_progress():
    try:
        return time.time() - REFRESH_LOCK.stat().st_mtime <= REFRESH_LOCK_MAX_AGE_SECONDS
    except OSError:
        return False


def spawn_background_refresh():
    if refresh_in_progress():
        return
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--refresh", "--signal", "8"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def fmt_reset(epoch):
    if not epoch:
        return "unknown"
    when = dt.datetime.fromtimestamp(int(epoch))
    now = dt.datetime.now()
    if when.date() == now.date():
        return when.strftime("%H:%M")
    return when.strftime("%a %H:%M").lower()


def compact_countdown(epoch):
    if not epoch:
        return ""
    seconds = max(0, int(int(epoch) - time.time()))
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


def css_class(remaining, reached):
    if reached or remaining <= 10:
        return "danger"
    if remaining <= 30:
        return "warn"
    return "subscription"


def limit_level(remaining, reached):
    if reached or remaining <= 10:
        return "critical"
    return ""


def emit_usage(limits, account, *, stale_age=None, refresh_error=None, notify=False):
    account = account or {}
    account_label = ai_accounts.display_label(account) if account else "active account"
    limits = limits or {}
    primary = limits.get("primary") or {}
    secondary = limits.get("secondary") or {}
    credits = limits.get("credits") or {}
    reached = limits.get("rateLimitReachedType")
    primary_used = int(round(float(primary.get("usedPercent") or 0)))
    primary_remaining = max(0, 100 - primary_used)
    secondary_used = int(round(float(secondary.get("usedPercent") or 0))) if secondary else None
    weekly_remaining = max(0, 100 - secondary_used) if secondary_used is not None else None

    # Show the 5h (primary) window's remaining % while the weekly window still
    # has budget. Once weekly is exhausted (0% left) or a limit is hit, show 0%
    # so a healthy-looking 5h figure can't mask a weekly block.
    weekly_blocked = bool(reached) or (weekly_remaining is not None and weekly_remaining <= 0)
    display = 0 if weekly_blocked else primary_remaining
    text = f"{display}%"
    level = limit_level(display, reached)
    block_window = secondary if weekly_blocked else primary
    if display == 0:
        countdown = compact_countdown(block_window.get("resetsAt"))
        if countdown:
            text = countdown

    credit_line = "credits: unlimited" if credits.get("unlimited") else f"credits: {credits.get('balance', '0')}"
    reset_label = fmt_reset(block_window.get("resetsAt"))
    tooltip_lines = [
        f"Codex subscription usage ({limits.get('planType') or account.get('plan') or 'plan'})",
        f"account: {account_label}",
        window_line("primary", primary),
        window_line("secondary", secondary),
        credit_line,
    ]
    tooltip_lines.append(
        "bar shows weekly reset countdown" if weekly_blocked and display == 0
        else "bar shows 5h reset countdown" if display == 0
        else "bar shows 5h window remaining %"
    )
    if stale_age is not None:
        tooltip_lines.append(f"cached {stale_age}s ago; refreshing in background")
    if refresh_error:
        tooltip_lines.append(f"last refresh failed: {refresh_error}")
    if reached:
        tooltip_lines.append(f"limit state: {reached}")
    tooltip_lines.extend(["", CODEX_USAGE_URL])

    if notify:
        maybe_notify("Codex", level, display, reset_label, icon=ASSET_DIR / "openai.svg")

        # At 0% (a window is exhausted), arm a dormant timer that pings when that
        # window resets; otherwise drop any pending timer.
        if display == 0:
            ai_reset.schedule(
                "Codex",
                "weekly" if weekly_blocked else "5h",
                block_window.get("resetsAt"),
                icon=ASSET_DIR / "openai.png",
            )
        else:
            ai_reset.cancel("Codex")

    waybar(text, "\n".join(tooltip_lines), css_class(display, reached))


def emit_cached_or_placeholder():
    cache = read_usage_cache()
    if not cache:
        spawn_background_refresh()
        waybar("...", "Codex usage refresh started", "refreshing")
        return 0

    age = cache_age_seconds(cache)
    if age >= CACHE_MAX_AGE_SECONDS:
        spawn_background_refresh()

    error = cache.get("error")
    if error == "auth":
        account = cache.get("account") or ai_accounts.active_account()
        account_label = ai_accounts.display_label(account) if account else "active account"
        waybar(
            "login",
            "\n".join([
                f"Codex account: {account_label}",
                cache.get("status") or "Codex login status unavailable",
                f"cached {age}s ago; refreshing in background" if age >= CACHE_MAX_AGE_SECONDS else "",
                "",
                "Open quick settings, then use Account or Login.",
            ]).strip(),
            "auth",
        )
        return 0
    if error and cache.get("limits"):
        emit_usage(
            cache.get("limits") or {},
            cache.get("account") or {},
            stale_age=age,
            refresh_error=error,
        )
        return 0
    if error:
        waybar(
            "err",
            "\n".join([
                cache.get("status") or "Codex status unavailable",
                "",
                f"Could not read Codex subscription usage: {error}",
                f"cached {age}s ago; refreshing in background" if age >= CACHE_MAX_AGE_SECONDS else "",
                CODEX_USAGE_URL,
            ]).strip(),
            "error",
        )
        return 0

    emit_usage(
        cache.get("limits") or {},
        cache.get("account") or {},
        stale_age=age if age >= CACHE_MAX_AGE_SECONDS else None,
        refresh_error=cache.get("refresh_error"),
    )
    return 0


def refresh_usage():
    try:
        account = ai_accounts.sync_active_slot()
    except Exception:
        account = ai_accounts.active_account()
    account_label = ai_accounts.display_label(account) if account else "active account"

    status = codex_login_status()
    if "Logged in" not in status:
        waybar(
            "login",
            "\n".join(
                [
                    f"Codex account: {account_label}",
                    status or "Codex login status unavailable",
                    "",
                    "Open quick settings, then use Account or Login.",
                ]
            ),
            "auth",
        )
        write_usage_cache({"error": "auth", "status": status, "account": account})
        return 0

    try:
        data = read_rate_limits()
    except Exception as exc:
        previous = read_usage_cache()
        if previous.get("limits"):
            previous.pop("error", None)
            previous["refresh_error"] = str(exc)
            previous["last_refresh_attempt_at"] = time.time()
            previous["status"] = status
            if account:
                previous["account"] = account
            write_cache_raw(previous)
            emit_usage(
                previous.get("limits") or {},
                previous.get("account") or {},
                stale_age=cache_age_seconds(previous),
                refresh_error=str(exc),
            )
            return 0
        waybar(
            "err",
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
        write_usage_cache({"error": str(exc), "status": status, "account": account})
        return 0

    limits = pick_codex_limits(data)
    write_usage_cache({"limits": limits, "account": account, "status": status})
    emit_usage(limits, account, notify=True)
    return 0


def refresh_with_lock(signal=None):
    fd = acquire_refresh_lock()
    if fd is None:
        return 0
    try:
        rc = refresh_usage()
        signal_waybar(signal)
        return rc
    finally:
        release_refresh_lock(fd)


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--refresh"]:
        signal = None
        if "--signal" in argv:
            try:
                signal = int(argv[argv.index("--signal") + 1])
            except (ValueError, IndexError):
                signal = None
        return refresh_with_lock(signal=signal)
    return emit_cached_or_placeholder()


if __name__ == "__main__":
    sys.exit(main())
