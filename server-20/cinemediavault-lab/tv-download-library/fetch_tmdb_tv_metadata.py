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
METADATA_MAP = Path(os.environ.get("TV_METADATA_MAP", APP_ROOT / "tv-metadata-map.json")).resolve()
MISS_CACHE = Path(os.environ.get("TV_METADATA_MISSES", APP_ROOT / "tv-metadata-misses.json")).resolve()


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
    results = payload.get("results", [])
    if not results:
        return None
    if first_air_year:
        same_year = [item for item in results if str(item.get("first_air_date", "")[:4]) == str(first_air_year)]
        if same_year:
            return same_year[0]
    return results[0]


def tv_credits(config: dict, tv_id: int) -> list[str]:
    params = {"language": config.get("language", "en-US")}
    if config.get("api_key") and not config.get("read_access_token"):
        params["api_key"] = config["api_key"]
    names = []
    seen = set()
    for endpoint in ("aggregate_credits", "credits"):
        url = f"https://api.themoviedb.org/3/tv/{tv_id}/{endpoint}?" + urllib.parse.urlencode(params)
        payload = request_json(url, config.get("read_access_token", ""))
        for person in payload.get("cast", []):
            name = str(person.get("name", "")).strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
    return names


def tv_season_details(config: dict, tv_id: int, season_number: int) -> dict:
    params = {"language": config.get("language", "en-US")}
    if config.get("api_key") and not config.get("read_access_token"):
        params["api_key"] = config["api_key"]
    url = f"https://api.themoviedb.org/3/tv/{tv_id}/season/{season_number}?" + urllib.parse.urlencode(params)
    return request_json(url, config.get("read_access_token", ""))


def episode_metadata_for_seasons(config: dict, tv_id: int, seasons: set[int], sleep_seconds: float) -> tuple[dict, dict]:
    episodes = {}
    air_dates = {}
    for season_number in sorted(seasons):
        try:
            payload = tv_season_details(config, tv_id, season_number)
        except urllib.error.HTTPError as exc:
            print(f"HTTP error for tv_id={tv_id} season={season_number}: {exc}", flush=True)
            if exc.code == 429:
                time.sleep(5)
            continue
        except Exception as exc:
            print(f"Season metadata failed for tv_id={tv_id} season={season_number}: {exc}", flush=True)
            continue
        for row in payload.get("episodes", []):
            number = row.get("episode_number")
            if not number:
                continue
            key = f"S{season_number:02d}E{int(number):02d}"
            episodes[key] = {
                "title": row.get("name") or "",
                "air_date": row.get("air_date") or "",
                "overview": row.get("overview") or "",
                "runtime": row.get("runtime"),
                "still_path": row.get("still_path") or "",
                "vote_average": row.get("vote_average"),
            }
            if row.get("air_date"):
                air_dates[key] = row.get("air_date")
        time.sleep(sleep_seconds)
    return episodes, air_dates


def genre_map(config: dict) -> dict[int, str]:
    params = {"language": config.get("language", "en-US")}
    if config.get("api_key") and not config.get("read_access_token"):
        params["api_key"] = config["api_key"]
    url = "https://api.themoviedb.org/3/genre/tv/list?" + urllib.parse.urlencode(params)
    payload = request_json(url, config.get("read_access_token", ""))
    return {int(item["id"]): item["name"] for item in payload.get("genres", []) if item.get("id") and item.get("name")}


def shows_from_cache() -> list[str]:
    payload = load_json(LIVE_CACHE, {})
    return sorted({row.get("show", "").strip() for row in payload.get("episodes", []) if row.get("show")})


def seasons_from_cache() -> dict[str, set[int]]:
    payload = load_json(LIVE_CACHE, {})
    result: dict[str, set[int]] = {}
    for row in payload.get("episodes", []):
        show = row.get("show", "").strip()
        season_text = row.get("season", "")
        match = re.search(r"(\d+)", season_text)
        if show and match:
            result.setdefault(show, set()).add(int(match.group(1)))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch TMDb TV overview and cast metadata.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum new metadata records. Use 0 for no limit.")
    parser.add_argument("--sleep", type=float, default=0.25, help="Delay between TMDb calls.")
    parser.add_argument("--force", action="store_true", help="Retry already mapped or missed shows.")
    args = parser.parse_args()

    config = load_config()
    genres_by_id = genre_map(config)
    shows = shows_from_cache()
    seasons_by_show = seasons_from_cache()
    metadata = load_json(METADATA_MAP, {})
    misses = set(load_json(MISS_CACHE, []))
    fetched = skipped = missed = 0

    for show in shows:
        title, year = clean_show_name(show)
        miss_key = f"{title}|{year}"
        expected_seasons = seasons_by_show.get(show, set())
        existing = metadata.get(show, {})
        if not args.force and existing:
            existing_episodes = existing.get("episodes") or {}
            missing_episode_data = bool(expected_seasons) and not existing_episodes
            if not missing_episode_data:
                skipped += 1
                continue
            if existing.get("tmdb_id"):
                episodes, air_dates = episode_metadata_for_seasons(config, int(existing["tmdb_id"]), expected_seasons, args.sleep)
                existing["episodes"] = episodes
                existing["episode_air_dates"] = air_dates
                metadata[show] = existing
                fetched += 1
                print(f"EPISODES {show} seasons={len(expected_seasons)} episodes={len(episodes)}", flush=True)
                if fetched % 25 == 0:
                    save_json(METADATA_MAP, metadata)
                    save_json(MISS_CACHE, sorted(misses))
                continue
            skipped += 1
            continue
        if not args.force and miss_key in misses:
            skipped += 1
            continue
        if args.limit and fetched >= args.limit:
            break
        try:
            match = search_tv(config, title, year)
            time.sleep(args.sleep)
            if not match:
                misses.add(miss_key)
                missed += 1
                print(f"MISS {title} ({year})", flush=True)
                continue
            actors = tv_credits(config, int(match["id"]))
            time.sleep(args.sleep)
            episodes, air_dates = episode_metadata_for_seasons(config, int(match["id"]), expected_seasons, args.sleep)
        except urllib.error.HTTPError as exc:
            print(f"HTTP error for {title}: {exc}", flush=True)
            if exc.code == 429:
                time.sleep(5)
            continue
        except Exception as exc:
            print(f"Metadata failed for {title}: {exc}", flush=True)
            continue
        metadata[show] = {
            "tmdb_id": match.get("id"),
            "title": match.get("name") or title,
            "first_air_date": match.get("first_air_date") or "",
            "year": str(match.get("first_air_date", "")[:4] or year),
            "overview": match.get("overview") or "",
            "vote_average": match.get("vote_average"),
            "vote_count": match.get("vote_count"),
            "genres": [genres_by_id[item] for item in match.get("genre_ids", []) if item in genres_by_id],
            "actors": actors,
            "episodes": episodes,
            "episode_air_dates": air_dates,
        }
        fetched += 1
        print(f"METADATA {show} -> {metadata[show]['title']} ({metadata[show]['year']})", flush=True)
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
