#!/usr/bin/env python3
"""Alternating Copa/TV/movie transcode orchestrator.

This controller runs one transcode at a time and alternates:
  1. one Copa TS through the commercial-removal H.264 pipeline
  2. one H.265 TV or movie item

It intentionally delegates copy/encode/verify/archive/deploy behavior to the
existing worker scripts so replacement semantics stay consistent.
"""

import argparse
import csv
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


COPA_DIR = Path("/mnt/nfs-share-tvshows/TV Shows/Copa Mundial de la FIFA 2026 (2026)/Season 2026")
COPA_WORK = Path("/mnt/c/DATA/HANDBRAKE-TV")
COPA_COMMERCIAL_WORK = Path("/mnt/c/DATA/COMMERCIAL-WORK")
COPA_PIPELINE = Path("/mnt/c/DATA/copa-commercial-transcode.py")

MOVIE_WORKER = Path("/mnt/c/DATA/handbrake_transcode_worker.py")
MOVIE_ROOT = Path("/mnt/nfs-share-movies/Movies")
MOVIE_COMPLETED = Path("/mnt/nfs-share-movies/COMPLETED")
MOVIE_CSV = Path("/mnt/c/DATA/movie-file-sizes.csv")
MOVIE_HTML = Path("/mnt/c/DATA/movie-file-sizes.html")
MOVIE_HTML_GENERATOR = Path("/mnt/c/DATA/movie_csv_to_html.py")
MOVIE_LOG = Path("/mnt/c/DATA/combined-transcode-movies-h265.log")
MOVIE_WORK = Path("/mnt/c/DATA/HANDBRAKE-H265")
MOVIE_AVI_QUEUE = Path("/mnt/c/DATA/handbrake-movies-avi-h265-queue.csv")
MOVIE_WATCHDOG_QUEUE = Path("/mnt/c/DATA/orchestrator-movies-h265-queue.csv")
MOVIE_QUEUES = [
    MOVIE_WATCHDOG_QUEUE,
]

TV_WORKER = Path("/mnt/c/DATA/handbrake-project/handbrake_transcode_worker.py")
TV_ROOT = Path("/mnt/nfs-share-tvshows/TV Shows")
TV_COMPLETED = Path("/mnt/nfs-share-tvshows/COMPLETED")
TV_CSV = Path("/mnt/c/DATA/tvshow-file-sizes.csv")
TV_HTML = Path("/mnt/c/DATA/tvshow-file-sizes.html")
TV_HTML_GENERATOR = Path("/mnt/c/DATA/handbrake-project/tvshow_csv_to_html.py")
TV_LOG = Path("/mnt/c/DATA/combined-transcode-tv-h265.log")
TV_WORK = Path("/mnt/c/DATA/HANDBRAKE-TV-H265")
TV_AVI_QUEUE = Path("/mnt/c/DATA/handbrake-tvshows-avi-h265-queue.csv")
TV_WATCHDOG_QUEUE = Path("/mnt/c/DATA/orchestrator-tv-h265-queue.csv")
TV_QUEUES = [
    TV_WATCHDOG_QUEUE,
]

STATE_FILE = Path("/mnt/c/DATA/combined-transcode-orchestrator.state")
LOG_FILE = Path("/mnt/c/DATA/combined-transcode-orchestrator.log")
COMPLETED_LEDGER = Path("/mnt/c/DATA/transcode-completed-ledger.csv")
AVI_TARGET_HEADROOM = 1.05
MOVIE_GB_PER_HOUR = 0.5

LEDGER_FIELDS = [
    "media_type",
    "canonical_path",
    "source_path",
    "completed_at",
    "original_bytes",
    "final_bytes",
]


def effective_target_gb(source: Path, requested_target_gb: float) -> float:
    if source.suffix.lower() != ".avi":
        return requested_target_gb
    try:
        source_gb = source.stat().st_size / (1024 ** 3)
    except OSError:
        return requested_target_gb
    # AVI conversions are compatibility jobs. Avoid inflating cartoons/specials
    # far beyond their current size while still leaving a little muxing room.
    return max(0.02, min(requested_target_gb, source_gb * AVI_TARGET_HEADROOM))


def media_duration_seconds(source: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )
    try:
        return max(0.0, float(result.stdout.strip()))
    except ValueError:
        return 0.0


def movie_duration_target_gb(source: Path, fallback_target_gb: float) -> float:
    duration = media_duration_seconds(source)
    if duration <= 0:
        return fallback_target_gb
    return max(0.05, (duration / 3600.0) * MOVIE_GB_PER_HOUR)


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def run(command: list[str]) -> None:
    log("RUN " + " ".join(command))
    subprocess.run(command, check=True)


def command_output(command: list[str]) -> str:
    result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return result.stdout.strip()


def handbrake_running() -> bool:
    output = command_output(["pgrep", "-af", "HandBrakeCLI|handbrake_transcode_worker|copa-commercial-transcode.py|remove-commercials.py"])
    current = str(Path(__file__))
    lines = [
        line for line in output.splitlines()
        if current not in line
        and "pgrep -af" not in line
        and "grep -Ei" not in line
        and "ps -eo" not in line
    ]
    return bool(lines)


