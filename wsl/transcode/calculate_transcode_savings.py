#!/usr/bin/env python3
import re
import csv
from pathlib import Path


LOGS = {
    "movies": [
        Path("/mnt/c/DATA/combined-transcode-movies-h265.log"),
        Path("/mnt/c/DATA/handbrake-movies-h265-1gb.log"),
        Path("/mnt/c/DATA/handbrake-movies-1.5gb.log"),
    ],
    "tv": [
        Path("/mnt/c/DATA/combined-transcode-tv-h265.log"),
        Path("/mnt/c/DATA/handbrake-once-upon-a-time-h265-700mb.log"),
        Path("/mnt/c/DATA/handbrake-tvshow-convert.log"),
    ],
    "copa": [
        Path("/mnt/c/DATA/combined-transcode-orchestrator-20260708.out"),
        Path("/mnt/c/DATA/combined-transcode-orchestrator-20260709.out"),
        Path("/mnt/c/DATA/PIP/copa-mundial-handbrake.log"),
    ],
}

SIZE_CSVS = [
    Path("/mnt/c/DATA/handbrake-movies-1.5gb-queue.csv"),
    Path("/mnt/c/DATA/handbrake-movies-1.5gb-4k-queue.csv"),
    Path("/mnt/c/DATA/handbrake-once-upon-a-time-h265-700mb-queue.csv"),
    Path("/mnt/c/DATA/tv-size-audit/tv-episode-sizes-20260707-071007.csv"),
]


MOVE_ORIGINAL = re.compile(r"Moving original to completed archive: (.*?) -> ")
COPA_ARCHIVE = re.compile(r"Archiving original TS: (.*?) -> ")
MOVE_ENCODE = re.compile(r"Moving completed encode back to original folder: (.*?) -> (.*)")
COPA_COPY = re.compile(r"Copying completed MP4 to NFS: (.*?) -> (.*?)(?:\.partial)?$")


def human(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def path_size(path_text: str) -> int | None:
    path = Path(path_text.strip())
    try:
        return path.stat().st_size
    except OSError:
        return None


def load_known_sizes() -> dict[str, int]:
    sizes: dict[str, int] = {}
    for csv_path in SIZE_CSVS:
        if not csv_path.is_file():
            continue
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                value = row.get("file_path") or row.get("full_path") or row.get("path")
                size = row.get("size_bytes")
                if not value or not size:
                    continue
                try:
                    sizes[value] = int(size)
                except ValueError:
                    continue
    return sizes


def parse_worker_logs(paths: list[Path], known_sizes: dict[str, int]):
    rows = []
    pending_original: tuple[str, int | None] | None = None
    for log in paths:
        if not log.is_file():
            continue
        with log.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                original_match = MOVE_ORIGINAL.search(line)
                if original_match:
                    original = original_match.group(1)
                    pending_original = (original, path_size(original) or known_sizes.get(original))
                    continue
                encode_match = MOVE_ENCODE.search(line)
                if encode_match and pending_original:
                    encoded = encode_match.group(1)
                    final = encode_match.group(2)
                    original, original_size = pending_original
                    replacement_size = path_size(final) or path_size(encoded)
                    if original_size and replacement_size:
                        rows.append((original, final, original_size, replacement_size))
                    pending_original = None
    return rows


def parse_copa_logs(paths: list[Path], known_sizes: dict[str, int]):
    rows = []
    pending_final: tuple[str, int | None] | None = None
    for log in paths:
        if not log.is_file():
            continue
        with log.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                copy_match = COPA_COPY.search(line)
                if copy_match:
                    encoded = copy_match.group(1)
                    final = copy_match.group(2).removesuffix(".partial")
                    pending_final = (final, path_size(final) or path_size(encoded))
                    continue
                archive_match = COPA_ARCHIVE.search(line)
                if archive_match and pending_final:
                    original = archive_match.group(1)
                    original_size = path_size(original) or known_sizes.get(original)
                    final, replacement_size = pending_final
                    if original_size and replacement_size:
                        rows.append((original, final, original_size, replacement_size))
                    pending_final = None
    return rows


def dedupe(rows):
    by_final = {}
    for original, final, original_size, replacement_size in rows:
        by_final[final] = (original, final, original_size, replacement_size)
    return list(by_final.values())


def summarize(label: str, rows):
    rows = dedupe(rows)
    original_total = sum(row[2] for row in rows)
    replacement_total = sum(row[3] for row in rows)
    saved = original_total - replacement_total
    print(label)
    print(f"  files: {len(rows)}")
    print(f"  original total: {human(original_total)}")
    print(f"  replacement total: {human(replacement_total)}")
    print(f"  saved: {human(saved)}")
    print()
    return rows, saved


def main() -> int:
    known_sizes = load_known_sizes()
    movie_rows = parse_worker_logs(LOGS["movies"], known_sizes)
    tv_rows = parse_worker_logs(LOGS["tv"], known_sizes)
    copa_rows = parse_copa_logs(LOGS["copa"], known_sizes)

    _, movie_saved = summarize("Movies", movie_rows)
    _, tv_saved = summarize("TV shows", tv_rows)
    _, copa_saved = summarize("Copa", copa_rows)
    print("TV shows including Copa")
    print(f"  saved: {human(tv_saved + copa_saved)}")
    print()
    print("Grand total")
    print(f"  saved: {human(movie_saved + tv_saved + copa_saved)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
