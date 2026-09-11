"""Wizard state, sessions and CSRF.

Resumability
------------
Every stage the operator completes is written atomically to a state file owned
by root with mode 0600. Refreshing the browser, closing it, or rebooting the
machine mid-install loses nothing: the wizard reopens at the furthest stage
reached, and the installer's own journal makes the *installation* resumable
independently of the browser.

Secrets in the state file
-------------------------
API keys are stored so that a resumed session does not force the operator to
re-enter them, but they are only ever sent *back* to the browser masked. The
administrator password is different: it is hashed the moment it is accepted and
the plaintext is never written to disk at all.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

STATE_VERSION = 1
SESSION_TTL_SECONDS = 8 * 3600
CSRF_TTL_SECONDS = 8 * 3600

#: Wizard stages, in order. The ids are stable and appear in the state file.
STAGES = (
    ("welcome",     "Welcome"),
    ("admin",       "Administrator"),
    ("network",     "Network and HTTPS"),
    ("media",       "Media locations"),
    ("mounts",      "Network shares"),
    ("metadata",    "Metadata providers"),
    ("livetv",      "Live TV"),
    ("epg",         "Guide"),
    ("dvr",         "Recording"),
    ("subtitles",   "Subtitles"),
    ("integrations", "Integrations"),
    ("modules",     "Modules and sizing"),
    ("review",      "Review"),
    ("install",     "Install"),
)
STAGE_IDS = tuple(s for s, _ in STAGES)


class WizardState:
    """The persisted wizard document."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.version = STATE_VERSION
        self.created_at = time.time()
        self.updated_at = time.time()
        self.config: dict[str, Any] = {}
        self.completed: list[str] = []
        self.current: str = "welcome"
        self.phase: str = "collecting"      # collecting | installing | done | failed
        self.discovered: dict[str, Any] = {}
        self.admin_hashed: bool = False
        self.install_started_at: float = 0.0
        self.load()

    # -- persistence ----------------------------------------------------
    def load(self) -> None:
        with self._lock:
            if not self.path.is_file():
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # A corrupt state file must not trap the operator. Keep it for
                # inspection and start fresh.
                try:
                    self.path.rename(self.path.with_suffix(
                        f".corrupt-{time.strftime('%Y%m%d-%H%M%S')}.json"))
                except OSError:
                    pass
                return
            if data.get("version") != STATE_VERSION:
                return
            self.created_at = data.get("created_at", time.time())
            self.updated_at = data.get("updated_at", time.time())
            self.config = data.get("config") or {}
            self.completed = [s for s in (data.get("completed") or [])
                              if s in STAGE_IDS]
            self.current = data.get("current") or "welcome"
            self.phase = data.get("phase") or "collecting"
            self.discovered = data.get("discovered") or {}
            self.admin_hashed = bool(data.get("admin_hashed"))
            self.install_started_at = data.get("install_started_at", 0.0)

    def save(self) -> None:
        with self._lock:
            self.updated_at = time.time()
            payload = {
                "version": STATE_VERSION,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "config": self.config,
                "completed": self.completed,
                "current": self.current,
                "phase": self.phase,
                "discovered": self.discovered,
                "admin_hashed": self.admin_hashed,
                "install_started_at": self.install_started_at,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)

    def reset(self) -> None:
        with self._lock:
            self.config = {}
            self.completed = []
            self.current = "welcome"
            self.phase = "collecting"
            self.discovered = {}
            self.admin_hashed = False
            self.install_started_at = 0.0
            self.save()

    # -- stage bookkeeping ------------------------------------------------
    def complete(self, stage: str) -> None:
        with self._lock:
            if stage in STAGE_IDS and stage not in self.completed:
                self.completed.append(stage)
            self.current = self.next_stage(stage)
            self.save()

    def next_stage(self, stage: str) -> str:
        try:
            index = STAGE_IDS.index(stage)
        except ValueError:
            return "welcome"
        return STAGE_IDS[min(index + 1, len(STAGE_IDS) - 1)]

    def previous_stage(self, stage: str) -> str:
        try:
            index = STAGE_IDS.index(stage)
        except ValueError:
            return "welcome"
        return STAGE_IDS[max(index - 1, 0)]

    def can_enter(self, stage: str) -> bool:
        """A stage is reachable once every stage before it is complete."""
        if stage not in STAGE_IDS:
            return False
        index = STAGE_IDS.index(stage)
        return all(s in self.completed for s in STAGE_IDS[:index])

    def furthest_stage(self) -> str:
        for stage in STAGE_IDS:
            if stage not in self.completed:
                return stage
        return STAGE_IDS[-1]

    def progress(self) -> dict:
        return {
            "stages": [
                {"id": sid, "title": title,
                 "complete": sid in self.completed,
                 "current": sid == self.current,
                 "reachable": self.can_enter(sid)}
                for sid, title in STAGES
            ],
            "current": self.current,
            "phase": self.phase,
            "percent": round(len(self.completed) * 100 / len(STAGES)),
        }


class SessionStore:
    """Bearer sessions for the wizard, plus per-session CSRF tokens.

    The wizard runs as root, so authentication is not optional. Access starts
    with a bootstrap token that ``install.sh`` prints on the console, which
    proves physical or SSH access to the machine. Once the administrator account
    is created, that account's credentials take over.
    """

    def __init__(self, bootstrap_token: str):
        self._lock = threading.RLock()
        self._sessions: dict[str, dict] = {}
        self._bootstrap_token = bootstrap_token
        self._failures: dict[str, list[float]] = {}

    # -- bootstrap -------------------------------------------------------
    def check_bootstrap(self, token: str, remote: str) -> bool:
        """Constant-time comparison, with per-address rate limiting."""
        if self._rate_limited(remote):
            return False
        ok = secrets.compare_digest(token or "", self._bootstrap_token)
        if not ok:
            self._record_failure(remote)
        return ok

    def _rate_limited(self, remote: str) -> bool:
        with self._lock:
            recent = [t for t in self._failures.get(remote, [])
                      if time.time() - t < 300]
            self._failures[remote] = recent
            # Five wrong tokens in five minutes from one address is an attack,
            # not a typo.
            return len(recent) >= 5

    def _record_failure(self, remote: str) -> None:
        with self._lock:
            self._failures.setdefault(remote, []).append(time.time())

    # -- sessions ----------------------------------------------------------
    def create(self, *, remote: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = {
                "created_at": time.time(),
                "last_seen": time.time(),
                "remote": remote,
                "csrf": secrets.token_urlsafe(32),
            }
        return token

    def get(self, token: str) -> dict | None:
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if time.time() - session["created_at"] > SESSION_TTL_SECONDS:
                del self._sessions[token]
                return None
            session["last_seen"] = time.time()
            return session

    def csrf_token(self, token: str) -> str:
        session = self.get(token)
        return session["csrf"] if session else ""

    def check_csrf(self, token: str, presented: str) -> bool:
        session = self.get(token)
        if not session:
            return False
        return secrets.compare_digest(session["csrf"], presented or "")

    def destroy(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def purge(self) -> None:
        with self._lock:
            cutoff = time.time() - SESSION_TTL_SECONDS
            for token in [t for t, s in self._sessions.items()
                          if s["created_at"] < cutoff]:
                del self._sessions[token]
