#!/usr/bin/env python3
import os
import re
import sqlite3
import time
from pathlib import Path


DB = Path(os.environ.get("CINEVAULT_DB", "/home/jnicolas/cinevault-data/cinevault.db")).resolve()
BACKUP_DIR = Path(os.environ.get("CINEVAULT_BACKUP_DIR", str(DB.parent / "backups"))).resolve()
KEEP = int(os.environ.get("CINEVAULT_BACKUP_KEEP", "10"))


def prune_backups() -> None:
    backups = sorted(BACKUP_DIR.glob("cinevault-db-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in backups[max(1, KEEP):]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def backup_database(reason: str = "daily") -> Path:
    if not DB.is_file():
        raise FileNotFoundError(f"CineVault database missing: {DB}")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    safe_reason = re.sub(r"[^a-zA-Z0-9_-]+", "-", reason.strip().lower() or "daily").strip("-")[:32] or "daily"
    destination = BACKUP_DIR / f"cinevault-db-{time.strftime('%Y%m%d-%H%M%S')}-{safe_reason}.db"

    source = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if str(result).lower() != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")
        target.commit()
    finally:
        target.close()
        source.close()

    prune_backups()
    return destination


if __name__ == "__main__":
    print(backup_database(os.environ.get("CINEVAULT_BACKUP_REASON", "daily")))
