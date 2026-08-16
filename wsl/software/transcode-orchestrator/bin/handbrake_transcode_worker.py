#!/usr/bin/env python3
"""Copy, transcode, verify, archive, and replace one or more video files.

This is the main worker behind the HandBrake shell launchers. It copies an
NFS source into the local WSL work directory, calculates a bitrate for the
requested target size, and invokes HandBrakeCLI. After encoding, it verifies
the output's duration and size before making any library changes.

In replacement mode, the worker archives the original file under COMPLETED,
moves the verified encode back into the source folder, removes local temporary
files, updates the movie CSV/HTML reports, and sends Webex job notifications.

Inputs may come from a queue CSV or a single --file argument. HandBrake itself
only encodes; all file movement and verification are handled by this script.
"""

import argparse
import csv
import logging
import math
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from properties_config import default_config_path, get_float, get_int, get_path, load_properties
from webex_job_notifications import WebexJob, file_size

WORK_DIR = Path("/mnt/c/DATA/HANDBRAKE")
DEFAULT_MOVIE_ROOT = Path("/mnt/nfs-share-movies/Movies")
DEFAULT_LIBRARY_CSV = Path("/mnt/c/DATA/movie-file-sizes.csv")
DEFAULT_LIBRARY_HTML = Path("/mnt/c/DATA/movie-file-sizes.html")
DEFAULT_HTML_GENERATOR = Path("/mnt/c/DATA/movie_csv_to_html.py")
DEFAULT_QUEUE = Path("/mnt/c/DATA/handbrake-queue.csv")
DEFAULT_COMPLETED_DIR = Path("/mnt/nfs-share-movies/COMPLETED")
DEFAULT_LOG_FILE = Path("/mnt/c/DATA/handbrake-convert.log")
DEFAULT_TARGET_GB = 2.5
DEFAULT_AUDIO_KBPS = 160
MIN_VIDEO_KBPS = 600
MAX_VIDEO_KBPS = 24000
DEFAULT_ENCODER = "x265"
DEFAULT_CONTAINER = "mp4"


logger = logging.getLogger("handbrake-worker")


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)


def log_step(message: str) -> None:
    logger.info(message)


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    log_step("RUN " + " ".join(command))
    if capture:
        result = subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.stderr:
            for line in result.stderr.splitlines():
                log_step(line)
        return result

    process = subprocess.Popen(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )

    assert process.stdout is not None
    for line in process.stdout:
        log_step(line.rstrip())

    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command)

    return subprocess.CompletedProcess(command, return_code)


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Missing required tool: {name}")


