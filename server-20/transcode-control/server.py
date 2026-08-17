#!/usr/bin/env python3
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("DATABASE", ROOT / "data" / "transcode-control.db"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8126"))
USER = os.environ.get("DASHBOARD_USER", "jnicolas")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "change-me")
AGENT_SECRET = os.environ.get("AGENT_SECRET", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET") or AGENT_SECRET


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript("""
      PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS snapshots(
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL,
        movie_free INTEGER DEFAULT 0, tv_free INTEGER DEFAULT 0,
        local_free INTEGER DEFAULT 0, saved_bytes INTEGER DEFAULT 0);
      CREATE TABLE IF NOT EXISTS commands(
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, action TEXT NOT NULL,
        payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        result TEXT DEFAULT '', completed_at TEXT DEFAULT '');
      CREATE TABLE IF NOT EXISTS audit(
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, actor TEXT NOT NULL,
        action TEXT NOT NULL, detail TEXT NOT NULL);
    """)
    return con


def init_db():
    with db() as con:
        con.execute("DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM snapshots ORDER BY id DESC LIMIT 10000)")


def json_body(handler):
    size = int(handler.headers.get("Content-Length", "0"))
    return json.loads(handler.rfile.read(size) or b"{}")


def signed(value):
    sig = hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"


def valid_signed(value):
    try:
        raw, sig = value.rsplit(".", 1)
        expected = hmac.new(SESSION_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
        return raw if hmac.compare_digest(sig, expected) else None
    except ValueError:
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "TranscodeControl/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def send_json(self, data, status=200):
        raw = json.dumps(data, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_file(self, path, mime):
        raw = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def is_agent(self):
        return bool(AGENT_SECRET) and hmac.compare_digest(self.headers.get("X-Agent-Secret", ""), AGENT_SECRET)

    def is_user(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        token = jar.get("tc_session")
        return bool(token and valid_signed(token.value))

    def require_user(self):
        if self.is_user():
            return True
        self.send_json({"error": "authentication required"}, 401)
        return False

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self.send_file(ROOT / "static" / "index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self.send_file(ROOT / "static" / "app.js", "application/javascript; charset=utf-8")
        if path == "/style.css":
            return self.send_file(ROOT / "static" / "style.css", "text/css; charset=utf-8")
        if path == "/api/session":
            return self.send_json({"authenticated": self.is_user(), "user": USER if self.is_user() else ""})
        if path == "/api/dashboard":
            if not self.require_user(): return
            with db() as con:
                row = con.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
                history = con.execute("SELECT created_at,movie_free,tv_free,local_free,saved_bytes FROM snapshots ORDER BY id DESC LIMIT 96").fetchall()
                commands = con.execute("SELECT * FROM commands ORDER BY id DESC LIMIT 30").fetchall()
            payload = json.loads(row["payload"]) if row else {}
            payload["snapshot_at"] = row["created_at"] if row else None
            payload["history"] = [dict(x) for x in reversed(history)]
            payload["commands"] = [dict(x) for x in commands]
            return self.send_json(payload)
        if path == "/api/agent/commands":
            if not self.is_agent(): return self.send_json({"error": "forbidden"}, 403)
            with db() as con:
                rows = con.execute("SELECT * FROM commands WHERE status='pending' ORDER BY id LIMIT 10").fetchall()
                for row in rows:
                    con.execute("UPDATE commands SET status='sent' WHERE id=?", (row["id"],))
            return self.send_json({"commands": [dict(x) for x in rows]})
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/login":
            data = json_body(self)
            if not (hmac.compare_digest(str(data.get("username", "")), USER) and hmac.compare_digest(str(data.get("password", "")), PASSWORD)):
                return self.send_json({"error": "invalid credentials"}, 403)
            token = signed(f"{USER}:{int(time.time())}:{secrets.token_hex(12)}")
            self.send_response(204)
            self.send_header("Set-Cookie", f"tc_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200")
            self.end_headers(); return
        if path == "/api/logout":
            self.send_response(204)
            self.send_header("Set-Cookie", "tc_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            self.end_headers(); return
        if path == "/api/agent/report":
            if not self.is_agent(): return self.send_json({"error": "forbidden"}, 403)
            data = json_body(self)
            disks = data.get("disks", {})
            with db() as con:
                con.execute("INSERT INTO snapshots(created_at,payload,movie_free,tv_free,local_free,saved_bytes) VALUES(?,?,?,?,?,?)",
                    (now(), json.dumps(data), disks.get("movies",{}).get("free",0), disks.get("tv",{}).get("free",0), disks.get("local",{}).get("free",0), data.get("stats",{}).get("saved_bytes",0)))
            return self.send_json({"ok": True})
        if path.startswith("/api/agent/commands/"):
            if not self.is_agent(): return self.send_json({"error": "forbidden"}, 403)
            command_id = int(path.rsplit("/", 1)[-1]); data = json_body(self)
            with db() as con:
                con.execute("UPDATE commands SET status=?,result=?,completed_at=? WHERE id=?", (data.get("status","done"), data.get("result",""), now(), command_id))
            return self.send_json({"ok": True})
        if path == "/api/commands":
            if not self.require_user(): return
            data = json_body(self); action = str(data.get("action", ""))
            allowed = {"move","remove","add","set_profile","start","stop","refresh"}
            if action not in allowed: return self.send_json({"error": "invalid action"}, 400)
            payload = data.get("payload", {})
            with db() as con:
                cur = con.execute("INSERT INTO commands(created_at,action,payload) VALUES(?,?,?)", (now(), action, json.dumps(payload)))
                con.execute("INSERT INTO audit(created_at,actor,action,detail) VALUES(?,?,?,?)", (now(), USER, action, json.dumps(payload)))
            return self.send_json({"ok": True, "id": cur.lastrowid}, 202)
        self.send_error(404)


if __name__ == "__main__":
    if not AGENT_SECRET or PASSWORD == "change-me":
        raise SystemExit("Set AGENT_SECRET and DASHBOARD_PASSWORD before starting")
    init_db()
    print(f"Transcode Control listening on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

