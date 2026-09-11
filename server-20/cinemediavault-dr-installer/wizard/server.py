#!/usr/bin/env python3
"""The CineMediaVault first-run setup wizard.

Security posture
----------------
This service installs an operating system's worth of software, so it runs as
root. Four things keep that acceptable:

* It **binds a private address only** and refuses to start on a public one.
* It requires a **bootstrap token** that ``install.sh`` prints on the console.
  Reaching it therefore requires console or SSH access to the machine already.
* Every state-changing request carries a **CSRF token** tied to the session, and
  requests from a foreign ``Origin`` are rejected.
* It **stops itself** once the installation finishes, and it is never enabled at
  boot.

It is deliberately not a general-purpose web application: there is no file
upload, no path traversal surface (static assets are served from a fixed
allow-list), and every request body is size-capped.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PACKAGE_ROOT = Path(os.environ.get("CMV_PACKAGE_ROOT",
                                  Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(PACKAGE_ROOT))

from installer.config.schema import apply_defaults          # noqa: E402
from installer.core.logging import InstallLogger            # noqa: E402
from installer.core.redact import REDACTOR                  # noqa: E402
from installer.version import INSTALLER_VERSION             # noqa: E402
from wizard import api                                      # noqa: E402
from wizard.session import SessionStore, WizardState        # noqa: E402

MAX_BODY_BYTES = 1 * 1024 * 1024
STATIC_DIR = PACKAGE_ROOT / "wizard" / "static"
TEMPLATE_DIR = PACKAGE_ROOT / "wizard" / "templates"

#: Only these static files are ever served. No directory listing, no traversal.
STATIC_ALLOW = {"wizard.css", "wizard.js"}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Content-Security-Policy": (
        "default-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
}


class WizardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, state: WizardState,
                 sessions: SessionStore, logger: InstallLogger):
        super().__init__(address, handler)
        self.state = state
        self.sessions = sessions
        self.logger = logger
        self.install_thread: threading.Thread | None = None
        self.install_events: list[dict] = []
        self.install_report: dict | None = None
        self.events_lock = threading.Lock()
        self.should_stop = threading.Event()
        self.started_at = time.time()


class Handler(BaseHTTPRequestHandler):
    server_version = "CineMediaVaultSetup/2.0"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- plumbing --------------------------------------------------------
    def log_message(self, fmt, *args):
        self.server.logger.debug(f"{self.address_string()} {fmt % args}")

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200,
              extra: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra)

    def _error(self, status: int, message: str,
               fields: dict[str, str] | None = None) -> None:
        self._json({"ok": False, "error": message, "fields": fields or {}}, status)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise api.ApiError("invalid Content-Length", 400)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise api.ApiError("request body too large", 413)
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise api.ApiError(f"invalid JSON: {exc}", 400) from exc
        if not isinstance(data, dict):
            raise api.ApiError("request body must be an object", 400)
        return data

    # -- authentication ----------------------------------------------------
    def _session_token(self) -> str:
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "cmv_setup":
                return value
        return ""

    def _require_session(self) -> dict:
        session = self.server.sessions.get(self._session_token())
        if session is None:
            raise api.ApiError("not signed in", 401)
        return session

    def _require_csrf(self) -> None:
        presented = self.headers.get("X-CSRF-Token") or ""
        if not self.server.sessions.check_csrf(self._session_token(), presented):
            raise api.ApiError("CSRF token missing or invalid", 403)

    def _check_origin(self) -> None:
        """Reject cross-origin state changes even before CSRF is checked."""
        origin = self.headers.get("Origin")
        if not origin:
            return                      # same-origin fetch, or a CLI client
        host = self.headers.get("Host") or ""
        try:
            parsed = urllib.parse.urlparse(origin)
        except ValueError:
            raise api.ApiError("bad Origin header", 403)
        if parsed.netloc != host:
            raise api.ApiError(
                f"cross-origin request refused (Origin {parsed.netloc}, "
                f"Host {host})", 403)

    # -- routing --------------------------------------------------------------
    def do_GET(self):
        self._route("GET")

    def do_HEAD(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method: str):
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        try:
            if method == "POST":
                self._check_origin()
            handler = ROUTES.get((method, path))
            if handler is None:
                if method == "GET" and path.startswith("/static/"):
                    return self._serve_static(path)
                return self._error(404, "not found")
            return handler(self)
        except api.ApiError as exc:
            return self._error(exc.status, str(exc), exc.fields)
        except BrokenPipeError:
            return
        except Exception as exc:                            # noqa: BLE001
            self.server.logger.error(
                f"unhandled error on {method} {path}: {type(exc).__name__}: "
                f"{REDACTOR.redact(exc)}")
            return self._error(500, "internal error")

    # -- static and pages ------------------------------------------------------
    def _serve_static(self, path: str):
        name = path[len("/static/"):]
        if name not in STATIC_ALLOW:
            return self._error(404, "not found")
        file = STATIC_DIR / name
        if not file.is_file():
            return self._error(404, "not found")
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self._send(200, file.read_bytes(), f"{content_type}; charset=utf-8")

    def page_index(self):
        template = TEMPLATE_DIR / "index.html"
        if not template.is_file():
            return self._error(500, "wizard template missing")
        html = template.read_text(encoding="utf-8")
        html = html.replace("{{VERSION}}", INSTALLER_VERSION)
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    # -- API -------------------------------------------------------------------
    def api_session(self):
        """Exchange the bootstrap token for a session."""
        payload = self._read_body()
        token = str(payload.get("token") or "")
        remote = self.client_address[0]
        if not self.server.sessions.check_bootstrap(token, remote):
            time.sleep(1.0)             # slow down guessing
            raise api.ApiError(
                "That setup code is not correct. It is printed on the console "
                "where you ran install.sh.", 401)
        session = self.server.sessions.create(remote=remote)
        csrf = self.server.sessions.csrf_token(session)
        self._json(
            {"ok": True, "csrf": csrf},
            extra={"Set-Cookie":
                   f"cmv_setup={session}; HttpOnly; SameSite=Strict; Path=/; "
                   f"Max-Age=28800"},
        )

    def api_state(self):
        self._require_session()
        state = self.server.state
        self._json({
            "ok": True,
            "version": INSTALLER_VERSION,
            "progress": state.progress(),
            "config": api.redacted_config(state),
            "phase": state.phase,
            "furthest": state.furthest_stage(),
            "csrf": self.server.sessions.csrf_token(self._session_token()),
        })

    def api_submit(self):
        self._require_session()
        self._require_csrf()
        payload = self._read_body()
        stage = str(payload.get("stage") or "")
        fields = payload.get("fields")
        if not isinstance(fields, dict):
            raise api.ApiError("fields must be an object", 400)
        result = api.submit_stage(self.server.state, stage, fields)
        self._json(result)

    def api_back(self):
        self._require_session()
        self._require_csrf()
        payload = self._read_body()
        stage = str(payload.get("stage") or "")
        state = self.server.state
        state.current = state.previous_stage(stage)
        state.save()
        self._json({"ok": True, "current": state.current})

    def api_goto(self):
        self._require_session()
        self._require_csrf()
        payload = self._read_body()
        stage = str(payload.get("stage") or "")
        state = self.server.state
        if not state.can_enter(stage):
            raise api.ApiError("finish the earlier steps first", 400)
        state.current = stage
        state.save()
        self._json({"ok": True, "current": state.current})

    def api_reset(self):
        self._require_session()
        self._require_csrf()
        if self.server.state.phase == "installing":
            raise api.ApiError("an installation is running; it cannot be reset now",
                               409)
        self.server.state.reset()
        self._json({"ok": True})

    def api_review(self):
        self._require_session()
        self._json(api.review(self.server.state))

    def api_validate_path(self):
        self._require_session()
        self._require_csrf()
        self._json(api.check_path(self._read_body()))

    def api_validate_port(self):
        self._require_session()
        self._require_csrf()
        self._json(api.check_port(self._read_body()))

    def api_discover_tuners(self):
        self._require_session()
        self._require_csrf()
        self._json(api.discover_tuners(self._read_body()))

    def api_lineup(self):
        self._require_session()
        self._require_csrf()
        self._json(api.fetch_lineup(self._read_body()))

    def api_mounts(self):
        self._require_session()
        self._json(api.list_mounts({}))

    def api_timezones(self):
        self._require_session()
        self._json(api.timezones({}))

    def api_suggest(self):
        self._require_session()
        self._json(api.suggest_defaults({}))

    # -- installation ------------------------------------------------------------
    def api_install(self):
        self._require_session()
        self._require_csrf()
        server = self.server
        state = server.state

        if state.phase == "installing" and server.install_thread and \
                server.install_thread.is_alive():
            # Refreshing the page during an install must attach to the running
            # job, never start a second one.
            return self._json({"ok": True, "already_running": True})

        payload = self._read_body()
        dry_run = bool(payload.get("dry_run"))

        state.phase = "installing"
        state.install_started_at = time.time()
        state.save()

        with server.events_lock:
            server.install_events.clear()
            server.install_report = None

        thread = threading.Thread(
            target=_run_install, args=(server, dry_run), daemon=True)
        server.install_thread = thread
        thread.start()
        self._json({"ok": True, "started": True, "dry_run": dry_run})

    def api_progress(self):
        self._require_session()
        server = self.server
        try:
            since = int(urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query).get("since", ["0"])[0])
        except (ValueError, IndexError):
            since = 0

        with server.events_lock:
            events = server.install_events[since:]
            report = server.install_report
        logs = server.logger.tail(count=400, since_seq=since if since else 0)

        self._json({
            "ok": True,
            "phase": server.state.phase,
            "events": events,
            "next_since": since + len(events),
            "logs": logs,
            "report": report,
            "running": bool(server.install_thread and server.install_thread.is_alive()),
        })

    def api_finish(self):
        """Shut the wizard down once the operator has read the result."""
        self._require_session()
        self._require_csrf()
        self._json({"ok": True, "message": "The setup service is stopping."})
        self.server.logger.info("setup finished; stopping the wizard service")
        threading.Timer(1.0, self.server.should_stop.set).start()

    def api_health(self):
        self._json({"ok": True, "version": INSTALLER_VERSION,
                    "uptime": round(time.time() - self.server.started_at, 1)})


def _run_install(server: WizardServer, dry_run: bool) -> None:
    """Run the installation in a worker thread, publishing progress events."""
    from installer.core.context import Context
    from installer.engine import Engine
    from installer import preflight

    logger = server.logger
    state = server.state

    def publish(event: dict) -> None:
        with server.events_lock:
            server.install_events.append(event)

    try:
        config = apply_defaults(state.config)
        ctx = Context(config, logger=logger, dry_run=dry_run,
                      package_root=PACKAGE_ROOT, assume_yes=True)

        publish({"event": "preflight_started", "ts": time.time()})
        checks = preflight.run_all(ctx)
        summary = preflight.summarise(checks)
        publish({"event": "preflight_finished", "ts": time.time(),
                 "summary": summary})

        if not summary["can_install"]:
            failures = [c.to_json() for c in checks if c.status == "fail"]
            with server.events_lock:
                server.install_report = {
                    "ok": False,
                    "error": "Preflight checks failed. Nothing was changed.",
                    "failures": failures,
                }
            state.phase = "failed"
            state.save()
            publish({"event": "run_failed", "ts": time.time(),
                     "error": "preflight failed"})
            return

        report = Engine(ctx, on_progress=publish).run()

        with server.events_lock:
            server.install_report = {
                **report.to_json(),
                "url": ctx.primary_url(),
                "admin": ctx.get("admin.username"),
                "services": ctx.service_urls,
                "dry_run": dry_run,
            }
        state.phase = "done" if report.ok else "failed"
        if report.ok and not dry_run:
            state.complete("install")
        state.save()

    except Exception as exc:                                # noqa: BLE001
        message = REDACTOR.redact(f"{type(exc).__name__}: {exc}")
        logger.error(f"installation failed: {message}")
        with server.events_lock:
            server.install_report = {"ok": False, "error": message}
        state.phase = "failed"
        state.save()
        publish({"event": "run_failed", "ts": time.time(), "error": message})


ROUTES = {
    ("GET", "/"): Handler.page_index,
    ("GET", "/api/health"): Handler.api_health,
    ("POST", "/api/session"): Handler.api_session,
    ("GET", "/api/state"): Handler.api_state,
    ("POST", "/api/stage"): Handler.api_submit,
    ("POST", "/api/back"): Handler.api_back,
    ("POST", "/api/goto"): Handler.api_goto,
    ("POST", "/api/reset"): Handler.api_reset,
    ("GET", "/api/review"): Handler.api_review,
    ("POST", "/api/validate/path"): Handler.api_validate_path,
    ("POST", "/api/validate/port"): Handler.api_validate_port,
    ("POST", "/api/discover/tuners"): Handler.api_discover_tuners,
    ("POST", "/api/discover/lineup"): Handler.api_lineup,
    ("GET", "/api/discover/mounts"): Handler.api_mounts,
    ("GET", "/api/timezones"): Handler.api_timezones,
    ("GET", "/api/suggest"): Handler.api_suggest,
    ("POST", "/api/install"): Handler.api_install,
    ("GET", "/api/progress"): Handler.api_progress,
    ("POST", "/api/finish"): Handler.api_finish,
}


def is_private(address: str) -> bool:
    if address in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CineMediaVault setup wizard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--state", default="/var/lib/cinemediavault/setup-state.json")
    parser.add_argument("--token", default="",
                        help="bootstrap token; read from CMV_SETUP_TOKEN when unset")
    parser.add_argument("--log-file", default="/var/log/cinemediavault/setup.log")
    parser.add_argument("--allow-public", action="store_true",
                        help=argparse.SUPPRESS)   # tests only; never documented
    args = parser.parse_args(argv)

    if not is_private(args.host) and not args.allow_public:
        print(f"Refusing to bind {args.host}: the setup wizard runs as root and "
              f"must never be reachable from a public address. Bind 127.0.0.1 or "
              f"a private LAN address instead.", file=sys.stderr)
        return 2

    token = args.token or os.environ.get("CMV_SETUP_TOKEN", "")
    if not token:
        print("No bootstrap token was supplied. Set CMV_SETUP_TOKEN or pass "
              "--token; the wizard will not run unauthenticated.", file=sys.stderr)
        return 2
    REDACTOR.register(token)

    try:
        Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
        logger = InstallLogger(args.log_file, console=True)
    except OSError:
        logger = InstallLogger(console=True)

    state = WizardState(args.state)
    sessions = SessionStore(token)

    server = WizardServer((args.host, args.port), Handler,
                          state=state, sessions=sessions, logger=logger)

    logger.info(f"setup wizard listening on http://{args.host}:{args.port}/")
    if state.completed:
        logger.info(f"resuming a previous session at the '{state.furthest_stage()}' step")

    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5},
                              daemon=True)
    thread.start()
    try:
        while not server.should_stop.wait(0.5):
            sessions.purge()
    except KeyboardInterrupt:
        logger.info("interrupted")
    finally:
        server.shutdown()
        server.server_close()
        logger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
