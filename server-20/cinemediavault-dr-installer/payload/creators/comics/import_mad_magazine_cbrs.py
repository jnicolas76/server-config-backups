#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path


MOVIES_ROOT = Path(
    os.environ.get("MOVIES_ROOT", "/home/jnicolas/Data9")
)
SOURCE = MOVIES_ROOT / "Comics" / "Mad Magazine"
COLLECTION = (
    MOVIES_ROOT / "comic-library" / "collections" / "mad-magazine"
)
STAGING = (
    MOVIES_ROOT / "comic-library" / "collections"
    / ".mad-magazine-cbr-staging"
)
MANIFEST = STAGING / "manifest.json"
IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}


def natural_key(value):
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
    ]


def safe_slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def issue_title(source):
    stem = source.stem
    match = re.search(
        r"(?i)\bmad[\s_.-]*(comics|magazine)[\s_.#-]*(\d+)",
        stem,
    )
    if match:
        series = "Mad Comics" if match.group(1).casefold() == "comics" else "Mad Magazine"
        return f"{series} {int(match.group(2)):03d}"
    cleaned = re.sub(r"[._]+", " ", stem)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    return cleaned


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def extract_archive(source, issue_dir):
    if issue_dir.exists():
        if issue_dir.parent != STAGING:
            raise RuntimeError(f"Unsafe partial directory: {issue_dir}")
        shutil.rmtree(issue_dir)
    raw = issue_dir / "raw"
    raw.mkdir(parents=True)
    result = subprocess.run(
        [
            "unrar", "x", "-o+", "-idq", str(source), f"{raw}/",
        ],
        check=False,
    )
    images = sorted(
        (
            path for path in raw.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=lambda path: natural_key(str(path.relative_to(raw))),
    )
    if not images:
        raise RuntimeError(
            f"No web-compatible image pages in {source} "
            f"(unrar exit {result.returncode})"
        )
    if result.returncode:
        print(
            f"Warning: unrar exit {result.returncode} for {source.name}; "
            f"using {len(images)} recovered pages",
            flush=True,
        )
    pages = []
    for number, image in enumerate(images, 1):
        extension = image.suffix.lower()
        target = issue_dir / f"page-{number:04d}{extension}"
        image.rename(target)
        pages.append(target.name)
    shutil.rmtree(raw)
    return pages


def read_existing_issues():
    index_path = COLLECTION / "index.html"
    document = index_path.read_text(encoding="utf-8")
    match = re.search(r"const issues=(\[.*?\]),library=", document, re.DOTALL)
    if not match:
        raise RuntimeError("Could not locate Mad Magazine issue data")
    return index_path, document, match, json.loads(match.group(1))


def main():
    if not SOURCE.is_dir() or not COLLECTION.is_dir():
        raise SystemExit("Mad Magazine source or collection is unavailable")
    archives = sorted(
        (
            path for path in SOURCE.iterdir()
            if path.is_file() and path.suffix.casefold() == ".cbr"
        ),
        key=lambda path: natural_key(path.name),
    )
    if not archives:
        raise SystemExit("No Mad Magazine CBR files found")

    STAGING.mkdir(parents=True, exist_ok=True)
    manifest = (
        json.loads(MANIFEST.read_text(encoding="utf-8"))
        if MANIFEST.exists() else {}
    )
    for number, archive in enumerate(archives, 2):
        key = archive.name
        title = issue_title(archive)
        folder = f"{number:04d}-{safe_slug(title)}"
        issue_dir = STAGING / folder
        if key in manifest and manifest[key] is None:
            print(
                f"Already marked unreadable {number - 1}/{len(archives)}: {title}",
                flush=True,
            )
            continue
        if key in manifest and (
            issue_dir.is_dir()
            or (COLLECTION / "issues" / folder).is_dir()
        ):
            print(f"Already staged {number - 1}/{len(archives)}: {title}", flush=True)
            continue
        print(f"Extracting {number - 1}/{len(archives)}: {title}", flush=True)
        try:
            pages = extract_archive(archive, issue_dir)
        except RuntimeError as error:
            print(f"Skipping unreadable archive: {error}", flush=True)
            manifest[key] = None
            atomic_json(MANIFEST, manifest)
            continue
        manifest[key] = {
            "title": title,
            "folder": folder,
            "cover": pages[0],
            "pages": pages,
        }
        atomic_json(MANIFEST, manifest)
        time.sleep(0.05)

    if len(manifest) != len(archives):
        raise RuntimeError(
            f"Manifest has {len(manifest)} entries for {len(archives)} archives"
        )

    index_path, document, match, existing = read_existing_issues()
    imported = [
        manifest[archive.name]
        for archive in archives
        if manifest[archive.name] is not None
    ]
    for issue in imported:
        source_dir = STAGING / issue["folder"]
        target_dir = COLLECTION / "issues" / issue["folder"]
        if target_dir.exists():
            continue
        if not source_dir.is_dir():
            raise RuntimeError(f"Staged issue missing: {source_dir}")
        source_dir.rename(target_dir)

    combined = existing[:1] + imported
    replacement = "const issues=" + json.dumps(
        combined, ensure_ascii=True, separators=(",", ":")
    ) + ",library="
    document = document[:match.start()] + replacement + document[match.end():]
    temporary = index_path.with_suffix(".html.tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(index_path)

    MANIFEST.unlink(missing_ok=True)
    STAGING.rmdir()
    print(
        f"Published Mad Magazine with {len(combined)} readable issues",
        flush=True,
    )


if __name__ == "__main__":
    main()
