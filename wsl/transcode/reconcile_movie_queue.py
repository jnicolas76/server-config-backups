#!/usr/bin/env python3
"""Reconcile the .232 movie queue with CineVault's live index."""
from __future__ import annotations

import csv
import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(os.environ.get("TRANSCODE_BASE", "/home/jnicolas/transcode"))
QUEUE = BASE / "orchestrator-movies-h265-queue.csv"
INDEX = BASE / "cinevault/home/media-download-library/movie-live-index.json"
MOVIE_ROOT = Path(os.environ.get("MOVIE_ROOT", "/mnt/movies/Movies"))
MAX_QUEUE = int(os.environ.get("MOVIE_QUEUE_MAX", "1000"))
RECENT_INDEX_LIMIT = int(os.environ.get("MOVIE_INDEX_RECENT_LIMIT", "250"))
MOVIE_MIN_GIB = float(os.environ.get("MOVIE_QUEUE_MIN_GIB", "3.0"))
MOVIE_2160_MIN_GIB = float(os.environ.get("MOVIE_QUEUE_2160_MIN_GIB", "4.0"))
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".m4v", ".avi", ".mov", ".mpeg", ".mpg", ".m2ts", ".ts", ".webm"}

sys.path.insert(0, str(BASE))
import combined_transcode_orchestrator as orchestrator  # noqa: E402


def indexed_path(item: dict) -> Path:
    relative = str(item.get("rel_path") or "").strip()
    if relative:
        return MOVIE_ROOT / relative
    raw = str(item.get("path") or "")
    marker = "/Movies/"
    return MOVIE_ROOT / raw.split(marker, 1)[1] if marker in raw else Path(raw)


def new_candidate(item: dict, path: Path) -> bool:
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return False
    size_gib = int(item.get("size") or 0) / (1024 ** 3)
    lower = str(path).casefold()
    if path.suffix.lower() == ".avi":
        return True
    if "2160" in lower or "4k" in lower:
        return size_gib > MOVIE_2160_MIN_GIB
    return size_gib > MOVIE_MIN_GIB


def load_new_candidates() -> list[Path]:
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    items = sorted(data.get("movies") or [], key=lambda item: float(item.get("modified") or 0), reverse=True)[:RECENT_INDEX_LIMIT]
    done = orchestrator.completed_keys()
    quarantined = orchestrator.quarantined_keys()
    candidates: list[Path] = []
    for item in items:
        path = indexed_path(item)
        if not new_candidate(item, path):
            continue
        canonical = orchestrator.canonical_media_path("movie", path)
        if canonical in done or canonical in quarantined:
            continue
        if not path.is_file() or orchestrator.source_done(path):
            continue
        candidates.append(path)
    return candidates


def write_queue(paths: list[Path]) -> None:
    temporary = QUEUE.with_suffix(QUEUE.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file_path"])
        writer.writeheader()
        for path in paths:
            writer.writerow({"file_path": str(path)})
        handle.flush(); os.fsync(handle.fileno())
    temporary.replace(QUEUE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not INDEX.is_file() or not QUEUE.is_file():
        raise SystemExit(f"Required file missing: index={INDEX.is_file()} queue={QUEUE.is_file()}")
    existing_rows = orchestrator.queue_paths(QUEUE)
    existing_eligible = orchestrator.eligible_movie_paths(1.0, [QUEUE])
    additions = load_new_candidates()
    merged: list[Path] = []
    seen: set[str] = set()
    for path in existing_eligible + additions:
        key = os.path.normcase(os.path.normpath(str(path)))
        if key in seen:
            continue
        seen.add(key); merged.append(path)
        if len(merged) >= MAX_QUEUE:
            break
    changed = [str(path) for path in existing_rows] != [str(path) for path in merged]
    if changed and not args.dry_run:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(QUEUE, QUEUE.with_suffix(QUEUE.suffix + f".bak-reconcile-{stamp}"))
        write_queue(merged)
    added = [path for path in merged if path not in existing_eligible]
    print(json.dumps({
        "changed": changed, "dry_run": args.dry_run, "raw_before": len(existing_rows),
        "eligible_preserved": len(existing_eligible), "new_added": len(added),
        "queue_after": len(merged), "next": [path.name for path in merged[:10]],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
