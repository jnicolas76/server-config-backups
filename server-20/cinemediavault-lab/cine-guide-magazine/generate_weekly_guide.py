#!/usr/bin/env python3
"""Generate the static CineMedia Vault weekly PDF guide from the live SQLite catalog."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import random
import shutil
import sqlite3
import subprocess
import tempfile
import unicodedata
import json
import re
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageOps
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import LETTER, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

INK = colors.HexColor("#101522")
NAVY = colors.HexColor("#07162f")
BLUE = colors.HexColor("#3979f6")
CYAN = colors.HexColor("#76c8ff")
GOLD = colors.HexColor("#f1bd55")
PAPER = colors.HexColor("#f5f0e6")
WHITE = colors.white
MUTED = colors.HexColor("#596273")
GRID = colors.HexColor("#d6d9df")


def monday_for(value: dt.date) -> dt.date:
    return value - dt.timedelta(days=value.weekday())


def clean(value, fallback="") -> str:
    return " ".join(str(value or fallback).replace("\n", " ").split())


def shorten(text: str, maximum: int) -> str:
    text = clean(text)
    return text if len(text) <= maximum else text[: maximum - 1].rstrip() + "..."


def fit_text(c: Canvas, text: str, max_width: float, start: float, minimum: float = 5.0) -> float:
    size = start
    while size > minimum and stringWidth(text, "Helvetica-Bold", size) > max_width:
        size -= 0.25
    return size


def resolve_art(base: Path, raw: str | None) -> Path | None:
    if not raw:
        return None
    path = Path(raw)
    candidates = [
        path, base / path,
        base / "media-download-library" / path,
        base / "media-download-library" / "posters" / path.name,
        base / "tv-download-library" / path,
        base / "tv-download-library" / "posters" / path.name,
    ]
    return next((p for p in candidates if p.is_file()), None)


def draw_crop(c: Canvas, path: Path | None, x: float, y: float, w: float, h: float, darken: float = 0) -> None:
    if not path or not path.is_file():
        c.setFillColor(colors.HexColor("#25314a")); c.rect(x, y, w, h, fill=1, stroke=0)
        return
    try:
        with Image.open(path) as source:
            rgb = source.convert("RGB")
            fitted = ImageOps.fit(rgb, (max(2, int(w * 2)), max(2, int(h * 2))), method=Image.Resampling.LANCZOS)
            c.drawImage(ImageReader(fitted), x, y, w, h, preserveAspectRatio=False, mask="auto")
        if darken:
            c.saveState(); c.setFillColor(colors.Color(0, 0, 0, alpha=darken)); c.rect(x, y, w, h, fill=1, stroke=0); c.restoreState()
    except Exception:
        c.setFillColor(colors.HexColor("#25314a")); c.rect(x, y, w, h, fill=1, stroke=0)


class Catalog:
    def __init__(self, db: Path, base: Path):
        self.db, self.base = db, base
        self.conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        people_db = base / "cinevault-data" / "tmdb-people.sqlite3"
        self.people_conn = sqlite3.connect(f"file:{people_db}?mode=ro", uri=True) if people_db.is_file() else None
        if self.people_conn:
            self.people_conn.row_factory = sqlite3.Row

    def schedule(self, start: int, stop: int):
        return self.conn.execute(
            """SELECT s.*,d.channel_number,d.name AS channel_name,d.kind AS channel_kind,d.sort_order
               FROM vchannel_schedule s JOIN vchannel_defs d ON d.id=s.channel_id
               WHERE s.stop_ts>? AND s.start_ts<? ORDER BY d.kind,d.sort_order,s.start_ts""", (start, stop)
        ).fetchall()

    def movie_details(self, title: str, year: str = "") -> dict:
        row = self.conn.execute(
            """SELECT mi.id,mi.title,mi.year,mi.file_path,m.overview,m.vote_average,m.release_date,
                      (SELECT path FROM artwork WHERE media_type='movie' AND media_id=mi.id AND art_type='poster' ORDER BY is_primary DESC,id LIMIT 1) poster
               FROM media_items mi JOIN movies m ON m.media_item_id=mi.id
               WHERE lower(mi.title)=lower(?) ORDER BY CASE WHEN mi.year=? THEN 0 ELSE 1 END,m.vote_average DESC LIMIT 1""", (title, str(year or ""))
        ).fetchone()
        return dict(row) if row else {}

    def show_details(self, title: str) -> dict:
        row = self.conn.execute(
            """SELECT t.id,t.title,t.year,t.overview,t.vote_average,
                      (SELECT path FROM artwork WHERE media_type='tv_show' AND media_id=t.id AND art_type='poster' ORDER BY is_primary DESC,id LIMIT 1) poster
               FROM tv_shows t WHERE lower(t.title)=lower(?) ORDER BY t.vote_average DESC LIMIT 1""", (title,)
        ).fetchone()
        return dict(row) if row else {}

    def cast(self, media_type: str, media_id: int, limit: int = 3) -> list[str]:
        rows = self.conn.execute(
            """SELECT p.name FROM media_people mp JOIN people p ON p.id=mp.person_id
               WHERE mp.media_type=? AND mp.media_id=? AND mp.role='actor' ORDER BY mp.ordering LIMIT ?""",
            (media_type, media_id, limit),
        ).fetchall()
        return [r[0] for r in rows]

    def cast_profiles(self, media_type: str, media_id: int, limit: int = 3) -> list[dict]:
        if not self.people_conn:
            return []
        try:
            rows = self.people_conn.execute(
                """SELECT p.person_id,p.name,p.biography,p.birthday,p.place_of_birth,p.known_for_department,
                          p.profile_local_path,c.character_name,c.credit_order
                   FROM tmdb_media_credits c JOIN tmdb_people p ON p.person_id=c.person_id
                   WHERE c.media_type=? AND c.media_id=? ORDER BY c.credit_order,p.name LIMIT ?""",
                (media_type, media_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            return []

    def story_portrait(self, story: dict) -> Path | None:
        """Use an already-cached actor portrait when a story names that person."""
        if not self.people_conn:
            return None
        haystack = clean(f"{story.get('title', '')} {story.get('summary', '')}").casefold()
        try:
            rows = self.people_conn.execute(
                """SELECT name,profile_local_path FROM tmdb_people
                   WHERE profile_local_path<>'' ORDER BY popularity DESC LIMIT 2500"""
            ).fetchall()
        except sqlite3.Error:
            return None
        for row in rows:
            name = clean(row["name"])
            if len(name) >= 5 and re.search(rf"\b{re.escape(name.casefold())}\b", haystack):
                path = Path(row["profile_local_path"] or "")
                if path.is_file():
                    return path
        return None

    def other_credits(self, names: list[str], exclude: str, limit: int = 5) -> list[str]:
        if not names:
            return []
        marks = ",".join("?" for _ in names)
        rows = self.conn.execute(
            f"""SELECT DISTINCT am.title FROM actor_media am JOIN actors a ON a.id=am.actor_id
                 WHERE a.name IN ({marks}) AND lower(am.title)<>lower(?) ORDER BY am.title LIMIT ?""", (*names, exclude, limit)
        ).fetchall()
        return [r[0] for r in rows]

    def genres(self, media_id: int) -> list[str]:
        rows = self.conn.execute(
            """SELECT g.name FROM media_genres mg JOIN genres g ON g.id=mg.genre_id
               WHERE mg.media_type='movie' AND mg.media_id=? ORDER BY g.name LIMIT 4""", (media_id,)
        ).fetchall()
        return [r[0] for r in rows]

    def episode_source(self, show: str, subtitle: str) -> str | None:
        row = self.conn.execute(
            """SELECT e.file_path FROM tv_episodes e JOIN tv_shows t ON t.id=e.show_id
               WHERE lower(t.title)=lower(?) AND lower(COALESCE(e.episode_title,e.title))=lower(?) LIMIT 1""", (show, subtitle)
        ).fetchone()
        return row[0] if row else None

    def recent_on_demand(self, limit: int = 6) -> list[dict]:
        rows = self.conn.execute(
            """SELECT 'movie' kind,mi.id,mi.title,mi.year,m.overview,m.vote_average,mi.created_at,
                      (SELECT path FROM artwork WHERE media_type='movie' AND media_id=mi.id AND art_type='poster' ORDER BY is_primary DESC,id LIMIT 1) poster
               FROM media_items mi JOIN movies m ON m.media_item_id=mi.id
               UNION ALL
               SELECT 'episode' kind,t.id,t.title,t.year,t.overview,t.vote_average,t.created_at,
                      (SELECT path FROM artwork WHERE media_type='tv_show' AND media_id=t.id AND art_type='poster' ORDER BY is_primary DESC,id LIMIT 1) poster
               FROM tv_shows t ORDER BY created_at DESC LIMIT ?""", (limit,)
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.update(channel="ON DEMAND", start_ts=int(dt.datetime.now().timestamp()), score=float(item.get("vote_average") or 0))
            item["poster_path"] = resolve_art(self.base, item.get("poster"))
            result.append(item)
        return result


def feature_candidates(cat: Catalog, rows, rng: random.Random) -> list[dict]:
    unique, items = set(), []
    for row in rows:
        key = (row["media_kind"], row["title"])
        if key in unique:
            continue
        unique.add(key)
        detail = cat.movie_details(row["title"]) if row["media_kind"] == "movie" else cat.show_details(row["title"])
        if not detail:
            continue
        detail.update(kind=row["media_kind"], schedule_title=row["title"], subtitle=row["subtitle"] or "", start_ts=row["start_ts"], channel=row["channel_number"])
        detail["poster_path"] = resolve_art(cat.base, detail.get("poster"))
        score = float(detail.get("vote_average") or row["rating"] or 0)
        detail["score"] = score
        items.append(detail)
    top = sorted(items, key=lambda item: (item["score"], rng.random()), reverse=True)[:24]
    rng.shuffle(top)
    return top


def extract_stills(feature: dict, out_dir: Path) -> list[Path]:
    source = Path(feature.get("file_path") or "")
    if not source.is_file() or not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return []
    try:
        duration = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(source)], text=True, timeout=30).strip())
    except Exception:
        return []
    results = []
    for index, fraction in enumerate((0.27, 0.52, 0.73), 1):
        target = out_dir / f"still-{index}.jpg"
        try:
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(max(300, duration * fraction)), "-i", str(source), "-frames:v", "1", "-vf", "scale=1280:-2", "-y", str(target)], check=True, timeout=90)
            if target.is_file(): results.append(target)
        except Exception:
            pass
    return results


def footer(c: Canvas, page: int, issue: str, width: float) -> None:
    c.setStrokeColor(GOLD); c.line(28, 22, width - 28, 22)
    c.setFont("Helvetica", 7); c.setFillColor(MUTED)
    c.drawString(28, 10, f"CineMedia Vault Guide  |  {issue}")
    c.drawRightString(width - 28, 10, str(page))


def para(c: Canvas, text: str, x: float, y: float, w: float, h: float, size=9, color=INK, leading=None, bold=False) -> None:
    style = ParagraphStyle("p", fontName="Helvetica-Bold" if bold else "Helvetica", fontSize=size, leading=leading or size * 1.25, textColor=color, alignment=TA_LEFT)
    p = Paragraph(clean(text), style); _, ph = p.wrap(w, h); p.drawOn(c, x, y + h - ph)


def cover(c: Canvas, feature: dict, issue: str, masthead: Path | None = None) -> None:
    c.setPageSize(LETTER); w, h = LETTER
    draw_crop(c, feature.get("poster_path"), 0, 0, w, h, .28)
    c.setFillColor(colors.Color(0.02, .05, .11, alpha=.45)); c.rect(0, 0, w, h, fill=1, stroke=0)
    if masthead and masthead.is_file():
        # The generated asset has a real alpha channel: float it directly over
        # the cover art instead of hiding the poster under a rectangular band.
        c.drawImage(str(masthead), 8, h - 154, w - 16, 145, preserveAspectRatio=True, anchor='c', mask='auto')
    else:
        c.setFillColor(GOLD); c.setFont("Times-BoldItalic", 34); c.drawString(30, h - 54, "CineMedia")
        c.setFillColor(CYAN); c.setFont("Helvetica-BoldOblique", 48); c.drawString(28, h - 108, "VAULT GUIDE")
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 34)
    title = clean(feature.get("title") or feature.get("schedule_title") or "This Week")
    size = fit_text(c, title, w - 60, 34, 19); c.setFont("Helvetica-Bold", size); c.drawString(30, 92, title)
    cast = feature.get("cast") or []
    c.setFont("Helvetica-Bold", 12); c.setFillColor(GOLD); c.drawString(30, 67, "STARRING " + "  •  ".join(cast[:3]).upper())
    c.setFont("Helvetica", 10); c.setFillColor(WHITE); c.drawString(30, 44, issue.upper() + "  |  YOUR WEEK OF MOVIES & TELEVISION")
    c.showPage()


def contents_page(c: Canvas, picks: list[dict], issue: str, page: int) -> None:
    c.setPageSize(LETTER); w, h = LETTER
    c.setFillColor(PAPER); c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#e94057")); c.rect(0, h - 132, w, 132, fill=1, stroke=0)
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 38); c.drawString(30, h - 72, "CONTENTS")
    c.setFont("Helvetica-Bold", 11); c.drawString(32, h - 100, "YOUR WEEK IN MOVIES, TELEVISION, PEOPLE AND ENTERTAINMENT")
    featured = [item for item in picks if item.get("poster_path")][:3]
    for index, item in enumerate(featured):
        x = 30 + index * 134
        draw_crop(c, item.get("poster_path"), x, 390, 118, 178)
        c.setFillColor(colors.Color(0, 0, 0, alpha=.74)); c.rect(x, 390, 118, 30, fill=1, stroke=0)
        c.setFillColor(WHITE); c.setFont("Helvetica-Bold", fit_text(c, item.get("title") or "", 108, 9, 6))
        c.drawCentredString(x + 59, 401, clean(item.get("title") or ""))
    sections = [
        ("03", "COVER STORY", "The featured title, cast and scenes behind this week's cover."),
        ("05", "WHAT TO WATCH", "Editors' picks and newly available on-demand highlights."),
        ("09", "MOVIES THIS WEEK", "Seven days of movie listings in aligned twelve-hour grids."),
        ("27", "TELEVISION THIS WEEK", "Series and episode listings from Monday through Sunday."),
        ("THROUGHOUT", "ENTERTAINMENT & NEWS", "Photo-led reports, longer story digests and industry headlines."),
    ]
    sx, sy = 432, 568
    for number, title, description in sections:
        c.setFillColor(BLUE); c.setFont("Helvetica-Bold", 10); c.drawString(sx, sy, number)
        c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 13); c.drawString(sx, sy - 19, title)
        para(c, description, sx, sy - 59, 150, 36, 7.3, INK, 9)
        sy -= 72
    c.setFillColor(colors.HexColor("#142b50")); c.roundRect(30, 58, w - 60, 154, 12, fill=1, stroke=0)
    c.setFillColor(GOLD); c.setFont("Helvetica-Bold", 10); c.drawString(50, 184, "INSIDE THIS ISSUE")
    inside_title = "More stories. More pictures. Your schedule."
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", fit_text(c, inside_title, w - 100, 22, 15)); c.drawString(50, 151, inside_title)
    para(c, "Current Monday-through-Sunday schedules meet cast profiles, entertainment reporting, news digests, on-demand picks and visual previews selected from your library.", 50, 78, w - 100, 52, 9.5, WHITE, 12)
    footer(c, page, issue, w); c.showPage()


def editorial(c: Canvas, feature: dict, stills: list[Path], also_features: list[dict], issue: str, page: int) -> None:
    c.setPageSize(LETTER); w, h = LETTER; c.setFillColor(PAPER); c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(NAVY); c.rect(0, h - 82, w, 82, fill=1, stroke=0)
    c.setFillColor(GOLD); c.setFont("Helvetica-Bold", 10); c.drawString(28, h - 27, "THIS WEEK'S COVER STORY")
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", fit_text(c, feature["title"], w - 56, 27, 16)); c.drawString(28, h - 60, feature["title"])
    draw_crop(c, feature.get("poster_path"), 28, h - 360, 176, 255)
    overview = feature.get("overview") or "A featured selection from this week's CineMedia Vault schedule."
    para(c, overview, 224, h - 360, 350, 230, 11, INK, 15)
    meta = " • ".join(filter(None, [str(feature.get("year") or ""), ", ".join(feature.get("genres") or []), f"★ {feature.get('score',0):.1f}"]))
    c.setFillColor(BLUE); c.setFont("Helvetica-Bold", 10); c.drawString(224, h - 118, meta)
    c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 13); c.drawString(28, 392, "ALSO FEATURING")
    sx, sw = 28, (w - 72) / 3
    for idx in range(3):
        item = also_features[idx] if idx < len(also_features) else None
        image = item.get("poster_path") if item else (stills[idx] if idx < len(stills) else None)
        x = sx + idx * (sw + 8)
        draw_crop(c, image, x, 145, sw, 205)
        if item:
            c.setFillColor(colors.Color(0, 0, 0, alpha=.72)); c.rect(x, 145, sw, 34, fill=1, stroke=0)
            c.setFillColor(WHITE); c.setFont("Helvetica-Bold", fit_text(c, item["title"], sw - 10, 10, 6.5)); c.drawCentredString(x + sw / 2, 158, item["title"])
    footer(c, page, issue, w); c.showPage()


def cast_spotlight(c: Canvas, feature: dict, issue: str, page: int) -> None:
    c.setPageSize(LETTER); w, h = LETTER; c.setFillColor(PAPER); c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(NAVY); c.rect(0, h - 82, w, 82, fill=1, stroke=0)
    c.setFillColor(GOLD); c.setFont("Helvetica-Bold", 10); c.drawString(28, h - 27, "CAST SPOTLIGHT")
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 27); c.drawString(28, h - 61, f"Meet the cast of {shorten(feature['title'], 34)}")
    profiles = feature.get("cast_profiles") or []
    if not profiles:
        c.setFillColor(INK); c.setFont("Helvetica", 12); c.drawString(28, h - 125, "Cast profiles are being enriched and will appear in the next issue.")
    for index, person in enumerate(profiles[:4]):
        y = h - 258 - index * 164
        portrait = Path(person.get("profile_local_path") or "")
        draw_crop(c, portrait if portrait.is_file() else None, 28, y, 94, 132)
        name = clean(person.get("name") or "Unknown")
        c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 17); c.drawString(140, y + 105, name)
        role = clean(person.get("character_name") or person.get("known_for_department") or "Cast")
        c.setFillColor(BLUE); c.setFont("Helvetica-Bold", 9); c.drawString(140, y + 87, role)
        facts = " • ".join(filter(None, [clean(person.get("birthday")), clean(person.get("place_of_birth"))]))
        if facts:
            c.setFillColor(MUTED); c.setFont("Helvetica", 7.5); c.drawString(140, y + 71, shorten(facts, 85))
        bio = person.get("biography") or f"{name} appears in this week's featured CineMedia Vault selection."
        bio = unicodedata.normalize("NFKD", clean(bio)).encode("ascii", "ignore").decode("ascii")
        para(c, shorten(bio, 620), 140, y, w - 170, 65, 8.3, INK, 10)
    footer(c, page, issue, w); c.showPage()


def picks_page(c: Canvas, picks: list[dict], issue: str, page: int, heading: str = "WHAT'S ON THIS WEEK") -> None:
    c.setPageSize(LETTER); w, h = LETTER; c.setFillColor(PAPER); c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(NAVY); c.rect(0, h - 72, w, 72, fill=1, stroke=0)
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 29); c.drawString(28, h - 47, heading)
    cards = picks[:6]
    for i, item in enumerate(cards):
        col, row = i % 2, i // 2; x = 28 + col * 282; y = h - 292 - row * 218
        draw_crop(c, item.get("poster_path"), x, y, 82, 154)
        c.setFillColor(NAVY); c.setFont("Helvetica-Bold", fit_text(c, item["title"], 182, 13, 8)); c.drawString(x + 94, y + 132, item["title"])
        when = "Available now" if item["channel"] == "ON DEMAND" else dt.datetime.fromtimestamp(item["start_ts"]).strftime("%a %I:%M %p").replace(" 0", " ")
        c.setFillColor(BLUE); c.setFont("Helvetica-Bold", 8); c.drawString(x + 94, y + 114, f"{item['channel']}  •  {when}")
        para(c, shorten(item.get("overview") or "Featured this week in CineMedia Vault.", 330), x + 94, y + 10, 176, 94, 7.5, INK, 9)
    footer(c, page, issue, w); c.showPage()


def section_page(c: Canvas, kind: str, picks: list[dict], issue: str, page: int) -> None:
    """A strong visual divider so movie and television listings never interleave."""
    c.setPageSize(LETTER); w, h = LETTER
    c.setFillColor(NAVY); c.rect(0, 0, w, h, fill=1, stroke=0)
    label = "MOVIES THIS WEEK" if kind == "movie" else "TELEVISION THIS WEEK"
    strap = "Seven days of films across CineMedia Vault" if kind == "movie" else "Seven days of series and episodes across CineMedia Vault"
    featured = [item for item in picks if item.get("kind") == kind and item.get("poster_path")][:3]
    card_w = 150
    start_x = (w - (card_w * 3 + 24)) / 2
    for index, item in enumerate(featured):
        x = start_x + index * (card_w + 12)
        draw_crop(c, item.get("poster_path"), x, 250, card_w, 278)
        c.setFillColor(colors.Color(0, 0, 0, alpha=.76)); c.rect(x, 250, card_w, 38, fill=1, stroke=0)
        c.setFillColor(WHITE); c.setFont("Helvetica-Bold", fit_text(c, item.get("title") or "", card_w - 12, 10, 6.5))
        c.drawCentredString(x + card_w / 2, 264, clean(item.get("title") or ""))
    c.setFillColor(GOLD); c.setFont("Helvetica-Bold", 11); c.drawCentredString(w / 2, 690, "CINEMEDIA VAULT GUIDE")
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", fit_text(c, label, w - 56, 38, 24)); c.drawCentredString(w / 2, 635, label)
    c.setFillColor(CYAN); c.setFont("Helvetica", 13); c.drawCentredString(w / 2, 604, strap)
    footer(c, page, issue, w); c.showPage()


def news_page(c: Canvas, stories: list[dict], issue: str, page: int, heading: str) -> None:
    c.setPageSize(LETTER); w, h = LETTER; c.setFillColor(PAPER); c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(NAVY); c.rect(0, h - 72, w, 72, fill=1, stroke=0)
    c.setFillColor(GOLD); c.setFont("Helvetica-Bold", 10); c.drawString(28, h - 25, "CINE NEWS WEEKLY")
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 27); c.drawString(28, h - 54, heading)
    for index, story in enumerate(stories[:4]):
        col, row = index % 2, index // 2; x = 28 + col * 282; y = h - 335 - row * 276
        c.setFillColor(colors.HexColor("#e3ebf8")); c.roundRect(x, y, 264, 246, 8, fill=1, stroke=0)
        c.setFillColor(BLUE); c.setFont("Helvetica-Bold", 8); c.drawString(x + 14, y + 218, clean(story.get("source") or "CINE NEWS").upper())
        title = clean(story.get("title") or "News update")
        para(c, title, x + 14, y + 156, 236, 54, 12, NAVY, 14, True)
        summary = unicodedata.normalize("NFKD", clean(story.get("summary"))).encode("ascii", "ignore").decode("ascii")
        para(c, shorten(summary, 600), x + 14, y + 18, 236, 128, 8.5, INK, 11)
    footer(c, page, issue, w); c.showPage()


def news_articles_page(c: Canvas, stories: list[dict], issue: str, page: int, heading: str = "NEWS FEATURES") -> None:
    """Colorful magazine page using complete attributed RSS summaries."""
    c.setPageSize(LETTER); w, h = LETTER; c.setFillColor(PAPER); c.rect(0, 0, w, h, fill=1, stroke=0)
    entertainment = "ENTERTAINMENT" in heading
    accent = colors.HexColor("#e83f6f") if entertainment else colors.HexColor("#2267d8")
    secondary = colors.HexColor("#ffb703") if entertainment else colors.HexColor("#4cc9a7")
    c.setFillColor(accent); c.rect(0, h - 92, w, 92, fill=1, stroke=0)
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 10); c.drawString(28, h - 27, "CINEMEDIA VAULT WEEKLY")
    c.setFont("Helvetica-Bold", 30); c.drawString(28, h - 65, heading)
    if stories:
        story = stories[0]
        x, y, sw, sh = 28, 408, w - 56, 264
        c.setFillColor(WHITE); c.roundRect(x, y, sw, sh, 10, fill=1, stroke=0)
        image_path = story.get("image_path")
        text_x = x + 20
        text_w = sw - 40
        if image_path and Path(image_path).is_file():
            draw_crop(c, Path(image_path), x, y, 192, sh)
            c.setFillColor(accent); c.rect(x + 192, y, 5, sh, fill=1, stroke=0)
            text_x, text_w = x + 216, sw - 236
        c.setFillColor(accent); c.setFont("Helvetica-Bold", 8); c.drawString(text_x, y + sh - 25, clean(story.get("source") or "CINE NEWS").upper())
        title = unicodedata.normalize("NFKD", clean(story.get("title") or "News update")).encode("ascii", "ignore").decode("ascii")
        para(c, title, text_x, y + sh - 112, text_w, 74, 17, NAVY, 20, True)
        summary = unicodedata.normalize("NFKD", clean(story.get("summary"))).encode("ascii", "ignore").decode("ascii")
        para(c, summary or "This developing story will be updated in the next edition.", text_x, y + 22, text_w, sh - 142, 9.3, INK, 12)
    slots = [(28, 55, (w - 70) / 2, 322), (35 + (w - 70) / 2, 55, (w - 70) / 2, 322)]
    card_colors = [colors.HexColor("#fff0f5") if entertainment else colors.HexColor("#eaf2ff"), colors.HexColor("#fff4cf") if entertainment else colors.HexColor("#e6faf4")]
    for index, story in enumerate(stories[1:3]):
        x, y, sw, sh = slots[index]
        c.setFillColor(card_colors[index]); c.roundRect(x, y, sw, sh, 10, fill=1, stroke=0)
        c.setFillColor(accent if index == 0 else secondary); c.rect(x, y + sh - 9, sw, 9, fill=1, stroke=0)
        c.setFillColor(accent); c.setFont("Helvetica-Bold", 8); c.drawString(x + 15, y + sh - 32, clean(story.get("source") or "CINE NEWS").upper())
        title = unicodedata.normalize("NFKD", clean(story.get("title") or "News update")).encode("ascii", "ignore").decode("ascii")
        para(c, title, x + 15, y + sh - 125, sw - 30, 78, 13, NAVY, 15, True)
        summary = unicodedata.normalize("NFKD", clean(story.get("summary"))).encode("ascii", "ignore").decode("ascii")
        para(c, summary or "This developing story will be updated in the next edition.", x + 15, y + 22, sw - 30, sh - 155, 8.6, INK, 11)
    footer(c, page, issue, w); c.showPage()


def sports_page(c: Canvas, games: list[dict], issue: str, page: int, heading: str = "SCORES & SPORTS") -> None:
    c.setPageSize(LETTER); w, h = LETTER; c.setFillColor(PAPER); c.rect(0, 0, w, h, fill=1, stroke=0)
    c.setFillColor(NAVY); c.rect(0, h - 72, w, 72, fill=1, stroke=0)
    c.setFillColor(GOLD); c.setFont("Helvetica-Bold", 10); c.drawString(28, h - 25, "CINE NEWS SPORTS DESK")
    c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 27); c.drawString(28, h - 54, heading)
    for index, game in enumerate(games[:8]):
        y = h - 126 - index * 76
        c.setFillColor(WHITE if index % 2 == 0 else colors.HexColor("#e7edf7")); c.roundRect(28, y - 43, w - 56, 64, 5, fill=1, stroke=0)
        league = clean(game.get("league") or "SPORTS")
        home, away = clean(game.get("home")), clean(game.get("away"))
        c.setFillColor(BLUE); c.setFont("Helvetica-Bold", 8); c.drawString(40, y - 3, league)
        c.setFillColor(NAVY); c.setFont("Helvetica-Bold", fit_text(c, home, 150, 11, 6.0)); c.drawRightString(246, y - 4, home)
        c.setFont("Helvetica-Bold", 15); c.drawCentredString(306, y - 5, f"{game.get('hs','-')} - {game.get('as','-')}")
        c.setFont("Helvetica-Bold", fit_text(c, away, 150, 11, 6.0)); c.drawString(366, y - 4, away)
        if game.get("date"):
            c.setFillColor(MUTED); c.setFont("Helvetica", 7); c.drawRightString(w - 40, y - 25, clean(game["date"]))
    footer(c, page, issue, w); c.showPage()


def load_news_data(path: Path) -> tuple[list[dict], list[dict]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    stories = []
    for category in ("top", "local"):
        for source in data.get("news", {}).get(category, []):
            for item in source.get("items", [])[:5]:
                stories.append({"source": source.get("source"), **item})
    entertainment = []
    for source in data.get("entertainment", []):
        if not source.get("reachable", True):
            continue
        for item in source.get("items", [])[:5]:
            # Magazine use is deliberately limited to attributed RSS metadata.
            # It does not scrape or reproduce the linked article body.
            entertainment.append({"source": source.get("source"), **item})
    return stories, entertainment


def daily_grid(c: Canvas, cat: Catalog, day: dt.date, rows, kind: str, half: int, used_heroes: set[str], issue: str, page: int) -> None:
    size = landscape(LETTER); c.setPageSize(size); w, h = size
    c.setFillColor(PAPER); c.rect(0, 0, w, h, fill=1, stroke=0)
    start_hour = 0 if half == 0 else 12
    start = dt.datetime.combine(day, dt.time(start_hour), tzinfo=dt.datetime.now().astimezone().tzinfo)
    stop = start + dt.timedelta(hours=12)
    candidates = []
    seen = set()
    for row in rows:
        if row["channel_kind"] != kind or row["stop_ts"] <= start.timestamp() or row["start_ts"] >= stop.timestamp():
            continue
        key = clean(row["title"]).casefold()
        if key in seen:
            continue
        seen.add(key)
        detail = cat.movie_details(row["title"]) if row["media_kind"] == "movie" else cat.show_details(row["title"])
        if not detail:
            continue
        detail.update(kind=row["media_kind"], start_ts=row["start_ts"], channel=row["channel_number"])
        detail["poster_path"] = resolve_art(cat.base, detail.get("poster"))
        detail["score"] = float(detail.get("vote_average") or row["rating"] or 0)
        candidates.append(detail)
    candidates.sort(key=lambda item: (item["title"].casefold() in used_heroes, -(int(item.get("year") or 0) if str(item.get("year") or "").isdigit() else 0), -item["score"]))
    hero = candidates[0] if candidates else {}
    if hero:
        used_heroes.add(hero["title"].casefold())
    c.setFillColor(NAVY); c.rect(0, h - 130, w, 130, fill=1, stroke=0)
    draw_crop(c, hero.get("poster_path"), 24, h - 119, 66, 94)
    c.setFillColor(GOLD); c.setFont("Helvetica-Bold", 10); c.drawString(104, h - 37, day.strftime("%A, %B %-d").upper())
    c.setFillColor(WHITE); title = hero.get("title", "Today's Highlights"); c.setFont("Helvetica-Bold", fit_text(c, title, 270, 22, 12)); c.drawString(104, h - 65, title)
    para(c, shorten(hero.get("overview", "Movies and television scheduled throughout the day."), 360), 104, h - 116, 330, 42, 7.5, WHITE, 9)
    c.setFont("Helvetica-Bold", 18); c.drawRightString(w - 24, h - 38, f"{kind.upper()} • {'MIDNIGHT-NOON' if half == 0 else 'NOON-MIDNIGHT'}")
    channels = []
    by_channel = defaultdict(list)
    for row in rows:
        if row["channel_kind"] != kind or row["stop_ts"] <= start.timestamp() or row["start_ts"] >= stop.timestamp(): continue
        by_channel[row["channel_id"]].append(row)
    for channel_id, values in sorted(by_channel.items(), key=lambda pair: pair[1][0]["sort_order"]): channels.append((channel_id, values))
    gx, gy, gw, gh = 24, 38, w - 48, h - 184
    label_w, time_w = 92, (gw - 92) / 12
    row_h = gh / max(1, len(channels))
    c.setFillColor(WHITE); c.rect(gx, gy, gw, gh, fill=1, stroke=0)
    for hour in range(13):
        x = gx + label_w + hour * time_w
        c.setStrokeColor(GRID); c.line(x, gy, x, gy + gh)
        if hour < 12:
            label = (start + dt.timedelta(hours=hour)).strftime("%-I %p")
            c.setFillColor(MUTED); c.setFont("Helvetica-Bold", 5.5); c.drawCentredString(x + time_w / 2, gy + gh + 5, label)
    for idx, (_, values) in enumerate(channels):
        y = gy + gh - (idx + 1) * row_h
        c.setFillColor(NAVY if idx % 2 == 0 else colors.HexColor("#10264b")); c.rect(gx, y, label_w, row_h, fill=1, stroke=0)
        c.setFillColor(WHITE); c.setFont("Helvetica-Bold", fit_text(c, values[0]["channel_number"] + " " + values[0]["channel_name"], label_w - 8, min(8, row_h * .34), 4.5)); c.drawString(gx + 4, y + row_h * .58, values[0]["channel_number"] + " " + values[0]["channel_name"])
        for entry in values:
            left = max(entry["start_ts"], start.timestamp()); right = min(entry["stop_ts"], stop.timestamp())
            x = gx + label_w + (left - start.timestamp()) / 3600 * time_w
            ew = max(1, (right - left) / 3600 * time_w)
            c.setFillColor(colors.HexColor("#dbe8ff") if idx % 2 == 0 else colors.HexColor("#edf3ff")); c.rect(x, y, ew, row_h, fill=1, stroke=0)
            c.setStrokeColor(colors.HexColor("#8aa9e8")); c.rect(x, y, ew, row_h, fill=0, stroke=1)
            label = clean(entry["title"] + (f" - {entry['subtitle']}" if entry["subtitle"] else ""))
            fs = fit_text(c, label, ew - 4, min(6.4, row_h * .27), 3.5); c.setFont("Helvetica-Bold", fs); c.setFillColor(INK)
            if ew > 15: c.drawString(x + 2, y + row_h * .58, shorten(label, max(5, int(ew / max(fs * .5, 1)))))
            if row_h > 13 and ew > 30:
                tm = dt.datetime.fromtimestamp(entry["start_ts"]).strftime("%-I:%M")
                c.setFont("Helvetica", max(3.5, fs - 1)); c.drawString(x + 2, y + 2, tm)
    footer(c, page, issue, w); c.showPage()


def next_issue_number(archive_dir: Path) -> int:
    numbers = []
    for path in archive_dir.glob("CineMedia-Vault-Guide-Issue-*.pdf"):
        match = re.search(r"Issue-(\d+)", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def archive_issue(output: Path, archive_dir: Path, issue_number: int, week: dt.date, feature: dict) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived = archive_dir / f"CineMedia-Vault-Guide-Issue-{issue_number:03d}-{week.isoformat()}.pdf"
    temporary = archived.with_suffix(".pdf.part")
    shutil.copy2(output, temporary)
    os.replace(temporary, archived)
    manifest = archive_dir / "issues.jsonl"
    record = {
        "issue": issue_number,
        "week": week.isoformat(),
        "feature": feature.get("title") or feature.get("schedule_title"),
        "file": archived.name,
        "created_at": dt.datetime.now().astimezone().isoformat(),
    }
    with manifest.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=True) + "\n")
    return archived


def generate(args) -> Path:
    base, db, output = Path(args.base).resolve(), Path(args.db).resolve(), Path(args.output).resolve()
    week = monday_for(dt.date.fromisoformat(args.week) if args.week else dt.date.today())
    tz = dt.datetime.now().astimezone().tzinfo
    start_dt = dt.datetime.combine(week, dt.time.min, tzinfo=tz); stop_dt = start_dt + dt.timedelta(days=7)
    cat = Catalog(db, base); rows = cat.schedule(int(start_dt.timestamp()), int(stop_dt.timestamp()))
    if not rows: raise SystemExit(f"No virtual-channel schedule rows found for {week} through {week + dt.timedelta(days=6)}")
    seed = int(hashlib.sha256(week.isoformat().encode()).hexdigest()[:12], 16); rng = random.Random(seed)
    picks = feature_candidates(cat, rows, rng)
    recent = cat.recent_on_demand(6)
    if not picks: raise SystemExit("No catalog metadata matched the weekly schedule")
    for item in picks:
        media_type = "movie" if item["kind"] == "movie" else "tv_show"
        item["cast_profiles"] = cat.cast_profiles(media_type, item["id"], 4)
        item["cast"] = [profile["name"] for profile in item["cast_profiles"][:3]] or cat.cast(media_type, item["id"], 3)
    # Build independent visual teasers for both listing sections. The general
    # feature ranking can be movie-heavy, but the TV divider must never be an
    # empty page when scheduled show artwork exists.
    section_features = {"movie": [], "tv": []}
    section_seen = {"movie": set(), "tv": set()}
    for scheduled in rows:
        section_kind = "movie" if scheduled["media_kind"] == "movie" else "tv"
        key = clean(scheduled["title"]).casefold()
        if key in section_seen[section_kind] or len(section_features[section_kind]) >= 3:
            continue
        detail = cat.movie_details(scheduled["title"]) if section_kind == "movie" else cat.show_details(scheduled["title"])
        if not detail:
            continue
        poster_path = resolve_art(cat.base, detail.get("poster"))
        if not poster_path or not Path(poster_path).is_file():
            continue
        detail.update(kind=section_kind, poster_path=poster_path)
        section_features[section_kind].append(detail)
        section_seen[section_kind].add(key)
    # During the initial background backfill, favor a genuinely scheduled
    # title whose person records have already arrived so every generated issue
    # can showcase real portraits and biographies immediately.
    if not any(item.get("cast_profiles") for item in picks):
        seen_titles = set()
        for scheduled in rows:
            if scheduled["media_kind"] != "movie" or scheduled["title"].casefold() in seen_titles:
                continue
            seen_titles.add(scheduled["title"].casefold())
            detail = cat.movie_details(scheduled["title"])
            if not detail:
                continue
            profiles = cat.cast_profiles("movie", detail["id"], 4)
            if not profiles:
                continue
            detail.update(kind="movie", schedule_title=scheduled["title"], subtitle="", start_ts=scheduled["start_ts"], channel=scheduled["channel_number"], score=float(detail.get("vote_average") or scheduled["rating"] or 0))
            detail["poster_path"] = resolve_art(cat.base, detail.get("poster"))
            detail["cast_profiles"] = profiles
            detail["cast"] = [profile["name"] for profile in profiles[:3]]
            picks.insert(0, detail)
            break
    archive_dir = Path(args.archive_dir).expanduser().resolve()
    archive_dir.mkdir(parents=True, exist_ok=True)
    history_path = archive_dir / "cover-history.json"
    try:
        cover_history = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cover_history = []
    recent_titles = {clean(item.get("title")).casefold() for item in cover_history[-12:]}
    def cover_rank(item):
        year = int(item.get("year") or 0) if str(item.get("year") or "").isdigit() else 0
        return (item["title"].casefold() not in recent_titles, year, bool(item.get("poster_path")), len(item.get("cast_profiles", [])), item["score"])
    cover_feature = max(picks[:24], key=cover_rank)
    cover_media_type = "movie" if cover_feature["kind"] == "movie" else "tv_show"
    cover_feature["genres"] = cat.genres(cover_feature["id"]) if cover_media_type == "movie" else []
    cover_feature["other_credits"] = cat.other_credits(cover_feature["cast"], cover_feature["title"])
    issue = f"{week.strftime('%B %-d')} - {(week + dt.timedelta(days=6)).strftime('%B %-d, %Y')}"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cine-guide-") as temp:
        stills = extract_stills(cover_feature, Path(temp))
        c = Canvas(str(output), pagesize=LETTER, pageCompression=1, title=f"CineMedia Vault Guide - {issue}", author="CineMedia Vault")
        also_features = [item for item in picks if item["title"].casefold() != cover_feature["title"].casefold()][:3]
        masthead = None
        if args.masthead:
            requested_masthead = Path(args.masthead).expanduser()
            masthead = requested_masthead if requested_masthead.is_absolute() else base / requested_masthead
            masthead = masthead.resolve()
        cover(c, cover_feature, issue, masthead)
        contents_page(c, picks, issue, 2)
        editorial(c, cover_feature, stills, also_features, issue, 3)
        cast_spotlight(c, cover_feature, issue, 4)
        picks_page(c, picks[1:], issue, 5)
        picks_page(c, recent, issue, 6, "NEW ON DEMAND")
        page = 7
        stories, entertainment = load_news_data(Path(args.news_data).expanduser())
        for story in stories + entertainment:
            portrait = cat.story_portrait(story)
            if portrait:
                story["image_path"] = str(portrait)
        actor_features = [item for item in picks if item.get("cast_profiles") and item["title"].casefold() != cover_feature["title"].casefold()][:2]
        for actor_feature in actor_features:
            cast_spotlight(c, actor_feature, issue, page); page += 1
        used_heroes = {cover_feature["title"].casefold()}
        inserts = []
        for start_index in range(0, min(len(stories), 9), 3):
            inserts.append(("news", stories[start_index:start_index + 3]))
        for start_index in range(0, min(len(entertainment), 18), 3):
            inserts.append(("entertainment", entertainment[start_index:start_index + 3]))
        # Alternate general-news and entertainment features, then place the
        # pages at regular intervals through the 28 listing pages.
        news_inserts = [item for item in inserts if item[0] == "news"]
        entertainment_inserts = [item for item in inserts if item[0] == "entertainment"]
        inserts = []
        while news_inserts or entertainment_inserts:
            if news_inserts: inserts.append(news_inserts.pop(0))
            if entertainment_inserts: inserts.append(entertainment_inserts.pop(0))
        grid_pages_written = 0
        insert_after = [4, 9, 14, 18, 23, 27]
        insert_index = 0
        # Keep the weekly magazine easy to browse: all movie listings first,
        # followed by all television listings. Each section is chronological
        # Monday-Sunday and midnight-noon/noon-midnight.
        for kind in ("movie", "tv"):
            section_page(c, kind, section_features[kind], issue, page); page += 1
            for offset in range(7):
                day = week + dt.timedelta(days=offset)
                for half in (0, 1):
                    daily_grid(c, cat, day, rows, kind, half, used_heroes, issue, page); page += 1
                    grid_pages_written += 1
                    if insert_index < len(inserts) and grid_pages_written >= insert_after[min(insert_index, len(insert_after) - 1)]:
                        insert_type, payload = inserts[insert_index]
                        if insert_type == "news":
                            news_articles_page(c, payload, issue, page, "NEWS FEATURES" if insert_index == 0 else "NEWS NOTEBOOK")
                        else:
                            news_articles_page(c, payload, issue, page, "ENTERTAINMENT REPORT" if insert_index == 1 else "ENTERTAINMENT NOTEBOOK")
                        page += 1; insert_index += 1
        c.save()
    issue_number = next_issue_number(archive_dir)
    archived = archive_issue(output, archive_dir, issue_number, week, cover_feature)
    cover_history.append({"issue": issue_number, "week": week.isoformat(), "title": cover_feature["title"], "year": cover_feature.get("year")})
    history_temp = history_path.with_suffix(".tmp")
    history_temp.write_text(json.dumps(cover_history[-52:], indent=2), encoding="utf-8")
    os.replace(history_temp, history_path)
    latest = output.parent / "CineMedia-Vault-Guide-Latest.pdf"
    temporary = latest.with_suffix(".tmp"); shutil.copy2(output, temporary); os.replace(temporary, latest)
    print(f"Created {output} ({page - 1} pages); latest={latest}; archive={archived}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="cinevault-data/cinemediavault-lab.db")
    parser.add_argument("--base", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("--week", help="Any date in the requested Monday-Sunday week (YYYY-MM-DD)")
    parser.add_argument("--archive-dir", default=os.environ.get("CINEGUIDE_ARCHIVE_DIR", "/media/jnicolas/Expansion/CineGuideMagazine"))
    parser.add_argument("--masthead", default=os.environ.get("CINEGUIDE_MASTHEAD", "cine-guide-magazine/masthead-cinemedia-vault-guide.png"))
    parser.add_argument("--news-data", default=os.environ.get("CINEGUIDE_NEWS_DATA", "/home/jnicolas/cinevault-genchannel/data.json"))
    generate(parser.parse_args())
