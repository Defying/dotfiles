#!/usr/bin/env python3
import argparse
import gzip
import os
import select
import shutil
import signal
import sys
import termios
import time
import tty
from pathlib import Path

COLOR = sys.stderr.isatty() and os.environ.get("TERM", "") != "dumb"
RESET = "\033[0m" if COLOR else ""
BOLD = "\033[1m" if COLOR else ""
DIM = "\033[2m" if COLOR else ""
RED = "\033[31m" if COLOR else ""
GREEN = "\033[32m" if COLOR else ""
CYAN = "\033[36m" if COLOR else ""
YELLOW = "\033[33m" if COLOR else ""


def human_bytes(value: float) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            return f"{size:4.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PiB"


def render_bar(processed: int, total: int, width: int = 30) -> str:
    if total <= 0:
        return "-" * width
    frac = max(0.0, min(1.0, processed / total))
    filled = int(width * frac)
    return "█" * filled + "░" * (width - filled)


def progress_line(label: str, processed: int, total: int, output_size: int, start: float) -> str:
    elapsed = max(time.time() - start, 0.001)
    rate = processed / elapsed
    frac = (processed / total) if total else 0.0
    eta = int((total - processed) / rate) if total and rate > 0 else 0
    ratio = (output_size / processed * 100.0) if processed > 0 else 0.0
    bar = render_bar(processed, total)
    pct = frac * 100.0
    return (
        f"\r{BOLD}{CYAN}{label}{RESET} "
        f"{CYAN}[{bar}]{RESET} "
        f"{pct:5.1f}%  "
        f"in {human_bytes(processed)}/{human_bytes(total)}  "
        f"out {human_bytes(output_size)}  "
        f"ratio {ratio:5.1f}%  "
        f"speed {human_bytes(rate)}/s  "
        f"eta {eta:5d}s  "
        f"{DIM}x=cancel{RESET}"
    )


def setup_tty_reader():
    try:
        tty_file = open("/dev/tty", "rb", buffering=0)
    except OSError:
        return None, None
    fd = tty_file.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    return tty_file, old


def restore_tty(tty_file, old):
    if tty_file and old is not None:
        termios.tcsetattr(tty_file.fileno(), termios.TCSADRAIN, old)
        tty_file.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--total-bytes", required=True, type=int)
    parser.add_argument("--label", default="image")
    parser.add_argument("--compress-level", type=int, default=1)
    args = parser.parse_args()

    src = args.input
    dst = Path(args.output)
    total = max(args.total_bytes, 0)
    chunk_size = 4 * 1024 * 1024
    processed = 0
    cancelled = False
    start = time.time()
    last_render = 0.0

    tty_file, tty_state = setup_tty_reader()

    def handle_signal(signum, _frame):
        raise KeyboardInterrupt(signum)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        with open(src, "rb", buffering=0) as infile, open(dst, "wb") as raw_out:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_out, compresslevel=args.compress_level, mtime=0) as gz:
                while True:
                    if tty_file:
                        ready, _, _ = select.select([tty_file], [], [], 0)
                        if ready:
                            key = tty_file.read(1)
                            if key in (b"x", b"X"):
                                cancelled = True
                                raise KeyboardInterrupt("cancelled")
                    chunk = infile.read(chunk_size)
                    if not chunk:
                        break
                    gz.write(chunk)
                    processed += len(chunk)
                    now = time.time()
                    if now - last_render >= 0.2:
                        gz.flush()
                        raw_out.flush()
                        out_size = raw_out.tell()
                        sys.stderr.write(progress_line(args.label, processed, total, out_size, start))
                        sys.stderr.flush()
                        last_render = now
                gz.flush()
                raw_out.flush()
                out_size = raw_out.tell()
        sys.stderr.write(progress_line(args.label, processed, total, out_size, start))
        sys.stderr.write(f"\n{GREEN}[ok]{RESET} {BOLD}compression finished{RESET}\n")
        sys.stderr.flush()
        return 0
    except KeyboardInterrupt:
        cancelled = True
        raise
    except Exception:
        raise
    finally:
        restore_tty(tty_file, tty_state)
        if cancelled:
            try:
                if dst.exists():
                    dst.unlink()
            except OSError:
                pass
            sys.stderr.write(f"\n{YELLOW}[info]{RESET} image backup cancelled, partial file removed\n")
            sys.stderr.flush()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
