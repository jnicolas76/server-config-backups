#!/usr/bin/env python3
import argparse
import html
import json
import mimetypes
import os
import random
import re
import socket
import subprocess
import time
import hashlib
import urllib.request
import urllib.error
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TV_ROOT = Path(os.environ.get("TV_ROOT", "/mnt/nfs-share-tvshows/TV Shows")).resolve()
APP_ROOT = Path(__file__).resolve().parent
LIVE_CACHE = Path(os.environ.get("TV_LIVE_CACHE", APP_ROOT / "tv-live-index.json")).resolve()
POSTER_MAP = Path(os.environ.get("TV_POSTER_MAP", APP_ROOT / "tv-poster-map.json")).resolve()
POSTER_DIR = Path(os.environ.get("TV_POSTER_DIR", APP_ROOT / "posters")).resolve()
METADATA_MAP = Path(os.environ.get("TV_METADATA_MAP", APP_ROOT / "tv-metadata-map.json")).resolve()
CUSTOM_ART_MAP = Path(os.environ.get("TV_CUSTOM_ART_MAP", APP_ROOT / "custom-art-map.json")).resolve()
EPISODE_THUMB_DIR = Path(os.environ.get("TV_EPISODE_THUMB_DIR", APP_ROOT / "episode-thumbnails")).resolve()
TMDB_CONFIG_FILE = Path(os.environ.get("TMDB_CONFIG_FILE", APP_ROOT.parent / "media-download-library" / "tmdb_config.json")).resolve()
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".m4v", ".avi", ".mov", ".mpeg", ".mpg", ".m2ts", ".ts", ".webm"}
RECENTLY_ADDED_LIMIT = int(os.environ.get("TV_RECENTLY_ADDED_LIMIT", "20"))
RECENT_SHELF_LIMIT = int(os.environ.get("TV_RECENT_SHELF_LIMIT", "20"))
RECENT_SHELF_EXPANDED_LIMIT = int(os.environ.get("TV_RECENT_SHELF_EXPANDED_LIMIT", "50"))
SERVER_DISPLAY_NAME = os.environ.get("CINEVAULT_SERVER_NAME") or socket.gethostname()


@dataclass
class Episode:
    id: int
    show: str
    season: str
    title: str
    path: Path
    rel_path: str
    size: int
    modified: float


@dataclass
class Season:
    key: str
    label: str
    episodes: list[Episode] = field(default_factory=list)


@dataclass
class Show:
    id: int
    title: str
    seasons: dict[str, Season] = field(default_factory=dict)
    size: int = 0
    count: int = 0


