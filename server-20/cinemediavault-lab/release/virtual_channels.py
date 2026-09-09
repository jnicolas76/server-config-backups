#!/usr/bin/env python3
"""CineMediaVault virtual channels: ten clock-driven movie channels and ten
clock-driven TV channels, cable-guide style, built on top of the existing
local movie/TV catalog. Schedules are generated in a bounded background
thread and persisted to SQLite - nothing here streams media or is computed
on the request path except cheap SQL reads and stable-key -> current-item
lookups.

Mirrors dvr_module.py's shape: its own SCHEMA, its own connect()/init_schema(),
a daemon scheduler thread gated by a STARTED flag, and handle_get/handle_post
entry points delegated to from the main dispatcher. Because it needs the live
movie/TV catalogs (which only exist as objects inside the main process), the
catalog objects (movie_app, tv_app) are passed in by the caller rather than
imported independently.
"""
import html
import json
import os
import random
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import genre_catalog

DB_PATH = Path(os.environ.get("CINEVAULT_DB", "/home/jnicolas/cinevault-data/cinevault.db"))
TZ = ZoneInfo("America/Denver")
POLL_SECONDS = max(30, int(os.environ.get("CINEVAULT_VCHANNEL_POLL_SECONDS", "300")))
HORIZON_DAYS = int(os.environ.get("CINEVAULT_VCHANNEL_HORIZON_DAYS", "14"))
PRIME_START_HOUR = 17
PRIME_END_HOUR = 24
LANGUAGE_PROBE_BATCH = 40
HOLDING_GAP_SECONDS = 180
ENGLISH_LANGUAGE_TAGS = {"eng", "en", "und", ""}

LOCK = threading.RLock()
STARTED = False
CONTROL_FILE = DB_PATH.parent / "virtual-channel-rebuild-request.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS vchannel_defs (
 id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('movie','tv')),
 channel_number TEXT NOT NULL, name TEXT NOT NULL, genre_key TEXT NOT NULL,
 sort_order INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vchannel_schedule (
 id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL REFERENCES vchannel_defs(id) ON DELETE CASCADE,
 start_ts INTEGER NOT NULL, stop_ts INTEGER NOT NULL, media_kind TEXT NOT NULL CHECK(media_kind IN ('movie','episode')),
 stable_key TEXT NOT NULL, show_key TEXT, episode_index INTEGER, title TEXT NOT NULL, subtitle TEXT,
 rating REAL, local_date TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(channel_id, start_ts));
CREATE INDEX IF NOT EXISTS idx_vsched_channel_time ON vchannel_schedule(channel_id, start_ts);
CREATE INDEX IF NOT EXISTS idx_vsched_date_key ON vchannel_schedule(local_date, media_kind, stable_key);
CREATE TABLE IF NOT EXISTS vchannel_show_progress (
 channel_id INTEGER NOT NULL, show_key TEXT NOT NULL, next_index INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL, PRIMARY KEY(channel_id, show_key));
CREATE TABLE IF NOT EXISTS vchannel_show_slot (
 id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL REFERENCES vchannel_defs(id) ON DELETE CASCADE,
 show_key TEXT NOT NULL, weekday INTEGER NOT NULL, position INTEGER NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(channel_id, weekday, position));
