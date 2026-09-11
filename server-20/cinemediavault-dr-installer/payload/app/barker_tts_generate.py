#!/usr/bin/env python3
"""Generate one cached CineMediaVault barker announcement with Kokoro ONNX."""
import argparse
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--voices", required=True)
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    kokoro = Kokoro(args.model, args.voices)
    try:
        samples, sample_rate = kokoro.create(args.text, voice=args.voice, speed=0.96, lang="en-us")
    except Exception:
        samples, sample_rate = kokoro.create(args.text, voice="af_heart", speed=0.96, lang="en-us")
    sf.write(temporary, samples, sample_rate, format="WAV", subtype="PCM_16")
    temporary.replace(output)


if __name__ == "__main__":
    main()
