#!/usr/bin/env python3
"""Render CineMediaVault's clock-driven one-hour movie/TV promo channel."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro


def run(args, timeout=300):
    result = subprocess.run(args, timeout=timeout, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise RuntimeError((result.stderr or f"command failed ({result.returncode})")[-3000:])
    return result


def probe(path: Path):
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
        "-of", "json", str(path)
    ], check=True, capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout)
    return float((data.get("format") or {}).get("duration") or 0), any(
        s.get("codec_type") == "audio" for s in data.get("streams", []))


def spoken(text):
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"\bS0*(\d+)\s*E0*(\d+)\b",
                   lambda m: f"season {int(m.group(1))}, episode {int(m.group(2))}", value, flags=re.I)
    value = re.sub(r"\bS0*(\d+)\b", lambda m: f"season {int(m.group(1))}", value, flags=re.I)
    return value


def clock(ts, timezone):
    try:
        from zoneinfo import ZoneInfo
        when = dt.datetime.fromtimestamp(int(ts), ZoneInfo(timezone))
    except Exception:
        when = dt.datetime.fromtimestamp(int(ts))
    return when.strftime("%A, %B %-d at %-I:%M %p")


def narration(item, index, timezone):
    title, summary = spoken(item.get("title")), spoken(item.get("summary"))
    subtitle = spoken(item.get("subtitle"))
    airtime = clock(item.get("airtime", time.time()), timezone)
    channel = spoken(item.get("channel", "Virtual Channels"))
    if item.get("kind") == "movie":
        cast = [spoken(x) for x in item.get("cast", []) if x]
        genre = next(iter(item.get("genres") or []), "movie")
        year = spoken(item.get("year"))
        descriptor = " ".join(x for x in (year, genre) if x)
        if len(cast) >= 2:
            subject = f"{cast[0]} and {cast[1]} star in the {descriptor}, {title}."
        elif cast:
            subject = f"{cast[0]} stars in the {descriptor}, {title}."
        else:
            subject = f"The {descriptor}, {title}."
        movie_openers = (
            f"Coming up {airtime} in {channel} on Cine Media Vault.",
            f"Later on {channel}, airing {airtime}.",
            f"Your movie forecast for {airtime}, on {channel}.",
            f"Featured soon on the {channel} channel.",
            f"Make time {airtime}, here on Cine Media Vault.",
        )
        lead = f"{movie_openers[index % len(movie_openers)]} {subject}"
    else:
        episode = f", {subtitle}" if subtitle else ""
        tv_openers = (
            f"Coming up {airtime} on {channel}.",
            f"Later on {channel}, Cine Media Vault has more ahead.",
            f"Still ahead on {channel}.",
            f"On the schedule for {airtime}, on {channel}.",
            f"Here's a look at what's ahead on {channel}.",
        )
        lead = f"{tv_openers[index % len(tv_openers)]} {title}{episode}."
    ident = " This is Cine Media Vault." if index % 12 == 0 else ""
    return f"{lead} {summary}{ident}".strip()


def display_copy(item, timezone):
    lines = ["COMING UP", spoken(item.get("title"))]
    if item.get("subtitle"):
        lines.append(spoken(item["subtitle"]))
    lines.append(f"{item.get('channel_number', '')} {item.get('channel', '')}  •  {clock(item.get('airtime'), timezone)}")
    summary = spoken(item.get("summary"))
    if summary:
        # drawtext does not wrap textfile content itself.
        import textwrap
        lines.extend(textwrap.wrap(summary, width=72)[:4])
    return "\n".join(lines)


def render_segment(item, narration_path, output, text_path, segment_seconds, timezone, seed):
    source = Path(item["path"])
    duration, has_audio = probe(source)
    if duration <= 1:
        raise RuntimeError("source has no usable duration")
    rng = random.Random(f"{seed}:{source}:{item.get('airtime')}")
    # Avoid title sequences/recaps at the front and credits/previews at the
    # back. Percentage guards scale for films while the minimum guards keep
    # ordinary TV episodes away from both edges as well.
    lead_guard = max(90.0, duration * 0.10)
    tail_guard = max(120.0, duration * 0.12)
    first = min(lead_guard, max(0.0, duration - segment_seconds))
    last = max(first, duration - segment_seconds - tail_guard)
    offset = rng.uniform(first, last) if last > first else first
    text_path.write_text(display_copy(item, timezone), encoding="utf-8")
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    # Metadata is rendered by the synchronized blue UI panel, never burned
    # into the footage. This keeps clips clean on every display size.
    draw = ("scale=854:480:force_original_aspect_ratio=decrease,"
            "pad=854:480:(ow-iw)/2:(oh-ih)/2:black,fps=24")
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{offset:.3f}", "-stream_loop", "-1", "-i", str(source),
               "-i", str(narration_path), "-t", str(segment_seconds)]
    if has_audio:
        audio = (f"[0:a]volume=0.16,aresample=48000,apad,atrim=duration={segment_seconds}[bed];"
                 f"[1:a]adelay=900,volume=1.0,aresample=48000,apad,atrim=duration={segment_seconds}[voice];"
                 f"[bed][voice]amix=inputs=2:duration=longest:normalize=0,atrim=duration={segment_seconds}[a]")
    else:
        audio = f"[1:a]adelay=900,volume=1.0,aresample=48000,apad,atrim=duration={segment_seconds}[a]"
    command += ["-filter_complex", f"[0:v]{draw}[v];{audio}", "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "fastdecode", "-crf", "30",
                "-pix_fmt", "yuv420p", "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
                "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", str(output)]
    run(command, timeout=max(300, segment_seconds * 8))


def choose_items(items, count, seed):
    movies = [x for x in items if x.get("kind") == "movie" and Path(x.get("path", "")).is_file()]
    television = [x for x in items if x.get("kind") == "tv" and Path(x.get("path", "")).is_file()]
    rng = random.Random(seed)
    rng.shuffle(movies); rng.shuffle(television)
    chosen = []
    pools = [movies, television]
    positions = [0, 0]
    for index in range(count):
        preferred = index % 2
        pool = pools[preferred] or pools[1 - preferred]
        if not pool:
            break
        pos_index = preferred if pools[preferred] else 1 - preferred
        chosen.append(pool[positions[pos_index] % len(pool)])
        positions[pos_index] += 1
    return chosen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/home/jnicolas/cinemediavault-lab/cinevault-data/combined-barker")
    parser.add_argument("--model", default="/home/jnicolas/cinemediavault-lab/tts-models/kokoro-v1.0.onnx")
    parser.add_argument("--voices", default="/home/jnicolas/cinemediavault-lab/tts-models/voices-v1.0.bin")
    parser.add_argument("--slot-start", type=int, help="broadcast slot epoch; defaults to the next six-hour boundary")
    parser.add_argument("--minutes", type=int, default=60)
    args = parser.parse_args()
    root = Path(args.root); root.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    now = int(time.time())
    if args.slot_start:
        slot = args.slot_start
    else:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(manifest.get("timezone", "America/Denver"))
        local_now = dt.datetime.now(zone)
        next_hour = ((local_now.hour // 6) + 1) * 6
        if next_hour >= 24:
            boundary = (local_now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            boundary = local_now.replace(hour=next_hour, minute=0, second=0, microsecond=0)
        slot = int(boundary.timestamp())
    total_seconds = max(60, args.minutes * 60)
    segment_seconds = 60
    count = total_seconds // segment_seconds
    items = choose_items(manifest.get("items", []), count, slot)
    if not items:
        raise SystemExit("No playable movie/TV schedule entries in manifest")
    work = root / f"work-{slot}-{os.getpid()}"; work.mkdir()
    final = root / f"barker-{slot}.mp4"; partial = final.with_suffix(".mp4.part")
    failures = []
    kokoro = Kokoro(args.model, args.voices)
    segments = []
    rendered_items = []
    try:
        for index in range(count):
            item = items[index % len(items)]
            wav = work / f"{index:03d}.wav"; segment = work / f"{index:03d}.mp4"; text = work / f"{index:03d}.txt"
            copy = narration(item, index, manifest.get("timezone", "America/Denver"))
            voice = "am_michael" if item.get("kind") == "movie" else "af_heart"
            if any(x in str(item.get("channel", "")).lower() for x in ("kids", "family", "animation", "simpsons")):
                voice = "af_bella"
            try:
                samples, rate = kokoro.create(copy, voice=voice, speed=1.03, lang="en-us")
                sf.write(wav, samples, rate, format="WAV", subtype="PCM_16")
                render_segment(item, wav, segment, text, segment_seconds,
                               manifest.get("timezone", "America/Denver"), slot + index)
                segments.append(segment)
                rendered_items.append(item)
            except Exception as exc:
                failures.append({"title": item.get("title"), "error": str(exc)[-600:]})
                print(f"segment {index:03d} failed: {item.get('title')}: {exc}", file=sys.stderr, flush=True)
        if not segments:
            raise RuntimeError("Every promo segment failed")
        # Repeat successful segments if isolated damaged sources prevented a full hour.
        while len(segments) < count:
            source_index = len(segments) % len(rendered_items)
            segments.append(segments[source_index])
            rendered_items.append(rendered_items[source_index])
        concat = work / "concat.txt"
        concat.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments[:count]), encoding="utf-8")
        run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat), "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(partial)], timeout=600)
        duration, _ = probe(partial)
        partial.replace(final)
        programme_rows = []
        for index in range(count):
            item = rendered_items[index]
            programme_rows.append({key: item.get(key) for key in
                                   ("kind", "title", "subtitle", "summary", "poster", "airtime",
                                    "channel", "channel_number", "year", "genres", "cast")})
        status = {"slot_start": slot, "duration": duration, "filename": final.name,
                  "generated_at": int(time.time()), "segments": len(segments[:count]),
                  "programmes": programme_rows, "failures": failures}
        descriptor = root / f"barker-{slot}.json"
        temp_status = descriptor.with_suffix(".json.part")
        temp_status.write_text(json.dumps(status, indent=2), encoding="utf-8")
        temp_status.replace(descriptor)
        # current.json is a convenience pointer only for immediately-active
        # manual builds. Scheduled future reels are selected at their airtime
        # by the server, so the old broadcast stays live until the boundary.
        if slot <= int(time.time()):
            pointer = root / "current.json.part"
            pointer.write_text(json.dumps(status, indent=2), encoding="utf-8")
            pointer.replace(root / "current.json")
        old = sorted(root.glob("barker-*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in old[3:]:
            stale.unlink(missing_ok=True)
            stale.with_suffix(".json").unlink(missing_ok=True)
        print(json.dumps(status, indent=2))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
