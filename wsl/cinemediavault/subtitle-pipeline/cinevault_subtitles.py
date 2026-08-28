#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

APP = "cinevault-subtitles"
HOME = Path.home()
STATE = HOME / ".local/state" / APP
CACHE = HOME / ".cache" / APP
DB = STATE / "queue.sqlite3"
HERE = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("CINEVAULT_SUBTITLE_CONFIG", HERE / "config.json"))
VIDEO_DEFAULT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm"}
TIMING = re.compile(r"(\d\d):(\d\d):(\d\d)[,.](\d\d\d)\s+-->\s+(\d\d):(\d\d):(\d\d)[,.](\d\d\d)")
LANG_ALIASES = {"eng": "en", "en": "en", "english": "en", "spa": "es", "es": "es", "spanish": "es", "castilian": "es"}
_WHISPER_MODEL = None


def now():
    return datetime.now(timezone.utc).isoformat()


def load_config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def connect():
    STATE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript("""
      PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS media (
        path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL,
        status TEXT NOT NULL, source_language TEXT, needs_en INTEGER NOT NULL DEFAULT 1,
        needs_es INTEGER NOT NULL DEFAULT 1, attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT, discovered_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        completed_at TEXT
      );
      CREATE INDEX IF NOT EXISTS media_status_idx ON media(status, mtime);
      CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY, kind TEXT, started_at TEXT, finished_at TEXT,
        discovered INTEGER DEFAULT 0, queued INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0,
        completed INTEGER DEFAULT 0, failed INTEGER DEFAULT 0
      );
    """)
    return db


def notify(cfg, message):
    sender = Path(cfg.get("webex_sender", ""))
    if not sender.exists():
        print(f"WEBEX skipped (sender missing): {message}", flush=True)
        return
    result = subprocess.run([sys.executable, str(sender), message], text=True, capture_output=True)
    if result.returncode:
        print(f"WEBEX failed: {result.stderr.strip()}", flush=True)
    else:
        print(result.stdout.strip(), flush=True)


def mounted_root(path):
    p = Path(path)
    if not p.is_dir():
        return False
    result = subprocess.run(["findmnt", "-n", "-T", str(p), "-o", "FSTYPE"], text=True, capture_output=True)
    return result.returncode == 0 and result.stdout.strip().lower() in {"cifs", "nfs", "nfs4"}


def sidecar_languages(video):
    langs = set()
    stem = video.stem.lower()
    for p in video.parent.glob("*.srt"):
        low = p.stem.lower()
        if low == stem or low.startswith(stem + "."):
            tokens = re.split(r"[. _-]+", low[len(stem):])
            for token in tokens:
                if token in LANG_ALIASES:
                    langs.add(LANG_ALIASES[token])
            if not tokens or not any(t for t in tokens if t):
                langs.add("unknown")
    return langs


def sidecar_for(video, language):
    stem = video.stem.lower()
    for path in video.parent.glob("*.srt"):
        low = path.stem.lower()
        if not low.startswith(stem + "."):
            continue
        tokens = re.split(r"[. _-]+", low[len(stem):])
        if any(LANG_ALIASES.get(token) == language for token in tokens):
            return path
    return None


def probe_subtitles(video):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
           "stream=index,codec_name:stream_tags=language,title", "-of", "json", str(video)]
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
    if result.returncode:
        return []
    streams = json.loads(result.stdout or "{}").get("streams", [])
    return [s for s in streams if s.get("codec_name") not in {"hdmv_pgs_subtitle", "dvd_subtitle", "xsub"}]


def embedded_languages(streams):
    found = set()
    for stream in streams:
        tags = stream.get("tags", {})
        raw = str(tags.get("language", "")).lower()
        title = str(tags.get("title", "")).lower()
        if raw in LANG_ALIASES:
            found.add(LANG_ALIASES[raw])
        for alias, normalized in LANG_ALIASES.items():
            if alias in title:
                found.add(normalized)
    return found


