"""Read-only helpers for CineVault's sidecar TMDB people cache."""
from __future__ import annotations

import html
import sqlite3
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "cinevault-data" / "tmdb-people.sqlite3"
IMAGE_ROOT = (ROOT / "cinevault-data" / "tmdb-people").resolve()


def _connect():
    if not DB.is_file():
        return None
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=.25)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=250")
    return conn


def credits(media_type: str, media_id: int, fallback_names=(), limit: int = 12, tmdb_media_id: int | None = None) -> list[dict]:
    conn = _connect()
    if not conn:
        return [{"name": str(name), "character_name": "", "biography": "", "person_id": 0, "has_image": False} for name in fallback_names[:limit]]
    try:
        # The web library and SQLite catalog maintain independent local IDs.
        # Prefer TMDB's stable ID whenever it is available; otherwise an
        # unrelated title with the same numeric local ID can supply its cast.
        if tmdb_media_id:
            rows = conn.execute(
                """SELECT p.person_id,p.name,p.biography,p.birthday,p.place_of_birth,p.known_for_department,
                          p.profile_local_path,c.character_name,c.credit_order
                   FROM tmdb_media_credits c JOIN tmdb_people p ON p.person_id=c.person_id
                   WHERE c.media_type=? AND c.tmdb_media_id=? ORDER BY c.credit_order,p.name LIMIT ?""",
                (media_type, int(tmdb_media_id), int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT p.person_id,p.name,p.biography,p.birthday,p.place_of_birth,p.known_for_department,
                          p.profile_local_path,c.character_name,c.credit_order
                   FROM tmdb_media_credits c JOIN tmdb_people p ON p.person_id=c.person_id
                   WHERE c.media_type=? AND c.media_id=? ORDER BY c.credit_order,p.name LIMIT ?""",
                (media_type, int(media_id), int(limit)),
            ).fetchall()
        if not rows and fallback_names:
            marks = ",".join("?" for _ in fallback_names[:limit])
            rows = conn.execute(
                f"""SELECT person_id,name,biography,birthday,place_of_birth,known_for_department,
                           profile_local_path,'' character_name,9999 credit_order
                    FROM tmdb_people WHERE name IN ({marks}) ORDER BY name LIMIT ?""",
                (*[str(name) for name in fallback_names[:limit]], int(limit)),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            path = Path(item.get("profile_local_path") or "")
            item["has_image"] = bool(path.is_file() and IMAGE_ROOT in path.resolve().parents)
            result.append(item)
        if result:
            return result
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return [{"name": str(name), "character_name": "", "biography": "", "person_id": 0, "has_image": False} for name in fallback_names[:limit]]


def cards_html(media_type: str, media_id: int, fallback_names=(), limit: int = 12, tmdb_media_id: int | None = None) -> str:
    people = credits(media_type, media_id, fallback_names, limit, tmdb_media_id)
    if not people:
        return "<li class='cast-empty'>No actor data available yet.</li>"
    cards = []
    for person in people:
        name = str(person.get("name") or "Unknown")
        href = "/actor?name=" + urllib.parse.quote(name)
        portrait = (
            f"<img loading='lazy' src='/tmdb-person-image/{int(person['person_id'])}' alt=''>"
            if person.get("has_image") and person.get("person_id") else
            "<span class='cast-portrait-placeholder' aria-hidden='true'>&#128100;</span>"
        )
        character = str(person.get("character_name") or "")
        detail = f"<small>{html.escape(character)}</small>" if character else ""
        cards.append(
            f"<li class='cast-person-card'><a href='{href}'>{portrait}<span><strong>{html.escape(name)}</strong>{detail}</span></a></li>"
        )
    return "".join(cards)


def person_for_image(person_id: int) -> Path | None:
    conn = _connect()
    if not conn:
        return None
    try:
        row = conn.execute("SELECT profile_local_path FROM tmdb_people WHERE person_id=?", (int(person_id),)).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    path = Path(row[0]).resolve()
    return path if path.is_file() and IMAGE_ROOT in path.parents else None


def handle_get(handler, path: str):
    prefix = "/tmdb-person-image/"
    if not path.startswith(prefix):
        return False
    try:
        person_id = int(path[len(prefix):].split("/", 1)[0])
    except ValueError:
        return handler.send_error(404)
    image = person_for_image(person_id)
    if not image:
        return handler.send_error(404)
    data = image.read_bytes()
    suffix = image.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "public, max-age=604800")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(data)
    return True
