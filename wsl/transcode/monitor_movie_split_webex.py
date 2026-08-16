#!/usr/bin/env python3
import subprocess
import time
from pathlib import Path


LOG = Path("/home/jnicolas/rsync_movies_split_from_data9.log")
NOTIFIER = Path("/home/jnicolas/send_webex_notification.py")
INTERVAL = 15 * 60


def notify(message):
    subprocess.run(
        [str(NOTIFIER), message],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def process_lines():
    result = subprocess.run(
        ["pgrep", "-af", "rsync .*Data9/Movies"],
        check=False,
        text=True,
        capture_output=True,
    )
    return result.stdout.splitlines()


def phase(lines):
    combined = "\n".join(lines)
    if "Data3/Movies-A-L" in combined:
        return "A-L: Data9 to Data3"
    if "Data7/Movies-M-Z" in combined:
        return "M-Z: Data9 to Data7"
    return "between phases"


def io_totals(lines):
    written = 0
    for line in lines:
        value = line.split(maxsplit=1)
        if not value or not value[0].isdigit():
            continue
        path = Path("/proc") / value[0] / "io"
        try:
            for item in path.read_text().splitlines():
                if item.startswith("write_bytes:"):
                    written += int(item.split()[1])
        except (FileNotFoundError, PermissionError, ValueError):
            continue
    return written


def log_counts():
    transferred = created = 0
    current_item = "scanning movie folders"
    try:
        with LOG.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith(">f"):
                    transferred += 1
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:
                        current_item = parts[1]
                    if line.startswith(">f+++++++++"):
                        created += 1
    except FileNotFoundError:
        pass
    return transferred, created, current_item


def format_bytes(value):
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024


def main():
    previous_time = time.monotonic()
    previous_bytes = 0
    first = True
    while True:
        lines = process_lines()
        if not lines:
            if not first:
                transferred, created, current_item = log_counts()
                notify(
                    f"**Movie split sync completed:** {transferred:,} files "
                    f"transferred; {created:,} newly created."
                )
            return

        now = time.monotonic()
        written = io_totals(lines)
        transferred, created, current_item = log_counts()
        elapsed = now - previous_time
        rate = max(0, written - previous_bytes) / elapsed if previous_bytes else 0
        rate_text = (
            f"{format_bytes(rate)}/s recent rate"
            if rate else "collecting rate sample"
        )
        notify(
            f"**Movie split sync status:** {phase(lines)}\n\n"
            f"Current movie: `{current_item}`\n\n"
            f"{transferred:,} files transferred; {created:,} new files. "
            f"{format_bytes(written)} written by active rsync processes; "
            f"{rate_text}."
        )
        first = False
        previous_time = now
        previous_bytes = written
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
