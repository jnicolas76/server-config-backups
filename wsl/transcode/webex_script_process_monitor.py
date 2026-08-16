#!/usr/bin/env python3
import os
import subprocess
import time
from pathlib import Path


WATCH_ROOTS = ("/mnt/c/DATA/", "/home/jnicolas/bin/")
EXCLUDED_NAMES = {
    # These are intentionally persistent services. Their own health/status
    # monitors report useful state; uptime alone is not an actionable alert.
    "combined_transcode_orchestrator.py",
    "handbrake_transcode_worker.py",
    "send_webex_notification.py",
    "send_webex_notification",
    "transcode_queue_watchdog.py",
    "webex_bot_listener.py",
    "webex_script_process_monitor.py",
    "monitor_music_import_webex.py",
    "run-copa-then-movies.sh",
}
NOTIFIER = "/home/jnicolas/bin/send_webex_notification"
POLL_SECONDS = 5
STATUS_SECONDS = 30 * 60


def notify(message):
    subprocess.run(
        [NOTIFIER, message],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def process_table():
    processes = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            if not raw:
                continue
            arguments = [
                value.decode("utf-8", errors="replace")
                for value in raw.rstrip(b"\0").split(b"\0")
            ]
            status = (entry / "status").read_text(
                encoding="utf-8", errors="replace"
            )
            ppid_line = next(
                line for line in status.splitlines() if line.startswith("PPid:")
            )
            processes[int(entry.name)] = {
                "ppid": int(ppid_line.split()[1]),
                "arguments": arguments,
            }
        except (FileNotFoundError, PermissionError, ProcessLookupError, StopIteration):
            continue
    return processes


def script_path(arguments):
    for argument in arguments:
        if not argument.startswith(WATCH_ROOTS):
            continue
        name = Path(argument).name
        if name in EXCLUDED_NAMES:
            return None
        if name.endswith((".py", ".sh", ".ps1")):
            return argument
    return None


def top_level_scripts(processes):
    candidates = {
        pid: script_path(process["arguments"])
        for pid, process in processes.items()
    }
    candidates = {pid: path for pid, path in candidates.items() if path}
    result = {}
    for pid, path in candidates.items():
        parent = processes[pid]["ppid"]
        is_child = False
        while parent in processes and parent > 1:
            if parent in candidates:
                is_child = True
                break
            parent = processes[parent]["ppid"]
        if not is_child:
            result[pid] = path
    return result


def duration(seconds):
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def main():
    active = {}
    notify("**Local WSL script monitoring enabled.**")
    while True:
        now = time.time()
        scripts = top_level_scripts(process_table())

        for pid, path in scripts.items():
            if pid not in active:
                active[pid] = {
                    "path": path,
                    "started": now,
                    "last_status": now,
                }
                notify(f"**WSL script started:** `{Path(path).name}` (PID {pid})")
            elif now - active[pid]["last_status"] >= STATUS_SECONDS:
                runtime = duration(now - active[pid]["started"])
                notify(
                    f"**WSL script status:** `{Path(path).name}` is still running "
                    f"after {runtime} (PID {pid})."
                )
                active[pid]["last_status"] = now

        for pid in list(active):
            if pid in scripts:
                continue
            job = active.pop(pid)
            runtime = duration(now - job["started"])
            notify(
                f"**WSL script finished:** `{Path(job['path']).name}` "
                f"after {runtime} (PID {pid})."
            )
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
