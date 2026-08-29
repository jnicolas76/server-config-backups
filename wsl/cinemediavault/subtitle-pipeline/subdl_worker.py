#!/usr/bin/env python3
"""Conservative SubDL acquisition lane for CineVault's movie-first subtitle queue."""
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

import cinevault_subtitles as cv


def local_day():
    return datetime.now().astimezone().date().isoformat()


def title_year(video):
    samples = [video.parent.name, video.stem]
    for sample in samples:
        match = re.search(r"^(.*?)[ ._-]*[\[(](19\d{2}|20\d{2})[\])]", sample)
        if match:
            title = re.sub(r"[._]+", " ", match.group(1)).strip(" -")
            return title, int(match.group(2))
    return None, None


def normalized(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def duration(path):
    result = cv.subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", str(path)], text=True,
                               capture_output=True, timeout=120)
    return float(result.stdout.strip()) if result.returncode == 0 else 0.0


def last_cue_end(path):
    cues = cv.parse_srt(path)
    if len(cues) < 50:
        return len(cues), 0.0
    match = cv.TIMING.search(cues[-1][0])
    if not match:
        return len(cues), 0.0
    h, m, s, ms = map(int, match.groups()[4:])
    return len(cues), h * 3600 + m * 60 + s + ms / 1000


def reserve_request(db, limit):
    day = local_day()
    db.execute("BEGIN IMMEDIATE")
    used = db.execute("SELECT requests FROM subdl_usage WHERE day=?", (day,)).fetchone()
    count = used[0] if used else 0
    if count >= limit:
        db.commit(); return False, count
    db.execute("INSERT INTO subdl_usage(day,requests) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET requests=requests+1", (day,))
    db.commit(); return True, count + 1


def claim(db):
    day = local_day()
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("""SELECT m.* FROM media m
      WHERE m.media_type='movie' AND m.status IN ('queued','failed') AND m.attempts < 3
        AND NOT EXISTS (SELECT 1 FROM subdl_attempts a WHERE a.path=m.path AND a.day=?)
      ORDER BY m.mtime DESC,m.path DESC LIMIT 1""", (day,)).fetchone()
    if row:
        db.execute("UPDATE media SET status='subdl_processing',updated_at=? WHERE path=?", (cv.now(), row["path"]))
    db.commit()
    return row


def record(db, row, status, detail, complete=False):
    day = local_day()
    db.execute("""INSERT INTO subdl_attempts(path,day,status,detail,attempted_at) VALUES(?,?,?,?,?)
      ON CONFLICT(path,day) DO UPDATE SET status=excluded.status,detail=excluded.detail,attempted_at=excluded.attempted_at""",
      (row["path"], day, status, detail[:1000], cv.now()))
    if complete:
        db.execute("""UPDATE media SET status='complete',error=NULL,completed_at=?,updated_at=?,
          completion_method='subdl',completion_detail=? WHERE path=?""", (cv.now(), cv.now(), detail[:1000], row["path"]))
    else:
        db.execute("UPDATE media SET status='queued',updated_at=? WHERE path=?", (cv.now(), row["path"]))
    db.commit()


def api_search(key, title, year):
    params = {"api_key": key, "type": "movie", "languages": "EN", "subs_per_page": 30,
              "releases": 1, "unpack": 1, "film_name": title, "year": year}
    url = "https://api.subdl.com/api/v1/subtitles?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=45) as response:
        return json.load(response)


def candidates(payload, title, year):
    movies = payload.get("results") or []
    allowed_ids = set()
    for movie in movies:
        candidate_title = movie.get("name") or movie.get("title") or ""
        candidate_year = movie.get("year")
        if normalized(candidate_title) == normalized(title) and str(candidate_year) == str(year):
            allowed_ids.add(str(movie.get("sd_id") or movie.get("id") or ""))
    subtitles = payload.get("subtitles") or []
    exact = [s for s in subtitles if str(s.get("language", "")).upper() in {"EN", "ENG", "ENGLISH"}
             and (not allowed_ids or str(s.get("sd_id") or s.get("movie_id") or "") in allowed_ids)]
    return exact


def download_candidate(candidate, destination):
    relative = candidate.get("url")
    if not relative:
        raise RuntimeError("candidate has no download URL")
    url = relative if str(relative).startswith("http") else "https://dl.subdl.com" + str(relative)
    archive = destination / "subtitle.zip"
    urllib.request.urlretrieve(url, archive)
    expected = str(candidate.get("md5") or "").lower()
    if expected and hashlib.md5(archive.read_bytes()).hexdigest().lower() != expected:
        raise RuntimeError("download checksum mismatch")
    with zipfile.ZipFile(archive) as bundle:
        names = [name for name in bundle.namelist() if name.lower().endswith(".srt") and not name.endswith("/")]
        if not names:
            raise RuntimeError("archive has no SRT")
        output = destination / "candidate.srt"
        output.write_bytes(bundle.read(names[0]))
    return output


