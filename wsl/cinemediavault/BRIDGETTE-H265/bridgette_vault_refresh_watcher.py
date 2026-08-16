#!/usr/bin/env python3
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path

MANIFEST = Path("/mnt/c/DATA/BRIDGETTE-H265/completed.csv")
LOG = Path("/mnt/c/DATA/BRIDGETTE-H265/vault-refresh.log")
URL = "http://127.0.0.1:8097/internal/refresh"


def log(message):
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")


def main():
    previous = MANIFEST.stat().st_mtime_ns if MANIFEST.exists() else 0
    log("Vault refresh watcher started")
    while True:
        current = MANIFEST.stat().st_mtime_ns if MANIFEST.exists() else 0
        if current != previous:
            try:
                with urllib.request.urlopen(URL, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                log(f"Vault refreshed count={payload.get('count', 'unknown')}")
                previous = current
            except Exception as exc:
                log(f"Vault refresh failed: {exc}")
        time.sleep(10)


if __name__ == "__main__":
    main()
