#!/usr/bin/env python3
import argparse
import csv
import html
import json
import mimetypes
import os
import re
import socket
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MOVIE_ROOT = Path(os.environ.get("MOVIE_ROOT", "/mnt/nfs-share-movies/Movies")).resolve()
MOVIE_CSV = Path(os.environ.get("MOVIE_CSV", "/mnt/c/DATA/movie-file-sizes.csv")).resolve()
LIVE_CACHE = Path(os.environ.get("MOVIE_LIVE_CACHE", "/mnt/c/DATA/media-download-library/movie-live-index.json")).resolve()
POSTER_MAP = Path(os.environ.get("MOVIE_POSTER_MAP", "/mnt/c/DATA/media-download-library/poster-map.json")).resolve()
POSTER_DIR = Path(os.environ.get("MOVIE_POSTER_DIR", "/mnt/c/DATA/media-download-library/posters")).resolve()
METADATA_MAP = Path(os.environ.get("MOVIE_METADATA_MAP", "/mnt/c/DATA/media-download-library/movie-metadata-map.json")).resolve()
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".m4v", ".avi", ".mov", ".mpeg", ".mpg", ".m2ts", ".ts", ".webm"}
RECENTLY_ADDED_LIMIT = int(os.environ.get("MOVIE_RECENTLY_ADDED_LIMIT", "15"))
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
        payload = json.loads(LIVE_CACHE.read_text(encoding="utf-8"))
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
        tmp = LIVE_CACHE.with_suffix(".tmp")
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


def metadata_for(item: MovieItem) -> dict:
    return metadata_map.get(item.rel_path) or metadata_map.get(item.title) or {}


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


