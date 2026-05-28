#!/usr/bin/env python3
"""Waybar CPU + memory bubble.

Reads /proc/stat and /proc/meminfo, prints a one-line JSON payload for
waybar's custom module format. CPU% is the delta since the last invocation,
persisted in a small state file under XDG_RUNTIME_DIR — no sleep inside,
no blocking waybar's poll loop. Cost per tick is one fopen + a few lines
of parsing.

Output JSON:
    {"text": "12 · 34", "tooltip": "cpu 12%% · mem 34%% (5.4/16 GiB)",
     "class": "ok|busy|hot"}
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

STATE_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "waybar-sysmon.json"


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


def cpu_percent() -> int:
    cur_idle, cur_total = read_cpu_totals()
    prev_idle, prev_total = 0, 0
    try:
        prev = json.loads(STATE_FILE.read_text())
        prev_idle  = int(prev.get("idle", 0))
        prev_total = int(prev.get("total", 0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        pass
    STATE_FILE.write_text(json.dumps({"idle": cur_idle, "total": cur_total}))

    di = cur_idle  - prev_idle
    dt = cur_total - prev_total
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


def classify(cpu: int, mem: int) -> str:
    if cpu >= 90 or mem >= 90:
        return "hot"
    if cpu >= 60 or mem >= 70:
        return "busy"
    return "ok"


def main() -> int:
    cpu = cpu_percent()
    mem, mem_human = mem_percent_and_human()
    text    = f"{cpu} · {mem}"
    tooltip = f"cpu {cpu}% · mem {mem}% ({mem_human})"
    print(json.dumps({"text": text, "tooltip": tooltip, "class": classify(cpu, mem)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
