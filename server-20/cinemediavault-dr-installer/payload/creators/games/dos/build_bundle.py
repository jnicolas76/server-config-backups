#!/usr/bin/env python3
"""Build a js-dos bundle for Earl Weaver Baseball."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


DOSBOX_CONF = """[sdl]
autolock=false
fullscreen=false
output=surface
waitonerror=true
usescancodes=true

[dosbox]
machine=svga_s3
memsize=16

[cpu]
core=auto
cputype=auto
cycles=auto
cycleup=500
cycledown=500

[mixer]
nosound=false
rate=44100
blocksize=1024
prebuffer=20

[render]
frameskip=0
aspect=false
scaler=none

[sblaster]
sbtype=sb16
sbbase=220
irq=7
dma=1
hdma=5
oplmode=auto

[speaker]
pcspeaker=true
tandy=auto
disney=true

[joystick]
joysticktype=auto
timed=true

[dos]
xms=true
ems=true
umb=true
keyboardlayout=auto

[autoexec]
@echo off
mount c .
c:
WEAVER.EXE F V
"""


def default_game_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "source" / "EARL2025"


def build(source: Path, output: Path) -> None:
    source = source.resolve()
    required = (source / "WEAVER.EXE", source / "EWB.BAT")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing required game files: " + ", ".join(missing))

    game_extensions = {".BAT", ".COM", ".DAT", ".EXE", ".G", ".IEA"}
    files = sorted(
        path
        for path in source.iterdir()
        if path.is_file()
        and path.suffix.upper() in game_extensions
        and path.name.upper() not in {"INSTALL.EXE"}
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()

    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        directory = zipfile.ZipInfo(".jsdos/")
        directory.external_attr = 0o40775 << 16
        bundle.writestr(directory, b"")
        bundle.writestr(".jsdos/dosbox.conf", DOSBOX_CONF)
        bundle.writestr(".jsdos/readme.txt", "Earl Weaver Baseball 2025 browser bundle\r\n")
        bundle.writestr("dosbox.conf", "[autoexec]\r\nWEAVER.EXE F V\r\n")
        save_directory = zipfile.ZipInfo("EWSAV/")
        save_directory.external_attr = 0o40775 << 16
        bundle.writestr(save_directory, b"")
        for path in files:
            bundle.write(path, path.relative_to(source).as_posix())

    with zipfile.ZipFile(temporary) as bundle:
        names = set(bundle.namelist())
        for required_name in (".jsdos/dosbox.conf", "WEAVER.EXE", "EWB.BAT"):
            if required_name not in names:
                raise RuntimeError(f"Bundle validation failed: {required_name} is absent")

    temporary.replace(output)
    print(f"Built {output} from {len(files)} game files ({output.stat().st_size:,} bytes)")


def main() -> int:
    web_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=default_game_dir())
    parser.add_argument("--output", type=Path, default=web_root / "games" / "earl2025.jsdos")
    args = parser.parse_args()
    build(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
