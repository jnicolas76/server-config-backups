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
LIVE_CACHE = Path(os.environ.get("MOVIE_LIVE_CACHE", APP_ROOT / "movie-live-index.json")).resolve()
CONFIG_FILE = Path(os.environ.get("TMDB_CONFIG_FILE", APP_ROOT / "tmdb_config.json")).resolve()
METADATA_MAP = Path(os.environ.get("MOVIE_METADATA_MAP", APP_ROOT / "movie-metadata-map.json")).resolve()
MISS_CACHE = Path(os.environ.get("MOVIE_METADATA_MISSES", APP_ROOT / "movie-metadata-misses.json")).resolve()


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
        raise SystemExit("TMDb credentials missing in tmdb_config.json or environment.")
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


def parse_title_year(title: str, rel_path: str) -> tuple[str, str]:
    text = title or rel_path
    match = re.search(r"\((18|19|20)\d{2}\)", text)
    if match:
        return text[: match.start()].strip(" ._-") or title, match.group(0).strip("()")
    match = re.search(r"(?<!\d)((?:18|19|20)\d{2})(?!\d)", text)
    if match:
        clean = re.sub(r"[._]+", " ", text[: match.start()]).strip(" ._-")
        return clean or title, match.group(1)
    return title, ""


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
    if not results:
        return None
    if year:
        same_year = [item for item in results if str(item.get("release_date", "")[:4]) == str(year)]
        if same_year:
            return same_year[0]
    return results[0]


def movie_credits(config: dict, movie_id: int) -> list[str]:
    params = {"language": config.get("language", "en-US")}
    if config.get("api_key") and not config.get("read_access_token"):
        params["api_key"] = config["api_key"]
    url = f"https://api.themoviedb.org/3/movie/{movie_id}/credits?" + urllib.parse.urlencode(params)
    payload = request_json(url, config.get("read_access_token", ""))
    return [person.get("name", "") for person in payload.get("cast", []) if person.get("name")]


def genre_map(config: dict) -> dict[int, str]:
    params = {"language": config.get("language", "en-US")}
    if config.get("api_key") and not config.get("read_access_token"):
        params["api_key"] = config["api_key"]
    url = "https://api.themoviedb.org/3/genre/movie/list?" + urllib.parse.urlencode(params)
    payload = request_json(url, config.get("read_access_token", ""))
    return {int(item["id"]): item["name"] for item in payload.get("genres", []) if item.get("id") and item.get("name")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch TMDb overview and cast metadata for movies.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum new metadata records. Use 0 for no limit.")
    parser.add_argument("--sleep", type=float, default=0.25, help="Delay between TMDb calls.")
    parser.add_argument("--force", action="store_true", help="Retry already mapped/missed movies.")
    args = parser.parse_args()

    config = load_config()
    genres_by_id = genre_map(config)
    movies = load_json(LIVE_CACHE, {}).get("movies", [])
    metadata = load_json(METADATA_MAP, {})
    misses = set(load_json(MISS_CACHE, []))
    fetched = skipped = missed = 0

    for movie in movies:
        rel_path = movie["rel_path"]
        if not args.force and rel_path in metadata:
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
            time.sleep(args.sleep)
            if not match:
                misses.add(miss_key)
                missed += 1
                print(f"MISS {title} ({year})", flush=True)
                continue
            actors = movie_credits(config, int(match["id"]))
        except urllib.error.HTTPError as exc:
            print(f"HTTP error for {title} ({year}): {exc}", flush=True)
            if exc.code == 429:
                time.sleep(5)
            continue
        except Exception as exc:
            print(f"Metadata failed for {title} ({year}): {exc}", flush=True)
            continue
        metadata[rel_path] = {
            "tmdb_id": match.get("id"),
            "title": match.get("title") or title,
            "release_date": match.get("release_date") or "",
            "year": str(match.get("release_date", "")[:4] or year),
            "overview": match.get("overview") or "",
            "vote_average": match.get("vote_average"),
            "vote_count": match.get("vote_count"),
            "genres": [genres_by_id[item] for item in match.get("genre_ids", []) if item in genres_by_id],
            "actors": actors,
        }
        fetched += 1
        print(f"METADATA {movie['title']} -> {metadata[rel_path]['title']} ({metadata[rel_path]['year']})", flush=True)
        if fetched % 25 == 0:
            save_json(METADATA_MAP, metadata)
            save_json(MISS_CACHE, sorted(misses))
        time.sleep(args.sleep)

    save_json(METADATA_MAP, metadata)
    save_json(MISS_CACHE, sorted(misses))
    print(f"Done. fetched={fetched} skipped={skipped} missed={missed} mapped={len(metadata)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
