#!/usr/bin/env python3
"""Build browser-ready js-dos bundles and a catalog from a DOS game archive."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


DOSBOX_CONF = """[sdl]
autolock=false
fullscreen=false
output=surface

[dosbox]
machine=svga_s3
memsize=16

[cpu]
core=auto
cputype=auto
cycles=auto

[render]
frameskip=0
aspect=false
scaler=none

[sblaster]
sbtype=sb16
sbbase=220
irq=7
dma=1
hdma=5

[speaker]
pcspeaker=true
tandy=auto
disney=true

[joystick]
joysticktype=auto
timed=true

[dos]
xms=true
ems=true
umb=true
keyboardlayout=auto

[autoexec]
@echo off
mount c .
c:
{commands}
"""

LAUNCH_EXTENSIONS = {".exe", ".com", ".bat"}
BAD_NAMES = {
    "install", "setup", "config", "configure", "setsound", "sound", "uninstall",
    "command", "gwbasic", "basica", "moslo", "slow", "pkunzip", "unzip",
    "readme", "update", "patch", "copy", "mpscopy", "playscr",
}


def safe_name(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "game"


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def safe_member(name: str) -> Path | None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.name:
        return None
    return Path(*path.parts)


def extract_best_zip(title_dir: Path, destination: Path) -> str | None:
    archives = sorted(title_dir.glob("*.zip"), key=lambda path: path.stat().st_size, reverse=True)
    for archive in archives:
        archive_root = destination / "_archive"
        shutil.rmtree(archive_root, ignore_errors=True)
        archive_root.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive) as source:
                launchers = [entry for entry in source.infolist()
                             if Path(entry.filename).suffix.lower() in LAUNCH_EXTENSIONS]
                if not launchers:
                    continue
                for entry in source.infolist():
                    relative = safe_member(entry.filename)
                    if relative is None or entry.is_dir():
                        continue
                    target = archive_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(entry) as incoming, target.open("wb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
                return archive.name
        except (OSError, zipfile.BadZipFile, NotImplementedError):
            continue
    return None


def copy_loose_game(title_dir: Path, destination: Path) -> bool:
    launchers = [path for path in title_dir.rglob("*")
                 if path.is_file() and path.suffix.lower() in LAUNCH_EXTENSIONS]
    if not launchers:
        return False
    for source in title_dir.rglob("*"):
        if not source.is_file() or source.suffix.lower() in {".zip", ".rar", ".sfv", ".nfo"}:
            continue
        relative = source.relative_to(title_dir)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return True


def launcher_score(path: Path, title: str) -> tuple[int, int, str]:
    stem = normalized(path.stem)
    title_key = normalized(title)
    score = {"sierra": 1200, "runme": 1000, "start": 970, "play": 950,
             "game": 925, "go": 900, "autoexec": 850}.get(stem, 0)
    if stem in BAD_NAMES or any(word in stem for word in ("install", "setup", "uninst")):
        score -= 2000
    if stem == title_key or (len(stem) >= 3 and (stem in title_key or title_key in stem)):
        score += 700
    if path.suffix.lower() == ".bat":
        score += 100
    elif path.suffix.lower() == ".com":
        score += 60
    score -= len(path.parts) * 5
    return score, -len(path.name), path.as_posix().lower()


def find_launcher(source: Path, title: str) -> Path | None:
    candidates = [path.relative_to(source) for path in source.rglob("*")
                  if path.is_file() and path.suffix.lower() in LAUNCH_EXTENSIONS]
    usable = [path for path in candidates if launcher_score(path, title)[0] > -1000]
    return max(usable, key=lambda path: launcher_score(path, title), default=None)


def cleaned_sierra_config(path: Path, game_root: Path) -> bytes:
    lines = path.read_text(encoding="cp437", errors="replace").splitlines()
    cleaned = []
    for line in lines:
        match = re.match(r"\s*([A-Za-z]+)\s*=\s*(\S+)", line)
        if match:
            key, value = match.groups()
            if key.lower() in {"cd", "directory"}:
                continue
            if key.lower().endswith("drv") and value.lower().endswith(".drv"):
                if not (game_root / value).is_file():
                    continue
        cleaned.append(line)
    return ("\r\n".join(cleaned) + "\r\n").encode("cp437", errors="replace")


def build_bundle(source: Path, output: Path, title: str, launcher: Path) -> None:
    game_root = source / launcher.parent
    launch_name = launcher.name
    stem, extension = Path(launch_name).stem, Path(launch_name).suffix
    if len(stem) > 8 or len(extension) > 4 or " " in launch_name:
        launch_name = f"GAME{extension[:4]}"
    if launcher.stem.lower() == "sierra" and (game_root / "RESOURCE.CFG").is_file():
        commands = [f"{launch_name} RESOURCE.CFG"]
    else:
        commands = [launch_name]
    config = DOSBOX_CONF.format(commands="\n".join(commands))
    temporary = output.with_suffix(".jsdos.tmp")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        bundle.writestr(".jsdos/dosbox.conf", config)
        bundle.writestr(".jsdos/readme.txt", f"{title}\r\nLaunches {launcher.as_posix()}\r\n")
        bundle.writestr("dosbox.conf", "[autoexec]\r\n" + "\r\n".join(commands) + "\r\n")
        for path in sorted(game_root.rglob("*")):
            if path.is_file():
                relative = path.relative_to(game_root)
                archive_name = launch_name if relative == Path(launcher.name) else relative.as_posix()
                if launcher.stem.lower() == "sierra" and relative.name.upper() in {"RESOURCE.CFG", "DEFAULT.CFG"}:
                    bundle.writestr(archive_name, cleaned_sierra_config(path, game_root))
                else:
                    bundle.write(path, archive_name)
    temporary.replace(output)


def source_titles(source: Path):
    for group in sorted(path for path in source.iterdir() if path.is_dir()):
        if re.fullmatch(r"\d{4}", group.name):
            for title_dir in sorted(path for path in group.iterdir() if path.is_dir()):
                yield group.name, title_dir.name, title_dir
            continue
        has_dos_launcher = any(
            path.is_file() and path.suffix.lower() in LAUNCH_EXTENSIONS
            for path in group.rglob("*")
        )
        if has_dos_launcher:
            yield "Other", group.name.replace("_", " "), group


def build_library(source: Path, output: Path, catalog_path: Path) -> None:
    entries: list[dict[str, object]] = [{
        "id": "earl2025", "title": "Earl Weaver Baseball 2025", "year": "2025",
        "bundle": "games/earl2025.jsdos", "available": True, "launcher": "WEAVER.EXE F V",
    }]
    used_ids = {"earl2025"}
    built = skipped = 0
    for year, title, title_dir in source_titles(source):
        slug = safe_name(f"{year}-{title}")
        while slug in used_ids:
            slug += "-2"
        used_ids.add(slug)
        with tempfile.TemporaryDirectory(prefix="jsdos-") as temporary:
            work = Path(temporary)
            origin = "loose files" if copy_loose_game(title_dir, work) else extract_best_zip(title_dir, work)
            launcher = find_launcher(work, title) if origin else None
            if launcher:
                bundle_name = f"library/{slug}.jsdos"
                build_bundle(work, output / f"{slug}.jsdos", title, launcher)
                entries.append({"id": slug, "title": title, "year": year,
                                "bundle": f"games/{bundle_name}", "available": True,
                                "launcher": launcher.as_posix(), "source": origin})
                built += 1
            else:
                entries.append({"id": slug, "title": title, "year": year,
                                "available": False,
                                "reason": "RAR-only or no launchable EXE/COM/BAT"})
                skipped += 1
    catalog_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    print(f"Cataloged {len(entries)} titles: {built + 1} playable, {skipped} unavailable")


def main() -> int:
    web_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=web_root / "games" / "GAMES")
    parser.add_argument("--output", type=Path, default=web_root / "games" / "library")
    parser.add_argument("--catalog", type=Path, default=web_root / "games" / "catalog.json")
    args = parser.parse_args()
    build_library(args.source.resolve(), args.output.resolve(), args.catalog.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