def movie_card_html(item: MovieItem, bucket: str, *, recent: bool = False, duplicate: bool = False) -> str:
    poster = poster_url_for(item)
    poster_html = (
        f"<img class='poster' loading='lazy' src='{html.escape(poster)}' alt=''>"
        if poster
        else "<div class='poster missing'></div>"
    )
    metadata = metadata_for(item)
    actors = metadata.get("actors") or []
    actor_text = ", ".join(actors) if actors else "No actor data available yet."
    search_text = " ".join([item.title, metadata.get("title", ""), metadata.get("year", ""), actor_text]).lower()
    meta_line = f"{date_label(item.modified)} - {human_size(item.size)}" if recent and item.modified else human_size(item.size)
    recent_attr = " data-recent='1'" if recent else ""
    duplicate_attr = " data-duplicate='1'" if duplicate else " data-duplicate='0'"
    return (
        f"<article class='movie-card' data-letter='{html.escape(bucket)}' data-title='{html.escape(search_text)}'{recent_attr}{duplicate_attr}>"
        f"<a class='poster-link' href='/movie/{item.id}' aria-label='Show details for {html.escape(item.title)}'>"
        f"{poster_html}"
        f"</a>"
        f"<div class='movie-info'>"
        f"<div class='title'>{html.escape(item.title)}</div>"
        f"<div class='size'>{html.escape(meta_line)}</div>"
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
        )[:RECENTLY_ADDED_LIMIT]
        if recent_items:
            recent_cards = "".join(movie_card_html(item, "RECENT", recent=True, duplicate=duplicate_key(item) in duplicates) for item in recent_items)
            sections.append(
                "<section class='letter-section recent-section' id='section-recent' data-letter='RECENT'>"
                "<h2>Recently Added</h2>"
                f"<div class='movie-grid'>{recent_cards}</div>"
                "</section>"
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
        poster = poster_url_for(item)
        metadata = metadata_for(item)
        display_title = metadata.get("title") or item.title
        year = metadata.get("year") or ""
        overview = metadata.get("overview") or "No TMDb summary available yet."
        actors = metadata.get("actors") or []
        actor_items = "".join(f"<li>{html.escape(actor)}</li>" for actor in actors) or "<li>No actor data available yet.</li>"
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
            .replace("{{YEAR}}", html.escape(year))
            .replace("{{LIBRARY_TITLE}}", html.escape(item.title))
            .replace("{{POSTER}}", poster_html)
            .replace("{{SUMMARY}}", html.escape(overview))
            .replace("{{ACTORS}}", actor_items)
            .replace("{{SIZE}}", human_size(item.size))
            .replace("{{PLAY}}", f"/player/movie/{item.id}?play=1")
            .replace("{{DOWNLOAD}}", f"/download/{item.id}")
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

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
    body { margin:0; font-family: Arial, Helvetica, sans-serif; background:var(--bg); color:var(--text); }
    header { position:sticky; top:0; z-index:4; background:rgba(17,21,27,.96); backdrop-filter:blur(10px); border-bottom:1px solid var(--line); padding:14px 22px; }
    .top-row { display:flex; align-items:center; justify-content:space-between; gap:18px; }
    .library-trigger { display:inline-grid; gap:2px; border:0; background:transparent; color:var(--text); padding:0; text-align:left; cursor:pointer; }
    .library-trigger h1::after { content:"⌄"; margin-left:8px; color:var(--muted); font-size:18px; }
    .header-actions { display:flex; align-items:center; gap:10px; flex-wrap:wrap; justify-content:flex-end; }
    .home-link { display:inline-flex; align-items:center; justify-content:center; min-height:38px; padding:0 14px; border-radius:999px; background:rgba(255,255,255,.10); border:1px solid var(--line); color:#fff; text-decoration:none; font-weight:800; }
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
    .bar { display:grid; grid-template-columns:minmax(240px, 560px); gap:12px; margin-top:12px; align-items:center; }
    input { width:100%; padding:13px 14px; border:1px solid #3a4552; border-radius:8px; background:#252b34; color:var(--text); font-size:16px; outline:none; }
    input:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(40,162,255,.16); }
    button { border:1px solid var(--accent); border-radius:8px; background:var(--accent); color:#06111c; font-weight:800; padding:11px 13px; cursor:pointer; white-space:nowrap; }
    .page-shell { display:grid; grid-template-columns:minmax(0, 1fr) 50px; gap:14px; padding:18px 16px 44px 22px; }
    main { min-width:0; }
    .letter-rail { position:sticky; top:112px; align-self:start; display:flex; flex-direction:column; gap:4px; max-height:calc(100vh - 126px); overflow:auto; padding:6px 4px; background:rgba(25,31,40,.72); border:1px solid var(--line); border-radius:8px; }
    .letter { width:34px; min-height:28px; padding:0; border-color:transparent; background:transparent; color:#cdd7e3; border-radius:6px; font-size:12px; }
    .letter.active { border-color:var(--accent); background:var(--accent); color:#06111c; }
    .letter-section { scroll-margin-top:128px; margin-bottom:30px; }
    .letter-section h2 { margin:0 0 12px; color:#d7e4f0; font-size:17px; border-bottom:1px solid var(--line); padding-bottom:8px; }
    .movie-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(132px, 1fr)); gap:18px 16px; align-items:start; }
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
      .movie-grid { grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:22px 18px; }
    }
    @media (max-width: 760px) {
      header { padding:12px 60px 12px 12px; }
      h1 { font-size:21px; }
      .bar { grid-template-columns:1fr; }
      .page-shell { grid-template-columns:minmax(0, 1fr) 42px; gap:8px; padding:12px 8px 34px 12px; }
      .letter-rail { top:118px; max-height:calc(100vh - 130px); }
      .letter { width:28px; min-height:24px; font-size:11px; }
      .movie-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); gap:16px 12px; }
      .title { font-size:14px; }
    }
    @media (max-width: 420px) {
      .movie-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <header>
    <div class="top-row">
      <button class="library-trigger" id="libraryTrigger"><h1>Movies</h1><span class="meta">{{SERVER_NAME}}</span></button>
      <div class="header-actions"><a class="home-link" href="/">Home</a><button id="refresh">Refresh</button><label class="duplicate-check"><input id="duplicateToggle" type="checkbox">Duplicates</label></div>
    </div>
    <div class="meta"><span id="visibleCount">{{COUNT}}</span> of {{COUNT}} movie files</div>
    <div class="bar">
      <input id="search" type="search" placeholder="Search movies or actors" autofocus>
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
      {{SECTIONS}}
    </main>
    <nav class="letter-rail" aria-label="Jump to movie letter">
      <button class="letter active" data-letter="ALL">All</button>
      {{LETTERS}}
    </nav>
  </div>
  <script>
    const search = document.getElementById("search");
    const duplicateToggle = document.getElementById("duplicateToggle");
    const libraryModal = document.getElementById("libraryModal");
    document.getElementById("libraryTrigger").addEventListener("click", () => libraryModal.classList.add("open"));
    document.getElementById("closeLibraries").addEventListener("click", () => libraryModal.classList.remove("open"));
    libraryModal.addEventListener("click", (event) => { if (event.target === libraryModal) libraryModal.classList.remove("open"); });
    const visibleCount = document.getElementById("visibleCount");
    const cards = [...document.querySelectorAll(".movie-card")];
    const sections = [...document.querySelectorAll(".letter-section")];
    let activeLetter = "ALL";
    let duplicateOnly = false;
    function applyFilters() {
      const q = search.value.trim().toLowerCase();
      let count = 0;
      for (const card of cards) {
        const matchesSearch = !q || card.dataset.title.includes(q);
        const matchesDuplicate = !duplicateOnly || card.dataset.duplicate === "1";
        const show = matchesSearch && matchesDuplicate;
        card.classList.toggle("hidden", !show);
        if (show && card.dataset.recent !== "1") count++;
      }
      for (const section of sections) {
        const visibleCards = section.querySelectorAll(".movie-card:not(.hidden)").length;
        section.classList.toggle("hidden", visibleCards === 0);
      }
      visibleCount.textContent = count;
    }
    search.addEventListener("input", applyFilters);
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
    document.getElementById("refresh").addEventListener("click", async () => {
      await fetch("/api/refresh");
      location.reload();
    });
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
    .detail-page::before { content:""; position:fixed; inset:-28px; background-image:var(--poster-bg); background-size:cover; background-position:right center; opacity:.62; filter:blur(9px) saturate(1.25); transform:scale(1.08); }
    .detail-page::after { content:""; position:fixed; inset:0; background:
      radial-gradient(circle at 76% 30%, rgba(33,103,157,.38), transparent 32%),
      linear-gradient(180deg, rgba(5,8,14,.10) 0%, rgba(4,29,77,.58) 48%, rgba(5,44,29,.76) 100%),
      linear-gradient(90deg, rgba(8,10,16,.96) 0%, rgba(8,10,16,.74) 38%, rgba(8,10,16,.28) 76%, rgba(8,10,16,.56) 100%);
    }
    .topbar { position:relative; z-index:2; display:flex; justify-content:space-between; align-items:center; max-width:1220px; margin:0 auto; }
    .circle { width:46px; height:46px; display:grid; place-items:center; border-radius:50%; background:rgba(8,12,18,.48); border:1px solid rgba(255,255,255,.12); color:#fff; text-decoration:none; font-size:25px; backdrop-filter:blur(10px); }
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
    .playbar { display:grid; grid-template-columns:minmax(210px, 400px) 44px; gap:10px; align-items:center; margin:10px 0 20px; }
    .play { min-height:50px; display:flex; align-items:center; justify-content:center; border-radius:999px; background:#fff; color:#111; text-decoration:none; font-size:20px; font-weight:900; box-shadow:0 16px 40px rgba(0,0,0,.28); }
    .quick-play { width:44px; height:44px; display:grid; place-items:center; border-radius:50%; background:rgba(122,54,70,.50); border:1px solid rgba(255,255,255,.12); color:#fff; text-decoration:none; font-size:0; position:relative; }
    .quick-play::before { content:"↻"; font-size:23px; line-height:1; transform:translateX(-2px); }
    .quick-play::after { content:"▶"; position:absolute; font-size:11px; line-height:1; transform:translate(5px,1px); }
    .action-row { display:grid; grid-template-columns:repeat(3, minmax(76px,100px)); gap:14px; margin:0 0 26px; }
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
      .detail-page::before { background-position:center top; opacity:.58; filter:blur(8px) saturate(1.25); }
      .detail-page::after { background:linear-gradient(180deg, rgba(7,10,15,.22) 0%, rgba(7,10,15,.62) 28%, rgba(62,0,24,.72) 58%, rgba(4,37,25,.86) 100%); }
      .topbar { margin-bottom:18px; }
      .circle { width:46px; height:46px; }
      .detail-shell { min-height:auto; grid-template-columns:1fr; gap:18px; padding:0; }
      .poster-wrap { order:1; min-height:230px; align-items:end; }
      .detail-poster { width:min(280px, 62vw); border-radius:16px; }
      .detail-info { order:2; text-align:left; max-width:none; }
      h1 { text-align:center; font-size:clamp(42px, 12vw, 70px); }
      .meta-row, .score-row { justify-content:center; }
      .playbar { grid-template-columns:1fr auto; }
      .action-row { grid-template-columns:repeat(3, minmax(64px,1fr)); gap:10px; }
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
      .action-row { grid-template-columns:repeat(3, 1fr); }
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
      <a class="circle" href="{{PLAY}}" aria-label="Cast or play">▱</a>
    </div>
    <div class="detail-shell">
      <div class="poster-wrap">{{POSTER}}</div>
      <section class="detail-info">
        <h1>{{TITLE}}</h1>
        <div class="meta-row"><span>{{YEAR}}</span><span>{{SIZE}}</span><span>Movie</span><span class="rating">Local</span></div>
        <div class="score-row"><span class="score">CineVault</span><span class="score">Ready to play</span><span class="score">Summary details</span></div>
        <div class="playbar">
          <a class="play" href="{{PLAY}}">▶ Play</a>
          <a class="quick-play" href="{{PLAY}}" aria-label="Start from beginning">Start over</a>
        </div>
        <div class="action-row">
          <a class="action mark-watched" href="/movies"><span>✓</span>Mark Watched</a>
          <a class="action download" href="{{DOWNLOAD}}"><span>↓</span>Download</a>
          <a class="action more-toggle" href="#cast" data-more-toggle="1"><span>⋮</span>More</a>
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
  </script>
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
