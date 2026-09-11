#!/usr/bin/env python3
"""Extract NES ROMs from ZIP files and build the browser catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path


def default_source() -> Path:
    return Path(__file__).resolve().parents[1] / "source"


def title_score(title: str) -> tuple[int, int, str]:
    lower = title.lower()
    score = 100 if "[!]" in title else 0
    score += 20 if "(u)" in lower or "(usa)" in lower else 0
    score -= 80 if re.search(r"\[(?:b|p|o|h|t)\d*\]", lower) else 0
    return score, -len(title), title.lower()


def region_for(title: str) -> str:
    tests = (
        (r"\((?:U|USA)\)", "USA"), (r"\((?:E|Europe)\)", "Europe"),
        (r"\((?:J|Japan)\)", "Japan"), (r"\((?:W|World)\)", "World"),
    )
    for pattern, region in tests:
        if re.search(pattern, title, re.IGNORECASE):
            return region
    return "Other"


def clean_title(value: str) -> str:
    value = value.replace("_", " ").strip()
    value = re.sub(r"\s*\[!\]\s*", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def roms_from_zip(path: Path):
    try:
        with zipfile.ZipFile(path) as archive:
            members = [item for item in archive.infolist()
                       if not item.is_dir() and item.filename.lower().endswith(".nes")]
            for member in members:
                try:
                    yield Path(member.filename).stem, archive.read(member)
                except (NotImplementedError, RuntimeError, OSError):
                    continue
    except (zipfile.BadZipFile, OSError):
        return


def build(source: Path, rom_dir: Path, catalog_path: Path) -> None:
    source = source.resolve()
    rom_dir.mkdir(parents=True, exist_ok=True)
    games: dict[str, dict[str, str]] = {}
    invalid = 0
    for path in sorted(source.iterdir()):
        candidates = []
        if path.is_file() and path.suffix.lower() == ".zip":
            candidates = list(roms_from_zip(path))
        elif path.is_file() and path.suffix.lower() == ".nes":
            candidates = [(path.stem, path.read_bytes())]
        for member_title, data in candidates:
            if len(data) < 16 or data[:4] != b"NES\x1a":
                invalid += 1
                continue
            digest = hashlib.sha1(data).hexdigest()
            title = clean_title(member_title or path.stem)
            existing = games.get(digest)
            if existing is None:
                output = rom_dir / f"{digest}.nes"
                if not output.is_file() or output.stat().st_size != len(data):
                    output.write_bytes(data)
                games[digest] = {
                    "id": digest,
                    "title": title,
                    "region": region_for(title),
                    "rom": f"roms/{digest}.nes",
                    "source": path.name,
                }
            elif title_score(title) > title_score(existing["title"]):
                existing.update(title=title, region=region_for(title), source=path.name)

    live_files = {f"{digest}.nes" for digest in games}
    for old_file in rom_dir.glob("*.nes"):
        if old_file.name not in live_files:
            old_file.unlink()

    catalog = sorted(games.values(), key=lambda game: game["title"].lower())
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    total_size = sum((rom_dir / f"{game['id']}.nes").stat().st_size for game in catalog)
    print(f"Cataloged {len(catalog)} unique NES games ({total_size / 1048576:.1f} MB); skipped {invalid} invalid ROMs")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=default_source())
    parser.add_argument("--rom-dir", type=Path, default=root / "roms")
    parser.add_argument("--catalog", type=Path, default=root / "catalog.json")
    args = parser.parse_args()
    if not args.source.is_dir() or not any(
        path.suffix.lower() in {".nes", ".zip"} for path in args.source.iterdir() if path.is_file()
    ):
        raise SystemExit(f"Place .nes or .zip files in the source folder first: {args.source}")
    build(args.source, args.rom_dir, args.catalog)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
