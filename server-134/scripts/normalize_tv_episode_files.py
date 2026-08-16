#!/usr/bin/env python3
import argparse
import csv
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path("/home/jnicolas/Data2/TV Shows")
ALLOWED_ROOTS = {
    ROOT,
    Path("/home/jnicolas/Data10/TV_Shows"),
}
SHOW_FOLDER_RE = re.compile(r"^.+ \((?:18|19|20)\d{2}\)$")
EPISODE_RE = re.compile(
    r"(?i)(?<![a-z0-9])s(\d{1,2})(?:ep|e)(\d{1,3})(?!\d)"
)
MEDIA_EXTENSIONS = {
    ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg",
    ".ts", ".webm", ".wmv",
}
SUBTITLE_EXTENSIONS = {".ass", ".srt", ".ssa", ".sub", ".vtt"}
TECHNICAL_RE = re.compile(
    r"(?i)(?:^|[\s(\[])(?:"
    r"\d{3,4}p|4k|web(?:rip|-?dl)|blu-?ray|brrip|remux|"
    r"h[._-]?26[45]|x26[45]|hevc|av1|hdr10?|dolby[ ._-]?vision|"
    r"aac|ddp?|dts(?:-?hd)?|amzn|nf|proper|repack|multi"
    r")(?:$|[\s)\]._-])"
)
TAGGED_GROUP_RE = re.compile(
    r"[\[(][^)\]]*(?:\d{3,4}p|4k|web(?:rip|-?dl)|blu-?ray|"
    r"h[._-]?26[45]|x26[45]|hevc|av1|hdr10?|ddp?|dts|"
    r"yts|tgx|galaxytv|bitsearch)[^)\]]*[\])]",
    re.IGNORECASE,
)
LEADING_DOWNLOAD_RE = re.compile(
    r"^\[(?:bitsearch(?:\.to)?|yts(?:\.[^\]]+)?|tgx)\]\s*",
    re.IGNORECASE,
)


def clean_words(value):
    value = LEADING_DOWNLOAD_RE.sub("", value.strip())
    value = value.replace("_", " ")
    if "." in value and " " not in value:
        value = value.replace(".", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ._-")


def clean_optional_info(value):
    value = value.replace("_", " ")
    if "." in value and " " not in value:
        value = value.replace(".", " ")
    value = TAGGED_GROUP_RE.sub(" ", value)
    technical = TECHNICAL_RE.search(value)
    if technical:
        value = value[:technical.start()]
    value = re.sub(
        r"\s*[-–—]\s*(?:galaxytv|yts|tgx|rarbg|bitsearch)"
        r"(?:\[[^\]]+\])?.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ._-")


def proposed_name(path, show_name):
    extension = path.suffix.lower()
    match = EPISODE_RE.search(path.stem)
    if not match:
        return None, "no recognizable SxxExx/SxxEPxx code"
    if EPISODE_RE.search(path.stem, match.end()):
        return None, "multiple episode codes"

    prefix = clean_words(path.stem[:match.start()])
    if SHOW_FOLDER_RE.fullmatch(show_name):
        prefix = show_name
    elif not prefix:
        prefix = clean_words(show_name)
    if not prefix:
        return None, "show name could not be derived"

    season = int(match.group(1))
    episode = int(match.group(2))
    if season > 99 or episode > 999:
        return None, "episode code is outside supported range"
    code = f"S{season:02d}EP{episode:02d}"
    optional = clean_optional_info(path.stem[match.end():])
    stem = f"{prefix} - {code}"
    if optional:
        stem += f" - {optional}"
    target_name = f"{stem}{extension}"
    if len(target_name.encode("utf-8")) > 250:
        return None, "normalized filename would exceed 250 bytes"
    return target_name, ""


def inventory(root):
    for current, directories, files in os.walk(root):
        directories.sort(key=str.casefold)
        files.sort(key=str.casefold)
        current_path = Path(current)
        relative = current_path.relative_to(root)
        show_name = relative.parts[0] if relative.parts else ""
        for filename in files:
            path = current_path / filename
            if path.suffix.lower() in MEDIA_EXTENSIONS | SUBTITLE_EXTENSIONS:
                yield path, show_name


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Normalize TV episode filenames without changing folders."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path("/home/jnicolas/tv-episode-filename-plan-20260703.csv"),
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("/home/jnicolas/tv-episode-filename-changes-20260703.csv"),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--delay", type=float, default=0.10)
    parser.add_argument("--scan-delay", type=float, default=0.002)
    parser.add_argument("--progress-every", type=int, default=250)
    args = parser.parse_args()

    root = args.root.resolve()
    allowed_roots = {
        path.resolve() for path in ALLOWED_ROOTS if path.exists()
    }
    if root not in allowed_roots or not root.is_dir():
        raise SystemExit(f"Unexpected or unavailable TV root: {root}")

    rows = []
    destinations = Counter()
    for path, show_name in inventory(root):
        target_name, reason = proposed_name(path, show_name)
        if target_name is None:
            rows.append({
                "status": "skipped",
                "old_path": str(path),
                "new_path": "",
                "reason": reason,
            })
        elif target_name == path.name:
            rows.append({
                "status": "already_clean",
                "old_path": str(path),
                "new_path": str(path),
                "reason": "",
            })
        else:
            target = path.with_name(target_name)
            destinations[str(target)] += 1
            rows.append({
                "status": "planned",
                "old_path": str(path),
                "new_path": str(target),
                "reason": "",
            })
        if args.scan_delay:
            time.sleep(args.scan_delay)

    for row in rows:
        if row["status"] != "planned":
            continue
        source = Path(row["old_path"])
        target = Path(row["new_path"])
        if destinations[str(target)] > 1:
            row["status"] = "skipped"
            row["reason"] = "multiple files resolve to this destination"
        elif target.exists() and target != source:
            row["status"] = "skipped"
            row["reason"] = "destination already exists"

    fields = ["status", "old_path", "new_path", "reason"]
    write_csv(args.plan, rows, fields)

    if args.apply:
        log_fields = [
            "timestamp", "status", "old_path", "new_path", "message"
        ]
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=log_fields)
            writer.writeheader()
            changed = 0
            for row in rows:
                if row["status"] != "planned":
                    continue
                source = Path(row["old_path"])
                target = Path(row["new_path"])
                status = "renamed"
                message = ""
                try:
                    if not source.is_file():
                        raise FileNotFoundError("source no longer exists")
                    if target.exists():
                        raise FileExistsError("destination appeared")
                    source.rename(target)
                    changed += 1
                except Exception as error:
                    status = "failed"
                    message = str(error)
                writer.writerow({
                    "timestamp": datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                    "status": status,
                    "old_path": source,
                    "new_path": target,
                    "message": message,
                })
                handle.flush()
                if changed and changed % args.progress_every == 0:
                    print(f"Renamed {changed} files", flush=True)
                if args.delay:
                    time.sleep(args.delay)

    counts = Counter(row["status"] for row in rows)
    print(f"Plan: {args.plan}")
    if args.apply:
        print(f"Log: {args.log}")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")


if __name__ == "__main__":
    main()
