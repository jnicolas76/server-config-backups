#!/usr/bin/env python3
import json
import re
from pathlib import Path


LIBRARY = Path("/home/jnicolas/Data9/comic-library")
SOURCE = LIBRARY / "collections" / "spy-vs-spy"
TARGET = LIBRARY / "collections" / "mad-magazine"


def atomic_write(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main():
    if not SOURCE.is_dir():
        raise SystemExit(f"Source collection not found: {SOURCE}")
    if TARGET.exists():
        raise SystemExit(f"Target collection already exists: {TARGET}")

    SOURCE.rename(TARGET)
    config_path = TARGET / "gallery_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["title"] = "Mad Magazine"
    config["slug"] = "mad-magazine"
    atomic_write(config_path, json.dumps(config, indent=2))

    index_path = TARGET / "index.html"
    document = index_path.read_text(encoding="utf-8")
    document = document.replace(
        "<title>Spy vs. Spy Library</title>",
        "<title>Mad Magazine Library</title>",
    ).replace("<h1>Spy vs. Spy</h1>", "<h1>Mad Magazine</h1>")
    atomic_write(index_path, document)

    hub_path = LIBRARY / "index.html"
    hub = hub_path.read_text(encoding="utf-8")
    match = re.search(r"const collections=(\[.*?\]),nav=", hub, re.DOTALL)
    if not match:
        raise RuntimeError("Could not locate collection data in library index")
    collections = json.loads(match.group(1))
    found = False
    for item in collections:
        if item["slug"] == "spy-vs-spy":
            item["slug"] = "mad-magazine"
            item["title"] = "Mad Magazine"
            found = True
    if not found:
        raise RuntimeError("Spy vs. Spy hub entry was not found")
    collections.sort(key=lambda item: item["title"].casefold())
    replacement = "const collections=" + json.dumps(
        collections, ensure_ascii=True, separators=(",", ":")
    ) + ",nav="
    hub = hub[:match.start()] + replacement + hub[match.end():]
    atomic_write(hub_path, hub)
    print("Promoted Spy vs. Spy to the Mad Magazine comic collection")


if __name__ == "__main__":
    main()