def handbrake_help_text() -> str:
    result = subprocess.run(
        ["HandBrakeCLI", "--help"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.stdout or ""


def handbrake_pass_options() -> list[str]:
    help_text = handbrake_help_text()
    if "--multi-pass" in help_text:
        options = ["--multi-pass"]
    elif "--two-pass" in help_text:
        options = ["--two-pass"]
    else:
        log_step("HandBrakeCLI does not advertise multi-pass/two-pass support; using single-pass bitrate mode.")
        return []

    if "--turbo" in help_text:
        options.append("--turbo")
    return options


def safe_name(path: Path) -> str:
    cleaned = "".join(char if char not in '<>:"/\\|?*' else "_" for char in path.stem)
    return cleaned.strip() or "movie"


def duration_seconds(path: Path) -> float:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture=True,
    )
    return float(result.stdout.strip())


def require_valid_output(output_path: Path, source_path: Path) -> None:
    if not output_path.is_file():
        raise RuntimeError(f"HandBrake did not create output file: {output_path}")

    output_size = output_path.stat().st_size
    source_size = source_path.stat().st_size if source_path.exists() else 0
    log_step(f"Output file size: {human_size(output_size)} ({output_size} bytes)")

    minimum_output_size = min(100 * 1024 * 1024, max(5 * 1024 * 1024, int(source_size * 0.25)))
    if output_size < minimum_output_size:
        raise RuntimeError(
            f"Output file is suspiciously small: {output_path} is {human_size(output_size)}; "
            f"minimum expected is {human_size(minimum_output_size)}"
        )

    if source_size and output_size < source_size * 0.01:
        raise RuntimeError(
            f"Output file is less than 1% of source size; refusing to mark complete: {output_path}"
        )

    try:
        output_duration = duration_seconds(output_path)
        source_duration = duration_seconds(source_path)
    except Exception as exc:
        raise RuntimeError(f"Could not verify output duration for {output_path}: {exc}") from exc

    log_step(f"Verified output duration: {output_duration / 60:.1f} minutes")
    if source_duration > 0 and output_duration < source_duration * 0.95:
        raise RuntimeError(
            f"Output duration is too short: source {source_duration:.1f}s, output {output_duration:.1f}s"
        )


def video_bitrate_kbps(duration: float, target_gb: float, audio_kbps: int) -> int:
    target_bytes = target_gb * 1024 * 1024 * 1024
    total_kbps = (target_bytes * 8) / duration / 1000
    video_kbps = math.floor(total_kbps - audio_kbps)
    return max(MIN_VIDEO_KBPS, min(MAX_VIDEO_KBPS, video_kbps))


def copy_source(source: Path, work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    local_source = work_dir / source.name

    if local_source.resolve() == source.resolve():
        return source

    if local_source.exists() and local_source.stat().st_size == source.stat().st_size:
        log_step(f"Using existing local copy: {local_source}")
        return local_source

    log_step(f"Copying source to local work folder: {source} -> {local_source}")
    shutil.copy2(source, local_source)
    log_step(f"Copied source to local work folder: {local_source}")
    return local_source


def local_source_for(source: Path, work_dir: Path) -> Path:
    return work_dir / source.name


def encoder_label(encoder: str) -> str:
    if encoder == "x264":
        return "h264"
    if encoder == "x265":
        return "h265"
    return encoder


def container_extension(container: str) -> str:
    if container == "mkv":
        return "mkv"
    return "mp4"


def handbrake_format(container: str) -> str:
    if container == "mkv":
        return "av_mkv"
    return "av_mp4"


def output_path_for(local_source: Path, work_dir: Path, target_gb: float, encoder: str, container: str) -> Path:
    extension = container_extension(container)
    return work_dir / f"{safe_name(local_source)}.{encoder_label(encoder)}.target-{target_gb:g}GB.{extension}"


def completed_output_path_for(source: Path, work_dir: Path, target_gb: float, encoder: str, container: str) -> Path:
    extension = container_extension(container)
    return work_dir / f"{safe_name(source)}.{encoder_label(encoder)}.target-{target_gb:g}GB.{extension}"


def final_replacement_path_for(source: Path, container: str) -> Path:
    return source.with_suffix(f".{container_extension(container)}")


def archive_original(source: Path, movie_root: Path, completed_dir: Path) -> Path:
    completed_dir.mkdir(parents=True, exist_ok=True)
    try:
        relative_source = source.relative_to(movie_root)
    except ValueError:
        relative_source = Path(source.name)

    archive_path = completed_dir / relative_source
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    if archive_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archive_path = archive_path.with_name(f"{archive_path.stem}.{timestamp}{archive_path.suffix}")

    log_step(f"Moving original to completed archive: {source} -> {archive_path}")
    shutil.move(str(source), str(archive_path))
    log_step(f"Moved original to completed archive: {archive_path}")
    return archive_path


def human_size(size_bytes: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size_bytes}B"


def csv_row_for_file(path: Path, root: Path) -> dict[str, str]:
    stat_result = path.stat()
    folder_path = path.parent
    try:
        relative_folder = folder_path.relative_to(root)
        relative_folder_text = "" if str(relative_folder) == "." else str(relative_folder)
    except ValueError:
        relative_folder_text = str(folder_path)

    return {
        "root": str(root),
        "relative_folder": relative_folder_text,
        "folder_path": str(folder_path),
        "file_name": path.name,
        "file_path": str(path),
        "extension": path.suffix[1:].lower(),
        "size_bytes": str(stat_result.st_size),
        "size_human": human_size(stat_result.st_size),
        "modified_time": datetime.fromtimestamp(stat_result.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def update_library_csv(csv_file: Path, root: Path, old_path: Path, new_path: Path) -> None:
    fieldnames = [
        "root",
        "relative_folder",
        "folder_path",
        "file_name",
        "file_path",
        "extension",
        "size_bytes",
        "size_human",
        "modified_time",
    ]

    rows: list[dict[str, str]] = []
    if csv_file.exists():
        with csv_file.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            rows = [
                row
                for row in reader
                if row.get("file_path") not in {str(old_path), str(new_path)}
            ]

    rows.append(csv_row_for_file(new_path, root))
    rows.sort(key=lambda row: row.get("file_path", "").lower())

    with csv_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    log_step(f"Updated library CSV row: {old_path} -> {new_path}")


def regenerate_html(csv_file: Path, html_file: Path, generator: Path) -> None:
    if not generator.is_file():
        log_step(f"Skipping HTML regeneration; generator not found: {generator}")
        return
    run(["python3", str(generator), str(csv_file), str(html_file)])
    log_step(f"Regenerated HTML report: {html_file}")


def replace_completed_one(
    source: Path,
    *,
    work_dir: Path,
    target_gb: float,
    encoder: str,
    container: str,
    movie_root: Path,
    completed_dir: Path,
    library_csv: Path,
    library_html: Path,
    html_generator: Path,
    overwrite: bool,
    dry_run: bool,
) -> None:
    encoded_output = completed_output_path_for(source, work_dir, target_gb, encoder, container)
    final_path = final_replacement_path_for(source, container)
    local_source = work_dir / source.name

    if final_path.is_file() and not source.exists() and not encoded_output.exists():
        log_step(f"Replacement already completed: {final_path}")
        if dry_run:
            log_step("Dry run only; no CSV/HTML changed.")
            return
        update_library_csv(library_csv, movie_root, source, final_path)
        regenerate_html(library_csv, library_html, html_generator)
        return

    if not encoded_output.is_file():
        log_step(f"Skipping; completed encode not found: {encoded_output}")
        return
    if not source.is_file():
        log_step(f"Skipping; original source not found: {source}")
        return
    if final_path.exists() and final_path != source and not overwrite:
        log_step(f"Skipping; destination already exists: {final_path}. Use --overwrite if you want to replace it.")
        return

    log_step(f"Original source: {source}")
    log_step(f"Completed encode: {encoded_output}")
    log_step(f"Replacement path: {final_path}")

    if dry_run:
        log_step("Dry run only; no files changed.")
        return

    if final_path.exists() and final_path != source:
        existing_archive = archive_original(final_path, movie_root, completed_dir)
        log_step(f"Archived existing destination instead of deleting it: {existing_archive}")

    archive_path = archive_original(source, movie_root, completed_dir)
    log_step(f"Original archived instead of deleted: {archive_path}")
    log_step(f"Moving completed encode back to original folder: {encoded_output} -> {final_path}")
    shutil.move(str(encoded_output), str(final_path))
    log_step(f"Moved completed encode back to original folder: {final_path}")

    if local_source.exists() and local_source != final_path:
        log_step(f"Deleting local source copy after archive/replacement: {local_source}")
        local_source.unlink()
        log_step(f"Deleted local source copy: {local_source}")

    update_library_csv(library_csv, movie_root, source, final_path)
    regenerate_html(library_csv, library_html, html_generator)


def transcode_one(
    source: Path,
    *,
    work_dir: Path,
    target_gb: float,
    audio_kbps: int,
    encoder: str,
    container: str,
    preset: str,
    overwrite: bool,
    dry_run: bool,
    resume_local: bool,
) -> None:
    if not source.is_file():
        if resume_local and local_source_for(source, work_dir).is_file():
            log_step(f"Original missing, but local resume file exists: {local_source_for(source, work_dir)}")
        else:
            log_step(f"Skipping missing file: {source}")
            return

    if resume_local:
        local_source = local_source_for(source, work_dir)
        if not local_source.is_file():
            log_step(f"Skipping; resume requested but local file not found: {local_source}")
            return
        log_step(f"Resuming from existing local file: {local_source}")
    else:
        local_source = source if dry_run else copy_source(source, work_dir)
    output_path = output_path_for(local_source, work_dir, target_gb, encoder, container)

    if output_path.exists() and not overwrite:
        try:
            require_valid_output(output_path, local_source)
        except Exception as exc:
            if dry_run:
                log_step(f"Existing output is invalid and would be removed before retry: {output_path} ({exc})")
            else:
                log_step(f"Existing output is invalid and will be removed: {output_path} ({exc})")
                output_path.unlink()
        else:
            log_step(f"Skipping existing valid output: {output_path}")
            return

    duration = duration_seconds(local_source)
    video_kbps = video_bitrate_kbps(duration, target_gb, audio_kbps)
    pass_options = handbrake_pass_options()
    if pass_options:
        log_step(f"Using HandBrake pass options: {' '.join(pass_options)}")

    command = [
        "HandBrakeCLI",
        "-i",
        str(local_source),
        "-o",
        str(output_path),
        "--format",
        handbrake_format(container),
        "--encoder",
        encoder,
        "--encoder-preset",
        preset,
        "--vb",
        str(video_kbps),
        *pass_options,
        "--rate",
        "auto",
        "--pfr",
        "--audio",
        "1",
        "--aencoder",
        "av_aac",
        "--ab",
        str(audio_kbps),
        "--mixdown",
        "stereo",
    ]
    if container == "mp4":
        command.append("--optimize")

    log_step(f"Prepared transcode source: {source}")
    log_step(f"Prepared transcode local copy: {local_source}")
    log_step(f"Prepared transcode output: {output_path}")
    log_step(f"Duration {duration / 60:.1f} minutes; target {target_gb:g} GB; audio {audio_kbps} kbps; video {video_kbps} kbps")
    log_step("HandBrake command: " + " ".join(f"'{part}'" if " " in part else part for part in command))

    if not dry_run:
        run(command)
        require_valid_output(output_path, local_source)
        log_step(f"Completed transcode output: {output_path}")
    else:
        log_step("Dry run only; transcode command was not executed.")


def paths_from_queue(queue_csv: Path) -> list[Path]:
    with queue_csv.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        paths = []
        for row in reader:
            value = row.get("full_path") or row.get("file_path") or row.get("path")
            if value:
                paths.append(Path(value))
        return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy movie files locally and transcode them with HandBrakeCLI."
    )
    parser.add_argument("--config", default=str(default_config_path("transcode-orchestrator")))
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--file", help="Single movie file to transcode.")
    source.add_argument(
        "--queue",
        default=None,
        help=f"CSV queue exported from the HTML report. Default when no --file is given: {DEFAULT_QUEUE}",
    )
    parser.add_argument("--work-dir", default=str(WORK_DIR), help=f"Default: {WORK_DIR}")
    parser.add_argument("--target-gb", type=float, default=DEFAULT_TARGET_GB)
    parser.add_argument("--audio-kbps", type=int, default=DEFAULT_AUDIO_KBPS)
    parser.add_argument("--preset", default="slow", choices=["medium", "slow", "slower"])
    parser.add_argument("--encoder", default=DEFAULT_ENCODER, choices=["x264", "x265"])
    parser.add_argument("--container", default=DEFAULT_CONTAINER, choices=["mp4", "mkv"])
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--resume-local",
        action="store_true",
        help="Use already-copied source files in the work directory instead of copying from the NAS again.",
    )
    parser.add_argument(
        "--replace-completed",
        action="store_true",
        help="Do not encode. Move completed output back beside the original, delete the original, and update CSV/HTML.",
    )
    parser.add_argument("--movie-root", default=str(DEFAULT_MOVIE_ROOT))
    parser.add_argument("--library-csv", default=str(DEFAULT_LIBRARY_CSV))
    parser.add_argument("--library-html", default=str(DEFAULT_LIBRARY_HTML))
    parser.add_argument("--html-generator", default=str(DEFAULT_HTML_GENERATOR))
    parser.add_argument("--completed-dir", default=str(DEFAULT_COMPLETED_DIR))
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE))
    return parser.parse_args()


