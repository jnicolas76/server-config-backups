#!/usr/bin/env python3
"""Hourly self-healing watchdog for CineVault transcode queues.

This script is intended to be launched by cron. It performs one quick
validation pass, sends one Webex status message, and exits.
"""

import csv
import fcntl
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from properties_config import default_config_path, get_float, get_int, get_path, load_properties

DATA_DIR = Path("/mnt/c/DATA")
MOVIE_ROOT = Path("/mnt/nfs-share-movies/Movies")
TV_ROOT = Path("/mnt/nfs-share-tvshows/TV Shows")

ORCHESTRATOR = DATA_DIR / "combined_transcode_orchestrator.py"
ORCHESTRATOR_START = DATA_DIR / "start_combined_transcode_orchestrator.sh"
ORCHESTRATOR_PID = DATA_DIR / "combined-transcode-orchestrator.pid"

MOVIE_QUEUE = DATA_DIR / "orchestrator-movies-h265-queue.csv"
TV_QUEUE = DATA_DIR / "orchestrator-tv-h265-queue.csv"

LOG_FILE = DATA_DIR / "transcode-queue-watchdog.log"
LOCK_FILE = DATA_DIR / "transcode-queue-watchdog.lock"
NOTIFIER = Path("/home/jnicolas/bin/send_webex_notification")

MOVIE_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".m4v", ".avi", ".mov", ".mpeg", ".mpg", ".m2ts", ".ts", ".webm"}
TV_VIDEO_EXTENSIONS = MOVIE_VIDEO_EXTENSIONS

MOVIE_MIN_GB = float(os.environ.get("WATCHDOG_MOVIE_MIN_GB", "3.0"))
MOVIE_2160_MIN_GB = float(os.environ.get("WATCHDOG_MOVIE_2160_MIN_GB", "4.0"))
MOVIE_TARGET_GB = float(os.environ.get("WATCHDOG_MOVIE_TARGET_GB", "1.0"))
TV_TARGET_GB = float(os.environ.get("WATCHDOG_TV_TARGET_GB", "0.7"))

# Hourly scans should be bounded. Existing static queues still feed the
# orchestrator; these dynamic queues catch new arrivals and obvious AVI work.
MAX_MOVIE_QUEUE = int(os.environ.get("WATCHDOG_MAX_MOVIE_QUEUE", "500"))
MAX_TV_QUEUE = int(os.environ.get("WATCHDOG_MAX_TV_QUEUE", "1000"))
CONFIG_PATH = default_config_path("transcode-orchestrator")


def apply_properties(config_path: Path = CONFIG_PATH) -> None:
    config = load_properties(config_path)
    if not config:
        return

    global DATA_DIR, MOVIE_ROOT, TV_ROOT, ORCHESTRATOR, ORCHESTRATOR_START
    global ORCHESTRATOR_PID, MOVIE_QUEUE, TV_QUEUE, LOG_FILE, LOCK_FILE
    global NOTIFIER, MOVIE_MIN_GB, MOVIE_2160_MIN_GB, MOVIE_TARGET_GB
    global TV_TARGET_GB, MAX_MOVIE_QUEUE, MAX_TV_QUEUE

    DATA_DIR = get_path(config, "data.dir", DATA_DIR)
    MOVIE_ROOT = get_path(config, "movies.root", MOVIE_ROOT)
    TV_ROOT = get_path(config, "tv.root", TV_ROOT)
    ORCHESTRATOR = get_path(config, "orchestrator.script", Path(__file__).resolve().parent / "combined_transcode_orchestrator.py")
    ORCHESTRATOR_START = get_path(config, "orchestrator.start.script", Path(__file__).resolve().parent / "start_combined_transcode_orchestrator.sh")
    ORCHESTRATOR_PID = get_path(config, "orchestrator.pid.file", DATA_DIR / "combined-transcode-orchestrator.pid")
    MOVIE_QUEUE = get_path(config, "movies.queue", DATA_DIR / "orchestrator-movies-h265-queue.csv")
    TV_QUEUE = get_path(config, "tv.queue", DATA_DIR / "orchestrator-tv-h265-queue.csv")
    LOG_FILE = get_path(config, "watchdog.log", DATA_DIR / "transcode-queue-watchdog.log")
    LOCK_FILE = get_path(config, "watchdog.lock", DATA_DIR / "transcode-queue-watchdog.lock")
    NOTIFIER = get_path(config, "webex.notifier", NOTIFIER)
    MOVIE_MIN_GB = get_float(config, "movies.min.gb", MOVIE_MIN_GB)
    MOVIE_2160_MIN_GB = get_float(config, "movies.2160.min.gb", MOVIE_2160_MIN_GB)
    MOVIE_TARGET_GB = get_float(config, "movies.fallback.target.gb", MOVIE_TARGET_GB)
    TV_TARGET_GB = get_float(config, "tv.target.gb", TV_TARGET_GB)
    MAX_MOVIE_QUEUE = get_int(config, "watchdog.max.movie.queue", MAX_MOVIE_QUEUE)
    MAX_TV_QUEUE = get_int(config, "watchdog.max.tv.queue", MAX_TV_QUEUE)