def scan(cfg):
    roots = [Path(x) for x in cfg["roots"]]
    bad = [str(x) for x in roots if not mounted_root(x)]
    if bad:
        raise SystemExit("Refusing scan; media root is not a mounted CIFS/NFS filesystem: " + ", ".join(bad))
    db = connect()
    run = db.execute("INSERT INTO runs(kind,started_at) VALUES('scan',?)", (now(),)).lastrowid
    db.commit()
    notify(cfg, f"**CineVault subtitles:** catalog discovery started for {len(roots)} roots.")
    extensions = {x.lower() for x in cfg.get("video_extensions", VIDEO_DEFAULT)}
    stable_before = time.time() - 60 * int(cfg.get("stable_age_minutes", 30))
    discovered = queued = skipped = 0
    for root in roots:
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for filename in files:
                video = Path(base) / filename
                if video.suffix.lower() not in extensions:
                    continue
                discovered += 1
                try:
                    stat = video.stat()
                except OSError:
                    continue
                sidecars = sidecar_languages(video)
                need_en, need_es = "en" not in sidecars, "es" not in sidecars
                embedded = set()
                if need_en or need_es:
                    embedded = embedded_languages(probe_subtitles(video))
                status = "waiting_stable" if stat.st_mtime > stable_before else ("queued" if need_en or need_es else "complete")
                if status == "queued": queued += 1
                else: skipped += 1
                old = db.execute("SELECT size,mtime,status FROM media WHERE path=?", (str(video),)).fetchone()
                if old and old["size"] == stat.st_size and old["mtime"] == stat.st_mtime and old["status"] in {"processing", "complete"}:
                    continue
                db.execute("""INSERT INTO media(path,size,mtime,status,needs_en,needs_es,error,discovered_at,updated_at)
                  VALUES(?,?,?,?,?,?,NULL,?,?) ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,
                  status=excluded.status,needs_en=excluded.needs_en,needs_es=excluded.needs_es,error=NULL,updated_at=excluded.updated_at""",
                  (str(video), stat.st_size, stat.st_mtime, status, int(need_en), int(need_es), now(), now()))
                if discovered % 500 == 0:
                    db.commit(); print(f"discovered={discovered} queued={queued}", flush=True)
    db.execute("UPDATE runs SET finished_at=?,discovered=?,queued=?,skipped=? WHERE id=?", (now(), discovered, queued, skipped, run))
    db.commit()
    notify(cfg, f"**CineVault subtitles:** discovery complete — {discovered:,} videos checked, {queued:,} queued, {skipped:,} already covered/recent.")
    print(json.dumps({"discovered": discovered, "queued": queued, "skipped": skipped}))


def parse_srt(path):
    cues = []
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        ti = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if ti is None or not TIMING.search(lines[ti]):
            continue
        cue_text = " ".join(x.strip() for x in lines[ti + 1:] if x.strip())
        if cue_text:
            cues.append((lines[ti], cue_text))
    return cues


def write_srt_atomic(target, cues):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + f".{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as out:
        for index, (timing, text) in enumerate(cues, 1):
            out.write(f"{index}\n{timing}\n{text}\n\n")
        out.flush(); os.fsync(out.fileno())
    if not cues or temp.stat().st_size < 32:
        temp.unlink(missing_ok=True)
        raise RuntimeError("generated SRT failed validation")
    temp.replace(target)


def cue_seconds(timing):
    match = TIMING.search(timing)
    if not match:
        return 0.0
    h, m, s, ms = map(int, match.groups()[:4])
    return h * 3600 + m * 60 + s + ms / 1000


def merge_events(dialogue, events, language):
    labels_es = {
        "[Music]": "[Música]", "[Screaming]": "[Gritos]", "[Explosion]": "[Explosión]",
        "[Gunfire]": "[Disparos]", "[Crashing / breaking]": "[Choque / objetos rompiéndose]",
        "[Siren]": "[Sirena]", "[Singing]": "[Canto]", "[Applause]": "[Aplausos]",
        "[Laughter]": "[Risas]", "[Crying]": "[Llanto]", "[Door slams]": "[Portazo]",
        "[Vehicle noise]": "[Ruido de vehículo]"
    }
    combined = list(parse_srt(dialogue))
    for timing, text in parse_srt(events):
        combined.append((timing, labels_es.get(text, text) if language == "es" else text))
    combined.sort(key=lambda cue: cue_seconds(cue[0]))
    return combined


