#!/usr/bin/env python3
import argparse
import csv
import html
import hashlib
import json
import mimetypes
import os
import random
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MOVIE_ROOT = Path(os.environ.get("MOVIE_ROOT", "/mnt/nfs-share-movies/Movies")).resolve()
MOVIE_CSV = Path(os.environ.get("MOVIE_CSV", "/mnt/c/DATA/movie-file-sizes.csv")).resolve()
LIVE_CACHE = Path(os.environ.get("MOVIE_LIVE_CACHE", "/mnt/c/DATA/media-download-library/movie-live-index.json")).resolve()
POSTER_MAP = Path(os.environ.get("MOVIE_POSTER_MAP", "/mnt/c/DATA/media-download-library/poster-map.json")).resolve()
POSTER_DIR = Path(os.environ.get("MOVIE_POSTER_DIR", "/mnt/c/DATA/media-download-library/posters")).resolve()
METADATA_MAP = Path(os.environ.get("MOVIE_METADATA_MAP", "/mnt/c/DATA/media-download-library/movie-metadata-map.json")).resolve()
TMDB_CONFIG_FILE = Path(os.environ.get("TMDB_CONFIG_FILE", METADATA_MAP.parent / "tmdb_config.json")).resolve()
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".m4v", ".avi", ".mov", ".mpeg", ".mpg", ".m2ts", ".ts", ".webm"}
RECENTLY_ADDED_LIMIT = int(os.environ.get("MOVIE_RECENTLY_ADDED_LIMIT", "20"))
RECENT_SHELF_LIMIT = int(os.environ.get("MOVIE_RECENT_SHELF_LIMIT", "20"))
RECENT_SHELF_EXPANDED_LIMIT = int(os.environ.get("MOVIE_RECENT_SHELF_EXPANDED_LIMIT", "50"))
SERVER_DISPLAY_NAME = os.environ.get("CINEVAULT_SERVER_NAME") or socket.gethostname()


@dataclass
class MovieItem:
    id: int
    title: str
    path: Path
    rel_path: str
    size: int
    modified: float