def log(message: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def notify(message: str) -> None:
    if not NOTIFIER.is_file():
        log("Webex notifier missing; skipped notification")
        return
    subprocess.run([str(NOTIFIER), message], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def is_pid_running(pid_file: Path) -> bool:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    proc = Path(f"/proc/{pid}")
    if not proc.exists():
        return False
    try:
        state = (proc / "stat").read_text(encoding="utf-8").split()[2]
    except (OSError, IndexError):
        return False
    if state == "Z":
        return False
    try:
        cmdline = (proc / "cmdline").read_text(encoding="utf-8").replace("\x00", " ")
    except OSError:
        return False
    return "combined_transcode_orchestrator.py" in cmdline


def active_transcode_lines() -> list[str]:
    result = run(["pgrep", "-af", "HandBrakeCLI|handbrake_transcode_worker"], timeout=20)
    lines = []
    for line in result.stdout.splitlines():
        if (
            "pgrep -af" in line
            or "grep -Ei" in line
            or "ps -eo" in line
            or Path(__file__).name in line
        ):
            continue
        lines.append(line)
    return lines


def is_transcode_active() -> bool:
    return bool(active_transcode_lines())


def path_is_done(source: Path) -> bool:
    if source.suffix.lower() != ".mp4" and source.with_suffix(".mp4").is_file():
        return True
    return not source.exists()


def write_queue(path: Path, sources: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file_path"])
        writer.writeheader()
        for source in sources:
            writer.writerow({"file_path": str(source)})
    tmp.replace(path)


def queue_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return max(0, sum(1 for _ in csv.DictReader(handle)))


def queue_paths(path: Path) -> list[Path]:
    if not path.is_file():
        return []
    paths: list[Path] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            value = row.get("file_path") or row.get("full_path") or row.get("path")
            if value:
                paths.append(Path(value))
    return paths


def preserve_existing_order(existing: list[Path], scanned: list[Path], predicate, limit: int) -> list[Path]:
    merged: list[Path] = []
    seen: set[str] = set()
    for source in existing + scanned:
        key = str(source)
        if key in seen:
            continue
        seen.add(key)
        if predicate(source):
            merged.append(source)
            if len(merged) >= limit:
                break
    return merged


def codec_is_2160(path: Path) -> bool:
    text = str(path).lower()
    if "2160" in text or "4k" in text:
        return True
    # Use filename/path as the fast path; hourly NFS media probing is avoided.
    return False


def movie_candidate(path: Path) -> bool:
    if path.suffix.lower() not in MOVIE_VIDEO_EXTENSIONS:
        return False
    if path_is_done(path):
        return False
    try:
        size_gb = path.stat().st_size / (1024 ** 3)
    except OSError:
        return False
    if path.suffix.lower() == ".avi":
        return True
    if codec_is_2160(path):
        return size_gb > MOVIE_2160_MIN_GB
    return size_gb > MOVIE_MIN_GB


def tv_candidate(path: Path) -> bool:
    if path.suffix.lower() not in TV_VIDEO_EXTENSIONS:
        return False
    if path_is_done(path):
        return False
    # Current TV policy: compatibility H.265 conversion for AVI.
    return path.suffix.lower() == ".avi"


def scan_sources(root: Path, predicate, limit: int) -> list[Path]:
    if not root.is_dir():
        return []
    result: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name.upper() not in {"COMPLETED", "@EADIR"}]
        for name in filenames:
            path = Path(dirpath) / name
            if predicate(path):
                result.append(path)
                if len(result) >= limit:
                    return sorted(result, key=lambda item: str(item).lower())
    return sorted(result, key=lambda item: str(item).lower())


def start_orchestrator() -> tuple[bool, str]:
    if is_transcode_active():
        return False, "encode already active"
    if is_pid_running(ORCHESTRATOR_PID):
        return False, "orchestrator already active"
    if not ORCHESTRATOR_START.is_file():
        return False, "starter script missing"
    result = run(
        [
            "bash",
            str(ORCHESTRATOR_START),
            "--skip-copa",
            "--tv-target-gb",
            f"{TV_TARGET_GB:g}",
            "--movie-target-gb",
            f"{MOVIE_TARGET_GB:g}",
        ],
        timeout=60,
    )
    if result.returncode == 0:
        return True, result.stdout.strip().splitlines()[0] if result.stdout.strip() else "started"
    return False, f"start failed rc={result.returncode}: {(result.stderr or result.stdout).strip()[:500]}"


def stale_recovery_note() -> str:
    if not ORCHESTRATOR_PID.is_file():
        return "no pid file"
    if is_pid_running(ORCHESTRATOR_PID):
        return "pid healthy"
    try:
        stale_pid = ORCHESTRATOR_PID.read_text(encoding="utf-8").strip()
    except OSError:
        stale_pid = "unknown"
    return f"stale pid recovered: {stale_pid}"


def first_queue_items(path: Path, limit: int = 3) -> list[str]:
    if not path.is_file():
        return []
    items: list[str] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            value = row.get("file_path") or row.get("full_path") or row.get("path")
            if not value:
                continue
            items.append(Path(value).name)
            if len(items) >= limit:
                break
    return items


def active_job_summary() -> str:
    lines = active_transcode_lines()
    if not lines:
        return "none visible yet"
    worker = next((line for line in lines if "handbrake_transcode_worker.py" in line), lines[0])
    match = re.search(r"--file\s+(.+?)\s+--work-dir", worker)
    if match:
        return Path(match.group(1)).name
    handbrake = next((line for line in lines if "HandBrakeCLI" in line), "")
    match = re.search(r"-i\s+(.+?)\s+-o", handbrake)
    if match:
        return Path(match.group(1)).name
    return worker.split(maxsplit=1)[1][:180] if " " in worker else worker[:180]


def latest_progress(log_path: Path) -> str:
    if not log_path.is_file():
        return ""
    try:
        result = run(["tail", "-n", "250", str(log_path)], timeout=20)
    except Exception:
        return ""
    for line in reversed(result.stdout.splitlines()):
        if "Encoding:" in line and "%" in line:
            match = re.search(r"Encoding: (.+)", line)
            return match.group(1).strip() if match else line[-160:]
    return ""


def main() -> int:
    apply_properties()
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Another watchdog run is active; exiting")
            return 0

        started_at = time.monotonic()
        notes: list[str] = []
        try:
            movies = preserve_existing_order(
                queue_paths(MOVIE_QUEUE),
                scan_sources(MOVIE_ROOT, movie_candidate, MAX_MOVIE_QUEUE),
                movie_candidate,
                MAX_MOVIE_QUEUE,
            )
            tv = preserve_existing_order(
                queue_paths(TV_QUEUE),
                scan_sources(TV_ROOT, tv_candidate, MAX_TV_QUEUE),
                tv_candidate,
                MAX_TV_QUEUE,
            )
            write_queue(MOVIE_QUEUE, movies)
            write_queue(TV_QUEUE, tv)
            notes.append(stale_recovery_note())

            active_before = is_transcode_active()
            started, start_note = start_orchestrator()
            active_after = is_transcode_active() or is_pid_running(ORCHESTRATOR_PID)
            if started:
                notes.append(f"self-healing action: {start_note}")
            else:
                notes.append(f"no start needed: {start_note}")

            current_job = active_job_summary()
            progress = latest_progress(DATA_DIR / "combined-transcode-movies-h265.log")
            if not progress:
                progress = latest_progress(DATA_DIR / "combined-transcode-tv-h265.log")
            movie_next = ", ".join(first_queue_items(MOVIE_QUEUE)) or "none"
            tv_next = ", ".join(first_queue_items(TV_QUEUE)) or "none"
            elapsed = time.monotonic() - started_at

            message = (
                "**Transcode watchdog hourly status**\n\n"
                f"- Movie H.265 queue: {len(movies)} pending\n"
                f"- TV H.265 queue: {len(tv)} pending\n"
                f"- Orchestrator PID healthy: {'yes' if is_pid_running(ORCHESTRATOR_PID) else 'no'}\n"
                f"- Encode active before check: {'yes' if active_before else 'no'}\n"
                f"- Encode/orchestrator active after check: {'yes' if active_after else 'no'}\n"
                f"- Current job: `{current_job}`\n"
                f"- Current progress: `{progress or 'not reported yet'}`\n"
                f"- Next movies: {movie_next}\n"
                f"- Next TV: {tv_next}\n"
                f"- Validation notes: {'; '.join(notes)}\n"
                f"- Watchdog runtime: {elapsed:.1f}s"
            )
            log(message.replace("\n", " | "))
            notify(message)
        except Exception as exc:
            log(f"Watchdog failed: {exc}")
            notify(f"**Transcode watchdog failed**\n\n`{exc}`")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
