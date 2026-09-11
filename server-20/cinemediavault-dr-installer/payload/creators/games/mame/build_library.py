#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, re, zipfile
from pathlib import Path
from urllib.parse import quote

ROM_EXTENSIONS = {'.zip', '.7z'}
SOURCE_EXTENSIONS = ROM_EXTENSIONS

def clean_title(value: str) -> str:
    value = value.replace('_', ' ').strip()
    value = re.sub(r'\s*\[!\]\s*', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()

def region_for(title: str) -> str:
    for pattern, region in ((r'\((?:U|USA)\)', 'USA'), (r'\((?:E|Europe)\)', 'Europe'), (r'\((?:J|Japan)\)', 'Japan'), (r'\((?:W|World)\)', 'World')):
        if re.search(pattern, title, re.IGNORECASE): return region
    return 'Other'

def digest_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def has_supported_member(path: Path) -> bool:
    if path.suffix.lower() != '.zip': return False
    try:
        with zipfile.ZipFile(path) as z:
            return any((not m.is_dir()) and Path(m.filename).suffix.lower() in ROM_EXTENSIONS - {'.zip', '.7z'} for m in z.infolist())
    except Exception:
        return False

def build(source: Path, rom_dir: Path, catalog_path: Path) -> None:
    rom_dir.mkdir(parents=True, exist_ok=True)
    games = []
    for folder in (source, rom_dir):
        if not folder.is_dir(): continue
        for path in sorted(folder.iterdir()):
            if not path.is_file(): continue
            suffix = path.suffix.lower()
            if suffix not in SOURCE_EXTENSIONS: continue
            if suffix == '.zip' and not has_supported_member(path) and folder == source: pass
            game_id = digest_file(path)
            rel = f'roms/{quote(path.name)}' if folder == rom_dir else None
            if folder == source:
                target = rom_dir / path.name
                if not target.exists() or target.stat().st_size != path.stat().st_size:
                    target.write_bytes(path.read_bytes())
                rel = f'roms/{quote(target.name)}'
            games.append({'id': game_id, 'title': clean_title(path.stem), 'region': region_for(path.stem), 'rom': rel, 'source': path.name})
    seen = {}
    for game in games: seen.setdefault(game['id'], game)
    catalog = sorted(seen.values(), key=lambda g: g['title'].lower())
    catalog_path.write_text(json.dumps(catalog, indent=2), encoding='utf-8')
    print(f'Cataloged {len(catalog)} Arcade MAME games')

def main() -> int:
    root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, default=root / 'source')
    p.add_argument('--rom-dir', type=Path, default=root / 'roms')
    p.add_argument('--catalog', type=Path, default=root / 'catalog.json')
    a = p.parse_args()
    build(a.source.resolve(), a.rom_dir.resolve(), a.catalog.resolve())
    return 0
if __name__ == '__main__': raise SystemExit(main())