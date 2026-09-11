#!/usr/bin/env python3
"""Ask CineVault to back up and rebuild both virtual-channel schedules.

Adapted from the live-host operator tool of the same name for this
installer's layout: paths and the service port come from the environment
CineMediaVault itself runs under (``CINEVAULT_DB``, ``CINEVAULT_PORT``),
never from a hand-written env-file path. Run it as the service user with
that environment sourced, e.g.:

    sudo -u cinevault bash -c \\
      'set -a; source /etc/cinemediavault/cinevault.env; \\
       source /etc/cinemediavault/secrets.env 2>/dev/null; \\
       python3 /opt/cinemediavault/scripts/repair_virtual_schedules.py'

This does not re-randomize an existing schedule; it asks the running
application to validate and, where needed, extend it using real media
durations - the same "repair_all" action the Admin > Virtual Channels page
exposes, invoked here without a browser.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_DB = "/var/lib/cinemediavault/db/cinemediavault.db"
DEFAULT_PORT = "5000"


def main() -> int:
    db_path = Path(os.environ.get("CINEVAULT_DB", DEFAULT_DB))
    port = os.environ.get("CINEVAULT_PORT", DEFAULT_PORT)
    if not db_path.is_file():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 2

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with sqlite3.connect(db_path) as db:
        user = db.execute(
            "SELECT id FROM users WHERE active=1 AND is_admin=1 "
            "ORDER BY is_super_admin DESC, id LIMIT 1").fetchone()
        if not user:
            print("no active administrator account is available", file=sys.stderr)
            return 3
        db.execute(
            "INSERT INTO user_sessions(user_id, token_hash, created_at, "
            "expires_at, last_seen_at, user_agent, remote_addr) "
            "VALUES(?, ?, datetime('now'), ?, datetime('now'), "
            "'virtual-schedule-cli', '127.0.0.1')",
            (user[0], token_hash, time.time() + 300))

    try:
        request = urllib.request.Request(
            f"https://127.0.0.1:{port}/admin/vchannels",
            data=urllib.parse.urlencode({"action": "repair_all"}).encode(),
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(
                request, context=ssl._create_unverified_context(), timeout=900) as response:
            if response.status != 200:
                print(f"rebuild failed with HTTP {response.status}", file=sys.stderr)
                return 4
        print("Both virtual schedules were backed up and rebuilt with actual media durations.")
        return 0
    finally:
        with sqlite3.connect(db_path) as db:
            db.execute("DELETE FROM user_sessions WHERE token_hash=?", (token_hash,))


if __name__ == "__main__":
    raise SystemExit(main())