def apply_properties_to_args(args: argparse.Namespace) -> argparse.Namespace:
    config = load_properties(Path(args.config))
    if not config:
        return args

    media_kind = config.get("worker.kind", "movies").strip().lower()
    prefix = "tv" if media_kind in {"tv", "tvshows", "shows"} else "movies"
    data_dir = get_path(config, "data.dir", "/mnt/c/DATA")

    args.work_dir = str(get_path(config, f"{prefix}.work.dir", args.work_dir))
    args.target_gb = get_float(
        config,
        f"{prefix}.fallback.target.gb" if prefix == "movies" else f"{prefix}.target.gb",
        args.target_gb,
    )
    args.audio_kbps = get_int(config, f"{prefix}.audio.kbps", args.audio_kbps)
    args.preset = config.get(f"{prefix}.preset", args.preset)
    args.encoder = config.get(f"{prefix}.encoder", args.encoder)
    args.container = config.get(f"{prefix}.container", args.container)
    args.movie_root = str(get_path(config, f"{prefix}.root", args.movie_root))
    args.completed_dir = str(get_path(config, f"{prefix}.completed.dir", args.completed_dir))
    args.library_csv = str(get_path(config, f"{prefix}.library.csv", args.library_csv))
    args.library_html = str(get_path(config, f"{prefix}.library.html", args.library_html))
    args.html_generator = str(get_path(config, f"{prefix}.html.generator", args.html_generator))
    args.log_file = str(get_path(config, f"{prefix}.log", data_dir / "handbrake-convert.log"))
    if args.queue is None:
        args.queue = str(get_path(config, f"{prefix}.queue", DEFAULT_QUEUE))
    return args


