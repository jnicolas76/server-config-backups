#!/usr/bin/env python3
import fcntl
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path("/mnt/c/DATA/BRIDGETTE-H265")
QUEUE = ROOT / "queue.json"
LOG = ROOT / "watchdog.log"
LOCK = ROOT / "watchdog.lock"
PAUSE = ROOT / "watchdog.pause"
WORKER = str(ROOT / "bridgette_h265_worker.py")
START = str(ROOT / "start_bridgette_h265.sh")
CHECK_SECONDS = 60
RESTART_COOLDOWN_SECONDS = 300


def log(message):
    ROOT.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}\n"
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line)


def worker_running():
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if WORKER in cmdline:
            return True
    return False


def load_queue():
    if not QUEUE.exists():
        return None
    try:
        return json.loads(QUEUE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"Queue read failed: {exc}")
        return None


def save_queue(data):
    temporary = QUEUE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, QUEUE)


def queue_items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "queue", "files"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def recover_queue(data):
    changed = False
    recovered = 0
    for item in queue_items(data):
        status = item.get("status", "pending")
        if status in ("processing", "failed"):
            item["status"] = "pending"
            item["retries"] = int(item.get("retries", 0)) + 1
            item["watchdog_recovered_at"] = datetime.now().isoformat(timespec="seconds")
            recovered += 1
            changed = True
    if changed:
        save_queue(data)
    return recovered


def is_complete(data):
    items = queue_items(data)
    return bool(items) and all(item.get("status") in ("completed", "already_h265") for item in items)


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return

        log("Watchdog started")
        last_restart = 0.0
        while True:
            if PAUSE.exists():
                time.sleep(CHECK_SECONDS)
                continue

            data = load_queue()
            if data is not None and is_complete(data):
                log("All Bridgette H265 jobs completed; watchdog exiting")
                return

            if not worker_running() and time.time() - last_restart >= RESTART_COOLDOWN_SECONDS:
                recovered = recover_queue(data) if data is not None else 0
                log(f"Worker missing; recovered {recovered} item(s) and restarting")
                result = subprocess.run([START], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=False)
                log(f"Worker start command exited with status {result.returncode}")
                last_restart = time.time()

            time.sleep(CHECK_SECONDS)


if __name__ == "__main__":
    main()
