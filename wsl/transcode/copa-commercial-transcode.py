#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from webex_job_notifications import WebexJob, file_size

DEFAULT_SOURCE = Path(
    "/mnt/nfs-share-tvshows/TV Shows/"
    "Copa Mundial de la FIFA 2026 (2026)/Season 2026"
)
DEFAULT_TV_ROOT = Path("/mnt/nfs-share-tvshows")
DEFAULT_COMPLETED = Path("/mnt/nfs-share-tvshows/COMPLETED")
DEFAULT_WORK = Path("/mnt/c/DATA/HANDBRAKE-TV")
REMOVER = Path("/mnt/c/DATA/remove-commercials.py")


def run(command):
    print("RUN", " ".join(str(item) for item in command), flush=True)
    subprocess.run([str(item) for item in command], check=True)


def duration(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return float(result.stdout.strip())


def archive_path(source, tv_root, completed):
    relative = source.relative_to(tv_root)
    destination = completed / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = destination.with_name(
            f"{destination.stem}.{stamp}{destination.suffix}"
        )
    return destination


def deploy(source, local_source, encoded, tv_root, completed):
    final = source.with_suffix(".mp4")
    partial = final.with_suffix(".mp4.partial")
    if final.exists():
        raise RuntimeError(f"Destination already exists: {final}")

    partial.unlink(missing_ok=True)
    print(f"Copying completed MP4 to NFS: {encoded} -> {partial}", flush=True)
    shutil.copy2(encoded, partial)
    if partial.stat().st_size != encoded.stat().st_size:
        raise RuntimeError("NFS copy size does not match the completed encode")

    partial.replace(final)
    destination = archive_path(source, tv_root, completed)
    print(f"Archiving original TS: {source} -> {destination}", flush=True)
    shutil.move(str(source), str(destination))

    encoded.unlink()
    local_source.unlink(missing_ok=True)
    print(f"Finished: {final}", flush=True)
    return final


def process(source, args, job=None):
    local_source = args.work_dir / source.name
    encoded = args.work_dir / f"{source.stem}.commercial-free.target-{args.target_gb:g}GB.mp4"
    args.work_dir.mkdir(parents=True, exist_ok=True)

    if not local_source.exists():
        if job:
            job.phase("Copying the source TS from the TV share.")
        print(f"Copying TS locally: {source} -> {local_source}", flush=True)
        shutil.copy2(source, local_source)

    if not encoded.exists():
        if job:
            job.phase(
                "Detecting/removing commercials and encoding the final MP4.",
                notify_now=True,
            )
        run([
            sys.executable, REMOVER, "auto", local_source,
            "--output", encoded,
            "--work-dir", args.commercial_work,
            "--target-gb", args.target_gb,
            "--audio-kbps", args.audio_kbps,
            "--preset", args.preset,
            "--allow-no-commercials",
        ])

    if job:
        job.phase("Validating output duration and target size.")
    source_duration = duration(local_source)
    output_duration = duration(encoded)
    if output_duration <= 0 or output_duration > source_duration + 2:
        raise RuntimeError(
            f"Invalid output duration: source={source_duration:.1f}s, "
            f"output={output_duration:.1f}s"
        )
    minimum = args.target_gb * (1024 ** 3) * 0.80
    maximum = args.target_gb * (1024 ** 3) * 1.10
    if not minimum <= encoded.stat().st_size <= maximum:
        raise RuntimeError(
            f"Output size {encoded.stat().st_size / 1024 ** 3:.2f} GiB "
            f"is outside the expected target range"
        )
    if job:
        job.phase("Deploying the MP4 and archiving the original TS.", notify_now=True)
    return deploy(
        source, local_source, encoded, args.tv_root, args.completed_dir
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Remove commercials and transcode queued Copa TS files."
    )
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--tv-root", type=Path, default=DEFAULT_TV_ROOT)
    parser.add_argument("--completed-dir", type=Path, default=DEFAULT_COMPLETED)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    parser.add_argument(
        "--commercial-work",
        type=Path,
        default=Path("/mnt/c/DATA/COMMERCIAL-WORK"),
    )
    parser.add_argument("--target-gb", type=float, default=1.5)
    parser.add_argument("--audio-kbps", type=int, default=96)
    parser.add_argument("--preset", default="slow")
    return parser.parse_args()


def main():
    args = parse_args()
    sources = args.files or sorted(
        args.source_dir.glob("*.ts"), key=lambda item: item.name.lower()
    )
    print(f"Queued {len(sources)} Copa TS file(s).", flush=True)
    for number, source in enumerate(sources, 1):
        print(f"\n=== Copa commercial job {number}/{len(sources)}: {source.name} ===")
        details = (
            f"Game: `{source.stem}`\n\n"
            f"Queue position: {number} of {len(sources)}\n\n"
            f"Source: {file_size(source)}; target: {args.target_gb:g} GB MP4"
        )
        with WebexJob(f"Copa transcode: {source.stem}", details) as job:
            final = process(source.resolve(), args, job)
            job.phase(f"Finished and deployed: {final}")
    print(f"\nCompleted {len(sources)} Copa commercial job(s).")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
