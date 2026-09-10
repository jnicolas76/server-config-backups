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
import hashlib
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
BARKER_TTS_DIR = Path(os.environ.get("CINEVAULT_BARKER_TTS_DIR", str(DB_PATH.parent / "barker-tts")))
BARKER_TTS_PYTHON = Path(os.environ.get("CINEVAULT_BARKER_TTS_PYTHON", "/home/jnicolas/cinemediavault-lab/tts-venv/bin/python"))
BARKER_TTS_SCRIPT = Path(os.environ.get("CINEVAULT_BARKER_TTS_SCRIPT", "/home/jnicolas/cinemediavault-lab/barker_tts_generate.py"))
BARKER_TTS_MODEL = Path(os.environ.get("CINEVAULT_BARKER_TTS_MODEL", "/home/jnicolas/cinemediavault-lab/tts-models/kokoro-v1.0.onnx"))
BARKER_TTS_VOICES = Path(os.environ.get("CINEVAULT_BARKER_TTS_VOICES", "/home/jnicolas/cinemediavault-lab/tts-models/voices-v1.0.bin"))
BARKER_TTS_LOCK = threading.Lock()
COMBINED_BARKER_DIR = Path(os.environ.get("CINEVAULT_COMBINED_BARKER_DIR", str(DB_PATH.parent / "combined-barker")))
COMBINED_BARKER_MANIFEST = COMBINED_BARKER_DIR / "manifest.json"

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
    # Five additional channels, added 2026-09-09. Each uses a sentinel
    # genre_key (never a real TMDb genre string) dispatched by
    # tv_special_eligible() above instead of plain genre-set membership -
    # exactly the same pattern M8 Film Noir / M10 International already use.
    ("t11", "T11", "Knowledge", "Knowledge"),
    ("t12", "T12", "The Simpsons", "TheSimpsons"),
    ("t13", "T13", "The Zone", "TheZone"),
    ("t14", "T14", "Nostalgia", "Nostalgia"),
    ("t15", "T15", "Sitcom", "Sitcom"),
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


# ---------------------------------------------------------------------------
# Five additional TV channels (T11-T15), added 2026-09-09. Same documented-
# heuristic pattern as FilmNoir/International above: a sentinel genre_key
# ("Knowledge"/"TheSimpsons"/"TheZone"/"Nostalgia"/"Sitcom" - none are real
# TMDb genre strings) triggers one of the eligibility functions below instead
# of a plain genre-set membership check. Every rule is either a real stored
# TMDb genre, a real stored release year, or an explicit named show a human
# asked for by title - never a guessed/invented tag.
# ---------------------------------------------------------------------------