def source_done(source: Path) -> bool:
    if source.suffix.lower() != ".mp4" and source.with_suffix(".mp4").is_file():
        return True
    return not source.exists()


def canonical_media_path(media_type: str, source: Path) -> str:
    root = MOVIE_ROOT if media_type == "movie" else TV_ROOT
    try:
        relative = source.relative_to(root)
    except ValueError:
        relative = source
    # A compatibility conversion may change AVI/MKV/etc. to MP4. Treat the
    # extension-independent library location as the same completed asset.
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


def record_completed(
    media_type: str,
    source: Path,
    original_bytes: int,
) -> None:
    key = canonical_media_path(media_type, source)
    if key in completed_keys():
        return
    final_path = source
    if not final_path.exists() and source.suffix.lower() != ".mp4":
        final_path = source.with_suffix(".mp4")
    try:
        final_bytes = final_path.stat().st_size
    except OSError:
        final_bytes = 0
    new_file = not COMPLETED_LEDGER.exists() or COMPLETED_LEDGER.stat().st_size == 0
    with COMPLETED_LEDGER.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(
            {
                "media_type": media_type,
                "canonical_path": key,
                "source_path": str(source),
                "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "original_bytes": original_bytes,
                "final_bytes": final_bytes,
            }
        )
    log(f"Recorded completed asset in ledger: {key}")


def queue_paths(queue: Path) -> list[Path]:
    if not queue.is_file():
        return []
    paths: list[Path] = []
    with queue.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            value = row.get("file_path") or row.get("full_path") or row.get("path")
            if value:
                paths.append(Path(value))
    return paths


def eligible_movie_paths(target_gb: float, queues: list[Path] | None = None) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    done = completed_keys()
    for queue in queues or MOVIE_QUEUES:
        for source in queue_paths(queue):
            key = str(source)
            if key in seen:
                continue
            seen.add(key)
            if canonical_media_path("movie", source) in done:
                continue
            try:
                source.stat()
            except OSError:
                continue
            if source_done(source):
                continue
            paths.append(source)
    return paths


def eligible_tv_paths(target_gb: float, queues: list[Path] | None = None) -> list[Path]:
    maximum_completed_size = int(target_gb * 1.20 * (1024 ** 3))
    paths: list[Path] = []
    seen: set[str] = set()
    done = completed_keys()
    for queue in queues or TV_QUEUES:
        for source in queue_paths(queue):
            key = str(source)
            if key in seen:
                continue
            seen.add(key)
            if canonical_media_path("tv", source) in done:
                continue
            try:
                size = source.stat().st_size
            except OSError:
                continue
            if source.suffix.lower() != ".avi" and size <= maximum_completed_size:
                continue
            if source_done(source):
                continue
            paths.append(source)
    return paths


def eligible_copa_paths() -> list[Path]:
    if not COPA_DIR.is_dir():
        return []
    return sorted(
        (path for path in COPA_DIR.glob("*.ts") if not path.with_suffix(".mp4").is_file()),
        key=lambda item: item.name.lower(),
        reverse=True,
    )


def run_copa(source: Path, target_gb: float) -> None:
    run(
        [
            sys.executable,
            str(COPA_PIPELINE),
            str(source),
            "--target-gb",
            f"{target_gb:g}",
            "--audio-kbps",
            "96",
            "--preset",
            "slow",
            "--work-dir",
            str(COPA_WORK),
            "--commercial-work",
            str(COPA_COMMERCIAL_WORK),
        ]
    )


def run_movie(source: Path, target_gb: float) -> None:
    original_bytes = source.stat().st_size
    target_gb = movie_duration_target_gb(source, target_gb)
    log(f"Effective movie target for {source.name}: {target_gb:.3g} GB ({MOVIE_GB_PER_HOUR:g} GB/hour)")
    base = [
        sys.executable,
        str(MOVIE_WORKER),
        "--file",
        str(source),
        "--work-dir",
        str(MOVIE_WORK),
        "--target-gb",
        f"{target_gb:g}",
        "--encoder",
        "x265",
        "--preset",
        "slow",
        "--movie-root",
        str(MOVIE_ROOT),
        "--completed-dir",
        str(MOVIE_COMPLETED),
        "--library-csv",
        str(MOVIE_CSV),
        "--library-html",
        str(MOVIE_HTML),
        "--html-generator",
        str(MOVIE_HTML_GENERATOR),
        "--log-file",
        str(MOVIE_LOG),
    ]
    run(base)
    run(base + ["--replace-completed"])
    record_completed("movie", source, original_bytes)