CREATE TABLE IF NOT EXISTS vchannel_build_state (
 id INTEGER PRIMARY KEY CHECK(id=1), last_build_at TEXT, last_build_status TEXT, last_error TEXT,
 horizon_until_date TEXT, language_probe_coverage INTEGER NOT NULL DEFAULT 0,
 language_probe_total INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
CREATE TABLE IF NOT EXISTS vchannel_media_probe (
 path_key TEXT PRIMARY KEY, seconds REAL NOT NULL DEFAULT 0, audio_language TEXT NOT NULL DEFAULT '',
 probed_at TEXT NOT NULL);
"""

# Ten virtual movie channels, in the required order. "FilmNoir" and
# "International" are not TMDb genre values (TMDb has neither), so those two
# channels use a documented fallback rule (see film_noir_eligible /
# international_eligible below) instead of a direct genre match - both rules
# only combine *real* stored metadata (existing TMDb genres, or the media
# file's own embedded audio-language tag), never invented per-title fields.
MOVIE_CHANNELS = [
    ("m1", "M1", "Action", "Action"),
    ("m2", "M2", "Romance", "Romance"),
    ("m3", "M3", "Comedy", "Comedy"),
    ("m4", "M4", "Science Fiction", "Science Fiction"),
    ("m5", "M5", "Horror", "Horror"),
    ("m6", "M6", "Thriller", "Thriller"),
    ("m7", "M7", "Drama", "Drama"),
    ("m8", "M8", "Film Noir", "FilmNoir"),
    ("m9", "M9", "Kids & Family", "Family"),
    ("m10", "M10", "International", "International"),
]

# Ten virtual TV channels chosen from the actual library's genre distribution
# (tallied 2026-09-08 across 610 genre-tagged shows in tv-metadata-map.json):
# Drama 350, Comedy 216, Sci-Fi & Fantasy 210, Action & Adventure 160,
# Crime 123, Mystery 106, Animation 97, Family+Kids 86, Documentary 40,
# Western 11. These are the ten largest genre buckets after alias merging
# (see genre_catalog.GENRE_ALIASES), so every channel has real local
# programming; Western is the thinnest but still non-empty.
TV_CHANNELS = [
    ("t1", "T1", "Drama", "Drama"),
    ("t2", "T2", "Comedy", "Comedy"),
    ("t3", "T3", "Science Fiction", "Science Fiction"),
    ("t4", "T4", "Action & Adventure", "Action"),
    ("t5", "T5", "Crime", "Crime"),
    ("t6", "T6", "Mystery", "Mystery"),
    ("t7", "T7", "Animation", "Animation"),
    ("t8", "T8", "Family & Kids", "Family"),
    ("t9", "T9", "Documentary", "Documentary"),
    ("t10", "T10", "Western", "Western"),
]


def now_text():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def connect():
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_schema():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = connect()
    try:
        c.executescript(SCHEMA)
        columns = {row[1] for row in c.execute("PRAGMA table_info(vchannel_build_state)")}
        if "schedule_seed" not in columns:
            c.execute("ALTER TABLE vchannel_build_state ADD COLUMN schedule_seed INTEGER NOT NULL DEFAULT 1")
        c.execute("INSERT OR IGNORE INTO vchannel_build_state(id,last_build_status,updated_at) VALUES(1,'pending',?)", (now_text(),))
        c.commit()
    finally:
        c.close()


def ensure_channel_defs():
    c = connect()
    try:
        for order, (slug, number, name, genre_key) in enumerate(MOVIE_CHANNELS):
            c.execute(
                "INSERT OR IGNORE INTO vchannel_defs(slug,kind,channel_number,name,genre_key,sort_order,created_at) VALUES(?,?,?,?,?,?,?)",
                (slug, "movie", number, name, genre_key, order, now_text()),
            )
        for order, (slug, number, name, genre_key) in enumerate(TV_CHANNELS):
            c.execute(
                "INSERT OR IGNORE INTO vchannel_defs(slug,kind,channel_number,name,genre_key,sort_order,created_at) VALUES(?,?,?,?,?,?,?)",
                (slug, "tv", number, name, genre_key, order, now_text()),
            )
        c.commit()
    finally:
        c.close()


def channels(kind=None):
    c = connect()
    try:
        if kind:
            rows = c.execute("SELECT * FROM vchannel_defs WHERE kind=? ORDER BY sort_order", (kind,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM vchannel_defs ORDER BY kind,sort_order").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def channel_by_id(channel_id):
    c = connect()
    try:
        row = c.execute("SELECT * FROM vchannel_defs WHERE id=?", (int(channel_id),)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Media probing (duration + embedded audio language), persisted durably so a
# restart never re-runs ffprobe on already-known files. This is intentionally
# separate from the site's existing in-memory MEDIA_DURATION_CACHE, which
# doesn't survive a restart and doesn't carry a language tag.
# ---------------------------------------------------------------------------

def _ffprobe_raw(path):
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type:stream_tags=language",
        "-of", "json", str(path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
        data = json.loads(result.stdout or "{}")
    except Exception:
        return 0.0, ""
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    language = ""
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "audio":
            language = str((stream.get("tags") or {}).get("language") or "").strip().lower()
            if language:
                break
    return duration, language


def get_cached_probe(path_key):
    c = connect()
    try:
        row = c.execute("SELECT seconds,audio_language FROM vchannel_media_probe WHERE path_key=?", (path_key,)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def store_probe(path_key, seconds, language):
    c = connect()
    try:
        c.execute(
            "INSERT INTO vchannel_media_probe(path_key,seconds,audio_language,probed_at) VALUES(?,?,?,?) "
            "ON CONFLICT(path_key) DO UPDATE SET seconds=excluded.seconds,audio_language=excluded.audio_language,probed_at=excluded.probed_at",
            (path_key, seconds, language, now_text()),
        )
        c.commit()
    finally:
        c.close()


def ensure_probed(path):
    """Return (seconds, audio_language) for a local file, probing once and
    caching durably. Safe to call from the background generator; never
    called from a request handler."""
    key = str(path)
    cached = get_cached_probe(key)
    if cached is not None:
        return cached["seconds"], cached["audio_language"]
    seconds, language = _ffprobe_raw(path)
    store_probe(key, seconds, language)
    return seconds, language


def advance_language_probe_batch(movie_app, limit=LANGUAGE_PROBE_BATCH):
    """Incrementally probe a bounded number of not-yet-probed movies per
    call so the whole 6000+ title library gets audio-language coverage over
    many background ticks instead of one long blocking scan."""
    c = connect()
    try:
        known = {r[0] for r in c.execute("SELECT path_key FROM vchannel_media_probe").fetchall()}
    finally:
        c.close()
    done = 0
    for item in movie_app.movie_index.items:
        if done >= limit:
            break
        key = str(item.path)
        if key in known:
            continue
        ensure_probed(item.path)
        done += 1
    c = connect()
    try:
        total = len(movie_app.movie_index.items)
        covered = c.execute("SELECT COUNT(*) FROM vchannel_media_probe").fetchone()[0]
        c.execute(
            "UPDATE vchannel_build_state SET language_probe_coverage=?,language_probe_total=?,updated_at=? WHERE id=1",
            (covered, total, now_text()),
        )
        c.commit()
    finally:
        c.close()
    return done


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def _canon_genre_set(raw_genres):
    out = set()
    for raw in raw_genres or []:
        canon = genre_catalog.canonical_genre(raw)
        if canon:
            out.add(canon)
    return out


def film_noir_eligible(canon_genres):
    # TMDb has no "Film Noir" genre. Approximate it from genres that are
    # actually stored: crime pictures that are also billed as thriller,
    # mystery, or drama. Documented heuristic, not fabricated metadata.
    return "Crime" in canon_genres and bool(canon_genres & {"Thriller", "Mystery", "Drama"})


def international_eligible(path):
    # No language field is captured in the TMDb metadata cache, so use the
    # media file's own embedded audio-language tag (real technical metadata
    # already read by ffprobe elsewhere in CineVault) rather than inventing
    # one. Files with no confidently-tagged non-English audio are excluded.
    cached = get_cached_probe(str(path))
    if not cached:
        return False
    language = cached["audio_language"]
    return bool(language) and language not in ENGLISH_LANGUAGE_TAGS


def eligible_movie_pool(genre_key, movie_app):
    pool = []
    for item in movie_app.movie_index.items:
        if not item.path.is_file():
            continue
        metadata = movie_app.metadata_for(item)
        raw_genres = metadata.get("genres") or []
        if not raw_genres:
            continue  # titles with missing genres are excluded, never guessed
        canon = _canon_genre_set(raw_genres)
        if genre_key == "FilmNoir":
            eligible = film_noir_eligible(canon)
        elif genre_key == "International":
            eligible = international_eligible(item.path)
        else:
            eligible = genre_key in canon
        if not eligible:
            continue
        try:
            rating = float(metadata.get("vote_average") or 0)
        except (TypeError, ValueError):
            rating = 0.0
        pool.append({
            "stable_key": movie_app.stable_asset_key(item),
            "title": metadata.get("title") or item.title,
            "path": item.path,
            "rating": rating,
        })
    return pool


def show_episode_order(tv_app, show):
    order = []
    for season in show.seasons.values():
        for ep in season.episodes:
            sn, en = tv_app.season_episode_numbers(ep)
            if sn is None or en is None:
                continue
            if not ep.path.is_file():
                continue
            order.append((sn, en, ep))
    order.sort(key=lambda t: (t[0], t[1]))
    return order


def eligible_tv_pool(genre_key, tv_app):
    pool = []
    for show in tv_app.tv_index.shows:
        metadata = tv_app.metadata_for(show)
        raw_genres = metadata.get("genres") or []
        if not raw_genres:
            continue
        canon = _canon_genre_set(raw_genres)
        if genre_key not in canon:
            continue
        order = show_episode_order(tv_app, show)
        if not order:
            continue
        try:
            rating = float(metadata.get("vote_average") or 0)
        except (TypeError, ValueError):
            rating = 0.0
        pool.append({"show_key": show.title, "show": show, "rating": rating, "order": order})
    return pool


# ---------------------------------------------------------------------------
# Weekly TV lineup assignment (computed once per channel, stable afterward)
# ---------------------------------------------------------------------------

def ensure_show_slots(channel, pool):
    c = connect()
    try:
        existing = c.execute("SELECT show_key FROM vchannel_show_slot WHERE channel_id=?", (channel["id"],)).fetchall()
        if existing:
            return
        rng = random.Random(f"vtv-slots:{channel['slug']}")
        ranked = sorted(pool, key=lambda p: -p["rating"])
        slots_per_weekday = 2 if len(ranked) >= 14 else 1
        weekdays = list(range(7))
        rng.shuffle(weekdays)
        assignment = []
        wi = 0
        for show in ranked:
            if wi >= len(weekdays) * slots_per_weekday:
                break
            weekday = weekdays[wi % len(weekdays)]
            position = wi // len(weekdays)
            assignment.append((weekday, position, show["show_key"]))
            wi += 1
        for weekday, position, show_key in assignment:
            c.execute(
                "INSERT OR IGNORE INTO vchannel_show_slot(channel_id,show_key,weekday,position,created_at) VALUES(?,?,?,?,?)",
                (channel["id"], show_key, weekday, position, now_text()),
            )
        c.commit()
    finally:
        c.close()


def show_slots_for_weekday(channel_id, weekday):
    c = connect()
    try:
        rows = c.execute(
            "SELECT show_key,position FROM vchannel_show_slot WHERE channel_id=? AND weekday=? ORDER BY position",
            (channel_id, weekday),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def get_progress(channel_id, show_key):
    c = connect()
    try:
        row = c.execute("SELECT next_index FROM vchannel_show_progress WHERE channel_id=? AND show_key=?", (channel_id, show_key)).fetchone()
        return int(row[0]) if row else 0
    finally:
        c.close()


def set_progress(channel_id, show_key, next_index):
    c = connect()
    try:
        c.execute(
            "INSERT INTO vchannel_show_progress(channel_id,show_key,next_index,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(channel_id,show_key) DO UPDATE SET next_index=excluded.next_index,updated_at=excluded.updated_at",
            (channel_id, show_key, next_index, now_text()),
        )
        c.commit()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Schedule generation
# ---------------------------------------------------------------------------

def _local_midnight(date_obj):
    import datetime
    return datetime.datetime.combine(date_obj, datetime.time(0, 0), tzinfo=TZ)


def _is_prime(ts):
    import datetime
    local = datetime.datetime.fromtimestamp(ts, TZ)
    return PRIME_START_HOUR <= local.hour < PRIME_END_HOUR


def schedule_seed():
    c = connect()
    try:
        row = c.execute("SELECT schedule_seed FROM vchannel_build_state WHERE id=1").fetchone()
        return int(row[0] or 1) if row else 1
    finally:
        c.close()


def _last_stop_ts(channel_id):
    c = connect()
    try:
        row = c.execute("SELECT MAX(stop_ts) FROM vchannel_schedule WHERE channel_id=?", (channel_id,)).fetchone()
        return int(row[0]) if row and row[0] else 0
    finally:
        c.close()


def _used_today(local_date_iso, media_kind="movie"):
    c = connect()
    try:
        rows = c.execute("SELECT DISTINCT stable_key FROM vchannel_schedule WHERE local_date=? AND media_kind=?", (local_date_iso, media_kind)).fetchall()
        return {r[0] for r in rows}
    finally:
        c.close()


def _insert_program(channel_id, start_ts, stop_ts, media_kind, stable_key, show_key, episode_index, title, subtitle, rating, local_date_iso):
    c = connect()
    try:
        c.execute(
            "INSERT OR IGNORE INTO vchannel_schedule(channel_id,start_ts,stop_ts,media_kind,stable_key,show_key,episode_index,title,subtitle,rating,local_date,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (channel_id, start_ts, stop_ts, media_kind, stable_key, show_key, episode_index, title, subtitle, rating, local_date_iso, now_text()),
        )
        c.commit()
    finally:
        c.close()


def generate_movie_day(date_obj, movie_channels, pools):
    import datetime
    local_date_iso = date_obj.isoformat()
    midnight = _local_midnight(date_obj).timestamp()
    next_midnight = _local_midnight(date_obj + datetime.timedelta(days=1)).timestamp()
    used_today = _used_today(local_date_iso, "movie")
    seed = schedule_seed()
    for channel in movie_channels:
        pool = pools.get(channel["id"], [])
        if not pool:
            continue
        pending = []
        start = max(_last_stop_ts(channel["id"]), midnight)
        guard = 0
        while start < next_midnight and guard < 60:
            guard += 1
            rng = random.Random(f"{seed}:{channel['slug']}:{local_date_iso}:{int(start)}")
            candidates = [p for p in pool if p["stable_key"] not in used_today]
            if not candidates:
                # Hard invariant: never repeat a movie on the same calendar day
                # anywhere in the ten-channel lineup. If this channel's whole
                # genre pool is already used today, leave the remainder of the
                # day open (rendered as a gap) rather than violate the rule.
                break
            if _is_prime(start) and len(candidates) > 3:
                ranked = sorted(candidates, key=lambda p: -p["rating"])
                top = ranked[: max(3, len(ranked) // 3)]
                pick = rng.choice(top)
            else:
                pick = rng.choice(candidates)
            seconds, _lang = ensure_probed(pick["path"])
            if seconds <= 0:
                pool = [p for p in pool if p["stable_key"] != pick["stable_key"]]
                pools[channel["id"]] = pool
                if not pool:
                    break
                continue
            stop = start + round(seconds)
            pending.append((channel["id"], int(start), int(stop), "movie", pick["stable_key"], None, None, pick["title"], "", pick["rating"], local_date_iso, now_text()))
            used_today.add(pick["stable_key"])
            start = stop
        if pending:
            c = connect()
            try:
                c.executemany(
                    "INSERT OR IGNORE INTO vchannel_schedule(channel_id,start_ts,stop_ts,media_kind,stable_key,show_key,episode_index,title,subtitle,rating,local_date,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    pending,
                )
                c.commit()
            finally:
                c.close()


def generate_tv_day(date_obj, tv_channels, pools):
    import datetime
    local_date_iso = date_obj.isoformat()
    midnight = _local_midnight(date_obj).timestamp()
    next_midnight = _local_midnight(date_obj + datetime.timedelta(days=1)).timestamp()
    seed = schedule_seed()
    for channel in tv_channels:
        pool = pools.get(channel["id"], {})
        if not pool:
            continue
        c = connect()
        try:
            progress = {row["show_key"]: int(row["next_index"]) for row in c.execute(
                "SELECT show_key,next_index FROM vchannel_show_progress WHERE channel_id=?", (channel["id"],)
            ).fetchall()}
        finally:
            c.close()
        pending = []
        start = max(_last_stop_ts(channel["id"]), midnight)
        previous_show = ""
        guard = 0
        while start < next_midnight and guard < 96:
            guard += 1
            candidates = list(pool.values())
            if len(candidates) > 1:
                without_repeat = [entry for entry in candidates if entry["show_key"] != previous_show]
                if without_repeat:
                    candidates = without_repeat
            rng = random.Random(f"{seed}:{channel['slug']}:{local_date_iso}:{int(start)}")
            if _is_prime(start) and len(candidates) > 3:
                ranked = sorted(candidates, key=lambda entry: -entry["rating"])
                candidates = ranked[:max(3, len(ranked) // 3)]
            show_entry = rng.choice(candidates)
            order = show_entry["order"]
            absolute_index = progress.get(show_entry["show_key"], 0)
            idx = absolute_index % len(order)
            sn, en, ep = order[idx]
            metadata = show_entry["meta"]
            row = show_entry["tv_app"].episode_metadata(metadata, ep)
            try:
                runtime_minutes = float(row.get("runtime") or 0)
            except (TypeError, ValueError):
                runtime_minutes = 0.0
            # The file duration is authoritative. Metadata runtimes are often a
            # generic series value and can outlast a particular episode, which
            # made the player repeatedly reload the just-finished programme.
            seconds, _lang = ensure_probed(ep.path)
            if seconds <= 0:
                seconds = runtime_minutes * 60 if runtime_minutes > 0 else 0
            if seconds <= 0:
                progress[show_entry["show_key"]] = absolute_index + 1
                continue
            stop = start + round(seconds)
            title_label = show_entry["show_key"]
            subtitle = f"S{sn:02d}E{en:02d}"
            try:
                rating = float(row.get("vote_average") or show_entry["rating"] or 0)
            except (TypeError, ValueError):
                rating = show_entry["rating"]
            stable_key = f"{show_entry['show_key']}|S{sn:02d}E{en:02d}"
            pending.append((channel["id"], int(start), int(stop), "episode", stable_key, show_entry["show_key"], absolute_index, title_label, subtitle, rating, local_date_iso, now_text()))
            progress[show_entry["show_key"]] = absolute_index + 1
            previous_show = show_entry["show_key"]
            start = stop
        c = connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.executemany(
                "INSERT OR IGNORE INTO vchannel_schedule(channel_id,start_ts,stop_ts,media_kind,stable_key,show_key,episode_index,title,subtitle,rating,local_date,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                pending,
            )
            c.executemany(
                "INSERT INTO vchannel_show_progress(channel_id,show_key,next_index,updated_at) VALUES(?,?,?,?) ON CONFLICT(channel_id,show_key) DO UPDATE SET next_index=excluded.next_index,updated_at=excluded.updated_at",
                [(channel["id"], show_key, next_index, now_text()) for show_key, next_index in progress.items()],
            )
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def _build_all_pools(movie_app, tv_app, movie_defs, tv_defs):
    """Single pass over the catalogs, bucketed into each channel's pool.
    eligible_movie_pool()/eligible_tv_pool() do the equivalent work for one
    channel at a time (handy for tests/tools), but calling them once per
    channel here would re-run show_episode_order()'s per-episode is_file()
    stat calls for every TV channel independently - a 10x redundant
    filesystem pass across the whole library on every generation tick.
    """
    movie_pools = {ch["id"]: [] for ch in movie_defs}
    for item in movie_app.movie_index.items:
        if not item.path.is_file():
            continue
        metadata = movie_app.metadata_for(item)
        raw_genres = metadata.get("genres") or []
        if not raw_genres:
            continue
        canon = _canon_genre_set(raw_genres)
        try:
            rating = float(metadata.get("vote_average") or 0)
        except (TypeError, ValueError):
            rating = 0.0
        entry = None
        for ch in movie_defs:
            genre_key = ch["genre_key"]
            if genre_key == "FilmNoir":
                eligible = film_noir_eligible(canon)
            elif genre_key == "International":
                eligible = international_eligible(item.path)
            else:
                eligible = genre_key in canon
            if not eligible:
                continue
            if entry is None:
                entry = {"stable_key": movie_app.stable_asset_key(item), "title": metadata.get("title") or item.title, "path": item.path, "rating": rating}
            movie_pools[ch["id"]].append(entry)

    tv_genre_keys = {ch["genre_key"] for ch in tv_defs}
    tv_pools = {ch["id"]: {} for ch in tv_defs}
    for show in tv_app.tv_index.shows:
        metadata = tv_app.metadata_for(show)
        raw_genres = metadata.get("genres") or []
        if not raw_genres:
            continue
        canon = _canon_genre_set(raw_genres)
        matches = canon & tv_genre_keys
        if not matches:
            continue
        order = show_episode_order(tv_app, show)
        if not order:
            continue
        try:
            rating = float(metadata.get("vote_average") or 0)
        except (TypeError, ValueError):
            rating = 0.0
        entry = {"order": order, "rating": rating, "meta": metadata, "tv_app": tv_app, "show_key": show.title}
        for ch in tv_defs:
            if ch["genre_key"] in matches:
                tv_pools[ch["id"]][show.title] = entry
    return movie_pools, tv_pools


def backup_schedule_database(label="virtual-schedule"):
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    source = connect()
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return target


def flush_schedules(randomize=False):
    """Clear both virtual lineups atomically. Progress is cleared because it
    is advanced while the future horizon is generated; retaining it after a
    flush would skip all episodes that had merely been scheduled."""
    init_schema()
    new_seed = random.SystemRandom().randrange(1, 2_147_483_647) if randomize else schedule_seed()
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM vchannel_schedule")
        c.execute("DELETE FROM vchannel_show_progress")
        c.execute("DELETE FROM vchannel_show_slot")
        c.execute("UPDATE vchannel_build_state SET schedule_seed=?,last_build_status='flushed',last_error=NULL,horizon_until_date=NULL,updated_at=? WHERE id=1", (new_seed, now_text()))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
    return new_seed


def rebuild_schedules(movie_app, tv_app, randomize=True):
    with LOCK:
        backup = backup_schedule_database("pre-virtual-rebuild")
        seed = flush_schedules(randomize=randomize)
        generate_horizon(movie_app, tv_app)
        return {"backup": str(backup), "seed": seed, "state": build_state(), **schedule_stats()}


def rebuild_tv_schedules(movie_app, tv_app):
    """Recreate only TV rows, preserving the movie lineup and schedule seed."""
    with LOCK:
        backup = backup_schedule_database("pre-tv-duration-repair")
        init_schema()
        c = connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM vchannel_schedule WHERE channel_id IN (SELECT id FROM vchannel_defs WHERE kind='tv')")
            c.execute("DELETE FROM vchannel_show_progress WHERE channel_id IN (SELECT id FROM vchannel_defs WHERE kind='tv')")
            c.execute("DELETE FROM vchannel_show_slot WHERE channel_id IN (SELECT id FROM vchannel_defs WHERE kind='tv')")
            c.execute("UPDATE vchannel_build_state SET last_build_status='repairing_tv_durations',last_error=NULL,horizon_until_date=NULL,updated_at=? WHERE id=1", (now_text(),))
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
        generate_horizon(movie_app, tv_app)
        return {"backup": str(backup), "state": build_state(), **schedule_stats()}


def schedule_stats():
    now = int(time.time())
    c = connect()
    try:
        result = {}
        for kind in ("movie", "tv"):
            result[kind] = {
                "rows": c.execute("SELECT COUNT(*) FROM vchannel_schedule s JOIN vchannel_defs d ON d.id=s.channel_id WHERE d.kind=?", (kind,)).fetchone()[0],
                "on_air": c.execute("SELECT COUNT(*) FROM vchannel_schedule s JOIN vchannel_defs d ON d.id=s.channel_id WHERE d.kind=? AND s.start_ts<=? AND s.stop_ts>?", (kind, now, now)).fetchone()[0],
            }
        return result
    finally:
        c.close()


def generate_horizon(movie_app, tv_app, horizon_days=HORIZON_DAYS):
    import datetime
    ensure_channel_defs()
    today = datetime.datetime.now(TZ).date()
    movie_defs = channels("movie")
    tv_defs = channels("tv")
    movie_pools, tv_pools = _build_all_pools(movie_app, tv_app, movie_defs, tv_defs)
    for offset in range(horizon_days):
        date_obj = today + datetime.timedelta(days=offset)
        generate_movie_day(date_obj, movie_defs, movie_pools)
        generate_tv_day(date_obj, tv_defs, tv_pools)
    horizon_until = (today + datetime.timedelta(days=horizon_days - 1)).isoformat()
    c = connect()
    try:
        c.execute(
            "UPDATE vchannel_build_state SET last_build_at=?,last_build_status='ok',last_error=NULL,horizon_until_date=?,updated_at=? WHERE id=1",
            (now_text(), horizon_until, now_text()),
        )
        c.commit()
    finally:
        c.close()


def build_state():
    c = connect()
    try:
        row = c.execute("SELECT * FROM vchannel_build_state WHERE id=1").fetchone()
        return dict(row) if row else {}
    finally:
        c.close()


def scheduler_loop(movie_app, tv_app):
    while True:
        try:
            advance_language_probe_batch(movie_app)
            with LOCK:
                generate_horizon(movie_app, tv_app)
        except Exception as exc:
            c = connect()
            try:
                c.execute("UPDATE vchannel_build_state SET last_build_status='error',last_error=?,updated_at=? WHERE id=1", (str(exc)[:2000], now_text()))
                c.commit()
            finally:
                c.close()
            print(f"Virtual channel scheduler error: {exc}", flush=True)
        time.sleep(POLL_SECONDS)


def initialize(movie_app, tv_app):
    global STARTED
    with LOCK:
        if STARTED:
            return
        init_schema()
        ensure_channel_defs()
        STARTED = True
        threading.Thread(target=scheduler_loop, args=(movie_app, tv_app), daemon=True, name="cinevault-vchannel-scheduler").start()


# ---------------------------------------------------------------------------
# Guide payload (read path - cheap SQL + a request-scoped stable-key index)
# ---------------------------------------------------------------------------

def _movie_key_index(movie_app):
    return {movie_app.stable_asset_key(item): item for item in movie_app.movie_index.items}


_EMPTY_PROGRAM_DETAILS = {"detail_href": None, "play_href": None, "overview": "", "poster": ""}


def _resolve_program_details(prog, movie_app, tv_app, movie_index=None):
    """Resolve a schedule row to hrefs plus the rich detail info (overview,
    artwork) the guide's persistent details panel needs. Reuses the same
    is_file()/lookup work the old href-only resolver did; no extra I/O."""
    if prog["media_kind"] == "movie":
        index = movie_index if movie_index is not None else _movie_key_index(movie_app)
        item = index.get(prog["stable_key"])
        if not item or not item.path.is_file():
            return dict(_EMPTY_PROGRAM_DETAILS)
        metadata = movie_app.metadata_for(item)
        return {
            "detail_href": f"/movie/{item.id}",
            "play_href": f"/player/movie/{item.id}",
            "overview": metadata.get("overview") or "",
            "poster": movie_app.poster_url_for(item) or "",
        }
    show_key = prog["show_key"]
    show = next((s for s in tv_app.tv_index.shows if s.title == show_key), None)
    if not show:
        return dict(_EMPTY_PROGRAM_DETAILS)
    order = show_episode_order(tv_app, show)
    idx = prog["episode_index"]
    if idx is None or idx < 0:
        return dict(_EMPTY_PROGRAM_DETAILS)
    _sn, _en, ep = order[idx % len(order)]
    if not ep.path.is_file():
        return dict(_EMPTY_PROGRAM_DETAILS)
    show_metadata = tv_app.metadata_for(show)
    overview = ""
    poster = ""
    if hasattr(tv_app, "episode_summary"):
        overview = tv_app.episode_summary(show_metadata, ep) or ""
    if not overview:
        overview = show_metadata.get("overview") or ""
    if hasattr(tv_app, "episode_still_url"):
        poster = tv_app.episode_still_url(show_metadata, ep) or ""
    if not poster and hasattr(tv_app, "poster_url_for"):
        poster = tv_app.poster_url_for(show) or ""
    return {
        "detail_href": f"/tv/show/{show.id}",
        "play_href": f"/player/tv/{ep.id}",
        "overview": overview,
        "poster": poster,
    }


def guide_payload(kind, query, movie_app, tv_app):
    now = int(time.time())
    start = int((query.get("from") or [now - now % 1800])[0])
    hours = max(2, min(24, int((query.get("hours") or [6])[0])))
    end = start + hours * 3600
    defs = channels(kind)
    c = connect()
    try:
        placeholders = ",".join("?" for _ in defs) or "0"
        ids = [d["id"] for d in defs]
        rows = c.execute(
            f"SELECT * FROM vchannel_schedule WHERE channel_id IN ({placeholders}) AND stop_ts>? AND start_ts<? ORDER BY channel_id,start_ts",
            (*ids, start, end),
        ).fetchall() if ids else []
    finally:
        c.close()
    by_channel = {}
    for row in rows:
        by_channel.setdefault(row["channel_id"], []).append(dict(row))
    movie_index = _movie_key_index(movie_app) if kind == "movie" else None
    def holding_payload(next_prog, hold_start, hold_stop):
        if not next_prog:
            return {"holding": True, "start": hold_start, "stop": hold_stop,
                    "next_title": "Schedule building", "next_start": None,
                    "next_subtitle": "", "next_rating": 0, "overview": "", "poster": "",
                    "next_play_href": "", "next_detail_href": ""}
        details = _resolve_program_details(next_prog, movie_app, tv_app, movie_index)
        return {"holding": True, "start": hold_start, "stop": hold_stop,
                "next_title": next_prog.get("title") or "Coming up", "next_start": next_prog.get("start_ts"),
                "next_subtitle": next_prog.get("subtitle") or "", "next_rating": next_prog.get("rating") or 0,
                "overview": details.get("overview") or "", "poster": details.get("poster") or "",
                "next_play_href": details.get("play_href") or "", "next_detail_href": details.get("detail_href") or ""}
    out_channels = []
    for ch in defs:
        progs = by_channel.get(ch["id"], [])
        out = []
        cursor = start
        for prog in progs:
            if prog["start_ts"] > cursor + HOLDING_GAP_SECONDS:
                out.append(holding_payload(prog, cursor, prog["start_ts"]))
            details = _resolve_program_details(prog, movie_app, tv_app, movie_index)
            out.append({
                "holding": False, "start": prog["start_ts"], "stop": prog["stop_ts"], "title": prog["title"],
                "subtitle": prog.get("subtitle") or "", "rating": prog.get("rating") or 0, "kind": prog["media_kind"],
                "is_now": prog["start_ts"] <= now < prog["stop_ts"], "channel_id": ch["id"],
                **details,
            })
            cursor = prog["stop_ts"]
        if cursor < end:
            nxt = c2 = None
            cc = connect()
            try:
                nxt = cc.execute("SELECT * FROM vchannel_schedule WHERE channel_id=? AND start_ts>=? ORDER BY start_ts LIMIT 1", (ch["id"], cursor)).fetchone()
            finally:
                cc.close()
            if nxt:
                out.append(holding_payload(dict(nxt), cursor, end))
            else:
                out.append(holding_payload(None, cursor, end))
        out_channels.append({"id": ch["id"], "number": ch["channel_number"], "name": ch["name"], "slug": ch["slug"], "programmes": out})
    return {"ok": True, "kind": kind, "start": start, "end": end, "now": now, "channels": out_channels}


def current_program(channel_id):
    now = int(time.time())
    c = connect()
    try:
        row = c.execute(
            "SELECT * FROM vchannel_schedule WHERE channel_id=? AND start_ts<=? AND stop_ts>? ORDER BY start_ts DESC LIMIT 1",
            (channel_id, now, now),
        ).fetchone()
        nxt = c.execute(
            "SELECT * FROM vchannel_schedule WHERE channel_id=? AND start_ts>? ORDER BY start_ts LIMIT 1",
            (channel_id, now),
        ).fetchone()
        return (dict(row) if row else None), (dict(nxt) if nxt else None)
    finally:
        c.close()


def program_after(channel_id, start_ts):
    c = connect()
    try:
        row = c.execute(
            "SELECT * FROM vchannel_schedule WHERE channel_id=? AND start_ts>? ORDER BY start_ts LIMIT 1",
            (channel_id, int(start_ts)),
        ).fetchone()
        nxt = None
        if row:
            nxt = c.execute(
                "SELECT * FROM vchannel_schedule WHERE channel_id=? AND start_ts>? ORDER BY start_ts LIMIT 1",
                (channel_id, int(row["start_ts"])),
            ).fetchone()
        return (dict(row) if row else None), (dict(nxt) if nxt else None)
    finally:
        c.close()


def resolve_current_item(prog, movie_app, tv_app):
    """Resolve a persisted schedule row's durable stable_key back to a
    currently-playable local item. Returns (kind, item_id, path, title) or
    None if the file has since been removed/renamed."""
    if prog["media_kind"] == "movie":
        item = _movie_key_index(movie_app).get(prog["stable_key"])
        if not item or not item.path.is_file():
            return None
        return "movie", item.id, item.path, movie_app.metadata_for(item).get("title") or item.title
    show = next((s for s in tv_app.tv_index.shows if s.title == prog["show_key"]), None)
    if not show:
        return None
    order = show_episode_order(tv_app, show)
    idx = prog["episode_index"]
    if idx is None or idx < 0:
        return None
    _sn, _en, ep = order[idx % len(order)]
    if not ep.path.is_file():
        return None
    return "tv", ep.id, ep.path, f"{show.title} - {prog.get('subtitle') or ''}".strip()


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------

GUIDE_STYLE = """
:root{color-scheme:dark;
--bg-deep:#040a16;--bg-mid:#081733;--bg-glow:#123268;
--panel:rgba(14,28,54,.72);--panel2:rgba(22,44,84,.82);--panel-strong:rgba(10,20,42,.92);
--line:rgba(126,171,230,.22);--line-strong:rgba(150,195,255,.42);
--accent:#3aa0ff;--gold:#3aa0ff;--accent2:#7cc3ff;--muted:#9fb3d1;--red:#ff5c72;--live:#35e08a}
*{box-sizing:border-box}
body{margin:0;color:#eaf2ff;font:15px system-ui,Segoe UI,sans-serif;min-height:100vh;
background:radial-gradient(circle at 18% -12%,var(--bg-glow),transparent 55%),linear-gradient(180deg,var(--bg-mid),var(--bg-deep) 62%);background-attachment:fixed}
header{position:sticky;top:0;z-index:8;backdrop-filter:blur(16px);background:linear-gradient(180deg,rgba(8,18,40,.94),rgba(6,13,30,.88));border-bottom:1px solid var(--line-strong);padding:13px 18px;box-shadow:0 6px 24px rgba(0,10,30,.4)}
.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px}
.brand{font-size:22px;font-weight:900;letter-spacing:.2px}.brand b{background:linear-gradient(90deg,var(--accent),var(--accent2));-webkit-background-clip:text;background-clip:text;color:transparent}
button,a.btn{border:1px solid var(--line-strong);background:var(--panel2);color:#eaf2ff;border-radius:9px;min-height:44px;padding:10px 14px;font-weight:750;text-decoration:none;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:6px;transition:transform .12s,box-shadow .12s}
button:hover,a.btn:hover{border-color:var(--accent2)}
button:focus-visible,a.btn:focus-visible,.program:focus-visible{outline:3px solid var(--gold);outline-offset:2px;z-index:6;position:relative}
.primary{background:linear-gradient(180deg,var(--accent2),var(--accent));color:#04101f;border-color:var(--accent)}
.primary[disabled]{opacity:.45;cursor:not-allowed}
nav{display:flex;gap:6px;overflow:auto;margin-top:12px}
nav button{border-radius:999px}
nav button.active{background:linear-gradient(180deg,var(--accent2),var(--accent));color:#04101f;border-color:var(--accent)}
main{padding:16px;max-width:1700px;margin:0 auto}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.barker{display:grid;grid-template-columns:minmax(300px,48%) 1fr;gap:22px;align-items:center;margin-bottom:16px;padding:14px;border:1px solid var(--line-strong);border-radius:16px;background:var(--panel-strong);box-shadow:0 12px 35px rgba(0,5,18,.45)}.barker-video-wrap{position:relative;aspect-ratio:16/9;background:#000;border-radius:12px;overflow:hidden}.barker video{width:100%;height:100%;object-fit:contain;background:#000}.barker-start{position:absolute;inset:0;margin:auto;width:max-content;height:48px;z-index:2}.barker-copy{min-width:0}.barker-kicker{color:var(--accent2);font-weight:900;letter-spacing:.14em}.barker h2{font-size:clamp(25px,4vw,46px);margin:8px 0}.barker-meta{font-weight:800;color:#fff}.barker-summary{color:#cad7e9;line-height:1.45}.barker-poster{float:left;width:78px;aspect-ratio:2/3;object-fit:cover;margin:0 14px 8px 0;border-radius:7px}.barker-progress{height:4px;background:#273650;border-radius:4px;overflow:hidden;margin-top:14px}.barker-progress i{display:block;height:100%;width:0;background:var(--accent)}
.muted{color:var(--muted)}
.guide-wrap{overflow:auto;max-height:60vh;border:1px solid var(--line-strong);border-radius:14px;position:relative;background:var(--panel);box-shadow:0 10px 40px rgba(0,8,24,.45),inset 0 1px 0 rgba(255,255,255,.04)}
.guide{min-width:2200px}
.time-row,.channel-row{display:grid;grid-template-columns:184px 1fr}
.time-row{position:sticky;top:0;z-index:5;background:var(--panel-strong);border-bottom:1px solid var(--line-strong)}
.channel-name{position:sticky;left:0;z-index:7;background:#0a142a;border-right:1px solid var(--line-strong);border-bottom:1px solid var(--line);padding:6px 10px;display:flex;align-items:center;gap:9px;box-shadow:8px 0 14px rgba(2,7,18,.72)}
.chan-badge{flex:0 0 auto;min-width:34px;text-align:center;padding:4px 7px;border-radius:7px;font-weight:900;font-size:12px;color:#04101f;background:linear-gradient(180deg,var(--accent2),var(--accent));box-shadow:0 0 10px -2px var(--accent)}
.chan-meta{display:flex;flex-direction:column;overflow:hidden}
.chan-meta b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px}
.chan-preview{flex:0 0 auto;position:relative;width:58px;height:33px;border-radius:6px;overflow:hidden;background:#010509;border:1px solid var(--line)}
.chan-preview video{width:100%;height:100%;object-fit:cover;display:block;background:#010509}
.chan-preview .preview-slot{width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:9px;text-align:center;line-height:1.2}
.chan-preview.is-live::after{content:'';position:absolute;top:2px;right:2px;width:5px;height:5px;border-radius:50%;background:var(--live);box-shadow:0 0 4px var(--live)}
@media(max-width:720px){.chan-preview{display:none}}
.timeline{position:relative;height:50px;border-bottom:1px solid var(--line);background:repeating-linear-gradient(90deg,transparent 0,transparent calc(6.25% - 1px),var(--line) calc(6.25% - 1px),var(--line) 6.25%)}
.channel-row:nth-child(odd) .timeline{background-color:rgba(255,255,255,.02)}.channel-row:nth-child(odd) .channel-name{background:#0d1931}
.program{position:absolute;top:4px;height:42px;padding:4px 7px;border:1px solid var(--line-strong);background:linear-gradient(180deg,rgba(30,52,92,.85),rgba(16,30,56,.85));overflow:hidden;text-align:left;border-radius:6px;font-size:12.5px;color:#eaf2ff;line-height:1.25}
.program b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.program .muted{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}
.program.now{background:linear-gradient(180deg,rgba(58,160,255,.35),rgba(20,40,76,.9));border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),0 0 16px -3px var(--accent)}
.program.now::before{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--live)}
.program.holding{background:repeating-linear-gradient(45deg,rgba(255,255,255,.03),rgba(255,255,255,.03) 10px,rgba(255,255,255,.06) 10px,rgba(255,255,255,.06) 20px);color:var(--muted);font-style:italic;border-style:dashed}
.program.selected{background:linear-gradient(180deg,rgba(124,195,255,.4),rgba(58,160,255,.28));border-color:var(--accent2);box-shadow:0 0 0 2px var(--accent2)}
.time-labels{height:34px;position:relative}
.time-labels span{position:absolute;padding:8px 5px;color:var(--muted);font-size:12px}
.now-line{position:absolute;top:0;bottom:0;width:2px;background:var(--gold);z-index:3;box-shadow:0 0 8px var(--gold)}
.now-line::before{content:'NOW';position:absolute;top:-2px;left:-14px;background:var(--gold);color:#04101f;font-size:9px;font-weight:900;padding:1px 4px;border-radius:3px 3px 0 0}
.details-panel{margin-top:14px;border:1px solid var(--line-strong);border-radius:14px;background:var(--panel);padding:16px;display:grid;grid-template-columns:auto 1fr;gap:16px;box-shadow:0 10px 40px rgba(0,8,24,.45),inset 0 1px 0 rgba(255,255,255,.04);min-height:120px}
.details-panel.empty{grid-template-columns:1fr;align-items:center;justify-items:center;color:var(--muted);text-align:center;font-style:italic}
.dp-art{width:120px;height:180px;border-radius:10px;object-fit:cover;background:var(--panel2);border:1px solid var(--line-strong)}
.dp-art.placeholder{display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:12px;text-align:center;padding:8px}
.dp-body h2{margin:0 0 4px;font-size:20px}
.dp-meta{color:var(--muted);margin-bottom:8px;display:flex;flex-wrap:wrap;gap:10px;font-size:13px}
.dp-meta .badge{background:var(--panel2);border:1px solid var(--line-strong);border-radius:999px;padding:2px 10px}
.dp-desc{margin:0 0 12px;color:#dce7fa;max-width:80ch}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
input[type=date]{min-height:44px;padding:9px;background:var(--panel-strong);color:#eaf2ff;border:1px solid var(--line-strong);border-radius:7px}
@media(max-width:720px){.barker{grid-template-columns:1fr}.guide-wrap{max-height:52vh}.details-panel{grid-template-columns:1fr}.dp-art{width:100%;height:160px}}
"""

GUIDE_PAGE = r'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>CineMediaVault Virtual Channels</title><style>__STYLE__</style></head><body>
<header><div class="top"><div class="brand">CineMedia<b>Vault</b> Virtual Channels</div><div><a class="btn" href="/live-tv">Physical Live TV</a><a class="btn" href="/">Home</a></div></div>
<nav><button data-kind="movie" class="__MOVIE_ACTIVE__">Virtual Movies</button><button data-kind="tv" class="__TV_ACTIVE__">Virtual TV</button></nav></header>
<main><section class="barker" id="barker"><div class="barker-video-wrap"><video id="barkerVideo" controls playsinline preload="metadata"></video><button id="barkerStart" class="barker-start primary">Start previews with sound</button></div><div class="barker-copy"><div class="barker-kicker">COMING UP ON CINEMEDIAVAULT</div><img id="barkerPoster" class="barker-poster" alt=""><h2 id="barkerTitle">Building your preview reel…</h2><div id="barkerMeta" class="barker-meta"></div><p id="barkerSummary" class="barker-summary"></p><div class="barker-progress"><i id="barkerProgress"></i></div></div></section>
<div class="toolbar"><button id="prev">&larr; Earlier</button><button id="today" class="primary">Now</button><button id="next">Later &rarr;</button>
<input type="date" id="day" min="__MIN_DATE__" max="__MAX_DATE__" value="__TODAY__"><span id="guideInfo" class="muted"></span></div>
<div class="guide-wrap"><div id="grid" class="guide">Loading&hellip;</div></div>
<div id="detailsPanel" class="details-panel empty"><div>Select a program in the grid to see details here.</div></div>
</main>
<script src="/assets/hls.min.js"></script>
<script>
let kind='__INITIAL_KIND__',start=Math.floor(Date.now()/1800000)*1800,tz='America/Denver',hours=8,selectedKey=null,lastData=null,guideBoundaryTimer=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function dt(t){return new Date(t*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}
function promoDt(t){const d=new Date(t*1000);return `${d.toLocaleDateString([],{weekday:'short',month:'short',day:'numeric'})} · ${d.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}`}
function fmtRemaining(sec){if(sec<=0)return'';const h=Math.floor(sec/3600),m=Math.round((sec%3600)/60);return h>0?`${h}h ${m}m`:`${m}m`}
const barker=(()=>{
  const video=document.getElementById('barkerVideo'),startButton=document.getElementById('barkerStart');let reel=[],index=0,hls=null,timer=null,started=false,currentSlot=null;
  function clearMedia(){clearTimeout(timer);if(hls)try{hls.destroy()}catch(_e){}hls=null;try{video.pause();video.removeAttribute('src');video.load()}catch(_e){}if(currentSlot){fetch('/api/vchannels/promo/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(currentSlot),keepalive:true}).catch(()=>{});currentSlot=null}}
  function show(item){document.getElementById('barkerTitle').textContent=item.title||'Coming up';document.getElementById('barkerMeta').textContent=`${promoDt(item.airtime??item.start)} · ${item.channel_number} ${item.channel}`;document.getElementById('barkerSummary').textContent=item.overview||item.subtitle||'';const p=document.getElementById('barkerPoster');p.src=item.poster||'';p.style.display=item.poster?'block':'none'}
  async function play(forceHls=false){if(!started)return;const now=Math.floor(Date.now()/1000);reel=reel.filter(slot=>slot.start>now);if(!reel.length){await refresh();return}index%=reel.length;clearMedia();const slot=reel[index];currentSlot={channel_id:slot.channelId,start_ts:slot.start};try{const r=await fetch(`/api/vchannels/promo/${slot.channelId}/${slot.start}${forceHls?'?force_hls=1':''}`,{cache:'no-store'}),item=await r.json();if(!item.available)throw 0;show(item);timer=setTimeout(()=>{if(!forceHls)play(true);else next()},12000);const seek=()=>{if(Number.isFinite(video.duration)&&video.duration>0)video.currentTime=Math.min(Number(item.offset)||0,Math.max(0,video.duration-1));video.volume=1;video.muted=false;video.play().catch(()=>{});const began=Date.now();clearTimeout(timer);timer=setTimeout(next,30000);const tick=()=>{if(!started)return;document.getElementById('barkerProgress').style.width=Math.min(100,(Date.now()-began)/300)+'%';if(Date.now()-began<30000)requestAnimationFrame(tick)};tick()};if(item.is_hls&&window.Hls&&Hls.isSupported()){hls=new Hls({maxBufferLength:35});hls.loadSource(item.source);hls.attachMedia(video);hls.on(Hls.Events.MANIFEST_PARSED,seek);hls.on(Hls.Events.ERROR,(_event,data)=>{if(data&&data.fatal){if(!forceHls)play(true);else next()}})}else{video.src=item.source;video.addEventListener('loadedmetadata',seek,{once:true})}video.onerror=()=>{if(!forceHls)play(true);else next()}}catch(_e){next()}}
  function next(){index=(index+1)%Math.max(1,reel.length);play(false)}
  async function load(){clearMedia();started=false;startButton.style.display='block';const now=Math.floor(Date.now()/1000),urls=[`/api/vchannels/guide?kind=${kind}&from=${now}&hours=24`,`/api/vchannels/guide?kind=${kind}&from=${now+86400}&hours=24`];const payloads=await Promise.all(urls.map(u=>fetch(u,{cache:'no-store'}).then(r=>r.json())));const seen=new Set(),all=[];payloads.forEach(d=>(d.channels||[]).forEach(ch=>(ch.programmes||[]).forEach(p=>{if(p.holding||p.start<now||!p.play_href||seen.has(p.title))return;seen.add(p.title);all.push({channelId:ch.id,channel:ch.name,channel_number:ch.number,start:p.start,title:p.title,subtitle:p.subtitle,overview:p.overview,poster:p.poster,rating:Number(p.rating)||0})})));reel=all.sort((a,b)=>b.rating-a.rating||a.start-b.start).slice(0,32);index=0;if(reel.length)show(reel[0]);else document.getElementById('barkerTitle').textContent='No upcoming promos available'}
  async function refresh(){const resume=started;await load();if(resume&&reel.length){started=true;startButton.style.display='none';play(false)}}
  startButton.onclick=()=>{started=true;startButton.style.display='none';play(false)};video.addEventListener('ended',next);return{load,refresh,stop:clearMedia};
})();
function scheduleBarkerRefresh(){const now=new Date(),next=new Date(now);next.setHours(24,0,5,0);setTimeout(()=>{barker.refresh().finally(scheduleBarkerRefresh)},Math.max(1000,next-now))}
scheduleBarkerRefresh();
setInterval(()=>barker.refresh(),15*60*1000);
const previews=(()=>{
  const active=new Map();
  const observer=('IntersectionObserver' in window)?new IntersectionObserver(entries=>{
    entries.forEach(entry=>{if(entry.isIntersecting)startOne(entry.target);else detachOne(entry.target,false)});
  },{root:document.querySelector('.guide-wrap'),rootMargin:'80px'}):null;
  function stopServer(channelIds){
    return fetch('/api/vchannels/preview/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel_ids:channelIds.map(Number)}),keepalive:true}).catch(()=>{});
  }
  function detachOne(el,stop){
    const rec=active.get(el);
    if(rec){
      if(rec.hls)try{rec.hls.destroy()}catch(_e){}
      if(rec.video){try{rec.video.pause();rec.video.removeAttribute('src');rec.video.load()}catch(_e){}}
      active.delete(el);
    }
    el.classList.remove('is-live');
    el.innerHTML='<div class="preview-slot" aria-hidden="true"></div>';
  }
  async function startOne(el,forceHls=false){
    if(active.has(el)||document.visibilityState==='hidden')return;
    const marker={loading:true};active.set(el,marker);
    try{
      const response=await fetch(`/api/vchannels/preview/${el.dataset.channel}${forceHls?'?force_hls=1':''}`,{cache:'no-store'});
      const data=await response.json();
      if(active.get(el)!==marker)return;
      if(!data.available){
        active.delete(el);el.innerHTML=`<div class="preview-slot">${data.reason==='busy'?'Preview busy':'No preview'}</div>`;
        if(data.reason==='busy')setTimeout(()=>startOne(el),12000);
        return;
      }
      const video=document.createElement('video');video.muted=true;video.autoplay=true;video.playsInline=true;video.preload='metadata';video.setAttribute('aria-label',data.title||'Channel preview');
      el.innerHTML='';el.appendChild(video);el.classList.add('is-live');
      const rec={video:video,hls:null,forceHls:forceHls};active.set(el,rec);
      const seek=()=>{if(Number.isFinite(video.duration)&&video.duration>0)video.currentTime=Math.min(Math.max(0,Number(data.offset)||0),Math.max(0,video.duration-.5));video.play().catch(()=>{})};
      if(data.is_hls&&window.Hls&&Hls.isSupported()){
        const h=new Hls({enableWorker:true,maxBufferLength:12,maxMaxBufferLength:20,backBufferLength:0});rec.hls=h;h.loadSource(data.source);h.attachMedia(video);h.on(Hls.Events.MANIFEST_PARSED,seek);
      }else{video.src=data.source;video.addEventListener('loadedmetadata',seek,{once:true})}
      video.addEventListener('error',()=>{if(active.get(el)!==rec)return;detachOne(el,false);if(!forceHls)setTimeout(()=>startOne(el,true),250)},{once:true});
    }catch(_e){if(active.get(el)===marker){active.delete(el);el.innerHTML='<div class="preview-slot">No preview</div>'}}
  }
  function watch(el){if(observer)observer.observe(el);else startOne(el)}
  function detachAll(stop){
    const ids=[];
    document.querySelectorAll('.chan-preview').forEach(el=>{if(observer)observer.unobserve(el);ids.push(el.dataset.channel);detachOne(el,false)});
    return stop&&ids.length?stopServer([...new Set(ids)]):Promise.resolve();
  }
  return {watch:watch,detachAll:detachAll,teardownAll:()=>detachAll(true)};
})();
async function loadGuide(){
  const r=await fetch(`/api/vchannels/guide?kind=${kind}&from=${start}&hours=${hours}`,{cache:'no-store'}),d=await r.json();
  lastData=d;
  const span=d.end-d.start;
  guideInfo.textContent=`${d.channels.length} channels · ${dt(d.start)}–${dt(d.end)}`;
  let times='<div class="time-row"><div class="channel-name"></div><div class="time-labels">';
  for(let t=d.start;t<=d.end;t+=1800) times+=`<span style="left:${(t-d.start)/span*100}%">${dt(t)}</span>`;
  times+='</div></div>';
  let rows=d.channels.map(ch=>{
    let ps=ch.programmes.map(p=>{
      const left=Math.max(0,(p.start-d.start)/span*100),right=Math.min(100,(p.stop-d.start)/span*100),w=Math.max(.7,right-left);
      if(p.holding){const key=`${ch.id}:holding:${p.start}`,sel=key===selectedKey?' selected':'';return `<button type="button" class="program holding${sel}" style="left:${left}%;width:${w}%" data-channel="${ch.id}" data-key="${key}" data-json='${JSON.stringify(p).replace(/'/g,'&#39;')}'><b>Off Air</b><span class="muted">Next: ${esc(p.next_title||'')}${p.next_start?' at '+dt(p.next_start):''}</span></button>`}
      const key=`${ch.id}:${p.start}`,sel=key===selectedKey?' selected':'';
      return `<button type="button" class="program ${p.is_now?'now':''}${sel}" style="left:${left}%;width:${w}%" data-channel="${ch.id}" data-key="${key}" data-json='${JSON.stringify(p).replace(/'/g,'&#39;')}'><b>${esc(p.title)}</b><span class="muted">${esc(p.subtitle||'')}${p.rating?' &#9733;'+Number(p.rating).toFixed(1):''}</span></button>`;
    }).join('');
    const now=(Date.now()/1000-d.start)/span*100;
    return `<div class="channel-row"><div class="channel-name"><span class="chan-badge">${esc(ch.number)}</span><div class="chan-preview" data-channel="${ch.id}"><div class="preview-slot" aria-hidden="true"></div></div><div class="chan-meta"><b>${esc(ch.name)}</b></div></div><div class="timeline">${now>=0&&now<=100?`<i class="now-line" style="left:${now}%"></i>`:''}${ps}</div></div>`;
  }).join('');
  previews.detachAll(false);
  grid.innerHTML=times+rows;
  if(document.visibilityState!=='hidden') grid.querySelectorAll('.chan-preview').forEach(el=>previews.watch(el));
  clearTimeout(guideBoundaryTimer);
  const nowSec=Date.now()/1000,bounds=[];
  d.channels.forEach(ch=>(ch.programmes||[]).forEach(p=>{if(p.start>nowSec)bounds.push(p.start);if(p.stop>nowSec)bounds.push(p.stop)}));
  if(bounds.length){const next=Math.min(...bounds);guideBoundaryTimer=setTimeout(loadGuide,Math.max(500,(next-nowSec)*1000+250))}
}
function renderDetails(p,channelId){
  selectedKey=`${channelId}:${p.start}`;
  const panel=detailsPanel;
  panel.classList.remove('empty');
  const now=Math.floor(Date.now()/1000);
  const isLive=!p.holding&&p.start<=now&&now<p.stop;
  const remaining=isLive?fmtRemaining(p.stop-now):'';
  const status=p.holding?(p.next_start?`Off air · next at ${dt(p.next_start)}`:'Off air'):(isLive?`Live now · ${remaining} remaining`:(p.start>now?`Starts ${dt(p.start)}`:'Recently aired'));
  const art=p.poster?`<img class="dp-art" src="${esc(p.poster)}" alt="">`:`<div class="dp-art placeholder">No artwork</div>`;
  panel.innerHTML=`${art}<div class="dp-body">
    <h2>${esc(p.holding?(p.next_title||'Coming up'):p.title)}</h2>
    <div class="dp-meta"><span class="badge">${esc(status)}</span>${!p.holding?`<span class="badge">${dt(p.start)}–${dt(p.stop)}</span>`:''}${(p.holding?p.next_subtitle:p.subtitle)?`<span class="badge">${esc(p.holding?p.next_subtitle:p.subtitle)}</span>`:''}${(p.holding?p.next_rating:p.rating)?`<span class="badge">&#9733; ${Number(p.holding?p.next_rating:p.rating).toFixed(1)}</span>`:''}</div>
    <p class="dp-desc">${esc(p.overview||'No description available.')}</p>
    <div class="actions">
      ${!p.holding?`<a class="btn primary" href="/watch/vchannel/${channelId}">Watch Live</a>`:''}
      ${(p.holding?p.next_play_href:p.play_href)?`<a class="btn" href="${esc(p.holding?p.next_play_href:p.play_href)}">Play from Beginning</a>`:''}
    </div></div>`;
  document.querySelectorAll('.program.selected').forEach(x=>x.classList.remove('selected'));
  const btn=grid.querySelector(`.program[data-key="${selectedKey}"]`);
  if(btn)btn.classList.add('selected');
}
grid.addEventListener('click',e=>{const b=e.target.closest('button.program');if(!b||!b.dataset.json)return;
  renderDetails(JSON.parse(b.dataset.json),b.dataset.channel)});
document.addEventListener('click',e=>{const a=e.target.closest('a[href]');if(!a||a.target==='_blank')return;e.preventDefault();const href=a.href;let moved=false;const go=()=>{if(moved)return;moved=true;location.href=href};previews.teardownAll().finally(go);setTimeout(go,350)},true);
function clearSelection(){selectedKey=null;detailsPanel.classList.add('empty');detailsPanel.innerHTML='<div>Select a program in the grid to see details here.</div>';
  document.querySelectorAll('.program.selected').forEach(x=>x.classList.remove('selected'))}
document.querySelector('nav').onclick=e=>{const b=e.target.closest('button[data-kind]');if(!b)return;kind=b.dataset.kind;
  document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('active',x===b));
  history.replaceState(null,'','/vchannels/'+(kind==='movie'?'movies':'tv'));clearSelection();barker.load();previews.teardownAll().finally(loadGuide)};
prev.onclick=()=>{start-=hours*1800;loadGuide()};next.onclick=()=>{start+=hours*1800;loadGuide()};
today.onclick=()=>{start=Math.floor(Date.now()/1800000)*1800;day.value=new Date().toLocaleDateString('en-CA',{timeZone:tz});loadGuide()};
day.onchange=()=>{const [y,m,dd]=day.value.split('-').map(Number);start=Math.floor(new Date(y,m-1,dd,0,0,0).getTime()/1000);loadGuide()};
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){clearSelection();return}
  if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].indexOf(e.key)===-1)return;
  const rows=[...document.querySelectorAll('.channel-row')];
  const items=[...document.querySelectorAll('button.program')];
  if(!items.length)return;
  const active=document.activeElement;
  if(!items.includes(active)){items[0].focus();e.preventDefault();return}
  if(e.key==='ArrowLeft'||e.key==='ArrowRight'){
    const idx=items.indexOf(active);
    const next=e.key==='ArrowRight'?idx+1:idx-1;
    if(items[next]){items[next].focus();items[next].scrollIntoView({block:'nearest',inline:'nearest'});e.preventDefault()}
    return}
  // Up/Down: move to the closest-overlapping program in the row above/below.
  const curRow=active.closest('.channel-row'),curRect=active.getBoundingClientRect();
  const rowIdx=rows.indexOf(curRow),targetRow=rows[e.key==='ArrowDown'?rowIdx+1:rowIdx-1];
  if(!targetRow)return;
  const candidates=[...targetRow.querySelectorAll('button.program')];
  if(!candidates.length)return;
  let best=candidates[0],bestDist=Infinity;
  for(const c of candidates){const r=c.getBoundingClientRect();const dist=Math.abs(r.left-curRect.left);if(dist<bestDist){bestDist=dist;best=c}}
  best.focus();best.scrollIntoView({block:'nearest',inline:'nearest'});e.preventDefault();
});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')previews.teardownAll();else document.querySelectorAll('.chan-preview').forEach(el=>previews.watch(el))});
window.addEventListener('pagehide',()=>{barker.stop();previews.teardownAll()});
barker.load();loadGuide();setInterval(loadGuide,60000);
</script></body></html>'''

TUNE_PLAYER_PAGE = r'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title><style>
:root{color-scheme:dark}body{margin:0;background:#000;color:#fff;font:15px system-ui,sans-serif}
header{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:#111}
a.btn,button{border:1px solid #444;background:#23262b;color:#fff;border-radius:8px;min-height:44px;padding:10px 14px;font-weight:700;text-decoration:none;display:inline-flex;align-items:center;cursor:pointer}
a.btn:focus-visible,button:focus-visible,video:focus-visible{outline:3px solid #f5b400}
video{width:100%;max-height:calc(100vh - 66px);background:#000;display:block}
.bar{display:flex;gap:8px;align-items:center;padding:10px 14px;background:#111}
.bar label{display:inline-flex;align-items:center;gap:6px;color:#fff;font-size:13px;font-weight:700}
.bar select{min-height:36px;border-radius:8px;border:1px solid #444;background:#23262b;color:#fff;padding:4px 8px}
body.channel-fullscreen{overflow:hidden}body.channel-fullscreen header,body.channel-fullscreen .bar{display:none}
body.channel-fullscreen video#v{position:fixed;inset:0;width:100vw;height:100vh;height:100dvh;max-height:none;object-fit:contain;background:#000}
.guide-drawer{display:none;position:fixed;inset:0;z-index:30;background:#05070b}.guide-drawer.open{display:block}.guide-drawer iframe{width:100%;height:100%;border:0}.guide-close{position:fixed;right:16px;top:16px;z-index:33}.guide-open video#v{position:fixed;left:16px;top:16px;width:min(38vw,540px);height:auto;aspect-ratio:16/9;z-index:32;border:3px solid #7cc3ff;border-radius:12px;object-fit:contain;cursor:pointer}.guide-open header,.guide-open .bar{display:none}
.up-next{position:fixed;left:0;right:0;bottom:0;z-index:20;background:linear-gradient(0deg,rgba(4,7,14,.97),rgba(4,7,14,.82) 65%,transparent);display:flex;align-items:center;gap:18px;padding:20px 26px;pointer-events:none}
.up-next.hidden{display:none}
.up-next img.next-poster{width:64px;height:96px;object-fit:cover;border-radius:8px;background:#1b2230;flex:0 0 auto}
.up-next img.next-poster.no-art{display:none}
.next-label{color:#7cc3ff;letter-spacing:.14em;font-size:11px;font-weight:900;text-transform:uppercase}
.next-title{font-size:21px;line-height:1.15;margin:2px 0}
.next-subtitle{font-size:13px;color:#c6d1e2}
.next-countdown{margin-top:4px;font-size:13px;font-weight:800;color:#cfe1ff}
.count-number{color:#7cc3ff;font-size:16px}
@media(max-width:850px){.up-next{padding:12px 14px;gap:10px}.up-next img.next-poster{width:44px;height:66px}.next-title{font-size:15px}}
</style></head><body>
<header><div id="pageTitle">__TITLE__</div><a class="btn" href="/vchannels/__KIND_PATH__">Back to Guide</a></header>
<video id="v" controls autoplay playsinline __SOURCE_ATTR__>__CAPTION_TRACK__</video>
<div class="bar">__PLAY_BEGINNING__<button id="openGuide" type="button">Guide</button><span id="audioControlHost">__AUDIO_CONTROL__</span></div>
<div class="guide-drawer" id="guideDrawer"><button class="guide-close" id="closeGuide" type="button">Return to Player</button><iframe id="guideFrame" title="CineVault Guide" data-src="/vchannels/__KIND_PATH__"></iframe></div>
<section id="upNext" class="up-next hidden" aria-live="polite"><img id="nextPoster" class="next-poster" alt=""><div><div class="next-label">Up Next</div><h1 id="nextTitle" class="next-title"></h1><div id="nextSubtitle" class="next-subtitle"></div><div class="next-countdown">Starting in <span id="nextCount" class="count-number">10</span> seconds</div></div></section>
<script src="/assets/hls.min.js"></script>
<script>
const v=document.getElementById('v'),channelId=__CHANNEL_ID__;
const guideDrawer=document.getElementById('guideDrawer'),guideFrame=document.getElementById('guideFrame');
document.getElementById('openGuide').onclick=()=>{if(!guideFrame.src)guideFrame.src=guideFrame.dataset.src;guideDrawer.classList.add('open');document.body.classList.add('guide-open')};
document.getElementById('closeGuide').onclick=()=>{guideDrawer.classList.remove('open');document.body.classList.remove('guide-open')};
v.onclick=()=>{if(document.body.classList.contains('guide-open')){guideDrawer.classList.remove('open');document.body.classList.remove('guide-open')}};
let offset=__OFFSET__,isHls=__IS_HLS__,src="__SOURCE__",next=__NEXT_JSON__,progStart=__ADVANCE_AFTER__,pinFor=__PIN_ADVANCE_AFTER__;
let hls=null,directFallbackStarted=false,everAdvanced=false;
if(new URLSearchParams(location.search).get('fullscreen')==='1')document.body.classList.add('channel-fullscreen');
/* Seamless (same-document) transitions only: navigating away with
   location.href/replace always exits the browser's native Fullscreen API on
   the <video> element, and per spec that state cannot be silently
   re-requested afterward without a fresh user gesture. Swapping src in place
   on the same <video> node - never removing/replacing it - is the only way
   an in-progress native fullscreen session survives an automatic Up Next
   advance or a direct-play error retry. */
function attachSource(newSrc,newIsHls){
  if(hls){try{hls.destroy()}catch(_e){}hls=null}
  if(newIsHls){
    if(v.canPlayType('application/vnd.apple.mpegurl')){v.src=newSrc}
    else if(window.Hls&&Hls.isSupported()){hls=new Hls();hls.loadSource(newSrc);hls.attachMedia(v)}
    else{v.src=newSrc}
  }else{v.src=newSrc}
}
function seekIn(){if(!Number.isFinite(v.duration)||v.duration<=0)return;v.currentTime=Math.min(Math.max(0,offset),Math.max(0,v.duration-1));v.removeEventListener('loadedmetadata',seekIn)}
attachSource(src,isHls);
v.addEventListener('loadedmetadata',seekIn);
function fetchTune(params){
  const url=new URL(`/api/vchannels/tune/${channelId}`,location.origin);
  Object.entries(params||{}).forEach(([k,val])=>{if(val!==null&&val!==undefined)url.searchParams.set(k,val)});
  return fetch(url,{cache:'no-store'}).then(r=>r.json());
}
function wireAudioSelect(){
  const sel=document.getElementById('audioSelect');
  if(!sel)return;
  sel.addEventListener('change',()=>{
    fetchTune({advance_after:pinFor,audio:sel.value}).then(data=>{
      if(!data.ok)return;
      applyProgramData(data,pinFor);
      v.play().catch(()=>{});
    });
  });
}
function applyProgramData(data,usedAdvanceAfter){
  offset=data.offset;isHls=data.is_hls;src=data.source;next=data.next;
  pinFor=usedAdvanceAfter===undefined?null:usedAdvanceAfter;progStart=data.advance_after;
  document.title=data.title;document.getElementById('pageTitle').textContent=data.title;
  const playBeginning=document.getElementById('playBeginningLink');
  if(playBeginning)playBeginning.href=data.play_beginning_href;
  Array.prototype.slice.call(v.querySelectorAll('track')).forEach(t=>t.remove());
  if(data.caption_track_html){const tmp=document.createElement('div');tmp.innerHTML=data.caption_track_html;Array.prototype.slice.call(tmp.children).forEach(el=>v.appendChild(el))}
  document.getElementById('audioControlHost').innerHTML=data.audio_control_html||'';
  wireAudioSelect();
  directFallbackStarted=false;
  attachSource(src,isHls);
  v.addEventListener('loadedmetadata',seekIn,{once:true});
  history.replaceState(null,'',`/watch/vchannel/${channelId}?advance_after=${progStart}&fullscreen=1`);
}
wireAudioSelect();
v.addEventListener('error',()=>{
  if(isHls||directFallbackStarted)return;
  directFallbackStarted=true;
  fetchTune({mode:'hls',advance_after:pinFor}).then(data=>{
    if(!data.ok){location.reload();return}
    applyProgramData(data,pinFor);
  }).catch(()=>location.reload());
});
function nextUrlFallback(){return `/watch/vchannel/${channelId}?advance_after=${progStart}&fullscreen=1`}
function advanceToNext(){
  fetchTune({advance_after:progStart}).then(data=>{
    if(!data.ok){location.href=nextUrlFallback();return}
    applyProgramData(data,progStart);
    document.getElementById('upNext').classList.add('hidden');
    if(!everAdvanced){everAdvanced=true;document.body.classList.add('channel-fullscreen')}
    v.play().catch(()=>{});
    v.addEventListener('ended',showUpNext,{once:true});
  }).catch(()=>{location.href=nextUrlFallback()});
}
function showUpNext(){
  const panel=document.getElementById('upNext');panel.classList.remove('hidden');
  document.getElementById('nextTitle').textContent=next.title||'Programming continues';
  document.getElementById('nextSubtitle').textContent=next.subtitle||'';
  const poster=document.getElementById('nextPoster');poster.src=next.poster||'';poster.classList.toggle('no-art',!next.poster);
  let remaining=10;document.getElementById('nextCount').textContent=remaining;
  const timer=setInterval(()=>{remaining-=1;document.getElementById('nextCount').textContent=Math.max(0,remaining);if(remaining<=0){clearInterval(timer);advanceToNext()}},1000);
}
v.addEventListener('ended',showUpNext,{once:true});
</script></body></html>'''

HOLDING_PAGE = r'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title><style>
body{margin:0;background:#090a0d;color:#fff;font:16px system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;text-align:center}
a.btn{border:1px solid #444;background:#23262b;color:#fff;border-radius:8px;padding:12px 18px;text-decoration:none;font-weight:700;display:inline-block;margin-top:18px}
a.btn:focus-visible{outline:3px solid #f5b400}
.card{max-width:520px;padding:24px}
.gold{color:#f5b400}
</style></head><body><div class="card">
<h1>__HEADLINE__</h1><p>__DETAIL__</p>
<a class="btn" href="/vchannels/__KIND_PATH__">Back to Guide</a>
<script>setTimeout(()=>location.reload(),__REFRESH_MS__)</script>
</div></body></html>'''


def _guide_page(kind):
    import datetime
    today = datetime.datetime.now(TZ).date()
    max_date = today + datetime.timedelta(days=HORIZON_DAYS - 1)
    return (
        GUIDE_PAGE.replace("__STYLE__", GUIDE_STYLE)
        .replace("__MOVIE_ACTIVE__", "active" if kind == "movie" else "")
        .replace("__TV_ACTIVE__", "active" if kind == "tv" else "")
        .replace("__INITIAL_KIND__", kind)
        .replace("__MIN_DATE__", today.isoformat())
        .replace("__MAX_DATE__", max_date.isoformat())
        .replace("__TODAY__", today.isoformat())
    )


def _holding_response(handler, kind, headline, detail, refresh_ms=15000):
    body = (
        HOLDING_PAGE.replace("__TITLE__", html.escape(headline))
        .replace("__HEADLINE__", html.escape(headline))
        .replace("__DETAIL__", html.escape(detail))
        .replace("__KIND_PATH__", "movies" if kind == "movie" else "tv")
        .replace("__REFRESH_MS__", str(refresh_ms))
    )
    return handler.render_html(body)


def _resolve_tune(channel_id, movie_app, tv_app, resolve_source_fn, caption_fn, advance_after=None, playback_mode="direct", audio_index=None, audio_markup_fn=None):
    """Single source of truth for 'what should be playing on this virtual
    channel right now', shared by watch_channel() (full HTML page, used for
    the very first tune-in) and the JSON /api/vchannels/tune/<id> endpoint
    (used for in-place Up Next transitions and direct-play-error retries).
    Never duplicated between the two - see handle_get()."""
    channel = channel_by_id(channel_id)
    if not channel:
        return {"ok": False, "reason": "not_found"}
    forced_advance = False
    if advance_after:
        try:
            prog, nxt = program_after(channel["id"], int(advance_after))
            forced_advance = True
        except (TypeError, ValueError):
            prog, nxt = current_program(channel["id"])
    else:
        prog, nxt = current_program(channel["id"])
    if not prog:
        detail = f"Programming resumes with {nxt['title']} shortly." if nxt else "The schedule is still being built for this channel."
        return {"ok": False, "reason": "holding", "channel": channel, "headline": f"{channel['name']} — Off Air", "detail": detail}
    resolved = resolve_current_item(prog, movie_app, tv_app)
    if not resolved:
        return {
            "ok": False, "reason": "unavailable", "channel": channel,
            "headline": f"{prog['title']} is unavailable",
            "detail": "This title's local file could not be found. It will be skipped automatically on its next scheduled airing.",
        }
    kind, item_id, path, title = resolved
    now = time.time()
    offset = 0 if forced_advance else max(0, now - prog["start_ts"])
    scheduled_len = max(1, prog["stop_ts"] - prog["start_ts"])
    if offset >= scheduled_len - 1:
        return {"ok": False, "reason": "advance", "channel": channel}
    audio_control, resolved_audio_index = audio_markup_fn(path, audio_index) if audio_markup_fn else ("", None)
    # Only pass a 5th argument when a specific audio track was actually
    # requested, so resolve_source_fn callbacks written against the older
    # 4-argument contract (playback_mode only) keep working unchanged.
    if audio_index is not None:
        resolved_source = resolve_source_fn(kind, item_id, path, playback_mode, resolved_audio_index)
    else:
        resolved_source = resolve_source_fn(kind, item_id, path, playback_mode)
    caption_html = caption_fn(path, kind, item_id) if caption_fn else ""
    next_info = {"title": "Programming continues", "subtitle": "", "overview": "", "poster": "", "start": None}
    if nxt:
        next_details = _resolve_program_details(nxt, movie_app, tv_app)
        next_info = {
            "title": nxt.get("title") or "Programming continues",
            "subtitle": nxt.get("subtitle") or "",
            "overview": next_details.get("overview") or "",
            "poster": next_details.get("poster") or "",
            "start": nxt.get("start_ts"),
        }
    return {
        "ok": True,
        "channel": channel,
        "title": f"{channel['channel_number']} {channel['name']} — {title}",
        "offset": int(offset),
        "is_hls": bool(resolved_source.get("is_hls")),
        "source": resolved_source.get("source", ""),
        "caption_track_html": caption_html,
        "audio_control_html": audio_control,
        "play_beginning_href": f"/player/{kind}/{item_id}",
        "advance_after": int(prog["start_ts"]),
        "next": next_info,
    }


def watch_channel(handler, user, channel_id, movie_app, tv_app, resolve_source_fn, caption_fn=None, audio_markup_fn=None):
    query = {}
    try:
        import urllib.parse as _up
        query = _up.parse_qs(_up.urlsplit(handler.path).query)
    except Exception:
        pass
    advance_after = (query.get("advance_after", [""])[0] or "").strip()
    playback_mode = (query.get("mode", ["direct"])[0] or "direct").lower()
    audio_index = None
    try:
        audio_raw = (query.get("audio", [""])[0] or "").strip()
        audio_index = int(audio_raw) if audio_raw else None
    except (TypeError, ValueError):
        audio_index = None
    result = _resolve_tune(channel_id, movie_app, tv_app, resolve_source_fn, caption_fn, advance_after, playback_mode, audio_index, audio_markup_fn)
    channel = result.get("channel")
    if not channel:
        return handler.send_error(404, "Channel not found")
    if not result["ok"]:
        if result["reason"] == "advance":
            return handler.redirect(f"/watch/vchannel/{channel['id']}")
        return _holding_response(handler, channel["kind"], result["headline"], result["detail"], refresh_ms=8000 if result["reason"] == "unavailable" else 15000)
    next_json = json.dumps(result["next"], ensure_ascii=False).replace("</", "<\\/")
    next_url = f"/watch/vchannel/{channel['id']}?advance_after={result['advance_after']}&fullscreen=1"
    body = (
        TUNE_PLAYER_PAGE.replace("__TITLE__", html.escape(result["title"]))
        .replace("__KIND_PATH__", "movies" if channel["kind"] == "movie" else "tv")
        .replace("__SOURCE_ATTR__", "")
        .replace("__CAPTION_TRACK__", result["caption_track_html"])
        .replace("__AUDIO_CONTROL__", result["audio_control_html"])
        .replace("__PLAY_BEGINNING__", f"<a class='btn' id='playBeginningLink' href='{html.escape(result['play_beginning_href'])}'>Play from Beginning</a>")
        .replace("__OFFSET__", str(result["offset"]))
        .replace("__IS_HLS__", "true" if result["is_hls"] else "false")
        .replace("__SOURCE__", result["source"])
        .replace("__CHANNEL_ID__", str(channel["id"]))
        .replace("__ADVANCE_AFTER__", str(result["advance_after"]))
        .replace("__PIN_ADVANCE_AFTER__", advance_after if advance_after else "null")
        .replace("__NEXT_URL__", next_url)
        .replace("__NEXT_JSON__", next_json)
    )
    return handler.render_html(body)


def tune_payload(channel_id, movie_app, tv_app, resolve_source_fn, caption_fn, advance_after=None, playback_mode="direct", audio_index=None, audio_markup_fn=None):
    """JSON variant of _resolve_tune() for in-place (same-document) program
    transitions: the browser tune page fetches this instead of navigating,
    so a real Fullscreen-API session on the <video> element is never lost -
    native fullscreen exits on document unload/navigation and (per spec)
    cannot be silently re-requested afterward without a new user gesture, so
    the only reliable fix is to never navigate for an automatic transition."""
    result = _resolve_tune(channel_id, movie_app, tv_app, resolve_source_fn, caption_fn, advance_after, playback_mode, audio_index, audio_markup_fn)
    channel = result.pop("channel", None)
    if not channel:
        return {"ok": False, "reason": "not_found"}
    if not result["ok"]:
        return result
    result["channel_id"] = channel["id"]
    return result


def preview_payload(channel_id, movie_app, tv_app, preview_source_fn, force_hls=False):
    """Cheap read-path lookup for a guide mini-preview tile: reuses the exact
    same schedule-row-lookup + stable-key resolution + offset math as
    watch_channel() (never duplicated), then asks the injected
    preview_source_fn for the cheapest available source. Never raises for a
    holding/unavailable/busy channel - it just reports why, so the guide can
    degrade to a static tile instead of erroring."""
    channel = channel_by_id(channel_id)
    if not channel:
        return {"available": False, "reason": "not_found"}
    prog, _nxt = current_program(channel["id"])
    if not prog:
        return {"available": False, "reason": "holding"}
    resolved = resolve_current_item(prog, movie_app, tv_app)
    if not resolved:
        return {"available": False, "reason": "unavailable"}
    kind, item_id, path, title = resolved
    now = time.time()
    offset = max(0, now - prog["start_ts"])
    scheduled_len = max(1, prog["stop_ts"] - prog["start_ts"])
    if offset >= scheduled_len - 1:
        return {"available": False, "reason": "advancing"}
    if not preview_source_fn:
        return {"available": False, "reason": "unsupported"}
    source = preview_source_fn(kind, item_id, path, int(offset), bool(force_hls))
    if not source:
        return {"available": False, "reason": "busy"}
    return {
        "available": True,
        "title": title,
        "offset": int(source.get("client_offset", offset)),
        "is_hls": bool(source.get("is_hls")),
        "source": source.get("source", ""),
        "shared": bool(source.get("shared", True)),
    }


def promo_payload(channel_id, start_ts, movie_app, tv_app, preview_source_fn, force_hls=False):
    """Resolve one future schedule row into a single bounded barker clip."""
    if int(start_ts) <= int(time.time()):
        return {"available": False, "reason": "already_started"}
    channel = channel_by_id(channel_id)
    if not channel or not preview_source_fn:
        return {"available": False, "reason": "not_found"}
    c = connect()
    try:
        row = c.execute("SELECT * FROM vchannel_schedule WHERE channel_id=? AND start_ts=?", (int(channel_id), int(start_ts))).fetchone()
    finally:
        c.close()
    if not row:
        return {"available": False, "reason": "not_found"}
    prog = dict(row)
    resolved = resolve_current_item(prog, movie_app, tv_app)
    if not resolved:
        return {"available": False, "reason": "unavailable"}
    kind, item_id, path, title = resolved
    duration = max(1, int(prog["stop_ts"] - prog["start_ts"]))
    # Stable daily selection: avoid credits/opening where possible and keep
    # the same promo reel on every device until the schedule/day changes.
    room = max(1, duration - 75)
    stable_number = sum((idx + 1) * ord(ch) for idx, ch in enumerate(f"{channel_id}:{start_ts}"))
    clip_offset = min(max(15, 15 + (stable_number % room)), max(0, duration - 35))
    source = preview_source_fn(kind, item_id, path, int(clip_offset), bool(force_hls), True)
    if not source:
        return {"available": False, "reason": "busy"}
    details = _resolve_program_details(prog, movie_app, tv_app)
    return {"available": True, "title": title, "subtitle": prog.get("subtitle") or "", "overview": details.get("overview") or "", "poster": details.get("poster") or "", "channel": channel["name"], "channel_number": channel["channel_number"], "airtime": int(prog["start_ts"]), "offset": int(source.get("client_offset", clip_offset)), "is_hls": bool(source.get("is_hls")), "source": source.get("source", "")}


def admin_page(handler, message=""):
    stats = schedule_stats()
    state = build_state()
    note = f"<div class='note'>{html.escape(message)}</div>" if message else ""
    body = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Virtual Channel Administration</title><style>
:root{{color-scheme:dark;--gold:#f5b73f}}*{{box-sizing:border-box}}body{{margin:0;background:#08090c;color:#fff;font:16px system-ui,sans-serif}}header,main{{padding:20px;max-width:1050px;margin:auto}}header{{display:flex;justify-content:space-between;border-bottom:1px solid #30343d}}a{{color:#fff}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}}.card{{background:#121720;border:1px solid #303a49;border-radius:16px;padding:18px}}button{{min-height:46px;padding:0 18px;border:0;border-radius:999px;background:var(--gold);color:#111;font-weight:900;cursor:pointer}}button.danger{{background:#732b37;color:#fff}}form{{margin-top:14px}}.note{{padding:12px;background:#15351f;border-radius:10px;margin-bottom:16px}}.muted{{color:#abb4c3}}code{{overflow-wrap:anywhere}}
</style></head><body><header><strong>Virtual Channel Administration</strong><nav><a href="/admin/modules">Modules</a> &middot; <a href="/vchannels">Guide</a> &middot; <a href="/">Home</a></nav></header><main>{note}<h1>24×7 Virtual Schedules</h1><p class="muted">The rolling {HORIZON_DAYS}-day horizon is extended automatically, providing continuous service year-round.</p><div class="cards">
<section class="card"><h2>Movie Channels</h2><p>{stats['movie']['rows']:,} scheduled programs</p><p>{stats['movie']['on_air']} of 10 channels currently on air</p></section>
<section class="card"><h2>TV Channels</h2><p>{stats['tv']['rows']:,} scheduled episodes</p><p>{stats['tv']['on_air']} of 10 channels currently on air</p></section>
<section class="card"><h2>Build Status</h2><p>{html.escape(str(state.get('last_build_status') or 'unknown'))}</p><p class="muted">Through {html.escape(str(state.get('horizon_until_date') or 'not built'))}</p><p class="muted">Randomization seed: {int(state.get('schedule_seed') or 1)}</p></section>
</div><section class="card"><h2>Schedule Operations</h2><p>Flush removes both Movie and TV schedules. Rebuild creates a new randomized rolling lineup and resets TV episode progression to Season 1/Episode 1 for its newly assigned sequence.</p>
<form method="post" action="/admin/vchannels" onsubmit="return confirm('Flush BOTH Movie and TV virtual schedules? The guide will remain empty until rebuilt.')"><button class="danger" name="action" value="flush">Flush Both Schedules</button></form>
<form method="post" action="/admin/vchannels" onsubmit="return confirm('Back up, flush, re-randomize, and rebuild BOTH virtual schedules now?')"><button name="action" value="rebuild">Rebuild &amp; Re-randomize</button></form></section></main></body></html>'''
    return handler.render_html(body)


def handle_get(handler, user, path, movie_app, tv_app, resolve_source_fn=None, caption_fn=None, preview_source_fn=None, audio_markup_fn=None):
    if path == "/admin/vchannels":
        if not user["is_admin"]:
            return handler.send_error(403)
        return admin_page(handler)
    if path in ("/vchannels", "/vchannels/movies", "/vchannels/tv"):
        kind = "tv" if path.endswith("/tv") else "movie"
        return handler.render_html(_guide_page(kind))
    if path == "/api/vchannels/guide":
        import urllib.parse as _up
        query = _up.parse_qs(_up.urlsplit(handler.path).query)
        kind = (query.get("kind", ["movie"])[0] or "movie").lower()
        if kind not in ("movie", "tv"):
            kind = "movie"
        return handler.json_response(guide_payload(kind, query, movie_app, tv_app))
    if path == "/api/vchannels/status":
        return handler.json_response({"ok": True, "state": build_state(), "channels": channels()})
    if path.startswith("/api/vchannels/promo/"):
        import urllib.parse as _up
        parts = path.strip("/").split("/")
        try:
            channel_id, start_ts = int(parts[-2]), int(parts[-1])
        except (ValueError, IndexError):
            return handler.json_response({"available": False, "reason": "not_found"})
        query = _up.parse_qs(_up.urlsplit(handler.path).query)
        force_hls = query.get("force_hls", ["0"])[0] == "1"
        return handler.json_response(promo_payload(channel_id, start_ts, movie_app, tv_app, preview_source_fn, force_hls))
    if path.startswith("/api/vchannels/preview/"):
        channel_id = path.rsplit("/", 1)[-1]
        try:
            channel_id = int(channel_id)
        except ValueError:
            return handler.json_response({"available": False, "reason": "not_found"})
        force_hls = False
        try:
            import urllib.parse as _up
            force_hls = (_up.parse_qs(_up.urlsplit(handler.path).query).get("force_hls", ["0"])[0] == "1")
        except Exception:
            pass
        return handler.json_response(preview_payload(channel_id, movie_app, tv_app, preview_source_fn, force_hls))
    if path.startswith("/api/vchannels/tune/"):
        channel_id = path.rsplit("/", 1)[-1]
        try:
            channel_id = int(channel_id)
        except ValueError:
            return handler.json_response({"ok": False, "reason": "not_found"})
        import urllib.parse as _up
        query = _up.parse_qs(_up.urlsplit(handler.path).query)
        advance_after = (query.get("advance_after", [""])[0] or "").strip()
        playback_mode = (query.get("mode", ["direct"])[0] or "direct").lower()
        audio_index = None
        try:
            audio_raw = (query.get("audio", [""])[0] or "").strip()
            audio_index = int(audio_raw) if audio_raw else None
        except (TypeError, ValueError):
            audio_index = None
        return handler.json_response(
            tune_payload(channel_id, movie_app, tv_app, resolve_source_fn, caption_fn, advance_after, playback_mode, audio_index, audio_markup_fn)
        )
    if path.startswith("/watch/vchannel/"):
        channel_id = path.rsplit("/", 1)[-1]
        try:
            channel_id = int(channel_id)
        except ValueError:
            return handler.send_error(404, "Channel not found")
        return watch_channel(handler, user, channel_id, movie_app, tv_app, resolve_source_fn, caption_fn, audio_markup_fn)
    return False


def handle_post(handler, user, path, movie_app, tv_app, stop_preview_fn=None):
    if path == "/admin/vchannels":
        if not user["is_admin"]:
            return handler.send_error(403)
        form = handler.read_form()
        action = form.get("action") or ""
        try:
            if action == "flush":
                backup = backup_schedule_database("pre-virtual-flush")
                with LOCK:
                    flush_schedules(randomize=False)
                return admin_page(handler, f"Both schedules flushed. Backup: {backup}")
            if action == "rebuild":
                result = rebuild_schedules(movie_app, tv_app, randomize=True)
                return admin_page(handler, f"Schedules rebuilt and re-randomized. Backup: {result['backup']}")
            if action == "repair_tv":
                result = rebuild_tv_schedules(movie_app, tv_app)
                return admin_page(handler, f"TV schedules rebuilt using actual media durations. Backup: {result['backup']}")
            if action == "repair_all":
                result = rebuild_schedules(movie_app, tv_app, randomize=False)
                return admin_page(handler, f"Movie and TV schedules rebuilt using actual media durations. Backup: {result['backup']}")
        except Exception as exc:
            return admin_page(handler, f"Schedule operation failed: {exc}")
        return admin_page(handler, "No schedule operation selected.")
    if path == "/api/admin/vchannels/rebuild":
        if not user["is_admin"]:
            return handler.send_error(403)
        try:
            generate_horizon(movie_app, tv_app)
            return handler.json_response({"ok": True, "state": build_state()})
        except Exception as exc:
            return handler.json_response({"ok": False, "error": str(exc)})
    if path == "/api/vchannels/promo/stop":
        body = handler.read_json() if hasattr(handler, "read_json") else {}
        try:
            channel_id, start_ts = int(body.get("channel_id")), int(body.get("start_ts"))
        except (TypeError, ValueError):
            return handler.json_response({"ok": True, "stopped": 0})
        c = connect()
        try:
            row = c.execute("SELECT * FROM vchannel_schedule WHERE channel_id=? AND start_ts=?", (channel_id, start_ts)).fetchone()
        finally:
            c.close()
        stopped = 0
        if row and stop_preview_fn:
            resolved = resolve_current_item(dict(row), movie_app, tv_app)
            if resolved:
                kind_, item_id, path_, _title = resolved
                stop_preview_fn(kind_, item_id, path_)
                stopped = 1
        return handler.json_response({"ok": True, "stopped": stopped})
    if path == "/api/vchannels/preview/stop":
        body = handler.read_json() if hasattr(handler, "read_json") else {}
        raw_ids = body.get("channel_ids")
        if not isinstance(raw_ids, list):
            raw_ids = [body.get("channel_id")]
        channel_ids = []
        for raw in raw_ids[:20]:
            try:
                channel_ids.append(int(raw))
            except (TypeError, ValueError):
                pass
        stopped = 0
        if stop_preview_fn:
            for channel_id in set(channel_ids):
                channel = channel_by_id(channel_id)
                if not channel:
                    continue
                prog, _nxt = current_program(channel_id)
                if not prog:
                    continue
                resolved = resolve_current_item(prog, movie_app, tv_app)
                if resolved:
                    kind, item_id, path_, _title = resolved
                    stop_preview_fn(kind, item_id, path_)
                    stopped += 1
        return handler.json_response({"ok": True, "stopped": stopped})
    return False