def stamp(seconds):
    ms = max(0, int(round(float(seconds) * 1000)))
    hours, ms = divmod(ms, 3600000); minutes, ms = divmod(ms, 60000); secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def whisper(media, target, cfg, language=None, task="transcribe"):
    global _WHISPER_MODEL
    from faster_whisper import WhisperModel
    if _WHISPER_MODEL is None:
        _WHISPER_MODEL = WhisperModel(cfg["whisper_model"], device="cuda", compute_type=cfg["whisper_compute_type"])
    model = _WHISPER_MODEL
    options = dict(language=language, task=task, beam_size=int(cfg["beam_size"]),
                   word_timestamps=bool(cfg["word_timestamps"]),
                   condition_on_previous_text=bool(cfg["condition_on_previous_text"]),
                   vad_filter=bool(cfg["vad_filter"]))
    segments, info = model.transcribe(str(media), **options)
    cues = []
    for segment in segments:
        text = " ".join(segment.text.strip().split())
        if not text: continue
        words = list(segment.words or [])
        start = words[0].start if words else segment.start
        end = words[-1].end if words else segment.end
        maximum = float(cfg.get("maximum_cue_seconds", 8))
        if end <= start or end - start > maximum:
            end = start + max(1.2, min(maximum, len(text) / float(cfg.get("characters_per_second", 15))))
        cues.append((f"{stamp(start)} --> {stamp(end)}", text))
    write_srt_atomic(target, cues)
    return info.language, len(cues)


def translate_srt(source, target, from_code, to_code):
    import argostranslate.translate
    installed = argostranslate.translate.get_installed_languages()
    src = next((x for x in installed if x.code == from_code), None)
    dst = next((x for x in installed if x.code == to_code), None)
    if not src or not dst:
        raise RuntimeError(f"Argos model missing: {from_code}->{to_code}")
    translation = src.get_translation(dst)
    cues = [(timing, translation.translate(text)) for timing, text in parse_srt(source)]
    write_srt_atomic(target, cues)


def extract_embedded(video, stream, target):
    index = str(stream["index"])
    temp = target.with_name(target.name + f".{os.getpid()}.tmp")
    result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(video), "-map", f"0:{index}", "-f", "srt", str(temp)])
    if result.returncode or not temp.exists():
        temp.unlink(missing_ok=True); raise RuntimeError(f"embedded subtitle extraction failed for stream {index}")
    cues = parse_srt(temp); temp.unlink(missing_ok=True); write_srt_atomic(target, cues)


def target_for(video, language):
    return video.with_name(f"{video.stem}.{language}.whisper.srt")


def busy(cfg):
    usage = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"], text=True, capture_output=True)
    try:
        if int(usage.stdout.strip().splitlines()[0]) > int(cfg.get("maximum_gpu_utilization_percent", 45)):
            return "GPU is busy"
    except (ValueError, IndexError): pass
    if cfg.get("pause_for_transcode_processes", True):
        ps = subprocess.run(["ps", "-eo", "comm,args"], text=True, capture_output=True).stdout.lower()
        if any(x in ps for x in ("handbrakecli", "combined_transcode_orchestrator")):
            return "transcode process is active"
    free = shutil.disk_usage(CACHE.parent if CACHE.parent.exists() else HOME).free / 1024**3
    if free < float(cfg.get("minimum_scratch_gb", 20)):
        return f"scratch free space is only {free:.1f} GB"
    return None


