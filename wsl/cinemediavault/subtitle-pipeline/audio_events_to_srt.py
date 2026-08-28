#!/usr/bin/env python3
"""Conservative AudioSet event captions, using the parameters tested on Speed."""
import argparse
from pathlib import Path

import soundfile as sf
import torch
from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

CATEGORIES = [
    ("[Screaming]", .20, ("Screaming", "Scream", "Yell", "Shout", "Gasp"), 10),
    ("[Explosion]", .15, ("Explosion", "Boom", "Burst, pop"), 10),
    ("[Gunfire]", .12, ("Gunshot, gunfire", "Machine gun", "Fusillade"), 10),
    ("[Crashing / breaking]", .12, ("Smash, crash", "Breaking", "Glass", "Shatter"), 10),
    ("[Siren]", .20, ("Siren", "Police car (siren)", "Ambulance (siren)"), 15),
    ("[Singing]", .25, ("Singing", "Male singing", "Female singing", "Choir"), 15),
    ("[Music]", .35, ("Music", "Background music", "Theme music"), 30),
    ("[Applause]", .25, ("Applause", "Clapping"), 15),
    ("[Laughter]", .25, ("Laughter", "Giggle", "Snicker"), 15),
    ("[Crying]", .25, ("Crying, sobbing", "Wail, moan"), 15),
    ("[Door slams]", .25, ("Slam", "Door"), 10),
    ("[Vehicle noise]", .25, ("Vehicle", "Car", "Bus", "Engine"), 20),
]


def stamp(seconds):
    ms = int(max(0, seconds) * 1000)
    h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source"); parser.add_argument("target"); parser.add_argument("--window", type=float, default=5)
    args = parser.parse_args(); model_name = "MIT/ast-finetuned-audioset-10-10-0.4593"
    extractor = AutoFeatureExtractor.from_pretrained(model_name)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModelForAudioClassification.from_pretrained(model_name).to(device).eval()
    label_index = {label: int(index) for index, label in model.config.id2label.items()}
    events, last = [], {}
    with sf.SoundFile(args.source) as audio:
        if audio.samplerate != 16000 or audio.channels != 1:
            raise SystemExit("audio must be 16 kHz mono")
        frames = int(args.window * audio.samplerate); position = 0
        with torch.inference_mode():
            while True:
                samples = audio.read(frames, dtype="float32", always_2d=False)
                if len(samples) < audio.samplerate: break
                inputs = {k: v.to(device) for k, v in extractor(samples, sampling_rate=16000, return_tensors="pt").items()}
                probabilities = torch.sigmoid(model(**inputs).logits)[0]
                choices = []
                for caption, threshold, labels, cooldown in CATEGORIES:
                    score = max((float(probabilities[label_index[x]]) for x in labels if x in label_index), default=0)
                    if score >= threshold and position / 16000 - last.get(caption, -1e9) >= cooldown:
                        choices.append((score, caption))
                if choices:
                    _, caption = max(choices); start = position / 16000
                    events.append((start, start + len(samples) / 16000, caption)); last[caption] = start
                position += len(samples)
    target = Path(args.target); temp = target.with_suffix(target.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as out:
        for i, (start, end, caption) in enumerate(events, 1):
            out.write(f"{i}\n{stamp(start)} --> {stamp(end)}\n{caption}\n\n")
    temp.replace(target); print(f"events={len(events)} device={device}")


if __name__ == "__main__": main()