def main() -> int:
    args = parse_args()
    args = apply_properties_to_args(args)
    setup_logging(Path(args.log_file))
    log_step("Starting handbrake worker")
    require_tool("HandBrakeCLI")
    require_tool("ffprobe")

    work_dir = Path(args.work_dir)

    if args.queue:
        log_step(f"Using queue: {args.queue}")
        sources = paths_from_queue(Path(args.queue))
    elif args.file:
        log_step(f"Using single file: {args.file}")
        sources = [Path(args.file)]
    else:
        default_queue = DEFAULT_QUEUE
        if not default_queue.is_file():
            log_step(f"No --file or --queue provided, and default queue was not found: {default_queue}")
            return 1
        log_step(f"Using default queue: {default_queue}")
        sources = paths_from_queue(default_queue)

    if not sources:
        log_step("No files found to transcode.")
        return 1

    for source in sources:
        if not source.is_file() and not (
            args.resume_local and local_source_for(source, work_dir).is_file()
        ):
            log_step(f"Skipping unavailable source before notification: {source}")
            continue
        operation = "Movie deployment" if args.replace_completed else "Movie transcode"
        details = (
            f"Movie: `{source.stem}`\n\n"
            f"Source: {file_size(source)}; target: {args.target_gb:g} GB "
            f"{args.container.upper()} using {args.encoder}"
        )
        with WebexJob(f"{operation}: {source.stem}", details) as job:
            if args.replace_completed:
                job.phase("Archiving the original and deploying the completed encode.")
                replace_completed_one(
                    source,
                    work_dir=work_dir,
                    target_gb=args.target_gb,
                    encoder=args.encoder,
                    container=args.container,
                    movie_root=Path(args.movie_root),
                    completed_dir=Path(args.completed_dir),
                    library_csv=Path(args.library_csv),
                    library_html=Path(args.library_html),
                    html_generator=Path(args.html_generator),
                    overwrite=args.overwrite,
                    dry_run=args.dry_run,
                )
            else:
                job.phase("Copying locally, then running HandBrake.")
                transcode_one(
                    source,
                    work_dir=work_dir,
                    target_gb=args.target_gb,
                    audio_kbps=args.audio_kbps,
                    encoder=args.encoder,
                    container=args.container,
                    preset=args.preset,
                    overwrite=args.overwrite,
                    dry_run=args.dry_run,
                    resume_local=args.resume_local,
                )
                job.phase("Encode completed and validated.")

    log_step("Finished handbrake worker")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        log_step(f"Command failed with exit code {exc.returncode}: {' '.join(str(part) for part in exc.cmd)}")
        raise
    except Exception as exc:
        log_step(f"Worker failed: {exc}")
        raise
