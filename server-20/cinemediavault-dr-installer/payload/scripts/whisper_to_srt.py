#!/usr/bin/env python3
import argparse
import os
import time
from pathlib import Path

from faster_whisper import WhisperModel

def stamp(seconds):
    milliseconds = max(0, int(round(float(seconds) * 1000)))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"

parser = argparse.ArgumentParser()
parser.add_argument("source")
parser.add_argument("target")
parser.add_argument("--model", default="base.en")
parser.add_argument("--no-vad", action="store_true")
parser.add_argument("--offset", type=float, default=0.0)
parser.add_argument("--language", default="en")
parser.add_argument("--task", choices=("transcribe", "translate"), default="transcribe")
args = parser.parse_args()
source = Path(args.source).resolve()
target = Path(args.target).resolve()
temporary = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")

started = time.time()
print(f"Loading Whisper model {args.model}", flush=True)
model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=max(1, os.cpu_count() or 4))
transcribe_options = {
    "language": args.language,
    "task": args.task,
    "beam_size": 5,
    "word_timestamps": True,
    "condition_on_previous_text": False,
    "vad_filter": not args.no_vad,
}
if not args.no_vad:
    transcribe_options["vad_parameters"] = {"min_silence_duration_ms": 500}
segments, info = model.transcribe(str(source), **transcribe_options)
print(f"Detected language={info.language} probability={info.language_probability:.3f} task={args.task} duration={info.duration:.1f}s", flush=True)
count = 0
with temporary.open("w", encoding="utf-8", newline="\n") as handle:
    for count, segment in enumerate(segments, 1):
        text = " ".join(segment.text.strip().split())
        if not text:
            continue
        start = segment.start
        end = segment.end
        words = list(segment.words or [])
        if words:
            start = words[0].start
            end = words[-1].end
        if segment.end - segment.start > 15 and len(words) > 1:
            gaps = [(words[index].start - words[index - 1].end, index) for index in range(1, len(words))]
            largest_gap, following_word = max(gaps, default=(0, 0))
            if largest_gap > 5:
                start = words[following_word].start
        readable_duration = max(1.2, min(7.0, len(text) / 12.0))
        if end <= start or end - start > 8:
            end = start + readable_duration
        handle.write(f"{count}\n{stamp(start + args.offset)} --> {stamp(end + args.offset)}\n{text}\n\n")
        if count % 50 == 0:
            print(f"segments={count} media={segment.end/60:.1f}m elapsed={(time.time()-started)/60:.1f}m", flush=True)
temporary.replace(target)
print(f"complete segments={count} target={target} bytes={target.stat().st_size} elapsed={(time.time()-started)/60:.1f}m", flush=True)
