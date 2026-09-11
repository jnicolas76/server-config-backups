#!/usr/bin/env python3
"""Extract Sega Genesis ROMs and build the browser catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from urllib.parse import quote
import zipfile
from pathlib import Path


ROM_EXTENSIONS = {".bin", ".gen", ".md", ".smd"}
SOURCE_EXTENSIONS = ROM_EXTENSIONS | {".zip"}


def clean_title(value: str) -> str:
    value = value.replace("_", " ").strip()
    value = re.sub(r"\s*\[!\]\s*", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def region_for(title: str) -> str:
    for pattern, region in (
        (r"\((?:U|USA)\)", "USA"),
        (r"\((?:E|Europe)\)", "Europe"),
        (r"\((?:J|Japan)\)", "Japan"),
        (r"\((?:W|World)\)", "World"),
    ):
        if re.search(pattern, title, re.IGNORECASE):
            return region
    return "Other"


def title_score(title: str) -> tuple[int, int, str]:
    lower = title.lower()
    score = 100 if "[!]" in title else 0
    score += 20 if "(u)" in lower or "(usa)" in lower else 0
    score -= 80 if re.search(r"\[(?:b|p|o|h|t)\d*\]", lower) else 0
    return score, -len(title), title.lower()


def candidates(path: Path):
    if path.suffix.lower() in ROM_EXTENSIONS:
        yield path.stem, path.suffix.lower(), path.read_bytes()
        return
    if path.suffix.lower() != ".zip":
        return
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                suffix = Path(member.filename).suffix.lower()
                if not member.is_dir() and suffix in ROM_EXTENSIONS:
                    yield Path(member.filename).stem, suffix, archive.read(member)
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return


def file_digest(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_genesis_archive(path: Path) -> bool:
    if "32x" in path.stem.lower():
        return False
    if path.suffix.lower() in ROM_EXTENSIONS:
        return True
    try:
        with zipfile.ZipFile(path) as archive:
            return any(
                not member.is_dir() and Path(member.filename).suffix.lower() in ROM_EXTENSIONS
                for member in archive.infolist()
            )
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return False


def build_in_place(rom_dir: Path, catalog_path: Path) -> None:
    games = []
    skipped = 0
    for path in sorted(rom_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        if not is_genesis_archive(path):
            skipped += 1
            continue
        title = clean_title(path.stem)
        games.append({
            "id": file_digest(path),
            "title": title,
            "region": region_for(title),
            "rom": f"roms/{quote(path.name)}",
            "source": path.name,
        })
    games.sort(key=lambda game: game["title"].lower())
    catalog_path.write_text(json.dumps(games, indent=2), encoding="utf-8")
    total_size = sum((rom_dir / game["source"]).stat().st_size for game in games)
    print(
        f"Cataloged {len(games)} Genesis games in place "
        f"({total_size / 1048576:.1f} MB); skipped {skipped} non-Genesis archives"
    )
def build(source: Path, rom_dir: Path, catalog_path: Path) -> None:
    rom_dir.mkdir(parents=True, exist_ok=True)
    games: dict[str, dict[str, str]] = {}
    for path in sorted(source.iterdir()):
        if not path.is_file():
            continue
        for member_title, suffix, data in candidates(path):
            if not data:
                continue
            digest = hashlib.sha1(data).hexdigest()
            title = clean_title(member_title or path.stem)
            output_name = f"{digest}{suffix}"
            output = rom_dir / output_name
            if not output.is_file() or output.stat().st_size != len(data):
                output.write_bytes(data)
            game = {
                "id": digest,
                "title": title,
                "region": region_for(title),
                "rom": f"roms/{output_name}",
                "source": path.name,
            }
            if digest not in games or title_score(title) > title_score(games[digest]["title"]):
                games[digest] = game

    live_files = {Path(game["rom"]).name for game in games.values()}
    generated_name = re.compile(r"^[0-9a-f]{40}\.(?:bin|gen|md|smd)$")
    for old_file in rom_dir.iterdir():
        if old_file.is_file() and generated_name.match(old_file.name) and old_file.name not in live_files:
            old_file.unlink()

    catalog = sorted(games.values(), key=lambda game: game["title"].lower())
    catalog_path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    total_size = sum((rom_dir / Path(game["rom"]).name).stat().st_size for game in catalog)
    print(f"Cataloged {len(catalog)} Genesis games ({total_size / 1048576:.1f} MB)")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / "source")
    parser.add_argument("--rom-dir", type=Path, default=root / "roms")
    parser.add_argument("--catalog", type=Path, default=root / "catalog.json")
    args = parser.parse_args()
    if not args.source.is_dir():
        raise SystemExit(f"Source folder not found: {args.source}")
    source = args.source.resolve()
    rom_dir = args.rom_dir.resolve()
    catalog = args.catalog.resolve()
    has_source_games = any(
        path.is_file() and path.suffix.lower() in SOURCE_EXTENSIONS for path in source.iterdir()
    )
    has_in_place_games = rom_dir.is_dir() and any(
        path.is_file() and path.suffix.lower() in SOURCE_EXTENSIONS for path in rom_dir.iterdir()
    )
    if has_source_games:
        build(source, rom_dir, catalog)
    elif has_in_place_games:
        build_in_place(rom_dir, catalog)
    else:
        catalog.write_text("[]\n", encoding="utf-8")
        print("Cataloged 0 Genesis games (0.0 MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