def _normalize_show_title(title):
    """Casefold, drop a leading 'The ', collapse punctuation/whitespace - so
    'The Simpsons' / 'Simpsons' / 'the simpsons!' all normalize identically,
    while still requiring a full-title match (never a bare substring, which
    would wrongly pull in e.g. 'Super Friends' when matching 'Friends')."""
    t = (title or "").strip().casefold()
    t = re.sub(r"^the\s+", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return t


def _title_matches_any(title, names):
    norm = _normalize_show_title(title)
    return any(_normalize_show_title(name) == norm for name in names)


def _show_year(metadata):
    try:
        return int(str(metadata.get("year") or "").strip()[:4])
    except (TypeError, ValueError):
        return 0


# Curated by exact (normalized) title. Checked live against the library's own
# tv-metadata-map.json on 2026-09-09 - titles marked "not in library" are kept
# anyway (harmless no-ops today) so the channel picks them up automatically if
# they're ever added, without needing another code change.
KNOWLEDGE_PRIORITY_TITLES = {"How It's Made", "Modern Marvels"}
SIMPSONS_TITLES = {"The Simpsons"}
ZONE_PRIORITY_TITLES = {
    "The Twilight Zone", "The Twilight Zone 1959", "Twilight Zone 2019",
    "The Outer Limits", "The Outer Limit 1995",
    "Amazing Stories", "Black Mirror", "Tales from the Crypt", "Tales from the Darkside",
    "Creepshow", "Are You Afraid of the Dark", "Goosebumps", "Unsolved Mysteries", "The X-Files",
}
NOSTALGIA_PRIORITY_TITLES = {
    "The Donna Reed Show", "I Love Lucy", "Wonder Woman", "The Incredible Hulk",
    "The Greatest American Hero", "Knight Rider", "The A-Team",
}
SITCOM_PRIORITY_TITLES = {
    "Seinfeld", "Friends", "The Office", "The Donna Reed Show", "I Love Lucy", "Malcolm in the Middle",
}
# Named-priority shows are guaranteed a top-of-lineup weekly ranking (see
# generate_tv_day()'s prime-time top-third-by-rating selection) regardless of
# their real TMDb score, which may be low, missing, or simply beaten out by
# higher-rated genre-fallback titles otherwise.
PRIORITY_RATING_FLOOR = 9.0
PRIORITY_TITLES_BY_GENRE_KEY = {
    "Knowledge": KNOWLEDGE_PRIORITY_TITLES,
    "TheZone": ZONE_PRIORITY_TITLES,
    "Nostalgia": NOSTALGIA_PRIORITY_TITLES,
    "Sitcom": SITCOM_PRIORITY_TITLES,
}
SPECIAL_TV_GENRE_KEYS = {"Knowledge", "TheSimpsons", "TheZone", "Nostalgia", "Sitcom"}


def knowledge_eligible(canon, title):
    if _title_matches_any(title, KNOWLEDGE_PRIORITY_TITLES):
        return True
    return "Documentary" in canon


def simpsons_eligible(title):
    return _title_matches_any(title, SIMPSONS_TITLES)


def zone_eligible(canon, title):
    # Explicit aliases cover the library's year-suffixed folders and its
    # historical singular "The Outer Limit 1995" folder spelling. The
    # fallback rule - Science Fiction *and* Mystery both actually tagged -
    # is a real, already-stored TMDb-genre combination that lands on the same
    # eerie speculative-anthology neighborhood (The X-Files, Fringe, Black
    # Mirror, Creepshow: 42 shows on the live library) without pulling in
    # every Science Fiction or every Mystery show wholesale.
    if _title_matches_any(title, ZONE_PRIORITY_TITLES):
        return True
    return bool({"Science Fiction", "Mystery"} <= canon)


def nostalgia_eligible(canon, title, metadata):
    if _title_matches_any(title, NOSTALGIA_PRIORITY_TITLES):
        return True
    year = _show_year(metadata)
    return bool(year) and 1960 <= year <= 1999


def sitcom_eligible(canon, title):
    if _title_matches_any(title, SITCOM_PRIORITY_TITLES):
        return True
    # Comedy minus Animation: keeps this a live-action sitcom lineup instead
    # of a duplicate of the Comedy movie channel's cartoon-inclusive pool.
    return "Comedy" in canon and "Animation" not in canon


def tv_special_eligible(genre_key, canon, title, metadata):
    """Single dispatch point for all five new sentinel genre_keys, shared by
    eligible_tv_pool() (single-channel helper) and _build_all_pools() (the
    real per-tick batch build) so the two can never drift apart."""
    if genre_key == "Knowledge":
        return knowledge_eligible(canon, title)
    if genre_key == "TheSimpsons":
        return simpsons_eligible(title)
    if genre_key == "TheZone":
        return zone_eligible(canon, title)
    if genre_key == "Nostalgia":
        return nostalgia_eligible(canon, title, metadata)
    if genre_key == "Sitcom":
        return sitcom_eligible(canon, title)
    return False


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
        canon = _canon_genre_set(raw_genres)
        title = show.title
        if genre_key in SPECIAL_TV_GENRE_KEYS:
            eligible = tv_special_eligible(genre_key, canon, title, metadata)
        else:
            # Titles with missing genres are excluded, never guessed - unless
            # a special channel above already matched by explicit name, which
            # is a stronger, human-curated signal than a genre guess.
            eligible = bool(raw_genres) and genre_key in canon
        if not eligible:
            continue
        order = show_episode_order(tv_app, show)
        if not order:
            continue
        try:
            rating = float(metadata.get("vote_average") or 0)
        except (TypeError, ValueError):
            rating = 0.0
        priority_titles = PRIORITY_TITLES_BY_GENRE_KEY.get(genre_key)
        if priority_titles and _title_matches_any(title, priority_titles):
            rating = max(rating, PRIORITY_RATING_FLOOR)
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

    tv_pools = {ch["id"]: {} for ch in tv_defs}
    for show in tv_app.tv_index.shows:
        metadata = tv_app.metadata_for(show)
        raw_genres = metadata.get("genres") or []
        canon = _canon_genre_set(raw_genres)
        title = show.title
        try:
            rating = float(metadata.get("vote_average") or 0)
        except (TypeError, ValueError):
            rating = 0.0
        order = None  # computed at most once per show, only if something matches
        for ch in tv_defs:
            genre_key = ch["genre_key"]
            if genre_key in SPECIAL_TV_GENRE_KEYS:
                eligible = tv_special_eligible(genre_key, canon, title, metadata)
            else:
                eligible = bool(raw_genres) and genre_key in canon
            if not eligible:
                continue
            if order is None:
                order = show_episode_order(tv_app, show)
                if not order:
                    break  # no locally-resolvable episodes at all - can't air on any channel
            entry_rating = rating
            priority_titles = PRIORITY_TITLES_BY_GENRE_KEY.get(genre_key)
            if priority_titles and _title_matches_any(title, priority_titles):
                entry_rating = max(entry_rating, PRIORITY_RATING_FLOOR)
            tv_pools[ch["id"]][title] = {"order": order, "rating": entry_rating, "meta": metadata, "tv_app": tv_app, "show_key": title}
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


def rebuild_tv_schedules(movie_app, tv_app, randomize=False):
    """Recreate only TV rows, preserving the movie lineup (and, by default,
    the schedule seed - the admin "repair_tv" button uses this default path
    to fix durations without reshuffling anything). randomize=True additionally
    rolls a fresh schedule_seed before regenerating, for a genuine TV-wide
    re-randomize (e.g. after adding new channels). This never touches any
    already-persisted movie row: both generate_movie_day()/generate_tv_day()
    only ever extend forward from each channel's own MAX(stop_ts), so a
    changed seed can only influence *not-yet-generated* future days, and only
    TV rows are deleted here in the first place - movie rows for
    already-covered days are untouched byte-for-byte."""
    with LOCK:
        backup = backup_schedule_database("pre-tv-duration-repair")
        init_schema()
        new_seed = random.SystemRandom().randrange(1, 2_147_483_647) if randomize else schedule_seed()
        c = connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM vchannel_schedule WHERE channel_id IN (SELECT id FROM vchannel_defs WHERE kind='tv')")
            c.execute("DELETE FROM vchannel_show_progress WHERE channel_id IN (SELECT id FROM vchannel_defs WHERE kind='tv')")
            c.execute("DELETE FROM vchannel_show_slot WHERE channel_id IN (SELECT id FROM vchannel_defs WHERE kind='tv')")
            c.execute(
                "UPDATE vchannel_build_state SET schedule_seed=?,last_build_status='repairing_tv_durations',last_error=NULL,horizon_until_date=NULL,updated_at=? WHERE id=1",
                (new_seed, now_text()),
            )
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
        generate_horizon(movie_app, tv_app)
        return {"backup": str(backup), "seed": new_seed, "state": build_state(), **schedule_stats()}


def schedule_stats():
    now = int(time.time())
    c = connect()
    try:
        result = {}
        for kind in ("movie", "tv"):
            result[kind] = {
                "rows": c.execute("SELECT COUNT(*) FROM vchannel_schedule s JOIN vchannel_defs d ON d.id=s.channel_id WHERE d.kind=?", (kind,)).fetchone()[0],
                "on_air": c.execute("SELECT COUNT(*) FROM vchannel_schedule s JOIN vchannel_defs d ON d.id=s.channel_id WHERE d.kind=? AND s.start_ts<=? AND s.stop_ts>?", (kind, now, now)).fetchone()[0],
                "total": len(MOVIE_CHANNELS) if kind == "movie" else len(TV_CHANNELS),
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
            with LOCK:
                write_combined_barker_manifest(movie_app, tv_app)
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
        # "art": a small channel-identity image, reused from whatever
        # already-resolved programme poster is first available in this
        # window - no extra catalog lookups. For single-show channels (The
        # Simpsons) every row is the same series, so this is effectively a
        # locally-derived channel logo with zero new code/fetching; for
        # mixed-genre channels it is just today's featured artwork. Never a
        # scraped/external image - always the same local TMDb poster art the
        # rest of the site already serves.
        art = next((p.get("poster") for p in out if not p.get("holding") and p.get("poster")), "")
        out_channels.append({"id": ch["id"], "number": ch["channel_number"], "name": ch["name"], "slug": ch["slug"], "art": art, "programmes": out})
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


def _spoken_episode(text):
    """Expand compact episode notation for natural TTS pronunciation."""
    value = str(text or "")
    value = re.sub(r"\bS0*(\d+)\s*E0*(\d+)\b", lambda m: f"season {int(m.group(1))}, episode {int(m.group(2))}", value, flags=re.I)
    value = re.sub(r"\bS0*(\d+)\b", lambda m: f"season {int(m.group(1))}", value, flags=re.I)
    return value


def write_combined_barker_manifest(movie_app, tv_app, limit=120):
    """Publish a bounded, atomic input manifest for the offline FFmpeg worker.

    The worker never needs application credentials or direct catalog access.
    Paths remain server-local and the manifest contains only already-resolved
    schedule/catalog metadata.
    """
    now = int(time.time())
    until = now + 48 * 3600
    c = connect()
    try:
        rows = c.execute(
            "SELECT s.*,d.name AS channel_name,d.channel_number,d.kind AS channel_kind "
            "FROM vchannel_schedule s JOIN vchannel_defs d ON d.id=s.channel_id "
            "WHERE s.start_ts>? AND s.start_ts<? ORDER BY s.start_ts,s.channel_id",
            (now, until),
        ).fetchall()
    finally:
        c.close()
    movie_index = _movie_key_index(movie_app)
    tv_show_index = {show.title: show for show in tv_app.tv_index.shows}
    tv_order_cache = {}
    items = []
    seen = set()
    for raw in rows:
        prog = dict(raw)
        unique = (prog["media_kind"], prog["stable_key"])
        if unique in seen:
            continue
        metadata = {}
        if prog["media_kind"] == "movie":
            movie = movie_index.get(prog["stable_key"])
            if not movie or not movie.path.is_file():
                continue
            media_kind, media_path, title = "movie", movie.path, prog.get("title") or movie.title
            metadata = movie_app.metadata_for(movie) or {}
            details = {"overview": metadata.get("overview") or "", "poster": movie_app.poster_url_for(movie) or ""}
        else:
            show = tv_show_index.get(prog.get("show_key"))
            if not show:
                continue
            order = tv_order_cache.setdefault(show.title, show_episode_order(tv_app, show))
            episode_index = prog.get("episode_index")
            if not order or episode_index is None or episode_index < 0:
                continue
            _season, _episode, ep = order[episode_index % len(order)]
            if not ep.path.is_file():
                continue
            media_kind, media_path, title = "tv", ep.path, prog.get("title") or show.title
            metadata = tv_app.metadata_for(show) or {}
            overview = tv_app.episode_summary(metadata, ep) if hasattr(tv_app, "episode_summary") else ""
            poster = tv_app.episode_still_url(metadata, ep) if hasattr(tv_app, "episode_still_url") else ""
            if not poster and hasattr(tv_app, "poster_url_for"):
                poster = tv_app.poster_url_for(show) or ""
            details = {"overview": overview or metadata.get("overview") or "", "poster": poster or ""}
        cast_rows = (metadata.get("credits") or {}).get("cast") or metadata.get("cast") or []
        cast = []
        for actor in cast_rows[:2]:
            name = actor.get("name") if isinstance(actor, dict) else str(actor)
            if name:
                cast.append(str(name))
        genres = metadata.get("genres") or []
        genres = [g.get("name", "") if isinstance(g, dict) else str(g) for g in genres]
        items.append({
            "kind": media_kind, "path": str(media_path), "title": str(prog.get("title") or title),
            "subtitle": _spoken_episode(prog.get("subtitle") or ""),
            "summary": str(details.get("overview") or "").strip(),
            "poster": str(details.get("poster") or ""),
            "airtime": int(prog["start_ts"]), "channel": prog["channel_name"],
            "channel_number": prog["channel_number"], "rating": float(prog.get("rating") or 0),
            "year": str(metadata.get("release_date") or metadata.get("year") or "")[:4],
            "genres": [g for g in genres if g], "cast": cast,
        })
        seen.add(unique)
        if len(items) >= limit:
            break
    COMBINED_BARKER_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": now, "timezone": str(TZ), "items": items}
    temporary = COMBINED_BARKER_MANIFEST.with_suffix(".json.part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(COMBINED_BARKER_MANIFEST)
    return payload


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
header{position:sticky;top:0;z-index:40;backdrop-filter:blur(16px);background:linear-gradient(180deg,rgba(8,18,40,.94),rgba(6,13,30,.88));border-bottom:1px solid var(--line-strong);padding:13px 18px;box-shadow:0 6px 24px rgba(0,10,30,.4)}
.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px}
.brand{font-size:22px;font-weight:900;letter-spacing:.2px}.brand b{background:linear-gradient(90deg,var(--accent),var(--accent2));-webkit-background-clip:text;background-clip:text;color:transparent}
button,a.btn{border:1px solid var(--line-strong);background:var(--panel2);color:#eaf2ff;border-radius:9px;min-height:44px;padding:10px 14px;font-weight:750;text-decoration:none;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:6px;transition:transform .12s,box-shadow .12s}
button:hover,a.btn:hover{border-color:var(--accent2)}
button:focus-visible,a.btn:focus-visible,.program:focus-visible{outline:3px solid var(--gold);outline-offset:2px;z-index:6;position:relative}
.primary{background:linear-gradient(180deg,var(--accent2),var(--accent));color:#04101f;border-color:var(--accent)}
.primary[disabled]{opacity:.45;cursor:not-allowed}
nav{display:flex;gap:6px;overflow:auto;margin-top:12px}
nav button,nav a{border-radius:999px}
nav button.active,nav a.active{background:linear-gradient(180deg,var(--accent2),var(--accent));color:#04101f;border-color:var(--accent)}
main{padding:16px;max-width:1700px;margin:0 auto}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.barker{position:sticky;top:105px;z-index:30;isolation:isolate;display:grid;grid-template-columns:minmax(220px,40%) 1fr;gap:22px;align-items:center;margin-bottom:16px;padding:14px;border:1px solid rgba(150,195,255,.55);border-radius:16px;background:rgba(8,24,55,.99);box-shadow:0 12px 35px rgba(0,5,18,.65)}.barker-video-wrap{position:relative;aspect-ratio:16/9;background:#000;border-radius:12px;overflow:hidden;border:1px solid rgba(124,195,255,.35)}.barker video{width:100%;height:100%;object-fit:contain;background:#000;display:block;cursor:pointer}.barker.external-active .barker-video-wrap{visibility:hidden}.barker-copy{min-width:0}.barker-kicker{color:#7cc3ff;font-weight:900;letter-spacing:.14em}.barker h2{font-size:clamp(22px,3vw,38px);margin:8px 0}.barker-meta{font-weight:800;color:#fff}.barker-summary{color:#dce9fb;line-height:1.45}.barker-poster{float:left;width:78px;aspect-ratio:2/3;object-fit:cover;margin:0 14px 8px 0;border-radius:7px}
video::-webkit-media-controls-wireless-playback-picker-button{display:none!important}
.muted{color:var(--muted)}
.guide-wrap{overflow:auto;max-height:60vh;border:1px solid var(--line-strong);border-radius:14px;position:relative;background:var(--panel);box-shadow:0 10px 40px rgba(0,8,24,.45),inset 0 1px 0 rgba(255,255,255,.04)}
.guide{min-width:2200px}
.time-row,.channel-row{display:grid;grid-template-columns:258px 1fr}
.time-row{position:sticky;top:0;z-index:5;background:var(--panel-strong);border-bottom:1px solid var(--line-strong)}
.channel-name{position:sticky;left:0;z-index:7;background:#0a142a;border-right:1px solid var(--line-strong);border-bottom:1px solid var(--line);padding:6px 10px;display:flex;align-items:center;gap:9px;box-shadow:8px 0 14px rgba(2,7,18,.72)}
.chan-badge{flex:0 0 auto;min-width:34px;text-align:center;padding:4px 7px;border-radius:7px;font-weight:900;font-size:12px;color:#04101f;background:linear-gradient(180deg,var(--accent2),var(--accent));box-shadow:0 0 10px -2px var(--accent)}
.chan-badge.simpsons{background:linear-gradient(180deg,#ffd23f,#f5b400);box-shadow:0 0 10px -2px #f5b400}
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
@media(max-width:720px){.barker{position:relative;top:auto;display:grid;grid-template-columns:1fr;gap:12px;padding:12px}.barker-video-wrap{width:100%;max-height:none}.barker-copy{padding:0 4px 3px}.barker h2{font-size:27px;line-height:1.08}.barker-meta{font-size:15px}.barker-summary{font-size:15px;line-height:1.5;max-height:none;overflow:visible}.barker-poster{width:72px;margin-right:13px}.guide-wrap{max-height:58vh}.details-panel{grid-template-columns:1fr}.dp-art{width:100%;height:160px}}
"""

GUIDE_PAGE = r'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>CineMediaVault Virtual Channels</title><style>__STYLE__</style></head><body>
<header><div class="top"><div class="brand">CineMedia<b>Vault</b> Virtual Channels</div><div><a class="btn" href="/live-tv">Physical Live TV</a><a class="btn" href="/">Home</a></div></div>
<nav><button data-kind="movie" class="__MOVIE_ACTIVE__">Virtual Movies</button><button data-kind="tv" class="__TV_ACTIVE__">Virtual TV</button><button id="miniPreviewToggle" type="button" aria-pressed="true">Channel Previews: On</button></nav></header>
<main><section class="barker" id="barker"><div class="barker-video-wrap"><video id="barkerVideo" autoplay playsinline preload="metadata" disableRemotePlayback x-webkit-airplay="deny" aria-label="Upcoming programme preview; tap if audio is blocked"></video></div><div class="barker-copy"><div id="barkerKicker" class="barker-kicker">COMING UP ON CINEMEDIAVAULT</div><img id="barkerPoster" class="barker-poster" alt=""><h2 id="barkerTitle">Building your preview reel…</h2><div id="barkerMeta" class="barker-meta"></div><p id="barkerSummary" class="barker-summary"></p></div></section>
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
  const video=document.getElementById('barkerVideo');let state=null,infoTimer=null,soundEnabled=false,loadedSlot=null;
  function clearMedia(){clearInterval(infoTimer);infoTimer=null;loadedSlot=null;try{video.pause();video.removeAttribute('src');video.load()}catch(_e){}}
  function show(item){document.getElementById('barkerTitle').textContent=item?.title||'Coming up';document.getElementById('barkerMeta').textContent=item?`${promoDt(item.airtime)} · ${item.channel_number||''} ${item.channel||''}${item.subtitle?' · '+item.subtitle:''}`:'';document.getElementById('barkerSummary').textContent=item?.summary||'';const p=document.getElementById('barkerPoster');p.src=item?.poster||'';p.style.display=item?.poster?'block':'none'}
  function sync(){const list=state?.programmes||[];if(!list.length)return;const duration=Math.max(1,Number(state.duration)||3600),position=((Date.now()/1000-Number(state.slot_start))%duration+duration)%duration,index=Math.floor(position/60)%list.length;show(list[index]);if(Math.abs((Number(video.currentTime)||0)-position)>4&&Number.isFinite(video.duration))video.currentTime=Math.min(position,Math.max(0,video.duration-.25))}
  function startAudible(){video.muted=false;return video.play().then(()=>{soundEnabled=true;localStorage.setItem('cinevault.barkerSound','on')}).catch(()=>{video.muted=true;return video.play().catch(()=>{})})}
  async function load(){if(document.getElementById('barker').classList.contains('external-active'))return;try{const response=await fetch('/api/vchannels/barker/status',{cache:'no-store'}),next=await response.json();if(!next.available){show({title:'The new preview reel is being prepared',summary:'The guide remains available while CineMediaVault finishes the next video.'});return}state=next;const duration=Math.max(1,Number(state.duration)||3600),position=((Date.now()/1000-Number(state.slot_start))%duration+duration)%duration;if(loadedSlot!==state.slot_start){loadedSlot=state.slot_start;video.src='/api/vchannels/barker/video?v='+state.slot_start;video.addEventListener('loadedmetadata',()=>{video.currentTime=Math.min(position,Math.max(0,video.duration-.25));video.muted=false;startAudible()},{once:true})}else if(video.paused){startAudible()}sync();clearInterval(infoTimer);infoTimer=setInterval(sync,1000)}catch(_e){show({title:'Preview temporarily unavailable',summary:'CineMediaVault will retry automatically.'})}}
  function suspend(){clearInterval(infoTimer);infoTimer=null;video.pause()}
  function resume(){document.getElementById('barker').classList.remove('external-active');document.getElementById('barkerKicker').textContent='COMING UP ON CINEMEDIAVAULT';load()}
  function external(info){suspend();document.getElementById('barker').classList.add('external-active');document.getElementById('barkerKicker').textContent='NOW PLAYING';show(info||{})}
  video.addEventListener('click',()=>{soundEnabled=true;video.muted=false;localStorage.setItem('cinevault.barkerSound','on');video.play().catch(()=>{})});video.addEventListener('ended',load);return{load,refresh:load,stop:clearMedia,suspend,resume,external};
})();
window.cinevaultSetActiveProgram=info=>barker.external(info);
window.cinevaultClearActiveProgram=()=>barker.resume();
window.cinevaultBarkerRect=()=>{const el=document.querySelector('.barker-video-wrap');if(!el)return null;const r=el.getBoundingClientRect();return{left:r.left,top:r.top,width:r.width,height:r.height}};
function scheduleBarkerRefresh(){const now=new Date(),next=new Date(now);next.setHours(24,0,5,0);setTimeout(()=>{barker.refresh().finally(scheduleBarkerRefresh)},Math.max(1000,next-now))}
scheduleBarkerRefresh();
setInterval(()=>barker.refresh(),15*60*1000);
const previews=(()=>{
  const active=new Map();
  const storageKey='cinevault.virtualChannelPreviews.enabled';
  let enabled=localStorage.getItem(storageKey)!=='false';
  const toggle=document.getElementById('miniPreviewToggle');
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
  function renderToggle(){
    toggle.textContent=`Channel Previews: ${enabled?'On':'Off'}`;
    toggle.classList.toggle('active',enabled);
    toggle.setAttribute('aria-pressed',String(enabled));
  }
  async function startOne(el,forceHls=false){
    if(!enabled||active.has(el)||document.visibilityState==='hidden')return;
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
  function watch(el){if(!enabled)return;if(observer)observer.observe(el);else startOne(el)}
  function detachAll(stop){
    const ids=[];
    document.querySelectorAll('.chan-preview').forEach(el=>{if(observer)observer.unobserve(el);ids.push(el.dataset.channel);detachOne(el,false)});
    return stop&&ids.length?stopServer([...new Set(ids)]):Promise.resolve();
  }
  async function setEnabled(next){
    enabled=Boolean(next);localStorage.setItem(storageKey,String(enabled));renderToggle();
    if(!enabled){await detachAll(true);return}
    if(document.visibilityState!=='hidden')document.querySelectorAll('.chan-preview').forEach(el=>watch(el));
  }
  toggle.onclick=()=>setEnabled(!enabled);
  renderToggle();
  return {watch:watch,detachAll:detachAll,teardownAll:()=>detachAll(true),isEnabled:()=>enabled,setEnabled:setEnabled};
})();
async function loadGuide(){
  const r=await fetch(`/api/vchannels/guide?kind=${kind}&from=${start}&hours=${hours}`,{cache:'no-store'}),d=await r.json();
  lastData=d;
  const span=d.end-d.start;
  guideInfo.textContent=`${d.channels.length} channels · ${dt(d.start)}–${dt(d.end)}`;
  let times='<div class="time-row"><div class="channel-name"><b>Channels</b></div><div class="time-labels">';
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
    return `<div class="channel-row"><div class="channel-name"><span class="chan-badge${ch.slug==='t12'?' simpsons':''}">${esc(ch.number)}</span><div class="chan-preview" data-channel="${ch.id}"><div class="preview-slot" aria-hidden="true"></div></div><div class="chan-meta"><b>${esc(ch.name)}</b></div></div><div class="timeline">${now>=0&&now<=100?`<i class="now-line" style="left:${now}%"></i>`:''}${ps}</div></div>`;
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
document.addEventListener('click',e=>{const a=e.target.closest('a[href]');if(!a||a.target==='_blank')return;e.preventDefault();const href=a.href;let moved=false;const go=()=>{if(moved)return;moved=true;if(window.self!==window.top){window.parent.postMessage({type:'cinevault-guide-navigate',href:href},location.origin)}else{location.href=href}};previews.teardownAll().finally(go);setTimeout(go,350)},true);
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
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')previews.teardownAll();else if(previews.isEnabled())document.querySelectorAll('.chan-preview').forEach(el=>previews.watch(el))});
window.addEventListener('pagehide',()=>{barker.stop();previews.teardownAll()});
barker.load();loadGuide();setInterval(loadGuide,60000);
</script></body></html>'''

COMBINED_BARKER_PAGE = r'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>CineMediaVault Promo Channel</title><style>
:root{color-scheme:dark;--blue:#3aa0ff;--line:rgba(150,195,255,.42)}*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:radial-gradient(circle at 15% 0,#123268,#040a16 62%);color:#fff;font:16px system-ui,sans-serif}.shell{height:100%;display:grid;grid-template-columns:minmax(300px,40%) 1fr;gap:20px;align-items:center;padding:clamp(14px,3vw,44px)}.stage{aspect-ratio:16/9;background:#000;border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:0 18px 55px #000}.stage video{width:100%;height:100%;object-fit:contain;background:#000}.info{min-width:0;background:rgba(10,20,42,.94);border:1px solid var(--line);border-radius:18px;padding:clamp(16px,2.5vw,34px);box-shadow:0 18px 55px rgba(0,0,0,.5)}.kicker{color:#7cc3ff;font-size:12px;font-weight:900;letter-spacing:.16em}.poster{float:left;width:min(130px,23%);aspect-ratio:2/3;object-fit:cover;border-radius:10px;margin:8px 20px 10px 0;background:#18243b}.info h1{font-size:clamp(25px,4vw,52px);line-height:1.04;margin:9px 0}.subtitle{font-size:clamp(15px,2vw,23px);font-weight:800;color:#d9e9ff}.meta{margin:9px 0;color:#9fc9ff;font-weight:800}.summary{font-size:clamp(14px,1.6vw,21px);line-height:1.45;color:#dce9fb}.top{position:fixed;left:14px;right:14px;top:12px;z-index:2;display:flex;justify-content:space-between;align-items:center;pointer-events:none;opacity:0;transition:opacity .2s}.top.show{opacity:1}.top a{pointer-events:auto;color:#fff;background:rgba(8,12,20,.92);border:1px solid #53657d;border-radius:9px;padding:11px 15px;text-decoration:none;font-weight:800}.notice{position:fixed;inset:0;z-index:3;display:none;place-items:center;text-align:center;background:rgba(0,0,0,.72);padding:30px}.notice.show{display:grid}.notice b{font-size:24px}.error{color:#ffbdc5}@media(max-width:760px){.shell{grid-template-columns:40% 1fr;gap:10px;padding:10px}.info{padding:12px}.poster{width:62px;margin-right:10px}.info h1{font-size:20px}.summary{font-size:12px;max-height:10em;overflow:auto}.meta,.subtitle{font-size:12px}}</style></head><body>
<div class="shell"><div class="stage"><video id="channel" autoplay playsinline preload="auto"></video></div><section class="info"><div class="kicker">COMING UP ON CINEMEDIAVAULT</div><img id="poster" class="poster" alt=""><h1 id="title">Preparing the promo channel…</h1><div id="subtitle" class="subtitle"></div><div id="meta" class="meta"></div><p id="summary" class="summary"></p></section></div><div id="top" class="top"><a href="/vchannels">Back to Guide</a><span>CMV Promo Channel</span></div><div id="notice" class="notice"><div><b id="message">Starting promo channel…</b><p>Press OK once if your browser blocks automatic sound.</p></div></div>
<script>
const video=document.getElementById('channel'),notice=document.getElementById('notice'),message=document.getElementById('message'),topbar=document.getElementById('top');let status=null,hideTimer=null,infoTimer=null;
function reveal(){topbar.classList.add('show');clearTimeout(hideTimer);hideTimer=setTimeout(()=>topbar.classList.remove('show'),3000)}
function renderInfo(){const list=status?.programmes||[];if(!list.length)return;const index=Math.floor((Number(video.currentTime)||0)/60)%list.length,item=list[index]||list[0];title.textContent=item.title||'Coming up';subtitle.textContent=item.subtitle||'';meta.textContent=`${item.channel_number||''} ${item.channel||''} · ${new Date(Number(item.airtime)*1000).toLocaleString([],{weekday:'long',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}`;summary.textContent=item.summary||'';poster.src=item.poster||'';poster.style.display=item.poster?'block':'none'}
async function tune(){try{const r=await fetch('/api/vchannels/barker/status',{cache:'no-store'});status=await r.json();if(!status.available)throw new Error(status.reason||'Promo channel is being prepared');const now=Math.floor(Date.now()/1000),duration=Math.max(1,Number(status.duration)||3600),offset=((now-Number(status.slot_start))%duration+duration)%duration;video.src='/api/vchannels/barker/video?v='+status.slot_start;video.addEventListener('loadedmetadata',()=>{video.currentTime=Math.min(offset,Math.max(0,video.duration-.25));renderInfo();clearInterval(infoTimer);infoTimer=setInterval(renderInfo,1000);video.muted=false;video.play().then(()=>notice.classList.remove('show')).catch(()=>{message.textContent='Press OK to start the promo channel';notice.classList.add('show')})},{once:true});video.addEventListener('ended',tune,{once:true})}catch(e){message.textContent=e.message;message.classList.add('error');notice.classList.add('show');setTimeout(tune,15000)}}
function activate(){if(video.paused)video.play().then(()=>notice.classList.remove('show')).catch(()=>{});reveal()}
document.addEventListener('click',activate);document.addEventListener('keydown',e=>{if(['Enter',' ','MediaPlay','MediaPlayPause'].includes(e.key)){activate();e.preventDefault()}else reveal()});document.addEventListener('mousemove',reveal);tune();
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
body.channel-fullscreen .now-info,.guide-open .now-info{display:none}
body.channel-fullscreen video#v{position:fixed;inset:0;width:100vw;height:100vh;height:100dvh;max-height:none;object-fit:contain;background:#000}
.guide-drawer{display:none;position:fixed;inset:0;z-index:30;background:#05070b}.guide-drawer.open{display:block}.guide-drawer iframe{width:100%;height:100%;border:0}.guide-close{position:fixed;right:16px;top:16px;z-index:33}.guide-open video#v{position:fixed;left:16px;top:16px;width:min(38vw,540px);height:auto;aspect-ratio:16/9;max-height:none;z-index:32;border:1px solid rgba(124,195,255,.35);border-radius:12px;object-fit:contain;cursor:pointer}.guide-open header,.guide-open .bar{display:none}
video::-webkit-media-controls-wireless-playback-picker-button{display:none!important}
.up-next{position:fixed;left:0;right:0;bottom:0;z-index:20;background:linear-gradient(0deg,rgba(4,7,14,.97),rgba(4,7,14,.82) 65%,transparent);display:flex;align-items:center;gap:18px;padding:20px 26px;pointer-events:none}
.up-next.hidden{display:none}
.up-next img.next-poster{width:64px;height:96px;object-fit:cover;border-radius:8px;background:#1b2230;flex:0 0 auto}
.up-next img.next-poster.no-art{display:none}
.next-label{color:#7cc3ff;letter-spacing:.14em;font-size:11px;font-weight:900;text-transform:uppercase}
.next-title{font-size:21px;line-height:1.15;margin:2px 0}
.next-subtitle{font-size:13px;color:#c6d1e2}
.next-countdown{margin-top:4px;font-size:13px;font-weight:800;color:#cfe1ff}
.count-number{color:#7cc3ff;font-size:16px}
.now-info{display:grid;grid-template-columns:110px minmax(0,1fr);gap:16px;padding:18px 20px;background:#0d1118;border-top:1px solid #252c37}.now-info img{width:110px;aspect-ratio:2/3;object-fit:cover;border-radius:9px;background:#1b2230}.now-info img.no-art{display:none}.now-kicker{color:#7cc3ff;font-size:12px;font-weight:900;letter-spacing:.13em}.now-info h1{font-size:24px;margin:5px 0}.now-meta{color:#b8c4d4;font-weight:700}.now-description{max-width:850px;color:#d7deea;line-height:1.5;margin:10px 0 0}
@media(max-width:850px){.up-next{padding:12px 14px;gap:10px}.up-next img.next-poster{width:44px;height:66px}.next-title{font-size:15px}.now-info{grid-template-columns:82px minmax(0,1fr);padding:14px;gap:12px}.now-info img{width:82px}.now-info h1{font-size:20px}}
</style></head><body>
<header><div id="pageTitle">__TITLE__</div><a class="btn" href="/vchannels/__KIND_PATH__">Back to Guide</a></header>
<video id="v" autoplay playsinline disableRemotePlayback x-webkit-airplay="deny" __SOURCE_ATTR__>__CAPTION_TRACK__</video>
<div class="bar">__PLAY_BEGINNING__<button id="openGuide" type="button">Guide</button><span id="audioControlHost">__AUDIO_CONTROL__</span><label>CC <select id="captionSelect"><option value="off">Off</option></select></label></div>
<section id="nowInfo" class="now-info"><img id="nowPoster" alt=""><div><div class="now-kicker">NOW PLAYING</div><h1 id="nowTitle"></h1><div id="nowMeta" class="now-meta"></div><p id="nowDescription" class="now-description"></p></div></section>
<div class="guide-drawer" id="guideDrawer"><button class="guide-close" id="closeGuide" type="button">Return to Player</button><iframe id="guideFrame" title="CineVault Guide" data-src="/vchannels/__KIND_PATH__"></iframe></div>
<section id="upNext" class="up-next hidden" aria-live="polite"><img id="nextPoster" class="next-poster" alt=""><div><div class="next-label">Up Next</div><h1 id="nextTitle" class="next-title"></h1><div id="nextSubtitle" class="next-subtitle"></div><div class="next-countdown">Starting in <span id="nextCount" class="count-number">10</span> seconds</div></div></section>
<script src="/assets/hls.min.js"></script>
<script>
const v=document.getElementById('v'),channelId=__CHANNEL_ID__;
const guideDrawer=document.getElementById('guideDrawer'),guideFrame=document.getElementById('guideFrame');
let offset=__OFFSET__,isHls=__IS_HLS__,src="__SOURCE__",next=__NEXT_JSON__,currentInfo=__CURRENT_JSON__,progStart=__ADVANCE_AFTER__,pinFor=__PIN_ADVANCE_AFTER__;
let hls=null,directFallbackStarted=false;
let guidePlacementTimer=null;
function guideInfo(){return{title:currentInfo?.title||'Now Playing',subtitle:currentInfo?.subtitle||'',summary:currentInfo?.overview||'',poster:currentInfo?.poster||'',airtime:currentInfo?.start||0,channel:currentInfo?.channel||'',channel_number:currentInfo?.channel_number||''}}
function syncGuidePlayer(){if(!document.body.classList.contains('guide-open'))return;try{const win=guideFrame.contentWindow,rect=win?.cinevaultBarkerRect?.();if(!rect)return;Object.assign(v.style,{left:rect.left+'px',top:rect.top+'px',width:rect.width+'px',height:rect.height+'px'});win.cinevaultSetActiveProgram?.(guideInfo())}catch(_e){}}
function bindGuideFrame(){try{const doc=guideFrame.contentDocument;if(!doc)return;doc.addEventListener('scroll',syncGuidePlayer,true);doc.defaultView.addEventListener('resize',syncGuidePlayer);doc.defaultView.cinevaultSetActiveProgram?.(guideInfo())}catch(_e){}syncGuidePlayer()}
function openGuide(){guideDrawer.classList.add('open');document.body.classList.add('guide-open');if(!guideFrame.src){guideFrame.addEventListener('load',bindGuideFrame,{once:true});guideFrame.src=guideFrame.dataset.src}else bindGuideFrame();clearInterval(guidePlacementTimer);guidePlacementTimer=setInterval(syncGuidePlayer,250)}
function closeGuide(){clearInterval(guidePlacementTimer);guidePlacementTimer=null;try{guideFrame.contentWindow?.cinevaultClearActiveProgram?.()}catch(_e){}guideDrawer.classList.remove('open');document.body.classList.remove('guide-open');['left','top','width','height'].forEach(k=>v.style[k]='')}
document.getElementById('openGuide').onclick=openGuide;
document.getElementById('closeGuide').onclick=closeGuide;
v.onclick=()=>{if(document.body.classList.contains('guide-open'))closeGuide()};
window.addEventListener('message',event=>{if(event.origin!==location.origin||!event.data||event.data.type!=='cinevault-guide-navigate')return;const href=String(event.data.href||'');if(!href.startsWith(location.origin+'/'))return;v.pause();if(hls){try{hls.destroy()}catch(_e){}hls=null}v.removeAttribute('src');v.load();closeGuide();location.href=href});
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
function wireCaptionSelect(){
  const sel=document.getElementById('captionSelect');if(!sel)return;
  const tracks=Array.from(v.textTracks||[]),prior=sel.value;sel.innerHTML='<option value="off">Off</option>';
  tracks.forEach((track,index)=>{const option=document.createElement('option');option.value=String(index);option.textContent=track.label||track.language||`Track ${index+1}`;sel.appendChild(option)});
  sel.value=prior!=='off'&&tracks[Number(prior)]?prior:'off';tracks.forEach((track,index)=>track.mode=sel.value===String(index)?'showing':'disabled');
  sel.onchange=()=>tracks.forEach((track,index)=>track.mode=sel.value===String(index)?'showing':'disabled');
}
function renderNowPlaying(info){
  info=info||{};document.getElementById('nowTitle').textContent=info.title||'Now Playing';
  const date=t=>t?new Date(t*1000).toLocaleString([],{weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'';
  const bits=[`${info.channel_number||''} ${info.channel||''}`.trim(),info.subtitle||'',info.start?`${date(info.start)}–${new Date(info.stop*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}`:'',info.rating?`★ ${Number(info.rating).toFixed(1)}`:''].filter(Boolean);
  document.getElementById('nowMeta').textContent=bits.join(' · ');document.getElementById('nowDescription').textContent=info.overview||'No description is available for this program.';
  const poster=document.getElementById('nowPoster');poster.src=info.poster||'';poster.classList.toggle('no-art',!info.poster);
  if(document.body.classList.contains('guide-open')){try{guideFrame.contentWindow?.cinevaultSetActiveProgram?.(guideInfo())}catch(_e){}}
}
function applyProgramData(data,usedAdvanceAfter){
  offset=data.offset;isHls=data.is_hls;src=data.source;next=data.next;currentInfo=data.current||currentInfo;renderNowPlaying(currentInfo);
  pinFor=usedAdvanceAfter===undefined?null:usedAdvanceAfter;progStart=data.advance_after;
  document.title=data.title;document.getElementById('pageTitle').textContent=data.title;
  const playBeginning=document.getElementById('playBeginningLink');
  if(playBeginning)playBeginning.href=data.play_beginning_href;
  Array.prototype.slice.call(v.querySelectorAll('track')).forEach(t=>t.remove());
  if(data.caption_track_html){const tmp=document.createElement('div');tmp.innerHTML=data.caption_track_html;Array.prototype.slice.call(tmp.children).forEach(el=>v.appendChild(el))}
  wireCaptionSelect();
  document.getElementById('audioControlHost').innerHTML=data.audio_control_html||'';
  wireAudioSelect();
  directFallbackStarted=false;
  attachSource(src,isHls);
  v.addEventListener('loadedmetadata',seekIn,{once:true});
  history.replaceState(null,'',`/watch/vchannel/${channelId}?advance_after=${progStart}&fullscreen=1`);
}
renderNowPlaying(currentInfo);
wireAudioSelect();
wireCaptionSelect();
v.addEventListener('loadedmetadata',wireCaptionSelect);
function expectedLiveOffset(){return Math.max(0,(Date.now()/1000)-Number(currentInfo?.start||Date.now()/1000))}
function enforceLinearPosition(){if(!Number.isFinite(v.duration)||v.duration<=0)return;const target=Math.min(expectedLiveOffset(),Math.max(0,v.duration-1));if(Math.abs((Number(v.currentTime)||0)-target)>4)v.currentTime=target}
v.addEventListener('pause',()=>{if(v.src)v.play().catch(()=>{})});
v.addEventListener('seeking',enforceLinearPosition);
v.addEventListener('ratechange',()=>{if(v.playbackRate!==1)v.playbackRate=1});
v.addEventListener('contextmenu',e=>e.preventDefault());
document.addEventListener('keydown',e=>{if(['MediaPlayPause','MediaPause','MediaStop','MediaRewind','MediaFastForward'].includes(e.key)||(e.key===' '&&(e.target===v||e.target===document.body))){e.preventDefault();enforceLinearPosition();v.play().catch(()=>{})}},true);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'){enforceLinearPosition();v.play().catch(()=>{})}});
setInterval(()=>{if(!v.paused)enforceLinearPosition()},5000);
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
    document.body.classList.add('channel-fullscreen');
    v.play().catch(()=>{});
    v.addEventListener('ended',showUpNext,{once:true});
  }).catch(()=>{location.href=nextUrlFallback()});
}
function leaveNativeFullscreen(){
  try{
    if(document.fullscreenElement&&document.exitFullscreen)return document.exitFullscreen().catch(()=>{});
    if(document.webkitFullscreenElement&&document.webkitExitFullscreen){document.webkitExitFullscreen();return Promise.resolve()}
    if(v.webkitDisplayingFullscreen&&v.webkitExitFullscreen){v.webkitExitFullscreen();return Promise.resolve()}
  }catch(_e){}
  return Promise.resolve();
}
function revealUpNext(){
  document.body.classList.remove('channel-fullscreen');
  const panel=document.getElementById('upNext');panel.classList.remove('hidden');
  document.getElementById('nextTitle').textContent=next.title||'Programming continues';
  document.getElementById('nextSubtitle').textContent=next.subtitle||'';
  const poster=document.getElementById('nextPoster');poster.src=next.poster||'';poster.classList.toggle('no-art',!next.poster);
  let remaining=10;document.getElementById('nextCount').textContent=remaining;
  const timer=setInterval(()=>{remaining-=1;document.getElementById('nextCount').textContent=Math.max(0,remaining);if(remaining<=0){clearInterval(timer);advanceToNext()}},1000);
}
function showUpNext(){leaveNativeFullscreen().finally(revealUpNext)}
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
    current_details = _resolve_program_details(prog, movie_app, tv_app)
    current_info = {
        "title": title,
        "subtitle": prog.get("subtitle") or "",
        "overview": current_details.get("overview") or "",
        "poster": current_details.get("poster") or "",
        "rating": float(prog.get("rating") or 0),
        "start": int(prog["start_ts"]),
        "stop": int(prog["stop_ts"]),
        "channel": channel["name"],
        "channel_number": channel["channel_number"],
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
        "current": current_info,
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
    current_json = json.dumps(result["current"], ensure_ascii=False).replace("</", "<\\/")
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
        .replace("__CURRENT_JSON__", current_json)
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
    return {"available": True, "title": title, "subtitle": prog.get("subtitle") or "", "overview": details.get("overview") or "", "poster": details.get("poster") or "", "channel": channel["name"], "channel_number": channel["channel_number"], "airtime": int(prog["start_ts"]), "offset": int(source.get("client_offset", clip_offset)), "is_hls": bool(source.get("is_hls")), "source": source.get("source", ""), "narration": f"/api/vchannels/narration/{int(channel_id)}/{int(start_ts)}"}


def _announcement(channel, prog, details=None):
    """Create short, deterministic announcer copy from trusted schedule data."""
    airtime = __import__("datetime").datetime.fromtimestamp(int(prog["start_ts"]), TZ)
    today = __import__("datetime").datetime.now(TZ).date()
    day = "Tonight" if airtime.date() == today else airtime.strftime("%A")
    clock = airtime.strftime("%I:%M %p").lstrip("0").replace(":00", "")
    title = re.sub(r"\s+", " ", str(prog.get("title") or "Coming up")).strip()
    subtitle = re.sub(r"\s+", " ", _spoken_episode(prog.get("subtitle") or "")).strip()
    episode = f", {subtitle}" if subtitle and subtitle.casefold() != title.casefold() else ""
    summary = re.sub(r"\s+", " ", str((details or {}).get("overview") or "")).strip()
    summary_copy = f" {summary}" if summary else ""
    # Keep the brand line occasional so a long reel sounds like programming,
    # not a repeated station ident.
    ident = " Only on Cine Media Vault." if (int(prog["start_ts"]) // 60) % 4 == 0 else ""
    text = f"{day} at {clock}, on {channel['name']}. {title}{episode}.{summary_copy}{ident}"
    family = any(word in channel["name"].casefold() for word in ("kids", "family", "animation", "simpsons"))
    voice = "af_bella" if family else ("am_michael" if channel["kind"] == "movie" else "af_heart")
    return text, voice


def serve_narration(handler, channel_id, start_ts, movie_app, tv_app):
    """Generate-once/cache-forever Kokoro narration and return a browser-ready WAV."""
    channel = channel_by_id(channel_id)
    if not channel:
        return handler.send_error(404, "Channel not found")
    c = connect()
    try:
        row = c.execute("SELECT * FROM vchannel_schedule WHERE channel_id=? AND start_ts=?", (int(channel_id), int(start_ts))).fetchone()
    finally:
        c.close()
    if not row:
        return handler.send_error(404, "Program not found")
    prog = dict(row)
    details = _resolve_program_details(prog, movie_app, tv_app)
    text, voice = _announcement(channel, prog, details)
    cache_key = hashlib.sha256(f"kokoro-v1|{voice}|{text}".encode("utf-8")).hexdigest()
    target = BARKER_TTS_DIR / cache_key[:2] / f"{cache_key}.wav"
    required = (BARKER_TTS_PYTHON, BARKER_TTS_SCRIPT, BARKER_TTS_MODEL, BARKER_TTS_VOICES)
    if not target.exists():
        if not all(path.exists() for path in required):
            return handler.send_error(503, "Barker narration is not installed")
        target.parent.mkdir(parents=True, exist_ok=True)
        with BARKER_TTS_LOCK:
            if not target.exists():
                try:
                    subprocess.run([str(BARKER_TTS_PYTHON), str(BARKER_TTS_SCRIPT), "--model", str(BARKER_TTS_MODEL), "--voices", str(BARKER_TTS_VOICES), "--voice", voice, "--text", text, "--output", str(target)], check=True, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    return handler.send_error(503, "Narration generation failed")
    data = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "audio/wav")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "private, max-age=604800, immutable")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(data)
    return True


def admin_page(handler, message=""):
    stats = schedule_stats()
    state = build_state()
    note = f"<div class='note'>{html.escape(message)}</div>" if message else ""
    body = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Virtual Channel Administration</title><style>
:root{{color-scheme:dark;--gold:#f5b73f}}*{{box-sizing:border-box}}body{{margin:0;background:#08090c;color:#fff;font:16px system-ui,sans-serif}}header,main{{padding:20px;max-width:1050px;margin:auto}}header{{display:flex;justify-content:space-between;border-bottom:1px solid #30343d}}a{{color:#fff}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}}.card{{background:#121720;border:1px solid #303a49;border-radius:16px;padding:18px}}button{{min-height:46px;padding:0 18px;border:0;border-radius:999px;background:var(--gold);color:#111;font-weight:900;cursor:pointer}}button.danger{{background:#732b37;color:#fff}}form{{margin-top:14px}}.note{{padding:12px;background:#15351f;border-radius:10px;margin-bottom:16px}}.muted{{color:#abb4c3}}code{{overflow-wrap:anywhere}}
</style></head><body><header><strong>Virtual Channel Administration</strong><nav><a href="/admin/modules">Modules</a> &middot; <a href="/vchannels">Guide</a> &middot; <a href="/">Home</a></nav></header><main>{note}<h1>24×7 Virtual Schedules</h1><p class="muted">The rolling {HORIZON_DAYS}-day horizon is extended automatically, providing continuous service year-round.</p><div class="cards">
<section class="card"><h2>Movie Channels</h2><p>{stats['movie']['rows']:,} scheduled programs</p><p>{stats['movie']['on_air']} of {stats['movie']['total']} channels currently on air</p></section>
<section class="card"><h2>TV Channels</h2><p>{stats['tv']['rows']:,} scheduled episodes</p><p>{stats['tv']['on_air']} of {stats['tv']['total']} channels currently on air</p></section>
<section class="card"><h2>Build Status</h2><p>{html.escape(str(state.get('last_build_status') or 'unknown'))}</p><p class="muted">Through {html.escape(str(state.get('horizon_until_date') or 'not built'))}</p><p class="muted">Randomization seed: {int(state.get('schedule_seed') or 1)}</p></section>
</div><section class="card"><h2>Schedule Operations</h2><p>Flush removes both Movie and TV schedules. Rebuild creates a new randomized rolling lineup and resets TV episode progression to Season 1/Episode 1 for its newly assigned sequence.</p>
<form method="post" action="/admin/vchannels" onsubmit="return confirm('Flush BOTH Movie and TV virtual schedules? The guide will remain empty until rebuilt.')"><button class="danger" name="action" value="flush">Flush Both Schedules</button></form>
<form method="post" action="/admin/vchannels" onsubmit="return confirm('Back up, flush, re-randomize, and rebuild BOTH virtual schedules now?')"><button name="action" value="rebuild">Rebuild &amp; Re-randomize</button></form>
<form method="post" action="/admin/vchannels" onsubmit="return confirm('Back up, then rebuild and re-randomize only the TV lineup? Movie schedule and all watch state are untouched.')"><button name="action" value="rebuild_tv">Rebuild &amp; Re-randomize TV Only</button></form></section></main></body></html>'''
    return handler.render_html(body)


def combined_barker_status():
    try:
        now = int(time.time())
        candidates = []
        for descriptor in COMBINED_BARKER_DIR.glob("barker-*.json"):
            try:
                candidate = json.loads(descriptor.read_text(encoding="utf-8"))
                if int(candidate.get("slot_start") or 0) <= now:
                    candidates.append(candidate)
            except Exception:
                continue
        if not candidates:
            # Compatibility with the initial/manual renderer build.
            candidates.append(json.loads((COMBINED_BARKER_DIR / "current.json").read_text(encoding="utf-8")))
        data = max(candidates, key=lambda item: int(item.get("slot_start") or 0))
        media = (COMBINED_BARKER_DIR / str(data.get("filename") or "")).resolve()
        root = COMBINED_BARKER_DIR.resolve()
        if media.parent != root or not media.is_file():
            raise FileNotFoundError
        return {"available": True, "slot_start": int(data["slot_start"]),
                "duration": float(data.get("duration") or 3600), "generated_at": int(data.get("generated_at") or 0),
                "filename": media.name, "programmes": data.get("programmes") or []}
    except Exception:
        return {"available": False, "reason": "The next promo reel is still being prepared"}


def handle_get(handler, user, path, movie_app, tv_app, resolve_source_fn=None, caption_fn=None, preview_source_fn=None, audio_markup_fn=None):
    if path == "/admin/vchannels":
        if not user["is_admin"]:
            return handler.send_error(403)
        return admin_page(handler)
    if path == "/api/vchannels/barker/status":
        return handler.json_response(combined_barker_status())
    if path == "/api/vchannels/barker/video":
        state = combined_barker_status()
        if not state.get("available"):
            return handler.send_error(503, state.get("reason", "Promo channel unavailable"))
        return handler.serve_file(COMBINED_BARKER_DIR / state["filename"], disposition="inline", service="movie")
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
    if path.startswith("/api/vchannels/narration/"):
        parts = path.strip("/").split("/")
        try:
            channel_id, start_ts = int(parts[-2]), int(parts[-1])
        except (ValueError, IndexError):
            return handler.send_error(404, "Narration not found")
        return serve_narration(handler, channel_id, start_ts, movie_app, tv_app)
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
            if action == "rebuild_tv":
                # TV-only rebuild + re-randomize (new schedule_seed): unlike
                # "rebuild" above, never touches the movie lineup at all -
                # only vchannel_schedule/progress/slot rows for kind='tv' are
                # deleted before regenerating. Used after adding/changing TV
                # channel definitions so the whole TV lineup gets a genuinely
                # fresh shuffle without disturbing movies or any watch state
                # (a completely separate table).
                result = rebuild_tv_schedules(movie_app, tv_app, randomize=True)
                return admin_page(handler, f"TV schedules rebuilt and re-randomized (movies untouched). Backup: {result['backup']}")
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
