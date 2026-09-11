#!/usr/bin/env python3
"""Rebuild the movie and/or TV live index without starting a web server.

The library modules build their index into a module-level object and persist it
to a JSON live cache. This helper imports the module, calls its refresh, and
exits - which is what the scheduled refresh timer needs, and is far cheaper than
starting a second copy of the application.

Two safety rules are enforced here rather than in the module:

* A library root that does not exist, or that is an empty directory, is
  refused. An unmounted share is indistinguishable from an empty library to a
  directory walk, and writing that result would erase every title from the
  catalogue.
* The live cache is only replaced when the new scan actually found something,
  unless ``--allow-empty`` is passed explicitly.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

KINDS = {
    "movies": {
        "module": "media-download-library/media_download_server.py",
        "index_attr": "movie_index",
        "root_env": "MOVIE_ROOT",
        "cache_env": "MOVIE_LIVE_CACHE",
    },
    "tv": {
        "module": "tv-download-library/tv_download_server.py",
        "index_attr": "tv_index",
        "root_env": "TV_ROOT",
        "cache_env": "TV_LIVE_CACHE",
    },
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def directory_has_content(path: Path) -> bool:
    try:
        with os.scandir(path) as entries:
            for _ in entries:
                return True
    except OSError:
        return False
    return False


def previous_count(cache_path: Path, key: str) -> int:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return len(payload.get(key) or [])
    except (OSError, ValueError):
        return 0


def refresh(kind: str, app_dir: Path, *, allow_empty: bool) -> int:
    spec = KINDS[kind]
    root_text = os.environ.get(spec["root_env"], "").strip()
    if not root_text:
        print(f"{kind}: {spec['root_env']} is not set; nothing to do")
        return 0
    root = Path(root_text)
    if not root.is_dir():
        print(f"{kind}: ERROR {root} does not exist", file=sys.stderr)
        return 2
    if not directory_has_content(root) and not allow_empty:
        print(f"{kind}: REFUSED - {root} is empty. An unmounted share looks "
              f"identical to an empty library; refusing to overwrite the index.",
              file=sys.stderr)
        return 3

    module_path = app_dir / spec["module"]
    if not module_path.is_file():
        print(f"{kind}: ERROR module not found: {module_path}", file=sys.stderr)
        return 2

    # The module directory must be importable for its own sibling imports.
    sys.path.insert(0, str(module_path.parent))
    module = load_module(module_path, f"cmv_refresh_{kind}")
    index = getattr(module, spec["index_attr"])

    cache_path = Path(os.environ.get(spec["cache_env"], "")) if os.environ.get(
        spec["cache_env"]) else None
    before = previous_count(cache_path, "movies" if kind == "movies" else "shows") \
        if cache_path else 0

    index.refresh()
    found = len(getattr(index, "items", []) or [])

    if found == 0 and before > 0 and not allow_empty:
        print(f"{kind}: WARNING scan found 0 items but the previous index had "
              f"{before}. The index file was still written by the module; "
              f"investigate the mount before trusting the catalogue.",
              file=sys.stderr)
        return 4

    print(f"{kind}: {found} item(s) indexed (previously {before})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kinds", nargs="*", choices=sorted(KINDS) or None,
                        default=list(KINDS),
                        help="which libraries to refresh (default: all)")
    parser.add_argument("--app-dir", default=os.environ.get(
        "CINEVAULT_APP_DIR", "/opt/cinemediavault/app"),
        help="directory holding the CineMediaVault application modules")
    parser.add_argument("--allow-empty", action="store_true",
                        help="permit indexing an empty library root "
                             "(dangerous: use only for a genuinely empty library)")
    args = parser.parse_args()

    app_dir = Path(args.app_dir)
    worst = 0
    for kind in args.kinds:
        try:
            code = refresh(kind, app_dir, allow_empty=args.allow_empty)
        except Exception as exc:                      # noqa: BLE001
            print(f"{kind}: ERROR {exc}", file=sys.stderr)
            code = 1
        worst = max(worst, code)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
