"""The install journal: what makes the installer resumable and idempotent.

The journal is a small JSON document written atomically after every state
transition. It records, per step:

* the step id and the *fingerprint* of the configuration that step depends on,
* its status (``pending``/``running``/``completed``/``failed``/``skipped``),
* the backup set produced while it ran, and
* a short result summary.

Resume semantics
----------------
A step is re-run when its fingerprint changed, when it is not ``completed``, or
when the operator asked for it explicitly. A step that completed under the same
fingerprint is skipped and reported, never repeated. A step recorded as
``running`` when the process died is treated as incomplete: steps are written to
be safe to re-enter from any point, so re-running is always the correct repair.

Because the journal is written before and after each step, a reboot in the
middle of an installation loses at most the step that was in flight.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .fsops import write_file

PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
SKIPPED = "skipped"

JOURNAL_VERSION = 1


@dataclass
class StepRecord:
    step_id: str
    status: str = PENDING
    fingerprint: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    attempts: int = 0
    summary: str = ""
    error: str = ""
    changed: bool = False
    backups: list[dict] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return 0.0


class Journal:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.version = JOURNAL_VERSION
        self.run_id: str = ""
        self.created_at: float = 0.0
        self.updated_at: float = 0.0
        self.installer_version: str = ""
        self.config_hash: str = ""
        self.phase: str = "new"      # new | preflight | installing | done | failed
        self.steps: dict[str, StepRecord] = {}
        self.order: list[str] = []
        self._load()

    # -- persistence --------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A corrupt journal must not block a repair run. Preserve it for
            # forensics and start clean.
            try:
                self.path.rename(self.path.with_suffix(
                    f".corrupt-{time.strftime('%Y%m%d-%H%M%S')}.json"))
            except OSError:
                pass
            return
        if data.get("version") != JOURNAL_VERSION:
            return
        self.run_id = data.get("run_id", "")
        self.created_at = data.get("created_at", 0.0)
        self.updated_at = data.get("updated_at", 0.0)
        self.installer_version = data.get("installer_version", "")
        self.config_hash = data.get("config_hash", "")
        self.phase = data.get("phase", "new")
        self.order = list(data.get("order", []))
        for step_id, raw in (data.get("steps") or {}).items():
            self.steps[step_id] = StepRecord(
                step_id=step_id,
                status=raw.get("status", PENDING),
                fingerprint=raw.get("fingerprint", ""),
                started_at=raw.get("started_at", 0.0),
                finished_at=raw.get("finished_at", 0.0),
                attempts=raw.get("attempts", 0),
                summary=raw.get("summary", ""),
                error=raw.get("error", ""),
                changed=raw.get("changed", False),
                backups=raw.get("backups", []),
            )

    def save(self) -> None:
        self.updated_at = time.time()
        payload = {
            "version": self.version,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "installer_version": self.installer_version,
            "config_hash": self.config_hash,
            "phase": self.phase,
            "order": self.order,
            "steps": {sid: asdict(rec) for sid, rec in self.steps.items()},
        }
        # The journal can contain step summaries; it is root-only regardless.
        write_file(self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n",
                   mode=0o600)

    # -- run lifecycle ------------------------------------------------------
    def begin_run(self, *, installer_version: str, config_hash: str,
                  order: list[str]) -> None:
        if not self.run_id:
            self.run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
            self.created_at = time.time()
        self.installer_version = installer_version
        self.config_hash = config_hash
        self.order = list(order)
        self.phase = "installing"
        for step_id in order:
            self.steps.setdefault(step_id, StepRecord(step_id=step_id))
        self.save()

    def finish_run(self, *, ok: bool) -> None:
        self.phase = "done" if ok else "failed"
        self.save()

    # -- step lifecycle -----------------------------------------------------
    def record(self, step_id: str) -> StepRecord:
        return self.steps.setdefault(step_id, StepRecord(step_id=step_id))

    def should_run(self, step_id: str, fingerprint: str, *, force: bool = False) -> bool:
        record = self.record(step_id)
        if force:
            return True
        if record.status != COMPLETED:
            return True
        if record.fingerprint != fingerprint:
            return True
        return False

    def start_step(self, step_id: str, fingerprint: str) -> StepRecord:
        record = self.record(step_id)
        record.status = RUNNING
        record.fingerprint = fingerprint
        record.started_at = time.time()
        record.finished_at = 0.0
        record.attempts += 1
        record.error = ""
        self.save()
        return record

    def complete_step(self, step_id: str, *, summary: str = "", changed: bool = False,
                      backups: list[dict] | None = None) -> None:
        record = self.record(step_id)
        record.status = COMPLETED
        record.finished_at = time.time()
        record.summary = summary
        record.changed = changed
        if backups:
            record.backups = backups
        self.save()

    def skip_step(self, step_id: str, *, summary: str = "") -> None:
        record = self.record(step_id)
        record.status = SKIPPED
        record.finished_at = time.time()
        record.summary = summary
        self.save()

    def fail_step(self, step_id: str, error: str, *,
                  backups: list[dict] | None = None) -> None:
        record = self.record(step_id)
        record.status = FAILED
        record.finished_at = time.time()
        record.error = error
        if backups:
            record.backups = backups
        self.save()

    # -- queries ------------------------------------------------------------
    def completed_steps(self) -> list[str]:
        return [s for s in self.order if self.steps.get(s, StepRecord(s)).status == COMPLETED]

    def failed_steps(self) -> list[str]:
        return [s for s in self.order if self.steps.get(s, StepRecord(s)).status == FAILED]

    def all_backups(self) -> list[dict]:
        """Every backup entry, newest step first, for rollback."""
        entries: list[dict] = []
        for step_id in reversed(self.order):
            record = self.steps.get(step_id)
            if record:
                entries.extend(record.backups)
        return entries

    def progress(self) -> dict[str, Any]:
        total = len(self.order) or 1
        done = sum(1 for s in self.order
                   if self.steps.get(s, StepRecord(s)).status in (COMPLETED, SKIPPED))
        current = next(
            (s for s in self.order if self.steps.get(s, StepRecord(s)).status == RUNNING),
            "")
        return {
            "phase": self.phase,
            "total": len(self.order),
            "completed": done,
            "percent": round(done * 100 / total),
            "current": current,
            "failed": self.failed_steps(),
            "steps": [
                {
                    "id": s,
                    "status": self.steps.get(s, StepRecord(s)).status,
                    "summary": self.steps.get(s, StepRecord(s)).summary,
                    "error": self.steps.get(s, StepRecord(s)).error,
                    "duration": round(self.steps.get(s, StepRecord(s)).duration, 2),
                }
                for s in self.order
            ],
        }


def fingerprint(*parts: Any) -> str:
    """Stable hash of the configuration a step depends on."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(
            json.dumps(part, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:32]
