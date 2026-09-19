#!/usr/bin/env python3
"""Incrementally cache TMDB cast/person metadata and profile images for CineVault."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Deployed directly in the CineMediaVault application root.
ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "cinevault-data" / "tmdb-people.sqlite3"
DEFAULT_CATALOG_DB = ROOT / "cinevault-data" / "cinemediavault-lab.db"
DEFAULT_CONFIG = ROOT / "media-download-library" / "tmdb_config.json"
DEFAULT_IMAGES = ROOT / "cinevault-data" / "tmdb-people"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tmdb_people (
  person_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  biography TEXT,
  birthday TEXT,
  deathday TEXT,
  place_of_birth TEXT,
  known_for_department TEXT,
  popularity REAL,
  profile_path TEXT,
  profile_local_path TEXT,
  source TEXT NOT NULL DEFAULT 'TMDB',
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tmdb_people_name ON tmdb_people(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS tmdb_media_credits (
  media_type TEXT NOT NULL CHECK(media_type IN ('movie','tv_show')),
  media_id INTEGER NOT NULL,
  tmdb_media_id INTEGER NOT NULL,
  person_id INTEGER NOT NULL REFERENCES tmdb_people(person_id) ON DELETE CASCADE,
  character_name TEXT,
  credit_order INTEGER NOT NULL DEFAULT 9999,
  credit_id TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(media_type,media_id,person_id,character_name)
);
CREATE INDEX IF NOT EXISTS idx_tmdb_credits_media ON tmdb_media_credits(media_type,media_id,credit_order);
CREATE INDEX IF NOT EXISTS idx_tmdb_credits_person ON tmdb_media_credits(person_id);
CREATE TABLE IF NOT EXISTS tmdb_people_sync (
  media_type TEXT NOT NULL CHECK(media_type IN ('movie','tv_show')),
  media_id INTEGER NOT NULL,
  tmdb_media_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  cast_count INTEGER NOT NULL DEFAULT 0,
  synced_at TEXT NOT NULL,
  error TEXT,
  PRIMARY KEY(media_type,media_id)
);
"""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def config(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    token = os.environ.get("TMDB_READ_ACCESS_TOKEN") or payload.get("read_access_token", "")
    key = os.environ.get("TMDB_API_KEY") or payload.get("api_key", "")
    if not token and not key:
        raise SystemExit("TMDB credentials are not configured")
    return {"token": token, "key": key, "language": payload.get("language", "en-US")}


class TMDB:
    def __init__(self, settings: dict, delay: float):
        self.settings, self.delay = settings, delay

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        values = dict(params or {})
        if self.settings["key"] and not self.settings["token"]:
            values["api_key"] = self.settings["key"]
        url = "https://api.themoviedb.org/3" + endpoint
        if values:
            url += "?" + urllib.parse.urlencode(values)
        headers = {"Accept": "application/json", "User-Agent": "CineMediaVault-TMDB-People-Cache/1.0"}
        if self.settings["token"]:
            headers["Authorization"] = "Bearer " + self.settings["token"]
        for attempt in range(5):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as response:
                    result = json.loads(response.read().decode("utf-8"))
                time.sleep(self.delay)
                return result
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 4:
                    time.sleep(5 * (attempt + 1)); continue
                raise

    def image(self, profile_path: str, destination: Path) -> bool:
        if not profile_path:
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and destination.stat().st_size > 1000:
            return True
        # w185 is ample for cast cards and magazine portraits while keeping the
        # local cache substantially smaller than the original w342 backfill.
        url = "https://image.tmdb.org/t/p/w185" + profile_path
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "CineMediaVault-TMDB-People-Cache/1.0"}), timeout=45) as source, temporary.open("wb") as target:
                while block := source.read(1024 * 128):
                    target.write(block)
            if temporary.stat().st_size < 1000:
                temporary.unlink(missing_ok=True); return False
            os.replace(temporary, destination); time.sleep(self.delay); return True
        except Exception:
            temporary.unlink(missing_ok=True); return False


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def pending_media(conn: sqlite3.Connection, limit: int):
    sql = """
      SELECT 'movie' media_type,mi.id media_id,m.tmdb_id tmdb_media_id,mi.title
      FROM catalog.movies m JOIN catalog.media_items mi ON mi.id=m.media_item_id
      LEFT JOIN tmdb_people_sync s ON s.media_type='movie' AND s.media_id=mi.id
      WHERE m.tmdb_id IS NOT NULL AND m.tmdb_id>0
        AND (s.media_id IS NULL OR (s.status='error' AND datetime(s.synced_at)<datetime('now','-1 day')))
      UNION ALL
      SELECT 'tv_show',t.id,t.tmdb_id,t.title FROM catalog.tv_shows t
      LEFT JOIN tmdb_people_sync s ON s.media_type='tv_show' AND s.media_id=t.id
      WHERE t.tmdb_id IS NOT NULL AND t.tmdb_id>0
        AND (s.media_id IS NULL OR (s.status='error' AND datetime(s.synced_at)<datetime('now','-1 day')))
      ORDER BY media_type,media_id
    """
    rows = conn.execute(sql).fetchall()
    return rows[:limit] if limit else rows


