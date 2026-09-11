#!/usr/bin/env python3
import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
MOVIE_APP_ROOT = Path(os.environ.get("MOVIE_DOWNLOAD_APP_ROOT", APP_ROOT.parent / "media-download-library")).resolve()
LIVE_CACHE = Path(os.environ.get("TV_LIVE_CACHE", APP_ROOT / "tv-live-index.json")).resolve()
CONFIG_FILE = Path(os.environ.get("TMDB_CONFIG_FILE", MOVIE_APP_ROOT / "tmdb_config.json")).resolve()
POSTER_DIR = Path(os.environ.get("TV_POSTER_DIR", APP_ROOT / "posters")).resolve()
POSTER_MAP = Path(os.environ.get("TV_POSTER_MAP", APP_ROOT / "tv-poster-map.json")).resolve()
MISS_CACHE = Path(os.environ.get("TV_POSTER_MISSES", APP_ROOT / "tv-poster-misses.json")).resolve()


def load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_config() -> dict:
    config = load_json(CONFIG_FILE, {})
    token = os.environ.get("TMDB_READ_ACCESS_TOKEN") or config.get("read_access_token", "")
    api_key = os.environ.get("TMDB_API_KEY") or config.get("api_key", "")
    if not token and not api_key:
        raise SystemExit("TMDb credentials missing in movie library tmdb_config.json or environment.")
    config["read_access_token"] = token
    config["api_key"] = api_key
    return config


def request_json(url: str, token: str) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "local-tv-download-library/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def download_file(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "local-tv-download-library/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        target.write_bytes(response.read())


def clean_show_name(value: str) -> tuple[str, str]:
    match = re.search(r"\((18|19|20)\d{2}\)", value)
    if match:
        return value[: match.start()].strip(" ._-"), match.group(0).strip("()")
    return value.strip(), ""


def search_tv(config: dict, show: str, first_air_year: str) -> dict | None:
    params = {
        "query": show,
        "include_adult": "false",
        "language": config.get("language", "en-US"),
        "page": "1",
    }
    if first_air_year:
        params["first_air_date_year"] = first_air_year
    if config.get("api_key") and not config.get("read_access_token"):
        params["api_key"] = config["api_key"]
    url = "https://api.themoviedb.org/3/search/tv?" + urllib.parse.urlencode(params)
    payload = request_json(url, config.get("read_access_token", ""))
    results = [item for item in payload.get("results", []) if item.get("poster_path")]
    if not results:
        return None
    if first_air_year:
        same_year = [item for item in results if str(item.get("first_air_date", "")[:4]) == str(first_air_year)]
        if same_year:
            return same_year[0]
    return results[0]


def shows_from_cache() -> list[str]:
    payload = load_json(LIVE_CACHE, {})
    return sorted({row.get("show", "").strip() for row in payload.get("episodes", []) if row.get("show")})


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch TMDb TV posters for the local TV download library.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum new posters to fetch. Use 0 for no limit.")
    parser.add_argument("--sleep", type=float, default=0.25, help="Delay between TMDb searches.")
    parser.add_argument("--force", action="store_true", help="Retry already mapped or missed shows.")
    args = parser.parse_args()

    config = load_config()
    shows = shows_from_cache()
    poster_map = load_json(POSTER_MAP, {})
    misses = set(load_json(MISS_CACHE, []))
    POSTER_DIR.mkdir(parents=True, exist_ok=True)

    fetched = 0
    skipped = 0
    missed = 0
    for show in shows:
        title, year = clean_show_name(show)
        miss_key = f"{title}|{year}"
        if not args.force and show in poster_map:
            skipped += 1
            continue
        if not args.force and miss_key in misses:
            skipped += 1
            continue
        if args.limit and fetched >= args.limit:
            break
        try:
            match = search_tv(config, title, year)
        except urllib.error.HTTPError as exc:
            print(f"HTTP error for {title}: {exc}", flush=True)
            if exc.code == 429:
                time.sleep(5)
            continue
        except Exception as exc:
            print(f"Search failed for {title}: {exc}", flush=True)
            continue
        if not match:
            misses.add(miss_key)
            missed += 1
            print(f"MISS {title} ({year})", flush=True)
            time.sleep(args.sleep)
            continue
        poster_path = match["poster_path"]
        poster_size = config.get("poster_size", "w342")
        extension = Path(poster_path).suffix or ".jpg"
        poster_name = f"tmdb-tv-{match['id']}-{poster_size}{extension}"
        poster_file = POSTER_DIR / poster_name
        if not poster_file.is_file():
            download_file(f"https://image.tmdb.org/t/p/{poster_size}{poster_path}", poster_file)
        poster_map[show] = f"posters/{poster_name}"
        fetched += 1
        print(f"POSTER {show} -> {poster_name}", flush=True)
        if fetched % 25 == 0:
            save_json(POSTER_MAP, poster_map)
            save_json(MISS_CACHE, sorted(misses))
        time.sleep(args.sleep)

    save_json(POSTER_MAP, poster_map)
    save_json(MISS_CACHE, sorted(misses))
    print(f"Done. fetched={fetched} skipped={skipped} missed={missed} mapped={len(poster_map)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
