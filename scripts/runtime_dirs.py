#!/usr/bin/env python3
"""Private runtime directory helper for user-session scripts."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def private_runtime_dir(name: str) -> Path:
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        path = Path(xdg)
        if path.is_dir():
            return path

    uid = os.getuid()
    candidates = [
        Path(os.environ.get("TMPDIR", "/tmp")) / f"{name}-{uid}",
        Path.home() / ".cache" / name,
    ]
    for path in candidates:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            st = path.stat()
            mode = stat.S_IMODE(st.st_mode)
            if st.st_uid == uid and mode & 0o077 == 0:
                return path
            path.chmod(0o700)
            st = path.stat()
            if st.st_uid == uid and stat.S_IMODE(st.st_mode) & 0o077 == 0:
                return path
        except OSError:
            continue

    return Path.home() / ".cache"
