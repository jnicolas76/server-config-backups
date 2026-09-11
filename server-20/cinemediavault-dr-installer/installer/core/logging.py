"""Logging that cannot leak secrets.

A single :class:`InstallLogger` writes three sinks at once:

* the console (human readable),
* a rotating install log on disk, and
* an in-memory ring buffer plus subscriber queues, which is what the setup
  wizard streams to the browser.

Every record goes through the process-wide redactor before it reaches any sink,
so there is no path that writes an unredacted line.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .redact import REDACTOR

LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40, "success": 25}
_LEVEL_NAMES = {v: k for k, v in LEVELS.items()}

_COLORS = {
    "debug": "\033[38;5;244m",
    "info": "\033[0m",
    "success": "\033[38;5;35m",
    "warning": "\033[38;5;214m",
    "error": "\033[38;5;203m",
}
_RESET = "\033[0m"


class InstallLogger:
    def __init__(
        self,
        log_file: str | os.PathLike | None = None,
        *,
        level: str = "info",
        console: bool = True,
        buffer_size: int = 5000,
    ) -> None:
        self.level = LEVELS.get(level, 20)
        self.console = console
        self._lock = threading.RLock()
        self._buffer: deque[dict[str, Any]] = deque(maxlen=buffer_size)
        self._subscribers: list[queue.Queue] = []
        self._seq = 0
        self._fh = None
        self._use_color = console and sys.stderr.isatty() and os.environ.get("NO_COLOR") is None
        if log_file:
            path = Path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            # 0640: readable by the installer group, never world-readable.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
            self._fh = os.fdopen(fd, "a", encoding="utf-8")

    # -- emission ---------------------------------------------------------
    def log(self, level: str, message: object, **fields: Any) -> dict[str, Any] | None:
        numeric = LEVELS.get(level, 20)
        if numeric < self.level:
            return None
        safe_message = REDACTOR.redact(message)
        safe_fields = REDACTOR.redact_mapping(fields) if fields else {}
        with self._lock:
            self._seq += 1
            record = {
                "seq": self._seq,
                "ts": time.time(),
                "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "level": level,
                "message": safe_message,
            }
            if safe_fields:
                record["fields"] = safe_fields
            self._buffer.append(record)
            if self._fh:
                try:
                    self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    self._fh.flush()
                except OSError:
                    pass
            dead = []
            for sub in self._subscribers:
                try:
                    sub.put_nowait(record)
                except queue.Full:
                    dead.append(sub)
            for sub in dead:
                self._subscribers.remove(sub)
        if self.console:
            self._write_console(record)
        return record

    def _write_console(self, record: dict[str, Any]) -> None:
        level = record["level"]
        prefix = {
            "debug": "  ",
            "info": "  ",
            "success": "OK",
            "warning": "!!",
            "error": "XX",
        }.get(level, "  ")
        line = f"{prefix} {record['message']}"
        if self._use_color:
            line = f"{_COLORS.get(level, '')}{line}{_RESET}"
        stream = sys.stderr if level in ("warning", "error") else sys.stdout
        print(line, file=stream, flush=True)

    # convenience -----------------------------------------------------------
    def debug(self, message: object, **f: Any): return self.log("debug", message, **f)
    def info(self, message: object, **f: Any): return self.log("info", message, **f)
    def success(self, message: object, **f: Any): return self.log("success", message, **f)
    def warning(self, message: object, **f: Any): return self.log("warning", message, **f)
    def error(self, message: object, **f: Any): return self.log("error", message, **f)

    def step(self, name: str, message: object, **f: Any):
        return self.log("info", f"[{name}] {message}", **f)

    # -- streaming ----------------------------------------------------------
    def subscribe(self, *, backlog: bool = True, maxsize: int = 2000) -> queue.Queue:
        sub: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            if backlog:
                for record in list(self._buffer):
                    try:
                        sub.put_nowait(record)
                    except queue.Full:
                        break
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: queue.Queue) -> None:
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)

    def tail(self, count: int = 200, since_seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            records = [r for r in self._buffer if r["seq"] > since_seq]
        return records[-count:]

    def close(self) -> None:
        with self._lock:
            if self._fh:
                try:
                    self._fh.close()
                finally:
                    self._fh = None


#: Default logger used when a component is not handed one explicitly.
_default = InstallLogger(console=True)


def get_logger() -> InstallLogger:
    return _default


def set_logger(logger: InstallLogger) -> None:
    global _default
    _default = logger
