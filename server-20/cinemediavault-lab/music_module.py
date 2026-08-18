#!/usr/bin/env python3
"""CineMediaVault Music module: scanner, API, player, playlists, and downloads."""

import html
import json
import mimetypes
import os
import random
import re
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

MUSIC_ROOT = Path(os.environ.get("CINEVAULT_MUSIC_ROOT", "/media/jnicolas/Expansion/Music")).resolve()
MUSIC_DB = Path(os.environ.get("CINEVAULT_MUSIC_DB", "/home/jnicolas/cinemediavault-lab/music.db")).resolve()
ART_CACHE = Path(os.environ.get("CINEVAULT_MUSIC_ART_CACHE", "/home/jnicolas/cinemediavault-lab/music-art")).resolve()
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma", ".alac"}
SCAN_LOCK = threading.Lock()
SCAN_STATE = {"running": False, "scanned": 0, "updated": 0, "total": 0, "current": "", "error": "", "started_at": 0, "finished_at": 0}


def connect():
    MUSIC_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(MUSIC_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize():
    conn = connect()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS artists (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_norm TEXT NOT NULL UNIQUE,
          sort_name TEXT NOT NULL DEFAULT '', artwork TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS albums (
          id INTEGER PRIMARY KEY, artist_id INTEGER REFERENCES artists(id), title TEXT NOT NULL,
          title_norm TEXT NOT NULL, year INTEGER NOT NULL DEFAULT 0, genre TEXT NOT NULL DEFAULT '',
          artwork TEXT NOT NULL DEFAULT '', UNIQUE(artist_id,title_norm,year)
        );
        CREATE TABLE IF NOT EXISTS tracks (
          id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, mtime REAL NOT NULL, size INTEGER NOT NULL,
          artist_id INTEGER REFERENCES artists(id), album_id INTEGER REFERENCES albums(id),
          title TEXT NOT NULL, track_number INTEGER NOT NULL DEFAULT 0, disc_number INTEGER NOT NULL DEFAULT 0,
          year INTEGER NOT NULL DEFAULT 0, genre TEXT NOT NULL DEFAULT '', duration REAL NOT NULL DEFAULT 0,
          bitrate INTEGER NOT NULL DEFAULT 0, codec TEXT NOT NULL DEFAULT '', added_at REAL NOT NULL,
          updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist_id);
        CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_id);
        CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);
        CREATE TABLE IF NOT EXISTS playlists (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
          artwork TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL,
          UNIQUE(user_id,name)
        );
        CREATE TABLE IF NOT EXISTS playlist_tracks (
          playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
          track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          position INTEGER NOT NULL, added_at REAL NOT NULL,
          PRIMARY KEY(playlist_id,track_id)
        );
        CREATE TABLE IF NOT EXISTS user_music_state (
          user_id INTEGER NOT NULL, track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
          position REAL NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
          rating INTEGER NOT NULL DEFAULT 0, play_count INTEGER NOT NULL DEFAULT 0,
          last_played_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(user_id,track_id)
        );
        """)
        conn.commit()
    finally:
        conn.close()


def norm(value):
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def int_tag(value):
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else 0


def probe(path):
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,bit_rate:format_tags=artist,album_artist,title,album,date,year,genre,track,disc",
            "-show_entries", "stream=codec_name", "-of", "json", str(path)
        ], capture_output=True, text=True, timeout=20, check=True)
        data = json.loads(result.stdout or "{}")
    except Exception:
        data = {}
    fmt = data.get("format") or {}
    tags = {str(k).lower(): str(v).strip() for k, v in (fmt.get("tags") or {}).items()}
    filename = path.stem
    fallback_artist, fallback_title = (filename.split(" - ", 1) + [filename])[:2] if " - " in filename else ("Unknown Artist", filename)
    artist = tags.get("album_artist") or tags.get("artist") or fallback_artist
    title = tags.get("title") or fallback_title
    album = tags.get("album") or "Unknown Album"
    streams = data.get("streams") or []
    return {
        "artist": artist, "title": title, "album": album,
        "year": int_tag(tags.get("date") or tags.get("year")), "genre": tags.get("genre", ""),
        "track": int_tag(tags.get("track")), "disc": int_tag(tags.get("disc")),
        "duration": float(fmt.get("duration") or 0), "bitrate": int(fmt.get("bit_rate") or 0),
        "codec": str((streams[0] if streams else {}).get("codec_name") or path.suffix.lstrip(".")),
    }


def upsert_track(conn, path, stat):
    existing = conn.execute("SELECT id,mtime,size FROM tracks WHERE path=?", (str(path),)).fetchone()
    if existing and abs(existing["mtime"] - stat.st_mtime) < .01 and existing["size"] == stat.st_size:
        return False
    meta = probe(path)
    artist_norm = norm(meta["artist"])
    conn.execute("INSERT INTO artists(name,name_norm,sort_name) VALUES(?,?,?) ON CONFLICT(name_norm) DO UPDATE SET name=excluded.name",
                 (meta["artist"], artist_norm, meta["artist"]))
    artist_id = conn.execute("SELECT id FROM artists WHERE name_norm=?", (artist_norm,)).fetchone()["id"]
    album_norm = norm(meta["album"])
    conn.execute("""INSERT INTO albums(artist_id,title,title_norm,year,genre) VALUES(?,?,?,?,?)
      ON CONFLICT(artist_id,title_norm,year) DO UPDATE SET title=excluded.title,genre=excluded.genre""",
                 (artist_id, meta["album"], album_norm, meta["year"], meta["genre"]))
    album_id = conn.execute("SELECT id FROM albums WHERE artist_id=? AND title_norm=? AND year=?",
                            (artist_id, album_norm, meta["year"])).fetchone()["id"]
    now = time.time()
    conn.execute("""INSERT INTO tracks(path,mtime,size,artist_id,album_id,title,track_number,disc_number,year,genre,duration,bitrate,codec,added_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime,size=excluded.size,
      artist_id=excluded.artist_id,album_id=excluded.album_id,title=excluded.title,track_number=excluded.track_number,
      disc_number=excluded.disc_number,year=excluded.year,genre=excluded.genre,duration=excluded.duration,
      bitrate=excluded.bitrate,codec=excluded.codec,updated_at=excluded.updated_at""",
      (str(path), stat.st_mtime, stat.st_size, artist_id, album_id, meta["title"], meta["track"], meta["disc"],
       meta["year"], meta["genre"], meta["duration"], meta["bitrate"], meta["codec"], now, now))
    return True


def scan_library():
    if not SCAN_LOCK.acquire(blocking=False):
        return
    SCAN_STATE.update(running=True, scanned=0, updated=0, total=0, current="", started_at=time.time(), error="")
    try:
        conn = connect()
        try:
            present = set()
            index = 0
            for root, dirs, files in os.walk(MUSIC_ROOT):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"@eaDir", "$RECYCLE.BIN"}]
                for name in files:
                    path = Path(root) / name
                    if path.suffix.lower() not in AUDIO_EXTENSIONS:
                        continue
                    index += 1
                    SCAN_STATE["total"] = index
                    SCAN_STATE["current"] = str(path.relative_to(MUSIC_ROOT))[-140:]
                    present.add(str(path))
                    try:
                        if upsert_track(conn, path, path.stat()):
                            SCAN_STATE["updated"] += 1
                    except OSError:
                        pass
                    SCAN_STATE["scanned"] = index
                    if index % 50 == 0:
                        conn.commit()
                    time.sleep(0.02)
            conn.commit()
            for row in conn.execute("SELECT id,path FROM tracks").fetchall():
                if row["path"] not in present:
                    conn.execute("DELETE FROM tracks WHERE id=?", (row["id"],))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        SCAN_STATE["error"] = str(exc)
    finally:
        SCAN_STATE.update(running=False, current="", finished_at=time.time())
        SCAN_LOCK.release()


def start_scan():
    if not SCAN_STATE["running"]:
        threading.Thread(target=scan_library, daemon=True, name="music-scan").start()
    return dict(SCAN_STATE)


def track_dict(row):
    return {
        "id": row["id"], "title": row["title"], "artist": row["artist"], "album": row["album"],
        "year": row["year"], "genre": row["genre"], "duration": row["duration"], "size": row["size"],
        "codec": row["codec"], "bitrate": row["bitrate"], "track_number": row["track_number"],
        "stream": f"/music/stream/{row['id']}", "artwork": f"/music/art/{row['id']}",
        "download": f"/music/download/{row['id']}",
    }


TRACK_SELECT = """SELECT t.*,ar.name artist,al.title album FROM tracks t
 JOIN artists ar ON ar.id=t.artist_id JOIN albums al ON al.id=t.album_id"""


def query_tracks(query="", limit=200, offset=0, artist_id=0, album_id=0):
    clauses, args = [], []
    if query:
        clauses.append("(lower(t.title) LIKE ? OR lower(ar.name) LIKE ? OR lower(al.title) LIKE ? OR lower(t.genre) LIKE ?)")
        token = f"%{query.casefold()}%"
        args.extend([token] * 4)
    if artist_id:
        clauses.append("t.artist_id=?"); args.append(artist_id)
    if album_id:
        clauses.append("t.album_id=?"); args.append(album_id)
    sql = TRACK_SELECT + ((" WHERE " + " AND ".join(clauses)) if clauses else "")
    sql += " ORDER BY ar.name,al.year,al.title,t.disc_number,t.track_number,t.title LIMIT ? OFFSET ?"
    args.extend([min(max(limit, 1), 1000), max(offset, 0)])
    conn = connect()
    try:
        return [track_dict(row) for row in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def json_response(handler, payload, status=200):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status); handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data))); handler.send_header("Cache-Control", "no-store")
    handler.end_headers(); handler.wfile.write(data)


def read_json(handler):
    try:
        return json.loads(handler.rfile.read(int(handler.headers.get("Content-Length") or 0)).decode("utf-8"))
    except Exception:
        return {}


def safe_track(track_id):
    conn = connect()
    try:
        row = conn.execute(TRACK_SELECT + " WHERE t.id=?", (int(track_id),)).fetchone()
        if not row:
            return None, None
        path = Path(row["path"]).resolve()
        if not str(path).startswith(str(MUSIC_ROOT) + os.sep) or not path.is_file():
            return None, None
        return row, path
    finally:
        conn.close()


def serve_file(handler, path, download_name="", head=False):
    size = path.stat().st_size
    start, end = 0, size - 1
    range_header = handler.headers.get("Range", "")
    match = re.match(r"bytes=(\d*)-(\d*)", range_header)
    status = 200
    if match:
        start = int(match.group(1) or 0); end = min(int(match.group(2) or end), end); status = 206
    length = max(0, end - start + 1)
    handler.send_response(status)
    handler.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    handler.send_header("Accept-Ranges", "bytes"); handler.send_header("Content-Length", str(length))
    if status == 206: handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    if download_name: handler.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(download_name)}")
    handler.end_headers()
    if head: return
    with path.open("rb") as source:
        source.seek(start); remaining = length
        while remaining:
            chunk = source.read(min(1024 * 256, remaining))
            if not chunk: break
            handler.wfile.write(chunk); remaining -= len(chunk)


def artwork_path(track_id):
    row, source = safe_track(track_id)
    if not row: return None
    ART_CACHE.mkdir(parents=True, exist_ok=True)
    target = ART_CACHE / f"track-{track_id}.jpg"
    if target.is_file(): return target
    preferred = {"cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png", "albumart.jpg"}
    for candidate in source.parent.iterdir():
        if candidate.is_file() and candidate.name.casefold() in preferred:
            return candidate
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-an", "-frames:v", "1", "-vf", "scale=700:-2", str(target)], timeout=25, check=True)
    except Exception:
        pass
    if target.is_file():
        return target
    missing = ART_CACHE / f"track-{track_id}.missing"
    if missing.is_file() and time.time() - missing.stat().st_mtime < 86400 * 14:
        return None
    try:
        query = f"{row['artist']} {row['title']}"
        url = "https://itunes.apple.com/search?" + urllib.parse.urlencode({"term": query, "entity": "song", "limit": 1})
        request = urllib.request.Request(url, headers={"User-Agent": "CineMediaVault/1.0"})
        with urllib.request.urlopen(request, timeout=12) as response:
            result = json.load(response)
        artwork = (result.get("results") or [{}])[0].get("artworkUrl100", "")
        if artwork:
            artwork = artwork.replace("100x100bb", "700x700bb")
            request = urllib.request.Request(artwork, headers={"User-Agent": "CineMediaVault/1.0"})
            with urllib.request.urlopen(request, timeout=20) as response, target.open("wb") as output:
                output.write(response.read())
            if target.stat().st_size > 1024:
                return target
    except Exception:
        pass
    missing.touch()
    return None


def playlist_api(handler, user):
    payload = read_json(handler); action = payload.get("action", "list"); user_id = int(user["id"])
    conn = connect()
    try:
        if action == "create":
            now = time.time(); name = str(payload.get("name") or "New Playlist").strip()[:120]
            conn.execute("INSERT INTO playlists(user_id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)", (user_id,name,str(payload.get("description") or "")[:500],now,now)); conn.commit()
        elif action == "details":
            pid = int(payload.get("playlist_id") or 0)
            owner = conn.execute("SELECT * FROM playlists WHERE id=? AND user_id=?",(pid,user_id)).fetchone()
            if not owner: return json_response(handler,{"ok":False,"error":"Playlist not found"},404)
            tracks = conn.execute(TRACK_SELECT + " JOIN playlist_tracks pt ON pt.track_id=t.id WHERE pt.playlist_id=? ORDER BY pt.position",(pid,)).fetchall()
            return json_response(handler,{"ok":True,"playlist":dict(owner),"tracks":[track_dict(row) for row in tracks]})
        elif action == "rename":
            pid = int(payload.get("playlist_id") or 0); name = str(payload.get("name") or "").strip()[:120]
            if not name: return json_response(handler,{"ok":False,"error":"Playlist name is required"},400)
            conn.execute("UPDATE playlists SET name=?,updated_at=? WHERE id=? AND user_id=?",(name,time.time(),pid,user_id)); conn.commit()
        elif action == "delete":
            conn.execute("DELETE FROM playlists WHERE id=? AND user_id=?", (int(payload.get("playlist_id") or 0),user_id)); conn.commit()
        elif action == "add":
            pid, tid = int(payload.get("playlist_id") or 0), int(payload.get("track_id") or 0)
            if not conn.execute("SELECT 1 FROM playlists WHERE id=? AND user_id=?", (pid,user_id)).fetchone(): return json_response(handler,{"ok":False,"error":"Playlist not found"},404)
            pos = conn.execute("SELECT COALESCE(MAX(position),0)+1 n FROM playlist_tracks WHERE playlist_id=?",(pid,)).fetchone()["n"]
            conn.execute("INSERT OR REPLACE INTO playlist_tracks VALUES(?,?,?,?)",(pid,tid,pos,time.time())); conn.execute("UPDATE playlists SET updated_at=? WHERE id=?",(time.time(),pid)); conn.commit()
        elif action == "remove":
            pid, tid = int(payload.get("playlist_id") or 0), int(payload.get("track_id") or 0)
            conn.execute("DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=? AND playlist_id IN (SELECT id FROM playlists WHERE user_id=?)",(pid,tid,user_id)); conn.commit()
        elif action == "move":
            pid, tid = int(payload.get("playlist_id") or 0), int(payload.get("track_id") or 0)
            direction = -1 if str(payload.get("direction")) == "up" else 1
            if not conn.execute("SELECT 1 FROM playlists WHERE id=? AND user_id=?",(pid,user_id)).fetchone(): return json_response(handler,{"ok":False,"error":"Playlist not found"},404)
            current = conn.execute("SELECT position FROM playlist_tracks WHERE playlist_id=? AND track_id=?",(pid,tid)).fetchone()
            if current:
                op = "<" if direction < 0 else ">"; order = "DESC" if direction < 0 else "ASC"
                neighbor = conn.execute(f"SELECT track_id,position FROM playlist_tracks WHERE playlist_id=? AND position {op} ? ORDER BY position {order} LIMIT 1",(pid,current["position"])).fetchone()
                if neighbor:
                    marker = -int(time.time() * 1000)
                    conn.execute("UPDATE playlist_tracks SET position=? WHERE playlist_id=? AND track_id=?",(marker,pid,tid))
                    conn.execute("UPDATE playlist_tracks SET position=? WHERE playlist_id=? AND track_id=?",(current["position"],pid,neighbor["track_id"]))
                    conn.execute("UPDATE playlist_tracks SET position=? WHERE playlist_id=? AND track_id=?",(neighbor["position"],pid,tid)); conn.commit()
        rows = conn.execute("""SELECT p.*,COUNT(pt.track_id) track_count,COALESCE(SUM(t.duration),0) duration
          FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id=p.id LEFT JOIN tracks t ON t.id=pt.track_id
          WHERE p.user_id=? GROUP BY p.id ORDER BY p.updated_at DESC""",(user_id,)).fetchall()
        return json_response(handler,{"ok":True,"playlists":[dict(row) for row in rows]})
    except sqlite3.IntegrityError as exc:
        return json_response(handler,{"ok":False,"error":str(exc)},409)
    finally: conn.close()


def playlist_tracks(playlist_id, user_id):
    conn = connect()
    try:
        owner = conn.execute("SELECT * FROM playlists WHERE id=? AND user_id=?",(playlist_id,user_id)).fetchone()
        if not owner: return None, []
        rows = conn.execute(TRACK_SELECT + " JOIN playlist_tracks pt ON pt.track_id=t.id WHERE pt.playlist_id=? ORDER BY pt.position",(playlist_id,)).fetchall()
        return dict(owner), [track_dict(row) for row in rows]
    finally: conn.close()


def download_playlist(handler, playlist_id, user_id):
    playlist, tracks = playlist_tracks(playlist_id, user_id)
    if not playlist: return handler.send_error(404)
    cache = MUSIC_DB.parent / "music-downloads"; cache.mkdir(parents=True,exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._ -]+","",playlist["name"]).strip() or "playlist"
    target = cache / f"{safe}-{playlist_id}.zip"
    with zipfile.ZipFile(target,"w",zipfile.ZIP_STORED,allowZip64=True) as archive:
        manifest = {"type":"music_playlist","playlist":playlist,"tracks":tracks,"created_at":time.time()}
        archive.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
        for index, track in enumerate(tracks,1):
            row,path=safe_track(track["id"])
            if path:
                name=re.sub(r"[\\/:*?\"<>|]+","_",f"{index:03d} - {track['artist']} - {track['title']}{path.suffix}")
                archive.write(path,f"Music/{name}")
                art=artwork_path(track["id"])
                if art: archive.write(art,f"Artwork/{track['id']}.jpg")
    return serve_file(handler,target,target.name)


def api_library(handler):
    params=urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
    q=params.get("q",[""])[0]; limit=int(params.get("limit",["200"])[0]); offset=int(params.get("offset",["0"])[0])
    tracks=query_tracks(q,limit,offset)
    conn=connect()
    try:
        stats=dict(conn.execute("SELECT COUNT(*) tracks,COUNT(DISTINCT artist_id) artists,COUNT(DISTINCT album_id) albums,COALESCE(SUM(duration),0) duration,COALESCE(SUM(size),0) bytes FROM tracks").fetchone())
    finally: conn.close()
    return json_response(handler,{"ok":True,"tracks":tracks,"stats":stats,"scan":dict(SCAN_STATE)})


MUSIC_PAGE = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CineMediaVault Music</title><style>
:root{color-scheme:dark;--gold:#f5b73f;--panel:#111722;--panel2:#171e2a;--muted:#a7afbd}*{box-sizing:border-box}body{margin:0;background:#07090d;color:#fff;font-family:Inter,system-ui,sans-serif;padding-bottom:150px}header{position:sticky;top:0;z-index:4;background:rgba(7,9,13,.95);backdrop-filter:blur(18px);padding:18px max(20px,4vw);border-bottom:1px solid #252b36;display:grid;grid-template-columns:auto minmax(220px,1fr) auto auto auto;align-items:center;gap:12px}.brand{font-size:22px;font-weight:950;white-space:nowrap}.brand b{color:var(--gold)}button,input{font:inherit}button{border:0;border-radius:999px;padding:11px 17px;font-weight:850;cursor:pointer}.primary{background:var(--gold);color:#111}.ghost{background:#222936;color:#fff}.search{width:100%;background:#1a202b;border:1px solid #343d4d;color:#fff;border-radius:999px;padding:13px 18px}main{max-width:1100px;margin:auto;padding:28px max(20px,4vw)}.stats{color:var(--muted);margin-bottom:20px}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}.tracks{display:grid;gap:12px}.track{display:grid;grid-template-columns:76px minmax(0,1fr) auto;gap:16px;align-items:center;padding:14px;border:1px solid #232c3a;border-radius:12px;background:var(--panel)}.cover{width:76px;height:76px;border-radius:8px;object-fit:cover;background:linear-gradient(135deg,#202735,#0d1118)}.track strong,.track small{display:block}.track strong{font-size:17px;margin-bottom:5px}.track small{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.actions{display:flex;gap:8px;align-items:center}.actions button{min-height:42px}.player{position:fixed;z-index:8;left:0;right:0;bottom:0;background:rgba(13,16,23,.98);border-top:1px solid #303746;padding:12px max(20px,4vw);display:grid;grid-template-columns:66px minmax(150px,1fr) auto minmax(180px,2fr) 120px;gap:14px;align-items:center}.player img{width:66px;height:66px;object-fit:cover;border-radius:8px;background:#202735}.now strong,.now small{display:block}.now small{color:var(--muted);margin-top:4px}.controls{display:flex;gap:7px}.controls button{min-width:44px;height:44px;padding:0 11px}.seek{display:flex;align-items:center;gap:8px}.seek input,.volume{width:100%}.time{font-variant-numeric:tabular-nums;color:var(--muted);font-size:12px}.drawer{position:fixed;z-index:12;inset:0 0 0 auto;width:min(460px,96vw);background:#0c1017;padding:24px;transform:translateX(105%);transition:.2s;overflow:auto;border-left:1px solid #303746}.drawer.open{transform:none}.drawer-head{display:flex;justify-content:space-between;align-items:center}.add-note{color:var(--gold);min-height:24px}.playlist{padding:14px;background:var(--panel2);border-radius:10px;margin:10px 0;display:flex;gap:10px;justify-content:space-between;align-items:center}.playlist small{display:block;color:var(--muted);margin-top:3px}
/* The Back control is grouped with the brand so the existing responsive header stays stable. */
.brand-wrap{display:flex;align-items:center;gap:10px}.back-button{width:42px;height:42px;padding:0;font-size:24px;line-height:1}
@media(max-width:760px){body{padding-bottom:230px}header{position:relative;grid-template-columns:1fr auto auto;padding:18px 20px}.brand{font-size:20px}.search{grid-column:1/-1;grid-row:2}.home-link{display:none}main{padding:22px 18px}.track{grid-template-columns:92px minmax(0,1fr);padding:14px;gap:14px}.cover{width:92px;height:92px}.track strong{font-size:18px}.actions{grid-column:1/-1;display:grid;grid-template-columns:1fr 1.25fr 1fr}.actions button{width:100%;min-height:44px}.player{grid-template-columns:78px minmax(0,1fr);padding:14px 18px;gap:12px}.player img{width:78px;height:78px}.controls{grid-column:1/-1;justify-content:center}.controls button{min-width:48px}.seek{grid-column:1/-1}.volume{display:none}}
</style></head><body><header><span class="brand-wrap"><button id="musicBack" class="ghost back-button" type="button" title="Back" aria-label="Back">&#8592;</button><span class="brand">CINE MEDIA | <b>MUSIC</b></span></span><input id="search" class="search" placeholder="Search artists, albums, tracks, or genres"><button id="shuffleAll" class="ghost">Shuffle</button><button id="playlistButton" class="primary">Playlists</button><a class="home-link" href="/" style="color:white">Home</a></header><main><div id="stats" class="stats">Loading music library...</div><div class="toolbar"><button id="scan" class="ghost">Scan Music</button><button data-sort="artist" class="ghost">Artists</button><button data-sort="album" class="ghost">Albums</button><button data-sort="track" class="ghost">Tracks</button></div><section id="tracks" class="tracks"></section></main>
<aside id="drawer" class="drawer"><div class="drawer-head"><h2>Playlists</h2><button id="closeDrawer" class="ghost">Close</button></div><p id="addNote" class="add-note"></p><form id="newPlaylist"><input id="playlistName" class="search" placeholder="New playlist name" required><button class="primary">Create playlist</button></form><div id="playlists"></div></aside>
<footer class="player"><img id="art" alt=""><div class="now"><strong id="nowTitle">Nothing playing</strong><small id="nowArtist"></small></div><div class="controls"><button id="prev">|&lt;</button><button id="play">Play</button><button id="next">&gt;|</button><button id="shuffle">Shuffle</button><button id="repeat">Repeat</button></div><div class="seek"><span id="elapsed" class="time">0:00</span><input id="seek" type="range" min="0" max="1000" value="0"><span id="duration" class="time">0:00</span></div><input id="volume" class="volume" type="range" min="0" max="1" value="1" step=".05"></footer><audio id="audio" preload="metadata"></audio>
<script>
const $=s=>document.querySelector(s),audio=$('#audio'),list=$('#tracks');let tracks=[],queue=[],index=-1,playlists=[],pendingTrack=0,repeat=false,shuffle=false,timer;
$('#musicBack').onclick=()=>{if(history.length>1)history.back();else location.href='/'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const fmt=s=>{s=Math.max(0,Math.floor(s||0));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
async function load(q=''){const d=await fetch('/api/music/library?q='+encodeURIComponent(q)).then(r=>r.json());tracks=d.tracks||[];queue=[...tracks];render();const x=d.stats||{};$('#stats').textContent=`${Number(x.tracks||0).toLocaleString()} tracks · ${Number(x.artists||0).toLocaleString()} artists · ${Number(x.albums||0).toLocaleString()} albums · ${Math.round((x.bytes||0)/1073741824)} GB`}
function render(){list.innerHTML=tracks.map((t,i)=>`<article class="track"><img class="cover" src="${t.artwork}" loading="lazy" onerror="this.style.visibility='hidden'"><div><strong>${esc(t.title)}</strong><small>${esc(t.artist)} · ${esc(t.album)}${t.year?' · '+t.year:''}</small></div><div class="actions"><button class="primary" data-play="${i}">Play</button><button class="ghost" data-add="${t.id}">+</button><a href="${t.download}" download><button class="ghost">Down</button></a></div></article>`).join('')}
function setTrack(i,autoplay=true){if(!queue.length)return;index=(i+queue.length)%queue.length;const t=queue[index];audio.src=t.stream;$('#art').src=t.artwork;$('#nowTitle').textContent=t.title;$('#nowArtist').textContent=t.artist+' · '+t.album;if(autoplay)audio.play()}
list.onclick=e=>{const p=e.target.closest('[data-play]');if(p){queue=[...tracks];setTrack(+p.dataset.play);return}const a=e.target.closest('[data-add]');if(a)addToPlaylist(+a.dataset.add)};
$('#play').onclick=()=>audio.paused?audio.play():audio.pause();$('#prev').onclick=()=>setTrack(index-1);$('#next').onclick=()=>setTrack(index+1);$('#shuffleAll').onclick=()=>{queue=[...tracks].sort(()=>Math.random()-.5);setTrack(0)};$('#shuffle').onclick=()=>{shuffle=!shuffle;$('#shuffle').classList.toggle('primary',shuffle)};$('#repeat').onclick=()=>{repeat=!repeat;$('#repeat').classList.toggle('primary',repeat)};
audio.onplay=()=>$('#play').textContent='Pause';audio.onpause=()=>$('#play').textContent='Play';audio.ontimeupdate=()=>{if(!audio.duration)return;$('#seek').value=audio.currentTime/audio.duration*1000;$('#elapsed').textContent=fmt(audio.currentTime);$('#duration').textContent=fmt(audio.duration)};audio.onended=()=>repeat?(audio.currentTime=0,audio.play()):setTrack(shuffle?Math.floor(Math.random()*queue.length):index+1);$('#seek').oninput=e=>{if(audio.duration)audio.currentTime=e.target.value/1000*audio.duration};$('#volume').oninput=e=>audio.volume=e.target.value;
$('#search').oninput=e=>{clearTimeout(timer);timer=setTimeout(()=>load(e.target.value),250)};
async function scanStatus(){try{const d=await fetch('/api/music/scan-status').then(r=>r.json()),s=d.scan||{};if(s.running){$('#scan').textContent=`Scanning ${Number(s.scanned||0).toLocaleString()}`;$('#stats').textContent=`Music scan active · ${Number(s.scanned||0).toLocaleString()} checked · ${Number(s.updated||0).toLocaleString()} updated${s.current?' · '+s.current:''}`;setTimeout(scanStatus,1500)}else{$('#scan').textContent='Scan Music';if(s.finished_at)load($('#search').value)}}catch(e){setTimeout(scanStatus,3000)}}
$('#scan').onclick=async()=>{await fetch('/api/music/scan',{method:'POST'});scanStatus()};scanStatus();
async function api(body){return fetch('/api/music/playlists',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json())}async function loadPlaylists(){const d=await api({action:'list'});playlists=d.playlists||[];$('#playlists').innerHTML=playlists.map(p=>`<div class="playlist"><div><strong>${esc(p.name)}</strong><small>${p.track_count} tracks</small></div><div><button data-select="${p.id}">Select</button><a href="/music/download-playlist/${p.id}"><button>Down</button></a></div></div>`).join('')}
async function addToPlaylist(track){if(!selectedPlaylist){$('#drawer').classList.add('open');await loadPlaylists();return}await api({action:'add',playlist_id:selectedPlaylist,track_id:track});await loadPlaylists()}$('#playlistButton').onclick=()=>{$('#drawer').classList.add('open');loadPlaylists()};$('#closeDrawer').onclick=()=>$('#drawer').classList.remove('open');$('#playlists').onclick=e=>{const b=e.target.closest('[data-select]');if(b){selectedPlaylist=+b.dataset.select;$('#drawer').classList.remove('open')}};$('#newPlaylist').onsubmit=async e=>{e.preventDefault();await api({action:'create',name:$('#playlistName').value});$('#playlistName').value='';loadPlaylists()};load();
</script></body></html>'''

# A restrained player theme keeps controls compact while preserving generous artwork.
MUSIC_PAGE = MUSIC_PAGE.replace('</style>', r'''
.track{border-radius:8px;background:#15191d;border-color:#2d3439;padding:11px}.cover{border-radius:6px}.actions{gap:7px}.actions button{min-height:40px}.actions a button{width:40px;min-width:40px;padding:0;border-radius:50%;font-size:0}.actions a button::before{content:"\2193";font-size:21px;font-weight:700}.primary{background:#35d39a;color:#07130f}.ghost{background:#242a2f}.search{border-radius:8px;background:#171b1f}.toolbar button{border-radius:8px}.player{background:rgba(15,18,20,.98)}
@media(max-width:760px){body{padding-bottom:190px}.track{grid-template-columns:72px minmax(0,1fr);gap:11px;padding:10px}.cover{width:72px;height:72px}.actions{grid-column:2;display:flex;justify-content:flex-start}.actions button{width:auto;min-height:40px;padding:8px 13px}.actions a button{width:40px}.player{grid-template-columns:64px minmax(0,1fr);padding:10px 14px}.player img{width:64px;height:64px}}
</style>''')

# Keep the player template readable above while applying the richer playlist labels and flow here.
MUSIC_PAGE = MUSIC_PAGE.replace(
    'onerror="this.style.visibility=\'hidden\'"',
    ''
).replace(
    '<button class="ghost" data-add="${t.id}">+</button>',
    '<button class="ghost" data-add="${t.id}" data-title="${esc(t.title)}">Add to playlist</button>'
).replace(
    '<button class="ghost">Down</button>',
    '<button class="ghost">Download</button>'
).replace(
    '<button>Down</button>',
    '<button class="ghost">Download</button>'
).replace(
    "if(a)addToPlaylist(+a.dataset.add)",
    "if(a)addToPlaylist(+a.dataset.add,a.dataset.title)"
).replace(
    "async function api(body){return fetch('/api/music/playlists',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json())}async function loadPlaylists(){const d=await api({action:'list'});playlists=d.playlists||[];$('#playlists').innerHTML=playlists.map(p=>`<div class=\"playlist\"><div><strong>${esc(p.name)}</strong><small>${p.track_count} tracks</small></div><div><button data-select=\"${p.id}\">Select</button><a href=\"/music/download-playlist/${p.id}\"><button>Down</button></a></div></div>`).join('')}",
    "async function api(body){return fetch('/api/music/playlists',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json())}async function loadPlaylists(){const d=await api({action:'list'});playlists=d.playlists||[];$('#playlists').innerHTML=playlists.length?playlists.map(p=>`<div class=\"playlist\"><div><strong>${esc(p.name)}</strong><small>${p.track_count} tracks</small></div><div>${pendingTrack?`<button class=\"primary\" data-add-to=\"${p.id}\">Add here</button>`:''}<a href=\"/music/download-playlist/${p.id}\"><button class=\"ghost\">Download</button></a></div></div>`).join(''):'<p class=\"stats\">Create your first playlist above.</p>'}"
).replace(
    "async function addToPlaylist(track){if(!selectedPlaylist){$('#drawer').classList.add('open');await loadPlaylists();return}await api({action:'add',playlist_id:selectedPlaylist,track_id:track});await loadPlaylists()}$('#playlistButton').onclick=()=>{$('#drawer').classList.add('open');loadPlaylists()};$('#closeDrawer').onclick=()=>$('#drawer').classList.remove('open');$('#playlists').onclick=e=>{const b=e.target.closest('[data-select]');if(b){selectedPlaylist=+b.dataset.select;$('#drawer').classList.remove('open')}};$('#newPlaylist').onsubmit=async e=>{e.preventDefault();await api({action:'create',name:$('#playlistName').value});$('#playlistName').value='';loadPlaylists()};load();",
    "async function addToPlaylist(track,title){pendingTrack=track;$('#addNote').textContent=`Add \\\"${title||'track'}\\\" to:`;$('#drawer').classList.add('open');await loadPlaylists()}$('#playlistButton').onclick=()=>{pendingTrack=0;$('#addNote').textContent='';$('#drawer').classList.add('open');loadPlaylists()};$('#closeDrawer').onclick=()=>$('#drawer').classList.remove('open');$('#playlists').onclick=async e=>{const b=e.target.closest('[data-add-to]');if(b&&pendingTrack){await api({action:'add',playlist_id:+b.dataset.addTo,track_id:pendingTrack});$('#addNote').textContent='Added to playlist.';pendingTrack=0;await loadPlaylists()}};$('#newPlaylist').onsubmit=async e=>{e.preventDefault();const d=await api({action:'create',name:$('#playlistName').value});$('#playlistName').value='';if(pendingTrack&&d.playlists?.length){await api({action:'add',playlist_id:d.playlists[0].id,track_id:pendingTrack});$('#addNote').textContent='Playlist created and track added.';pendingTrack=0}loadPlaylists()};load();"
)

# Full-library rows stay dense; secondary track actions live in a mobile-friendly sheet.
MUSIC_PAGE = MUSIC_PAGE.replace('</style>', r'''
.library-summary{display:flex;justify-content:space-between;gap:16px;padding:0 2px 14px;border-bottom:1px solid #292f34;color:var(--muted);font-weight:750;text-transform:uppercase;letter-spacing:.02em}.track{grid-template-columns:54px minmax(0,1fr) 44px;min-height:68px;padding:7px 8px;background:transparent;border:0;border-bottom:1px solid #24292e;border-radius:0}.cover{width:54px;height:54px}.track-copy{min-width:0}.track strong{font-size:16px;margin-bottom:3px}.track small{font-size:13px}.track-menu{width:40px;height:40px;padding:0;background:transparent;color:#aeb5bd;font-size:25px}.track-menu:hover{background:#22282d}.action-shade{position:fixed;z-index:30;inset:0;background:rgba(0,0,0,.62);display:none;align-items:flex-end;justify-content:center}.action-shade.open{display:flex}.action-sheet{width:min(620px,100%);max-height:86vh;overflow:auto;background:#15191d;border:1px solid #343b41;border-radius:18px 18px 0 0;padding:10px 0 18px;box-shadow:0 -20px 60px rgba(0,0,0,.45)}.sheet-handle{width:42px;height:4px;border-radius:4px;background:#596067;margin:2px auto 13px}.sheet-title{font-size:20px;font-weight:850;padding:0 22px 16px;border-bottom:1px solid #30363b}.sheet-action{width:100%;display:flex;align-items:center;gap:16px;border-radius:0;background:transparent;color:#fff;padding:14px 22px;text-align:left;font-weight:650}.sheet-action:hover{background:#252b30}.sheet-icon{width:30px;color:var(--gold);font-size:20px;text-align:center}.sheet-close{margin:12px 20px 0;width:calc(100% - 40px);background:#2a3035;color:#fff}.player{z-index:20}
@media(max-width:760px){body{padding-bottom:186px}.tracks{gap:0}.track{grid-template-columns:52px minmax(0,1fr) 42px;min-height:66px;padding:7px 2px;gap:11px}.cover{width:52px;height:52px}.track strong{font-size:16px}.actions{grid-column:auto;display:block}.library-summary{font-size:12px}.action-sheet{border-radius:16px 16px 0 0}}
</style>''')

MUSIC_PAGE = MUSIC_PAGE.replace('</body>', r'''
<div id="trackActionShade" class="action-shade" aria-hidden="true">
  <section class="action-sheet" role="dialog" aria-modal="true" aria-labelledby="trackActionTitle">
    <div class="sheet-handle"></div><div id="trackActionTitle" class="sheet-title">Track</div>
    <button class="sheet-action" data-track-action="play"><span class="sheet-icon">&#9654;</span>Play</button>
    <button class="sheet-action" data-track-action="next"><span class="sheet-icon">&#8618;</span>Play next</button>
    <button class="sheet-action" data-track-action="queue"><span class="sheet-icon">&#9776;</span>Add to queue</button>
    <button class="sheet-action" data-track-action="playlist"><span class="sheet-icon">+</span>Add to playlist</button>
    <button class="sheet-action" data-track-action="album"><span class="sheet-icon">&#9835;</span>Go to album</button>
    <button class="sheet-action" data-track-action="download"><span class="sheet-icon">&#8595;</span>Download</button>
    <button class="sheet-action" data-track-action="share"><span class="sheet-icon">&#8599;</span>Share</button>
    <button id="trackActionClose" class="sheet-close">Close</button>
  </section>
</div>
<script>
let actionTrack=null;
function libraryDuration(seconds){
  seconds=Number(seconds||0); const days=Math.floor(seconds/86400), hours=Math.floor((seconds%86400)/3600);
  return days ? `${days.toLocaleString()} days${hours?' '+hours+' hours':''}` : `${hours} hours`;
}
render=function(){
  list.innerHTML=`<div class="library-summary"><span>${tracks.length.toLocaleString()} loaded</span><span>Full library</span></div>`+
    tracks.map((t,i)=>`<article class="track" data-row-play="${i}"><img class="cover" src="${t.artwork}" loading="lazy" alt=""><div class="track-copy"><strong>${esc(t.title)}</strong><small>${esc(t.artist)} · ${esc(t.album)}${t.year?' · '+t.year:''}</small></div><button class="track-menu" data-track-menu="${i}" aria-label="Actions for ${esc(t.title)}">&#8942;</button></article>`).join('');
};
load=async function(q=''){
  const d=await fetch('/api/music/library?q='+encodeURIComponent(q)).then(r=>r.json());
  tracks=d.tracks||[]; queue=[...tracks]; render(); const x=d.stats||{};
  $('#stats').textContent=`${Number(x.tracks||0).toLocaleString()} tracks · ${Number(x.artists||0).toLocaleString()} artists · ${Number(x.albums||0).toLocaleString()} albums · ${libraryDuration(x.duration)} · ${Math.round((x.bytes||0)/1073741824)} GB`;
};
function openTrackActions(track){actionTrack=track;$('#trackActionTitle').textContent=track.title;$('#trackActionShade').classList.add('open');$('#trackActionShade').setAttribute('aria-hidden','false')}
function closeTrackActions(){actionTrack=null;$('#trackActionShade').classList.remove('open');$('#trackActionShade').setAttribute('aria-hidden','true')}
list.onclick=e=>{
  const menu=e.target.closest('[data-track-menu]'); if(menu){openTrackActions(tracks[+menu.dataset.trackMenu]);return}
  const row=e.target.closest('[data-row-play]'); if(row){queue=[...tracks];setTrack(+row.dataset.rowPlay)}
};
$('#trackActionClose').onclick=closeTrackActions;
$('#trackActionShade').onclick=e=>{if(e.target.id==='trackActionShade')closeTrackActions()};
$('#trackActionShade').querySelector('.action-sheet').onclick=async e=>{
  const button=e.target.closest('[data-track-action]'); if(!button||!actionTrack)return;
  const t=actionTrack, action=button.dataset.trackAction;
  if(action==='play'){queue=[...tracks];const i=queue.findIndex(x=>x.id===t.id);setTrack(i<0?0:i)}
  if(action==='next'){if(index<0){queue=[t];index=-1}else queue.splice(index+1,0,t)}
  if(action==='queue')queue.push(t);
  if(action==='playlist'){closeTrackActions();await addToPlaylist(t.id,t.title);return}
  if(action==='album'){$('#search').value=t.album||'';await load(t.album||'')}
  if(action==='download'){const a=document.createElement('a');a.href=t.download;a.download='';document.body.appendChild(a);a.click();a.remove()}
  if(action==='share'){const url=new URL(t.stream,location.href).href; if(navigator.share)await navigator.share({title:t.title,text:`${t.artist} - ${t.title}`,url});else await navigator.clipboard.writeText(url)}
  closeTrackActions();
};
load();
</script>
</body>''')

MUSIC_PAGE = MUSIC_PAGE.replace('</style>', r'''
.playlist{cursor:pointer}.playlist:hover{background:#202832}.playlist-open{background:transparent;color:#fff;padding:0;text-align:left;border-radius:0}.playlist-tools{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 18px}.playlist-tools button,.playlist-tools a{flex:1}.playlist-tools a button{width:100%}.playlist-name-edit{display:grid;grid-template-columns:1fr auto;gap:8px}.playlist-detail-track{display:grid;grid-template-columns:48px minmax(0,1fr) auto;gap:10px;align-items:center;padding:10px 0;border-bottom:1px solid #272e35}.playlist-detail-track img{width:48px;height:48px;object-fit:cover;border-radius:5px;background:#202735}.playlist-detail-track strong,.playlist-detail-track small{display:block}.playlist-detail-track small{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.playlist-track-actions{display:flex;gap:5px}.playlist-track-actions button{width:34px;height:34px;padding:0;border-radius:50%;background:#252c32;color:#fff}.danger{background:#612b30!important;color:#fff!important}body:not(.music-playing) .player{grid-template-columns:1fr;min-height:58px;padding:9px 18px}body:not(.music-playing) .player img,body:not(.music-playing) .player .controls,body:not(.music-playing) .player .seek,body:not(.music-playing) .player .volume{display:none}body:not(.music-playing) .player .now{text-align:center}body:not(.music-playing) .player .now strong{font-size:14px;color:var(--muted);font-weight:650}body:not(.music-playing) .player .now small{display:none}
@media(max-width:520px){.playlist-detail-track{grid-template-columns:42px minmax(0,1fr)}.playlist-detail-track img{width:42px;height:42px}.playlist-track-actions{grid-column:2}.playlist-track-actions button{width:38px;height:34px}}
</style>''')

MUSIC_PAGE = MUSIC_PAGE.replace('</body>', r'''
<script>
let openPlaylistId=0;
loadPlaylists=async function(){
  const d=await api({action:'list'}); playlists=d.playlists||[];
  $('#playlists').innerHTML=playlists.length?playlists.map(p=>`<div class="playlist" data-open-playlist="${p.id}"><button class="playlist-open"><strong>${esc(p.name)}</strong><small>${p.track_count} tracks · ${fmt(p.duration)}</small></button><div>${pendingTrack?`<button class="primary" data-add-to="${p.id}">Add here</button>`:''}<a href="/music/download-playlist/${p.id}" download><button class="ghost" aria-label="Download ${esc(p.name)}">&#8595;</button></a></div></div>`).join(''):'<p class="stats">Create your first playlist above.</p>';
};
async function openPlaylist(id){
  const d=await api({action:'details',playlist_id:id}); if(!d.ok)return; openPlaylistId=id; const p=d.playlist,t=d.tracks||[];
  $('#addNote').textContent=''; $('#newPlaylist').style.display='none';
  $('#playlists').innerHTML=`<button class="ghost" data-playlist-back>&larr; All playlists</button><h2>${esc(p.name)}</h2><div class="playlist-name-edit"><input id="renamePlaylist" class="search" value="${esc(p.name)}"><button class="ghost" data-playlist-rename>Rename</button></div><div class="playlist-tools"><button class="primary" data-playlist-play>Play all</button><button class="ghost" data-playlist-shuffle>Shuffle</button><a href="/music/download-playlist/${id}" download><button class="ghost">Download</button></a><button class="danger" data-playlist-delete>Delete</button></div><div>${t.length?t.map((x,i)=>`<div class="playlist-detail-track"><img src="${x.artwork}" loading="lazy" alt=""><div><strong>${esc(x.title)}</strong><small>${esc(x.artist)} · ${esc(x.album)}</small></div><div class="playlist-track-actions"><button data-playlist-track-play="${i}" title="Play">&#9654;</button><button data-playlist-move="up" data-track-id="${x.id}" title="Move up">&#8593;</button><button data-playlist-move="down" data-track-id="${x.id}" title="Move down">&#8595;</button><button class="danger" data-playlist-remove="${x.id}" title="Remove">&times;</button></div></div>`).join(''):'<p class="stats">This playlist is empty.</p>'}</div>`;
  $('#playlists').dataset.tracks=JSON.stringify(t);
}
$('#playlists').addEventListener('click',async e=>{
  const add=e.target.closest('[data-add-to]'); if(add)return;
  const open=e.target.closest('[data-open-playlist]'); if(open){await openPlaylist(+open.dataset.openPlaylist);return}
  if(e.target.closest('[data-playlist-back]')){$('#newPlaylist').style.display='';openPlaylistId=0;await loadPlaylists();return}
  if(e.target.closest('[data-playlist-rename]')){await api({action:'rename',playlist_id:openPlaylistId,name:$('#renamePlaylist').value});await openPlaylist(openPlaylistId);return}
  if(e.target.closest('[data-playlist-delete]')){if(confirm('Delete this playlist?')){await api({action:'delete',playlist_id:openPlaylistId});$('#newPlaylist').style.display='';openPlaylistId=0;await loadPlaylists()}return}
  const all=JSON.parse($('#playlists').dataset.tracks||'[]');
  if(e.target.closest('[data-playlist-play]')){queue=all;setTrack(0);return}
  if(e.target.closest('[data-playlist-shuffle]')){queue=[...all].sort(()=>Math.random()-.5);setTrack(0);return}
  const play=e.target.closest('[data-playlist-track-play]');if(play){queue=all;setTrack(+play.dataset.playlistTrackPlay);return}
  const move=e.target.closest('[data-playlist-move]');if(move){await api({action:'move',playlist_id:openPlaylistId,track_id:+move.dataset.trackId,direction:move.dataset.playlistMove});await openPlaylist(openPlaylistId);return}
  const remove=e.target.closest('[data-playlist-remove]');if(remove){await api({action:'remove',playlist_id:openPlaylistId,track_id:+remove.dataset.playlistRemove});await openPlaylist(openPlaylistId)}
});
const cmvSetTrack=setTrack;
setTrack=function(i,autoplay=true){document.body.classList.add('music-playing');return cmvSetTrack(i,autoplay)};
</script>
</body>''')


def handle_get(handler, user, path, head=False):
    if path == "/music":
        data=MUSIC_PAGE.encode(); handler.send_response(200); handler.send_header("Content-Type","text/html; charset=utf-8"); handler.send_header("Content-Length",str(len(data))); handler.end_headers(); return handler.wfile.write(data)
    if path == "/api/music/library": return api_library(handler)
    if path == "/api/music/scan-status": return json_response(handler,{"ok":True,"scan":dict(SCAN_STATE)})
    match=re.match(r"^/music/(stream|download|art)/(\d+)$",path)
    if match:
        action,track_id=match.groups(); row,file=safe_track(track_id)
        if not row: return handler.send_error(404)
        if action=="art":
            art=artwork_path(track_id)
            if not art: return handler.send_error(404)
            return serve_file(handler,art,head=head)
        return serve_file(handler,file,file.name if action=="download" else "",head)
    match=re.match(r"^/music/download-playlist/(\d+)$",path)
    if match:return download_playlist(handler,int(match.group(1)),int(user["id"]))
    return False


def handle_post(handler, user, path):
    if path == "/api/music/scan": start_scan(); return json_response(handler,{"ok":True,"scan":dict(SCAN_STATE)})
    if path == "/api/music/playlists": return playlist_api(handler,user)
    return False


initialize()
if os.environ.get("CINEMEDIAVAULT_MUSIC_AUTO_SCAN", "0").strip().lower() in {"1", "true", "yes", "on"}:
    threading.Thread(target=lambda:(time.sleep(3),start_scan()),daemon=True,name="music-scan-start").start()
