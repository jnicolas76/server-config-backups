#!/usr/bin/env python3
import importlib.util
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


COMICS = Path("/home/jnicolas/Data9/Comics")
LIBRARY = Path("/home/jnicolas/Data9/comic-library")
COLLECTIONS = LIBRARY / "collections"
IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
NOTIFIER = Path("/home/jnicolas/send_webex_notification.py")


def load_gallery_module():
    path = Path("/home/jnicolas/build_new_comic_collections.py")
    spec = importlib.util.spec_from_file_location("comic_gallery_base", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load_gallery_module()


def notify(message):
    if NOTIFIER.is_file():
        subprocess.run(
            [str(NOTIFIER), message],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def natural_key(value):
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
    ]


def safe_slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def clean_title(path):
    title = path.stem
    release_markers = (
        r"digital|c2c|noads?|edited|cover only|empire|zone|"
        r"minutemen|thegroup|fawkes|spyder|scandog|dcp|halo|novus"
    )
    title = re.sub(
        rf"\s+\([^)]*(?:{release_markers})[^)]*\)",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        rf"\s+\[[^\]]*(?:{release_markers})[^\]]*\]",
        "",
        title,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", title).strip(" .-_")


def extract_archive(source, destination):
    temporary = destination.with_name(destination.name + ".partial")
    if temporary.exists():
        shutil.rmtree(temporary)
    raw = temporary / "raw"
    raw.mkdir(parents=True)
    if source.suffix.casefold() == ".cbz":
        with zipfile.ZipFile(source) as archive:
            archive.extractall(raw)
    else:
        result = subprocess.run(
            ["unrar", "x", "-o+", "-idq", str(source), f"{raw}/"],
            check=False,
        )
        if result.returncode and not any(raw.rglob("*")):
            raise RuntimeError(
                f"unrar exit {result.returncode} with no recovered files: {source}"
            )
    images = sorted(
        (
            path for path in raw.rglob("*")
            if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS
        ),
        key=lambda path: natural_key(str(path.relative_to(raw))),
    )
    if not images:
        raise RuntimeError(f"No readable image pages: {source}")
    pages = []
    for number, image in enumerate(images, 1):
        target = temporary / f"page-{number:04d}{image.suffix.casefold()}"
        image.rename(target)
        pages.append(target.name)
    shutil.rmtree(raw)
    temporary.rename(destination)
    return pages


def archives_under(path):
    return sorted(
        (
            item for item in path.rglob("*")
            if item.is_file() and item.suffix.casefold() in {".cbr", ".cbz"}
        ),
        key=lambda item: natural_key(str(item.relative_to(path))),
    )


def build_new_collection(slug, title, sources, colors):
    target = COLLECTIONS / slug
    if target.exists():
        print(f"Already published: {target}", flush=True)
        config = json.loads(
            (target / "gallery_config.json").read_text(encoding="utf-8")
        )
        return config
    staging = COLLECTIONS / f".{slug}-building"
    issues_root = staging / "issues"
    issues_root.mkdir(parents=True, exist_ok=True)
    manifest_path = staging / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists() else {}
    )
    issues = []
    for number, source in enumerate(sources, 1):
        key = str(source)
        title_text = clean_title(source)
        folder = f"{number:04d}-{safe_slug(title_text)}"
        destination = issues_root / folder
        if key in manifest and destination.is_dir():
            issue = manifest[key]
        else:
            print(f"{title}: {number}/{len(sources)} {title_text}", flush=True)
            pages = extract_archive(source, destination)
            issue = {
                "title": title_text,
                "folder": folder,
                "cover": pages[0],
                "pages": pages,
            }
            manifest[key] = issue
            manifest_path.write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
        issues.append(issue)
        if number % 25 == 0:
            notify(f"**Comic build status:** {title} {number}/{len(sources)} issues.")

    (staging / "index.html").write_text(
        BASE.gallery_html(title, issues, colors), encoding="utf-8"
    )
    config = {
        "title": title,
        "slug": slug,
        "primary": colors[0],
        "secondary": colors[1],
        "accent": colors[2],
    }
    (staging / "gallery_config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    manifest_path.unlink(missing_ok=True)
    staging.rename(target)
    return config


def append_walking_dead_extras():
    source_root = COMICS / (
        "The Walking Dead 001-193 (2003-2019) (Digital) (Zone-Empire)"
    )
    sources = archives_under(source_root / "Extras") + archives_under(
        source_root / "Variant Covers"
    )
    target = COLLECTIONS / "the-walking-dead"
    index_path = target / "index.html"
    document = index_path.read_text(encoding="utf-8")
    match = re.search(
        r"const issues=(\[.*?\])(?=,\s*library=|;\s*const\s+library=)",
        document,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError("Could not locate Walking Dead issue data")
    issues = json.loads(match.group(1))
    existing_titles = {issue["title"].casefold() for issue in issues}
    added = 0
    for source in sources:
        title = clean_title(source)
        if title.casefold() in existing_titles:
            continue
        number = len(issues) + 1
        folder = f"{number:04d}-{safe_slug(title)}"
        destination = target / "issues" / folder
        print(f"Walking Dead extra: {title}", flush=True)
        pages = extract_archive(source, destination)
        issues.append({
            "title": title,
            "folder": folder,
            "cover": pages[0],
            "pages": pages,
        })
        existing_titles.add(title.casefold())
        added += 1
    replacement = json.dumps(
        issues, ensure_ascii=True, separators=(",", ":")
    )
    document = (
        document[:match.start(1)] + replacement + document[match.end(1):]
    )
    temporary = index_path.with_suffix(".html.tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(index_path)
    return added, len(issues)


def main():
    notify(
        "**Comic build started:** Invincible, Marvel Encyclopedia, "
        "How to Draw Comics the Marvel Way, and Walking Dead extras."
    )
    built = []
    built.append(build_new_collection(
        "invincible",
        "Invincible",
        archives_under(COMICS / "Invincible (001 - 107 + extras)"),
        ("#f4c430", "#1b4f9c", "#e53935"),
    ))
    built.append(build_new_collection(
        "marvel-comics-encyclopedia",
        "The Marvel Comics Encyclopedia",
        archives_under(COMICS / "The Marvel Comics Encyclopedia"),
        ("#d71920", "#174a9c", "#ffd329"),
    ))
    built.append(build_new_collection(
        "how-to-draw-comics-the-marvel-way",
        "How to Draw Comics the Marvel Way",
        [COMICS / "How To Draw Comics The Marvel Way.cbr"],
        ("#c8102e", "#202020", "#f8d348"),
    ))
    added, walking_total = append_walking_dead_extras()
    BASE.update_hub(built)
    notify(
        f"**Comic build completed:** published Invincible, Marvel Encyclopedia, "
        f"and How to Draw Comics the Marvel Way; added {added} Walking Dead "
        f"extras ({walking_total} total entries)."
    )
    print(
        f"Completed: new_collections={len(built)} "
        f"walking_dead_added={added} walking_dead_total={walking_total}"
    )


if __name__ == "__main__":
    main()
