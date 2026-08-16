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
LIVE_CACHE = APP_ROOT / "movie-live-index.json"
CONFIG_FILE = Path(os.environ.get("TMDB_CONFIG_FILE", APP_ROOT / "tmdb_config.json")).resolve()
POSTER_DIR = Path(os.environ.get("MOVIE_POSTER_DIR", APP_ROOT / "posters")).resolve()
POSTER_MAP = Path(os.environ.get("MOVIE_POSTER_MAP", APP_ROOT / "poster-map.json")).resolve()
MISS_CACHE = Path(os.environ.get("MOVIE_POSTER_MISSES", APP_ROOT / "poster-misses.json")).resolve()


def parse_title_year(title: str, rel_path: str) -> tuple[str, str]:
    text = title or rel_path
    match = re.search(r"\((18|19|20)\d{2}\)", text)
    if match:
        year = match.group(0).strip("()")
        clean = text[: match.start()].strip(" ._-")
        return clean or title, year
    match = re.search(r"(?<!\d)((?:18|19|20)\d{2})(?!\d)", text)
    if match:
        year = match.group(1)
        clean = text[: match.start()].strip(" ._-")
        clean = re.sub(r"[._]+", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean or title, year
    return title, ""


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
        raise SystemExit(
            "TMDb credentials missing. Copy tmdb_config.example.json to tmdb_config.json "
            "and set read_access_token, or export TMDB_READ_ACCESS_TOKEN."
        )
    config["read_access_token"] = token
    config["api_key"] = api_key
    return config


def request_json(url: str, token: str) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "local-media-download-library/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def download_file(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "local-media-download-library/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    target.write_bytes(data)


def search_movie(config: dict, title: str, year: str) -> dict | None:
    params = {
        "query": title,
        "include_adult": "false",
        "language": config.get("language", "en-US"),
        "page": "1",
    }
    if year:
        params["year"] = year
    if config.get("api_key") and not config.get("read_access_token"):
        params["api_key"] = config["api_key"]
    url = "https://api.themoviedb.org/3/search/movie?" + urllib.parse.urlencode(params)
    payload = request_json(url, config.get("read_access_token", ""))
    results = payload.get("results", [])
    with_posters = [item for item in results if item.get("poster_path")]
    if not with_posters:
        return None
    if year:
        same_year = [
            item
            for item in with_posters
            if str(item.get("release_date", "")[:4]) == str(year)
        ]
        if same_year:
            return same_year[0]
    return with_posters[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch TMDb posters for the local movie download library.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum new posters to fetch this run. Use 0 for no limit.")
    parser.add_argument("--sleep", type=float, default=0.25, help="Delay between TMDb searches.")
    parser.add_argument("--force", action="store_true", help="Retry items already mapped or missed.")
    args = parser.parse_args()

    config = load_config()
    movies = load_json(LIVE_CACHE, {}).get("movies", [])
    poster_map = load_json(POSTER_MAP, {})
    misses = set(load_json(MISS_CACHE, []))
    POSTER_DIR.mkdir(parents=True, exist_ok=True)

    fetched = 0
    skipped = 0
    missed = 0
    for movie in movies:
        rel_path = movie["rel_path"]
        if not args.force and rel_path in poster_map:
            skipped += 1
            continue
        title, year = parse_title_year(movie["title"], rel_path)
        miss_key = f"{title}|{year}"
        if not args.force and miss_key in misses:
            skipped += 1
            continue
        if args.limit and fetched >= args.limit:
            break
        try:
            match = search_movie(config, title, year)
        except urllib.error.HTTPError as exc:
            print(f"HTTP error for {title} ({year}): {exc}", flush=True)
            if exc.code == 429:
                time.sleep(5)
            continue
        except Exception as exc:
            print(f"Search failed for {title} ({year}): {exc}", flush=True)
            continue
        if not match:
            misses.add(miss_key)
            missed += 1
            print(f"MISS {title} ({year})", flush=True)
            if (missed + fetched) % 25 == 0:
                save_json(POSTER_MAP, poster_map)
                save_json(MISS_CACHE, sorted(misses))
            time.sleep(args.sleep)
            continue
        poster_path = match["poster_path"]
        poster_size = config.get("poster_size", "w342")
        extension = Path(poster_path).suffix or ".jpg"
        poster_name = f"tmdb-{match['id']}-{poster_size}{extension}"
        poster_file = POSTER_DIR / poster_name
        if not poster_file.is_file():
            image_url = f"https://image.tmdb.org/t/p/{poster_size}{poster_path}"
            download_file(image_url, poster_file)
        poster_map[rel_path] = f"posters/{poster_name}"
        fetched += 1
        print(f"POSTER {title} ({year}) -> {poster_name}", flush=True)
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