def run_tv(source: Path, target_gb: float) -> None:
    original_bytes = source.stat().st_size
    target_gb = effective_target_gb(source, target_gb)
    log(f"Effective TV target for {source.name}: {target_gb:g} GB")
    base = [
        sys.executable,
        str(TV_WORKER),
        "--file",
        str(source),
        "--work-dir",
        str(TV_WORK),
        "--target-gb",
        f"{target_gb:g}",
        "--encoder",
        "x265",
        "--preset",
        "slow",
        "--movie-root",
        str(TV_ROOT),
        "--completed-dir",
        str(TV_COMPLETED),
        "--library-csv",
        str(TV_CSV),
        "--library-html",
        str(TV_HTML),
        "--html-generator",
        str(TV_HTML_GENERATOR),
        "--log-file",
        str(TV_LOG),
    ]
    run(base)
    run(base + ["--replace-completed"])
    record_completed("tv", source, original_bytes)


def read_next_non_copa_kind() -> str:
    if not STATE_FILE.exists():
        return "tv"
    value = STATE_FILE.read_text(encoding="utf-8").strip()
    return value if value in {"tv", "movie"} else "tv"


def write_next_non_copa_kind(value: str) -> None:
    STATE_FILE.write_text(value + "\n", encoding="utf-8")


def pick_non_copa(
    tv_target_gb: float,
    movie_target_gb: float,
    *,
    commit_state: bool,
    tv_queues: list[Path] | None = None,
    movie_queues: list[Path] | None = None,
) -> tuple[str, Path] | None:
    preferred = read_next_non_copa_kind()
    tv = eligible_tv_paths(tv_target_gb, tv_queues)
    movies = eligible_movie_paths(movie_target_gb, movie_queues)
    if preferred == "tv" and tv:
        if commit_state:
            write_next_non_copa_kind("movie")
        return "tv", tv[0]
    if preferred == "movie" and movies:
        if commit_state:
            write_next_non_copa_kind("tv")
        return "movie", movies[0]
    if tv:
        if commit_state:
            write_next_non_copa_kind("movie")
        return "tv", tv[0]
    if movies:
        if commit_state:
            write_next_non_copa_kind("tv")
        return "movie", movies[0]
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Alternate Copa H.264 jobs with TV/movie H.265 jobs.")
    parser.add_argument("--copa-target-gb", type=float, default=1.5)
    parser.add_argument("--tv-target-gb", type=float, default=0.7)
    parser.add_argument("--movie-target-gb", type=float, default=1.0)
    parser.add_argument("--max-cycles", type=int, default=0, help="0 means run until queues are empty.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ignore-active", action="store_true", help="Do not refuse to start when another encode is running.")
    parser.add_argument("--skip-copa", action="store_true", help="Only run TV/movie H.265 jobs; do not run Copa jobs.")
    parser.add_argument("--avi-only", action="store_true", help="Only use the dedicated TV/movie AVI H.265 queues.")
    parser.add_argument("--only-movie", action="store_true", help="Run only the movie H.265 lane.")
    parser.add_argument("--only-tv", action="store_true", help="Run only the TV H.265 lane.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.only_movie and args.only_tv:
        log("--only-movie and --only-tv cannot be used together.")
        return 2
    if handbrake_running() and not args.ignore_active:
        log("Another transcode is already running. Refusing to start a parallel encode.")
        return 3

    tv_queues = [TV_AVI_QUEUE] if args.avi_only else None
    movie_queues = [MOVIE_AVI_QUEUE] if args.avi_only else None

    cycle = 0
    while True:
        if args.max_cycles and cycle >= args.max_cycles:
            log(f"Reached max cycles: {args.max_cycles}")
            return 0
        cycle += 1

        did_work = False
        copa = [] if args.skip_copa else eligible_copa_paths()
        if args.only_movie:
            movies = eligible_movie_paths(args.movie_target_gb, movie_queues)
            non_copa = ("movie", movies[0]) if movies else None
        elif args.only_tv:
            tv = eligible_tv_paths(args.tv_target_gb, tv_queues)
            non_copa = ("tv", tv[0]) if tv else None
        else:
            non_copa = pick_non_copa(
                args.tv_target_gb,
                args.movie_target_gb,
                commit_state=not args.dry_run,
                tv_queues=tv_queues,
                movie_queues=movie_queues,
            )

        log(
            f"Cycle {cycle}: Copa pending={len(copa)}, "
            f"TV pending={len(eligible_tv_paths(args.tv_target_gb, tv_queues))}, "
            f"movie pending={len(eligible_movie_paths(args.movie_target_gb, movie_queues))}"
        )

        if copa:
            log(f"Selected Copa H.264 job: {copa[0]}")
            did_work = True
            if not args.dry_run:
                run_copa(copa[0], args.copa_target_gb)

        if non_copa:
            kind, source = non_copa
            log(f"Selected {kind.upper()} H.265 job: {source}")
            did_work = True
            if not args.dry_run:
                if kind == "tv":
                    run_tv(source, args.tv_target_gb)
                else:
                    run_movie(source, args.movie_target_gb)

        if not did_work:
            log("No eligible Copa, TV, or movie jobs remain.")
            return 0

        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
