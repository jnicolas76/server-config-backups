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


DATA_DIR = Path("/mnt/c/DATA")
MOVIE_ROOT = Path("/mnt/nfs-share-movies/Movies")
TV_ROOT = Path("/mnt/nfs-share-tvshows/TV Shows")
MOVIE_COMPLETED = Path("/mnt/nfs-share-movies/COMPLETED")
TV_COMPLETED = Path("/mnt/nfs-share-tvshows/COMPLETED")

ORCHESTRATOR = DATA_DIR / "combined_transcode_orchestrator.py"
ORCHESTRATOR_START = DATA_DIR / "start_combined_transcode_orchestrator.sh"
ORCHESTRATOR_PID = DATA_DIR / "combined-transcode-orchestrator.pid"
MOVIE_LANE_PID = DATA_DIR / "movie-lane-transcode.pid"
TV_AVI_LANE_PID = DATA_DIR / "tv-avi-lane-transcode.pid"

MOVIE_QUEUE = DATA_DIR / "orchestrator-movies-h265-queue.csv"
TV_QUEUE = DATA_DIR / "orchestrator-tv-h265-queue.csv"
COMPLETED_LEDGER = DATA_DIR / "transcode-completed-ledger.csv"

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
ENABLE_DYNAMIC_SCAN = os.environ.get("WATCHDOG_ENABLE_DYNAMIC_SCAN", "0").strip().lower() in {"1", "true", "yes", "on"}


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


def lane_pid_running(pid_file: Path, lane_flag: str) -> bool:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    proc = Path(f"/proc/{pid}")
    if not proc.exists():
        return False
    try:
        state = (proc / "stat").read_text(encoding="utf-8").split()[2]
        cmdline = (proc / "cmdline").read_text(encoding="utf-8").replace("\x00", " ")
    except (OSError, IndexError):
        return False
    return state != "Z" and "combined_transcode_orchestrator.py" in cmdline and lane_flag in cmdline


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
    if lines:
        return lines

    # pgrep can miss shortly-lived command lines during process churn. A ps
    # fallback is cheap and avoids starting another orchestrator while an
    # encode worker is already alive.
    result = run(["ps", "-eo", "pid,args"], timeout=20)
    for line in result.stdout.splitlines():
        if "HandBrakeCLI" not in line and "handbrake_transcode_worker.py" not in line:
            continue
        if Path(__file__).name in line:
            continue
        lines.append(line.strip())
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


def canonical_media_path(media_type: str, source: Path) -> str:
    root = MOVIE_ROOT if media_type == "movie" else TV_ROOT
    try:
        relative = source.relative_to(root)
    except ValueError:
        relative = source
    return f"{media_type}:{relative.with_suffix('').as_posix().casefold()}"


def completed_keys() -> set[str]:
    if not COMPLETED_LEDGER.is_file():
        return set()
    with COMPLETED_LEDGER.open(newline="", encoding="utf-8-sig") as handle:
        return {
            row["canonical_path"]
            for row in csv.DictReader(handle)
            if row.get("canonical_path")
        }


def exclude_completed(media_type: str, sources: list[Path]) -> list[Path]:
    done = completed_keys()
    return [source for source in sources if canonical_media_path(media_type, source) not in done]


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


def bounded_existing_queue(path: Path, limit: int) -> list[Path]:
    """Return existing queue rows without touching NFS paths.

    The hourly watchdog must always reach Webex. Full NFS validation can hang
    inside CIFS/kernel I/O, so by default we trust the existing queue file and
    let the orchestrator handle skipped or completed assets one at a time.
    Set WATCHDOG_ENABLE_DYNAMIC_SCAN=1 only when a deeper rescan is needed.
    """
    return queue_paths(path)[:limit]


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


def start_dual_lanes() -> tuple[bool, str]:
    movie_ok = lane_pid_running(MOVIE_LANE_PID, "--only-movie")
    tv_ok = lane_pid_running(TV_AVI_LANE_PID, "--only-tv")
    if movie_ok and tv_ok:
        return False, "movie and TV AVI lanes already active"
    if not DUAL_LANE_START.is_file():
        return False, "dual-lane starter script missing"
    result = run(["bash", str(DUAL_LANE_START)], timeout=60)
    if result.returncode == 0:
        details = "; ".join(line.strip() for line in result.stdout.splitlines() if line.strip())[:700]
        return True, details or "dual lanes started"
    return False, f"dual-lane start failed rc={result.returncode}: {(result.stderr or result.stdout).strip()[:500]}"


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


