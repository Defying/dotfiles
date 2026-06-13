#!/usr/bin/env python3
"""Small local account switcher for Codex CLI auth used by Waybar.

Codex itself stores one active ChatGPT login at ~/.codex/auth.json. This helper
keeps named copies under ~/.codex/accounts/ and copies the selected account back
to auth.json when switching.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CODEX_HOME = Path.home() / ".codex"
AUTH_PATH = CODEX_HOME / "auth.json"
ACCOUNTS_DIR = CODEX_HOME / "accounts"
ACTIVE_PATH = ACCOUNTS_DIR / "active"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "waybar"
CODEX_CACHE = CACHE_DIR / "codex-usage.json"
ACCOUNT_CACHE = CACHE_DIR / "codex-account.json"
BUN_CODEX = Path.home() / ".bun" / "bin" / "codex"
CODEX_BIN = str(BUN_CODEX) if BUN_CODEX.exists() else "codex"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _safe_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _write_private(path: Path, data: bytes) -> None:
    _ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        path.chmod(0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _jwt_claims(token: str | None) -> dict:
    if not token or token.count(".") < 2:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:
        return {}


def _slug(value: str | None, fallback: str = "account") -> str:
    raw = (value or fallback).strip().lower()
    raw = re.sub(r"@.*$", "", raw)
    raw = re.sub(r"[^a-z0-9_.-]+", "-", raw).strip(".-")
    return raw or fallback


def account_from_auth(path: Path = AUTH_PATH, name: str | None = None) -> dict:
    data = _safe_json(path)
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    claims = _jwt_claims(tokens.get("id_token"))
    openai = claims.get("https://api.openai.com/auth") or {}
    email = claims.get("email") or ""
    account_id = tokens.get("account_id") or openai.get("chatgpt_account_id") or ""
    plan = openai.get("chatgpt_plan_type") or ""
    label = name or email or claims.get("name") or (f"codex-{account_id[:8]}" if account_id else "Codex account")
    return {
        "label": label,
        "email": email,
        "name": claims.get("name") or "",
        "account_id": account_id,
        "plan": plan,
        "auth_mode": data.get("auth_mode") or "",
        "updated_at": _now(),
    }


def slot_dir(slot: str) -> Path:
    return ACCOUNTS_DIR / slot


def slot_auth(slot: str) -> Path:
    return slot_dir(slot) / "auth.json"


def slot_meta(slot: str) -> Path:
    return slot_dir(slot) / "meta.json"


def read_active_slot() -> str:
    try:
        return ACTIVE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_active_slot(slot: str) -> None:
    _ensure_private_dir(ACCOUNTS_DIR)
    _write_private(ACTIVE_PATH, (slot + "\n").encode("utf-8"))


def _account_slot_candidates(account_id: str) -> list[dict]:
    if not account_id:
        return []
    return [account for account in list_accounts() if account.get("account_id") == account_id]


def _preferred_slot_for_account_id(account_id: str, current_slot: str | None = None) -> str:
    candidates = _account_slot_candidates(account_id)
    if not candidates:
        return ""
    suffix = f"-{account_id[:8].lower()}"

    def rank(account: dict) -> tuple[int, int, str]:
        slot = account.get("slot") or ""
        auto_suffix = slot.lower().endswith(suffix)
        current = slot == current_slot
        # Prefer human aliases like "defying" over auto-created
        # "defying-e9273352"; otherwise keep the current slot stable.
        return (1 if auto_suffix else 0, 0 if current else 1, slot)

    return min(candidates, key=rank).get("slot") or ""


def save_current(name: str | None = None) -> dict:
    if not AUTH_PATH.exists():
        raise RuntimeError("Codex auth.json does not exist; run Codex login first")
    meta = account_from_auth(AUTH_PATH, name=name)
    account_id = meta.get("account_id") or ""
    slot = _slug(name or meta.get("email") or account_id or meta.get("label"), "codex")
    if account_id:
        slot = f"{slot}-{account_id[:8]}"
    if account_id and not name:
        existing_slot = _preferred_slot_for_account_id(account_id)
        if existing_slot:
            slot = existing_slot
            existing = _safe_json(slot_meta(slot))
            if existing.get("label"):
                meta["label"] = existing["label"]
    meta["slot"] = slot
    _write_private(slot_auth(slot), AUTH_PATH.read_bytes())
    _write_private(slot_meta(slot), json.dumps(meta, indent=2, sort_keys=True).encode("utf-8"))
    write_active_slot(slot)
    write_account_cache(meta)
    return meta


def sync_active_slot() -> dict:
    _ensure_private_dir(ACCOUNTS_DIR)
    slot = read_active_slot()
    if not slot:
        return save_current()
    if AUTH_PATH.exists():
        meta = account_from_auth(AUTH_PATH)
        existing = _safe_json(slot_meta(slot))
        current_id = meta.get("account_id")
        existing_id = existing.get("account_id")
        if current_id and existing_id and current_id != existing_id:
            return save_current()
        if current_id:
            preferred_slot = _preferred_slot_for_account_id(current_id, current_slot=slot)
            if preferred_slot and preferred_slot != slot:
                slot = preferred_slot
                existing = _safe_json(slot_meta(slot))
                write_active_slot(slot)
        _write_private(slot_auth(slot), AUTH_PATH.read_bytes())
        if existing.get("label"):
            meta["label"] = existing["label"]
        meta["slot"] = slot
        _write_private(slot_meta(slot), json.dumps(meta, indent=2, sort_keys=True).encode("utf-8"))
        write_account_cache(meta)
        return meta
    return _safe_json(slot_meta(slot))


def list_accounts() -> list[dict]:
    _ensure_private_dir(ACCOUNTS_DIR)
    accounts: list[dict] = []
    for path in sorted(ACCOUNTS_DIR.iterdir()):
        if not path.is_dir() or not (path / "auth.json").exists():
            continue
        meta = _safe_json(path / "meta.json") or account_from_auth(path / "auth.json")
        meta["slot"] = path.name
        accounts.append(meta)
    return accounts


def active_account() -> dict:
    slot = read_active_slot()
    if slot and slot_auth(slot).exists():
        meta = _safe_json(slot_meta(slot))
        if meta:
            meta["slot"] = slot
            return meta
    if AUTH_PATH.exists():
        try:
            return sync_active_slot()
        except Exception:
            return account_from_auth(AUTH_PATH)
    return {}


def write_account_cache(meta: dict) -> None:
    try:
        _write_private(ACCOUNT_CACHE, json.dumps(meta, sort_keys=True).encode("utf-8"))
    except OSError:
        pass


def clear_codex_cache() -> None:
    try:
        CODEX_CACHE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def signal_waybar() -> None:
    subprocess.run(["pkill", "-RTMIN+8", "-x", "waybar"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def activate(slot: str) -> dict:
    auth = slot_auth(slot)
    if not auth.exists():
        raise RuntimeError(f"unknown Codex account: {slot}")
    try:
        sync_active_slot()
    except Exception:
        pass
    _write_private(AUTH_PATH, auth.read_bytes())
    write_active_slot(slot)
    meta = _safe_json(slot_meta(slot)) or account_from_auth(auth)
    meta["slot"] = slot
    meta["updated_at"] = _now()
    _write_private(slot_meta(slot), json.dumps(meta, indent=2, sort_keys=True).encode("utf-8"))
    write_account_cache(meta)
    clear_codex_cache()
    signal_waybar()
    return meta


def display_label(meta: dict) -> str:
    label = meta.get("label") or meta.get("email") or meta.get("slot") or "Codex account"
    plan = meta.get("plan")
    if plan:
        return f"{label} ({plan})"
    return label


def terminal_command(script: Path, slot: str) -> str:
    quoted_script = sh_quote(str(script))
    quoted_slot = sh_quote(slot)
    return (
        f"python3 {quoted_script} codex-login {quoted_slot}; "
        "printf '\\nPress Enter to close...'; read _"
    )


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def spawn_terminal(command: str) -> bool:
    terminals = [
        ["ghostty", "-e", "bash", "-lc", command],
        ["foot", "bash", "-lc", command],
        ["kitty", "bash", "-lc", command],
        ["alacritty", "-e", "bash", "-lc", command],
    ]
    for term in terminals:
        if shutil.which(term[0]):
            subprocess.Popen(term, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
    return False


def fuzzel(items: list[str], prompt: str) -> str:
    if not shutil.which("fuzzel"):
        return ""
    try:
        proc = subprocess.run(
            ["fuzzel", "--dmenu", "--prompt", prompt, "--lines", "10", "--width", "42"],
            input="\n".join(items) + "\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        return proc.stdout.strip()
    except Exception:
        return ""


def codex_login(slot: str) -> int:
    target = slot_dir(slot)
    _ensure_private_dir(target)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(target)
    print(f"Logging into Codex account slot: {slot}")
    result = subprocess.run([CODEX_BIN, "login"], env=env)
    if result.returncode != 0:
        return result.returncode
    if not slot_auth(slot).exists():
        print("Codex login finished, but no auth.json was written.", file=sys.stderr)
        return 1
    meta = account_from_auth(slot_auth(slot), name=slot)
    meta["slot"] = slot
    _write_private(slot_meta(slot), json.dumps(meta, indent=2, sort_keys=True).encode("utf-8"))
    activate(slot)
    print(f"Activated {display_label(meta)}")
    return 0


def codex_login_new() -> int:
    base = f"codex-{dt.datetime.now().strftime('%Y%m%d-%H%M')}"
    entered = fuzzel([base], "account name  ")
    slot = _slug(entered or base, base)
    script = Path(__file__).resolve()
    command = terminal_command(script, slot)
    if spawn_terminal(command):
        return 0
    print(command)
    return 0


def codex_menu() -> int:
    try:
        current = sync_active_slot()
    except Exception:
        current = active_account()
    active_slot = current.get("slot") or read_active_slot()
    accounts = list_accounts()
    items = ["new login", "save current"]
    for account in accounts:
        prefix = "* " if account.get("slot") == active_slot else "  "
        items.append(f"{prefix}{display_label(account)} [{account.get('slot')}]")
    choice = fuzzel(items, "codex account  ")
    if not choice:
        return 0
    if choice == "new login":
        return codex_login_new()
    if choice == "save current":
        meta = save_current()
        print(display_label(meta))
        return 0
    match = re.search(r"\[([^\]]+)\]\s*$", choice)
    if match:
        meta = activate(match.group(1))
        subprocess.run(
            ["notify-send", "-a", "Codex account", "-t", "2200", "Codex account", display_label(meta)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return 0


def status_json() -> int:
    try:
        meta = sync_active_slot()
    except Exception:
        meta = active_account()
    if meta:
        write_account_cache(meta)
    print(json.dumps(meta))
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "codex-status-json"
    try:
        if command == "codex-status-json":
            return status_json()
        if command == "codex-save-current":
            meta = save_current(argv[2] if len(argv) > 2 else None)
            print(display_label(meta))
            return 0
        if command == "codex-activate":
            meta = activate(argv[2])
            print(display_label(meta))
            return 0
        if command == "codex-login":
            return codex_login(argv[2])
        if command == "codex-login-new":
            return codex_login_new()
        if command == "codex-menu":
            return codex_menu()
    except Exception as exc:
        print(f"ai_accounts: {exc}", file=sys.stderr)
        return 1
    print("usage: ai_accounts.py codex-status-json|codex-menu|codex-login-new|codex-save-current|codex-activate SLOT|codex-login SLOT", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