def process(row, key, cfg):
    video = Path(row["path"])
    title, year = title_year(video)
    if not title or not year:
        return False, "skipped: title/year could not be parsed"
    payload = api_search(key, title, year)
    choices = candidates(payload, title, year)
    if not choices:
        return False, f"no exact English result for {title} ({year})"
    movie_seconds = duration(video)
    if movie_seconds <= 0:
        return False, "ffprobe duration unavailable"
    best = None
    with tempfile.TemporaryDirectory(prefix="subdl-", dir=cv.CACHE) as temp_name:
        scratch = Path(temp_name)
        for index, candidate in enumerate(choices[:10]):
            candidate_dir = scratch / str(index); candidate_dir.mkdir()
            try:
                srt = download_candidate(candidate, candidate_dir)
                cue_count, subtitle_end = last_cue_end(srt)
                delta = abs(movie_seconds - subtitle_end)
                coverage = subtitle_end / movie_seconds
                if cue_count >= 50 and coverage >= 0.85 and delta <= max(300, movie_seconds * 0.05):
                    score = delta
                    if best is None or score < best[0]:
                        best = (score, srt, cue_count, subtitle_end)
            except Exception:
                continue
        if not best:
            return False, f"{len(choices)} result(s), none passed duration/cue validation"
        target_en = video.with_name(f"{video.stem}.en.subdl.srt")
        target_es = video.with_name(f"{video.stem}.es.subdl.srt")
        cv.write_srt_atomic(target_en, cv.parse_srt(best[1]))
        cv.translate_srt(target_en, target_es, "en", "es")
        return True, (f"SubDL exact title/year; cues={best[2]}; movie={movie_seconds:.1f}s; "
                      f"subtitle_end={best[3]:.1f}s; English downloaded; Spanish translated locally")


def main():
    cfg = cv.load_config(); db = cv.connect(); cv.CACHE.mkdir(parents=True, exist_ok=True)
    key_path = Path(cfg["subdl_key_file"]).expanduser()
    key = key_path.read_text(encoding="utf-8").strip()
    limit = int(cfg.get("subdl_daily_request_limit", 1000))
    succeeded = attempted = 0
    cv.notify(cfg, f"**CineVault SubDL:** bottom-up movie lane started with a hard {limit:,}-request daily cap.")
    while True:
        row = claim(db)
        if not row:
            break
        parsed_title, parsed_year = title_year(Path(row["path"]))
        if not parsed_title or not parsed_year:
            attempted += 1
            detail = "skipped: title/year could not be parsed; no API request used"
            record(db, row, "metadata_unusable", detail, False)
            print(f"no-request skip: {row['path']} — {detail}", flush=True)
            continue
        allowed, used = reserve_request(db, limit)
        if not allowed:
            db.execute("UPDATE media SET status='queued',updated_at=? WHERE path=?", (cv.now(), row["path"])); db.commit()
            break
        attempted += 1
        try:
            ok, detail = process(row, key, cfg)
            record(db, row, "downloaded" if ok else "not_found_or_rejected", detail, ok)
            succeeded += int(ok)
            print(f"{used}/{limit} {'complete' if ok else 'skip'}: {row['path']} — {detail}", flush=True)
        except Exception as exc:
            record(db, row, "error", str(exc), False)
            print(f"{used}/{limit} error: {row['path']} — {exc}", file=sys.stderr, flush=True)
        if attempted % int(cfg.get("subdl_progress_every_requests", 50)) == 0:
            cv.export_reports(cfg)
            cv.notify(cfg, f"**CineVault SubDL progress:** {attempted} checked this run, {succeeded} accepted; {used}/{limit} requests used today.")
        time.sleep(float(cfg.get("subdl_request_delay_seconds", 1.0)))
    cv.export_reports(cfg)
    used = db.execute("SELECT requests FROM subdl_usage WHERE day=?", (local_day(),)).fetchone()
    used = used[0] if used else 0
    cv.notify(cfg, f"**CineVault SubDL:** run stopped — {attempted} checked, {succeeded} accepted, {used}/{limit} requests used today. Movie-first ordering remains enforced.")


if __name__ == "__main__":
    main()