class MovieIndex:
    def __init__(self):
        self.items: list[MovieItem] = []
        self.by_id: dict[int, MovieItem] = {}
        self.scanned_at = 0.0
        self.scanning = False
        self.error = ""

    def refresh(self) -> None:
        if self.scanning:
            return
        self.scanning = True
        self.error = ""
        try:
            items = self.refresh_from_find()

            items.sort(key=lambda item: natural_key(item.title))
            for index, item in enumerate(items, start=1):
                item.id = index
            self.items = items
            self.by_id = {item.id: item for item in items}
            self.scanned_at = time.time()
            self.save_live_cache(items)
        except Exception as exc:
            self.error = str(exc)
            raise
        finally:
            self.scanning = False

    def refresh_from_csv(self) -> list[MovieItem]:
        items: list[MovieItem] = []
        with MOVIE_CSV.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for next_id, row in enumerate(reader, start=1):
                path_value = row.get("file_path") or ""
                ext = "." + (row.get("extension") or Path(path_value).suffix.lstrip(".")).lower()
                if not path_value or ext not in VIDEO_EXTENSIONS:
                    continue
                path = Path(path_value)
                try:
                    rel_path = str(path.relative_to(MOVIE_ROOT))
                except ValueError:
                    rel_path = path.name
                try:
                    size = int(row.get("size_bytes") or 0)
                except ValueError:
                    size = 0
                title = clean_title(path)
                items.append(MovieItem(next_id, title, path, rel_path, size, 0.0))
        return items

    def refresh_from_find(self) -> list[MovieItem]:
        items: list[MovieItem] = []
        expression = ["find", str(MOVIE_ROOT), "-maxdepth", "2", "-type", "f", "("]
        for index, ext in enumerate(sorted(VIDEO_EXTENSIONS)):
            if index:
                expression.append("-o")
            expression.extend(["-iname", f"*{ext}"])
        expression.append(")")
        result = subprocess.run(expression, check=True, text=True, stdout=subprocess.PIPE)
        for next_id, line in enumerate(result.stdout.splitlines(), start=1):
            path = Path(line)
            try:
                stat = path.stat()
                rel_path = str(path.relative_to(MOVIE_ROOT))
            except OSError:
                continue
            title = clean_title(path)
            items.append(MovieItem(next_id, title, path, rel_path, stat.st_size, stat.st_mtime))
        return items

    def refresh_from_disk(self) -> list[MovieItem]:
        items: list[MovieItem] = []
        next_id = 1
        candidates: list[Path] = []
        for entry in MOVIE_ROOT.iterdir():
            try:
                if entry.is_file():
                    candidates.append(entry)
                elif entry.is_dir():
                    candidates.extend(path for path in entry.iterdir() if path.is_file())
            except OSError:
                continue
        for path in candidates:
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            rel_path = str(path.relative_to(MOVIE_ROOT))
            title = clean_title(path)
            items.append(MovieItem(next_id, title, path, rel_path, stat.st_size, stat.st_mtime))
            next_id += 1
        return items

    def refresh_background(self) -> None:
        import threading

        thread = threading.Thread(target=self.refresh, daemon=True)
        thread.start()

    def load_csv_bootstrap(self) -> None:
        if LIVE_CACHE.is_file():
            try:
                items = self.load_live_cache()
                items.sort(key=lambda item: natural_key(item.title))
                for index, item in enumerate(items, start=1):
                    item.id = index
                self.items = items
                self.by_id = {item.id: item for item in items}
                self.scanned_at = LIVE_CACHE.stat().st_mtime
                return
            except Exception as exc:
                self.error = f"Live cache load failed: {exc}"
        if not MOVIE_CSV.is_file():
            return
        items = self.refresh_from_csv()
        items.sort(key=lambda item: natural_key(item.title))
        for index, item in enumerate(items, start=1):
            item.id = index
        self.items = items
        self.by_id = {item.id: item for item in items}
        self.scanned_at = time.time()

    def load_live_cache(self) -> list[MovieItem]:
        raw = LIVE_CACHE.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload, _ = json.JSONDecoder().raw_decode(raw)
        items: list[MovieItem] = []
        for next_id, row in enumerate(payload.get("movies", []), start=1):
            path = Path(row["path"])
            items.append(
                MovieItem(
                    next_id,
                    row["title"],
                    path,
                    row["rel_path"],
                    int(row["size"]),
                    float(row.get("modified", 0.0)),
                )
            )
        return items

    def save_live_cache(self, items: list[MovieItem]) -> None:
        LIVE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "root": str(MOVIE_ROOT),
            "scanned_at": time.time(),
            "movies": [
                {
                    "title": item.title,
                    "path": str(item.path),
                    "rel_path": item.rel_path,
                    "size": item.size,
                    "modified": item.modified,
                }
                for item in items
            ],
        }
        tmp = LIVE_CACHE.with_name(f"{LIVE_CACHE.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(LIVE_CACHE)


movie_index = MovieIndex()
poster_map: dict[str, str] = {}
metadata_map: dict[str, dict] = {}


def clean_title(path: Path) -> str:
    folder = path.parent.name
    stem = path.stem
    if normalize_name(stem).startswith(normalize_name(folder)):
        return folder
    return f"{folder} - {stem}"


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def duplicate_key(item: MovieItem) -> str:
    value = " ".join([item.title, item.rel_path])
    year_match = re.search(r"(19|20)\d{2}", value)
    year = year_match.group(0) if year_match else ""
    base = item.title
    if year:
        base = base[: base.find(year)] if year in base else base
    base = re.sub(r"\[[^\]]*\]|\([^\)]*\)", " ", base)
    base = re.sub(r"\b(1080p|720p|2160p|480p|4k|bluray|blu-ray|webrip|web-dl|web|h264|h265|x264|x265|aac|dts|yts|mx|bitsearch|to)\b", " ", base, flags=re.I)
    key_base = normalize_name(base)
    if not key_base:
        key_base = normalize_name(item.path.stem)
    return f"{key_base}:{year}" if year else key_base


def media_part_tag(item: MovieItem) -> str:
    value = f"{item.title} {item.rel_path}"
    match = re.search(r"(?:^|[.\s_-])(?:cd|disc|disk|part|pt)[.\s_-]*([0-9]+)(?:$|[.\s_-])", value, re.I)
    return match.group(1) if match else ""


def duplicate_keys(items: list[MovieItem]) -> set[str]:
    groups: dict[str, list[MovieItem]] = {}
    for item in items:
        key = duplicate_key(item)
        if key:
            groups.setdefault(key, []).append(item)
    keys: set[str] = set()
    for key, group in groups.items():
        if len(group) <= 1:
            continue
        part_tags = {media_part_tag(item) for item in group}
        has_untagged_copy = "" in part_tags
        if not has_untagged_copy and len(part_tags) > 1:
            continue
        keys.add(key)
    return keys


def natural_key(value: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "movie"


def load_poster_map() -> None:
    global poster_map
    if not POSTER_MAP.is_file():
        poster_map = {}
        return
    try:
        poster_map = json.loads(POSTER_MAP.read_text(encoding="utf-8"))
    except Exception:
        poster_map = {}


def load_metadata_map() -> None:
    global metadata_map
    if not METADATA_MAP.is_file():
        metadata_map = {}
        return
    try:
        metadata_map = json.loads(METADATA_MAP.read_text(encoding="utf-8"))
    except Exception:
        metadata_map = {}


def poster_url_for(item: MovieItem) -> str:
    rel = poster_map.get(item.rel_path) or poster_map.get(item.title)
    if not rel:
        return ""
    return "/" + rel.lstrip("/")


def poster_backdrop_html(items, limit: int = 70) -> str:
    candidates = list(items)
    random.shuffle(candidates)
    posters: list[str] = []
    seen: set[str] = set()
    for item in candidates[: limit * 3]:
        poster = poster_url_for(item)
        if not poster or poster in seen:
            continue
        seen.add(poster)
        posters.append(poster)
    random.shuffle(posters)
    return "".join(f"<img src='{html.escape(poster)}' alt=''>" for poster in posters[:limit])


def metadata_for(item: MovieItem) -> dict:
    return metadata_map.get(item.rel_path) or metadata_map.get(item.title) or {}


def load_json_file(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json_file(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def tmdb_config() -> dict:
    config = load_json_file(TMDB_CONFIG_FILE, {})
    token = os.environ.get("TMDB_READ_ACCESS_TOKEN") or config.get("read_access_token", "")
    api_key = os.environ.get("TMDB_API_KEY") or config.get("api_key", "")
    if not token and not api_key:
        raise FileNotFoundError(f"TMDB credentials missing: {TMDB_CONFIG_FILE}")
    config["read_access_token"] = token
    config["api_key"] = api_key
    config.setdefault("language", "en-US")
    return config


def tmdb_request_json(path: str, params: dict | None = None) -> dict:
    config = tmdb_config()
    params = dict(params or {})
    params.setdefault("language", config.get("language", "en-US"))
    if config.get("api_key") and not config.get("read_access_token"):
        params["api_key"] = config["api_key"]
    url = "https://api.themoviedb.org/3" + path + "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json", "User-Agent": "cinevault-movie-fix-match/1.0"}
    if config.get("read_access_token"):
        headers["Authorization"] = f"Bearer {config['read_access_token']}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def tmdb_image_url(path: str, size: str = "w342") -> str:
    return f"https://image.tmdb.org/t/p/{size}{path}" if path else ""


def tmdb_search_candidates(query: str, year: str = "") -> list[dict]:
    params = {"query": query, "include_adult": "false", "page": "1"}
    if year:
        params["year"] = year
    payload = tmdb_request_json("/search/movie", params)
    return payload.get("results", [])[:12]


def tmdb_movie_credits(movie_id: int) -> list[str]:
    payload = tmdb_request_json(f"/movie/{movie_id}/credits", {})
    return [person.get("name", "") for person in payload.get("cast", []) if person.get("name")]


def download_tmdb_poster(item: MovieItem, tmdb_row: dict) -> str:
    poster_path = tmdb_row.get("poster_path") or ""
    if not poster_path:
        return ""
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(poster_path).suffix or ".jpg"
    filename = f"tmdb-{tmdb_row.get('id')}-w500{ext}"
    destination = POSTER_DIR / filename
    if not destination.is_file():
        urllib.request.urlretrieve(tmdb_image_url(poster_path, "w500"), destination)
    return f"posters/{filename}"


def apply_tmdb_match(item: MovieItem, tmdb_id: int) -> dict:
    global poster_map, metadata_map
    detail = tmdb_request_json(f"/movie/{tmdb_id}", {})
    actors = tmdb_movie_credits(tmdb_id)
    metadata = load_json_file(METADATA_MAP, {})
    metadata[item.rel_path] = {
        "tmdb_id": detail.get("id"),
        "title": detail.get("title") or item.title,
        "release_date": detail.get("release_date") or "",
        "year": str(detail.get("release_date", "")[:4] or ""),
        "overview": detail.get("overview") or "",
        "vote_average": detail.get("vote_average"),
        "vote_count": detail.get("vote_count"),
        "genres": [genre.get("name") for genre in detail.get("genres", []) if genre.get("name")],
        "actors": actors,
        "poster_path": detail.get("poster_path") or "",
        "backdrop_path": detail.get("backdrop_path") or "",
    }
    save_json_file(METADATA_MAP, metadata)
    metadata_map = metadata
    rel_poster = download_tmdb_poster(item, detail)
    if rel_poster:
        poster_data = load_json_file(POSTER_MAP, {})
        poster_data[item.rel_path] = rel_poster
        save_json_file(POSTER_MAP, poster_data)
        poster_map = poster_data
    return metadata[item.rel_path]


def parse_multipart_form(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]]]:
    match = re.search(r"boundary=(?P<boundary>[^;]+)", content_type)
    if not match:
        raise ValueError("Missing multipart boundary")
    boundary = match.group("boundary").strip().strip('"').encode("utf-8")
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes, str]] = {}
    for part in body.split(b"--" + boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        raw_headers, data = part.split(b"\r\n\r\n", 1)
        data = data.rstrip(b"\r\n")
        headers = raw_headers.decode("utf-8", errors="ignore").split("\r\n")
        disposition = next((line for line in headers if line.lower().startswith("content-disposition:")), "")
        content_type_header = next((line for line in headers if line.lower().startswith("content-type:")), "")
        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        if filename_match:
            filename = Path(filename_match.group(1)).name
            mime = content_type_header.split(":", 1)[1].strip() if ":" in content_type_header else ""
            files[name] = (filename, data, mime)
        else:
            fields[name] = data.decode("utf-8", errors="ignore")
    return fields, files


def save_uploaded_art(item: MovieItem, filename: str, data: bytes, mime: str) -> str:
    global poster_map
    if len(data) < 1024:
        raise ValueError("Uploaded image is empty or too small")
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Uploaded image is larger than 20 MB")
    suffix = Path(filename).suffix.lower()
    mime_suffix = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(mime.lower(), "")
    suffix = suffix if suffix in {".jpg", ".jpeg", ".png", ".webp"} else mime_suffix
    if suffix == ".jpeg":
        suffix = ".jpg"
    if not suffix:
        raise ValueError("Only JPG, PNG, and WebP images are supported")
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(data).hexdigest()[:12]
    destination = POSTER_DIR / f"custom-{slugify(item.title)}-{digest}{suffix}"
    destination.write_bytes(data)
    poster_data = load_json_file(POSTER_MAP, {})
    poster_data[item.rel_path] = f"posters/{destination.name}"
    save_json_file(POSTER_MAP, poster_data)
    poster_map = poster_data
    return poster_data[item.rel_path]


def human_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(size)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{size} B"


def date_label(timestamp: float) -> str:
    if not timestamp:
        return ""
    return time.strftime("%b %d, %Y", time.localtime(timestamp))


def month_start_timestamp() -> float:
    now = time.localtime()
    return time.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def current_year_text() -> str:
    return str(time.localtime().tm_year)


def tmdb_score_html(metadata: dict) -> str:
    try:
        value = float(metadata.get("vote_average") or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    count = metadata.get("vote_count")
    title = f"TMDb rating from {int(count)} votes" if isinstance(count, int) and count else "TMDb rating"
    return f"<span class='score tmdb-score' title='{html.escape(title)}'><span class='score-badge'>TMDb</span>{value:.1f}</span>"


def genres_for(metadata: dict) -> list[str]:
    genres = metadata.get("genres") or []
    return [str(genre) for genre in genres if str(genre).strip()]


def release_label_for(metadata: dict) -> str:
    return str(metadata.get("release_date") or metadata.get("year") or "")


def recent_shelf_section(section_id: str, title: str, cards: list[str], grid_class: str) -> str:
    if not cards:
        return ""
    visible = cards[:RECENT_SHELF_LIMIT]
    extra = cards[RECENT_SHELF_LIMIT:RECENT_SHELF_EXPANDED_LIMIT]
    if extra:
        visible.append(
            f"<button class='see-all-card' type='button' data-shelf-target='{html.escape(section_id)}'>"
            "<span>See all</span><small>Last 50</small>"
            "</button>"
        )
    card_html = "".join(visible) + "".join(
        f"<div class='shelf-extra hidden'>{card}</div>"
        for card in extra
    )
    return (
        f"<section class='letter-section shelf-section' id='{html.escape(section_id)}' data-letter='RECENT'>"
        f"<h2>{html.escape(title)}</h2>"
        f"<div class='{grid_class} shelf-row'>{card_html}</div>"
        "</section>"
    )


def movie_card_html(item: MovieItem, bucket: str, *, recent: bool = False, duplicate: bool = False) -> str:
    poster = poster_url_for(item)
    poster_html = (
        f"<img class='poster' loading='lazy' src='{html.escape(poster)}' alt=''>"
        if poster
        else "<div class='poster missing'></div>"
    )
    metadata = metadata_for(item)
    actors = metadata.get("actors") or []
    genres = genres_for(metadata)
    actor_text = ", ".join(actors) if actors else "No actor data available yet."
    genre_text = ", ".join(genres)
    display_title = metadata.get("title") or item.title
    year = str(metadata.get("year") or "")
    search_text = " ".join([item.title, display_title, metadata.get("year", ""), metadata.get("release_date", ""), actor_text, genre_text]).lower()
    try:
        rating = float(metadata.get("vote_average") or 0)
    except (TypeError, ValueError):
        rating = 0
    recent_attr = " data-recent='1'" if recent else ""
    duplicate_attr = " data-duplicate='1'" if duplicate else " data-duplicate='0'"
    return (
        f"<article class='movie-card' data-letter='{html.escape(bucket)}' data-title='{html.escape(search_text)}' data-rating='{rating:.3f}' data-genres='{html.escape(genre_text.lower())}'{recent_attr}{duplicate_attr}>"
        f"<a class='poster-link' href='/movie/{item.id}' aria-label='Show details for {html.escape(display_title)}'>"
        f"{poster_html}"
        f"</a>"
        f"<div class='movie-info'>"
        f"<div class='title'>{html.escape(display_title)}</div>"
        f"<div class='size'>{html.escape(year)}</div>"
        f"</div>"
        "</article>"
    )


def title_bucket(title: str) -> str:
    match = re.search(r"[A-Za-z0-9]", title)
    if not match:
        return "#"
    char = match.group(0).upper()
    return char if char.isalpha() else "0-9"


def safe_item(item_id: str) -> MovieItem:
    try:
        numeric_id = int(item_id)
    except ValueError:
        raise FileNotFoundError("Invalid movie id")
    item = movie_index.by_id.get(numeric_id)
    if not item:
        raise FileNotFoundError("Movie id not found")
    resolved = item.path.resolve()
    if not str(resolved).startswith(str(MOVIE_ROOT) + os.sep):
        raise PermissionError("Refusing path outside movie root")
    if not resolved.is_file():
        repaired = repair_missing_item(item)
        if not repaired:
            raise FileNotFoundError("Movie file missing")
        item = repaired
    return item


def repair_missing_item(item: MovieItem) -> MovieItem | None:
    target_keys = {
        normalize_name(item.title),
        normalize_name(item.path.stem),
        normalize_name(item.path.parent.name),
    }
    year_match = re.search(r"(19|20)\d{2}", item.title + " " + item.rel_path)
    find_cmd = ["find", str(MOVIE_ROOT), "-maxdepth", "2", "-type", "f"]
    if year_match:
        find_cmd.extend(["-iname", f"*{year_match.group(0)}*"])
    try:
        result = subprocess.run(find_cmd, check=True, text=True, stdout=subprocess.PIPE, timeout=90)
    except Exception:
        return None
    for line in result.stdout.splitlines():
        candidate = Path(line)
        if candidate.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        candidate_keys = {
            normalize_name(clean_title(candidate)),
            normalize_name(candidate.stem),
            normalize_name(candidate.parent.name),
        }
        if target_keys & candidate_keys:
            try:
                stat = candidate.stat()
                rel_path = str(candidate.relative_to(MOVIE_ROOT))
            except OSError:
                continue
            item.path = candidate
            item.rel_path = rel_path
            item.title = clean_title(candidate)
            item.size = stat.st_size
            item.modified = stat.st_mtime
            movie_index.by_id[item.id] = item
            return item
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "MediaDownloadLibrary/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/":
                return self.page()
            if parsed.path.startswith("/movie/fix-match/"):
                return self.fix_match_page(parsed.path.rsplit("/", 1)[-1], parsed)
            if parsed.path.startswith("/movie/apply-match/"):
                return self.apply_match(parsed.path.rsplit("/", 1)[-1], parsed)
            if parsed.path.startswith("/movie/"):
                return self.movie_detail(parsed.path.rsplit("/", 1)[-1])
            if parsed.path == "/api/movies":
                return self.api_movies()
            if parsed.path == "/api/reload-posters":
                load_poster_map()
                load_metadata_map()
                return self.json_response({"ok": True, "posters": len(poster_map), "metadata": len(metadata_map)})
            if parsed.path == "/api/refresh":
                movie_index.refresh_background()
                return self.json_response({"ok": True, "count": len(movie_index.items), "scanning": movie_index.scanning, "scanned_at": movie_index.scanned_at})
            if parsed.path.startswith("/posters/"):
                return self.serve_poster(parsed.path)
            if parsed.path.startswith("/play/"):
                return self.play(parsed.path.rsplit("/", 1)[-1])
            if parsed.path.startswith("/download/"):
                return self.download(parsed.path.rsplit("/", 1)[-1])
            self.send_error(404)
        except FileNotFoundError as exc:
            self.send_error(404, str(exc))
        except PermissionError as exc:
            self.send_error(403, str(exc))
        except BrokenPipeError:
            pass
        except Exception as exc:
            self.send_error(500, str(exc))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path.startswith("/movie/upload-art/"):
                return self.upload_art(parsed.path.rsplit("/", 1)[-1])
            self.send_error(404)
        except FileNotFoundError as exc:
            self.send_error(404, str(exc))
        except PermissionError as exc:
            self.send_error(403, str(exc))
        except BrokenPipeError:
            pass
        except Exception as exc:
            self.send_error(500, str(exc))

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path.startswith("/download/"):
                return self.download(parsed.path.rsplit("/", 1)[-1], head_only=True)
            if parsed.path.startswith("/play/"):
                return self.play(parsed.path.rsplit("/", 1)[-1], head_only=True)
            if parsed.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                return
            self.send_error(404)
        except FileNotFoundError as exc:
            self.send_error(404, str(exc))
        except PermissionError as exc:
            self.send_error(403, str(exc))
        except Exception as exc:
            self.send_error(500, str(exc))

    def json_response(self, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def api_movies(self):
        duplicates = duplicate_keys(movie_index.items)
        payload = {
            "root": str(MOVIE_ROOT),
            "source": "csv bootstrap with live find refresh" if movie_index.scanning else "live find scan",
            "count": len(movie_index.items),
            "scanned_at": movie_index.scanned_at,
            "scanning": movie_index.scanning,
            "error": movie_index.error,
            "movies": [
                {
                    "id": item.id,
                    "title": item.title,
                    "rel_path": item.rel_path,
                    "size": item.size,
                    "size_label": human_size(item.size),
                    "duplicate": duplicate_key(item) in duplicates,
                    "poster": poster_url_for(item),
                    "download": f"/download/{item.id}",
                }
                for item in movie_index.items
            ],
        }
        return self.json_response(payload)

    def page(self):
        buckets: dict[str, list[str]] = {}
        duplicates = duplicate_keys(movie_index.items)
        for item in movie_index.items:
            bucket = title_bucket(item.title)
            buckets.setdefault(bucket, []).append(movie_card_html(item, bucket, duplicate=duplicate_key(item) in duplicates))
        letters = ["0-9"] + [chr(code) for code in range(ord("A"), ord("Z") + 1)]
        sections = []
        recent_items = sorted(
            [item for item in movie_index.items if item.modified],
            key=lambda item: item.modified,
            reverse=True,
        )[:RECENT_SHELF_EXPANDED_LIMIT]
        recent_released_items = [
            item for item in recent_items
            if item.modified >= month_start_timestamp() and str(metadata_for(item).get("year") or "") == current_year_text()
        ]
        sections.append(
            recent_shelf_section(
                "section-recent",
                "Recently Added",
                [movie_card_html(item, "RECENT", recent=True, duplicate=duplicate_key(item) in duplicates) for item in recent_items],
                "movie-grid",
            )
        )
        sections.append(
            recent_shelf_section(
                "section-released",
                "Recently Released",
                [movie_card_html(item, "RECENT", recent=True, duplicate=duplicate_key(item) in duplicates) for item in recent_released_items],
                "movie-grid",
            )
        )
        for letter in letters:
            cards = buckets.get(letter, [])
            if not cards:
                continue
            sections.append(
                f"<section class='letter-section' id='section-{html.escape(letter)}' data-letter='{html.escape(letter)}'>"
                f"<h2>{html.escape(letter)}</h2>"
                f"<div class='movie-grid'>"
                f"{''.join(cards)}"
                f"</div>"
                "</section>"
            )
        letter_buttons = "\n".join(
            f"<button class='letter' data-letter='{letter}'>{letter}</button>"
            for letter in letters
        )
        body = (
            PAGE_TEMPLATE
            .replace("{{COUNT}}", str(len(movie_index.items)))
            .replace("{{SERVER_NAME}}", html.escape(SERVER_DISPLAY_NAME))
            .replace("{{BACKDROP_POSTERS}}", poster_backdrop_html(movie_index.items))
            .replace("{{SECTIONS}}", "\n".join(sections))
            .replace("{{LETTERS}}", letter_buttons)
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def movie_detail(self, item_id: str):
        item = safe_item(item_id)
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        detail_mode = (params.get("mode", [""])[0] or "").lower()
        if detail_mode not in {"direct", "hls"}:
            detail_mode = ""
        mode_query = f"?mode={detail_mode}&play=1" if detail_mode else "?play=1"
        current_user = getattr(self, "current_user", lambda: None)()
        admin_action = f'<a class="action" href="/admin/media/movie/{item.id}"><span>A</span>Admin</a>' if current_user and current_user["is_admin"] else ""
        poster = poster_url_for(item)
        metadata = metadata_for(item)
        display_title = metadata.get("title") or item.title
        release_label = release_label_for(metadata)
        rating_html = tmdb_score_html(metadata)
        genre_text = ", ".join(genres_for(metadata))
        overview = metadata.get("overview") or "No TMDb summary available yet."
        actors = metadata.get("actors") or []
        actor_items = "".join(
            f"<li><a href='/actor?name={urllib.parse.quote(str(actor))}'>{html.escape(str(actor))}</a></li>"
            for actor in actors
        ) or "<li>No actor data available yet.</li>"
        poster_html = (
            f"<img class='detail-poster' src='{html.escape(poster)}' alt=''>"
            if poster
            else "<div class='detail-poster missing'></div>"
        )
        background_style = f" style=\"--poster-bg:url('{html.escape(poster)}')\"" if poster else ""
        body = (
            DETAIL_TEMPLATE
            .replace("{{BACKGROUND_STYLE}}", background_style)
            .replace("{{TITLE}}", html.escape(display_title))
            .replace("{{ITEM_ID}}", str(item.id))
            .replace("{{YEAR}}", html.escape(release_label))
            .replace("{{GENRES}}", html.escape(genre_text))
            .replace("{{LIBRARY_TITLE}}", html.escape(item.title))
            .replace("{{POSTER}}", poster_html)
            .replace("{{SUMMARY}}", html.escape(overview))
            .replace("{{ACTORS}}", actor_items)
            .replace("{{TMDB_SCORE}}", rating_html)
            .replace("{{SIZE}}", human_size(item.size))
            .replace("{{ITEM_KEY}}", f"movie:{item.id}")
            .replace("{{PLAY}}", f"/player/movie/{item.id}{mode_query}")
            .replace("{{PLAY_DIRECT}}", f"/movie/{item.id}?mode=direct")
            .replace("{{PLAY_HLS}}", f"/movie/{item.id}?mode=hls")
            .replace("{{DIRECT_CLASS}}", "active" if detail_mode == "direct" else "")
            .replace("{{HLS_CLASS}}", "active" if detail_mode == "hls" else "")
            .replace("{{DOWNLOAD}}", f"/download/{item.id}")
            .replace("{{ADMIN_ACTION}}", admin_action)
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def fix_match_page(self, item_id: str, parsed):
        item = safe_item(item_id)
        params = urllib.parse.parse_qs(parsed.query)
        metadata = metadata_for(item)
        query = params.get("q", [metadata.get("title") or item.title])[0].strip()
        year = params.get("year", [str(metadata.get("year") or "")])[0].strip()
        candidates = tmdb_search_candidates(query, year)
        current_id = str(metadata.get("tmdb_id") or "")
        rows = []
        for candidate in candidates:
            tmdb_id = str(candidate.get("id") or "")
            title = candidate.get("title") or "Untitled"
            release_date = candidate.get("release_date") or ""
            candidate_year = release_date[:4]
            overview = candidate.get("overview") or "No summary available."
            poster = tmdb_image_url(candidate.get("poster_path") or "", "w342")
            poster_html = f"<img src='{html.escape(poster)}' alt=''>" if poster else "<div class='match-poster missing'>No Poster</div>"
            selected = tmdb_id == current_id
            rows.append(
                "<article class='match-row'>"
                f"<a class='match-poster' href='/movie/apply-match/{item.id}?tmdb_id={html.escape(tmdb_id)}'>{poster_html}</a>"
                "<div class='match-copy'>"
                f"<h2>{html.escape(title)} <span>- ({html.escape(candidate_year)})</span></h2>"
                f"<p>{html.escape(overview)}</p>"
                f"<a class='apply' href='/movie/apply-match/{item.id}?tmdb_id={html.escape(tmdb_id)}'>{'Current Match' if selected else 'Use This Match'}</a>"
                "</div>"
                f"<div class='check'>{'✓' if selected else ''}</div>"
                "</article>"
            )
        body = (
            FIX_MATCH_TEMPLATE
            .replace("{{ITEM_ID}}", str(item.id))
            .replace("{{TITLE}}", html.escape(metadata.get("title") or item.title))
            .replace("{{QUERY}}", html.escape(query))
            .replace("{{YEAR}}", html.escape(year))
            .replace("{{ROWS}}", "".join(rows) or "<p class='empty'>No TMDB matches found.</p>")
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def apply_match(self, item_id: str, parsed):
        try:
            item = safe_item(item_id)
            tmdb_id = int(urllib.parse.parse_qs(parsed.query).get("tmdb_id", [""])[0])
        except Exception:
            raise FileNotFoundError("Invalid match request")
        apply_tmdb_match(item, tmdb_id)
        self.send_response(302)
        self.send_header("Location", f"/movie/{item.id}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def upload_art(self, item_id: str):
        item = safe_item(item_id)
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0 or content_length > 22 * 1024 * 1024:
            raise ValueError("Invalid upload size")
        body = self.rfile.read(content_length)
        _fields, files = parse_multipart_form(self.headers.get("Content-Type", ""), body)
        filename, data, mime = files.get("art_file", ("", b"", ""))
        save_uploaded_art(item, filename, data, mime)
        self.send_response(302)
        self.send_header("Location", f"/movie/{item.id}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def download(self, item_id: str, head_only: bool = False):
        return self.serve_media(item_id, disposition="attachment", head_only=head_only)

    def play(self, item_id: str, head_only: bool = False):
        return self.serve_media(item_id, disposition="inline", head_only=head_only)

    def serve_media(self, item_id: str, disposition: str, head_only: bool = False):
        item = safe_item(item_id)
        file_size = item.path.stat().st_size
        start = 0
        end = file_size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header:
            match = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if match:
                if match.group(1):
                    start = int(match.group(1))
                if match.group(2):
                    end = int(match.group(2))
                end = min(end, file_size - 1)
                status = 206

        length = max(0, end - start + 1)
        mime = mimetypes.guess_type(item.path.name)[0] or "application/octet-stream"
        encoded_name = urllib.parse.quote(item.path.name)
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{encoded_name}")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()
        if head_only:
            return

        with item.path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def serve_poster(self, path: str):
        name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
        poster = (POSTER_DIR / name).resolve()
        if not str(poster).startswith(str(POSTER_DIR) + os.sep):
            raise PermissionError("Refusing path outside poster root")
        if not poster.is_file():
            raise FileNotFoundError("Poster missing")
        data = poster.read_bytes()
        mime = mimetypes.guess_type(poster.name)[0] or "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Movies</title>
  <style>
    :root { color-scheme: dark; --bg:#11151b; --panel:#191f28; --tile:#0d1117; --line:#2b3440; --text:#f4f7fb; --muted:#a8b2bf; --accent:#28a2ff; --accent2:#f5a524; }
    * { box-sizing: border-box; }
    html { scroll-behavior:smooth; }
    body { margin:0; font-family: Arial, Helvetica, sans-serif; background:var(--bg); color:var(--text); min-height:100vh; overflow-x:hidden; }
    .library-backdrop { position:fixed; inset:-90px -160px auto -160px; height:calc(100vh + 190px); z-index:0; display:grid; grid-template-columns:repeat(15, minmax(58px, 1fr)); gap:10px; transform:rotate(-10deg) translateY(-34px); opacity:.34; pointer-events:none; overflow:hidden; filter:saturate(1.12) contrast(1.04); }
    .library-backdrop::after { content:""; position:absolute; inset:-20px; background:linear-gradient(180deg,rgba(17,21,27,.58) 0%,rgba(17,21,27,.76) 52%,rgba(17,21,27,.96) 100%),linear-gradient(90deg,rgba(17,21,27,.82),rgba(17,21,27,.18),rgba(17,21,27,.82)); }
    .library-backdrop img { width:100%; aspect-ratio:2/3; object-fit:cover; border-radius:10px; box-shadow:0 18px 36px rgba(0,0,0,.55); }
    .library-backdrop img:nth-child(3n) { transform:translateY(34px); }
    .library-backdrop img:nth-child(4n) { transform:translateY(-22px); }
    .library-backdrop img:nth-child(5n) { transform:translateY(56px); }
    header { position:sticky; top:0; z-index:4; background:rgba(17,21,27,.96); backdrop-filter:blur(10px); border-bottom:1px solid var(--line); padding:22px 22px 14px; }
    .top-row { display:flex; align-items:flex-start; justify-content:space-between; gap:18px; }
    .title-stack { display:flex; flex-direction:column; align-items:flex-start; gap:8px; min-width:0; }
    .library-trigger { display:inline-grid; gap:2px; border:0; background:transparent; color:var(--text); padding:0; text-align:left; cursor:pointer; }
    .library-trigger h1::after { content:"⌄"; margin-left:8px; color:var(--muted); font-size:18px; }
    .header-actions { display:flex; align-items:center; gap:10px; flex-wrap:nowrap; justify-content:flex-end; }
    .home-link { display:inline-flex; align-items:center; justify-content:center; min-height:38px; padding:0 14px; border-radius:999px; background:rgba(255,255,255,.10); border:1px solid var(--line); color:#fff; text-decoration:none; font-weight:800; }
    .search-button { min-width:38px; min-height:38px; border-radius:999px; background:rgba(255,255,255,.08); border:1px solid var(--line); color:#fff; font-size:18px; line-height:1; padding:0 12px; }
    .search-button.active { background:var(--accent); border-color:var(--accent); color:#06111c; }
    .playback-toggle { min-height:38px; min-width:72px; display:inline-flex; align-items:center; justify-content:center; padding:0 12px; border-radius:999px; border:1px solid var(--line); background:rgba(255,255,255,.08); color:#fff; font-size:12px; font-weight:900; cursor:pointer; }
    .playback-toggle.hls { color:#06111c; background:var(--accent2); border-color:var(--accent2); }
    .duplicate-check { min-height:38px; display:inline-flex; align-items:center; gap:8px; padding:0 12px; border-radius:999px; background:rgba(255,255,255,.08); border:1px solid var(--line); color:#fff; font-weight:800; cursor:pointer; user-select:none; }
    .duplicate-check input { width:16px; height:16px; padding:0; margin:0; accent-color:var(--accent2); box-shadow:none; cursor:pointer; }
    .modal { position:fixed; inset:0; z-index:20; display:none; align-items:end; background:rgba(8,10,15,.72); }
    .modal.open { display:flex; }
    .sheet { width:100%; max-width:640px; margin:0 auto; background:#050506; border-radius:22px 22px 0 0; padding:18px 24px 24px; box-shadow:0 -20px 70px rgba(0,0,0,.55); }
    .sheet h2 { margin:18px 0 18px; text-align:center; font-size:22px; }
    .sheet-list { border-top:1px solid var(--line); border-bottom:1px solid var(--line); padding:8px 0; }
    .sheet-item { display:grid; grid-template-columns:44px 1fr auto; align-items:center; gap:14px; min-height:78px; color:#fff; text-decoration:none; }
    .sheet-icon { color:#e7ecf4; font-size:26px; }
    .sheet-title { display:block; font-size:22px; font-weight:800; }
    .sheet-sub { display:block; color:var(--muted); margin-top:3px; font-size:15px; }
    .sheet-more { color:var(--muted); font-size:26px; }
    .sheet-actions { display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-top:24px; }
    .sheet-actions a,.sheet-actions button { min-height:52px; border:0; border-radius:999px; background:#1b1b1d; color:#fff; text-decoration:none; font-size:18px; font-weight:800; display:grid; place-items:center; }
    h1 { margin:0; font-size:24px; letter-spacing:0; }
    .meta { color:var(--muted); margin-top:4px; font-size:13px; }
    .bar { display:none; grid-template-columns:minmax(240px, 560px) minmax(160px, 220px); gap:12px; margin-top:12px; align-items:center; }
    body.search-open .bar { display:grid; }
    input { width:100%; padding:13px 14px; border:1px solid #3a4552; border-radius:8px; background:#252b34; color:var(--text); font-size:16px; outline:none; }
    input:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(40,162,255,.16); }
    select { width:100%; padding:13px 14px; border:1px solid #3a4552; border-radius:8px; background:#252b34; color:var(--text); font-size:16px; outline:none; }
    select:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(40,162,255,.16); }
    button { border:1px solid var(--accent); border-radius:8px; background:var(--accent); color:#06111c; font-weight:800; padding:11px 13px; cursor:pointer; white-space:nowrap; }
    .page-shell { position:relative; z-index:1; display:grid; grid-template-columns:minmax(0, 1fr); gap:14px; padding:18px 16px 44px 22px; }
    body.search-open .page-shell { grid-template-columns:minmax(0, 1fr) 50px; }
    main { min-width:0; }
    .letter-rail { position:sticky; top:112px; align-self:start; display:none; flex-direction:column; gap:4px; max-height:calc(100vh - 126px); overflow:auto; padding:6px 4px; background:rgba(25,31,40,.72); border:1px solid var(--line); border-radius:8px; }
    body.search-open .letter-rail { display:flex; }
    .letter { width:34px; min-height:28px; padding:0; border-color:transparent; background:transparent; color:#cdd7e3; border-radius:6px; font-size:12px; }
    .letter.active { border-color:var(--accent); background:var(--accent); color:#06111c; }
    .letter-section { scroll-margin-top:128px; margin-bottom:30px; }
    .letter-section h2 { margin:0 0 12px; color:#d7e4f0; font-size:17px; border-bottom:1px solid var(--line); padding-bottom:8px; }
    .movie-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(106px, 1fr)); gap:16px 13px; align-items:start; }
    .shelf-row { display:flex; gap:14px; overflow-x:auto; overscroll-behavior-inline:contain; scroll-snap-type:x proximity; padding:2px 2px 10px; }
    .shelf-row .movie-card { flex:0 0 106px; scroll-snap-align:start; }
    .shelf-extra { flex:0 0 106px; }
    .shelf-extra .movie-card { width:100%; }
    .see-all-card { flex:0 0 106px; aspect-ratio:2/3; display:grid; place-items:center; align-content:center; gap:6px; border:1px solid var(--line); border-radius:6px; background:rgba(255,255,255,.08); color:#fff; box-shadow:0 10px 22px rgba(0,0,0,.24); }
    .see-all-card span { font-size:15px; font-weight:900; }
    .see-all-card small { color:var(--muted); font-size:11px; }
    .movie-card { min-width:0; }
    .movie-card[data-duplicate="1"] .poster-link { outline:2px solid rgba(245,165,36,.72); outline-offset:3px; }
    .poster-link { display:block; width:100%; position:relative; aspect-ratio:2 / 3; background:#080b10; border:1px solid #222b36; border-radius:6px; overflow:hidden; box-shadow:0 10px 22px rgba(0,0,0,.34); text-decoration:none; }
    .poster-link:focus-visible { outline:3px solid var(--accent); outline-offset:3px; }
    .poster { width:100%; height:100%; object-fit:cover; display:block; background:#05070a; }
    .poster.missing { width:100%; height:100%; display:block; background:linear-gradient(145deg,#202833,#090d12); }
    .poster.missing::after { content:"No Poster"; display:grid; place-items:center; width:100%; height:100%; color:#8290a0; font-size:12px; font-weight:800; }
    .movie-info { padding:8px 2px 0; }
    .title { font-weight:800; color:#fff; font-size:13px; line-height:1.25; overflow-wrap:anywhere; }
    .size { margin-top:4px; white-space:nowrap; color:#aeb9c6; font-size:12px; }
    .hidden { display:none; }
    @media (min-width: 1200px) {
      .movie-grid { grid-template-columns:repeat(auto-fill, minmax(120px, 1fr)); gap:18px 14px; }
      .shelf-row .movie-card, .shelf-extra, .see-all-card { flex-basis:120px; }
    }
    @media (min-width: 900px) {
      header { padding:18px 16px 10px; }
      .top-row { gap:12px; }
      .header-actions { gap:8px; }
      .home-link, .duplicate-check, button, .playback-toggle { min-height:22px; padding:0 7px; font-size:9px; border-radius:12px; }
      .search-button { min-width:22px; min-height:22px; font-size:10px; padding:0 7px; }
      .duplicate-check { gap:4px; }
      .duplicate-check input { width:9px; height:9px; }
      h1 { font-size:13px; }
      .library-trigger h1::after { font-size:9px; margin-left:4px; }
      .meta { font-size:8px; margin-top:1px; }
      .bar { grid-template-columns:minmax(240px, 520px) minmax(130px, 190px); gap:10px; margin-top:10px; }
      input, select { padding:6px 8px; font-size:9px; border-radius:5px; }
      .page-shell { grid-template-columns:minmax(0, 1fr); gap:10px; padding:14px 12px 36px 16px; }
      body.search-open .page-shell { grid-template-columns:minmax(0, 1fr) 38px; }
      .letter-rail { top:92px; max-height:calc(100vh - 104px); gap:3px; padding:5px 3px; }
      .letter { width:21px; min-height:18px; font-size:8px; border-radius:4px; }
      .letter-section { scroll-margin-top:104px; margin-bottom:24px; }
      .letter-section h2 { font-size:10px; margin-bottom:7px; padding-bottom:5px; }
      .movie-grid { grid-template-columns:repeat(auto-fill, minmax(72px, 72px)); justify-content:start; gap:10px 9px; }
      .shelf-row { gap:9px; padding-bottom:7px; }
      .shelf-row .movie-card, .shelf-extra, .see-all-card { flex-basis:72px; }
      .poster-link { border-radius:4px; box-shadow:0 7px 16px rgba(0,0,0,.30); }
      .movie-info { padding-top:5px; }
      .title { font-size:6px; line-height:1.12; }
      .size { margin-top:1px; font-size:5px; }
      .poster.missing::after { font-size:6px; }
      .see-all-card span { font-size:8px; }
      .see-all-card small { font-size:6px; }
      .library-backdrop { height:470px; inset:-58px -190px auto -170px; grid-template-columns:repeat(11, minmax(58px,1fr)); opacity:.30; gap:8px; }
    }
    @media (max-width: 760px) {
      header { padding:24px 60px 12px 12px; }
      h1 { font-size:21px; }
      .bar { grid-template-columns:1fr; }
      .page-shell { grid-template-columns:minmax(0, 1fr); gap:8px; padding:12px 8px 34px 12px; }
      body.search-open .page-shell { grid-template-columns:minmax(0, 1fr) 42px; }
      .letter-rail { top:118px; max-height:calc(100vh - 130px); }
      .letter { width:28px; min-height:24px; font-size:11px; }
      .movie-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); gap:14px 10px; }
      .shelf-row .movie-card, .shelf-extra, .see-all-card { flex-basis:104px; }
      .title { font-size:14px; }
    }
    @media (max-width: 420px) {
      .movie-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <div class="library-backdrop" aria-hidden="true">{{BACKDROP_POSTERS}}</div>
  <header>
    <div class="top-row">
      <div class="title-stack"><button class="library-trigger" id="libraryTrigger"><h1>Movies</h1><span class="meta">{{SERVER_NAME}}</span></button><label class="duplicate-check"><input id="duplicateToggle" type="checkbox">Duplicates</label></div>
      <div class="header-actions"><a class="home-link" href="/">Home</a><button class="playback-toggle" id="playbackModeButton" type="button" data-mode="direct">Direct</button><button class="search-button" id="searchToggle" type="button" aria-label="Search" title="Search">⌕</button></div>
    </div>
    <div class="meta"><span id="visibleCount">{{COUNT}}</span> of {{COUNT}} movie files</div>
    <div class="bar">
      <input id="search" type="search" placeholder="Search movies, actors, or genres" autofocus>
      <select id="sortMode" aria-label="Sort movies">
        <option value="title">A to Z</option>
        <option value="rating">Highest rated</option>
      </select>
    </div>
  </header>
  <div class="modal" id="libraryModal" aria-hidden="true">
    <div class="sheet" role="dialog" aria-modal="true" aria-label="Favorite Libraries">
      <h2>Favorite Libraries</h2>
      <div class="sheet-list">
        <a class="sheet-item" href="/movies"><span class="sheet-icon">▥</span><span><span class="sheet-title">Movies</span><span class="sheet-sub">{{SERVER_NAME}}</span></span><span class="sheet-more">⋮</span></a>
        <a class="sheet-item" href="/tv"><span class="sheet-icon">▣</span><span><span class="sheet-title">TV Shows</span><span class="sheet-sub">{{SERVER_NAME}}</span></span><span class="sheet-more">⋮</span></a>
      </div>
      <div class="sheet-actions"><button id="closeLibraries">Close</button><a href="/">Home</a></div>
    </div>
  </div>
  <div class="page-shell">
    <main id="top">
      <section class="letter-section hidden" id="section-rating" data-letter="RATING">
        <h2>Highest Rated</h2>
        <div class="movie-grid" id="ratingGrid"></div>
      </section>
      {{SECTIONS}}
    </main>
    <nav class="letter-rail" aria-label="Jump to movie letter">
      <button class="letter active" data-letter="ALL">All</button>
      {{LETTERS}}
    </nav>
  </div>
  <script>
    const search = document.getElementById("search");
    const searchToggle = document.getElementById("searchToggle");
    const sortMode = document.getElementById("sortMode");
    const duplicateToggle = document.getElementById("duplicateToggle");
    const libraryModal = document.getElementById("libraryModal");
    document.getElementById("libraryTrigger").addEventListener("click", () => libraryModal.classList.add("open"));
    document.getElementById("closeLibraries").addEventListener("click", () => libraryModal.classList.remove("open"));
    libraryModal.addEventListener("click", (event) => { if (event.target === libraryModal) libraryModal.classList.remove("open"); });
    const visibleCount = document.getElementById("visibleCount");
    const cards = [...document.querySelectorAll(".movie-card")];
    const normalCards = cards.filter((card) => card.dataset.recent !== "1");
    const sections = [...document.querySelectorAll(".letter-section")];
    const ratingSection = document.getElementById("section-rating");
    const ratingGrid = document.getElementById("ratingGrid");
    const originalPositions = new Map(normalCards.map((card) => [card, { parent: card.parentNode, next: card.nextSibling }]));
    let activeLetter = "ALL";
    let duplicateOnly = false;
    function restoreCards() {
      for (const card of normalCards) {
        const original = originalPositions.get(card);
        if (original && card.parentNode !== original.parent) {
          original.parent.insertBefore(card, original.next);
        }
      }
    }
    function applySortMode() {
      if (sortMode.value !== "rating") {
        restoreCards();
        ratingSection.classList.add("hidden");
        return;
      }
      const sorted = [...normalCards].sort((a, b) => Number(b.dataset.rating || 0) - Number(a.dataset.rating || 0) || a.dataset.title.localeCompare(b.dataset.title));
      for (const card of sorted) ratingGrid.appendChild(card);
      ratingSection.classList.remove("hidden");
    }
    function applyFilters() {
      applySortMode();
      const q = search.value.trim().toLowerCase();
      let count = 0;
      const scopedCards = sortMode.value === "rating" ? normalCards : cards;
      for (const card of scopedCards) {
        const matchesSearch = !q || card.dataset.title.includes(q);
        const matchesDuplicate = !duplicateOnly || card.dataset.duplicate === "1";
        const show = matchesSearch && matchesDuplicate;
        card.classList.toggle("hidden", !show);
        if (show && card.dataset.recent !== "1") count++;
      }
      for (const section of sections) {
        if (sortMode.value === "rating" && section !== ratingSection) {
          section.classList.add("hidden");
          continue;
        }
        if (sortMode.value !== "rating" && section === ratingSection) {
          section.classList.add("hidden");
          continue;
        }
        const visibleCards = section.querySelectorAll(".movie-card:not(.hidden)").length;
        section.classList.toggle("hidden", visibleCards === 0);
      }
      visibleCount.textContent = count;
    }
    function setSearchOpen(open) {
      document.body.classList.toggle("search-open", open);
      searchToggle.classList.toggle("active", open);
      if (open) search.focus();
    }
    searchToggle.addEventListener("click", () => setSearchOpen(!document.body.classList.contains("search-open")));
    search.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !search.value.trim()) setSearchOpen(false);
    });
    search.addEventListener("input", applyFilters);
    sortMode.addEventListener("change", applyFilters);
    document.querySelectorAll("[data-shelf-target]").forEach((button) => {
      button.addEventListener("click", () => {
        const shelf = document.getElementById(button.dataset.shelfTarget);
        if (!shelf) return;
        shelf.querySelectorAll(".shelf-extra").forEach((item) => item.classList.remove("hidden"));
        button.remove();
        applyFilters();
      });
    });
    duplicateToggle.addEventListener("change", () => {
      duplicateOnly = duplicateToggle.checked;
      applyFilters();
    });
    document.querySelectorAll(".letter").forEach((button) => {
      button.addEventListener("click", () => {
        activeLetter = button.dataset.letter;
        document.querySelectorAll(".letter").forEach((item) => item.classList.toggle("active", item === button));
        const target = activeLetter === "ALL" ? document.getElementById("top") : document.getElementById(`section-${activeLetter}`);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    const playbackModeButton = document.getElementById("playbackModeButton");
    function setPlaybackModeButton(mode) {
      if (!playbackModeButton) return;
      const normalized = mode === "hls" ? "hls" : "direct";
      playbackModeButton.dataset.mode = normalized;
      playbackModeButton.textContent = normalized === "hls" ? "HLS" : "Direct";
      playbackModeButton.classList.toggle("hls", normalized === "hls");
    }
    if (playbackModeButton) {
      fetch("/api/playback-mode").then(r => r.json()).then(p => setPlaybackModeButton(p.mode)).catch(() => {});
      playbackModeButton.addEventListener("click", async () => {
        const nextMode = playbackModeButton.dataset.mode === "hls" ? "direct" : "hls";
        playbackModeButton.disabled = true;
        try {
          const response = await fetch("/api/playback-mode", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({mode:nextMode})});
          const payload = await response.json();
          if (!payload.ok) throw new Error(payload.error || "mode update failed");
          setPlaybackModeButton(payload.mode);
        } finally {
          playbackModeButton.disabled = false;
        }
      });
    }
    const refreshButton = document.getElementById("refresh");
    if (refreshButton) {
      refreshButton.addEventListener("click", async () => {
        await fetch("/api/refresh");
        location.reload();
      });
    }
    async function reloadAfterLiveScan() {
      try {
        const response = await fetch("/api/movies");
        const payload = await response.json();
        if (!payload.scanning && payload.source === "live find scan" && {{COUNT}} > 0) {
          const key = `live-scan-${payload.count}-${payload.scanned_at}`;
          if (sessionStorage.getItem("movie-library-live-scan") !== key) {
            sessionStorage.setItem("movie-library-live-scan", key);
            location.reload();
          }
        }
      } catch {}
    }
    setInterval(reloadAfterLiveScan, 15000);
  </script>
</body>
</html>
"""


DETAIL_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{TITLE}}</title>
  <style>
    :root { color-scheme: dark; --bg:#070a0f; --text:#f7fbff; --muted:#c9d2df; --soft:rgba(255,255,255,.16); --line:rgba(255,255,255,.18); --green:#2ee66b; --gold:#f5a524; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; font-family:Arial, Helvetica, sans-serif; background:var(--bg); color:var(--text); }
    .detail-page { position:relative; min-height:100vh; overflow:hidden; padding:22px clamp(22px,4vw,56px); }
    .detail-page::before { content:""; position:fixed; inset:-26px; background-image:var(--poster-bg); background-size:cover; background-position:center top; opacity:.66; filter:blur(7px) saturate(1.25); transform:scale(1.08); }
    .detail-page::after { content:""; position:fixed; inset:0; background:linear-gradient(180deg,rgba(0,0,0,.12) 0%,rgba(0,0,0,.38) 28%,rgba(18,34,13,.82) 100%), linear-gradient(90deg,rgba(0,0,0,.88) 0%,rgba(0,0,0,.50) 48%,rgba(0,0,0,.76) 100%); }
    .topbar { position:relative; z-index:2; display:flex; justify-content:space-between; align-items:center; max-width:1220px; margin:0 auto; }
    .circle { width:46px; height:46px; display:grid; place-items:center; border-radius:50%; background:rgba(8,12,18,.48); border:1px solid rgba(255,255,255,.12); color:#fff; text-decoration:none; font-size:25px; backdrop-filter:blur(10px); }
    .circle svg { width:24px; height:24px; display:block; }
    .topbar-actions { display:flex; align-items:center; gap:10px; }
    .cast-button { cursor:default; }
    .detail-shell { position:relative; z-index:1; max-width:1220px; min-height:calc(100vh - 80px); margin:0 auto; display:grid; grid-template-columns:minmax(0, 640px) minmax(260px, 380px); gap:46px; align-items:center; padding:22px 0 34px; }
    .poster-wrap { display:flex; justify-content:center; order:2; opacity:.98; }
    .detail-poster { width:min(350px, 100%); aspect-ratio:2/3; object-fit:cover; border-radius:18px; border:1px solid var(--line); box-shadow:0 30px 80px rgba(0,0,0,.68); background:#05070a; }
    .detail-poster.missing { display:grid; place-items:center; background:linear-gradient(145deg,#202833,#090d12); }
    .detail-poster.missing::after { content:"No Poster"; color:#8390a0; font-weight:800; }
    .detail-info { color:#fff; order:1; max-width:660px; }
    h1 { margin:0; text-align:left; font-size:clamp(44px, 6.6vw, 84px); line-height:.94; letter-spacing:0; text-shadow:0 7px 26px rgba(0,0,0,.6); max-width:650px; }
    .meta-row { display:flex; flex-wrap:wrap; gap:10px 14px; align-items:center; margin:16px 0 16px; color:#edf4ff; font-size:17px; }
    .rating { display:inline-flex; align-items:center; min-height:32px; padding:0 10px; border-radius:11px; background:#05070a; color:#fff; font-weight:900; }
    .score-row { display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin:0 0 26px; color:#fff; }
    .score { display:inline-flex; align-items:center; gap:6px; min-height:34px; padding:0 10px; border-radius:999px; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.08); font-weight:800; }
    .score-badge { color:#8ed4ff; font-size:11px; font-weight:900; letter-spacing:.02em; }
    .playbar { display:grid; grid-template-columns:minmax(210px, 400px) 44px; gap:10px; align-items:center; margin:10px 0 20px; }
    .play { min-height:50px; display:flex; align-items:center; justify-content:center; border-radius:999px; background:#fff; color:#111; text-decoration:none; font-size:20px; font-weight:900; box-shadow:0 16px 40px rgba(0,0,0,.28); }
    .quick-play { width:44px; height:44px; display:grid; place-items:center; border-radius:50%; background:rgba(122,54,70,.50); border:1px solid rgba(255,255,255,.12); color:#fff; text-decoration:none; font-size:0; position:relative; }
    .quick-play::before { content:"\\21BB"; font-size:23px; line-height:1; transform:translateX(-2px); }
    .quick-play::after { content:"\\25B6"; position:absolute; font-size:11px; line-height:1; transform:translate(5px,1px); }
    .play-mode-row { display:flex; gap:8px; flex-wrap:wrap; margin:-8px 0 18px; }
    .play-mode-row a { min-height:30px; display:inline-flex; align-items:center; justify-content:center; padding:0 11px; border-radius:999px; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.14); color:#fff; text-decoration:none; font-size:12px; font-weight:900; }
    .play-mode-row a.active { color:#071018; background:#fff; border-color:#fff; }
    .play-mode-row a.hls.active { background:var(--gold); border-color:var(--gold); }
    .mobile-download-row { display:flex; flex-wrap:wrap; align-items:center; justify-content:center; gap:8px 10px; margin:-4px 0 18px; }
    .mobile-toggle { display:inline-flex; align-items:center; gap:7px; min-height:34px; border-radius:999px; padding:6px 11px; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.14); color:#fff; font-size:12px; font-weight:900; }
    .mobile-toggle input { width:16px; height:16px; accent-color:var(--gold); }
    .mobile-ready { display:none; color:#04110a; background:#26e86b; border-radius:999px; padding:8px 12px; text-decoration:none; font-size:12px; font-weight:900; }
    .mobile-ready.ready { display:inline-flex; }
    .mobile-status { color:rgba(239,245,255,.78); font-size:12px; min-height:16px; }
    .action-row { display:grid; grid-template-columns:repeat(5, minmax(76px,100px)); gap:14px; margin:0 0 26px; }
    .action { color:#dfe8f4; text-align:center; text-decoration:none; font-size:12px; line-height:1.25; }
    .action span { width:50px; height:50px; display:grid; place-items:center; margin:0 auto 7px; border-radius:50%; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.10); font-size:21px; }
    .download span, .mark-watched span { background:rgba(255,255,255,.10); border:3px solid rgba(255,255,255,.92); color:#fff; font-size:25px; }
    .download span { font-size:27px; }
    .summary { max-width:760px; color:#f2f5fa; line-height:1.5; font-size:18px; margin:0 0 16px; text-shadow:0 2px 14px rgba(0,0,0,.42); }
    .cast-panel { display:none; margin-top:18px; }
    .cast-panel.open { display:block; }
    .more-toggle.active span { background:rgba(245,183,63,.28); border-color:rgba(245,183,63,.56); }
    .library-title { color:rgba(239,245,255,.68); margin:0 0 22px; font-size:13px; }
    .file-grid { display:grid; grid-template-columns:140px 1fr; gap:12px 22px; max-width:520px; margin:24px 0; color:#fff; font-size:17px; }
    .file-grid .label { color:rgba(236,243,255,.72); }
    h2 { margin:26px 0 14px; font-size:24px; color:#fff; }
    ul { display:flex; flex-wrap:wrap; gap:10px; padding:0; margin:0; list-style:none; }
    li { border:1px solid rgba(255,255,255,.16); background:rgba(255,255,255,.11); border-radius:999px; padding:9px 12px; color:#eef5ff; font-size:14px; }
    @media (min-width: 1080px) {
      .poster-wrap { transform:translateY(20px); }
    }
    @media (max-width: 860px) {
      .detail-page { min-height:100svh; padding:18px 22px 30px; overflow:auto; }
      .detail-page::before { background-position:center top; opacity:.66; filter:blur(7px) saturate(1.25); }
      .detail-page::after { background:linear-gradient(180deg,rgba(0,0,0,.12) 0%,rgba(0,0,0,.38) 28%,rgba(18,34,13,.82) 100%), linear-gradient(90deg,rgba(0,0,0,.88) 0%,rgba(0,0,0,.50) 48%,rgba(0,0,0,.76) 100%); }
      .topbar { margin-bottom:18px; }
      .circle { width:46px; height:46px; }
      .detail-shell { min-height:auto; grid-template-columns:1fr; gap:18px; padding:0; }
      .poster-wrap { order:1; min-height:230px; align-items:end; }
      .detail-poster { width:min(280px, 62vw); border-radius:16px; }
      .detail-info { order:2; text-align:left; max-width:none; }
      h1 { text-align:center; font-size:clamp(42px, 12vw, 70px); }
      .meta-row, .score-row { justify-content:center; }
      .playbar { grid-template-columns:1fr auto; }
      .action-row { grid-template-columns:repeat(5, minmax(64px,1fr)); gap:10px; }
      .action span { width:50px; height:50px; }
      .summary { font-size:17px; }
    }
    @media (max-width: 520px) {
      .detail-page { padding:14px 20px 28px; }
      .poster-wrap { min-height:180px; }
      .detail-poster { width:min(210px, 54vw); }
      .playbar { grid-template-columns:1fr 42px; gap:9px; }
      .play { min-height:46px; font-size:18px; }
      .quick-play { width:42px; height:42px; }
      .action-row { grid-template-columns:repeat(5, 1fr); }
      .action { font-size:12px; }
      .action span { width:46px; height:46px; font-size:20px; }
      .download span, .mark-watched span { width:46px; height:46px; font-size:24px; border-width:3px; }
      .file-grid { grid-template-columns:120px 1fr; font-size:16px; }
      .meta-row { font-size:15px; gap:8px 12px; }
      .score-row { gap:8px; }
      .score { min-height:30px; font-size:13px; }
    }
  </style>
</head>
<body>
  <main class="detail-page"{{BACKGROUND_STYLE}}>
    <div class="topbar">
      <a class="circle" href="/movies" aria-label="Back to movies">←</a>
      <div class="topbar-actions"><button class="circle cast-button" type="button" aria-label="Cast" title="Cast"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 10V7.5C5 6.1 6.1 5 7.5 5h17C25.9 5 27 6.1 27 7.5v17c0 1.4-1.1 2.5-2.5 2.5H22" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><path d="M5 21c3.3 0 6 2.7 6 6M5 15c6.6 0 12 5.4 12 12M5 27h.1" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round"/></svg></button><a class="circle" href="/" aria-label="Home" title="Home"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 15.5 16 6l11 9.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><path d="M8.5 14.5V27h15V14.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/><path d="M13 27v-8h6v8" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/></svg></a></div>
    </div>
    <div class="detail-shell">
      <div class="poster-wrap">{{POSTER}}</div>
      <section class="detail-info">
        <h1>{{TITLE}}</h1>
        <div class="meta-row"><span>{{YEAR}}</span><span>{{SIZE}}</span><span>Movie</span><span>{{GENRES}}</span><span class="rating">Local</span></div>
        <div class="score-row"><span class="score">CineVault</span><span class="score">Ready to play</span><span class="score">Summary details</span>{{TMDB_SCORE}}</div>
        <div class="playbar">
          <a class="play" id="playLink" href="{{PLAY}}">▶ Play</a>
          <a class="quick-play" href="{{PLAY}}" aria-label="Start from beginning">Start over</a>
        </div>
        <div class="play-mode-row"><a class="{{DIRECT_CLASS}}" href="{{PLAY_DIRECT}}">Direct</a><a class="hls {{HLS_CLASS}}" href="{{PLAY_HLS}}">HLS</a></div>
        <div class="mobile-download-row" data-mobile-scope="movie" data-mobile-item="{{ITEM_ID}}">
          <label class="mobile-toggle"><input type="checkbox" data-mobile-toggle> Compress download</label>
          <a class="mobile-ready" data-mobile-ready href="#">Download mobile copy</a>
          <span class="mobile-status" data-mobile-status></span>
        </div>
        <div class="action-row">
          <a class="action mark-watched" href="/movies"><span>✓</span>Mark Watched</a>
          <a class="action download" href="{{DOWNLOAD}}"><span>↓</span>Download</a>
          <a class="action" href="/movie/fix-match/{{ITEM_ID}}"><span>⌕</span>Fix Match</a>
          <a class="action more-toggle" href="#cast" data-more-toggle="1"><span>⋮</span>More</a>
          {{ADMIN_ACTION}}
        </div>
        <p class="summary">{{SUMMARY}}</p>
        <div class="library-title">{{LIBRARY_TITLE}}</div>
        <div class="file-grid">
          <div class="label">Video</div><div>Local file stream</div>
          <div class="label">Audio</div><div>Original audio</div>
          <div class="label">Subtitles</div><div>Off</div>
          <div class="label">Size</div><div>{{SIZE}}</div>
        </div>
        <section class="cast-panel" id="cast">
          <h2>Cast & Crew ›</h2>
          <ul>{{ACTORS}}</ul>
        </section>
      </section>
    </div>
  </main>
  <script>
    document.querySelectorAll("[data-more-toggle]").forEach(link => {
      link.addEventListener("click", event => {
        event.preventDefault();
        const panel = document.getElementById("cast");
        if (!panel) return;
        const isOpen = panel.classList.toggle("open");
        link.classList.toggle("active", isOpen);
        if (isOpen) panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    });
    const itemKey = "{{ITEM_KEY}}";
    const playLink = document.getElementById("playLink");
    function formatRemaining(seconds) {
      const minutes = Math.max(1, Math.round(seconds / 60));
      return `Resume - ${minutes}m left`;
    }
    async function refreshWatchState() {
      if (!itemKey || !playLink) return;
      try {
        const response = await fetch(`/api/watch/state?key=${encodeURIComponent(itemKey)}`, {cache:"no-store"});
        const payload = await response.json();
        const item = payload.item || null;
        if (item && item.duration && item.position > 10 && item.position < item.duration * 0.92) {
          playLink.textContent = formatRemaining(item.duration - item.position);
        } else {
          playLink.textContent = "\u25b6 Play";
        }
      } catch (_) {}
    }
    window.addEventListener("pageshow", refreshWatchState);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) refreshWatchState();
    });
    function mobileContainerFor(element) {
      return element.closest("[data-mobile-scope]") || document.querySelector(".mobile-download-row");
    }
    function setMobileStatus(container, message) {
      const status = container && container.querySelector("[data-mobile-status]");
      if (status) status.textContent = message || "";
    }
    function setMobileReady(container, payload) {
      const ready = container && container.querySelector("[data-mobile-ready]");
      if (!ready) return;
      if (payload && payload.ready && payload.download_url) {
        ready.href = payload.download_url;
        ready.classList.add("ready");
      } else {
        ready.classList.remove("ready");
      }
    }
    async function pollMobileDownload(container) {
      if (!container) return;
      const scope = container.dataset.mobileScope;
      const itemId = container.dataset.mobileItem;
      if (!scope || !itemId) return;
      try {
        const response = await fetch(`/api/mobile-download/status?scope=${encodeURIComponent(scope)}&item_id=${encodeURIComponent(itemId)}`, {cache:"no-store"});
        const payload = await response.json();
        setMobileReady(container, payload);
        if (payload.ready) {
          setMobileStatus(container, "Mobile copy ready");
        } else if (payload.status === "running" || payload.status === "queued") {
          setMobileStatus(container, `${payload.message || "Preparing"} ${payload.progress || 0}%`);
          setTimeout(() => pollMobileDownload(container), 5000);
        } else if (payload.status === "failed" || payload.status === "error") {
          setMobileStatus(container, payload.error || "Mobile prepare failed");
        }
      } catch (_) {}
    }
    document.querySelectorAll(".action.download").forEach(link => {
      link.addEventListener("click", async event => {
        const container = mobileContainerFor(link);
        const toggle = container && container.querySelector("[data-mobile-toggle]");
        if (!toggle || !toggle.checked) return;
        event.preventDefault();
        const scope = container.dataset.mobileScope;
        const itemId = container.dataset.mobileItem;
        setMobileStatus(container, "Added to mobile queue");
        try {
          const response = await fetch("/api/mobile-download/enqueue", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({scope, item_id:itemId})
          });
          const payload = await response.json();
          setMobileReady(container, payload);
          if (payload.ready) {
            setMobileStatus(container, "Mobile copy ready");
          } else {
            const estimate = payload.estimate_seconds ? ` - est. ${Math.max(1, Math.round(payload.estimate_seconds / 60))} min` : "";
            const prefix = (payload.status === "running" || payload.status === "queued") ? "Already in progress: " : "";
            setMobileStatus(container, `${prefix}${payload.message || "Queued"}${estimate}`);
            setTimeout(() => pollMobileDownload(container), 3000);
          }
        } catch (_) {
          setMobileStatus(container, "Could not queue mobile copy");
        }
      });
    });
    document.querySelectorAll("[data-mobile-scope]").forEach(pollMobileDownload);
  </script>
</body>
</html>
"""


FIX_MATCH_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Fix Match - {{TITLE}}</title>
  <style>
    :root { color-scheme:dark; --bg:#090b0f; --text:#f7fbff; --muted:#aeb8c6; --line:rgba(255,255,255,.12); --gold:#f5b73f; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--text); font-family:Arial, Helvetica, sans-serif; }
    .page { max-width:900px; margin:0 auto; padding:22px clamp(18px,4vw,42px) 44px; }
    header { display:grid; grid-template-columns:44px 1fr; gap:12px; align-items:center; margin-bottom:22px; }
    .back { width:44px; height:44px; border-radius:999px; display:grid; place-items:center; color:#fff; text-decoration:none; background:rgba(255,255,255,.08); font-size:25px; }
    h1 { margin:0; text-align:center; font-size:24px; }
    .sub { text-align:center; color:var(--muted); font-weight:800; margin-top:4px; }
    form { display:grid; grid-template-columns:1fr 100px auto; gap:10px; margin:24px 0; }
    .upload { display:grid; gap:10px; padding:16px; margin:0 0 28px; border:1px solid var(--line); border-radius:16px; background:rgba(255,255,255,.05); }
    .upload h2 { margin:0; font-size:20px; }
    .upload-row { display:grid; grid-template-columns:1fr auto; gap:10px; }
    input, button { min-height:44px; border-radius:12px; border:1px solid var(--line); font:inherit; font-weight:800; }
    input { background:#151922; color:#fff; padding:0 12px; }
    input[type=file] { padding:10px; }
    button { background:var(--gold); color:#071018; padding:0 16px; cursor:pointer; }
    .matches { display:grid; gap:28px; }
    .match-row { display:grid; grid-template-columns:150px minmax(0,1fr) 38px; gap:24px; align-items:start; padding:8px 0 26px; border-bottom:1px solid var(--line); }
    .match-poster { display:block; width:150px; aspect-ratio:2/3; border-radius:10px; overflow:hidden; background:#151922; color:var(--muted); text-decoration:none; }
    .match-poster img { width:100%; height:100%; object-fit:cover; display:block; }
    .match-poster.missing { display:grid; place-items:center; font-weight:900; }
    h2 { margin:3px 0 14px; font-size:24px; line-height:1.15; }
    h2 span { color:var(--muted); font-size:18px; font-weight:700; }
    p { margin:0 0 16px; color:#e8edf5; font-size:18px; line-height:1.36; }
    .apply { display:inline-flex; min-height:38px; align-items:center; border-radius:999px; background:var(--gold); color:#071018; padding:0 14px; text-decoration:none; font-weight:900; }
    .check { width:34px; height:34px; border-radius:999px; display:grid; place-items:center; background:rgba(255,255,255,.12); color:#fff; font-weight:900; font-size:20px; }
    .empty { color:var(--muted); font-size:18px; }
    @media (max-width:640px) {
      .page { padding:18px 18px 36px; }
      form { grid-template-columns:1fr 82px; }
      form button { grid-column:1 / -1; }
      .upload-row { grid-template-columns:1fr; }
      .match-row { grid-template-columns:130px minmax(0,1fr) 30px; gap:14px; }
      .match-poster { width:130px; }
      h2 { font-size:20px; }
      p { font-size:16px; }
    }
  </style>
</head>
<body>
  <main class="page">
    <header>
      <a class="back" href="/movie/{{ITEM_ID}}" aria-label="Back">←</a>
      <div><h1>{{TITLE}}</h1><div class="sub">Fix Match</div></div>
    </header>
    <form method="get" action="/movie/fix-match/{{ITEM_ID}}">
      <input name="q" value="{{QUERY}}" placeholder="Search TMDB title">
      <input name="year" value="{{YEAR}}" placeholder="Year">
      <button type="submit">Search</button>
    </form>
    <form class="upload" method="post" action="/movie/upload-art/{{ITEM_ID}}" enctype="multipart/form-data">
      <h2>Upload Custom Poster</h2>
      <div class="upload-row">
        <input type="file" name="art_file" accept="image/jpeg,image/png,image/webp" required>
        <button type="submit">Upload</button>
      </div>
    </form>
    <section class="matches">{{ROWS}}</section>
  </main>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Movie download web library")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8120)
    args = parser.parse_args()
    movie_index.load_csv_bootstrap()
    load_poster_map()
    load_metadata_map()
    print(f"Loaded {len(movie_index.items)} movie rows from CSV bootstrap", flush=True)
    movie_index.refresh_background()
    print(f"Started background live movie scan from {MOVIE_ROOT}", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving on http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
