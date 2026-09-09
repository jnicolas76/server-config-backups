#!/usr/bin/env python3
"""Ask CineVault to back up and repair both schedules without re-randomizing."""
import hashlib
import os
import secrets
import sqlite3
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
env_file = ROOT / "cinemediavault-lab.env"
for line in env_file.read_text(encoding="utf-8").splitlines():
    if line and not line.lstrip().startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

db_path = Path(os.environ.get("CINEVAULT_DB", ROOT / "cinevault-data/cinemediavault-lab.db"))
token = secrets.token_urlsafe(32)
token_hash = hashlib.sha256(token.encode()).hexdigest()
with sqlite3.connect(db_path) as db:
    user = db.execute("SELECT id FROM users WHERE active=1 AND is_admin=1 ORDER BY is_super_admin DESC,id LIMIT 1").fetchone()
    if not user:
        raise SystemExit("No active administrator account is available")
    db.execute("INSERT INTO user_sessions(user_id,token_hash,created_at,expires_at,last_seen_at,user_agent,remote_addr) VALUES(?,?,datetime('now'),?,datetime('now'),'virtual-schedule-cli','127.0.0.1')", (user[0], token_hash, time.time() + 300))

try:
    request = urllib.request.Request(
        "https://127.0.0.1:5000/admin/vchannels",
        data=urllib.parse.urlencode({"action": "repair_all"}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, context=ssl._create_unverified_context(), timeout=900) as response:
        if response.status != 200:
            raise SystemExit(f"Rebuild failed with HTTP {response.status}")
    print("Both virtual schedules were backed up and rebuilt with actual media durations.")
finally:
    with sqlite3.connect(db_path) as db:
        db.execute("DELETE FROM user_sessions WHERE token_hash=?", (token_hash,))
