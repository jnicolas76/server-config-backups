#!/usr/bin/env python3
"""Download the browser js-dos player and DOSBox WebAssembly runtime."""

from __future__ import annotations

import io
import json
import ssl
import argparse
import tarfile
import urllib.request
import urllib.error
from pathlib import Path


PLAYER_BASE = "https://v8.js-dos.com/latest"
NPM_METADATA = "https://registry.npmjs.org/emulators/latest"


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "EWB-browser-builder/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(request, timeout=60, context=context) as response:
            return response.read()


def setup(root: Path, force: bool = False) -> None:
    runtime = root / "runtime"
    emulators = runtime / "emulators"
    required_paths = (
        runtime / "js-dos.js",
        runtime / "js-dos.css",
        emulators / "emulators.js",
        emulators / "wdosbox.js",
        emulators / "wdosbox.wasm",
        emulators / "wlibzip.js",
        emulators / "wlibzip.wasm",
    )
    if not force and all(path.is_file() for path in required_paths):
        print("js-dos runtime is already installed; using local files")
        return
    emulators.mkdir(parents=True, exist_ok=True)
    (runtime / "js-dos.js").write_bytes(download(f"{PLAYER_BASE}/js-dos.js"))
    (runtime / "js-dos.css").write_bytes(download(f"{PLAYER_BASE}/js-dos.css"))

    metadata = json.loads(download(NPM_METADATA))
    archive = download(metadata["dist"]["tarball"])
    copied = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        for member in package.getmembers():
            prefix = "package/dist/"
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            relative = member.name[len(prefix) :]
            if "/" in relative or Path(relative).suffix not in {".js", ".wasm"}:
                continue
            source = package.extractfile(member)
            if source is None:
                continue
            (emulators / relative).write_bytes(source.read())
            copied.append(relative)

    required = {"emulators.js", "wdosbox.js", "wdosbox.wasm", "wlibzip.js", "wlibzip.wasm"}
    missing = sorted(required.difference(copied))
    if missing:
        raise RuntimeError("Runtime is incomplete: " + ", ".join(missing))
    print(f"Installed js-dos runtime {metadata['version']} ({len(copied)} emulator files)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="download the runtime again")
    args = parser.parse_args()
    setup(Path(__file__).resolve().parents[1], force=args.force)
