#!/usr/bin/env python3
"""Conservatively remove stale, unreferenced Radarr/Sonarr download remnants."""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ALLOWED_ROOTS = (
    Path("/home/jnicolas/downloads/radarr"),
    Path("/home/jnicolas/downloads/tv-sonarr"),
)
QBITTORRENT_STATE = Path("/home/jnicolas/config/qbittorrent/qBittorrent/BT_backup")
MINIMUM_AGE_SECONDS = 60 * 60
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm"}
ARR_DATABASES = (
    Path("/home/jnicolas/config/radarr/radarr.db"),
    Path("/home/jnicolas/config/sonarr/sonarr.db"),
)


def newest_mtime(path: Path) -> float:
    newest = path.lstat().st_mtime
    if path.is_dir():
        for base, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                child = Path(base) / name
                try:
                    newest = max(newest, child.lstat().st_mtime)
                except FileNotFoundError:
                    pass
    return newest


def torrent_records():
    return [path.read_bytes() for path in QBITTORRENT_STATE.glob("*.fastresume")]


def successful_imports():
    imports = {}
    for database in ARR_DATABASES:
        db = sqlite3.connect(database)
        for (raw,) in db.execute("SELECT Data FROM History WHERE EventType=3"):
            try:
                data = json.loads(raw or "{}")
            except json.JSONDecodeError:
                continue
            dropped, imported = data.get("droppedPath"), data.get("importedPath")
            if dropped and imported:
                dropped_path = Path(dropped)
                if str(dropped_path).startswith("/downloads/"):
                    dropped_path = Path("/home/jnicolas/downloads") / dropped_path.relative_to("/downloads")
                imports[dropped_path] = Path(imported)
    return imports


def imported_host_path(path: Path) -> Path:
    value = str(path)
    mappings = (("/movies/", "/mnt/nfs-share-movies/"), ("/tv/", "/mnt/nfs-share-tvshows/"))
    for source, target in mappings:
        if value.startswith(source):
            return Path(target + value[len(source):])
    return path


def verified_successful_import(path: Path, imports) -> bool:
    videos = [path] if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS else []
    if path.is_dir():
        videos = [child for child in path.rglob("*") if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS]
    if videos:
        for video in videos:
            imported = imports.get(video)
            if not imported or not imported_host_path(imported).is_file():
                return False
        return True
    for dropped, imported in imports.items():
        try:
            dropped.relative_to(path)
        except ValueError:
            continue
        if imported_host_path(imported).is_file():
            return True
    return False


def referenced_by_qbittorrent(path: Path, records) -> bool:
    candidates = {
        os.fsencode(path.name),
        os.fsencode(str(path)),
        os.fsencode(str(path.parent)),
    }
    return any(value and value in record for record in records for value in candidates)


def size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for base, _, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += (Path(base) / name).stat().st_size
            except FileNotFoundError:
                pass
    return total


def emit(action, path, **extra):
    print(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "action": action,
                      "path": str(path), **extra}, sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Perform deletions; default is dry-run")
    args = parser.parse_args()

    resolved_roots = []
    for root in ALLOWED_ROOTS:
        if root.is_symlink() or not root.is_dir():
            raise SystemExit(f"guard failed: allowed root is missing, not a directory, or a symlink: {root}")
        resolved = root.resolve(strict=True)
        expected = Path("/home/jnicolas/downloads") / root.name
        if resolved != expected:
            raise SystemExit(f"guard failed: unexpected resolved root: {resolved}")
        resolved_roots.append(resolved)
    if not QBITTORRENT_STATE.is_dir():
        raise SystemExit("guard failed: qBittorrent state directory is unavailable")

    records = torrent_records()
    imports = successful_imports()
    now = time.time()
    candidates = candidate_bytes = removed = reclaimed = 0
    for root in resolved_roots:
        for entry in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if entry.is_symlink():
                emit("skip_symlink", entry); continue
            resolved = entry.resolve(strict=True)
            if resolved.parent != root:
                emit("skip_containment_guard", entry); continue
            age = now - newest_mtime(entry)
            if age <= MINIMUM_AGE_SECONDS:
                emit("skip_recent", entry, age_minutes=round(age / 60, 1)); continue
            if referenced_by_qbittorrent(entry, records):
                emit("skip_qbittorrent_reference", entry, age_minutes=round(age / 60, 1)); continue
            if not verified_successful_import(entry, imports):
                emit("skip_no_verified_import", entry, age_minutes=round(age / 60, 1)); continue
            candidates += 1
            amount = size_bytes(entry)
            candidate_bytes += amount
            if not args.apply:
                emit("would_remove", entry, age_minutes=round(age / 60, 1), bytes=amount)
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed += 1; reclaimed += amount
            emit("removed", entry, age_minutes=round(age / 60, 1), bytes=amount)
    emit("summary", Path("/home/jnicolas/downloads"), mode="apply" if args.apply else "dry-run",
         qbit_records=len(records), candidates=candidates, candidate_bytes=candidate_bytes,
         removed=removed, reclaimed_bytes=reclaimed)


if __name__ == "__main__":
    main()