def process_one(db, cfg, row):
    video = Path(row["path"])
    if not video.exists(): raise RuntimeError("media disappeared")
    if not all(mounted_root(root) for root in cfg["roots"]): raise RuntimeError("media mount guard failed")
    sidecars = sidecar_languages(video)
    need = {lang for lang in ("en", "es") if lang not in sidecars}
    if not need: return "already complete"
    targets = {lang: target_for(video, lang) for lang in need}
    streams = probe_subtitles(video)
    for lang in list(need):
        matching = [s for s in streams if lang in embedded_languages([s])]
        if matching:
            extract_embedded(video, matching[0], targets[lang]); need.remove(lang)
    if not need: return "extracted embedded tracks"
    english_sidecar = sidecar_for(video, "en")
    spanish_sidecar = sidecar_for(video, "es")
    if "es" in need and english_sidecar:
        translate_srt(english_sidecar, targets["es"], "en", "es"); need.remove("es")
    if "en" in need and spanish_sidecar:
        translate_srt(spanish_sidecar, targets["en"], "es", "en"); need.remove("en")
    if not need: return "translated existing sidecar"
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="job-", dir=CACHE) as scratch_name:
        scratch = Path(scratch_name)
        audio = scratch / "audio.flac"
        result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(video), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", str(audio)])
        if result.returncode or not audio.exists(): raise RuntimeError("local audio extraction failed")
        native = scratch / "native.srt"
        detected, count = whisper(audio, native, cfg, language=None, task="transcribe")
        detected = "es" if detected.startswith("es") else "en"
        if detected == "es":
            if "es" in need: write_srt_atomic(targets["es"], parse_srt(native)); need.remove("es")
            if "en" in need:
                english = scratch / "english.srt"; whisper(audio, english, cfg, language="es", task="translate")
                write_srt_atomic(targets["en"], parse_srt(english)); need.remove("en")
        else:
            if "en" in need: write_srt_atomic(targets["en"], parse_srt(native)); need.remove("en")
            if "es" in need:
                translate_srt(native, targets["es"], "en", "es"); need.remove("es")
        if cfg.get("audio_events", True):
            events = scratch / "events.srt"
            event_script = Path(__file__).with_name("audio_events_to_srt.py")
            event_result = subprocess.run([sys.executable, str(event_script), str(audio), str(events),
                                           "--window", str(cfg.get("event_window_seconds", 5.0))])
            if event_result.returncode:
                raise RuntimeError("audio-event caption pass failed")
            for lang, target in targets.items():
                if target.exists():
                    write_srt_atomic(target, merge_events(target, events, lang))
        return f"Whisper language={detected} cues={count}"


def worker(cfg, once=False):
    db = connect(); completed_this_run = failed_this_run = 0
    notify(cfg, "**CineVault subtitles:** worker started (single low-impact RTX 4090 worker).")
    while True:
        reason = busy(cfg)
        if reason:
            print(f"paused: {reason}", flush=True)
            if once: return
            time.sleep(int(cfg.get("worker_poll_seconds", 120))); continue
        row = db.execute("SELECT * FROM media WHERE status IN ('queued','failed') AND attempts < 3 ORDER BY mtime,path LIMIT 1").fetchone()
        if not row:
            counts = dict(db.execute("SELECT status,count(*) n FROM media GROUP BY status").fetchall())
            notify(cfg, f"**CineVault subtitles:** queue is currently complete. Status: {counts}.")
            return
        db.execute("UPDATE media SET status='processing',attempts=attempts+1,updated_at=? WHERE path=?", (now(), row["path"])); db.commit()
        try:
            detail = process_one(db, cfg, row)
            db.execute("UPDATE media SET status='complete',error=NULL,completed_at=?,updated_at=? WHERE path=?", (now(), now(), row["path"])); db.commit()
            completed_this_run += 1; print(f"complete: {row['path']} ({detail})", flush=True)
        except Exception as exc:
            failed_this_run += 1
            db.execute("UPDATE media SET status='failed',error=?,updated_at=? WHERE path=?", (str(exc)[:1000], now(), row["path"])); db.commit()
            notify(cfg, f"**CineVault subtitle failure:** `{Path(row['path']).name}` — {str(exc)[:400]}")
            print(f"failed: {row['path']}: {exc}", file=sys.stderr, flush=True)
        every = int(cfg.get("progress_every_completed", 10))
        if completed_this_run and completed_this_run % every == 0:
            queued = db.execute("SELECT count(*) FROM media WHERE status='queued'").fetchone()[0]
            notify(cfg, f"**CineVault subtitles progress:** {completed_this_run} completed this run, {failed_this_run} failed, {queued} queued.")
        if once: return


def status():
    db = connect()
    counts = {r["status"]: r["n"] for r in db.execute("SELECT status,count(*) n FROM media GROUP BY status")}
    print(json.dumps({"database": str(DB), "counts": counts}, indent=2))
    for row in db.execute("SELECT path,error FROM media WHERE status='failed' ORDER BY updated_at DESC LIMIT 10"):
        print(f"FAILED {row['path']}: {row['error']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("scan", "worker", "run-once", "status"))
    args = parser.parse_args(); cfg = load_config()
    {"scan": lambda: scan(cfg), "worker": lambda: worker(cfg), "run-once": lambda: worker(cfg, True), "status": status}[args.command]()


if __name__ == "__main__": main()
