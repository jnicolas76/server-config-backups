#!/usr/bin/env python3
from pathlib import Path
from PIL import Image, ImageChops, ImageStat

SOURCE_SCREENSHOTS = Path("/mnt/d/BRIDGE/Bridgette B - MegaPack/Bridgette B (screenshots)")
OUTPUT_DIR = Path("/mnt/c/DATA/bridgette-vault/posters")
GRID_OPTIONS = ((3, 3), (4, 4), (5, 5))
JPEG_QUALITY = 90


def cell_score(image: Image.Image) -> float:
    sample = image.convert("RGB").resize((80, 80))
    stat = ImageStat.Stat(sample)
    mean = sum(stat.mean) / 3
    spread = sum(stat.stddev) / 3
    if mean < 12 or spread < 8:
        return -1
    return spread * 2.2 + mean * 0.18


def trim_dark_edges(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    bg = Image.new("RGB", rgb.size, (0, 0, 0))
    diff = ImageChops.difference(rgb, bg)
    mask = diff.convert("L").point(lambda value: 255 if value > 18 else 0)
    bbox = mask.getbbox()
    if not bbox:
        return rgb
    left, top, right, bottom = bbox
    width, height = rgb.size
    pad_x = max(0, int((right - left) * 0.015))
    pad_y = max(0, int((bottom - top) * 0.015))
    left = max(0, left - pad_x)
    top = max(0, top - pad_y)
    right = min(width, right + pad_x)
    bottom = min(height, bottom + pad_y)
    return rgb.crop((left, top, right, bottom))


def best_crop(image: Image.Image) -> Image.Image:
    width, height = image.size
    best = None
    best_score = -1.0
    for cols, rows in GRID_OPTIONS:
        cell_w = width / cols
        cell_h = height / rows
        margin_x = int(cell_w * 0.045)
        margin_y = int(cell_h * 0.045)
        for row in range(rows):
            for col in range(cols):
                left = int(col * cell_w) + margin_x
                top = int(row * cell_h) + margin_y
                right = int((col + 1) * cell_w) - margin_x
                bottom = int((row + 1) * cell_h) - margin_y
                crop = image.crop((left, top, right, bottom))
                score = cell_score(crop)
                center_bias = abs((col + 0.5) / cols - 0.5) + abs((row + 0.5) / rows - 0.5)
                score -= center_bias * 3.0
                if score > best_score:
                    best_score = score
                    best = crop
    return trim_dark_edges(best or image)


def build() -> tuple[int, int]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    made = 0
    skipped = 0
    for source in sorted(SOURCE_SCREENSHOTS.glob("*.jpg")):
        target = OUTPUT_DIR / source.name
        try:
            src_mtime = source.stat().st_mtime
            if target.is_file() and target.stat().st_mtime >= src_mtime:
                skipped += 1
                continue
            with Image.open(source) as image:
                crop = best_crop(image)
                crop.save(target, "JPEG", quality=JPEG_QUALITY, optimize=True)
            made += 1
        except Exception as exc:
            print(f"failed,{source},{exc}", flush=True)
    return made, skipped


if __name__ == "__main__":
    generated, skipped_current = build()
    print(f"Generated {generated} poster crop(s); skipped {skipped_current} current poster(s).")
