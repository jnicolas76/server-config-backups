#!/usr/bin/env python3
import argparse
import base64
import hashlib
import hmac
import html
import mimetypes
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MEDIA_ROOT = Path(os.environ.get("BRIDGETTE_MEDIA_ROOT", "/mnt/d/BRIDGE/Bridgette B - MegaPack")).resolve()
SCREENSHOT_DIR = Path(os.environ.get("BRIDGETTE_SCREENSHOT_DIR", MEDIA_ROOT / "Bridgette B (screenshots)")).resolve()
POSTER_DIR = Path(os.environ.get("BRIDGETTE_POSTER_DIR", "/mnt/c/DATA/bridgette-vault/posters")).resolve()
PASSWORD = os.environ.get("BRIDGETTE_PASSWORD", "CHANGE_ME")
SESSION_SECRET = os.environ.get("BRIDGETTE_SESSION_SECRET", "change-this-local-secret")
SESSION_TTL_SECONDS = int(os.environ.get("BRIDGETTE_SESSION_TTL_SECONDS", "43200"))
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".f4v", ".webm", ".m4v"}


@dataclass
class MediaItem:
    id: int
    title: str
    path: Path
    rel_path: str
    size: int
    modified: float
    thumb: str


def human_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(size)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{size} B"


def natural_key(value: str):
    import re

    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def title_bucket(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return "0-9"
    first = stripped[0].upper()
    return first if "A" <= first <= "Z" else "0-9"


def clean_title(path: Path) -> str:
    title = path.stem.replace("_", " ").replace(".", " ")
    return " ".join(title.split())


def scan_media() -> list[MediaItem]:
    items: list[MediaItem] = []
    candidates = []
    for path in MEDIA_ROOT.iterdir():
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            candidates.append(path)
    candidates.sort(key=lambda path: natural_key(path.name))
    for index, path in enumerate(candidates, start=1):
        try:
            stat = path.stat()
            rel_path = str(path.relative_to(MEDIA_ROOT))
        except OSError:
            continue
        poster_path = POSTER_DIR / f"{path.name}.jpg"
        thumb_path = SCREENSHOT_DIR / f"{path.name}.jpg"
        thumb_source = poster_path if poster_path.is_file() else thumb_path
        thumb = f"/thumb/{index}" if thumb_source.is_file() else ""
        items.append(MediaItem(index, clean_title(path), path, rel_path, stat.st_size, stat.st_mtime, thumb))
    return items


ITEMS: list[MediaItem] = []
ITEM_BY_ID: dict[int, MediaItem] = {}


def refresh_index() -> None:
    global ITEMS, ITEM_BY_ID
    ITEMS = scan_media()
    ITEM_BY_ID = {item.id: item for item in ITEMS}


def safe_item(value: str) -> MediaItem:
    try:
        item = ITEM_BY_ID[int(value)]
    except Exception:
        raise FileNotFoundError("Media item not found")
    resolved = item.path.resolve()
    if not str(resolved).startswith(str(MEDIA_ROOT) + os.sep):
        raise PermissionError("Refusing path outside media root")
    if not resolved.is_file():
        raise FileNotFoundError("Media file missing")
    return item


def sign_session(expires: int) -> str:
    payload = str(expires).encode("utf-8")
    digest = hmac.new(SESSION_SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
    token = payload + b"." + base64.urlsafe_b64encode(digest)
    return base64.urlsafe_b64encode(token).decode("ascii")


def verify_session(token: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii"))
        payload, signature = decoded.split(b".", 1)
        expires = int(payload.decode("utf-8"))
    except Exception:
        return False
    if expires < int(time.time()):
        return False
    expected = base64.urlsafe_b64encode(hmac.new(SESSION_SECRET.encode("utf-8"), payload, hashlib.sha256).digest())
    return hmac.compare_digest(signature, expected)


LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Private Vault</title>
  <style>
    :root { color-scheme:dark; --bg:#070910; --text:#f8fbff; --muted:#b8c2d0; --accent:#d9ff4a; --line:rgba(255,255,255,.18); }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; display:grid; place-items:center; font-family:Arial, Helvetica, sans-serif; background:radial-gradient(circle at 74% 18%, rgba(72,127,255,.32), transparent 32%), radial-gradient(circle at 20% 30%, rgba(191,46,146,.34), transparent 38%), var(--bg); color:var(--text); padding:20px; }
    .login { width:min(420px, 100%); border:1px solid var(--line); background:rgba(11,15,24,.78); backdrop-filter:blur(14px); border-radius:14px; padding:24px; box-shadow:0 28px 80px rgba(0,0,0,.42); }
    h1 { margin:0 0 6px; font-size:30px; }
    p { margin:0 0 20px; color:var(--muted); line-height:1.4; }
    input { width:100%; height:46px; border-radius:10px; border:1px solid rgba(255,255,255,.22); background:#171e2a; color:#fff; padding:0 14px; font-size:16px; outline:none; }
    input:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(217,255,74,.14); }
    button { width:100%; height:44px; margin-top:12px; border:0; border-radius:999px; background:var(--accent); color:#10131a; font-weight:900; font-size:15px; cursor:pointer; }
    .error { min-height:20px; margin-top:12px; color:#ff9ea7; font-weight:800; font-size:13px; }
  </style>
</head>
<body>
  <form class="login" method="post" action="/login">
    <h1>Private Vault</h1>
    <p>Enter the password to open the gallery.</p>
    <input type="password" name="password" placeholder="Password" autofocus autocomplete="current-password">
    <button type="submit">Unlock</button>
    <div class="error">{{ERROR}}</div>
  </form>
</body>
</html>
"""


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Private Vault</title>
  <style>
    :root { color-scheme:dark; --bg:#080a12; --panel:#151b26; --line:#2a3441; --text:#f8fbff; --muted:#aeb9c6; --accent:#d9ff4a; }
    * { box-sizing:border-box; }
    html { scroll-behavior:smooth; }
    body { margin:0; font-family:Arial, Helvetica, sans-serif; background:var(--bg); color:var(--text); overflow-x:hidden; }
    .hero { position:relative; min-height:46vh; overflow:hidden; display:flex; flex-direction:column; border-bottom:1px solid var(--line); }
    .hero::before { content:""; position:absolute; inset:0; background:radial-gradient(circle at 82% 10%, rgba(83,132,255,.45), transparent 34%), radial-gradient(circle at 18% 28%, rgba(199,52,145,.46), transparent 38%), linear-gradient(110deg, rgba(8,10,18,.98), rgba(29,13,52,.9) 44%, rgba(0,94,166,.7)); }
    .hero::after { content:""; position:absolute; inset:0; background:linear-gradient(90deg, rgba(8,10,18,.88), rgba(8,10,18,.56) 48%, rgba(8,10,18,.34)); z-index:2; }
    .poster-cloud { position:absolute; left:-70px; right:-70px; top:44px; display:grid; grid-template-columns:repeat(12, minmax(72px, 1fr)); gap:11px; transform:rotate(8deg); opacity:.68; z-index:1; }
    .poster-cloud img { width:100%; aspect-ratio:16/9; object-fit:cover; border-radius:8px; box-shadow:0 18px 36px rgba(0,0,0,.45); }
    .poster-cloud img:nth-child(3n) { transform:translateY(28px); }
    .poster-cloud img:nth-child(5n) { transform:translateY(48px); }
    header { position:relative; z-index:4; display:flex; justify-content:space-between; align-items:center; gap:16px; padding:18px 22px; }
    .brand { font-weight:900; font-size:28px; }
    .logout { color:#fff; text-decoration:none; border:1px solid rgba(255,255,255,.22); border-radius:999px; padding:8px 12px; background:rgba(255,255,255,.10); font-weight:800; font-size:13px; }
    .hero-main { position:relative; z-index:3; padding:44px 22px 36px; max-width:960px; }
    h1 { margin:0; font-size:clamp(30px,5vw,58px); line-height:1; }
    .tagline { margin:12px 0 18px; max-width:560px; color:#edf4ff; font-weight:800; line-height:1.35; }
    .pills { display:flex; flex-wrap:wrap; gap:8px; }
    .pill { min-height:30px; display:inline-flex; align-items:center; padding:0 11px; border-radius:999px; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.16); font-size:12px; font-weight:900; }
    .toolbar { position:sticky; top:0; z-index:5; display:grid; grid-template-columns:minmax(220px, 540px) auto; gap:12px; align-items:center; background:rgba(8,10,18,.94); border-bottom:1px solid var(--line); backdrop-filter:blur(10px); padding:12px 58px 12px 16px; }
    input[type=search] { width:100%; height:42px; border-radius:9px; border:1px solid #3a4552; background:#202734; color:#fff; padding:0 13px; font-size:15px; outline:none; }
    button { height:42px; border:1px solid var(--accent); border-radius:9px; background:var(--accent); color:#10131a; font-weight:900; padding:0 13px; cursor:pointer; }
    .page-shell { display:grid; grid-template-columns:minmax(0, 1fr) 46px; gap:10px; padding:18px 12px 42px 18px; }
    .letter-rail { position:sticky; top:76px; align-self:start; display:flex; flex-direction:column; gap:4px; max-height:calc(100vh - 90px); overflow:auto; padding:6px 4px; background:rgba(21,27,38,.78); border:1px solid var(--line); border-radius:9px; }
    .letter { width:30px; min-height:26px; padding:0; border-color:transparent; background:transparent; color:#cdd7e3; border-radius:6px; font-size:11px; }
    .letter.active { border-color:var(--accent); background:var(--accent); color:#10131a; }
    .letter-section { scroll-margin-top:92px; margin-bottom:30px; }
    .letter-section h2 { margin:0 0 12px; color:#d7e4f0; font-size:17px; border-bottom:1px solid var(--line); padding-bottom:8px; }
    .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(190px, 1fr)); gap:18px 15px; align-items:start; }
    .card { min-width:0; }
    .poster-link { display:block; aspect-ratio:16/9; background:#05070a; border:1px solid #222b36; border-radius:8px; overflow:hidden; box-shadow:0 12px 24px rgba(0,0,0,.32); }
    .poster { width:100%; height:100%; object-fit:cover; display:block; }
    .missing { width:100%; height:100%; display:grid; place-items:center; color:#8894a3; font-size:12px; font-weight:900; background:linear-gradient(145deg,#202833,#090d12); }
    .info { padding:8px 2px 0; }
    .title { font-weight:900; font-size:13px; line-height:1.25; overflow-wrap:anywhere; }
    .meta { color:var(--muted); font-size:12px; margin-top:4px; }
    .hidden { display:none; }
    @media (max-width:760px) {
      .poster-cloud { grid-template-columns:repeat(8, minmax(70px, 1fr)); left:-180px; right:-180px; opacity:.48; }
      .toolbar { grid-template-columns:1fr; padding-right:12px; }
      .page-shell { grid-template-columns:minmax(0,1fr) 40px; padding:12px 8px 30px 10px; }
      .grid { grid-template-columns:repeat(2, minmax(0, 1fr)); gap:15px 10px; }
      .brand { font-size:23px; }
      .hero-main { padding-top:28px; }
    }
  </style>
</head>
<body>
  <section class="hero">
    <div class="poster-cloud" aria-hidden="true">{{POSTER_CLOUD}}</div>
    <header><div class="brand">Private Vault</div><a class="logout" href="/logout">Lock</a></header>
    <div class="hero-main">
      <h1>Private video gallery.</h1>
      <div class="tagline">Browse local files from the D: drive with password-protected access, poster thumbnails, direct playback, and downloads.</div>
      <div class="pills"><div class="pill">{{COUNT}} videos</div><div class="pill">{{TOTAL_SIZE}}</div><div class="pill">Local WSL host</div></div>
    </div>
  </section>
  <div class="toolbar">
    <input id="search" type="search" placeholder="Search selections" autofocus>
    <button id="refresh">Refresh</button>
  </div>
  <div class="page-shell">
    <main id="top">{{SECTIONS}}</main>
    <nav class="letter-rail" aria-label="Jump to letter"><button class="letter active" data-letter="ALL">All</button>{{LETTERS}}</nav>
  </div>
  <script>
    const search = document.getElementById("search");
    const cards = [...document.querySelectorAll(".card")];
    const sections = [...document.querySelectorAll(".letter-section")];
    function applyFilters() {
      const q = search.value.trim().toLowerCase();
      for (const card of cards) card.classList.toggle("hidden", q && !card.dataset.title.includes(q));
      for (const section of sections) section.classList.toggle("hidden", section.querySelectorAll(".card:not(.hidden)").length === 0);
    }
    search.addEventListener("input", applyFilters);
    document.querySelectorAll(".letter").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll(".letter").forEach((item) => item.classList.toggle("active", item === button));
        const target = button.dataset.letter === "ALL" ? document.getElementById("top") : document.getElementById(`section-${button.dataset.letter}`);
        if (target) target.scrollIntoView({ behavior:"smooth", block:"start" });
      });
    });
    document.getElementById("refresh").addEventListener("click", async () => {
      await fetch("/refresh");
      location.reload();
    });
  </script>
</body>
</html>
"""


DETAIL_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{TITLE}}</title>
  <style>
    :root { color-scheme:dark; --bg:#05070b; --text:#f8fbff; --muted:#aeb9c6; --accent:#d9ff4a; --line:rgba(255,255,255,.16); }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; background:#05070b; color:var(--text); font-family:Arial, Helvetica, sans-serif; }
    header { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:14px 18px; background:#0b1017; border-bottom:1px solid var(--line); }
    a { color:inherit; }
    .back,.download { text-decoration:none; font-weight:900; border-radius:999px; padding:8px 12px; background:rgba(255,255,255,.12); border:1px solid var(--line); }
    .download { background:var(--accent); color:#10131a; border:0; }
    main { width:min(1240px,100%); margin:0 auto; padding:16px; }
    video { width:100%; max-height:calc(100vh - 180px); background:#000; border-radius:8px; border:1px solid var(--line); box-shadow:0 26px 80px rgba(0,0,0,.44); }
    h1 { margin:16px 0 8px; font-size:clamp(24px,4vw,44px); line-height:1.05; overflow-wrap:anywhere; }
    .meta { color:var(--muted); }
  </style>
</head>
<body>
  <header><a class="back" href="/">← Gallery</a><a class="download" href="{{DOWNLOAD}}">Download</a></header>
  <main>
    <video controls autoplay playsinline src="{{PLAY}}"></video>
    <h1>{{TITLE}}</h1>
    <div class="meta">{{SIZE}}</div>
  </main>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "PrivateVault/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def authenticated(self) -> bool:
        jar = cookies.SimpleCookie(self.headers.get("Cookie"))
        token = jar.get("vault_session")
        return bool(token and verify_session(token.value))

    def require_auth(self) -> bool:
        if self.authenticated():
            return True
        self.redirect("/login")
        return False

    def redirect(self, target: str):
        self.send_response(302)
        self.send_header("Location", target)
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/internal/refresh" and self.client_address[0] in {"127.0.0.1", "::1"}:
                refresh_index()
                return self.json_response({"ok": True, "count": len(ITEMS)})
            if path == "/login":
                return self.login_page("")
            if path == "/logout":
                return self.logout()
            if not self.require_auth():
                return
            if path == "/":
                return self.gallery()
            if path == "/refresh":
                refresh_index()
                return self.json_response({"ok": True, "count": len(ITEMS)})
            if path.startswith("/item/"):
                return self.detail(path.rsplit("/", 1)[-1])
            if path.startswith("/play/"):
                return self.serve_media(path.rsplit("/", 1)[-1], "inline")
            if path.startswith("/download/"):
                return self.serve_media(path.rsplit("/", 1)[-1], "attachment")
            if path.startswith("/thumb/"):
                return self.serve_thumb(path.rsplit("/", 1)[-1])
            self.send_error(404)
        except FileNotFoundError as exc:
            self.send_error(404, str(exc))
        except PermissionError as exc:
            self.send_error(403, str(exc))
        except BrokenPipeError:
            pass

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path != "/login":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(min(length, 4096)).decode("utf-8", "ignore")
        fields = urllib.parse.parse_qs(body)
        if secrets.compare_digest(fields.get("password", [""])[0], PASSWORD):
            expires = int(time.time()) + SESSION_TTL_SECONDS
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"vault_session={sign_session(expires)}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_TTL_SECONDS}")
            self.end_headers()
            return
        self.login_page("Incorrect password.")

    def login_page(self, error: str):
        data = LOGIN_PAGE.replace("{{ERROR}}", html.escape(error)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def logout(self):
        self.send_response(302)
        self.send_header("Location", "/login")
        self.send_header("Set-Cookie", "vault_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0")
        self.end_headers()

    def gallery(self):
        buckets: dict[str, list[str]] = {}
        for item in ITEMS:
            bucket = title_bucket(item.title)
            poster = f"<img class='poster' loading='lazy' src='{item.thumb}' alt=''>" if item.thumb else "<div class='missing'>No Image</div>"
            buckets.setdefault(bucket, []).append(
                f"<article class='card' data-title='{html.escape(item.title.lower())}'>"
                f"<a class='poster-link' href='/item/{item.id}'>{poster}</a>"
                f"<div class='info'><div class='title'>{html.escape(item.title)}</div><div class='meta'>{human_size(item.size)}</div></div>"
                f"</article>"
            )
        letters = ["0-9"] + [chr(code) for code in range(ord("A"), ord("Z") + 1)]
        sections = []
        for letter in letters:
            cards = buckets.get(letter, [])
            if cards:
                sections.append(f"<section class='letter-section' id='section-{letter}'><h2>{letter}</h2><div class='grid'>{''.join(cards)}</div></section>")
        letter_buttons = "".join(f"<button class='letter' data-letter='{letter}'>{letter}</button>" for letter in letters)
        poster_cloud = "".join(
            f"<img src='{item.thumb}' alt=''>"
            for item in ITEMS[:48]
            if item.thumb
        )
        total_size = sum(item.size for item in ITEMS)
        body = (
            PAGE_TEMPLATE
            .replace("{{COUNT}}", str(len(ITEMS)))
            .replace("{{TOTAL_SIZE}}", human_size(total_size))
            .replace("{{SECTIONS}}", "\n".join(sections))
            .replace("{{LETTERS}}", letter_buttons)
            .replace("{{POSTER_CLOUD}}", poster_cloud)
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def detail(self, item_id: str):
        item = safe_item(item_id)
        body = (
            DETAIL_TEMPLATE
            .replace("{{TITLE}}", html.escape(item.title))
            .replace("{{PLAY}}", f"/play/{item.id}")
            .replace("{{DOWNLOAD}}", f"/download/{item.id}")
            .replace("{{SIZE}}", human_size(item.size))
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def json_response(self, payload: dict):
        import json

        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_thumb(self, item_id: str):
        item = safe_item(item_id)
        poster_target = (POSTER_DIR / f"{item.path.name}.jpg").resolve()
        screenshot_target = (SCREENSHOT_DIR / f"{item.path.name}.jpg").resolve()
        target = poster_target if poster_target.is_file() else screenshot_target
        allowed_roots = (str(POSTER_DIR) + os.sep, str(SCREENSHOT_DIR) + os.sep)
        if not str(target).startswith(allowed_roots):
            raise PermissionError("Refusing path outside thumbnail root")
        if not target.is_file():
            raise FileNotFoundError("Thumbnail missing")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def serve_media(self, item_id: str, disposition: str):
        item = safe_item(item_id)
        file_size = item.path.stat().st_size
        start = 0
        end = file_size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header:
            import re

            match = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if match:
                if match.group(1):
                    start = int(match.group(1))
                if match.group(2):
                    end = int(match.group(2))
                end = min(end, file_size - 1)
                status = 206
        length = max(0, end - start + 1)
        mime = mimetypes.guess_type(item.path.name)[0] or "application/octet-stream"
        encoded_name = urllib.parse.quote(item.path.name)
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{encoded_name}")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()
        with item.path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def main():
    parser = argparse.ArgumentParser(description="Password-protected private gallery")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8097)
    args = parser.parse_args()
    refresh_index()
    print(f"Loaded {len(ITEMS)} media items from {MEDIA_ROOT}", flush=True)
    print(f"Serving Private Vault on http://{args.host}:{args.port}/", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
