#!/usr/bin/env python3
import argparse
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = "/home/jnicolas/cinevault-data/cinevault.db"
MOVIE_APP_DIR = Path(os.environ.get("MOVIE_APP_DIR", "/home/jnicolas/media-download-library"))
TV_APP_DIR = Path(os.environ.get("TV_APP_DIR", "/home/jnicolas/tv-download-library"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path, default):
    if not path.is_file():
        return default
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(text)
        return obj


def norm_sort_title(value: str) -> str:
    value = re.sub(r"^(the|a|an)\s+", "", value.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def episode_key(title: str) -> str:
    match = re.search(r"\bS(\d{1,2})(?:E|EP)(\d{1,3})\b", title, re.I)
    if not match:
        return ""
    return f"S{int(match.group(1)):02d}E{int(match.group(2)):02d}"


def season_number(label: str) -> int | None:
    match = re.search(r"(\d+)", label or "")
    return int(match.group(1)) if match else None


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS libraries (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  root_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_items (
  id INTEGER PRIMARY KEY,
  library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
  external_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  sort_title TEXT NOT NULL,
  year TEXT,
  file_path TEXT NOT NULL UNIQUE,
  rel_path TEXT NOT NULL,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  modified_at REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(library_id, external_id)
);

CREATE TABLE IF NOT EXISTS movies (
  media_item_id INTEGER PRIMARY KEY REFERENCES media_items(id) ON DELETE CASCADE,
  tmdb_id INTEGER,
  runtime_seconds INTEGER,
  vote_average REAL,
  vote_count INTEGER,
  overview TEXT,
  release_date TEXT
);

CREATE TABLE IF NOT EXISTS tv_shows (
  id INTEGER PRIMARY KEY,
  library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
  external_id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  sort_title TEXT NOT NULL,
  year TEXT,
  root_path TEXT,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  episode_count INTEGER NOT NULL DEFAULT 0,
  season_count INTEGER NOT NULL DEFAULT 0,
  tmdb_id INTEGER,
  vote_average REAL,
  vote_count INTEGER,
  overview TEXT,
  first_air_date TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tv_seasons (
  id INTEGER PRIMARY KEY,
  show_id INTEGER NOT NULL REFERENCES tv_shows(id) ON DELETE CASCADE,
  season_number INTEGER,
  label TEXT NOT NULL,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  episode_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE(show_id, label)
);

CREATE TABLE IF NOT EXISTS tv_episodes (
  id INTEGER PRIMARY KEY,
  show_id INTEGER NOT NULL REFERENCES tv_shows(id) ON DELETE CASCADE,
  season_id INTEGER NOT NULL REFERENCES tv_seasons(id) ON DELETE CASCADE,
  external_id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  episode_title TEXT,
  season_number INTEGER,
  episode_number INTEGER,
  air_date TEXT,
  overview TEXT,
  runtime_minutes INTEGER,
  vote_average REAL,
  file_path TEXT NOT NULL UNIQUE,
  rel_path TEXT NOT NULL,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  modified_at REAL NOT NULL DEFAULT 0,
  still_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS media_people (
  media_type TEXT NOT NULL,
  media_id INTEGER NOT NULL,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  ordering INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(media_type, media_id, person_id, role)
);

CREATE TABLE IF NOT EXISTS genres (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS media_genres (
  media_type TEXT NOT NULL,
  media_id INTEGER NOT NULL,
  genre_id INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
  PRIMARY KEY(media_type, media_id, genre_id)
);

CREATE TABLE IF NOT EXISTS artwork (
  id INTEGER PRIMARY KEY,
  media_type TEXT NOT NULL,
  media_id INTEGER NOT NULL,
  art_type TEXT NOT NULL,
  path TEXT NOT NULL,
  source TEXT,
  is_primary INTEGER NOT NULL DEFAULT 1,
  UNIQUE(media_type, media_id, art_type, path)
);

CREATE TABLE IF NOT EXISTS watch_progress (
  id INTEGER PRIMARY KEY,
  client_id TEXT NOT NULL,
  media_type TEXT NOT NULL,
  media_id INTEGER NOT NULL,
  position_seconds REAL NOT NULL DEFAULT 0,
  duration_seconds REAL NOT NULL DEFAULT 0,
  progress REAL NOT NULL DEFAULT 0,
  watched INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  UNIQUE(client_id, media_type, media_id)
);

CREATE TABLE IF NOT EXISTS playlists (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_items (
  playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  media_type TEXT NOT NULL,
  media_id INTEGER NOT NULL,
  ordering INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(playlist_id, media_type, media_id)
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  message TEXT,
  movies_count INTEGER NOT NULL DEFAULT 0,
  shows_count INTEGER NOT NULL DEFAULT 0,
  episodes_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cast_devices (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT,
  host TEXT,
  port INTEGER,
  model TEXT,
  playable INTEGER NOT NULL DEFAULT 0,
  last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_items_title ON media_items(sort_title, year);
CREATE INDEX IF NOT EXISTS idx_media_items_rel_path ON media_items(rel_path);
CREATE INDEX IF NOT EXISTS idx_movies_tmdb ON movies(tmdb_id);
CREATE INDEX IF NOT EXISTS idx_tv_shows_title ON tv_shows(sort_title, year);
CREATE INDEX IF NOT EXISTS idx_tv_shows_tmdb ON tv_shows(tmdb_id);
CREATE INDEX IF NOT EXISTS idx_tv_episodes_show_season_episode ON tv_episodes(show_id, season_number, episode_number);
CREATE INDEX IF NOT EXISTS idx_watch_progress_updated ON watch_progress(updated_at);
CREATE INDEX IF NOT EXISTS idx_scan_runs_started ON scan_runs(started_at);
"""


def upsert_library(conn, kind: str, name: str, root_path: str) -> int:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO libraries(kind, name, root_path, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(kind) DO UPDATE SET
          name=excluded.name,
          root_path=excluded.root_path,
          updated_at=excluded.updated_at
        """,
        (kind, name, root_path, now, now),
    )
    return int(conn.execute("SELECT id FROM libraries WHERE kind=?", (kind,)).fetchone()["id"])


def upsert_person(conn, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO people(name) VALUES(?)", (name,))
    return int(conn.execute("SELECT id FROM people WHERE name=?", (name,)).fetchone()["id"])


def upsert_genre(conn, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO genres(name) VALUES(?)", (name,))
    return int(conn.execute("SELECT id FROM genres WHERE name=?", (name,)).fetchone()["id"])


def replace_people(conn, media_type: str, media_id: int, names: list[str], role: str = "actor") -> None:
    conn.execute("DELETE FROM media_people WHERE media_type=? AND media_id=? AND role=?", (media_type, media_id, role))
    for order, name in enumerate(names or [], start=1):
        if not name:
            continue
        person_id = upsert_person(conn, str(name).strip())
        conn.execute(
            "INSERT OR IGNORE INTO media_people(media_type, media_id, person_id, role, ordering) VALUES(?, ?, ?, ?, ?)",
            (media_type, media_id, person_id, role, order),
        )


def replace_genres(conn, media_type: str, media_id: int, names: list[str]) -> None:
    conn.execute("DELETE FROM media_genres WHERE media_type=? AND media_id=?", (media_type, media_id))
    for name in names or []:
        if not name:
            continue
        genre_id = upsert_genre(conn, str(name).strip())
        conn.execute(
            "INSERT OR IGNORE INTO media_genres(media_type, media_id, genre_id) VALUES(?, ?, ?)",
            (media_type, media_id, genre_id),
        )


def replace_art(conn, media_type: str, media_id: int, art_type: str, path: str, source: str) -> None:
    if not path:
        return
    conn.execute("DELETE FROM artwork WHERE media_type=? AND media_id=? AND art_type=?", (media_type, media_id, art_type))
    conn.execute(
        "INSERT OR IGNORE INTO artwork(media_type, media_id, art_type, path, source, is_primary) VALUES(?, ?, ?, ?, ?, 1)",
        (media_type, media_id, art_type, path, source),
    )


def import_movies(conn) -> int:
    live = load_json(MOVIE_APP_DIR / "movie-live-index.json", {})
    metadata = load_json(MOVIE_APP_DIR / "movie-metadata-map.json", {})
    posters = load_json(MOVIE_APP_DIR / "poster-map.json", {})
    library_id = upsert_library(conn, "movies", "Movies", live.get("root") or "")
    now = utc_now()
    count = 0
    for row in live.get("movies", []):
        rel_path = row.get("rel_path") or ""
        title = row.get("title") or Path(row.get("path", "")).stem
        meta = metadata.get(rel_path) or metadata.get(title) or {}
        display_title = meta.get("title") or title
        year = str(meta.get("year") or "")
        conn.execute(
            """
            INSERT INTO media_items(library_id, external_id, kind, title, sort_title, year, file_path, rel_path, size_bytes, modified_at, created_at, updated_at)
            VALUES(?, ?, 'movie', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
              title=excluded.title, sort_title=excluded.sort_title, year=excluded.year,
              rel_path=excluded.rel_path, size_bytes=excluded.size_bytes, modified_at=excluded.modified_at,
              updated_at=excluded.updated_at
            """,
            (
                library_id,
                rel_path,
                display_title,
                norm_sort_title(display_title),
                year,
                row.get("path") or "",
                rel_path,
                int(row.get("size") or 0),
                float(row.get("modified") or 0),
                now,
                now,
            ),
        )
        media_id = int(conn.execute("SELECT id FROM media_items WHERE file_path=?", (row.get("path") or "",)).fetchone()["id"])
        conn.execute(
            """
            INSERT INTO movies(media_item_id, tmdb_id, runtime_seconds, vote_average, vote_count, overview, release_date)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_item_id) DO UPDATE SET
              tmdb_id=excluded.tmdb_id, runtime_seconds=excluded.runtime_seconds,
              vote_average=excluded.vote_average, vote_count=excluded.vote_count,
              overview=excluded.overview, release_date=excluded.release_date
            """,
            (
                media_id,
                meta.get("tmdb_id"),
                meta.get("runtime_seconds") or None,
                meta.get("vote_average"),
                meta.get("vote_count"),
                meta.get("overview"),
                meta.get("release_date"),
            ),
        )
        replace_people(conn, "movie", media_id, meta.get("actors") or [])
        replace_genres(conn, "movie", media_id, meta.get("genres") or [])
        replace_art(conn, "movie", media_id, "poster", posters.get(rel_path) or posters.get(title) or "", "tmdb")
        count += 1
    return count


def import_tv(conn) -> tuple[int, int]:
    live = load_json(TV_APP_DIR / "tv-live-index.json", {})
    metadata = load_json(TV_APP_DIR / "tv-metadata-map.json", {})
    posters = load_json(TV_APP_DIR / "tv-poster-map.json", {})
    library_id = upsert_library(conn, "tv", "TV Shows", live.get("root") or "")
    now = utc_now()
    shows: dict[str, dict] = {}
    for row in live.get("episodes", []):
        shows.setdefault(row.get("show") or "Unknown", {"episodes": [], "size": 0, "seasons": set()})
        shows[row.get("show") or "Unknown"]["episodes"].append(row)
        shows[row.get("show") or "Unknown"]["size"] += int(row.get("size") or 0)
        shows[row.get("show") or "Unknown"]["seasons"].add(row.get("season") or "Season 00")

    show_count = 0
    episode_count = 0
    for show_title, data in sorted(shows.items(), key=lambda item: norm_sort_title(item[0])):
        meta = metadata.get(show_title) or {}
        display_title = meta.get("title") or show_title
        seasons = sorted(data["seasons"], key=lambda label: season_number(label) or 0)
        conn.execute(
            """
            INSERT INTO tv_shows(library_id, external_id, title, sort_title, year, root_path, size_bytes, episode_count, season_count, tmdb_id, vote_average, vote_count, overview, first_air_date, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(external_id) DO UPDATE SET
              title=excluded.title, sort_title=excluded.sort_title, year=excluded.year,
              root_path=excluded.root_path, size_bytes=excluded.size_bytes, episode_count=excluded.episode_count,
              season_count=excluded.season_count, tmdb_id=excluded.tmdb_id, vote_average=excluded.vote_average,
              vote_count=excluded.vote_count, overview=excluded.overview, first_air_date=excluded.first_air_date,
              updated_at=excluded.updated_at
            """,
            (
                library_id,
                show_title,
                display_title,
                norm_sort_title(display_title),
                str(meta.get("year") or ""),
                str(Path(live.get("root") or "") / show_title),
                int(data["size"]),
                len(data["episodes"]),
                len(seasons),
                meta.get("tmdb_id"),
                meta.get("vote_average"),
                meta.get("vote_count"),
                meta.get("overview"),
                meta.get("first_air_date"),
                now,
                now,
            ),
        )
        show_id = int(conn.execute("SELECT id FROM tv_shows WHERE external_id=?", (show_title,)).fetchone()["id"])
        replace_people(conn, "tv_show", show_id, meta.get("actors") or [])
        replace_genres(conn, "tv_show", show_id, meta.get("genres") or [])
        replace_art(conn, "tv_show", show_id, "poster", posters.get(show_title) or "", "tmdb")

        season_ids: dict[str, int] = {}
        for label in seasons:
            season_rows = [ep for ep in data["episodes"] if (ep.get("season") or "Season 00") == label]
            conn.execute(
                """
                INSERT INTO tv_seasons(show_id, season_number, label, size_bytes, episode_count)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(show_id, label) DO UPDATE SET
                  season_number=excluded.season_number, size_bytes=excluded.size_bytes, episode_count=excluded.episode_count
                """,
                (show_id, season_number(label), label, sum(int(ep.get("size") or 0) for ep in season_rows), len(season_rows)),
            )
            season_ids[label] = int(conn.execute("SELECT id FROM tv_seasons WHERE show_id=? AND label=?", (show_id, label)).fetchone()["id"])

        episode_meta = meta.get("episodes") or {}
        for row in data["episodes"]:
            key = episode_key(row.get("title") or row.get("rel_path") or "")
            emeta = episode_meta.get(key) if key else {}
            sn = season_number(row.get("season") or "")
            en = None
            if key:
                match = re.match(r"S(\d+)E(\d+)", key)
                if match:
                    sn = int(match.group(1))
                    en = int(match.group(2))
            conn.execute(
                """
                INSERT INTO tv_episodes(show_id, season_id, external_id, title, episode_title, season_number, episode_number, air_date, overview, runtime_minutes, vote_average, file_path, rel_path, size_bytes, modified_at, still_path, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                  title=excluded.title, episode_title=excluded.episode_title, season_number=excluded.season_number,
                  episode_number=excluded.episode_number, air_date=excluded.air_date, overview=excluded.overview,
                  runtime_minutes=excluded.runtime_minutes, vote_average=excluded.vote_average, rel_path=excluded.rel_path,
                  size_bytes=excluded.size_bytes, modified_at=excluded.modified_at, still_path=excluded.still_path,
                  updated_at=excluded.updated_at
                """,
                (
                    show_id,
                    season_ids[row.get("season") or "Season 00"],
                    row.get("rel_path") or row.get("path") or "",
                    row.get("title") or "",
                    (emeta or {}).get("title"),
                    sn,
                    en,
                    (emeta or {}).get("air_date"),
                    (emeta or {}).get("overview"),
                    (emeta or {}).get("runtime"),
                    (emeta or {}).get("vote_average"),
                    row.get("path") or "",
                    row.get("rel_path") or "",
                    int(row.get("size") or 0),
                    float(row.get("modified") or 0),
                    (emeta or {}).get("still_path"),
                    now,
                    now,
                ),
            )
            episode_count += 1
        show_count += 1
    return show_count, episode_count


def status(conn, db_path: Path) -> dict:
    tables = ["libraries", "media_items", "movies", "tv_shows", "tv_seasons", "tv_episodes", "people", "genres", "artwork", "watch_progress", "scan_runs"]
    counts = {name: conn.execute(f"SELECT COUNT(*) AS c FROM {name}").fetchone()["c"] for name in tables}
    counts["db_path"] = str(db_path)
    counts["db_size_bytes"] = db_path.stat().st_size if db_path.exists() else 0
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or refresh the CineVault SQLite database from existing caches.")
    parser.add_argument("--db", default=os.environ.get("CINEVAULT_DB", DEFAULT_DB))
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    db_path = Path(args.db)
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    if args.status:
        print(json.dumps(status(conn, db_path), indent=2, sort_keys=True))
        return 0

    started = utc_now()
    scan_id = conn.execute(
        "INSERT INTO scan_runs(started_at, status, message) VALUES(?, 'running', 'sqlite import started')",
        (started,),
    ).lastrowid
    try:
        movie_count = import_movies(conn)
        show_count, episode_count = import_tv(conn)
        finished = utc_now()
        conn.execute(
            """
            UPDATE scan_runs
            SET finished_at=?, status='ok', message=?, movies_count=?, shows_count=?, episodes_count=?
            WHERE id=?
            """,
            (finished, "sqlite import completed", movie_count, show_count, episode_count, scan_id),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.execute(
            "UPDATE scan_runs SET finished_at=?, status='failed', message=? WHERE id=?",
            (utc_now(), str(exc), scan_id),
        )
        conn.commit()
        raise
    print(json.dumps(status(conn, db_path), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
