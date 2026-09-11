#!/usr/bin/env python3
import argparse
from pathlib import Path

import soundfile as sf
import torch
from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

parser = argparse.ArgumentParser()
parser.add_argument("source", help="16 kHz mono PCM WAV")
parser.add_argument("target")
parser.add_argument("--window", type=float, default=5.0)
args = parser.parse_args()

categories = [
    ("[Screaming]", 0.20, ("Screaming", "Scream", "Yell", "Shout", "Gasp")),
    ("[Explosion]", 0.15, ("Explosion", "Boom", "Burst, pop")),
    ("[Gunfire]", 0.12, ("Gunshot, gunfire", "Machine gun", "Fusillade")),
    ("[Crashing / breaking]", 0.12, ("Smash, crash", "Breaking", "Glass", "Shatter")),
    ("[Siren]", 0.20, ("Siren", "Police car (siren)", "Ambulance (siren)")),
    ("[Singing]", 0.25, ("Singing", "Male singing", "Female singing", "Choir")),
    ("[Music]", 0.35, ("Music", "Background music", "Theme music")),
    ("[Applause]", 0.25, ("Applause", "Clapping")),
    ("[Laughter]", 0.25, ("Laughter", "Giggle", "Snicker")),
    ("[Crying]", 0.25, ("Crying, sobbing", "Wail, moan")),
    ("[Door slams]", 0.25, ("Slam", "Door")),
    ("[Vehicle noise]", 0.25, ("Vehicle", "Car", "Bus", "Engine")),
]


def stamp(seconds):
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1_000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


model_name = "MIT/ast-finetuned-audioset-10-10-0.4593"
extractor = AutoFeatureExtractor.from_pretrained(model_name)
model = AutoModelForAudioClassification.from_pretrained(model_name).eval()
label_to_index = {label: int(index) for index, label in model.config.id2label.items()}

source = Path(args.source).resolve()
target = Path(args.target).resolve()
temporary = target.with_suffix(target.suffix + ".tmp")
events = []
with sf.SoundFile(source) as audio:
    if audio.samplerate != 16000 or audio.channels != 1:
        raise SystemExit("Source must be 16 kHz mono PCM WAV")
    window_frames = int(args.window * audio.samplerate)
    position = 0
    with torch.inference_mode():
        while True:
            samples = audio.read(window_frames, dtype="float32", always_2d=False)
            if len(samples) < audio.samplerate:
                break
            inputs = extractor(samples, sampling_rate=audio.samplerate, return_tensors="pt")
            probabilities = torch.sigmoid(model(**inputs).logits)[0]
            matches = []
            for caption, threshold, labels in categories:
                score = max((float(probabilities[label_to_index[label]]) for label in labels if label in label_to_index), default=0.0)
                if score >= threshold:
                    matches.append((score, caption))
            if matches:
                score, caption = max(matches)
                start = position / audio.samplerate
                end = start + len(samples) / audio.samplerate
                events.append((start, end, caption, score))
            position += len(samples)
            if position and position % (window_frames * 120) == 0:
                print(f"processed={position / audio.samplerate / 60:.1f}m events={len(events)}", flush=True)

with temporary.open("w", encoding="utf-8", newline="\n") as handle:
    for index, (start, end, caption, _score) in enumerate(events, 1):
        handle.write(f"{index}\n{stamp(start)} --> {stamp(end)}\n{caption}\n\n")
temporary.replace(target)
print(f"complete events={len(events)} target={target} bytes={target.stat().st_size}")