def freshest_progress(log_paths: list[Path]) -> str:
    available = [path for path in log_paths if path.is_file()]
    available.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in available:
        progress = latest_progress(path)
        if progress:
            return progress
    return ""


def disk_usage_line(label: str, path: Path) -> str:
    if not path.exists():
        return f"{label}: unavailable ({path})"
    result = run(["df", "-h", str(path)], timeout=20)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return f"{label}: df failed ({detail[-1] if detail else 'unknown error'})"
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        return f"{label}: df returned no data"
    parts = lines[1].split()
    if len(parts) < 6:
        return f"{label}: {lines[1]}"
    filesystem, size, used, avail, pct, mount = parts[0], parts[1], parts[2], parts[3], parts[4], parts[-1]
    return f"{label}: {avail} free of {size} ({used} used, {pct}) on {mount} [{filesystem}]"


def completed_folder_line(label: str, path: Path) -> str:
    if not path.exists():
        return f"{label}: unavailable ({path})"
    du = run(["du", "-sh", str(path)], timeout=60)
    size = "unknown size"
    if du.returncode == 0 and du.stdout.strip():
        size = du.stdout.splitlines()[0].split()[0]
    count = "unknown"
    find = run(["find", str(path), "-type", "f"], timeout=60)
    if find.returncode == 0:
        count = str(len([line for line in find.stdout.splitlines() if line.strip()]))
    return f"{label}: {count} files, {size}"


def main() -> int:
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
            if ENABLE_DYNAMIC_SCAN:
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
                notes.append("dynamic scan enabled")
            else:
                movies = bounded_existing_queue(MOVIE_QUEUE, MAX_MOVIE_QUEUE)
                tv = bounded_existing_queue(TV_QUEUE, MAX_TV_QUEUE)
                notes.append("dynamic scan skipped for fast hourly status")
            movies = exclude_completed("movie", movies)
            tv = exclude_completed("tv", tv)
            write_queue(MOVIE_QUEUE, movies)
            write_queue(TV_QUEUE, tv)
            notes.append(stale_recovery_note())

            active_before = is_transcode_active()
            started, start_note = start_orchestrator()
            movie_lane_healthy = False
            tv_lane_healthy = False
            active_after = is_transcode_active() or is_pid_running(ORCHESTRATOR_PID)
            if started:
                notes.append(f"self-healing action: {start_note}")
            else:
                notes.append(f"no start needed: {start_note}")

            current_job = active_job_summary()
            progress = freshest_progress(
                [
                    DATA_DIR / "combined-transcode-movies-h265.log",
                    DATA_DIR / "combined-transcode-tv-h265.log",
                ]
            )
            movie_next = ", ".join(first_queue_items(MOVIE_QUEUE)) or "none"
            tv_next = ", ".join(first_queue_items(TV_QUEUE)) or "none"
            movies_disk = disk_usage_line("Movies disk", MOVIE_ROOT)
            tv_disk = disk_usage_line("TV Shows disk", TV_ROOT)
            movie_completed = completed_folder_line("Movies COMPLETED", MOVIE_COMPLETED)
            tv_completed = completed_folder_line("TV Shows COMPLETED", TV_COMPLETED)
            elapsed = time.monotonic() - started_at

            single_lane_healthy = is_pid_running(ORCHESTRATOR_PID)
            message = (
                "**Transcode watchdog hourly status**\n\n"
                f"- Movie H.265 queue: {len(movies)} pending\n"
                f"- TV H.265 queue: {len(tv)} pending\n"
                f"- {movies_disk}\n"
                f"- {tv_disk}\n"
                f"- {movie_completed}\n"
                f"- {tv_completed}\n"
                f"- Single-lane orchestrator healthy: {'yes' if single_lane_healthy else 'no'}\n"
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
