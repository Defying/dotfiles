#!/usr/bin/env python3
"""Waybar CPU + memory + network bubble.

Reads /proc/stat, /proc/meminfo and the active interface's byte counters,
prints a one-line JSON payload for waybar's custom module format. CPU% and
network rates are deltas since the last invocation, persisted in a small
state file under XDG_RUNTIME_DIR — no sleep inside, no blocking waybar's
poll loop. Cost per tick is a few fopen + line parses.

Network rates are reported in bits/sec with auto Kbps/Mbps/Gbps units
(1000-based, the networking convention). GPU% is intentionally absent:
the Asahi GPU driver exposes no utilisation counter (no devfreq, no
drm-engine fdinfo, runtime_status is "unsupported"), so there is nothing
to read without fabricating a number.

Output JSON:
    {"text": "cpu 12%  mem 34%  ↓ 1.2Mbps ↑ 0.3Mbps",
     "tooltip": "...", "class": "ok|busy|hot"}
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from runtime_dirs import private_runtime_dir

STATE_FILE = private_runtime_dir("waybar-sysmon") / "waybar-sysmon.json"


def read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return {}


def write_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state))
    except OSError:
        pass


def read_cpu_totals() -> tuple[int, int]:
    """Returns (idle, total) jiffies from the aggregate 'cpu' line."""
    with open("/proc/stat") as f:
        head = f.readline()
    parts = head.split()
    # cpu user nice system idle iowait irq softirq steal guest guest_nice
    if parts[0] != "cpu":
        return 0, 0
    fields = [int(x) for x in parts[1:11]]
    idle = fields[3] + fields[4]   # idle + iowait
    total = sum(fields)
    return idle, total


def cpu_percent(prev: dict, cur_idle: int, cur_total: int) -> int:
    di = cur_idle - int(prev.get("idle", 0))
    dt = cur_total - int(prev.get("total", 0))
    if dt <= 0:
        return 0
    busy = dt - di
    return max(0, min(100, round(busy * 100 / dt)))


def mem_percent_and_human() -> tuple[int, str]:
    fields: dict[str, int] = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, _, rest = line.partition(":")
            v = rest.strip().split()
            if v:
                try:
                    fields[key] = int(v[0])  # kB
                except ValueError:
                    continue
    total = fields.get("MemTotal", 0)
    avail = fields.get("MemAvailable", fields.get("MemFree", 0))
    if total <= 0:
        return 0, "0/0 GiB"
    used = total - avail
    pct  = max(0, min(100, round(used * 100 / total)))
    used_gi  = used  / (1024 * 1024)
    total_gi = total / (1024 * 1024)
    return pct, f"{used_gi:.1f}/{total_gi:.0f} GiB"


def default_iface() -> str | None:
    """The interface carrying the default route (e.g. wld0)."""
    try:
        with open("/proc/net/route") as f:
            next(f)
            for line in f:
                p = line.split()
                if len(p) > 3 and p[1] == "00000000" and (int(p[3], 16) & 2):
                    return p[0]
    except (OSError, ValueError, StopIteration):
        pass
    return None


def net_bytes(iface: str) -> tuple[int, int]:
    base = f"/sys/class/net/{iface}/statistics"
    try:
        rx = int(Path(f"{base}/rx_bytes").read_text())
        tx = int(Path(f"{base}/tx_bytes").read_text())
        return rx, tx
    except (OSError, ValueError):
        return 0, 0


def fmt_rate(bits_per_sec: float) -> str:
    # Always one decimal, lowercase unit, floored at kbps (sub-kbps shows as
    # "0.0kbps") — no raw "bps". Longest output is "999.9kbps"/"999.9mbps" = 9
    # chars, which the :>9 fields below rely on to keep the bubble fixed-width.
    bps = max(0.0, bits_per_sec)
    if bps >= 1e9:
        return f"{bps / 1e9:.1f}gbps"
    if bps >= 1e6:
        return f"{bps / 1e6:.1f}mbps"
    return f"{bps / 1e3:.1f}kbps"


def classify(cpu: int, mem: int) -> str:
    if cpu >= 90 or mem >= 90:
        return "hot"
    if cpu >= 60 or mem >= 70:
        return "busy"
    return "ok"


def main() -> int:
    prev = read_state()
    now = time.time()
    cur: dict = {"t": now}

    cur_idle, cur_total = read_cpu_totals()
    cur["idle"], cur["total"] = cur_idle, cur_total
    cpu = cpu_percent(prev, cur_idle, cur_total)

    mem, mem_human = mem_percent_and_human()

    iface = default_iface()
    cur["iface"] = iface or ""
    down = up = 0.0
    if iface:
        rx, tx = net_bytes(iface)
        cur["rx"], cur["tx"] = rx, tx
        dt = now - float(prev.get("t", 0) or 0)
        # Only trust a delta if the same interface was sampled last tick.
        if prev.get("iface") == iface and dt > 0 and "rx" in prev:
            down = (rx - int(prev["rx"])) * 8 / dt
            up   = (tx - int(prev["tx"])) * 8 / dt

    write_state(cur)

    # Fixed-width fields so the bubble never changes size as values change
    # (variable width reflows the bar and jostles the centered group). SF Mono
    # is monospace, so constant char counts == constant pixel width.
    net_str = f"↓ {fmt_rate(down):>9} ↑ {fmt_rate(up):>9}"
    text    = f"cpu {cpu:>3}%  mem {mem:>3}%  {net_str}"
    tooltip = (f"cpu {cpu}% · mem {mem}% ({mem_human})\n"
               f"net {iface or '—'}  ↓ {fmt_rate(down)}  ↑ {fmt_rate(up)}")
    print(json.dumps({"text": text, "tooltip": tooltip, "class": classify(cpu, mem)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
