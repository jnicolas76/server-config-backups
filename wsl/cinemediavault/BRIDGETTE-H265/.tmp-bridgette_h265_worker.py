#!/usr/bin/env python3
"""Isolated, resumable Bridgette Vault H.265 reduction worker."""

import argparse
import csv
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".ts", ".mpeg", ".mpg", ".f4v"}
TARGET_RATIO = 0.40
AUDIO_BPS = 128_000
ROOT = Path("/mnt/d/BRIDGE/Bridgette B - MegaPack")
STATE_DIR = Path("/mnt/c/DATA/BRIDGETTE-H265")
QUEUE = STATE_DIR / "queue.json"
LOG = STATE_DIR / "bridgette-h265.log"
MANIFEST = STATE_DIR / "completed.csv"
FAILED = STATE_DIR / "failed.csv"
LOCK = STATE_DIR / "worker.lock"
STOP = False
VAULT_REFRESH_URL = "http://127.0.0.1:8097/internal/refresh"


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message):
    line = f"{now()} {message}"
    try:
        print(line, flush=True)
    except BrokenPipeError:
        # Detached workers must continue even if their original terminal closes.
        try:
            sys.stdout = open(os.devnull, "w")
        except Exception:
            pass
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def refresh_vault():
    try:
        with urllib.request.urlopen(VAULT_REFRESH_URL, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        log(f"VAULT refreshed count={payload.get('count', 'unknown')}")
    except Exception as exc:
        # A vault outage must never fail or pause a completed transcode.
        log(f"VAULT refresh failed: {exc}")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def probe(path):
    result = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,width,height",
        "-of", "json", str(path),
    ])
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    data = json.loads(result.stdout)
    duration = float(data["format"].get("duration") or 0)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video or duration <= 0:
        raise RuntimeError("missing valid video stream or duration")
    return {
        "duration": duration,
        "size": int(data["format"].get("size") or path.stat().st_size),
        "codec": video.get("codec_name", ""),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
    }


def append_csv(path, row):
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row))
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def build_queue():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if QUEUE.exists():
        return json.loads(QUEUE.read_text(encoding="utf-8"))
    seen = set()
    items = []
    for path in sorted(ROOT.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTS:
            continue
        # Windows paths are case-insensitive; avoid duplicate directory aliases.
        key = str(path).casefold()
        if key in seen or ".cmv-h265" in path.name:
            continue
        seen.add(key)
        try:
            info = probe(path)
        except Exception as exc:
            append_csv(FAILED, {"time": now(), "source": str(path), "error": f"queue probe: {exc}"})
            continue
        if info["codec"] in {"hevc", "h265"}:
            log(f"SKIP already H.265: {path.name}")
            continue
        items.append({"source": str(path), "original_size": info["size"], "status": "pending"})
    QUEUE.write_text(json.dumps(items, indent=2), encoding="utf-8")
    log(f"QUEUE created with {len(items)} pending files")
    return items


def save_queue(items):
    tmp = QUEUE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    os.replace(tmp, QUEUE)


def transcode(item):
    source = Path(item["source"])
    if not source.exists():
        raise RuntimeError("source no longer exists")
    before = probe(source)
    if before["codec"] in {"hevc", "h265"}:
        return "already_h265", before, before, source

    target_bytes = int(before["size"] * TARGET_RATIO)
    total_bps = int(target_bytes * 8 / before["duration"])
    video_bps = max(220_000, total_bps - AUDIO_BPS)
    maxrate = int(video_bps * 1.15)
    bufsize = int(video_bps * 2)
    temp = source.with_name(source.name + ".cmv-h265.partial.mp4")
    final = source if source.suffix.lower() == ".mp4" else source.with_name(source.stem + ".cmv-h265.mp4")
    temp.unlink(missing_ok=True)

    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0",
        "-c:v", "hevc_nvenc", "-preset", "p6", "-tune", "hq",
        "-rc", "vbr", "-multipass", "fullres",
        "-b:v", str(video_bps), "-maxrate", str(maxrate), "-bufsize", str(bufsize),
        "-spatial-aq", "1", "-temporal-aq", "1", "-rc-lookahead", "32",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        str(temp),
    ]
    log(f"START {source.name} | {before['size']/1073741824:.2f} GiB -> target {target_bytes/1073741824:.2f} GiB")
    with LOG.open("a", encoding="utf-8") as fh:
        proc = subprocess.run(command, stdout=fh, stderr=fh)
    if proc.returncode:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg exited {proc.returncode}")

    after = probe(temp)
    duration_delta = abs(after["duration"] - before["duration"])
    duration_tolerance = max(3.0, before["duration"] * 0.01)
    if after["codec"] not in {"hevc", "h265"}:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"output codec is {after['codec']}, expected HEVC")
    if duration_delta > duration_tolerance:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"duration mismatch {duration_delta:.2f}s")
    if after["size"] >= before["size"] or after["size"] > int(before["size"] * 0.46):
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"output ratio {after['size']/before['size']:.1%} failed reduction validation")

    # Atomic replacement occurs only after successful validation.
    if final == source:
        os.replace(temp, source)
    else:
        if final.exists():
            temp.unlink(missing_ok=True)
            raise RuntimeError(f"destination already exists: {final}")
        os.replace(temp, final)
        source.unlink()
    return "completed", before, after, final


def handle_signal(signum, frame):
    global STOP
    STOP = True
    log(f"STOP requested by signal {signum}; worker will stop after current ffmpeg exits")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild-queue", action="store_true")
    args = parser.parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_fh = LOCK.open("w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Bridgette worker is already running", file=sys.stderr)
        return 2
    lock_fh.write(str(os.getpid()))
    lock_fh.flush()
    if args.rebuild_queue and QUEUE.exists():
        QUEUE.rename(QUEUE.with_name(f"queue-{int(time.time())}.json.bak"))
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    items = build_queue()
    log(f"WORKER start PID={os.getpid()} total={len(items)}")
    for index, item in enumerate(items):
        if STOP:
            break
        if item.get("status") in {"completed", "already_h265"}:
            continue
        item["status"] = "processing"
        item["started"] = now()
        save_queue(items)
        try:
            status, before, after, final = transcode(item)
            item.update({"status": status, "finished": now(), "output": str(final), "output_size": after["size"]})
            append_csv(MANIFEST, {
                "time": now(), "source": item["source"], "output": str(final),
                "original_bytes": before["size"], "output_bytes": after["size"],
                "saved_bytes": before["size"] - after["size"], "ratio": f"{after['size']/before['size']:.6f}",
                "duration_seconds": f"{after['duration']:.3f}", "codec": after["codec"],
            })
            log(f"DONE {Path(item['source']).name} | ratio={after['size']/before['size']:.1%} saved={(before['size']-after['size'])/1073741824:.2f} GiB")
            refresh_vault()
        except Exception as exc:
            item.update({"status": "failed", "finished": now(), "error": str(exc)})
            append_csv(FAILED, {"time": now(), "source": item["source"], "error": str(exc)})
            log(f"FAILED {Path(item['source']).name}: {exc}")
        save_queue(items)
    pending = sum(1 for x in items if x.get("status") in {"pending", "processing"})
    failed = sum(1 for x in items if x.get("status") == "failed")
    done = sum(1 for x in items if x.get("status") in {"completed", "already_h265"})
    log(f"WORKER end completed={done} failed={failed} pending={pending}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
