#!/usr/bin/env python3
"""Shared Webex notification helpers for long-running local WSL jobs.

Scripts create a WebexJob to announce a job's start, periodic status, success,
or failure. Notifications are delegated to the local notifier executable, so
missing Webex configuration does not stop encoding or file-transfer work.

The helper also supplies lightweight file-size formatting used in status
messages. It does not perform media processing or file replacement itself.
"""

import os
import subprocess
import threading
import time
from pathlib import Path


NOTIFIER = Path(os.environ.get(
    "WEBEX_NOTIFIER",
    str(Path(__file__).resolve().parents[1].parent / "webex-notifier" / "bin" / "send_webex_notification"),
))
if not NOTIFIER.is_file():
    NOTIFIER = Path("/home/jnicolas/bin/send_webex_notification")


def send(message):
    if not NOTIFIER.is_file():
        return
    subprocess.run(
        [str(NOTIFIER), message],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def file_size(path):
    try:
        size = Path(path).stat().st_size
    except OSError:
        return "unknown size"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024


class WebexJob:
    def __init__(self, title, details="", interval=None):
        self.title = title
        self.details = details
        self.interval = interval or int(
            os.environ.get("WEBEX_STATUS_SECONDS", "1800")
        )
        self.started = 0.0
        self.current_phase = "starting"
        self.stop_event = threading.Event()
        self.thread = None

    def __enter__(self):
        self.started = time.monotonic()
        message = f"**Job started:** {self.title}"
        if self.details:
            message += f"\n\n{self.details}"
        send(message)
        self.thread = threading.Thread(target=self._status_loop, daemon=True)
        self.thread.start()
        return self

    def phase(self, description, notify_now=False):
        self.current_phase = description
        if notify_now:
            send(f"**Job status:** {self.title}\n\n{description}")

    def _status_loop(self):
        while not self.stop_event.wait(self.interval):
            minutes = int((time.monotonic() - self.started) / 60)
            send(
                f"**Job status:** {self.title}\n\n"
                f"Running for {minutes} minutes. Phase: {self.current_phase}"
            )

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        minutes = (time.monotonic() - self.started) / 60
        if exc_value is None:
            send(
                f"**Job completed:** {self.title}\n\n"
                f"Runtime: {minutes:.1f} minutes."
            )
        else:
            send(
                f"**Job failed:** {self.title}\n\n"
                f"After {minutes:.1f} minutes: {exc_value}"
            )
        return False
