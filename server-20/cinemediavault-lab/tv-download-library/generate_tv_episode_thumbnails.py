#!/usr/bin/env python3
import argparse
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_DIR = Path(os.environ.get("TV_APP_DIR", Path(__file__).resolve().parent)).resolve()
THUMB_DIR = Path(os.environ.get("TV_EPISODE_THUMB_DIR", APP_DIR / "episode-thumbnails")).resolve()
LOG_DIR = Path(os.environ.get("MEDIA_LIBRARY_LOG_DIR", "/home/jnicolas/media-library-refresh-logs")).resolve()


def load_tv_app():
    spec = importlib.util.spec_from_file_location("tv_download_app_for_thumbs", APP_DIR / "tv_download_server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["tv_download_app_for_thumbs"] = module
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tv_episode_thumbnails")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    log_path = LOG_DIR / f"thumbnail-scan-{time.strftime('%Y-%m-%d')}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


def ffmpeg_thumbnail(ffmpeg: str, source: Path, output: Path, seek_seconds: int, logger: logging.Logger) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".tmp.jpg")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(seek_seconds),
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        "scale=320:-1:force_original_aspect_ratio=decrease",
        "-q:v",
        "4",
        str(temp),
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90)
    if result.returncode != 0 or not temp.is_file() or temp.stat().st_size < 1024:
        try:
            temp.unlink()
        except OSError:
            pass
        if result.stderr.strip():
            logger.info("ffmpeg failed seek=%s path=%s error=%s", seek_seconds, source, result.stderr.strip()[:500])
        return False
    temp.replace(output)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate local fallback episode thumbnails for CineVault TV rows.")
    parser.add_argument("--limit", type=int, default=int(os.environ.get("TV_THUMBNAIL_LIMIT", "300")), help="Maximum thumbnails to create this run. 0 = no limit.")
    parser.add_argument("--seek", type=int, default=int(os.environ.get("TV_THUMBNAIL_SEEK_SECONDS", "60")), help="Preferred thumbnail seek position in seconds.")
    parser.add_argument("--retry-seek", type=int, default=int(os.environ.get("TV_THUMBNAIL_RETRY_SEEK_SECONDS", "10")), help="Fallback seek position in seconds.")
    parser.add_argument("--include-tmdb-stills", action="store_true", help="Generate thumbnails even when TMDB already has an episode still.")
    args = parser.parse_args()

    logger = setup_logging()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.error("ffmpeg was not found in PATH")
        return 2

    tv = load_tv_app()
    if not tv.tv_index.load_cache():
        tv.tv_index.refresh()
    tv.load_metadata_map()
    THUMB_DIR.mkdir(parents=True, exist_ok=True)

    created = skipped_existing = skipped_tmdb = failed = considered = 0
    for show in tv.tv_index.shows:
        metadata = tv.metadata_for(show)
        for season in show.seasons.values():
            for episode in season.episodes:
                considered += 1
                output = THUMB_DIR / tv.episode_thumbnail_name(episode)
                if output.is_file() and output.stat().st_size > 1024:
                    skipped_existing += 1
                    continue
                row = tv.episode_metadata(metadata, episode)
                if not args.include_tmdb_stills and str(row.get("still_path") or "").startswith("/"):
                    skipped_tmdb += 1
                    continue
                if args.limit and created >= args.limit:
                    payload = {
                        "considered": considered,
                        "created": created,
                        "skipped_existing": skipped_existing,
                        "skipped_tmdb": skipped_tmdb,
                        "failed": failed,
                        "limit_reached": True,
                    }
                    logger.info("thumbnail run summary %s", json.dumps(payload, sort_keys=True))
                    return 0
                if not episode.path.is_file():
                    failed += 1
                    logger.info("missing episode file path=%s", episode.path)
                    continue
                if ffmpeg_thumbnail(ffmpeg, episode.path, output, args.seek, logger) or ffmpeg_thumbnail(ffmpeg, episode.path, output, args.retry_seek, logger):
                    created += 1
                    logger.info("created thumbnail show=%s episode=%s output=%s", show.title, episode.rel_path, output)
                else:
                    failed += 1
    payload = {
        "considered": considered,
        "created": created,
        "skipped_existing": skipped_existing,
        "skipped_tmdb": skipped_tmdb,
        "failed": failed,
        "limit_reached": False,
    }
    logger.info("thumbnail run summary %s", json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