def cached_person_fresh(conn: sqlite3.Connection, person_id: int, days: int = 180) -> bool:
    row = conn.execute("SELECT updated_at FROM tmdb_people WHERE person_id=?", (person_id,)).fetchone()
    if not row:
        return False
    try:
        stamp = dt.datetime.fromisoformat(row[0])
        return stamp > dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    except Exception:
        return False


def upsert_person(conn, client: TMDB, person: dict, images: Path) -> None:
    person_id = int(person["id"])
    details = person if cached_person_fresh(conn, person_id) else client.get(f"/person/{person_id}", {"language": client.settings["language"]})
    profile = details.get("profile_path") or person.get("profile_path") or ""
    suffix = Path(profile).suffix or ".jpg"
    local = images / f"tmdb-person-{person_id}{suffix}"
    if profile:
        client.image(profile, local)
    conn.execute(
        """INSERT INTO tmdb_people(person_id,name,biography,birthday,deathday,place_of_birth,known_for_department,popularity,profile_path,profile_local_path,source,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(person_id) DO UPDATE SET name=excluded.name,biography=excluded.biography,birthday=excluded.birthday,
             deathday=excluded.deathday,place_of_birth=excluded.place_of_birth,known_for_department=excluded.known_for_department,
             popularity=excluded.popularity,profile_path=excluded.profile_path,profile_local_path=excluded.profile_local_path,updated_at=excluded.updated_at""",
        (person_id, details.get("name") or person.get("name") or "Unknown", details.get("biography") or "", details.get("birthday"), details.get("deathday"),
         details.get("place_of_birth"), details.get("known_for_department"), details.get("popularity"), profile,
         str(local) if local.is_file() else "", "TMDB", now()),
    )


def process(conn, client: TMDB, images: Path, row, cast_limit: int) -> int:
    media_type, media_id, tmdb_id, title = row
    endpoint = f"/movie/{tmdb_id}/credits" if media_type == "movie" else f"/tv/{tmdb_id}/aggregate_credits"
    cast = client.get(endpoint, {"language": client.settings["language"]}).get("cast", [])
    cast.sort(key=lambda item: int(item.get("order", 9999)))
    if cast_limit:
        cast = cast[:cast_limit]
    conn.execute("DELETE FROM tmdb_media_credits WHERE media_type=? AND media_id=?", (media_type, media_id))
    count = 0
    for member in cast:
        if not member.get("id") or not member.get("name"):
            continue
        upsert_person(conn, client, member, images)
        roles = member.get("roles") or []
        character = member.get("character") or (roles[0].get("character") if roles else "") or ""
        credit_id = member.get("credit_id") or (roles[0].get("credit_id") if roles else "") or ""
        conn.execute(
            """INSERT OR REPLACE INTO tmdb_media_credits(media_type,media_id,tmdb_media_id,person_id,character_name,credit_order,credit_id,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (media_type, media_id, tmdb_id, int(member["id"]), character, int(member.get("order", 9999)), credit_id, now()),
        )
        count += 1
    conn.execute("INSERT OR REPLACE INTO tmdb_people_sync(media_type,media_id,tmdb_media_id,status,cast_count,synced_at,error) VALUES(?,?,?,?,?,?,NULL)",
                 (media_type, media_id, tmdb_id, "ok", count, now()))
    conn.commit()
    print(f"SYNC {media_type} {media_id} tmdb={tmdb_id} cast={count} {title}", flush=True)
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--catalog-db", default=str(DEFAULT_CATALOG_DB))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--images", default=str(DEFAULT_IMAGES))
    parser.add_argument("--media-limit", type=int, default=100, help="Media titles per run; 0 means all pending")
    parser.add_argument("--cast-limit", type=int, default=20, help="Top-billed cast per title; 0 means all")
    parser.add_argument("--sleep", type=float, default=.22)
    args = parser.parse_args()
    db, catalog_db, images = Path(args.db).resolve(), Path(args.catalog_db).resolve(), Path(args.images).resolve()
    conn = sqlite3.connect(db, timeout=60)
    conn.execute("PRAGMA foreign_keys=ON"); conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("ATTACH DATABASE ? AS catalog", (f"file:{catalog_db}?mode=ro",))
    initialize(conn)
    client = TMDB(config(Path(args.config).resolve()), max(.05, args.sleep))
    rows = pending_media(conn, args.media_limit)
    completed = people = errors = 0
    print(f"START pending={len(rows)} media_limit={args.media_limit} cast_limit={args.cast_limit}", flush=True)
    for row in rows:
        try:
            people += process(conn, client, images, row, args.cast_limit); completed += 1
        except Exception as exc:
            errors += 1
            conn.execute("INSERT OR REPLACE INTO tmdb_people_sync(media_type,media_id,tmdb_media_id,status,cast_count,synced_at,error) VALUES(?,?,?,?,?,?,?)",
                         (row[0], row[1], row[2], "error", 0, now(), str(exc)[:500]))
            conn.commit(); print(f"ERROR {row[0]} {row[1]} {row[3]}: {exc}", flush=True)
            time.sleep(2)
    total_people = conn.execute("SELECT COUNT(*) FROM tmdb_people").fetchone()[0]
    total_images = conn.execute("SELECT COUNT(*) FROM tmdb_people WHERE profile_local_path<>''").fetchone()[0]
    print(f"DONE media={completed} credit_rows={people} errors={errors} cached_people={total_people} local_images={total_images}", flush=True)
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