class TVIndex:
    def __init__(self):
        self.shows: list[Show] = []
        self.show_by_id: dict[int, Show] = {}
        self.episode_by_id: dict[int, Episode] = {}
        self.season_by_key: dict[str, Season] = {}
        self.scanned_at = 0.0
        self.scanning = False
        self.error = ""

    def refresh(self) -> None:
        if self.scanning:
            return
        self.scanning = True
        self.error = ""
        try:
            episodes = self.scan_episodes()
            self.build(episodes)
            self.save_cache()
        except Exception as exc:
            self.error = str(exc)
            raise
        finally:
            self.scanning = False

    def scan_episodes(self) -> list[Episode]:
        expression = ["find", str(TV_ROOT), "-maxdepth", "4", "-type", "f", "("]
        for index, ext in enumerate(sorted(VIDEO_EXTENSIONS)):
            if index:
                expression.append("-o")
            expression.extend(["-iname", f"*{ext}"])
        expression.append(")")
        result = subprocess.run(expression, check=True, text=True, stdout=subprocess.PIPE)
        episodes: list[Episode] = []
        for next_id, line in enumerate(result.stdout.splitlines(), start=1):
            path = Path(line)
            try:
                stat = path.stat()
                rel_path = str(path.relative_to(TV_ROOT))
            except OSError:
                continue
            show, season = parse_show_season(path)
            episodes.append(
                Episode(
                    next_id,
                    show,
                    season,
                    clean_episode_title(path),
                    path,
                    rel_path,
                    stat.st_size,
                    stat.st_mtime,
                )
            )
        episodes.sort(key=lambda ep: (natural_key(ep.show), season_sort_key(ep.season), episode_sort_key(ep.title), natural_key(ep.rel_path)))
        for index, episode in enumerate(episodes, start=1):
            episode.id = index
        return episodes

    def build(self, episodes: list[Episode]) -> None:
        show_map: dict[str, Show] = {}
        for episode in episodes:
            show = show_map.get(episode.show)
            if not show:
                show = Show(len(show_map) + 1, episode.show)
                show_map[episode.show] = show
            season_key = f"{show.id}:{slugify(episode.season)}"
            season = show.seasons.get(season_key)
            if not season:
                season = Season(season_key, episode.season)
                show.seasons[season_key] = season
            season.episodes.append(episode)
            show.size += episode.size
            show.count += 1
        self.shows = sorted(show_map.values(), key=lambda show: natural_key(show.title))
        for index, show in enumerate(self.shows, start=1):
            old_id = show.id
            show.id = index
            new_seasons: dict[str, Season] = {}
            for season in sorted(show.seasons.values(), key=lambda season: season_sort_key(season.label)):
                season.key = f"{show.id}:{slugify(season.label)}"
                new_seasons[season.key] = season
            show.seasons = new_seasons
        self.show_by_id = {show.id: show for show in self.shows}
        self.episode_by_id = {episode.id: episode for show in self.shows for season in show.seasons.values() for episode in season.episodes}
        self.season_by_key = {season.key: season for show in self.shows for season in show.seasons.values()}
        self.scanned_at = time.time()

    def load_cache(self) -> bool:
        if not LIVE_CACHE.is_file():
            return False
        raw = LIVE_CACHE.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload, _ = json.JSONDecoder().raw_decode(raw)
        episodes = []
        for row in payload.get("episodes", []):
            episodes.append(
                Episode(
                    int(row["id"]),
                    row["show"],
                    row["season"],
                    row["title"],
                    Path(row["path"]),
                    row["rel_path"],
                    int(row["size"]),
                    float(row.get("modified", 0.0)),
                )
            )
        self.build(episodes)
        self.scanned_at = float(payload.get("scanned_at", LIVE_CACHE.stat().st_mtime))
        return True

    def save_cache(self) -> None:
        LIVE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        episodes = [episode for show in self.shows for season in show.seasons.values() for episode in season.episodes]
        payload = {
            "root": str(TV_ROOT),
            "scanned_at": self.scanned_at,
            "episodes": [
                {
                    "id": episode.id,
                    "show": episode.show,
                    "season": episode.season,
                    "title": episode.title,
                    "path": str(episode.path),
                    "rel_path": episode.rel_path,
                    "size": episode.size,
                    "modified": episode.modified,
                }
                for episode in episodes
            ],
        }
        tmp = LIVE_CACHE.with_name(f"{LIVE_CACHE.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(LIVE_CACHE)

    def refresh_background(self) -> None:
        import threading

        threading.Thread(target=self.refresh, daemon=True).start()


tv_index = TVIndex()
poster_map: dict[str, str] = {}
metadata_map: dict[str, dict] = {}
metadata_map_mtime = 0.0
custom_art_map: dict[str, dict] = {}


def parse_show_season(path: Path) -> tuple[str, str]:
    try:
        rel_parts = path.relative_to(TV_ROOT).parts
    except ValueError:
        rel_parts = path.parts
    show = rel_parts[0].strip() if len(rel_parts) >= 2 else path.parent.name.strip()
    season = "Other"
    if len(rel_parts) >= 3:
        season = rel_parts[1].strip()
    elif len(rel_parts) >= 2:
        season = detect_season(path.name) or "Season 01"
    season = normalize_season_label(season)
    return show or "Unknown Show", season


def detect_season(value: str) -> str:
    match = re.search(r"[Ss](\d{1,2})[Ee](?:P)?\d{1,3}", value)
    if match:
        return f"Season {int(match.group(1)):02d}"
    return ""


def normalize_season_label(value: str) -> str:
    match = re.search(r"season\D*(\d{1,2})", value, re.I)
    if match:
        return f"Season {int(match.group(1)):02d}"
    match = re.search(r"\b[Ss](\d{1,2})\b", value)
    if match:
        return f"Season {int(match.group(1)):02d}"
    return value.strip() or "Other"


def clean_episode_title(path: Path) -> str:
    title = path.stem.replace(".", " ").replace("_", " ")
    title = re.sub(r"\s+", " ", title).strip()
    return title or path.name


def season_sort_key(value: str):
    match = re.search(r"(\d+)", value)
    if match:
        return (0, int(match.group(1)), value.lower())
    return (1, 9999, value.lower())


def episode_sort_key(value: str):
    match = re.search(r"[Ss]\d{1,2}\s*[xX]\s*[Ee]?(\d{1,3})", value)
    if match:
        return (0, int(match.group(1)), natural_key(value))
    match = re.search(r"[Ss]\d{1,2}[Ee](?:P)?(\d{1,3})", value)
    if match:
        return (0, int(match.group(1)), natural_key(value))
    match = re.search(r"Season\s+\d{1,2}\D+Episode\s+(\d{1,3})", value, re.I)
    if match:
        return (0, int(match.group(1)), natural_key(value))
    match = re.search(r"\b[Ee](?:P)?(\d{1,3})\b", value)
    if match:
        return (0, int(match.group(1)), natural_key(value))
    return (1, 9999, natural_key(value))


def natural_key(value: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]




def download_safe_filename(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', ' ', value or '').strip()
    value = re.sub(r'\s+', ' ', value)
    return value[:180].strip(' .') or 'CineMediaVault'

def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "other"


def title_bucket(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return "#"
    first = stripped[0].upper()
    return first if "A" <= first <= "Z" else "0-9"


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


def show_modified(show: Show) -> float:
    return max(
        (episode.modified for season in show.seasons.values() for episode in season.episodes),
        default=0.0,
    )


def episode_number_label(episode: Episode) -> str:
    match = re.search(r"[Ss]\d{1,2}\s*[xX]\s*[Ee]?(\d{1,3})", episode.title)
    if match:
        return f"E{int(match.group(1))}"
    match = re.search(r"[Ss]\d{1,2}[Ee](?:P)?(\d{1,3})", episode.title)
    if match:
        return f"E{int(match.group(1))}"
    match = re.search(r"Season\s+\d{1,2}\D+Episode\s+(\d{1,3})", episode.title, re.I)
    if match:
        return f"E{int(match.group(1))}"
    match = re.search(r"\b[Ee](?:P)?(\d{1,3})\b", episode.title)
    if match:
        return f"E{int(match.group(1))}"
    return ""


def season_number_label(season_label: str) -> str:
    match = re.search(r"(\d+)", season_label)
    if match:
        return f"Season {int(match.group(1))}"
    return season_label


def show_card_subtitle(show: Show) -> str:
    seasons = list(show.seasons.values())
    if len(seasons) == 1:
        season = seasons[0]
        if season.episodes:
            latest = max(season.episodes, key=lambda episode: episode.modified)
            episode_label = episode_number_label(latest)
            if episode_label:
                return f"{season_number_label(season.label)} • {episode_label}"
        return season_number_label(season.label)
    return f"{len(seasons)} seasons • {show.count} episodes"


def show_for_episode(episode: Episode) -> Show | None:
    for show in tv_index.shows:
        if show.title == episode.show:
            return show
    return None


def episode_card_subtitle(episodes: list[Episode]) -> str:
    if len(episodes) != 1:
        return ""
    episode = episodes[0]
    episode_label = episode_number_label(episode)
    season_label = season_number_label(episode.season)
    return f"{season_label} â€¢ {episode_label}" if episode_label else season_label


def recent_episode_groups(limit: int) -> list[tuple[Show, list[Episode]]]:
    grouped: dict[tuple[str, str], list[Episode]] = {}
    for show in tv_index.shows:
        for season in show.seasons.values():
            for episode in season.episodes:
                if not episode.modified:
                    continue
                key = (show.title, date_label(episode.modified))
                grouped.setdefault(key, []).append(episode)

    groups = sorted(
        grouped.values(),
        key=lambda episodes: max(episode.modified for episode in episodes),
        reverse=True,
    )
    result: list[tuple[Show, list[Episode]]] = []
    for episodes in groups:
        show = show_for_episode(episodes[0])
        if show:
            result.append((show, sorted(episodes, key=lambda episode: episode.modified, reverse=True)))
        if len(result) >= limit:
            break
    return result


def show_card_html(show: Show, bucket: str, *, recent: bool = False, subtitle_override: str | None = None) -> str:
    poster = poster_url_for(show)
    metadata = metadata_for(show)
    actors = metadata.get("actors") or []
    genres = genres_for(metadata)
    actor_text = ", ".join(actors) if actors else "No actor data available yet."
    genre_text = ", ".join(genres)
    display_title = metadata.get("title") or show.title
    subtitle = show_card_subtitle(show) if subtitle_override is None else subtitle_override
    search_text = " ".join([show.title, display_title, metadata.get("year", ""), metadata.get("first_air_date", ""), actor_text, genre_text]).lower()
    poster_html = (
        f"<img class='poster' loading='lazy' src='{html.escape(poster)}' alt=''>"
        if poster
        else "<div class='poster missing'></div>"
    )
    subtitle_html = f"<div class='size'>{html.escape(subtitle)}</div>" if subtitle else ""
    recent_attr = " data-recent='1'" if recent else ""
    return (
        f"<article class='show-card' data-letter='{html.escape(bucket)}' data-title='{html.escape(search_text)}' data-genres='{html.escape(genre_text.lower())}'{recent_attr}>"
        f"<a class='poster-link' href='/tv/show/{show.id}' aria-label='Show details for {html.escape(display_title)}'>"
        f"{poster_html}"
        f"</a>"
        f"<div class='show-info'>"
        f"<div class='title'>{html.escape(display_title)}</div>"
        f"{subtitle_html}"
        f"</div>"
        f"</article>"
    )


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
    return str(metadata.get("first_air_date") or metadata.get("year") or "")


def season_episode_numbers(episode: Episode) -> tuple[int | None, int | None]:
    season_match = re.search(r"(\d+)", episode.season)
    episode_match = re.search(r"[Ss](\d{1,2})\s*[xX]\s*[Ee]?(\d{1,3})", episode.title)
    if episode_match:
        return int(episode_match.group(1)), int(episode_match.group(2))
    episode_match = re.search(r"[Ss](\d{1,2})[Ee](?:P)?(\d{1,3})", episode.title)
    if episode_match:
        return int(episode_match.group(1)), int(episode_match.group(2))
    episode_match = re.search(r"Season\s+(\d{1,2})\D+Episode\s+(\d{1,3})", episode.title, re.I)
    if episode_match:
        return int(episode_match.group(1)), int(episode_match.group(2))
    episode_only_match = re.search(r"\b[Ee](?:P)?(\d{1,3})\b", episode.title)
    season_number = int(season_match.group(1)) if season_match else None
    episode_number = int(episode_only_match.group(1)) if episode_only_match else None
    return season_number, episode_number


def episode_key(episode: Episode) -> str:
    season_number, episode_number = season_episode_numbers(episode)
    if season_number is None or episode_number is None:
        return ""
    return f"S{season_number:02d}E{episode_number:02d}"


def episode_full_label(episode: Episode) -> str:
    season_number, episode_number = season_episode_numbers(episode)
    if season_number is None or episode_number is None:
        return season_number_label(episode.season)
    return f"S{season_number} - E{episode_number}"


def clean_episode_display_title(episode: Episode) -> str:
    title = episode.title
    title = re.sub(re.escape(episode.show), " ", title, flags=re.I)
    title = re.sub(r"\((18|19|20)\d{2}\)", " ", title)
    title = re.sub(r"[Ss]\d{1,2}\s*[xX]\s*[Ee]?\d{1,3}", " ", title)
    title = re.sub(r"[Ss]\d{1,2}[Ee](?:P)?\d{1,3}", " ", title)
    title = re.sub(r"Season\s+\d{1,2}\D+Episode\s+\d{1,3}", " ", title, flags=re.I)
    title = re.sub(r"\b[Ee](?:P)?\d{1,3}\b", " ", title)
    title = re.sub(
        r"\b(2160p|1080p|720p|480p|WEB[- ]?DL|WEBDL|WEBRip|HDTV|BluRay|BRRip|DVDRip|"
        r"x264|x265|h264|h265|HEVC|AAC|AC3|EAC3|DDP?5?\.?1|10bit|8bit|YTS|EZTV|RARBG|"
        r"SuccessfulCrab|AMZN|NF|MAX|DSNP|HULU)\b",
        " ",
        title,
        flags=re.I,
    )
    title = re.sub(r"\[[^\]]+\]|\{[^}]+\}", " ", title)
    title = re.sub(r"[-_.]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -_.")
    return title or episode_number_label(episode) or episode.title


def episode_metadata(metadata: dict, episode: Episode) -> dict:
    key = episode_key(episode)
    episodes = metadata.get("episodes") or {}
    row = episodes.get(key) if key else None
    return row if isinstance(row, dict) else {}


def display_episode_title(metadata: dict, episode: Episode) -> str:
    row = episode_metadata(metadata, episode)
    return str(row.get("title") or clean_episode_display_title(episode))


def episode_summary(metadata: dict, episode: Episode) -> str:
    row = episode_metadata(metadata, episode)
    return str(row.get("overview") or "")


def episode_still_url(metadata: dict, episode: Episode) -> str:
    row = episode_metadata(metadata, episode)
    still_path = str(row.get("still_path") or "")
    if still_path.startswith("/"):
        return f"https://image.tmdb.org/t/p/w300{still_path}"
    local_thumb = episode_thumbnail_path(episode)
    if local_thumb.is_file():
        return f"/episode-thumbnails/{local_thumb.name}"
    return ""


def episode_thumbnail_name(episode: Episode) -> str:
    digest = hashlib.sha1(episode.rel_path.encode("utf-8", errors="ignore")).hexdigest()
    return f"{digest}.jpg"


def episode_thumbnail_path(episode: Episode) -> Path:
    return EPISODE_THUMB_DIR / episode_thumbnail_name(episode)


def episode_air_date(metadata: dict, episode: Episode) -> str:
    row = episode_metadata(metadata, episode)
    if row.get("air_date"):
        return str(row.get("air_date"))
    dates = metadata.get("episode_air_dates") or {}
    key = episode_key(episode)
    return str(dates.get(key) or "")


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


def load_poster_map() -> None:
    global poster_map
    if not POSTER_MAP.is_file():
        poster_map = {}
    else:
        try:
            poster_map = json.loads(POSTER_MAP.read_text(encoding="utf-8"))
        except Exception:
            poster_map = {}
    load_custom_art_map()


def load_custom_art_map() -> None:
    global custom_art_map
    if not CUSTOM_ART_MAP.is_file():
        custom_art_map = {}
        return
    try:
        custom_art_map = json.loads(CUSTOM_ART_MAP.read_text(encoding="utf-8"))
    except Exception:
        custom_art_map = {}


def art_url(rel: str) -> str:
    return "/" + rel.lstrip("/") if rel else ""


def custom_art_for(show: Show, key: str) -> str:
    value = custom_art_map.get(show.title) or {}
    return art_url(value.get(key, ""))


def load_metadata_map() -> None:
    global metadata_map, metadata_map_mtime
    if not METADATA_MAP.is_file():
        metadata_map = {}
        metadata_map_mtime = 0.0
        return
    try:
        metadata_map_mtime = METADATA_MAP.stat().st_mtime
        metadata_map = json.loads(METADATA_MAP.read_text(encoding="utf-8"))
    except Exception:
        metadata_map = {}
        metadata_map_mtime = 0.0


def refresh_metadata_map_if_changed() -> None:
    try:
        current_mtime = METADATA_MAP.stat().st_mtime
    except OSError:
        return
    if current_mtime != metadata_map_mtime:
        load_metadata_map()


def poster_url_for(show: Show) -> str:
    custom = custom_art_for(show, "poster")
    if custom:
        return custom
    rel = poster_map.get(show.title)
    if not rel:
        return ""
    return art_url(rel)


def poster_backdrop_html(shows, limit: int = 70) -> str:
    candidates = list(shows)
    random.shuffle(candidates)
    posters: list[str] = []
    seen: set[str] = set()
    for show in candidates[: limit * 3]:
        poster = poster_url_for(show)
        if not poster or poster in seen:
            continue
        seen.add(poster)
        posters.append(poster)
    random.shuffle(posters)
    return "".join(f"<img src='{html.escape(poster)}' alt=''>" for poster in posters[:limit])


def detail_art_url_for(show: Show) -> str:
    return custom_art_for(show, "foreground") or custom_art_for(show, "detail") or poster_url_for(show)


def backdrop_url_for(show: Show) -> str:
    return custom_art_for(show, "backdrop") or detail_art_url_for(show)


def metadata_for(show: Show) -> dict:
    refresh_metadata_map_if_changed()
    return metadata_map.get(show.title) or {}


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


def save_custom_art_map(payload) -> None:
    global custom_art_map
    save_json_file(CUSTOM_ART_MAP, payload)
    custom_art_map = payload


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
    headers = {"Accept": "application/json", "User-Agent": "cinevault-tv-fix-match/1.0"}
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
        params["first_air_date_year"] = year
    payload = tmdb_request_json("/search/tv", params)
    return payload.get("results", [])[:12]


def local_season_numbers(show: Show) -> set[int]:
    seasons: set[int] = set()
    for season in show.seasons.values():
        match = re.search(r"(\d+)", season.label)
        if match:
            seasons.add(int(match.group(1)))
    return seasons


def tmdb_tv_credits(tv_id: int) -> list[str]:
    names = []
    seen = set()
    for endpoint in ("aggregate_credits", "credits"):
        payload = tmdb_request_json(f"/tv/{tv_id}/{endpoint}", {})
        for person in payload.get("cast", []):
            name = str(person.get("name") or "").strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
    return names


def tmdb_genre_names() -> dict[int, str]:
    payload = tmdb_request_json("/genre/tv/list", {})
    return {int(item["id"]): item["name"] for item in payload.get("genres", []) if item.get("id") and item.get("name")}


def tmdb_episode_metadata(tv_id: int, seasons: set[int]) -> tuple[dict, dict]:
    episodes = {}
    air_dates = {}
    for season_number in sorted(seasons):
        try:
            payload = tmdb_request_json(f"/tv/{tv_id}/season/{season_number}", {})
        except urllib.error.HTTPError:
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
    return episodes, air_dates


def download_tmdb_poster(show: Show, tmdb_row: dict) -> str:
    poster_path = tmdb_row.get("poster_path") or ""
    if not poster_path:
        return ""
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(poster_path).suffix or ".jpg"
    filename = f"{slugify(show.title)}-{tmdb_row.get('id')}{ext}"
    destination = POSTER_DIR / filename
    if not destination.is_file():
        url = tmdb_image_url(poster_path, "w500")
        urllib.request.urlretrieve(url, destination)
    return f"posters/{filename}"


def apply_tmdb_match(show: Show, tmdb_id: int) -> dict:
    global poster_map, metadata_map
    detail = tmdb_request_json(f"/tv/{tmdb_id}", {})
    genres_by_id = tmdb_genre_names()
    actors = tmdb_tv_credits(tmdb_id)
    episodes, air_dates = tmdb_episode_metadata(tmdb_id, local_season_numbers(show))
    metadata = load_json_file(METADATA_MAP, {})
    metadata[show.title] = {
        "tmdb_id": detail.get("id"),
        "title": detail.get("name") or show.title,
        "first_air_date": detail.get("first_air_date") or "",
        "year": str(detail.get("first_air_date", "")[:4] or ""),
        "overview": detail.get("overview") or "",
        "vote_average": detail.get("vote_average"),
        "vote_count": detail.get("vote_count"),
        "genres": [genres_by_id[item] for item in detail.get("genre_ids", []) if item in genres_by_id] or [genre.get("name") for genre in detail.get("genres", []) if genre.get("name")],
        "actors": actors,
        "episodes": episodes,
        "episode_air_dates": air_dates,
        "poster_path": detail.get("poster_path") or "",
        "backdrop_path": detail.get("backdrop_path") or "",
    }
    save_json_file(METADATA_MAP, metadata)
    metadata_map = metadata
    rel_poster = download_tmdb_poster(show, detail)
    if rel_poster:
        poster_data = load_json_file(POSTER_MAP, {})
        poster_data[show.title] = rel_poster
        save_json_file(POSTER_MAP, poster_data)
        poster_map = poster_data
    return metadata[show.title]


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


def save_uploaded_art(show: Show, art_type: str, filename: str, data: bytes, mime: str) -> str:
    allowed_types = {"poster", "foreground", "backdrop"}
    if art_type not in allowed_types:
        raise ValueError("Invalid art type")
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
    destination = POSTER_DIR / f"custom-{slugify(show.title)}-{art_type}-{digest}{suffix}"
    destination.write_bytes(data)
    custom_data = load_json_file(CUSTOM_ART_MAP, {})
    current = custom_data.get(show.title) or {}
    current[art_type] = f"posters/{destination.name}"
    if art_type == "poster":
        current.setdefault("detail", f"posters/{destination.name}")
    custom_data[show.title] = current
    save_custom_art_map(custom_data)
    return current[art_type]


def safe_episode(item_id: str) -> Episode:
    try:
        episode = tv_index.episode_by_id[int(item_id)]
    except Exception:
        raise FileNotFoundError("Episode id not found")
    resolved = episode.path.resolve()
    if not str(resolved).startswith(str(TV_ROOT) + os.sep):
        raise PermissionError("Refusing path outside TV root")
    if not resolved.is_file():
        raise FileNotFoundError("Episode file missing")
    return episode


def safe_season(key: str) -> Season:
    decoded = urllib.parse.unquote(key)
    season = tv_index.season_by_key.get(decoded)
    if not season:
        raise FileNotFoundError("Season not found")
    for episode in season.episodes:
        resolved = episode.path.resolve()
        if not str(resolved).startswith(str(TV_ROOT) + os.sep):
            raise PermissionError("Refusing path outside TV root")
    return season


class Handler(BaseHTTPRequestHandler):
    server_version = "TVDownloadLibrary/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/":
                return self.page()
            if parsed.path.startswith("/tv/show/"):
                return self.show_detail(parsed.path.rsplit("/", 1)[-1])
            if parsed.path.startswith("/tv/fix-match/"):
                return self.fix_match_page(parsed.path.rsplit("/", 1)[-1], parsed)
            if parsed.path.startswith("/tv/apply-match/"):
                return self.apply_match(parsed.path.rsplit("/", 1)[-1], parsed)
            if parsed.path == "/api/refresh":
                tv_index.refresh_background()
                return self.json_response({"ok": True, "shows": len(tv_index.shows), "scanning": tv_index.scanning})
            if parsed.path == "/api/reload-posters":
                load_poster_map()
                load_metadata_map()
                return self.json_response({"ok": True, "posters": len(poster_map), "metadata": len(metadata_map)})
            if parsed.path.startswith("/posters/"):
                return self.serve_poster(parsed.path)
            if parsed.path.startswith("/episode-thumbnails/"):
                return self.serve_episode_thumbnail(parsed.path)
            if parsed.path.startswith("/play/episode/"):
                return self.play_episode(parsed.path.rsplit("/", 1)[-1])
            if parsed.path.startswith("/download/episode/"):
                return self.download_episode(parsed.path.rsplit("/", 1)[-1])
            if parsed.path.startswith("/download/season/"):
                return self.download_season(parsed.path.rsplit("/", 1)[-1])
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
            if parsed.path.startswith("/tv/upload-art/"):
                return self.upload_art(parsed.path.rsplit("/", 1)[-1])
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

    def page(self):
        buckets: dict[str, list[str]] = {}
        for show in tv_index.shows:
            bucket = title_bucket(show.title)
            buckets.setdefault(bucket, []).append(show_card_html(show, bucket))
        letters = ["0-9"] + [chr(code) for code in range(ord("A"), ord("Z") + 1)]
        sections = []
        recent_groups = recent_episode_groups(RECENT_SHELF_EXPANDED_LIMIT)
        recent_shows = [show for show, _episodes in recent_groups]
        recent_released_shows = [
            show for show in sorted(
                [show for show in tv_index.shows if show_modified(show)],
                key=show_modified,
                reverse=True,
            )[:RECENT_SHELF_EXPANDED_LIMIT]
            if show_modified(show) >= month_start_timestamp() and str(metadata_for(show).get("year") or "") == current_year_text()
        ]
        sections.append(
            recent_shelf_section(
                "section-recent",
                "Recently Added",
                [
                    show_card_html(show, "RECENT", recent=True, subtitle_override=episode_card_subtitle(episodes))
                    for show, episodes in recent_groups
                ],
                "show-grid",
            )
        )
        sections.append(
            recent_shelf_section(
                "section-released",
                "Recently Released",
                [show_card_html(show, "RECENT", recent=True) for show in recent_released_shows],
                "show-grid",
            )
        )
        for letter in letters:
            cards = buckets.get(letter, [])
            if not cards:
                continue
            sections.append(
                f"<section class='letter-section' id='section-{html.escape(letter)}' data-letter='{html.escape(letter)}'>"
                f"<h2>{html.escape(letter)}</h2>"
                f"<div class='show-grid'>"
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
            .replace("{{COUNT}}", str(len(tv_index.shows)))
            .replace("{{EPISODES}}", str(len(tv_index.episode_by_id)))
            .replace("{{SERVER_NAME}}", html.escape(SERVER_DISPLAY_NAME))
            .replace("{{BACKDROP_POSTERS}}", poster_backdrop_html(tv_index.shows))
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

    def show_detail(self, show_id: str):
        try:
            show = tv_index.show_by_id[int(show_id)]
        except Exception:
            raise FileNotFoundError("Show id not found")
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        detail_mode = (params.get("mode", [""])[0] or "").lower()
        if detail_mode not in {"direct", "hls"}:
            detail_mode = ""
        current_user = getattr(self, "current_user", lambda: None)()
        is_admin = bool(current_user and current_user["is_admin"])
        poster = poster_url_for(show)
        detail_art = detail_art_url_for(show)
        backdrop = backdrop_url_for(show)
        metadata = metadata_for(show)
        display_title = metadata.get("title") or show.title
        release_label = release_label_for(metadata)
        rating_html = tmdb_score_html(metadata)
        genre_text = ", ".join(genres_for(metadata))
        overview = metadata.get("overview") or "No summary details available yet."
        actors = metadata.get("actors") or []
        actor_items = "".join(
            f"<li><a href='/actor?name={urllib.parse.quote(str(actor))}'>{html.escape(str(actor))}</a></li>"
            for actor in actors
        ) or "<li>No actor data available yet.</li>"
        poster_html = (
            f"<img class='detail-poster' src='{html.escape(detail_art)}' alt=''>"
            if detail_art
            else "<div class='detail-poster missing'></div>"
        )
        background_style = f" style=\"--poster-bg:url('{html.escape(backdrop)}')\"" if backdrop else ""
        seasons = sorted(show.seasons.values(), key=lambda season: season_sort_key(season.label))
        first_episode = next((season.episodes[0] for season in seasons if season.episodes), None)
        mode_query = f"?mode={detail_mode}&play=1" if detail_mode else "?play=1"
        play_href = f"/player/tv/{first_episode.id}{mode_query}" if first_episode else "/tv"
        download_href = f"/download/episode/{first_episode.id}" if first_episode else "/tv"
        admin_action = f'<a class="action" href="/admin/media/tv/{first_episode.id}"><span>A</span>Admin</a>' if is_admin and first_episode else ""
        season_blocks = []
        for index, season in enumerate(seasons):
            season_size = sum(episode.size for episode in season.episodes)
            episodes = []
            for episode in season.episodes:
                title = display_episode_title(metadata, episode)
                label = episode_number_label(episode)
                air_date = episode_air_date(metadata, episode)
                overview = episode_summary(metadata, episode)
                still = episode_still_url(metadata, episode)
                still_html = (
                    f"<img class='episode-still' loading='lazy' src='{html.escape(still)}' alt=''>"
                    if still
                    else "<div class='episode-still missing'></div>"
                )
                meta_parts = [part for part in [label, air_date] if part]
                meta_html = f"<span class='episode-date'>{html.escape('  '.join(meta_parts))}</span>" if meta_parts else ""
                summary_html = (
                    f"<span class='episode-summary'>{html.escape(overview)}</span>"
                    if overview
                    else ""
                )
                admin_episode_link = (
                    f"<a class='episode-admin' href='/admin/media/tv/{episode.id}'>Admin</a>"
                    if is_admin
                    else ""
                )
                episodes.append(
                    f"<li>"
                    f"<a class='episode-main' href='/player/tv/{episode.id}'>"
                    f"{still_html}"
                    f"<span class='episode-copy'>"
                    f"<span class='episode-title'>{html.escape(title)}</span>"
                    f"{meta_html}"
                    f"{summary_html}"
                    f"</span>"
                    f"</a>"
                    f"<span class='episode-size'>{human_size(episode.size)}</span>"
                    f"<span class='episode-actions'>"
                    f"<a class='play-link' href='/player/tv/{episode.id}?play=1'>Play</a>"
                    f"<a class='episode-more' href='/player/tv/{episode.id}?mode=hls&play=1'>HLS</a>"
                    f"<label class='episode-compress'><input type='checkbox' data-mobile-toggle> Compress</label>"
                    f"<a class='episode-download' href='/download/episode/{episode.id}' data-mobile-request data-mobile-scope='episode' data-mobile-item='{episode.id}'>Download</a>"
                    f"<a class='mobile-ready episode-ready' data-mobile-ready href='#'>Mobile</a>"
                    f"<span class='mobile-status episode-mobile-status' data-mobile-status></span>"
                    f"<a class='episode-more' href='/player/tv/{episode.id}'>More</a>"
                    f"{admin_episode_link}"
                    f"</span>"
                    f"</li>"
                )
            open_attr = " open" if index == 0 else ""
            season_blocks.append(
                f"<details class='season' id='season-{slugify(season.label)}'{open_attr}>"
                f"<summary><span>{html.escape(season.label)} <small>{len(season.episodes)} episodes - {human_size(season_size)}</small></span>"
                f"<span class='season-download-tools' data-mobile-scope='season' data-mobile-item='{html.escape(season.key, quote=True)}'>"
                f"<label class='download-toggle'><input type='checkbox' data-mobile-toggle> Compress</label>"
                f"<a class='season-download' href='/download/season/{urllib.parse.quote(season.key)}' data-mobile-request data-mobile-scope='season' data-mobile-item='{html.escape(season.key, quote=True)}'>Season ZIP</a>"
                f"<a class='mobile-ready season-ready' data-mobile-ready href='#'>Download mobile season</a>"
                f"<span class='mobile-status season-mobile-status' data-mobile-status></span>"
                f"</span></summary>"
                f"<ol>{''.join(episodes)}</ol>"
                f"</details>"
            )
        play_mode_links = (
            f"<a class='{'active' if detail_mode == 'direct' else ''}' href='/tv/show/{show.id}?mode=direct'>Direct</a>"
            f"<a class='hls {'active' if detail_mode == 'hls' else ''}' href='/tv/show/{show.id}?mode=hls'>HLS</a>"
            if first_episode
            else ""
        )
        body = (
            DETAIL_TEMPLATE
            .replace("{{BACKGROUND_STYLE}}", background_style)
            .replace("{{TITLE}}", html.escape(display_title))
            .replace("{{SHOW_ID}}", str(show.id))
            .replace("{{YEAR}}", html.escape(release_label))
            .replace("{{GENRES}}", html.escape(genre_text))
            .replace("{{TMDB_SCORE}}", rating_html)
            .replace("{{LIBRARY_TITLE}}", html.escape(show.title))
            .replace("{{POSTER}}", poster_html)
            .replace("{{SUMMARY}}", html.escape(overview))
            .replace("{{ACTORS}}", actor_items)
            .replace("{{SIZE}}", human_size(show.size))
            .replace("{{ITEM_KEY}}", f"tv:{first_episode.id}" if first_episode else "")
            .replace("{{EPISODES}}", str(show.count))
            .replace("{{SEASONS}}", str(len(seasons)))
            .replace("{{PLAY}}", play_href)
            .replace("{{PLAY_MODE_LINKS}}", play_mode_links)
            .replace("{{DOWNLOAD}}", download_href)
            .replace("{{ADMIN_ACTION}}", admin_action)
            .replace("{{SEASON_BLOCKS}}", "\n".join(season_blocks))
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def fix_match_page(self, show_id: str, parsed):
        try:
            show = tv_index.show_by_id[int(show_id)]
        except Exception:
            raise FileNotFoundError("Show id not found")
        params = urllib.parse.parse_qs(parsed.query)
        metadata = metadata_for(show)
        query = params.get("q", [metadata.get("title") or show.title])[0].strip()
        year = params.get("year", [str(metadata.get("year") or "")])[0].strip()
        candidates = tmdb_search_candidates(query, year)
        current_id = str(metadata.get("tmdb_id") or "")
        rows = []
        for item in candidates:
            tmdb_id = str(item.get("id") or "")
            title = item.get("name") or "Untitled"
            first_air = item.get("first_air_date") or ""
            candidate_year = first_air[:4]
            overview = item.get("overview") or "No summary available."
            poster = tmdb_image_url(item.get("poster_path") or "", "w342")
            poster_html = f"<img src='{html.escape(poster)}' alt=''>" if poster else "<div class='match-poster missing'>No Poster</div>"
            selected = tmdb_id == current_id
            rows.append(
                "<article class='match-row'>"
                f"<a class='match-poster' href='/tv/apply-match/{show.id}?tmdb_id={html.escape(tmdb_id)}'>{poster_html}</a>"
                "<div class='match-copy'>"
                f"<h2>{html.escape(title)} <span>- ({html.escape(candidate_year)})</span></h2>"
                f"<p>{html.escape(overview)}</p>"
                f"<a class='apply' href='/tv/apply-match/{show.id}?tmdb_id={html.escape(tmdb_id)}'>{'Current Match' if selected else 'Use This Match'}</a>"
                "</div>"
                f"<div class='check'>{'✓' if selected else ''}</div>"
                "</article>"
            )
        body = (
            FIX_MATCH_TEMPLATE
            .replace("{{SHOW_ID}}", str(show.id))
            .replace("{{TITLE}}", html.escape(metadata.get("title") or show.title))
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

    def apply_match(self, show_id: str, parsed):
        try:
            show = tv_index.show_by_id[int(show_id)]
            tmdb_id = int(urllib.parse.parse_qs(parsed.query).get("tmdb_id", [""])[0])
        except Exception:
            raise FileNotFoundError("Invalid match request")
        apply_tmdb_match(show, tmdb_id)
        self.send_response(302)
        self.send_header("Location", f"/tv/show/{show.id}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def upload_art(self, show_id: str):
        try:
            show = tv_index.show_by_id[int(show_id)]
        except Exception:
            raise FileNotFoundError("Show id not found")
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0 or content_length > 22 * 1024 * 1024:
            raise ValueError("Invalid upload size")
        body = self.rfile.read(content_length)
        fields, files = parse_multipart_form(self.headers.get("Content-Type", ""), body)
        filename, data, mime = files.get("art_file", ("", b"", ""))
        save_uploaded_art(show, fields.get("art_type", "poster"), filename, data, mime)
        self.send_response(302)
        self.send_header("Location", f"/tv/show/{show.id}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def download_episode(self, item_id: str):
        episode = safe_episode(item_id)
        return self.serve_file(episode.path, disposition="attachment")

    def serve_episode_thumbnail(self, request_path: str):
        name = Path(urllib.parse.unquote(request_path)).name
        if not re.fullmatch(r"[a-f0-9]{40}\.jpg", name):
            raise PermissionError("Invalid thumbnail path")
        path = (EPISODE_THUMB_DIR / name).resolve()
        if not str(path).startswith(str(EPISODE_THUMB_DIR.resolve()) + os.sep) or not path.is_file():
            raise FileNotFoundError("Thumbnail not found")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def play_episode(self, item_id: str):
        episode = safe_episode(item_id)
        return self.serve_file(episode.path, disposition="inline")

    def download_season(self, key: str):
        season = safe_season(key)
        show = next((candidate for candidate in tv_index.shows if any(existing is season for existing in candidate.seasons.values())), None)
        metadata = metadata_for(show) if show else {}
        show_name = metadata.get("title") or (show.title if show else "TV Show")
        zip_name = f"{download_safe_filename(show_name)} - {download_safe_filename(season.label)}.zip"
        filename = urllib.parse.quote(zip_name)
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{filename}")
        self.end_headers()
        manifest = {
            "schema": "cinemediavault-season-v1",
            "show": metadata.get("title") or (show.title if show else ""),
            "season": season.label,
            "poster": poster_url_for(show) if show else "",
            "summary": metadata.get("overview") or "",
            "episodes": {},
        }
        for episode in season.episodes:
            if episode.path.is_file():
                manifest["episodes"][episode.path.name] = {
                    "title": display_episode_title(metadata, episode),
                    "subtitle": f"{episode_number_label(episode)} {episode_air_date(metadata, episode)}".strip(),
                    "summary": episode_summary(metadata, episode),
                    "poster": poster_url_for(show) if show else "",
                    "still": episode_still_url(metadata, episode),
                    "show": manifest["show"],
                    "season": season.label,
                    "episode": episode_number_label(episode),
                }
        with zipfile.ZipFile(self.wfile, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("cinemediavault-season.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for episode in season.episodes:
                if episode.path.is_file():
                    archive.write(episode.path, arcname=episode.path.name)

    def serve_file(self, path: Path, disposition: str = "attachment"):
        file_size = path.stat().st_size
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
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded_name = urllib.parse.quote(path.name)
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{encoded_name}")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()
        with path.open("rb") as handle:
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
  <title>TV Shows</title>
  <style>
    :root { color-scheme: dark; --bg:#11151b; --panel:#191f28; --tile:#0d1117; --line:#2b3440; --text:#f4f7fb; --muted:#a8b2bf; --accent:#28a2ff; --accent2:#f5a524; }
    * { box-sizing: border-box; }
    html { scroll-behavior:smooth; }
    body { margin:0; font-family:Arial, Helvetica, sans-serif; background:var(--bg); color:var(--text); min-height:100vh; overflow-x:hidden; }
    .library-backdrop { position:fixed; inset:-90px -160px auto -160px; height:calc(100vh + 190px); z-index:0; display:grid; grid-template-columns:repeat(15, minmax(58px, 1fr)); gap:10px; transform:rotate(-10deg) translateY(-34px); opacity:.34; pointer-events:none; overflow:hidden; filter:saturate(1.12) contrast(1.04); }
    .library-backdrop::after { content:""; position:absolute; inset:-20px; background:linear-gradient(180deg,rgba(17,21,27,.58) 0%,rgba(17,21,27,.76) 52%,rgba(17,21,27,.96) 100%),linear-gradient(90deg,rgba(17,21,27,.82),rgba(17,21,27,.18),rgba(17,21,27,.82)); }
    .library-backdrop img { width:100%; aspect-ratio:2/3; object-fit:cover; border-radius:10px; box-shadow:0 18px 36px rgba(0,0,0,.55); }
    .library-backdrop img:nth-child(3n) { transform:translateY(34px); }
    .library-backdrop img:nth-child(4n) { transform:translateY(-22px); }
    .library-backdrop img:nth-child(5n) { transform:translateY(56px); }
    header { position:sticky; top:0; z-index:4; background:rgba(17,21,27,.96); backdrop-filter:blur(10px); border-bottom:1px solid var(--line); padding:22px 22px 14px; }
    .top-row { display:flex; align-items:flex-start; justify-content:space-between; gap:18px; }
    .library-trigger { display:inline-grid; gap:2px; border:0; background:transparent; color:var(--text); padding:0; text-align:left; cursor:pointer; }
    .library-trigger h1::after { content:"⌄"; margin-left:8px; color:var(--muted); font-size:18px; }
    .header-actions { display:flex; align-items:center; gap:10px; flex-wrap:nowrap; justify-content:flex-end; }
    .home-link { display:inline-flex; align-items:center; justify-content:center; min-height:38px; padding:0 14px; border-radius:999px; background:rgba(255,255,255,.10); border:1px solid var(--line); color:#fff; text-decoration:none; font-weight:800; }
    .search-button { min-width:38px; min-height:38px; border-radius:999px; background:rgba(255,255,255,.08); border:1px solid var(--line); color:#fff; font-size:18px; line-height:1; padding:0 12px; }
    .search-button.active { background:var(--accent); border-color:var(--accent); color:#06111c; }
    .playback-toggle { min-height:38px; min-width:72px; display:inline-flex; align-items:center; justify-content:center; padding:0 12px; border-radius:999px; border:1px solid var(--line); background:rgba(255,255,255,.08); color:#fff; font-size:12px; font-weight:900; cursor:pointer; }
    .playback-toggle.hls { color:#06111c; background:var(--accent2); border-color:var(--accent2); }
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
    .bar { display:none; grid-template-columns:minmax(240px, 560px) auto; gap:12px; margin-top:12px; align-items:center; }
    body.search-open .bar { display:grid; }
    input { width:100%; padding:13px 14px; border:1px solid #3a4552; border-radius:8px; background:#252b34; color:var(--text); font-size:16px; outline:none; }
    input:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(40,162,255,.16); }
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
    .show-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(106px, 1fr)); gap:16px 13px; align-items:start; }
    .shelf-row { display:flex; gap:14px; overflow-x:auto; overscroll-behavior-inline:contain; scroll-snap-type:x proximity; padding:2px 2px 10px; }
    .shelf-row .show-card { flex:0 0 106px; scroll-snap-align:start; }
    .shelf-extra { flex:0 0 106px; }
    .shelf-extra .show-card { width:100%; }
    .see-all-card { flex:0 0 106px; aspect-ratio:2/3; display:grid; place-items:center; align-content:center; gap:6px; border:1px solid var(--line); border-radius:6px; background:rgba(255,255,255,.08); color:#fff; box-shadow:0 10px 22px rgba(0,0,0,.24); }
    .see-all-card span { font-size:15px; font-weight:900; }
    .see-all-card small { color:var(--muted); font-size:11px; }
    .show-card { min-width:0; }
    .poster-link { display:block; width:100%; position:relative; aspect-ratio:2 / 3; background:#080b10; border:1px solid #222b36; border-radius:6px; overflow:hidden; box-shadow:0 10px 22px rgba(0,0,0,.34); text-decoration:none; }
    .poster-link:focus-visible { outline:3px solid var(--accent); outline-offset:3px; }
    .poster { width:100%; height:100%; object-fit:cover; display:block; background:#05070a; }
    .poster.missing { width:100%; height:100%; display:block; background:linear-gradient(145deg,#202833,#090d12); }
    .poster.missing::after { content:"No Poster"; display:grid; place-items:center; width:100%; height:100%; color:#8290a0; font-size:12px; font-weight:800; }
    .show-info { padding:8px 2px 0; }
    .title { font-weight:800; color:#fff; font-size:13px; line-height:1.25; overflow-wrap:anywhere; }
    .size { margin-top:4px; color:#aeb9c6; font-size:12px; }
    .hidden { display:none; }
    @media (min-width: 1200px) {
      .show-grid { grid-template-columns:repeat(auto-fill, minmax(120px, 1fr)); gap:18px 14px; }
      .shelf-row .show-card, .shelf-extra, .see-all-card { flex-basis:120px; }
    }
    @media (min-width: 900px) {
      header { padding:18px 16px 10px; }
      .top-row { gap:12px; }
      .header-actions { gap:8px; }
      .home-link, button, .playback-toggle { min-height:22px; padding:0 7px; font-size:9px; border-radius:12px; }
      .search-button { min-width:22px; min-height:22px; font-size:10px; padding:0 7px; }
      h1 { font-size:13px; }
      .library-trigger h1::after { font-size:9px; margin-left:4px; }
      .meta { font-size:8px; margin-top:1px; }
      .bar { grid-template-columns:minmax(240px, 520px) auto; gap:10px; margin-top:10px; }
      input { padding:6px 8px; font-size:9px; border-radius:5px; }
      .page-shell { grid-template-columns:minmax(0, 1fr); gap:10px; padding:14px 12px 36px 16px; }
      body.search-open .page-shell { grid-template-columns:minmax(0, 1fr) 38px; }
      .letter-rail { top:92px; max-height:calc(100vh - 104px); gap:3px; padding:5px 3px; }
      .letter { width:21px; min-height:18px; font-size:8px; border-radius:4px; }
      .letter-section { scroll-margin-top:104px; margin-bottom:24px; }
      .letter-section h2 { font-size:10px; margin-bottom:7px; padding-bottom:5px; }
      .show-grid { grid-template-columns:repeat(auto-fill, minmax(72px, 72px)); justify-content:start; gap:10px 9px; }
      .shelf-row { gap:9px; padding-bottom:7px; }
      .shelf-row .show-card, .shelf-extra, .see-all-card { flex-basis:72px; }
      .poster-link { border-radius:4px; box-shadow:0 7px 16px rgba(0,0,0,.30); }
      .show-info { padding-top:5px; }
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
      .show-grid { grid-template-columns:repeat(2, minmax(0, 1fr)); gap:14px 10px; }
      .shelf-row .show-card, .shelf-extra, .see-all-card { flex-basis:104px; }
      .title { font-size:14px; }
    }
  </style>
</head>
<body>
  <div class="library-backdrop" aria-hidden="true">{{BACKDROP_POSTERS}}</div>
  <header>
    <div class="top-row">
      <button class="library-trigger" id="libraryTrigger"><h1>TV Shows</h1><span class="meta">{{SERVER_NAME}}</span></button>
      <div class="header-actions"><a class="home-link" href="/">Home</a><button class="playback-toggle" id="playbackModeButton" type="button" data-mode="direct">Direct</button><button class="search-button" id="searchToggle" type="button" aria-label="Search" title="Search">⌕</button></div>
    </div>
    <div class="meta"><span id="visibleCount">{{COUNT}}</span> of {{COUNT}} shows - {{EPISODES}} episodes</div>
    <div class="bar">
      <input id="search" type="search" placeholder="Search shows, actors, or genres">
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
    <main id="top">{{SECTIONS}}</main>
    <nav class="letter-rail" aria-label="Jump to TV show letter">
      <button class="letter active" data-letter="ALL">All</button>
      {{LETTERS}}
    </nav>
  </div>
  <script>
    const search = document.getElementById("search");
    const searchToggle = document.getElementById("searchToggle");
    const libraryModal = document.getElementById("libraryModal");
    document.getElementById("libraryTrigger").addEventListener("click", () => libraryModal.classList.add("open"));
    document.getElementById("closeLibraries").addEventListener("click", () => libraryModal.classList.remove("open"));
    libraryModal.addEventListener("click", (event) => { if (event.target === libraryModal) libraryModal.classList.remove("open"); });
    const visibleCount = document.getElementById("visibleCount");
    const cards = [...document.querySelectorAll(".show-card")];
    const sections = [...document.querySelectorAll(".letter-section")];
    function applyFilters() {
      const q = search.value.trim().toLowerCase();
      let count = 0;
      for (const card of cards) {
        const show = !q || card.dataset.title.includes(q);
        card.classList.toggle("hidden", !show);
        if (show && card.dataset.recent !== "1") count++;
      }
      for (const section of sections) {
        const visibleCards = section.querySelectorAll(".show-card:not(.hidden)").length;
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
    document.querySelectorAll("[data-shelf-target]").forEach((button) => {
      button.addEventListener("click", () => {
        const shelf = document.getElementById(button.dataset.shelfTarget);
        if (!shelf) return;
        shelf.querySelectorAll(".shelf-extra").forEach((item) => item.classList.remove("hidden"));
        button.remove();
        applyFilters();
      });
    });
    document.querySelectorAll(".letter").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll(".letter").forEach((item) => item.classList.toggle("active", item === button));
        const letter = button.dataset.letter;
        const target = letter === "ALL" ? document.getElementById("top") : document.getElementById(`section-${letter}`);
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
    :root { color-scheme: dark; --bg:#070a0f; --text:#f7fbff; --muted:#c9d2df; --line:rgba(255,255,255,.18); --green:#2ee66b; --gold:#f5a524; }
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
    .detail-shell { position:relative; z-index:1; max-width:1220px; min-height:calc(100vh - 80px); margin:0 auto; display:grid; grid-template-columns:minmax(0, 680px) minmax(260px, 380px); gap:46px; align-items:start; padding:54px 0 34px; }
    .poster-wrap { display:flex; justify-content:center; order:2; opacity:.98; position:sticky; top:92px; }
    .detail-poster { width:min(350px, 100%); aspect-ratio:2/3; object-fit:cover; border-radius:18px; border:1px solid var(--line); box-shadow:0 30px 80px rgba(0,0,0,.68); background:#05070a; }
    .detail-poster.missing { display:grid; place-items:center; background:linear-gradient(145deg,#202833,#090d12); }
    .detail-poster.missing::after { content:"No Poster"; color:#8390a0; font-weight:800; }
    .detail-info { color:#fff; order:1; max-width:760px; }
    h1 { margin:0; text-align:left; font-size:clamp(44px, 6.6vw, 84px); line-height:.94; letter-spacing:0; text-shadow:0 7px 26px rgba(0,0,0,.6); max-width:720px; }
    .meta-row { display:flex; flex-wrap:wrap; gap:10px 14px; align-items:center; margin:16px 0; color:#edf4ff; font-size:17px; }
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
    .action-row { display:grid; grid-template-columns:repeat(5, minmax(70px,100px)); gap:14px; margin:0 0 26px; }
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
    .cast-list { display:flex; flex-wrap:wrap; gap:10px; padding:0; margin:0 0 28px; list-style:none; }
    .cast-list li { border:1px solid rgba(255,255,255,.16); background:rgba(255,255,255,.11); border-radius:999px; padding:9px 12px; color:#eef5ff; font-size:14px; }
    .seasons { margin-top:26px; display:grid; gap:12px; }
    .season { border:1px solid rgba(255,255,255,.16); background:rgba(8,12,18,.42); border-radius:14px; overflow:hidden; backdrop-filter:blur(10px); }
    .season summary { display:flex; justify-content:space-between; gap:14px; align-items:center; padding:15px 16px; cursor:pointer; font-weight:900; }
    .season summary small { display:block; margin-top:3px; color:var(--muted); font-size:12px; font-weight:500; }
    .season-download, .episode-download, .play-link, .episode-more, .episode-admin { color:#06111c; border-radius:999px; padding:6px 9px; text-decoration:none; font-size:11px; font-weight:900; white-space:nowrap; }
    .season-download, .episode-download { background:var(--gold); }
    .play-link { background:var(--green); }
    .episode-more { background:rgba(255,255,255,.18); color:#fff; border:1px solid rgba(255,255,255,.16); }
    .episode-admin { background:#344054; color:#fff; border:1px solid rgba(255,255,255,.16); }
    .season-download-tools, .episode-actions { display:flex; gap:7px; align-items:center; flex-wrap:wrap; justify-content:flex-end; }
    .download-toggle, .episode-compress { display:inline-flex; align-items:center; gap:5px; color:#fff; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.14); border-radius:999px; padding:6px 9px; font-size:11px; font-weight:900; white-space:nowrap; }
    .download-toggle input, .episode-compress input { width:14px; height:14px; accent-color:var(--gold); }
    .mobile-ready { display:none; color:#04110a; background:#26e86b; border-radius:999px; padding:6px 9px; text-decoration:none; font-size:11px; font-weight:900; white-space:nowrap; }
    .mobile-ready.ready { display:inline-flex; }
    .mobile-status { color:rgba(239,245,255,.76); font-size:11px; max-width:240px; }
    ol { list-style:none; padding:0 16px 12px; margin:0; }
    .season li { display:grid; grid-template-columns:minmax(0,1fr) auto auto; gap:10px; align-items:center; padding:12px 0; border-top:1px solid rgba(255,255,255,.08); color:#eef5ff; }
    .episode-main { min-width:0; display:grid; grid-template-columns:112px minmax(0,1fr); gap:14px; align-items:center; color:#eef5ff; text-decoration:none; }
    .episode-still { width:112px; aspect-ratio:16/9; object-fit:cover; border-radius:8px; background:rgba(255,255,255,.08); }
    .episode-still.missing::after { content:""; display:block; width:100%; height:100%; border-radius:8px; background:linear-gradient(145deg, rgba(255,255,255,.12), rgba(255,255,255,.03)); }
    .episode-copy { min-width:0; display:block; }
    .episode-title { display:block; overflow-wrap:anywhere; font-weight:900; font-size:17px; }
    .episode-date { display:block; margin-top:3px; color:var(--muted); font-size:12px; font-weight:500; }
    .episode-summary { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; margin-top:5px; color:rgba(239,245,255,.76); font-size:13px; line-height:1.35; }
    .episode-size { color:var(--muted); font-size:12px; white-space:nowrap; }
    @media (max-width: 860px) {
      .detail-page { min-height:100svh; padding:18px 22px 30px; overflow:auto; }
      .detail-page::before { background-position:center top; opacity:.66; filter:blur(7px) saturate(1.25); }
      .detail-page::after { background:linear-gradient(180deg,rgba(0,0,0,.12) 0%,rgba(0,0,0,.38) 28%,rgba(18,34,13,.82) 100%), linear-gradient(90deg,rgba(0,0,0,.88) 0%,rgba(0,0,0,.50) 48%,rgba(0,0,0,.76) 100%); }
      .topbar { margin-bottom:18px; }
      .detail-shell { min-height:auto; grid-template-columns:1fr; gap:18px; padding:0; }
      .poster-wrap { order:1; min-height:230px; align-items:end; position:static; }
      .detail-poster { width:min(280px, 62vw); border-radius:16px; }
      .detail-info { order:2; text-align:left; max-width:none; }
      h1 { text-align:center; font-size:clamp(42px, 12vw, 70px); }
      .meta-row, .score-row { justify-content:center; }
      .playbar { grid-template-columns:1fr auto; }
      .action-row { grid-template-columns:repeat(5, minmax(64px,1fr)); gap:10px; }
      .action span { width:50px; height:50px; }
      .summary { font-size:17px; }
      .season li { grid-template-columns:1fr; align-items:start; }
      .episode-size { display:none; }
      .episode-main { grid-template-columns:104px minmax(0,1fr); }
      .episode-still { width:104px; }
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
      .season summary { align-items:flex-start; flex-direction:column; }
      .episode-actions { grid-column:1 / -1; justify-content:flex-start; }
      .episode-main { grid-template-columns:92px minmax(0,1fr); gap:10px; }
      .episode-still { width:92px; }
      .episode-title { font-size:16px; }
    }
  </style>
</head>
<body>
  <main class="detail-page"{{BACKGROUND_STYLE}}>
    <div class="topbar">
      <a class="circle" href="/tv" aria-label="Back to TV Shows">←</a>
      <div class="topbar-actions"><button class="circle cast-button" type="button" aria-label="Cast" title="Cast"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 10V7.5C5 6.1 6.1 5 7.5 5h17C25.9 5 27 6.1 27 7.5v17c0 1.4-1.1 2.5-2.5 2.5H22" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><path d="M5 21c3.3 0 6 2.7 6 6M5 15c6.6 0 12 5.4 12 12M5 27h.1" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round"/></svg></button><a class="circle" href="/" aria-label="Home" title="Home"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 15.5 16 6l11 9.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><path d="M8.5 14.5V27h15V14.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/><path d="M13 27v-8h6v8" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/></svg></a></div>
    </div>
    <div class="detail-shell">
      <div class="poster-wrap">{{POSTER}}</div>
      <section class="detail-info">
        <h1>{{TITLE}}</h1>
        <div class="meta-row"><span>{{YEAR}}</span><span>{{SEASONS}} seasons</span><span>{{EPISODES}} episodes</span><span>{{GENRES}}</span><span class="rating">Local</span></div>
        <div class="score-row"><span class="score">CineVault</span><span class="score">Ready to play</span><span class="score">Season downloads</span>{{TMDB_SCORE}}</div>
        <div class="playbar">
          <a class="play" id="playLink" href="{{PLAY}}">▶ Play</a>
          <a class="quick-play" href="{{PLAY}}" aria-label="Start from beginning">Start over</a>
        </div>
        <div class="play-mode-row">{{PLAY_MODE_LINKS}}</div>
        <div class="action-row">
          <a class="action mark-watched" href="/tv"><span>✓</span>Mark Watched</a>
          <a class="action download" href="{{DOWNLOAD}}"><span>↓</span>Download</a>
          <a class="action" href="/tv/fix-match/{{SHOW_ID}}"><span>⌕</span>Fix Match</a>
          <a class="action more-toggle" href="#cast" data-more-toggle="1"><span>⋮</span>More</a>
          {{ADMIN_ACTION}}
        </div>
        <p class="summary">{{SUMMARY}}</p>
        <div class="library-title">{{LIBRARY_TITLE}}</div>
        <div class="file-grid">
          <div class="label">Video</div><div>Local episode stream</div>
          <div class="label">Audio</div><div>Original audio</div>
          <div class="label">Subtitles</div><div>Off</div>
          <div class="label">Size</div><div>{{SIZE}}</div>
        </div>
        <section class="cast-panel" id="cast">
          <h2>Cast & Crew ›</h2>
          <ul class="cast-list">{{ACTORS}}</ul>
        </section>
        <h2 id="seasons">Seasons</h2>
        <div class="seasons">{{SEASON_BLOCKS}}</div>
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
    const seasonCards = [...document.querySelectorAll("details.season")];
    const focusSeason = selected => seasonCards.forEach(card => {
      card.hidden = Boolean(selected && card !== selected);
    });
    seasonCards.forEach(card => card.addEventListener("toggle", () => {
      if (card.open) focusSeason(card);
      else if (!seasonCards.some(item => item.open)) focusSeason(null);
    }));
    const initiallyOpenSeason = seasonCards.find(card => card.open);
    if (initiallyOpenSeason) focusSeason(initiallyOpenSeason);
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
      return element.closest("[data-mobile-scope]") || element.closest(".episode-actions") || element.parentElement;
    }
    function mobileScopeFor(link, container) {
      return link.dataset.mobileScope || (container && container.dataset.mobileScope) || "";
    }
    function mobileItemFor(link, container) {
      return link.dataset.mobileItem || (container && container.dataset.mobileItem) || "";
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
    async function pollMobileDownload(container, scope, itemId) {
      if (!container || !scope || !itemId) return;
      try {
        const response = await fetch(`/api/mobile-download/status?scope=${encodeURIComponent(scope)}&item_id=${encodeURIComponent(itemId)}`, {cache:"no-store"});
        const payload = await response.json();
        setMobileReady(container, payload);
        if (payload.ready) {
          setMobileStatus(container, "Mobile copy ready");
        } else if (payload.status === "running" || payload.status === "queued") {
          setMobileStatus(container, `${payload.message || "Preparing"} ${payload.progress || 0}%`);
          setTimeout(() => pollMobileDownload(container, scope, itemId), 5000);
        } else if (payload.status === "failed" || payload.status === "error") {
          setMobileStatus(container, payload.error || "Mobile prepare failed");
        }
      } catch (_) {}
    }
    document.querySelectorAll("[data-mobile-request]").forEach(link => {
      link.addEventListener("click", async event => {
        const container = mobileContainerFor(link);
        const toggle = container && container.querySelector("[data-mobile-toggle]");
        if (!toggle || !toggle.checked) return;
        event.preventDefault();
        const scope = mobileScopeFor(link, container);
        const itemId = mobileItemFor(link, container);
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
            setTimeout(() => pollMobileDownload(container, scope, itemId), 3000);
          }
        } catch (_) {
          setMobileStatus(container, "Could not queue mobile copy");
        }
      });
      const container = mobileContainerFor(link);
      pollMobileDownload(container, mobileScopeFor(link, container), mobileItemFor(link, container));
    });
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
    .upload-row { display:grid; grid-template-columns:160px 1fr auto; gap:10px; }
    select { min-height:44px; border-radius:12px; border:1px solid var(--line); background:#151922; color:#fff; padding:0 12px; font:inherit; font-weight:800; }
    input[type=file] { padding:10px; }
    input, button { min-height:44px; border-radius:12px; border:1px solid var(--line); font:inherit; font-weight:800; }
    input { background:#151922; color:#fff; padding:0 12px; }
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
      <a class="back" href="/tv/show/{{SHOW_ID}}" aria-label="Back">←</a>
      <div><h1>{{TITLE}}</h1><div class="sub">Fix Match</div></div>
    </header>
    <form method="get" action="/tv/fix-match/{{SHOW_ID}}">
      <input name="q" value="{{QUERY}}" placeholder="Search TMDB title">
      <input name="year" value="{{YEAR}}" placeholder="Year">
      <button type="submit">Search</button>
    </form>
    <form class="upload" method="post" action="/tv/upload-art/{{SHOW_ID}}" enctype="multipart/form-data">
      <h2>Upload Custom Art</h2>
      <div class="upload-row">
        <select name="art_type">
          <option value="poster">Poster / card art</option>
          <option value="foreground">Detail foreground</option>
          <option value="backdrop">Background</option>
        </select>
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
    parser = argparse.ArgumentParser(description="TV show download web library")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8094)
    args = parser.parse_args()
    if not tv_index.load_cache():
        tv_index.refresh()
    load_poster_map()
    load_metadata_map()
    tv_index.refresh_background()
    print(f"Loaded {len(tv_index.shows)} shows and {len(tv_index.episode_by_id)} episodes", flush=True)
    print(f"Serving on http://{args.host}:{args.port}/", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
